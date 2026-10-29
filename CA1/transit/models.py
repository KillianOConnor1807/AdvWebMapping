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
