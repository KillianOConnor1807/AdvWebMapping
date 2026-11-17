from django.contrib.gis import admin

from .models import District


@admin.register(District)
class DistrictAdmin(admin.GISModelAdmin):
    list_display = ["code", "name", "osm_id"]
    search_fields = ["code", "name"]
