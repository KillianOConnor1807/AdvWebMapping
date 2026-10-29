# dashboard/views.py
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db import connection
from django.db.models import OuterRef, Subquery
from django.shortcuts import render

from districts.models import District
from gyms.models import Gym
from transit.models import TransitLine

# O'Connell Bridge, used as the reference point for city-centre statistics
DUBLIN_CENTRE = Point(-6.2603, 53.3498, srid=4326)
TRANSIT_BUFFERS_M = (250, 500, 1000)
CENTRE_BANDS_KM = ((0, 2), (2, 5), (5, 10), (10, None))

# Nearest stop to every gym in one query. The KNN operator (<->) uses the GiST index to
# pick 5 candidate stops cheaply; the true distance in metres then chooses between them.
NEAREST_STOP_SQL = """
    SELECT g.name, s.name, l.name, l.colour, s.metres
    FROM gyms_gym g
    CROSS JOIN LATERAL (
        SELECT c.name, c.line_id, c.metres
        FROM (
            SELECT st.name, st.line_id,
                   ST_Distance(st.location::geography, g.location::geography) AS metres
            FROM transit_transitstop st
            ORDER BY st.location <-> g.location
            LIMIT 5
        ) c
        ORDER BY c.metres
        LIMIT 1
    ) s
    JOIN transit_transitline l ON l.id = s.line_id
    ORDER BY s.metres
"""


def index(request):
    """Main map page"""
    return render(request, 'dashboard/index.html', {'total_gyms': Gym.objects.count()})


def _avg(values):
    return round(sum(values) / len(values), 2) if values else None


def _fmt_distance(metres):
    return f'{metres / 1000:.1f} km' if metres >= 1000 else f'{round(metres)} m'


def analytics(request):
    """Gym statistics built from PostGIS spatial queries"""
    # Point-in-polygon: tag every gym with the district polygon that contains it (one query)
    containing = District.objects.filter(boundary__contains=OuterRef('location'))
    gyms = list(
        Gym.objects.annotate(
            district_code=Subquery(containing.values('code')[:1]),
            district_name=Subquery(containing.values('name')[:1]),
            district_order=Subquery(containing.values('sort_order')[:1]),
            centre_distance=Distance('location', DUBLIN_CENTRE),
        ).order_by('name')
    )
    priced = [g for g in gyms if g.price_per_month is not None]

    # ① Gyms by district
    by_district = {}
    for g in gyms:
        if g.district_code:
            row = by_district.setdefault(g.district_code, {
                'code': g.district_code, 'name': g.district_name, 'order': g.district_order, 'gyms': [],
            })
            row['gyms'].append(g)
    district_rows = sorted(by_district.values(), key=lambda r: (-len(r['gyms']), r['order']))
    for row in district_rows:
        row['count'] = len(row['gyms'])
        row['avg_price'] = _avg([g.price_per_month for g in row['gyms'] if g.price_per_month is not None])
        row['names'] = ', '.join(g.name for g in row['gyms'])

    # ② Transit access: buffer queries around each line
    transit_rows, near_any_line = [], set()
    for line in TransitLine.objects.all():
        counts = []
        for metres in TRANSIT_BUFFERS_M:
            ids = set(Gym.objects.filter(location__distance_lte=(line.route, D(m=metres)))
                      .values_list('id', flat=True))
            counts.append(len(ids))
            if metres == 500:
                near_any_line |= ids
        transit_rows.append({'name': line.name, 'colour': line.colour, 'counts': counts})

    with connection.cursor() as cursor:
        cursor.execute(NEAREST_STOP_SQL)
        nearest_stops = [
            {'gym': gym, 'stop': stop, 'line': line, 'colour': colour, 'label': _fmt_distance(metres)}
            for gym, stop, line, colour, metres in cursor.fetchall()
        ]

    # ③ Distance from the city centre, grouped into bands
    bands = []
    for low, high in CENTRE_BANDS_KM:
        in_band = [g for g in gyms if g.centre_distance.km >= low and (high is None or g.centre_distance.km < high)]
        label = f'{low}–{high} km' if high else f'{low}+ km'
        bands.append({'label': label, 'count': len(in_band), 'names': ', '.join(g.name for g in in_band)})
    max_band = max((b['count'] for b in bands), default=0) or 1
    for b in bands:
        b['percent'] = round(100 * b['count'] / max_band)
    by_distance = sorted(gyms, key=lambda g: g.centre_distance.m)

    # ④ Price and level
    level_rows = []
    for value, label in Gym.LEVEL_CHOICES:
        at_level = [g for g in gyms if g.level == value]
        level_rows.append({
            'label': label, 'count': len(at_level),
            'avg_price': _avg([g.price_per_month for g in at_level if g.price_per_month is not None]),
        })
    cheapest = min((g.price_per_month for g in priced), default=None)
    dearest = max((g.price_per_month for g in priced), default=None)

    context = {
        'summary': {
            'total': len(gyms),
            'avg_price': _avg([g.price_per_month for g in priced]),
            'priced': len(priced),
            'beginner_friendly': sum(g.beginner_friendly for g in gyms),
            'near_transit': len(near_any_line),
            'districts_with_gyms': len(district_rows),
            'districts_total': District.objects.count(),
        },
        'district_rows': district_rows,
        'transit_buffer_labels': [_fmt_distance(m) for m in TRANSIT_BUFFERS_M],
        'transit_rows': transit_rows,
        'nearest_stops': nearest_stops,
        'bands': bands,
        'closest': by_distance[0] if by_distance else None,
        'farthest': by_distance[-1] if by_distance else None,
        'level_rows': level_rows,
        'cheapest': cheapest,
        'cheapest_names': ', '.join(g.name for g in priced if g.price_per_month == cheapest),
        'dearest': dearest,
        'dearest_names': ', '.join(g.name for g in priced if g.price_per_month == dearest),
        'not_listed': len(gyms) - len(priced),
    }
    return render(request, 'dashboard/analytics.html', context)
