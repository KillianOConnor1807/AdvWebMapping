import json
from pathlib import Path

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon
from django.contrib.gis.gdal import GDALException
from django.contrib.gis.geos import GEOSException
from django.core.management.base import BaseCommand, CommandError
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
            # DATABASE WRITE (delete): remove all district polygons (gyms keep their district name,
            # which is stored as plain text on the gym, so no gyms are lost)
            deleted, _ = District.objects.all().delete()
            self.stdout.write(f"Deleted {deleted} existing districts")

        path = Path(file)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CommandError(f"File not found: {path}. Check the --file path.")
        except (OSError, UnicodeDecodeError) as exc:
            raise CommandError(f"Could not read {path}: {exc}")
        except json.JSONDecodeError as exc:
            raise CommandError(f"{path} is not valid JSON ({exc}).")
        features = data.get("features") if isinstance(data, dict) else None
        if not isinstance(features, list) or not features:
            raise CommandError(f"{path} must be a GeoJSON FeatureCollection with at least one feature.")

        for order, feature in enumerate(features):
            try:
                props = feature["properties"]
                code, name = props["code"], props["name"]
                geom = GEOSGeometry(json.dumps(feature["geometry"]), srid=4326)
            except (KeyError, TypeError) as exc:
                raise CommandError(f"Feature #{order + 1} is missing required data: {exc}")
            except (GEOSException, GDALException, ValueError) as exc:
                raise CommandError(f"Feature #{order + 1} has an invalid geometry: {exc}")
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                raise CommandError(f"Feature #{order + 1} ({code}) must be a Polygon or MultiPolygon, "
                                   f"not {geom.geom_type}.")
            if geom.geom_type == "Polygon":
                geom = MultiPolygon(geom, srid=4326)
            # DATABASE WRITE (insert or update): one district polygon from the GeoJSON file, stored as a
            # MultiPolygon (SRID 4326, GiST-indexed). Matched on the unique `code` (e.g. "D08"), so
            # re-running updates the boundary instead of duplicating it.
            District.objects.update_or_create(
                code=code,
                defaults={"name": name, "osm_id": props.get("osm_id"),
                          "sort_order": order, "boundary": geom},
            )
        self.stdout.write(f"Loaded {len(features)} districts")

        # DATABASE WRITE (update): re-saving each gym runs Gym.save(), which stores the name of the
        # district whose polygon contains the gym's point (a point-in-polygon query). Only the
        # `district` column is written.
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
