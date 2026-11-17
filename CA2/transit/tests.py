import tempfile
import zipfile
from io import StringIO
from itertools import accumulate
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from transit.bus_seed import SeedError, build_seed, load_seed, save_seed, simplify_line
from transit.gtfs import GtfsError, parse_feed
from transit.models import BusRoute, BusService, BusServiceException, BusStop, BusTimetable

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# A tiny feed: two routes, three stops, one row with a broken coordinate, a time past midnight
FEED = {
    "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                  "R1,1,46A,Phoenix Park - Dun Laoghaire,3\nR2,1,7,Mountjoy Square - Loughlinstown,3\n",
    "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type\n"
                 "S1,1001,Stop One,53.3500,-6.2600,\nS2,1002,Stop Two,53.3600,-6.2500,\n"
                 "S3,1003,Stop Three,53.4000,-6.1000,\nBAD,9,Broken,abc,-6.2,\n",
    "trips.txt": "route_id,service_id,trip_id,trip_headsign,direction_id,shape_id\n"
                 "R1,1,T1,Out,0,SH1\nR1,1,T2,Out,0,SH1\nR2,1,T3,Out,1,SH2\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                      "T1,07:00:00,07:00:00,S1,1\nT1,07:10:00,07:10:00,S2,2\nT2,08:00:00,08:00:00,S1,1\n"
                      "T3,09:00:00,09:00:00,S2,1\nT3,25:10:00,25:10:00,S3,2\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                    "1,1,1,1,1,1,0,0,20260101,20261231\n",
    "calendar_dates.txt": "service_id,date,exception_type\n1,20261225,2\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
                  "SH1,53.35,-6.26,1\nSH1,53.355,-6.255,2\nSH1,53.36,-6.25,3\nSH2,53.36,-6.25,1\nSH2,53.40,-6.10,2\n",
}


def make_zip(directory, files=FEED, name="feed.zip"):
    path = Path(directory) / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in files.items():
            archive.writestr(member, text)
    return str(path)


class GtfsParserTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_parses_routes_stops_and_links(self):
        feed = parse_feed(make_zip(self.tmp))
        self.assertEqual({r["short_name"] for r in feed.routes}, {"46A", "7"})
        self.assertEqual(len(feed.stops), 3)
        self.assertEqual(feed.skipped_stops, 1)                  # the row with latitude "abc"
        self.assertEqual(feed.route_stops["R1"], {"S1", "S2"})
        self.assertEqual(feed.route_stops["R2"], {"S2", "S3"})   # including the 25:10 stop time

    def test_parses_the_timetable_calendar_and_departures(self):
        feed = parse_feed(make_zip(self.tmp))
        self.assertEqual(feed.services, [{"service_id": "1", "days": "1111100", "start": "20260101", "end": "20261231"}])
        self.assertEqual(feed.service_exceptions, [{"service_id": "1", "date": "20261225", "added": False}])
        times = {}
        for row in feed.timetable:
            times[(row["stop_id"], row["route_id"])] = list(accumulate(row["variants"][row["by_service"]["1"]]))
        self.assertEqual(times, {("S1", "R1"): [420, 480], ("S2", "R1"): [430], ("S2", "R2"): [540],
                                 ("S3", "R2"): [1510]})                       # 25:10 is 01:10 the next day
        self.assertEqual({r["headsign"] for r in feed.timetable}, {"Out"})

    def test_missing_files_are_reported(self):
        incomplete = {k: v for k, v in FEED.items() if k != "shapes.txt"}
        with self.assertRaisesMessage(GtfsError, "shapes.txt"):
            parse_feed(make_zip(self.tmp, incomplete))

    def test_missing_and_non_zip_files(self):
        with self.assertRaises(GtfsError):
            parse_feed(str(Path(self.tmp) / "nope.zip"))
        bad = Path(self.tmp) / "bad.zip"
        bad.write_text("not a zip")
        with self.assertRaises(GtfsError):
            parse_feed(str(bad))


@override_settings(CACHES=LOCMEM)
class BusDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with tempfile.TemporaryDirectory() as tmp:
            call_command("load_bus_gtfs", file=make_zip(tmp), stdout=StringIO())

    def test_loader_saves_routes_stops_and_links(self):
        self.assertEqual(BusRoute.objects.count(), 2)
        self.assertEqual(BusStop.objects.count(), 3)
        self.assertEqual(BusStop.objects.get(stop_id="S2").routes.count(), 2)
        self.assertEqual(BusRoute.objects.get(short_name="46A").route.geom_type, "MultiLineString")

    def test_loader_saves_the_timetable(self):
        self.assertEqual(BusTimetable.objects.count(), 4)
        self.assertEqual(BusService.objects.get(service_id="1").weekdays, "1111100")
        self.assertEqual(BusServiceException.objects.count(), 1)

    def test_loader_replaces_rather_than_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            call_command("load_bus_gtfs", file=make_zip(tmp))
        self.assertEqual(BusRoute.objects.count(), 2)
        self.assertEqual(BusStop.objects.count(), 3)
        self.assertEqual(BusTimetable.objects.count(), 4)
        self.assertEqual(BusService.objects.count(), 1)

    def test_loader_reports_a_missing_file_clearly(self):
        with self.assertRaises(CommandError):
            call_command("load_bus_gtfs", file="/no/such/file.zip")
        self.assertEqual(BusRoute.objects.count(), 2)            # existing data untouched

    def test_stops_need_a_bounding_box(self):
        response = self.client.get("/api/transit/bus-stops/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("bbox", response.json()["details"])
        self.assertEqual(self.client.get("/api/transit/bus-stops/?bbox=1,2,3").status_code, 400)
        self.assertEqual(self.client.get("/api/transit/bus-stops/?bbox=a,b,c,d").status_code, 400)

    def test_stops_in_a_box(self):
        response = self.client.get("/api/transit/bus-stops/?bbox=-6.3,53.3,-6.2,53.4")
        self.assertEqual(response.status_code, 200)
        names = {f["properties"]["name"] for f in response.json()["features"]}
        self.assertEqual(names, {"Stop One", "Stop Two"})        # Stop Three is outside the box

    def test_nearest_stops_are_ordered_and_list_their_routes(self):
        response = self.client.get("/api/transit/bus-stops/nearest/?lat=53.3501&lng=-6.2601&distance=2000")
        self.assertEqual(response.status_code, 200)
        first = response.json()["features"][0]["properties"]
        self.assertEqual(first["name"], "Stop One")
        self.assertEqual(first["routes"], ["46A"])
        self.assertIsNotNone(first["distance_m"])

    def test_nearest_rejects_bad_input(self):
        for query in ("", "lat=abc&lng=-6.2", "lat=53&lng=-6&distance=99999", "lat=53&lng=-6&limit=0"):
            with self.subTest(query=query):
                response = self.client.get("/api/transit/bus-stops/nearest/?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "validation_error")

    def test_route_list_is_light_and_detail_is_geojson(self):
        listing = self.client.get("/api/transit/bus-routes/").json()
        self.assertEqual({r["short_name"] for r in listing}, {"46A", "7"})
        self.assertNotIn("geometry", listing[0])
        route = BusRoute.objects.get(short_name="46A")
        detail = self.client.get(f"/api/transit/bus-routes/{route.pk}/").json()
        self.assertEqual(detail["geometry"]["type"], "MultiLineString")
        stops = self.client.get(f"/api/transit/bus-routes/{route.pk}/stops/").json()
        self.assertEqual(len(stops["features"]), 2)

    def test_unknown_route_is_a_json_404(self):
        response = self.client.get("/api/transit/bus-routes/999999/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")


class BusSeedTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name

    def test_simplify_drops_points_on_a_straight_line_but_keeps_the_ends(self):
        line = [(0.0, 0.0), (1.0, 0.000001), (2.0, 0.0)]
        self.assertEqual(simplify_line(line, 0.00003), [(0.0, 0.0), (2.0, 0.0)])
        self.assertEqual(simplify_line(line, 0), line)                    # tolerance 0 keeps everything
        bend = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)]
        self.assertEqual(simplify_line(bend, 0.00003), bend)              # a real corner stays

    def test_seed_round_trip_matches_the_feed(self):
        feed = parse_feed(make_zip(self.tmp))
        path = str(Path(self.tmp) / "seed.json.gz")
        save_seed(path, build_seed(feed))
        loaded = load_seed(path)
        self.assertEqual({r["short_name"] for r in loaded.routes}, {"46A", "7"})
        self.assertEqual(len(loaded.stops), 3)
        self.assertEqual(loaded.route_stops["R1"], {"S1", "S2"})
        self.assertEqual(len(loaded.timetable), 4)
        self.assertEqual(loaded.services[0]["days"], "1111100")
        self.assertEqual(loaded.service_exceptions[0]["added"], False)

    def test_seed_file_is_byte_for_byte_reproducible(self):
        feed = parse_feed(make_zip(self.tmp))
        first, second = str(Path(self.tmp) / "a.gz"), str(Path(self.tmp) / "b.gz")
        save_seed(first, build_seed(feed))
        save_seed(second, build_seed(feed))
        self.assertEqual(Path(first).read_bytes(), Path(second).read_bytes())

    def test_load_bus_seed_command_fills_the_database(self):
        path = str(Path(self.tmp) / "seed.json.gz")
        save_seed(path, build_seed(parse_feed(make_zip(self.tmp))))
        call_command("load_bus_seed", file=path, stdout=StringIO())
        self.assertEqual(BusRoute.objects.count(), 2)
        self.assertEqual(BusStop.objects.count(), 3)
        self.assertEqual(BusStop.objects.get(stop_id="S2").routes.count(), 2)

    def test_bad_seed_files_are_reported_clearly(self):
        with self.assertRaises(SeedError):
            load_seed(str(Path(self.tmp) / "missing.json.gz"))
        junk = Path(self.tmp) / "junk.json.gz"
        junk.write_text("not gzip")
        with self.assertRaises(SeedError):
            load_seed(str(junk))
        with self.assertRaises(CommandError):
            call_command("load_bus_seed", file=str(junk))

    def test_the_bundled_seed_file_loads(self):
        call_command("load_bus_seed", stdout=StringIO())                   # the real file shipped in the repo
        self.assertGreater(BusRoute.objects.count(), 100)
        self.assertGreater(BusStop.objects.count(), 4000)
        self.assertGreater(BusStop.routes.through.objects.count(), 10000)
        self.assertGreater(BusTimetable.objects.count(), 10000)
        self.assertGreater(BusService.objects.count(), 5)
