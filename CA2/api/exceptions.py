"""One consistent JSON error format for the whole API.

Every error response has this shape:

    {"error": "Short, human-readable message",
     "code": "machine_readable_code",
     "details": {...}}          <- only when there is more to say, e.g. per-field validation errors

DRF's own errors (validation, 404, permissions, throttling) are rewrapped into it, and errors DRF
does not handle (database, geometry, unexpected bugs) become JSON instead of Django's HTML error page.
"""
import logging

from django.conf import settings
from django.contrib.gis.gdal import GDALException
from django.contrib.gis.geos import GEOSException
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DataError, IntegrityError, InterfaceError, OperationalError
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.views import set_rollback

logger = logging.getLogger(__name__)

# Machine-readable code for each HTTP status DRF can produce
STATUS_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    415: "unsupported_media_type",
    429: "throttled",
}
MAX_SUMMARY_FIELDS = 3


def error_body(message, code, details=None):
    body = {"error": message, "code": code}
    if details:
        body["details"] = details
    return body


def _messages(value):
    """Flatten DRF error structures (dicts, lists, ErrorDetail) into plain strings."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _messages(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _messages(item)
    else:
        yield str(value)


def _summary(details):
    """One readable sentence from validation details, e.g. 'lat: This parameter is required.'"""
    if not isinstance(details, dict):
        return " ".join(_messages(details))
    parts = []
    for field, value in details.items():
        text = " ".join(_messages(value))
        parts.append(text if field == "non_field_errors" else f"{field}: {text}")
    shown = "; ".join(parts[:MAX_SUMMARY_FIELDS])
    hidden = len(parts) - MAX_SUMMARY_FIELDS
    return f"{shown} (and {hidden} more)" if hidden > 0 else shown


def _respond(status_code, code, message, details=None):
    set_rollback()  # don't leave a half-finished database transaction behind
    return Response(error_body(message, code, details), status=status_code)


def api_exception_handler(exc, context):
    # 1. Errors DRF understands: keep its status code and headers (e.g. Retry-After), change the body
    response = drf_exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, exceptions.ValidationError):
            details = response.data
            response.data = error_body(_summary(details) or "Validation failed.", "validation_error", details)
        else:
            detail = response.data.get("detail") if isinstance(response.data, dict) else response.data
            response.data = error_body(str(detail), STATUS_CODES.get(response.status_code, "error"))
        return response

    # 2. Errors DRF does not handle: turn them into JSON instead of an HTML error page
    path = getattr(context.get("request"), "path", "?")

    if isinstance(exc, DjangoValidationError):
        details = exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
        return _respond(400, "validation_error", _summary(details) or "Validation failed.", details)

    if isinstance(exc, (GEOSException, GDALException)):
        logger.warning("Unreadable geometry on %s: %s", path, exc)
        return _respond(400, "invalid_geometry", "The geometry sent is not valid.")

    if isinstance(exc, DataError):  # the database refused a value (wrong type, too long, out of range)
        logger.warning("Database rejected data on %s: %s", path, exc)
        return _respond(400, "invalid_data", "Some of the values sent were rejected. Check them and try again.")

    if isinstance(exc, IntegrityError):  # e.g. a uniqueness rule
        logger.warning("Integrity error on %s: %s", path, exc)
        return _respond(409, "conflict", "This conflicts with data that already exists.")

    if isinstance(exc, (OperationalError, InterfaceError)):  # database down or connection lost
        logger.error("Database unavailable on %s", path, exc_info=exc)
        return _respond(503, "service_unavailable", "The database is temporarily unavailable. Please try again shortly.")

    if settings.DEBUG:
        return None  # while developing, let Django show its full traceback page

    logger.error("Unhandled error on %s", path, exc_info=exc)
    return _respond(500, "server_error", "Something went wrong on our side. The error has been logged.")
