from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponseBase, HttpResponseNotFound


class IntegrationFeatureGateMiddleware:
    """Hide every integration API route before CSRF or authentication runs."""

    def __init__(
        self, get_response: Callable[[HttpRequest], HttpResponseBase]
    ) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponseBase:
        integration_api = request.path == "/api/v1/admin/integrations" or request.path.startswith(
            "/api/v1/admin/integrations/"
        )
        if integration_api and not (
            settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED
            and settings.CIVICLOOP_INTEGRATIONS_ENABLED
        ):
            return HttpResponseNotFound()
        return self.get_response(request)
