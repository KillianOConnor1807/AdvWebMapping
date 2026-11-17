"""Load Dublin Bus routes, stops and which routes serve which stops from the NTA's GTFS zip.

    python manage.py load_bus_gtfs                      # reads data/GTFS_Dublin_Bus.zip
    python manage.py load_bus_gtfs --file other.zip

Download the feed from https://www.transportforireland.ie/transitData/PT_Data.html
Data: National Transport Authority, licensed CC BY 4.0.

Each run REPLACES all bus data (see transit/bus_loader.py). For a fresh database you do not need
the zip: `load_bus_seed` loads the small bundled copy instead.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from transit.bus_loader import replace_bus_data
from transit.bus_seed import DEFAULT_TOLERANCE
from transit.gtfs import GtfsError, parse_feed

DEFAULT_FILE = Path(settings.BASE_DIR) / "data" / "GTFS_Dublin_Bus.zip"


class Command(BaseCommand):
    help = "Replace the bus routes and stops in the database with those from a GTFS zip"

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="GTFS zip to load")
        parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                            help="Line simplification in degrees (0 keeps every point)")

    def handle(self, *args, file, tolerance, **options):
        self.stdout.write(f"Reading {file} (the timetable is large, this takes a little while)...")
        try:
            feed = parse_feed(file)
        except GtfsError as exc:
            raise CommandError(str(exc))
        if not feed.routes or not feed.stops:
            raise CommandError("The feed contains no usable routes or stops, so nothing was changed.")

        loaded = replace_bus_data(feed, tolerance)

        if feed.skipped_stops or feed.skipped_routes:
            self.stderr.write(f"Skipped {feed.skipped_stops} stops with bad coordinates and "
                              f"{feed.skipped_routes} routes with no drawable path.")
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {loaded.routes} routes, {loaded.stops} stops, {loaded.links} route/stop links and "
            f"{loaded.timetables} timetables (feed version {feed.version or 'unknown'})."))
