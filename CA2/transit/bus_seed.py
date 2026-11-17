"""A compact, committed copy of the Dublin Bus data, so a fresh database can be filled without
downloading the 37 MB GTFS zip (which is too large to keep in Git and changes with every NTA update).

The file is gzipped JSON holding only what the app uses: simplified route lines, stops, which
routes serve which stops, and the timetable (every departure from every stop). Regenerate it from a new GTFS zip with `python manage.py make_bus_seed`.

Pure Python (no Django) so it is easy to test.
Data: National Transport Authority, licensed CC BY 4.0.
"""
import gzip
import json
from math import hypot
from pathlib import Path

from .gtfs import Feed

SEED_FORMAT = 2
DEFAULT_FILE = Path(__file__).resolve().parent / "data" / "dublin_bus_seed.json.gz"
# 0.00003 degrees is about 3 m: invisible on the map, but removes most of the points in the GTFS shapes
DEFAULT_TOLERANCE = 0.00003
COORDINATE_DECIMALS = 5     # 5 decimal places is about 1 m


class SeedError(Exception):
    """The seed file is missing or unreadable. The message is safe to show to a user."""


def simplify_line(points, tolerance):
    """Douglas-Peucker: drop points that lie within `tolerance` of the straight line between their
    neighbours, keeping the shape. Works in degrees, like PostGIS ST_Simplify on a lng/lat geometry."""
    if len(points) < 3 or tolerance <= 0:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        (x1, y1), (x2, y2) = points[first], points[last]
        dx, dy = x2 - x1, y2 - y1
        length_sq = dx * dx + dy * dy
        farthest, farthest_index = -1.0, None
        for i in range(first + 1, last):
            x, y = points[i]
            if length_sq == 0:
                distance = hypot(x - x1, y - y1)
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length_sq))
                distance = hypot(x - (x1 + t * dx), y - (y1 + t * dy))
            if distance > farthest:
                farthest, farthest_index = distance, i
        if farthest > tolerance:
            keep[farthest_index] = True
            stack.extend([(first, farthest_index), (farthest_index, last)])
    return [point for point, kept in zip(points, keep) if kept]


def build_seed(feed, tolerance=DEFAULT_TOLERANCE):
    """Turn a parsed GTFS feed into the compact dictionary that is saved to the seed file."""
    def rounded(point):
        return [round(point[0], COORDINATE_DECIMALS), round(point[1], COORDINATE_DECIMALS)]

    return {
        "format": SEED_FORMAT,
        "source": "National Transport Authority, GTFS Dublin Bus (CC BY 4.0)",
        "feed_version": feed.version,
        "routes": [
            [r["route_id"], r["short_name"], r["long_name"],
             [[rounded(p) for p in simplify_line(line, tolerance)] for line in r["lines"]]]
            for r in feed.routes
        ],
        "stops": [[s["stop_id"], s["code"], s["name"], round(s["lon"], COORDINATE_DECIMALS),
                   round(s["lat"], COORDINATE_DECIMALS)] for s in feed.stops],
        "links": {route_id: sorted(stop_ids) for route_id, stop_ids in sorted(feed.route_stops.items())},
        "services": [[x["service_id"], x["days"], x["start"], x["end"]] for x in feed.services],
        "exceptions": [[x["service_id"], x["date"], 1 if x["added"] else 0] for x in feed.service_exceptions],
        "timetable": [[t["stop_id"], t["route_id"], t["headsign"], t["variants"], t["by_service"]]
                      for t in sorted(feed.timetable, key=lambda t: (t["route_id"], t["stop_id"]))],
    }


def save_seed(path, seed):
    """Write the seed as gzipped JSON. mtime=0 and sorted keys make the bytes identical on every run,
    so Git only records a change when the data really changed."""
    payload = json.dumps(seed, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as gz:
        gz.write(payload)


def load_seed(path=DEFAULT_FILE):
    """Read a seed file back into the same Feed structure that gtfs.parse_feed returns."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            seed = json.load(handle)
    except FileNotFoundError:
        raise SeedError(f"Seed file not found: {path}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SeedError(f"{path} is not a readable bus seed file ({exc}).")
    try:
        if seed["format"] != SEED_FORMAT:
            raise SeedError(f"{path} has format {seed['format']}; this version of the app reads format {SEED_FORMAT}.")
        return Feed(
            version=seed.get("feed_version", ""),
            routes=[{"route_id": rid, "short_name": short, "long_name": long_name,
                     "lines": [[tuple(p) for p in line] for line in lines]}
                    for rid, short, long_name, lines in seed["routes"]],
            stops=[{"stop_id": sid, "code": code, "name": name, "lon": lon, "lat": lat}
                   for sid, code, name, lon, lat in seed["stops"]],
            route_stops={rid: set(stop_ids) for rid, stop_ids in seed["links"].items()},
            services=[{"service_id": sid, "days": days, "start": start, "end": end}
                      for sid, days, start, end in seed["services"]],
            service_exceptions=[{"service_id": sid, "date": date, "added": bool(added)}
                                for sid, date, added in seed["exceptions"]],
            timetable=[{"stop_id": stop, "route_id": route, "headsign": headsign,
                        "variants": variants, "by_service": by_service}
                       for stop, route, headsign, variants, by_service in seed["timetable"]],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SeedError(f"{path} is missing expected data ({type(exc).__name__}: {exc}).")
