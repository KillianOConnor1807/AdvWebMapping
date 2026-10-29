# api/serializers.py
from rest_framework import serializers
from rest_framework_gis.serializers import GeoFeatureModelSerializer

from districts.models import District
from gyms.models import Gym
from transit.models import TransitLine, TransitStop

# Rough bounding box around Dublin (lng/lat); rejects obviously wrong coordinates
DUBLIN_BOUNDS = {"west": -6.55, "south": 53.10, "east": -6.00, "north": 53.50}


class GymSerializer(GeoFeatureModelSerializer):
    distance_m = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = Gym
        geo_field = "location"
        fields = [
            "id", "name", "address", "eircode", "district", "phone", "website",
            "level", "beginner_friendly", "price_per_month", "classes",
            "data_source", "owner", "distance_m", "can_edit",
        ]
        read_only_fields = ["owner", "district"]  # district is derived from location

    def get_distance_m(self, obj):
        dist = getattr(obj, "distance", None)
        return round(dist.m) if dist is not None else None

    def get_can_edit(self, obj):
        # Mirrors IsOwnerOrReadOnly so the page only offers Edit/Delete when the API would allow it
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and (user.is_staff or obj.owner_id == user.id))

    def validate_location(self, value):
        b = DUBLIN_BOUNDS
        if not (b["west"] <= value.x <= b["east"] and b["south"] <= value.y <= b["north"]):
            raise serializers.ValidationError("Location must be within the Dublin area.")
        return value

    def validate_price_per_month(self, value):
        if value is not None and value > 1000:
            raise serializers.ValidationError("Price looks unrealistic (max 1000).")
        return value


class TransitLineSerializer(GeoFeatureModelSerializer):
    stop_count = serializers.IntegerField(source="stops.count", read_only=True)

    class Meta:
        model = TransitLine
        geo_field = "route"
        fields = ["id", "name", "mode", "colour", "stop_count"]


class TransitStopSerializer(GeoFeatureModelSerializer):
    line_name = serializers.CharField(source="line.name", read_only=True)

    class Meta:
        model = TransitStop
        geo_field = "location"
        fields = ["id", "name", "line", "line_name"]


class DistrictSerializer(GeoFeatureModelSerializer):
    gym_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = District
        geo_field = "boundary"
        fields = ["id", "code", "name", "osm_id", "gym_count"]
