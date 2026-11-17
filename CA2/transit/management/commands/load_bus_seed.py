"""Load the bundled Dublin Bus data (no GTFS download needed).

    python manage.py load_bus_seed                 # reads transit/data/dublin_bus_seed.json.gz

`seed` runs this automatically on an empty database, so a new machine or a cloud deployment gets
the bus routes and stops on first start. Each run REPLACES all bus data.
"""
from django.core.management.base import BaseCommand, CommandError

from transit.bus_loader import replace_bus_data
from transit.bus_seed import DEFAULT_FILE, SeedError, load_seed


class Command(BaseCommand):
    help = "Replace the bus routes and stops in the database with the bundled seed file"

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="Seed file to load")

    def handle(self, *args, file, **options):
        try:
            feed = load_seed(file)
        except SeedError as exc:
            raise CommandError(str(exc))
        if not feed.routes or not feed.stops:
            raise CommandError("The seed file contains no routes or stops, so nothing was changed.")

        # The seed's lines are already simplified, so no further simplification (tolerance 0)
        loaded = replace_bus_data(feed, tolerance=0)
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {loaded.routes} routes, {loaded.stops} stops, {loaded.links} route/stop links and "
            f"{loaded.timetables} timetables from {file}."))
