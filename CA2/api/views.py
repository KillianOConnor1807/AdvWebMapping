# api/views.py
import math
from collections.abc import Mapping

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.measure import D
from django.db.models import Avg, Count, F, Func, IntegerField, OuterRef, Subquery
from rest_framework import exceptions, permissions, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from districts.models import District
from gyms.models import Gym
from transit.models import BusRoute, BusStop, TransitLine, TransitStop
from .connections import build_connection
from .directions import PROFILES, build_directions, route_between
from transit import realtime
from transit.departures import departures_at_stop
from .filters import GymFilter
from .serializers import (
    BusRouteListSerializer, BusRouteSerializer, BusStopSerializer, DistrictSerializer,
    GymSerializer, TransitLineSerializer, TransitStopSerializer,
)

MAX_RADIUS_KM = 50
MAX_LINE_BUFFER_M = 5000
MAX_RESULTS = 100
MAX_BOX_DEGREES = 5          # largest bounding box accepted by within_bounds
MAX_ID = 2**63 - 1           # largest value a BigAutoField id can hold
MAX_STOP_RESULTS = 500       # most bus stops returned for one map view
MAX_STOP_RADIUS_M = 2000     # widest 'nearest stops' search


def read_number(source, name, errors, *, default=None, minimum=None, maximum=None,
                positive=False, integer=False):
    """Read one number from query parameters or a JSON body.

    Problems are added to `errors` (keyed by parameter name) and None is returned, so a request with
    several bad values reports all of them at once. NaN and infinity are rejected.
    """
    raw = source.get(name, default)
    if raw is None or raw == "":
        errors[name] = ["This parameter is required."]
        return None
    if isinstance(raw, bool):
        errors[name] = ["Must be a whole number." if integer else "Must be a number."]
        return None
    try:
        number = int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        errors[name] = ["Must be a whole number." if integer else "Must be a number."]
        return None
    if not integer and not math.isfinite(number):
        errors[name] = ["Must be a finite number."]
        return None
    if positive and number <= 0:
        errors[name] = ["Must be greater than 0."]
        return None
    if (minimum is not None and number < minimum) or (maximum is not None and number > maximum):
        if minimum is not None and maximum is not None:
            errors[name] = [f"Must be between {minimum:g} and {maximum:g}."]
        elif maximum is not None:
            errors[name] = [f"Must be at most {maximum:g}."]
        else:
            errors[name] = [f"Must be at least {minimum:g}."]
        return None
    return number


def raise_if_invalid(errors):
    if errors:
        raise exceptions.ValidationError(errors)


def parse_bounds(source):
    """Turn {west, south, east, north} into a validated Polygon box (raises a 400 if it is unusable)."""
    errors = {}
    west = read_number(source, "west", errors, minimum=-180, maximum=180)
    east = read_number(source, "east", errors, minimum=-180, maximum=180)
    south = read_number(source, "south", errors, minimum=-90, maximum=90)
    north = read_number(source, "north", errors, minimum=-90, maximum=90)
    raise_if_invalid(errors)
    if west >= east or south >= north:
        raise exceptions.ValidationError(
            {"non_field_errors": ["west must be less than east, and south less than north."]})
    if east - west > MAX_BOX_DEGREES or north - south > MAX_BOX_DEGREES:
        raise exceptions.ValidationError(
            {"non_field_errors": [f"The box is too large (at most {MAX_BOX_DEGREES} degrees across)."]})
    box = Polygon.from_bbox((west, south, east, north))
    box.srid = 4326
    return box


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

    # Writes through the API: POST creates, PUT/PATCH updates and DELETE removes a gym (ModelViewSet).
    # GymSerializer validates the data and IsOwnerOrReadOnly checks permission before anything is saved.
    def perform_create(self, serializer):
        # DATABASE WRITE (insert): save the new gym with the logged-in user as its owner. The owner is set
        # here, never taken from the request, so a client cannot create gyms owned by someone else.
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=["get"])
    def nearby(self, request):
        """GET /api/gyms/nearby/?lat=53.34&lng=-6.26&distance=5&limit=10  (distance in km)"""
        errors, params = {}, request.query_params
        lat = read_number(params, "lat", errors, minimum=-90, maximum=90)
        lng = read_number(params, "lng", errors, minimum=-180, maximum=180)
        distance = read_number(params, "distance", errors, default=5, positive=True, maximum=MAX_RADIUS_KM)
        limit = read_number(params, "limit", errors, default=10, integer=True, minimum=1, maximum=MAX_RESULTS)
        raise_if_invalid(errors)

        origin = Point(lng, lat, srid=4326)
        qs = (
            self.filter_queryset(self.get_queryset())
            .filter(location__distance_lte=(origin, D(km=distance)))
            .annotate(distance=Distance("location", origin))
            .order_by("distance")[:limit]
        )
        return Response(self.get_serializer(qs, many=True).data)

    # POST only because the box is sent as JSON; it just reads data, so anonymous users may call it
    @action(detail=False, methods=["post"], permission_classes=[permissions.AllowAny])
    def within_bounds(self, request):
        """POST {"west":..,"south":..,"east":..,"north":..} -> gyms inside the box"""
        data = request.data
        if not isinstance(data, Mapping):
            raise exceptions.ValidationError(
                {"non_field_errors": ["Send a JSON object with west, south, east and north."]})
        bbox = parse_bounds(data)
        qs = self.filter_queryset(self.get_queryset()).filter(location__within=bbox)
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def near_line(self, request):
        """GET /api/gyms/near_line/?line=1&distance=500  (distance in metres)

        Buffer query: gyms within `distance` of any branch of a transit line, nearest first.
        """
        errors, params = {}, request.query_params
        line_id = read_number(params, "line", errors, integer=True, minimum=1, maximum=MAX_ID)
        distance = read_number(params, "distance", errors, default=500, positive=True,
                               maximum=MAX_LINE_BUFFER_M)
        raise_if_invalid(errors)

        line = TransitLine.objects.filter(pk=line_id).first()
        if line is None:
            raise exceptions.NotFound("Transit line not found.")

        qs = (
            self.filter_queryset(self.get_queryset())
            .filter(location__distance_lte=(line.route, D(m=distance)))
            .annotate(distance=Distance("location", line.route))
            .order_by("distance")
        )
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["get"])
    def connection(self, request, pk=None):
        """GET /api/gyms/{id}/connection/?lat=53.34&lng=-6.26&max_walk=2000

        Public-transport options from the user's position to this gym: nearest stops at each end
        and the routes (bus, Luas, DART) that connect them. See api/connections.py.
        """
        errors, params = {}, request.query_params
        lat = read_number(params, "lat", errors, minimum=-90, maximum=90)
        lng = read_number(params, "lng", errors, minimum=-180, maximum=180)
        max_walk = read_number(params, "max_walk", errors, default=2000, positive=True, maximum=MAX_STOP_RADIUS_M)
        raise_if_invalid(errors)
        return Response(build_connection(Point(lng, lat, srid=4326), self.get_object(), max_walk))

    @action(detail=True, methods=["get"])
    def directions(self, request, pk=None):
        """GET /api/gyms/{id}/directions/?lat=53.34&lng=-6.26&mode=foot  (mode: foot, bike or car)

        The road route from the user's position to this gym, as a GeoJSON LineString plus distance
        and time. See api/directions.py.
        """
        errors, params = {}, request.query_params
        lat = read_number(params, "lat", errors, minimum=-90, maximum=90)
        lng = read_number(params, "lng", errors, minimum=-180, maximum=180)
        mode = params.get("mode", "foot")
        if mode not in PROFILES:
            errors["mode"] = "Must be one of: " + ", ".join(PROFILES) + "."
        raise_if_invalid(errors)
        return Response(build_directions(Point(lng, lat, srid=4326), self.get_object(), mode))

    @action(detail=True, methods=["get"])
    def district(self, request, pk=None):
        """GET /api/gyms/{id}/district/ -> the district polygon containing this gym"""
        found = districts_with_gym_count().filter(boundary__contains=self.get_object().location).first()
        if found is None:
            raise exceptions.NotFound("This gym is outside all known districts.")
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


class BusRouteViewSet(viewsets.ReadOnlyModelViewSet):
    """Dublin Bus routes. The list is light (no geometry); a single route comes back as GeoJSON."""
    queryset = BusRoute.objects.annotate(stop_count=Count("stops"))
    filterset_fields = ["short_name"]
    search_fields = ["short_name", "long_name"]
    pagination_class = None

    def get_serializer_class(self):
        return BusRouteListSerializer if self.action == "list" else BusRouteSerializer

    @action(detail=True, methods=["get"])
    def stops(self, request, pk=None):
        """GET /api/transit/bus-routes/{id}/stops/ -> the stops this route serves"""
        stops = self.get_object().stops.prefetch_related("routes")
        return Response(BusStopSerializer(stops, many=True, context=self.get_serializer_context()).data)


class BusStopViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = BusStop.objects.prefetch_related("routes")
    serializer_class = BusStopSerializer
    search_fields = ["name", "code"]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        """GET /api/transit/bus-stops/?bbox=west,south,east,north

        A box is required: the map asks only for the stops it can see, instead of all ~4,300.
        """
        raw = request.query_params.get("bbox")
        if not raw:
            raise exceptions.ValidationError({"bbox": ["This parameter is required: west,south,east,north."]})
        parts = raw.split(",")
        if len(parts) != 4:
            raise exceptions.ValidationError({"bbox": ["Use four numbers: west,south,east,north."]})
        box = parse_bounds(dict(zip(("west", "south", "east", "north"), parts)))

        stops = list(self.filter_queryset(self.get_queryset()).filter(location__within=box)[:MAX_STOP_RESULTS + 1])
        response = Response(self.get_serializer(stops[:MAX_STOP_RESULTS], many=True).data)
        if len(stops) > MAX_STOP_RESULTS:
            response["X-Truncated"] = "true"   # the client should zoom in for the rest
        return response

    @action(detail=True, methods=["get"])
    def departures(self, request, pk=None):
        """GET /api/transit/bus-stops/{id}/departures/?limit=8

        The next buses at this stop, soonest first: scheduled times from the GTFS timetable, replaced
        by live times when the NTA feed is switched on. See transit/departures.py.
        """
        errors = {}
        limit = read_number(request.query_params, "limit", errors, default=8, integer=True, minimum=1, maximum=20)
        raise_if_invalid(errors)
        stop = self.get_object()
        return Response({
            "stop": {"id": stop.pk, "name": stop.name, "code": stop.code},
            "live_enabled": realtime.configured(),
            "departures": departures_at_stop(stop, limit=limit),
        })

    @action(detail=False, methods=["get"])
    def nearest(self, request):
        """GET /api/transit/bus-stops/nearest/?lat=53.34&lng=-6.26&distance=500&limit=5  (distance in metres)"""
        errors, params = {}, request.query_params
        lat = read_number(params, "lat", errors, minimum=-90, maximum=90)
        lng = read_number(params, "lng", errors, minimum=-180, maximum=180)
        distance = read_number(params, "distance", errors, default=500, positive=True, maximum=MAX_STOP_RADIUS_M)
        limit = read_number(params, "limit", errors, default=5, integer=True, minimum=1, maximum=20)
        raise_if_invalid(errors)

        origin = Point(lng, lat, srid=4326)
        stops = (
            self.get_queryset()
            .filter(location__distance_lte=(origin, D(m=distance)))   # uses the spatial index
            .annotate(distance=Distance("location", origin))
            .order_by("distance")[:limit]
        )
        return Response(self.get_serializer(stops, many=True).data)


@api_view(["GET"])
def route_view(request):
    """GET /api/route/?from_lat=53.34&from_lng=-6.26&to_lat=53.29&to_lng=-6.15&mode=foot

    The road or footpath route between any two points, as a GeoJSON LineString plus distance and
    time. The map uses it to draw the walking parts of a public transport journey along real streets.
    """
    errors, params = {}, request.query_params
    from_lat = read_number(params, "from_lat", errors, minimum=-90, maximum=90)
    from_lng = read_number(params, "from_lng", errors, minimum=-180, maximum=180)
    to_lat = read_number(params, "to_lat", errors, minimum=-90, maximum=90)
    to_lng = read_number(params, "to_lng", errors, minimum=-180, maximum=180)
    mode = params.get("mode", "foot")
    if mode not in PROFILES:
        errors["mode"] = "Must be one of: " + ", ".join(PROFILES) + "."
    raise_if_invalid(errors)
    return Response(route_between(Point(from_lng, from_lat, srid=4326), Point(to_lng, to_lat, srid=4326), mode))
