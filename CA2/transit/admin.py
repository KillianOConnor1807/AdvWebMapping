from django.contrib.gis import admin

from .models import BusRoute, BusService, BusStop, BusTimetable, TransitLine, TransitStop


@admin.register(TransitLine)
class TransitLineAdmin(admin.GISModelAdmin):
    list_display = ["name", "mode", "colour"]
    list_filter = ["mode"]


@admin.register(TransitStop)
class TransitStopAdmin(admin.GISModelAdmin):
    list_display = ["name", "line"]
    list_filter = ["line"]
    search_fields = ["name"]


@admin.register(BusRoute)
class BusRouteAdmin(admin.GISModelAdmin):
    list_display = ["short_name", "long_name"]
    search_fields = ["short_name", "long_name"]


@admin.register(BusStop)
class BusStopAdmin(admin.GISModelAdmin):
    list_display = ["name", "code"]
    search_fields = ["name", "code"]


@admin.register(BusService)
class BusServiceAdmin(admin.ModelAdmin):
    list_display = ["service_id", "weekdays", "start_date", "end_date"]


@admin.register(BusTimetable)
class BusTimetableAdmin(admin.ModelAdmin):
    list_display = ["route", "stop", "headsign"]
    search_fields = ["route__short_name", "stop__name"]
    raw_id_fields = ["stop", "route"]
