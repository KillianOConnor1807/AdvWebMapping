from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings

# The production static storage needs `collectstatic` to have run; tests use the plain one instead.
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=PLAIN_STATIC)
class PwaTests(TestCase):
    def test_manifest_describes_an_installable_app(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json", response["Content-Type"])
        data = response.json()
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/")
        sizes = {icon["sizes"] for icon in data["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertIn("maskable", {icon["purpose"] for icon in data["icons"]})

    def test_icon_files_exist(self):
        for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
            self.assertIsNotNone(finders.find(f"icons/{name}"), name)

    def test_service_worker_headers_and_content(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response["Content-Type"])
        self.assertEqual(response["Service-Worker-Allowed"], "/")
        self.assertIn("no-cache", response["Cache-Control"])
        body = response.content.decode()
        self.assertIn("/offline/", body)
        self.assertIn("/api/", body)

    def test_offline_page(self):
        response = self.client.get("/offline/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "offline")

    def test_pwa_endpoints_are_read_only(self):
        self.assertEqual(self.client.post("/sw.js").status_code, 405)
        self.assertEqual(self.client.post("/manifest.webmanifest").status_code, 405)

    def test_pages_link_the_manifest_and_register_the_worker(self):
        response = self.client.get("/")
        self.assertContains(response, 'rel="manifest"')
        self.assertContains(response, "pwa.js")
        self.assertContains(response, 'id="install-btn"')
