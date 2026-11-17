from datetime import date, datetime
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.test import TestCase, override_settings

from gyms.models import Gym
from transit.models import BusRoute, BusService, BusStop, BusTimetable, TransitLine, TransitStop

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# A bus that only runs west -> east along latitude 53.35, between longitudes -6.26 and -6.20
WEST, EAST = -6.2595, -6.2045


@override_settings(CACHES=LOCMEM)
class ConnectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.west_gym = Gym.objects.create(name="West Gym", location=Point(WEST, 53.3503, srid=4326))
        cls.east_gym = Gym.objects.create(name="East Gym", location=Point(EAST, 53.3503, srid=4326))
        route = BusRoute.objects.create(
            route_id="R1", short_name="46A", long_name="West to East",
            route=MultiLineString(LineString((-6.26, 53.35), (-6.20, 53.35), srid=4326), srid=4326))
        for stop_id, name, lng in (("A", "West Stop", -6.259), ("B", "East Stop", -6.205)):
            BusStop.objects.create(stop_id=stop_id, name=name, location=Point(lng, 53.3502, srid=4326)).routes.add(route)

        line = TransitLine.objects.create(
            name="Green Line", mode="luas", colour="#00a94f",
            route=MultiLineString(LineString((-6.26, 53.34), (-6.20, 53.34), srid=4326), srid=4326))
        TransitStop.objects.create(line=line, name="Rail West", location=Point(-6.2590, 53.3410, srid=4326))
        TransitStop.objects.create(line=line, name="Rail East", location=Point(-6.2050, 53.3410, srid=4326))

    def get(self, gym, lat, lng, extra=""):
        return self.client.get(f"/api/gyms/{gym.pk}/connection/?lat={lat}&lng={lng}{extra}")

    def test_bus_in_the_right_direction_is_offered(self):
        response = self.get(self.east_gym, 53.3503, WEST)          # you are west, the gym is east
        self.assertEqual(response.status_code, 200)
        data = response.json()
        bus = [o for o in data["options"] if o["mode"] == "bus"]
        self.assertEqual(len(bus), 1)
        self.assertEqual(bus[0]["route"], "46A")
        self.assertEqual(bus[0]["board"]["name"], "West Stop")
        self.assertEqual(bus[0]["alight"]["name"], "East Stop")
        self.assertIn("openstreetmap.org/directions", data["directions_url"])
        self.assertGreater(data["straight_line_m"], 3000)

    def test_bus_running_the_wrong_way_is_not_offered(self):
        data = self.get(self.west_gym, 53.3503, EAST).json()       # you are east, the gym is west
        self.assertEqual([o for o in data["options"] if o["mode"] == "bus"], [])
        self.assertTrue(data["nearest_stops_to_you"])              # still shows the stops nearby

    def test_luas_line_connecting_both_ends(self):
        data = self.get(self.east_gym, 53.3410, WEST, "&max_walk=300").json()
        rail = [o for o in data["options"] if o["mode"] == "luas"]
        self.assertEqual(len(rail), 1)
        self.assertEqual(rail[0]["route"], "Green Line")

    def test_each_bus_option_says_when_the_next_bus_leaves(self):
        # A weekday timetable at the boarding stop: buses at 10:00 and 10:30
        BusService.objects.create(service_id="1", weekdays="1111111", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        board = BusStop.objects.get(stop_id="A")
        route = BusRoute.objects.get(short_name="46A")
        BusTimetable.objects.create(stop=board, route=route, headsign="East", variants=[[600, 30]], by_service={"1": 0})
        now = datetime(2026, 11, 17, 9, 48, tzinfo=ZoneInfo("Europe/Dublin"))
        with patch("api.connections.local_now", return_value=now):
            options = self.get(self.east_gym, 53.3503, WEST).json()["options"]
        bus = next(o for o in options if o["mode"] == "bus")
        self.assertEqual((bus["next_departure"]["minutes"], bus["next_departure"]["time"]), (12, "10:00"))
        self.assertFalse(bus["next_departure"]["live"])
        rail = [o for o in options if o["mode"] != "bus"]
        self.assertTrue(all("next_departure" not in o for o in rail))

    def test_a_bus_without_timetable_data_still_works(self):
        bus = next(o for o in self.get(self.east_gym, 53.3503, WEST).json()["options"] if o["mode"] == "bus")
        self.assertIsNone(bus["next_departure"])

    def test_a_route_is_found_even_when_many_other_stops_are_closer(self):
        # Busy streets have many stops from other routes. They must not crowd out the route we want.
        other = BusRoute.objects.create(
            route_id="R99", short_name="99", long_name="Elsewhere",
            route=MultiLineString(LineString((-6.30, 53.40), (-6.31, 53.41), srid=4326), srid=4326))
        for i in range(15):
            BusStop.objects.create(stop_id=f"X{i}", name=f"Decoy {i}",
                                   location=Point(WEST, 53.3503 + i * 0.000001, srid=4326)).routes.add(other)
        routes = [o["route"] for o in self.get(self.east_gym, 53.3503, WEST).json()["options"]]
        self.assertIn("46A", routes)

    def test_options_are_sorted_by_walking_distance(self):
        walks = [o["total_walk_m"] for o in self.get(self.east_gym, 53.3503, WEST).json()["options"]]
        self.assertEqual(walks, sorted(walks))

    def test_bad_input_is_a_clear_400(self):
        for query in ("", "lat=abc&lng=-6.2", "lat=53&lng=-6&max_walk=99999"):
            with self.subTest(query=query):
                response = self.client.get(f"/api/gyms/{self.east_gym.pk}/connection/?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "validation_error")

    def test_unknown_gym_is_a_json_404(self):
        response = self.client.get("/api/gyms/999999/connection/?lat=53.35&lng=-6.26")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "not_found")


FAKE_ROUTE = {"code": "Ok", "routes": [{"distance": 2340.4, "duration": 1680.0,
              "geometry": {"type": "LineString", "coordinates": [[-6.26, 53.35], [-6.25, 53.351], [-6.2595, 53.3503]]}}]}


@override_settings(CACHES=LOCMEM)
class DirectionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.gym = Gym.objects.create(name="Route Gym", location=Point(WEST, 53.3503, srid=4326))

    def url(self, query):
        return f"/api/gyms/{self.gym.pk}/directions/?{query}"

    def test_returns_the_route_distance_and_time(self):
        with patch("api.directions.fetch_json", return_value=FAKE_ROUTE) as fake:
            data = self.client.get(self.url("lat=53.34&lng=-6.26&mode=bike")).json()
        self.assertEqual(data["mode"], "bike")
        self.assertEqual(data["distance_m"], 2340)
        self.assertEqual(data["duration_min"], 28)
        self.assertEqual(data["geometry"]["type"], "LineString")
        self.assertIn("routed-bike", fake.call_args[0][0])

    def test_defaults_to_walking(self):
        with patch("api.directions.fetch_json", return_value=FAKE_ROUTE) as fake:
            self.assertEqual(self.client.get(self.url("lat=53.34&lng=-6.26")).json()["mode"], "foot")
        self.assertIn("routed-foot", fake.call_args[0][0])

    def test_route_between_any_two_points(self):
        with patch("api.directions.fetch_json", return_value=FAKE_ROUTE) as fake:
            data = self.client.get("/api/route/?from_lat=53.34&from_lng=-6.26&to_lat=53.35&to_lng=-6.25").json()
        self.assertEqual((data["mode"], data["distance_m"]), ("foot", 2340))
        self.assertEqual(data["geometry"]["type"], "LineString")
        self.assertIn("-6.26,53.34;-6.25,53.35", fake.call_args[0][0])       # longitude first, as the service expects

    def test_route_between_rejects_bad_input(self):
        for query in ("", "from_lat=53&from_lng=-6&to_lat=53", "from_lat=abc&from_lng=-6&to_lat=53&to_lng=-6",
                      "from_lat=53&from_lng=-6&to_lat=53&to_lng=-6&mode=boat"):
            response = self.client.get("/api/route/?" + query)
            self.assertEqual(response.status_code, 400, query)
            self.assertEqual(response.json()["code"], "validation_error", query)

    def test_bad_input_is_a_clear_400(self):
        for query in ("lat=abc&lng=-6.26", "lat=53.34", "lat=53.34&lng=-6.26&mode=helicopter", "lat=95&lng=-6.26"):
            response = self.client.get(self.url(query))
            self.assertEqual(response.status_code, 400, query)
            self.assertEqual(response.json()["code"], "validation_error", query)

    def test_no_route_is_a_404(self):
        with patch("api.directions.fetch_json", return_value={"code": "NoRoute", "routes": []}):
            self.assertEqual(self.client.get(self.url("lat=53.34&lng=-6.26")).status_code, 404)

    def test_routing_service_down_is_a_503_in_the_standard_shape(self):
        with patch("api.directions.fetch_json", side_effect=URLError("offline")):
            response = self.client.get(self.url("lat=53.34&lng=-6.26"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "service_unavailable")
