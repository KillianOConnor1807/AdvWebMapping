"""Turn-by-turn style routes (the line to draw on the map) from the OpenStreetMap community routing
service (FOSSGIS OSRM, built on OpenStreetMap road data).

The browser asks our API and our server asks the routing service, so the browser never talks to a
third party, failures come back in the app's normal error shape, and tests can replace `fetch_json`.
Fair use: one request per click, identified by a User-Agent.
"""
import json
import logging
import urllib.error
import urllib.request

from rest_framework import exceptions, status

logger = logging.getLogger(__name__)

BASE_URL = "https://routing.openstreetmap.de/routed-{profile}/route/v1/driving/{lng1},{lat1};{lng2},{lat2}"
PROFILES = {"foot": "foot", "bike": "bike", "car": "car"}
TIMEOUT_SECONDS = 8
USER_AGENT = "MuayThaiGymFinder/1.0 (TU Dublin student project)"


class RoutingUnavailable(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The routing service is not available right now. Please try again in a moment."
    default_code = "routing_unavailable"


def fetch_json(url):
    """GET a URL and parse the JSON. Isolated so tests can replace it without any network."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def route_between(origin, destination, mode):
    """Route between two GEOS Points for mode 'foot', 'bike' or 'car'.

    Returns {mode, distance_m, duration_min, geometry} where geometry is a GeoJSON LineString that
    follows the real roads and paths, so it can be drawn on the map.
    """
    # FINDING THE ROAD ROUTE: ask the routing service (OpenStreetMap road data) for the best route
    # between the two points for this mode (walking, cycling or driving). The reply holds the line
    # to draw, plus its length and time.
    url = BASE_URL.format(profile=PROFILES[mode], lng1=origin.x, lat1=origin.y, lng2=destination.x, lat2=destination.y)
    url += "?overview=full&geometries=geojson&steps=false"
    try:
        data = fetch_json(url)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("Routing request failed: %s", exc)
        raise RoutingUnavailable()

    if data.get("code") == "NoRoute" or not data.get("routes"):
        raise exceptions.NotFound("No route could be found between these two places for that mode of travel.")
    route = data["routes"][0]
    return {
        "mode": mode,
        "distance_m": round(route["distance"]),
        "duration_min": max(1, round(route["duration"] / 60)),
        "geometry": route["geometry"],
    }


def build_directions(origin, gym, mode):
    """The route from `origin` to a gym, with the gym's name added."""
    return {**route_between(origin, gym.location, mode), "gym": {"id": gym.pk, "name": gym.name}}
