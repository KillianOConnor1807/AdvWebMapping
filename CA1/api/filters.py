from django.db.models import Q
from django_filters import rest_framework as filters

from districts.models import District
from gyms.models import Gym


class GymFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains", max_length=30)
    district = filters.CharFilter(method="filter_district", help_text="District code or name, e.g. D08")
    level = filters.MultipleChoiceFilter(choices=Gym.LEVEL_CHOICES)
    price_min = filters.NumberFilter(field_name="price_per_month", lookup_expr="gte")
    price_max = filters.NumberFilter(field_name="price_per_month", lookup_expr="lte")

    class Meta:
        model = Gym
        fields = ["beginner_friendly"]

    def filter_district(self, queryset, name, value):
        # Containment query: gyms whose point lies within the district polygon
        district = District.objects.filter(Q(code__iexact=value) | Q(name__iexact=value)).first()
        if district is None:
            return queryset.none()
        return queryset.filter(location__within=district.boundary)
