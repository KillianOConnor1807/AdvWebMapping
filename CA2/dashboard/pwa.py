"""Progressive Web App endpoints: web manifest, service worker and offline page.

The service worker is served by Django at /sw.js (not from /static/) because a worker can only
control pages at or below its own URL. Served from the site root it covers the whole app.
"""
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.views.decorators.http import require_safe

# Bump this to make every installed copy discard and re-download its cached app shell.
PWA_VERSION = "6"

THEME = "#06101F"       # matches the navbar colour in static/css/theme.css
BACKGROUND = "#0A1628"  # matches the page background


@require_safe
def manifest(request):
    """The 'install me' description: name, icons, colours and how the app should open."""
    data = {
        "name": "Dublin Muay Thai Gym Finder",
        "short_name": "Gym Finder",
        "description": "Find Muay Thai gyms in Dublin and the Luas, DART and bus stops nearest to them.",
        "lang": "en-IE",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": BACKGROUND,
        "theme_color": THEME,
        "icons": [
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json")


@require_safe
def service_worker(request):
    """The service worker script, rendered from a template so it can list the hashed static URLs."""
    body = render_to_string("dashboard/sw.js", {"version": PWA_VERSION})  # no request: nothing user-specific
    response = HttpResponse(body, content_type="application/javascript")
    response["Cache-Control"] = "no-cache"        # browsers must re-check the worker so updates arrive
    response["Service-Worker-Allowed"] = "/"
    return response


@require_safe
def offline(request):
    """Shown when a page is requested without a network connection and has not been saved."""
    return HttpResponse(render_to_string("dashboard/offline.html"))
