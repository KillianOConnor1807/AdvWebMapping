# api/views.py
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.measure import D
from django.db.models import Avg, Count, F, Func, IntegerField, OuterRef, Subquery
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from districts.models import District
from gyms.models import Gym
from transit.models import TransitLine, TransitStop
from .filters import GymFilter
from .serializers import (
    DistrictSerializer, GymSerializer, TransitLineSerializer, TransitStopSerializer,
)

MAX_RADIUS_KM = 50
MAX_LINE_BUFFER_M = 5000


def districts_with_gym_count():
    """Districts annotated with the number of gyms inside each polygon (ST_Within subquery)."""
    gyms_inside = (
        Gym.objects.filter(location__within=OuterRef("boundary"))
        .order_by()
        .annotate(n=Func(F("id"), function="COUNT"))
        .values("n")
    )
    return District.objects.annotate(gym_count=Subquery(gyms_inside, output_field=IntegerField()))


class IsOwnerOrReadOnly(permissions.BasePermission):
    """Anyone can read; only the owner or staff can modify an existing gym."""

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_staff or obj.owner_id == request.user.id


class GymViewSet(viewsets.ModelViewSet):
    queryset = Gym.objects.all()
    serializer_class = GymSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filterset_class = GymFilter
    search_fields = ["name", "address"]
    ordering_fields = ["name", "price_per_month"]
    pagination_class = None  # plain GeoJSON FeatureCollection

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=["get"])
    def nearby(self, request):
        """GET /api/gyms/nearby/?lat=53.34&lng=-6.26&distance=5&limit=10  (distance in km)"""
        try:
            lat = float(request.query_params["lat"])
            lng = float(request.query_params["lng"])
            distance = float(request.query_params.get("distance", 5))
            limit = int(request.query_params.get("limit", 10))
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError
            if not (0 < distance <= MAX_RADIUS_KM) or limit <= 0:
                raise ValueError
        except (KeyError, ValueError):
            return Response(
                {"error": f"Provide valid lat and lng; distance must be 0-{MAX_RADIUS_KM} km "
                          "and limit must be positive."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        origin = Point(lng, lat, srid=4326)
        qs = (
            self.filter_queryset(self.get_queryset())
            .filter(location__distance_lte=(origin, D(km=distance)))
            .annotate(distance=Distance("location", origin))
            .order_by("distance")[:limit]
        )
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["post"])
    def within_bounds(self, request):
        """POST {"west":..,"south":..,"east":..,"north":..} -> gyms inside the box"""
        try:
            w, s, e, n = (float(request.data[k]) for k in ("west", "south", "east", "north"))
            if w >= e or s >= n:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return Response({"error": "Invalid bounding box coordinates."},
                            status=status.HTTP_400_BAD_REQUEST)
        bbox = Polygon.from_bbox((w, s, e, n))
        bbox.srid = 4326
        qs = self.filter_queryset(self.get_queryset()).filter(location__within=bbox)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def near_line(self, request):
        """GET /api/gyms/near_line/?line=1&distance=500  (distance in metres)

        Buffer query: gyms within `distance` of any branch of a transit line, nearest first.
        """
        try:
            line_id = int(request.query_params["line"])
            distance = float(request.query_params.get("distance", 500))
            if not (0 < distance <= MAX_LINE_BUFFER_M):
                raise ValueError
        except (KeyError, ValueError):
            return Response(
                {"error": f"Provide a line id; distance must be 0-{MAX_LINE_BUFFER_M} m."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        line = TransitLine.objects.filter(pk=line_id).first()
        if line is None:
            return Response({"error": "Transit line not found."}, status=status.HTTP_404_NOT_FOUND)

        qs = (
            self.filter_queryset(self.get_queryset())
            .filter(location__distance_lte=(line.route, D(m=distance)))
            .annotate(distance=Distance("location", line.route))
            .order_by("distance")
        )
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["get"])
    def district(self, request, pk=None):
        """GET /api/gyms/{id}/district/ -> the district polygon containing this gym"""
        found = districts_with_gym_count().filter(boundary__contains=self.get_object().location).first()
        if found is None:
            return Response({"error": "This gym is outside all known districts."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(DistrictSerializer(found).data)

    @action(detail=False, methods=["get"])
    def statistics(self, request):
        qs = self.filter_queryset(self.get_queryset())
        agg = qs.aggregate(total=Count("id"), avg_price=Avg("price_per_month"))
        by_level = {r["level"]: r["n"] for r in qs.values("level").annotate(n=Count("id"))}
        return Response({
            "total_gyms": agg["total"],
            "average_price_per_month": round(float(agg["avg_price"]), 2) if agg["avg_price"] else None,
            "by_level": by_level,
        })


class TransitLineViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TransitLine.objects.all()
    serializer_class = TransitLineSerializer
    filterset_fields = ["mode"]
    pagination_class = None


class TransitStopViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TransitStop.objects.select_related("line")
    serializer_class = TransitStopSerializer
    filterset_fields = ["line"]
    search_fields = ["name"]
    pagination_class = None


class DistrictViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DistrictSerializer
    search_fields = ["code", "name"]
    pagination_class = None

    def get_queryset(self):
        return districts_with_gym_count()
