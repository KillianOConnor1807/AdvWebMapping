from django.core.management import call_command
from django.core.management.base import BaseCommand

from districts.models import District
from gyms.models import Gym
from transit.models import BusRoute, TransitLine

# Districts first so new gyms get their district straight away
STEPS = [
    (District, "load_districts"),
    (Gym, "populate_gyms"),
    (TransitLine, "populate_transit"),
    (BusRoute, "load_bus_seed"),    # bundled Dublin Bus data, no GTFS download needed
]


class Command(BaseCommand):
    help = "Load the bundled seed data into any table that is still empty (safe to run on every start)"

    def handle(self, *args, **options):
        for model, command in STEPS:
            label = model._meta.verbose_name_plural
            if model.objects.exists():
                self.stdout.write(f"{label}: already loaded, skipping")
                continue
            self.stdout.write(f"{label}: empty, running {command}")
            # DATABASE WRITE: runs the loader command for this table. Only reached when the table is empty,
            # so data that already exists (or was added through the website) is never overwritten.
            call_command(command, stdout=self.stdout, stderr=self.stderr)
