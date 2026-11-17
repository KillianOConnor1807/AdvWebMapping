"""Live bus delays from the National Transport Authority's GTFS-Realtime feed.

The feed is protocol-buffer data (a compact binary format). Rather than add a library, this file
reads the few fields we need. The server fetches it, keeps it for REALTIME_CACHE_SECONDS, and
everyone shares that copy: browsers never contact the NTA, and the API key never leaves the server.

Switched on by setting NTA_API_KEY (in .env). Without a key, or if the NTA is unreachable, the app
quietly shows scheduled times instead.

Data: National Transport Authority, licensed CC BY 4.0.
"""
import logging
import threading
import time
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 300     # keep showing the last good copy for up to 5 minutes if the NTA is down
RETRY_AFTER_SECONDS = 30      # after a failure, do not ask again for this long
TIMEOUT_SECONDS = 6
SKIPPED, NO_DATA = 1, 2       # stop_time_update schedule relationships that carry no time

_lock = threading.Lock()
_cache = {"fetched_at": 0.0, "tried_at": 0.0, "delays": None}


# ----- Reading protocol buffers -----
def _varint(buffer, position):
    result = shift = 0
    while True:
        byte = buffer[position]
        position += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, position
        shift += 7


def _fields(buffer):
    """Yield (field number, value) for each field in a message. Values are ints or bytes."""
    position, end = 0, len(buffer)
    while position < end:
        key, position = _varint(buffer, position)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, position = _varint(buffer, position)
        elif wire == 2:
            length, position = _varint(buffer, position)
            value = buffer[position:position + length]
            position += length
        elif wire == 1:
            value, position = buffer[position:position + 8], position + 8
        elif wire == 5:
            value, position = buffer[position:position + 4], position + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, value


def _signed(value):
    return value - (1 << 64) if value >= 1 << 63 else value     # negative numbers are sent as 64-bit


def _event(buffer):
    """A StopTimeEvent: returns (delay seconds or None, absolute time or None)."""
    delay = at = None
    for number, value in _fields(buffer):
        if number == 1:
            delay = _signed(value)
        elif number == 2:
            at = _signed(value)
    return delay, at


def decode_delays(data):
    """Turn a FeedMessage into {(route_id, stop_id): [(expected epoch seconds, delay seconds or None)]}."""
    delays = {}
    for number, entity in _fields(data):
        if number != 2:                                   # FeedMessage.entity
            continue
        for number, update in _fields(entity):
            if number != 3:                               # FeedEntity.trip_update
                continue
            route_id, cancelled, stops = None, False, []
            for number, value in _fields(update):
                if number == 1:                           # TripUpdate.trip (a TripDescriptor)
                    for n, v in _fields(value):
                        if n == 5:
                            route_id = v.decode("utf-8", "replace")
                        elif n == 4 and v == 3:           # schedule_relationship CANCELED
                            cancelled = True
                elif number == 2:                         # TripUpdate.stop_time_update
                    stops.append(value)
            if route_id is None or cancelled:
                continue
            for stop in stops:
                stop_id, event, relationship = None, None, 0
                for n, v in _fields(stop):
                    if n == 4:
                        stop_id = v.decode("utf-8", "replace")
                    elif n in (2, 3) and (n == 3 or event is None):   # prefer the departure over the arrival
                        event = _event(v)
                    elif n == 5:
                        relationship = v
                if stop_id is None or event is None or relationship in (SKIPPED, NO_DATA):
                    continue
                delay, at = event
                if at is None:
                    continue
                delays.setdefault((route_id, stop_id), []).append((at, delay))
    return delays


# ----- Fetching and sharing -----
def configured():
    return bool(getattr(settings, "NTA_API_KEY", ""))


def fetch_feed():
    """Download the raw feed from the NTA. Isolated so tests can replace it without any network."""
    request = urllib.request.Request(settings.NTA_GTFSR_URL, headers={
        settings.NTA_API_KEY_HEADER: settings.NTA_API_KEY,
        "Accept": "application/x-protobuf",
        "User-Agent": "MuayThaiGymFinder/1.0 (TU Dublin student project)",
    })
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def get_delays():
    """The current delays, or None when live data is off or unavailable. Cached and shared."""
    if not configured():
        return None
    now = time.time()
    with _lock:
        fresh = _cache["delays"] is not None and now - _cache["fetched_at"] < settings.REALTIME_CACHE_SECONDS
        if not fresh and now - _cache["tried_at"] >= RETRY_AFTER_SECONDS:
            _cache["tried_at"] = now
            try:
                _cache["delays"] = decode_delays(fetch_feed())
                _cache["fetched_at"] = now
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, IndexError) as exc:
                logger.warning("Live bus feed failed: %s", exc)
        if _cache["delays"] is not None and now - _cache["fetched_at"] > STALE_AFTER_SECONDS:
            _cache["delays"] = None
        return _cache["delays"]


def reset_cache():
    """Forget the stored copy (used by tests)."""
    _cache.update(fetched_at=0.0, tried_at=0.0, delays=None)
