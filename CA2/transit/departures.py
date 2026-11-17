"""Next buses at a stop: the timetable, with live delays added when the NTA feed is available.

Times in the database are minutes after midnight on the day a service starts, and can pass 1440 for
trips that run after midnight (1510 is 01:10 the next morning). So at 00:30 on Wednesday we must also
look at Tuesday's service, whose late trips are still running. And near the end of the day we look
at tomorrow's first buses too.
"""
from bisect import bisect_left
from datetime import datetime, time, timedelta
from itertools import accumulate
from math import ceil
from zoneinfo import ZoneInfo

from . import realtime
from .models import BusService, BusServiceException, BusTimetable

DUBLIN = ZoneInfo("Europe/Dublin")
MINUTES_PER_DAY = 1440
LIVE_MATCH_SECONDS = 90          # a live update belongs to a scheduled departure if within this many seconds
LIVE_NEAREST_SECONDS = 20 * 60   # when an update gives no delay, match the nearest departure within this


def local_now():
    return datetime.now(DUBLIN)


def active_services(day):
    """The service_ids running on `day`: weekly pattern inside the date range, then one-off changes."""
    services = BusService.objects.filter(start_date__lte=day, end_date__gte=day)
    active = {s.service_id for s in services if s.weekdays[day.weekday()] == "1"}
    for change in BusServiceException.objects.filter(date=day).select_related("service"):
        if change.added:
            active.add(change.service.service_id)
        else:
            active.discard(change.service.service_id)
    return active


def _clock(minutes):
    minutes %= MINUTES_PER_DAY
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def service_days(now):
    """(services running that day, minutes to add to move that day's times onto today's clock) for
    yesterday, today and tomorrow. Worked out once and shared, because it costs database queries."""
    today = now.date()
    return [(active_services(today + timedelta(days=offset)), offset * MINUTES_PER_DAY) for offset in (-1, 0, 1)]


def scheduled_departures(rows, now, limit, days=None):
    """The next `limit` scheduled departures over the given BusTimetable rows (stop and route loaded)."""
    today = now.date()
    minute_now = now.hour * 60 + now.minute
    midnight = datetime.combine(today, time(0), tzinfo=DUBLIN)
    days = days if days is not None else service_days(now)

    found = {}
    for row in rows:
        decoded = {}
        for services, shift in days:
            for service_id in services:
                index = row.by_service.get(service_id)
                if index is None:
                    continue
                if index not in decoded:
                    decoded[index] = list(accumulate(row.variants[index]))   # gaps back to clock times
                times = decoded[index]
                start = bisect_left(times, minute_now - shift)               # first one that has not left yet
                for t in times[start:start + limit]:
                    minute = t + shift
                    found[(row.pk, minute)] = {
                        "route": row.route.short_name, "route_pk": row.route_id, "headsign": row.headsign,
                        "minutes": minute - minute_now, "time": _clock(minute), "next_day": minute >= MINUTES_PER_DAY,
                        "live": False, "delay_min": 0,
                        "_gtfs_route": row.route.route_id, "_gtfs_stop": row.stop.stop_id,
                        "_scheduled": (midnight + timedelta(minutes=minute)).timestamp(),
                    }
    return sorted(found.values(), key=lambda d: (d["minutes"], d["route"]))[:limit]


def add_live_times(items, now, delays):
    """Where the live feed has an update for a departure, use it: new minutes, new clock time, delay."""
    for item in items:
        updates = delays.get((item["_gtfs_route"], item["_gtfs_stop"]), [])
        scheduled = item["_scheduled"]
        delay = None
        for expected, given_delay in updates:
            if given_delay is not None and abs((expected - given_delay) - scheduled) <= LIVE_MATCH_SECONDS:
                delay = given_delay
                break
        if delay is None:
            nearest = min((u for u in updates if u[1] is None), key=lambda u: abs(u[0] - scheduled), default=None)
            if nearest and abs(nearest[0] - scheduled) <= LIVE_NEAREST_SECONDS:
                delay = nearest[0] - scheduled
        if delay is None:
            continue
        expected = datetime.fromtimestamp(scheduled + delay, DUBLIN)
        item.update(live=True, delay_min=round(delay / 60),
                    minutes=max(0, ceil((scheduled + delay - now.timestamp()) / 60)),
                    time=f"{expected:%H:%M}")
    return items


def _finish(items, now, use_live):
    """Add live times if available, then drop the internal fields used for matching."""
    if use_live:
        delays = realtime.get_delays()
        if delays:
            add_live_times(items, now, delays)
            items.sort(key=lambda d: (d["minutes"], d["route"]))
    for item in items:
        for key in [k for k in item if k.startswith("_")]:
            del item[key]
    return items


def departures_at_stop(stop, now=None, limit=8, use_live=True):
    """Next buses at one stop, soonest first, across all routes."""
    now = (now or local_now()).astimezone(DUBLIN)
    rows = BusTimetable.objects.filter(stop=stop).select_related("route", "stop")
    return _finish(scheduled_departures(rows, now, limit), now, use_live)


def next_departure(stop_id, route_id, now=None, days=None, use_live=True):
    """The next bus of one route at one stop (by database ids), or None if the timetable has none.
    Pass `days` (from service_days) when asking for several, to share the calendar lookups."""
    now = (now or local_now()).astimezone(DUBLIN)
    rows = BusTimetable.objects.filter(stop_id=stop_id, route_id=route_id).select_related("route", "stop")
    items = _finish(scheduled_departures(rows, now, 1, days), now, use_live)
    return items[0] if items else None
