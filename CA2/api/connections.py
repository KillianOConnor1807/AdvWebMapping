"""'How do I get from here to this gym by public transport?'

Given where the user is and a gym, find the stops within walking distance of each end and work out
which routes connect them. Spatial work used here:
  * distance-limited nearest-stop queries (PostGIS ST_DWithin, via the GiST indexes),
  * linear referencing along each route's line, to check the bus really travels from the boarding
    stop towards the alighting stop and not the other way (GEOS project_normalized).
Walking distances are straight-line ("as the crow flies"), so they are labelled approximate.
"""
from collections import defaultdict
from math import asin, cos, radians, sin, sqrt
from urllib.parse import urlencode

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D

from transit.models import BusRoute, BusStop, TransitStop

EARTH_RADIUS_M = 6371008.8
WALK_M_PER_MIN = 80          # a relaxed walking pace, about 4.8 km/h
STOPS_PER_END = 8            # nearest stops considered at each end
RAIL_WALK_M = 1500           # Luas and DART stops are further apart, so allow a longer walk
ON_ROUTE_TOLERANCE = 0.0007  # degrees (~50 m): how close a stop must be to a route line to count as on it
MAX_OPTIONS = 10


def travels_in_order(route_geometry, board, alight):
    """True if some direction of the route passes `board` before `alight`.

    A route is stored as one line per direction. A stop belongs to a direction if it lies within
    ON_ROUTE_TOLERANCE of that line, and the order is its fractional position along the line.
    """
    for line in route_geometry:
        if line.distance(board) <= ON_ROUTE_TOLERANCE and line.distance(alight) <= ON_ROUTE_TOLERANCE:
            if line.project_normalized(board) < line.project_normalized(alight):
                return True
    return False


def _stop_info(stop, kind):
    return {
        "kind": kind,
        "id": stop.pk,
        "name": stop.name,
        "code": getattr(stop, "code", ""),
        "lat": stop.location.y,
        "lng": stop.location.x,
        "walk_m": round(stop.distance.m),
    }


def _nearby_bus_stops(point, radius_m, limit):
    """Nearest bus stops within radius_m, closest first, with their routes loaded in one extra query."""
    queryset = BusStop.objects.filter(location__distance_lte=(point, D(m=radius_m))).prefetch_related("routes")
    return list(queryset.annotate(distance=Distance("location", point)).order_by("distance")[:limit])


def bus_options(origin, gym_point, max_walk):
    near_origin = _nearby_bus_stops(origin, max_walk, STOPS_PER_END)
    near_gym = _nearby_bus_stops(gym_point, max_walk, STOPS_PER_END)

    # route -> the nearby stops (at each end) that it serves
    at_origin, at_gym = defaultdict(list), defaultdict(list)
    for stop in near_origin:
        for route in stop.routes.all():
            at_origin[route.pk].append(stop)
    for stop in near_gym:
        for route in stop.routes.all():
            at_gym[route.pk].append(stop)

    shared = set(at_origin) & set(at_gym)
    geometry = {r.pk: r for r in BusRoute.objects.filter(pk__in=shared)}
    options = []
    for route_pk in shared:
        route = geometry[route_pk]
        best = None
        for board in at_origin[route_pk]:
            for alight in at_gym[route_pk]:
                if board.pk == alight.pk or not travels_in_order(route.route, board.location, alight.location):
                    continue
                walk = board.distance.m + alight.distance.m
                if best is None or walk < best[0]:
                    best = (walk, board, alight)
        if best:
            walk, board, alight = best
            options.append({
                "mode": "bus", "route": route.short_name, "route_pk": route.pk, "long_name": route.long_name,
                "board": _stop_info(board, "bus"), "alight": _stop_info(alight, "bus"),
                "total_walk_m": round(walk),
            })
    return options, near_origin[:3], near_gym[:3]


def rail_options(origin, gym_point):
    """Luas and DART: nearest stop on each line at each end; lines present at both ends are options."""
    def nearest_per_line(point):
        best = {}
        stops = (TransitStop.objects.filter(location__distance_lte=(point, D(m=RAIL_WALK_M)))
                 .annotate(distance=Distance("location", point)).select_related("line").order_by("distance"))
        for stop in stops:
            best.setdefault(stop.line_id, stop)
        return best

    near_origin, near_gym = nearest_per_line(origin), nearest_per_line(gym_point)
    options = []
    for line_id in set(near_origin) & set(near_gym):
        board, alight = near_origin[line_id], near_gym[line_id]
        if board.pk == alight.pk:
            continue
        options.append({
            "mode": board.line.mode, "route": board.line.name, "colour": board.line.colour,
            "board": _stop_info(board, "rail"), "alight": _stop_info(alight, "rail"),
            "total_walk_m": round(board.distance.m + alight.distance.m),
        })
    return options


def directions_url(origin, gym_point):
    """OpenStreetMap's own walking directions between the two points."""
    route = f"{origin.y:.6f},{origin.x:.6f};{gym_point.y:.6f},{gym_point.x:.6f}"
    return "https://www.openstreetmap.org/directions?" + urlencode({"engine": "fossgis_osrm_foot", "route": route})


def build_connection(origin, gym, max_walk):
    options, nearest_origin, nearest_gym = bus_options(origin, gym.location, max_walk)
    options += rail_options(origin, gym.location)
    options.sort(key=lambda o: o["total_walk_m"])

    straight = round(distance_m(origin, gym.location))
    return {
        "origin": {"lat": origin.y, "lng": origin.x},
        "gym": {"id": gym.pk, "name": gym.name, "address": gym.address, "lat": gym.location.y, "lng": gym.location.x},
        "straight_line_m": straight,
        "walk_only_min": round(straight / WALK_M_PER_MIN),
        "max_walk_m": max_walk,
        "options": options[:MAX_OPTIONS],
        "nearest_stops_to_you": [_stop_info(s, "bus") for s in nearest_origin],
        "nearest_stops_to_gym": [_stop_info(s, "bus") for s in nearest_gym],
        "directions_url": directions_url(origin, gym.location),
    }


def distance_m(a, b):
    """Great-circle distance in metres between two lng/lat points (haversine formula)."""
    lat1, lat2 = radians(a.y), radians(b.y)
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin(radians(b.x - a.x) / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(h))
