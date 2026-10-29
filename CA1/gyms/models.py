from django.conf import settings
from django.contrib.gis.db import models
from django.core.validators import MinValueValidator

from districts.models import District


class Gym(models.Model):
    LEVEL_CHOICES = [
        ("beginner", "Beginner friendly"),
        ("mixed", "All levels"),
        ("competition", "Competition focused"),
    ]

    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    eircode = models.CharField(max_length=8, blank=True)
    # Filled in automatically from `location` on save (see save() below)
    district = models.CharField(max_length=100, blank=True, help_text="e.g. Dublin 8")
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="mixed")
    beginner_friendly = models.BooleanField(default=True)
    price_per_month = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0)],
    )
    classes = models.TextField(blank=True, help_text="Free-text class timetable")

    # Source of truth for location; spatial_index=True creates the GiST index
    location = models.PointField(srid=4326, spatial_index=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="gyms",
    )
    data_source = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "gyms"

    def save(self, *args, **kwargs):
        # Point-in-polygon lookup keeps the district in sync with the location
        found = District.containing(self.location) if self.location else None
        self.district = found.name if found else ""
        super().save(*args, **kwargs)

    @property
    def latitude(self):
        return self.location.y

    @property
    def longitude(self):
        return