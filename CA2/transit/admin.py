from django.contrib.gis import admin

from .models import TransitLine, TransitStop


@admin.register(TransitLine)
class TransitLineAdmin(admin.GISModelAdmin):
    list_display = ["name", "mode", "colour"]
    list_filter = ["mode"]


@admin.register(TransitStop)
class TransitStopAdmin(admin.GISModelAdmin):
    list_display = ["name", "line"]
    list_filter = ["line"]
    search_fields = ["name"]
