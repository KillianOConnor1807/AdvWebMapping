from django.contrib.gis.db import models
from django.core.validators import RegexValidator


class TransitLine(models.Model):
    MODE_CHOICES = [
        ("luas", "Luas"),
        ("dart", "DART"),
    ]

    name = models.CharField(max_length=100, unique=True)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    colour = models.CharField(
        max_length=7, default="#e4002b",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a hex colour like #e4002b")],
    )
    # MultiLineString so a line with branches (e.g. Red Line to Tallaght and Saggart) is one feature
    route = models.MultiLineStringField(srid=4326, spatial_index=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TransitStop(models.Model):
    line = models.ForeignKey(TransitLine, on_delete=models.CASCADE, related_name="stops")
    name = models.CharField(max_length=100)
    location = models.PointField(srid=4326, spatial_index=True)

    class Meta:
        ordering = ["line", "name"]
        constraints = [
            models.UniqueConstraint(fields=["line", "name"], name="unique_stop_per_line"),
        ]

    def __str__(self):
        return f"{self.name} ({self.line})"


class BusRoute(models.Model):
    """A Dublin Bus route such as 46A, with its drawn path (from the GTFS feed's shapes)."""
    route_id = models.CharField(max_length=40, unique=True, help_text="GTFS route_id (can change between feed versions)")
    short_name = models.CharField(max_length=10, help_text="The number on the bus, e.g. 46A")
    long_name = models.CharField(max_length=150, blank=True)
    # One line per direction, simplified when loaded so the whole network stays light
    route = models.MultiLineStringField(srid=4326, spatial_index=True)

    class Meta:
        ordering = ["short_name"]

    def __str__(self):
        return f"{self.short_name} {self.long_name}".strip()


class BusStop(models.Model):
    stop_id = models.CharField(max_length=40, unique=True, help_text="GTFS stop_id")
    code = models.CharField(max_length=10, blank=True, help_text="The number on the stop sign")
    name = models.CharField(max_length=150)
    location = models.PointField(srid=4326, spatial_index=True)
    # A stop serves many routes and a route serves many stops
    routes = models.ManyToManyField(BusRoute, related_name="stops", blank=True)

    class Meta:
        ordering = ["name", "stop_id"]

    def __str__(self):
        return f"{self.name} ({self.code})" if self.code else self.name
