from django.contrib.gis.db import models


class District(models.Model):
    code = models.CharField(max_length=10, unique=True, help_text="e.g. D08, D6W, DLR")
    name = models.CharField(max_length=100)
    osm_id = models.BigIntegerField(null=True, blank=True, help_text="OpenStreetMap relation id")
    sort_order = models.PositiveSmallIntegerField(default=0)
    # MultiPolygon because some areas (e.g. Dublin 5, the DLR coast) are made of several parts
    boundary = models.MultiPolygonField(srid=4326, spatial_index=True)

    class Meta:
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.name

    @classmethod
    def containing(cls, point):
        """The district whose boundary contains `point`, or None (point-in-polygon)."""
        return cls.objects.filter(boundary__contains=point).first()
