"""Reads a GTFS feed (the standard zip of CSV files that transport agencies publish).

Pure Python, no Django: it turns the zip into plain dictionaries, and the management command
`load_bus_gtfs` saves those to the database. Keeping them apart makes this easy to test.

Spec: https://gtfs.org/schedule/reference/
Dublin Bus data: National Transport Authority, licensed CC BY 4.0.
"""
import csv
import io
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field

REQUIRED_FILES = ("routes.txt", "stops.txt", "trips.txt", "stop_times.txt", "shapes.txt")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class GtfsError(Exception):
    """The feed is missing, unreadable or not a GTFS zip. The message is safe to show to a user."""


@dataclass
class Feed:
    version: str = ""
    routes: list = field(default_factory=list)       # {route_id, short_name, long_name, lines}
    stops: list = field(default_factory=list)        # {stop_id, code, name, lon, lat}
    route_stops: dict = field(default_factory=dict)  # route_id -> set of stop_ids it serves
    skipped_stops: int = 0                           # rows with unusable coordinates
    skipped_routes: int = 0                          # routes with no drawable shape
    # Timetable. Which days each service runs, and every departure from each stop on each route.
    services: list = field(default_factory=list)            # {service_id, days, start, end}
    service_exceptions: list = field(default_factory=list)  # {service_id, date, added}
    timetable: list = field(default_factory=list)           # {stop_id, route_id, headsign, variants, by_service}


def _rows(archive, name):
    """Yield one dict per CSV row, reading the zip member as a stream (stop_times.txt is ~170 MB)."""
    with archive.open(name) as raw:
        # utf-8-sig drops the byte-order mark some publishers put at the start of the file
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def _coordinate(row, lat_key, lon_key):
    try:
        lat, lon = float(row[lat_key]), float(row[lon_key])
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lon, lat


def _minutes(text):
    """'07:05:30' -> 425 whole minutes since midnight. Hours can pass 24 for trips that run after
    midnight ('25:10:00' is 01:10 the next morning). Returns None if the text is unreadable."""
    try:
        hours, minutes = text.split(":")[:2]
        return int(hours) * 60 + int(minutes)
    except (AttributeError, ValueError):
        return None


def _build_timetable(departures, headsigns):
    """Turn {(stop, route): {service: [minutes]}} into compact rows.

    Each list of departures is stored as the first time followed by the gaps between times (small
    numbers that compress well). Services with identical times, such as Tuesday and Wednesday,
    share one list.
    """
    rows = []
    for (stop_id, route_id), by_service in departures.items():
        variants, seen, chosen = [], {}, {}
        for service_id, minutes in by_service.items():
            minutes.sort()
            deltas = [minutes[0]] + [b - a for a, b in zip(minutes, minutes[1:])]
            key = tuple(deltas)
            if key not in seen:
                seen[key] = len(variants)
                variants.append(deltas)
            chosen[service_id] = seen[key]
        common = headsigns[(stop_id, route_id)].most_common(1)
        rows.append({"stop_id": stop_id, "route_id": route_id, "headsign": common[0][0] if common else "",
                     "variants": variants, "by_service": chosen})
    return rows


def parse_feed(path):
    """Read the feed at `path`. Raises GtfsError with a readable message if it can't be used."""
    try:
        archive = zipfile.ZipFile(path)
    except FileNotFoundError:
        raise GtfsError(f"File not found: {path}")
    except (zipfile.BadZipFile, OSError) as exc:
        raise GtfsError(f"{path} is not a readable zip file ({exc}).")

    with archive:
        missing = [name for name in REQUIRED_FILES if name not in archive.namelist()]
        if missing:
            raise GtfsError("The zip is not a complete GTFS feed. Missing: " + ", ".join(missing))
        try:
            return _parse(archive)
        except (KeyError, csv.Error, UnicodeDecodeError) as exc:
            raise GtfsError(f"The feed has an unexpected format ({type(exc).__name__}: {exc}).")


def _parse(archive):
    feed = Feed()

    if "feed_info.txt" in archive.namelist():
        info = next(_rows(archive, "feed_info.txt"), {})
        feed.version = info.get("feed_version", "")

    routes = {r["route_id"]: r for r in _rows(archive, "routes.txt")}

    # Stops. location_type 0 or blank means a boarding stop (1+ are stations, entrances, etc.)
    for row in _rows(archive, "stops.txt"):
        if row.get("location_type") not in ("", "0", None):
            continue
        point = _coordinate(row, "stop_lat", "stop_lon")
        if point is None:
            feed.skipped_stops += 1
            continue
        feed.stops.append({
            "stop_id": row["stop_id"], "code": row.get("stop_code", ""),
            "name": row["stop_name"], "lon": point[0], "lat": point[1],
        })

    # Trips: remember which route each trip belongs to, and count how often each drawn shape is used
    trip_route, trip_service, trip_headsign = {}, {}, {}
    shape_use = Counter()   # (route_id, direction, shape_id) -> number of trips
    for trip in _rows(archive, "trips.txt"):
        trip_route[trip["trip_id"]] = trip["route_id"]
        trip_service[trip["trip_id"]] = trip.get("service_id", "")
        trip_headsign[trip["trip_id"]] = trip.get("trip_headsign", "")
        if trip.get("shape_id"):
            shape_use[(trip["route_id"], trip.get("direction_id", ""), trip["shape_id"])] += 1

    # One drawn path per route and direction: the shape most trips use (skips rare detours and depot runs)
    best = {}
    for (route_id, direction, shape_id), uses in shape_use.items():
        key = (route_id, direction)
        if key not in best or uses > best[key][1]:
            best[key] = (shape_id, uses)
    wanted = {shape_id for shape_id, _ in best.values()}

    # Only the chosen shapes are read from shapes.txt (1.1 million points in the whole file)
    points = defaultdict(list)
    for row in _rows(archive, "shapes.txt"):
        if row["shape_id"] not in wanted:
            continue
        point = _coordinate(row, "shape_pt_lat", "shape_pt_lon")
        if point is not None:
            points[row["shape_id"]].append((int(row["shape_pt_sequence"]), point))

    lines = defaultdict(list)
    for (route_id, _direction), (shape_id, _uses) in sorted(best.items()):
        coords = [pt for _, pt in sorted(points.get(shape_id, []))]
        if len(coords) >= 2:
            lines[route_id].append(coords)

    # Stream the timetable once. It gives us both which routes serve each stop and, for the
    # departures feature, the time of every departure from each stop on each route.
    route_stops = defaultdict(set)
    departures = defaultdict(lambda: defaultdict(list))   # (stop_id, route_id) -> service_id -> [minutes]
    headsigns = defaultdict(Counter)
    for row in _rows(archive, "stop_times.txt"):
        trip_id = row["trip_id"]
        route_id = trip_route.get(trip_id)
        if route_id is None:
            continue
        stop_id = row["stop_id"]
        route_stops[route_id].add(stop_id)
        if row.get("pickup_type") == "1":      # drop-off only: nobody can board here
            continue
        minute = _minutes(row.get("departure_time") or row.get("arrival_time"))
        if minute is None:
            continue
        departures[(stop_id, route_id)][trip_service[trip_id]].append(minute)
        headsigns[(stop_id, route_id)][trip_headsign.get(trip_id, "")] += 1

    for route_id, route in routes.items():
        if route_id not in lines:
            feed.skipped_routes += 1
            continue
        feed.routes.append({
            "route_id": route_id,
            "short_name": route.get("route_short_name") or route_id,
            "long_name": route.get("route_long_name", ""),
            "lines": lines[route_id],
        })
    feed.route_stops = {rid: stops for rid, stops in route_stops.items() if rid in lines}

    # Which days each service runs (calendar.txt) and the one-off changes (calendar_dates.txt)
    names = archive.namelist()
    if "calendar.txt" in names:
        for row in _rows(archive, "calendar.txt"):
            feed.services.append({
                "service_id": row["service_id"],
                "days": "".join("1" if row.get(day) == "1" else "0" for day in WEEKDAYS),
                "start": row["start_date"], "end": row["end_date"],
            })
    if "calendar_dates.txt" in names:
        for row in _rows(archive, "calendar_dates.txt"):
            feed.service_exceptions.append({
                "service_id": row["service_id"], "date": row["date"], "added": row["exception_type"] == "1",
            })
        known = {service["service_id"] for service in feed.services}
        for service_id in sorted({e["service_id"] for e in feed.service_exceptions} - known):
            dates = sorted(e["date"] for e in feed.service_exceptions if e["service_id"] == service_id)
            feed.services.append({"service_id": service_id, "days": "0000000", "start": dates[0], "end": dates[-1]})

    valid_stops = {stop["stop_id"] for stop in feed.stops}
    feed.timetable = [row for row in _build_timetable(departures, headsigns)
                      if row["route_id"] in lines and row["stop_id"] in valid_stops]
    return feed
