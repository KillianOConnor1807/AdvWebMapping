from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.serializers import GymSerializer
from gyms.models import Gym

# Throttling uses a database cache table that the test database doesn't have; use memory instead
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class ErrorHandlingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        Gym.objects.create(name="Test Gym", location=Point(-6.26, 53.34, srid=4326))

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status)
        self.assertIn("error", response.json())
        self.assertIn("code", response.json())
        if code:
            self.assertEqual(response.json()["code"], code)

    def test_nearby_requires_coordinates(self):
        r = self.client.get("/api/gyms/nearby/")
        self.assert_error(r, 400, "validation_error")
        self.assertIn("lat", r.json()["details"])
        self.assertIn("lng", r.json()["details"])

    def test_nearby_rejects_bad_values(self):
        for query in ("lat=abc&lng=-6.2", "lat=95&lng=-6.2", "lat=nan&lng=-6.2",
                      "lat=53&lng=-6&distance=-1", "lat=53&lng=-6&distance=9999",
                      "lat=53&lng=-6&limit=0", "lat=53&lng=-6&limit=1.5"):
            with self.subTest(query=query):
                self.assert_error(self.client.get("/api/gyms/nearby/?" + query), 400, "validation_error")

    def test_nearby_valid(self):
        r = self.client.get("/api/gyms/nearby/?lat=53.34&lng=-6.26&distance=1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["features"]), 1)

    def test_within_bounds_rejects_bad_boxes(self):
        bad = [{}, {"west": "x", "south": 1, "east": 2, "north": 3},
               {"west": -6.3, "south": 53.3, "east": -6.4, "north": 53.4},  # west > east
               {"west": -60, "south": 0, "east": 60, "north": 10}]           # too large
        for body in bad:
            with self.subTest(body=body):
                self.assert_error(self.client.post("/api/gyms/within_bounds/", body, format="json"), 400)
        self.assert_error(self.client.post("/api/gyms/within_bounds/", [1, 2], format="json"), 400)
        # NaN is not valid JSON, so send the raw text
        r = self.client.post("/api/gyms/within_bounds/",
                             data='{"west": NaN, "south": 53, "east": -6, "north": 54}',
                             content_type="application/json")
        self.assert_error(r, 400)

    def test_near_line_unknown_line_is_404(self):
        self.assert_error(self.client.get("/api/gyms/near_line/?line=999999"), 404, "not_found")

    def test_near_line_bad_line_is_400(self):
        self.assert_error(self.client.get("/api/gyms/near_line/?line=abc"), 400)

    def test_missing_gym_is_json_404(self):
        self.assert_error(self.client.get("/api/gyms/999999/"), 404, "not_found")

    def test_unknown_api_path_is_json_404(self):
        self.assert_error(self.client.get("/api/does-not-exist/"), 404, "not_found")

    def test_unknown_site_path_is_html_404(self):
        r = self.client.get("/no-such-page/")
        self.assertEqual(r.status_code, 404)
        self.assertIn("text/html", r["Content-Type"])

    def test_anonymous_cannot_create(self):
        r = self.client.post("/api/gyms/", {}, format="json")
        self.assertIn(r.status_code, (401, 403))
        self.assertIn("error", r.json())


@override_settings(CACHES=LOCMEM)
class GymSerializerTests(TestCase):
    def payload(self, **over):
        data = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-6.26, 53.34]},
                "properties": {"name": "New Gym"}}
        data["properties"].update(over.pop("properties", {}))
        data.update(over)
        return data

    def test_valid_gym(self):
        self.assertTrue(GymSerializer(data=self.payload()).is_valid())

    def test_rejects_non_point(self):
        s = GymSerializer(data=self.payload(geometry={"type": "LineString",
                                                        "coordinates": [[-6.26, 53.34], [-6.25, 53.35]]}))
        self.assertFalse(s.is_valid())

    def test_rejects_outside_dublin(self):
        s = GymSerializer(data=self.payload(geometry={"type": "Point", "coordinates": [2.35, 48.85]}))
        self.assertFalse(s.is_valid())

    def test_rejects_negative_or_huge_price(self):
        self.assertFalse(GymSerializer(data=self.payload(properties={"price_per_month": "5000"})).is_valid())
        self.assertFalse(GymSerializer(data=self.payload(properties={"price_per_month": "-5"})).is_valid())

    def test_rejects_duplicate_nearby(self):
        Gym.objects.create(name="New Gym", location=Point(-6.26, 53.34, srid=4326))
        s = GymSerializer(data=self.payload())
        self.assertFalse(s.is_valid())
        self.assertIn("name", s.errors)
