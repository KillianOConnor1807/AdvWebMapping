from datetime import date, datetime
from unittest.mock import patch
from urllib.error import URLError
from zoneinfo import ZoneInfo

from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings

from transit import realtime
from transit.departures import departures_at_stop, next_departure
from transit.models import BusRoute, BusService, BusServiceException, BusStop, BusTimetable
from django.contrib.gis.geos import LineString, MultiLineString

DUBLIN = ZoneInfo("Europe/Dublin")
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def at(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=DUBLIN)


def varint(n):
    n &= (1 << 64) - 1                      # negative numbers are sent as 64-bit two's complement
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def text(number, value):
    data = value if isinstance(value, bytes) else value.encode()
    return varint(number << 3 | 2) + varint(len(data)) + data


def number(field, value):
    return varint(field << 3) + varint(value)


def feed_message(route_id, stop_id, delay=None, at_time=None, cancelled=False, relationship=None):
    """Build a tiny GTFS-Realtime FeedMessage by hand, the way the NTA's server would."""
    event = (number(1, delay) if delay is not None else b"") + (number(2, at_time) if at_time is not None else b"")
    stop_update = text(4, stop_id) + text(3, event) + (number(5, relationship) if relationship is not None else b"")
    trip = text(1, "trip-1") + text(5, route_id) + (number(4, 3) if cancelled else b"")
    update = text(1, trip) + text(2, stop_update)
    entity = text(1, "entity-1") + text(3, update)
    return text(1, text(1, "2.0")) + text(2, entity)            # a header, then one entity


class TimetableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        line = MultiLineString(LineString((-6.26, 53.35), (-6.20, 53.35), srid=4326), srid=4326)
        cls.route = BusRoute.objects.create(route_id="R_GTFS", short_name="46A", long_name="A to B", route=line)
        cls.night = BusRoute.objects.create(route_id="N_GTFS", short_name="N4", long_name="Night", route=line)
        cls.stop = BusStop.objects.create(stop_id="S_GTFS", code="1001", name="Test Stop", location=Point(-6.25, 53.35, srid=4326))
        # 07:00, 08:00 and 09:00 (stored as the first time then the gaps), and one trip at 25:10 = 01:10 next day
        BusTimetable.objects.create(stop=cls.stop, route=cls.route, headsign="Dundrum", variants=[[420, 60, 60]], by_service={"1": 0})
        BusTimetable.objects.create(stop=cls.stop, route=cls.night, headsign="City", variants=[[1510]], by_service={"1": 0})
        cls.service = BusService.objects.create(service_id="1", weekdays="1111100", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        BusServiceException.objects.create(service=cls.service, date=date(2026, 12, 25), added=False)    # Christmas Day: none
        BusServiceException.objects.create(service=cls.service, date=date(2026, 12, 26), added=True)     # a Saturday that runs

    def setUp(self):
        realtime.reset_cache()

    def test_next_buses_soonest_first(self):
        found = departures_at_stop(self.stop, at(2026, 11, 17, 7, 30), limit=2, use_live=False)     # a Tuesday
        self.assertEqual([(d["route"], d["time"], d["minutes"]) for d in found], [("46A", "08:00", 30), ("46A", "09:00", 90)])
        self.assertEqual(found[0]["headsign"], "Dundrum")
        self.assertFalse(found[0]["live"])
        self.assertNotIn("_scheduled", found[0])                     # internal fields are not exposed

    def test_after_the_last_bus_the_first_one_tomorrow_is_shown(self):
        found = departures_at_stop(self.stop, at(2026, 11, 17, 9, 30), limit=1, use_live=False)
        self.assertEqual(found[0]["time"], "01:10")                  # the night bus, 25:10 on today's timetable
        found = departures_at_stop(self.stop, at(2026, 11, 17, 9, 30), limit=3, use_live=False)
        tomorrow = [d for d in found if d["route"] == "46A"][0]
        self.assertEqual((tomorrow["time"], tomorrow["minutes"], tomorrow["next_day"]), ("07:00", 1290, True))

    def test_a_trip_after_midnight_belongs_to_the_previous_days_service(self):
        found = departures_at_stop(self.stop, at(2026, 11, 18, 0, 50), limit=1, use_live=False)       # Wednesday 00:50
        self.assertEqual((found[0]["route"], found[0]["time"], found[0]["minutes"]), ("N4", "01:10", 20))

    def test_no_service_on_a_day_it_does_not_run(self):
        # Saturday 10:00: the weekly pattern is Monday to Friday, and Friday's late trip has gone
        found = departures_at_stop(self.stop, at(2026, 11, 21, 10, 0), limit=5, use_live=False)
        self.assertEqual([d for d in found if d["minutes"] < 1000], [])

    def test_exceptions_remove_and_add_days(self):
        christmas = departures_at_stop(self.stop, at(2026, 12, 25, 7, 30), limit=5, use_live=False)
        self.assertEqual([d for d in christmas if d["minutes"] < 600], [])                           # not running
        boxing_day = departures_at_stop(self.stop, at(2026, 12, 26, 7, 30), limit=1, use_live=False)  # a Saturday, added
        self.assertEqual(boxing_day[0]["time"], "08:00")

    def test_next_departure_for_one_route(self):
        found = next_departure(self.stop.pk, self.route.pk, at(2026, 11, 17, 7, 48), use_live=False)
        self.assertEqual((found["route"], found["minutes"]), ("46A", 12))
        self.assertIsNone(next_departure(self.stop.pk, 999999, at(2026, 11, 17, 7, 48), use_live=False))

    @override_settings(NTA_API_KEY="test-key")
    def test_live_delay_replaces_the_scheduled_time(self):
        scheduled = at(2026, 11, 17, 8, 0).timestamp()
        live = {("R_GTFS", "S_GTFS"): [(scheduled + 300, 300)]}         # five minutes late
        with patch("transit.realtime.get_delays", return_value=live):
            found = departures_at_stop(self.stop, at(2026, 11, 17, 7, 30), limit=2)
        self.assertEqual((found[0]["time"], found[0]["minutes"], found[0]["live"], found[0]["delay_min"]), ("08:05", 35, True, 5))
        self.assertFalse(found[1]["live"])                                # 09:00 has no live update

    def test_live_update_without_a_delay_is_matched_to_the_nearest_departure(self):
        scheduled = at(2026, 11, 17, 8, 0).timestamp()
        with patch("transit.realtime.get_delays", return_value={("R_GTFS", "S_GTFS"): [(scheduled + 120, None)]}):
            found = departures_at_stop(self.stop, at(2026, 11, 17, 7, 30), limit=1)
        self.assertEqual((found[0]["time"], found[0]["live"]), ("08:02", True))

    def test_a_live_update_for_another_bus_is_ignored(self):
        scheduled = at(2026, 11, 17, 8, 0).timestamp()
        with patch("transit.realtime.get_delays", return_value={("OTHER", "S_GTFS"): [(scheduled + 300, 300)]}):
            found = departures_at_stop(self.stop, at(2026, 11, 17, 7, 30), limit=1)
        self.assertFalse(found[0]["live"])


class RealtimeFeedTests(TestCase):
    def setUp(self):
        realtime.reset_cache()

    def test_decodes_delays_from_the_binary_feed(self):
        data = feed_message("R_GTFS", "S_GTFS", delay=-60, at_time=1_800_000_000)
        self.assertEqual(realtime.decode_delays(data), {("R_GTFS", "S_GTFS"): [(1_800_000_000, -60)]})

    def test_cancelled_trips_and_skipped_stops_are_left_out(self):
        self.assertEqual(realtime.decode_delays(feed_message("R", "S", 0, 1, cancelled=True)), {})
        self.assertEqual(realtime.decode_delays(feed_message("R", "S", 0, 1, relationship=1)), {})

    def test_garbage_is_an_error_not_a_crash_elsewhere(self):
        with self.assertRaises((ValueError, IndexError)):
            realtime.decode_delays(b"\xff\xff\xff")

    @override_settings(NTA_API_KEY="")
    def test_off_without_a_key(self):
        self.assertFalse(realtime.configured())
        with patch("transit.realtime.fetch_feed") as fetch:
            self.assertIsNone(realtime.get_delays())
        fetch.assert_not_called()

    @override_settings(NTA_API_KEY="test-key")
    def test_fetches_once_and_shares_the_copy(self):
        data = feed_message("R", "S", 30, 1_800_000_000)
        with patch("transit.realtime.fetch_feed", return_value=data) as fetch:
            first, second = realtime.get_delays(), realtime.get_delays()
        self.assertEqual(first, second)
        self.assertEqual(fetch.call_count, 1)

    @override_settings(NTA_API_KEY="test-key")
    def test_a_failing_feed_gives_none_and_is_not_hammered(self):
        with patch("transit.realtime.fetch_feed", side_effect=URLError("down")) as fetch:
            self.assertIsNone(realtime.get_delays())
            self.assertIsNone(realtime.get_delays())
        self.assertEqual(fetch.call_count, 1)                              # the second call waited


@override_settings(CACHES=LOCMEM)
class DeparturesApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        TimetableTests.setUpTestData.__func__(cls)

    def url(self, query=""):
        return f"/api/transit/bus-stops/{self.stop.pk}/departures/{query}"

    def test_lists_the_next_buses(self):
        with patch("transit.departures.local_now", return_value=at(2026, 11, 17, 7, 30)):
            data = self.client.get(self.url("?limit=1")).json()
        self.assertEqual(data["stop"]["name"], "Test Stop")
        self.assertFalse(data["live_enabled"])
        self.assertEqual(data["departures"][0]["route"], "46A")
        self.assertEqual(len(data["departures"]), 1)

    def test_bad_limit_is_a_400_and_unknown_stop_a_404(self):
        self.assertEqual(self.client.get(self.url("?limit=0")).status_code, 400)
        self.assertEqual(self.client.get(self.url("?limit=abc")).status_code, 400)
        self.assertEqual(self.client.get("/api/transit/bus-stops/999999/departures/").status_code, 404)
