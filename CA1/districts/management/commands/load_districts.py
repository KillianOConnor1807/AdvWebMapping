import json
from pathlib import Path

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.core.management.base import BaseCommand
from django.db import transaction

from districts.models import District
from gyms.models import Gym

# Boundaries © OpenStreetMap contributors (ODbL); see the attribution in the file itself
DEFAULT_FILE = Path(__file__).resolve().parents[2] / "data" / "dublin_districts.geojson"


class Command(BaseCommand):
    help = "Load Dublin district boundaries from GeoJSON and assign each gym to its district"

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE), help="GeoJSON FeatureCollection to load")
        parser.add_argument("--clear", action="store_true", help="Delete existing districts first")

    @transaction.atomic
    def handle(self, *args, file, clear=False, **options):
        if clear:
            deleted, _ = District.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} existing districts")

        data = json.loads(Path(file).read_text(encoding="utf-8"))
        for order, feature in enumerate(data["features"]):
            props = feature["properties"]
            geom = GEOSGeometry(json.dumps(feature["geometry"]), srid=4326)
            if geom.geom_type == "Polygon":
                geom = MultiPolygon(geom, srid=4326)
            District.objects.update_or_create(
                code=props["code"],
                defaults={"name": props["name"], "osm_id": props.get("osm_id"),
                          "sort_order": order, "boundary": geom},
            )
        self.stdout.write(f"Loaded {len(data['features'])} districts")

        # Gym.save() looks up the containing district, so re-saving refreshes every gym
        unassigned = []
        for gym in Gym.objects.all():
            gym.save(update_fields=["district"])
            if not gym.district:
                unassigned.append(gym.name)
        if unassigned:
            self.stderr.write("Gyms outside every district: " + ", ".join(unassigned))

        self.stdout.write(self.style.SUCCESS(
            f"Done: {District.objects.count()} districts, "
            f"{Gym.objects.count() - len(unassigned)}/{Gym.objects.count()} gyms assigned"))
