"""Saves parsed bus data (from the GTFS zip or from the seed file) into the database."""
from collections import namedtuple
from datetime import datetime

from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.db import transaction

from .models import BusRoute, BusService, BusServiceException, BusStop, BusTimetable

Loaded = namedtuple("Loaded", "routes stops links timetables")


def _date(text):
    """GTFS dates look like 20261225. Returns None for anything unreadable."""
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def build_line(lines, tolerance=0):
    """Make one MultiLineString (one line per direction), optionally simplified by `tolerance` degrees."""
    geometry = MultiLineString(*[LineString(coords, srid=4326) for coords in lines], srid=4326)
    if tolerance > 0:
        geometry = geometry.simplify(tolerance, preserve_topology=True)
        if geometry.geom_type == "LineString":   # simplify can collapse a one-line collection
            geometry = MultiLineString(geometry, srid=4326)
    return geometry


@transaction.atomic
def replace_bus_data(feed, tolerance=0):
    """Replace all bus data (routes, stops, links and timetables) with that in `feed`. Returns a Loaded count."""
    # DATABASE WRITE (delete): clear the old bus data first. The NTA warns that route and stop IDs
    # can change between feed versions, so old and new rows must never be mixed. Deleting routes
    # and stops also removes their links. Everything here is one transaction: if anything below
    # fails, the old data is left exactly as it was.
    BusRoute.objects.all().delete()
    BusStop.objects.all().delete()
    BusService.objects.all().delete()     # also removes their exceptions

    # DATABASE WRITE (insert): one BusRoute row per bus route (116 in the Dublin Bus feed).
    # `route` is a MultiLineString (one line per direction); Postgres adds each row to the
    # GiST spatial index as it is inserted.
    routes = BusRoute.objects.bulk_create([
        BusRoute(route_id=r["route_id"], short_name=r["short_name"][:10], long_name=r["long_name"][:150],
                 route=build_line(r["lines"], tolerance))
        for r in feed.routes
    ])
    route_pk = {r.route_id: r.pk for r in routes}

    # DATABASE WRITE (insert): one BusStop row per stop (~4,300), stored as a Point
    # (SRID 4326, x = longitude, y = latitude) and inserted in batches of 1,000.
    stops = BusStop.objects.bulk_create([
        BusStop(stop_id=s["stop_id"], code=s["code"][:10], name=s["name"][:150],
                location=Point(s["lon"], s["lat"], srid=4326))
        for s in feed.stops
    ], batch_size=1000)
    stop_pk = {s.stop_id: s.pk for s in stops}

    # DATABASE WRITE (insert): rows in the many-to-many table recording which routes serve
    # which stops (~11,000 links). Used for "which buses stop here?" and the transit connection.
    Link = BusStop.routes.through
    links = [
        Link(busroute_id=route_pk[route_id], busstop_id=stop_pk[stop_id])
        for route_id, stop_ids in feed.route_stops.items() if route_id in route_pk
        for stop_id in stop_ids if stop_id in stop_pk
    ]
    Link.objects.bulk_create(links, batch_size=2000)

    # DATABASE WRITE (insert): the timetable calendar. One BusService row per service (a pattern of
    # weekdays plus a date range, e.g. "Mon-Fri until 15 Feb")...
    services = {}
    for item in feed.services:
        start, end = _date(item["start"]), _date(item["end"])
        if start and end:
            services[item["service_id"]] = BusService(
                service_id=item["service_id"], weekdays=item["days"], start_date=start, end_date=end)
    BusService.objects.bulk_create(services.values())
    saved = {s.service_id: s for s in BusService.objects.all()}

    # DATABASE WRITE (insert): ...and one BusServiceException row per one-off change
    # (e.g. "does not run on 25 December").
    BusServiceException.objects.bulk_create([
        BusServiceException(service=saved[e["service_id"]], date=_date(e["date"]), added=e["added"])
        for e in feed.service_exceptions if e["service_id"] in saved and _date(e["date"])
    ], batch_size=1000)

    # DATABASE WRITE (insert): one BusTimetable row per stop and route (~10,000 rows). Each holds
    # every departure time from that stop on that route, so "next buses at this stop" is one query.
    timetables = BusTimetable.objects.bulk_create([
        BusTimetable(stop_id=stop_pk[t["stop_id"]], route_id=route_pk[t["route_id"]],
                     headsign=t["headsign"][:150], variants=t["variants"], by_service=t["by_service"])
        for t in feed.timetable if t["stop_id"] in stop_pk and t["route_id"] in route_pk
    ], batch_size=500)
    return Loaded(len(routes), len(stops), len(links), len(timetables))
