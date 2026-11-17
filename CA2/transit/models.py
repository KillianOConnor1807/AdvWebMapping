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


class BusService(models.Model):
    """A GTFS 'service': a set of days a group of trips runs, e.g. 'Monday to Friday until February'."""
    service_id = models.CharField(max_length=40, unique=True)
    weekdays = models.CharField(max_length=7, help_text="Seven 0/1 flags, Monday first: runs on that day of the week?")
    start_date = models.DateField()
    end_date = models.DateField()

    def __str__(self):
        return f"Service {self.service_id} ({self.start_date} to {self.end_date})"


class BusServiceException(models.Model):
    """A one-off change to a service, such as 'does not run on 25 December'."""
    service = models.ForeignKey(BusService, on_delete=models.CASCADE, related_name="exceptions")
    date = models.DateField(db_index=True)
    added = models.BooleanField(help_text="True: runs on this date although its weekly pattern says no. False: does not run.")


class BusTimetable(models.Model):
    """Every departure from one stop on one route.

    `variants` holds lists of departure times (minutes after midnight, stored as the first time then
    the gaps between times; 1510 means 01:10 the next morning). `by_service` says which list applies
    for each service, so the same times are not stored twice.
    """
    stop = models.ForeignKey(BusStop, on_delete=models.CASCADE, related_name="timetables")
    route = models.ForeignKey(BusRoute, on_delete=models.CASCADE, related_name="timetables")
    headsign = models.CharField(max_length=150, blank=True, help_text="Where the bus is heading")
    variants = models.JSONField()
    by_service = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["stop", "route"], name="one_timetable_per_stop_and_route")]

    def __str__(self):
        return f"{self.route.short_name} at {self.stop}"
