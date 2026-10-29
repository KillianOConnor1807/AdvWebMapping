from django.contrib.gis import admin

from .models import Gym


@admin.register(Gym)
class GymAdmin(admin.GISModelAdmin):
    list_display = ["name", "district", "level", "beginner_friendly", "price_per_month", "owner"]
    list_filter = ["level", "beginner_friendly", "district"]
    search_fields = ["name", "address", "eircode"]
    readonly_fields = ["district", "created_at", "last_updated"]  # district is derived from location
    # Open the location map widget over Dublin rather than the whole world
    gis_widget_kwargs = {"attrs": {"default_lon": -6.26, "default_lat": 53.35, "default_zoom": 11}}

    def save_model(self, request, obj, form, change):
        if not change and obj.owner is None:
            obj.owner = request.user
        super().save_model(request, obj, form, change)
