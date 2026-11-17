"""Error views. /api/ paths get JSON in the same shape as the API; everything else gets a friendly HTML page."""
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.http import HttpResponse

from api.exceptions import error_body

PAGES = {
    400: ("bad_request", "Bad request", "That request could not be understood."),
    403: ("forbidden", "Access denied", "You don't have permission to view this page."),
    404: ("not_found", "Page not found", "We couldn't find the page you were looking for."),
    500: ("server_error", "Something went wrong", "An unexpected error occurred on our side. Please try again shortly."),
}


def _respond(request, status):
    code, title, message = PAGES[status]
    if request.path.startswith("/api/"):
        return JsonResponse(error_body(message, code), status=status)
    # Rendered without request/context processors so it still works if the database is down
    html = render_to_string("dashboard/error.html", {"status": status, "title": title, "message": message})
    return HttpResponse(html, status=status)


def bad_request(request, exception=None):
    return _respond(request, 400)


def permission_denied(request, exception=None):
    return _respond(request, 403)


def not_found(request, exception=None):
    return _respond(request, 404)


def server_error(request):
    return _respond(request, 500)
