"""Rebuild the bundled bus seed file from a GTFS zip (needed only when the NTA publishes new data).

    python manage.py make_bus_seed                 # data/GTFS_Dublin_Bus.zip -> transit/data/dublin_bus_seed.json.gz

Does not touch the database. Commit the regenerated file to share it.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from transit.bus_seed import DEFAULT_FILE, DEFAULT_TOLERANCE, build_seed, save_seed
from transit.gtfs import GtfsError, parse_feed

DEFAULT_ZIP = Path(settings.BASE_DIR) / "data" / "GTFS_Dublin_Bus.zip"


class Command(BaseCommand):
    help = "Write the compact bus seed file from a GTFS zip"

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_ZIP), help="GTFS zip to read")
        parser.add_argument("--output", default=str(DEFAULT_FILE), help="Seed file to write")
        parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                            help="Line simplification in degrees")

    def handle(self, *args, file, output, tolerance, **options):
        try:
            feed = parse_feed(file)
        except GtfsError as exc:
            raise CommandError(str(exc))
        if not feed.routes or not feed.stops:
            raise CommandError("The feed contains no usable routes or stops.")

        save_seed(output, build_seed(feed, tolerance))
        size_kb = Path(output).stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {output} ({size_kb:.0f} KB): {len(feed.routes)} routes, {len(feed.stops)} stops."))
