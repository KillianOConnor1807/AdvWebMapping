class PermissionsPolicyMiddleware:
    """Tell browsers which powerful features pages may use.

    Only geolocation is allowed (for the "Use my location" button); everything else is blocked.
    """

    POLICY = "geolocation=(self), camera=(), microphone=(), payment=(), usb=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("Permissions-Policy", self.POLICY)
        return response
