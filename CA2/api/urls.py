from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    BusRouteViewSet, BusStopViewSet, DistrictViewSet, GymViewSet, TransitLineViewSet, TransitStopViewSet,
)

router = DefaultRouter()
router.register(r'gyms', GymViewSet, basename='gym')
router.register(r'transit/lines', TransitLineViewSet, basename='transit-line')
router.register(r'transit/stops', TransitStopViewSet, basename='transit-stop')
router.register(r'transit/bus-routes', BusRouteViewSet, basename='bus-route')
router.register(r'transit/bus-stops', BusStopViewSet, basename='bus-stop')
router.register(r'districts', DistrictViewSet, basename='district')

urlpatterns = [
    path('', include(router.urls)),
]