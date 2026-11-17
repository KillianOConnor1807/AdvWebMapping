"""Load Dublin Bus routes, stops and which routes serve which stops from the NTA's GTFS zip.

    python manage.py load_bus_gtfs                      # reads data/GTFS_Dublin_Bus.zip
    python manage.py load_bus_gtfs --file other.zip

Download the feed from https://www.transportforireland.ie/transitData/PT_Data.html
Data: National Transport Authority, licensed CC BY 4.0.

Each run REPLACES all bus data: the NTA warns that stop and route IDs can change between feed
versions, so mixing old and new rows would leave stale links behind.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.gis.geos import LineString, MultiLineString, Point
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from transit.gtfs import GtfsError, parse_feed
from transit.models import BusRoute, BusStop

DEFAULT_FILE = Path(settings.BASE_DIR) / "data" / "GTFS_Dublin_Bus.zip"
# 0.00003 degrees is about 3 m: invisible on the map, but removes most of the ~1.1 million shape points
SIMPLIFY_DEGREES = 0.00003


class Command(BaseCommand):
    help = "Replace the bus routes and stops in the database with those from a GTFS zip"

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="GTFS zip to load")
        parser.add_argument("--tolerance", type=float, default=SIMPLIFY_DEGREES,
                            help="Line simplification in degrees (0 keeps every point)")

    def handle(self, *args, file, tolerance, **options):
        self.stdout.write(f"Reading {file} (the timetable is large, this takes a little while)...")
        try:
            feed = parse_feed(file)
        except GtfsError as exc:
            raise CommandError(str(exc))
        if not feed.routes or not feed.stops:
            raise CommandError("The feed contains no usable routes or stops, so nothing was changed.")

        with transaction.atomic():
            BusRoute.objects.all().delete()
            BusStop.objects.all().delete()

            routes = BusRoute.objects.bulk_create([
                BusRoute(route_id=r["route_id"], short_name=r["short_name"][:10], long_name=r["long_name"][:150],
                         route=self.build_line(r["lines"], tolerance))
                for r in feed.routes
            ])
            route_pk = {r.route_id: r.pk for r in routes}

            stops = BusStop.objects.bulk_create([
                BusStop(stop_id=s["stop_id"], code=s["code"][:10], name=s["name"][:150],
                        location=Point(s["lon"], s["lat"], srid=4326))
                for s in feed.stops
            ], batch_size=1000)
            stop_pk = {s.stop_id: s.pk for s in stops}

            Link = BusStop.routes.through
            links = [
                Link(busroute_id=route_pk[route_id], busstop_id=stop_pk[stop_id])
                for route_id, stop_ids in feed.route_stops.items()
                for stop_id in stop_ids if stop_id in stop_pk
            ]
            Link.objects.bulk_create(links, batch_size=2000)

        if feed.skipped_stops or feed.skipped_routes:
            self.stderr.write(f"Skipped {feed.skipped_stops} stops with bad coordinates and "
                              f"{feed.skipped_routes} routes with no drawable path.")
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {len(routes)} routes, {len(stops)} stops and {len(links)} route/stop links "
            f"(feed version {feed.version or 'unknown'})."))

    @staticmethod
    def build_line(lines, tolerance):
        geometry = MultiLineString(*[LineString(coords, srid=4326) for coords in lines], srid=4326)
        if tolerance > 0:
            geometry = geometry.simplify(tolerance, preserve_topology=True)
            if geometry.geom_type == "LineString":   # simplify can collapse a one-line collection
                geometry = MultiLineString(geometry, srid=4326)
        return geometry
