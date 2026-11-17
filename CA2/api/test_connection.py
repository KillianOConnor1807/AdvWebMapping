from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.test import TestCase, override_settings

from gyms.models import Gym
from transit.models import BusRoute, BusStop, TransitLine, TransitStop

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
