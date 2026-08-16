from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from identity.services.sessions import enforce_administrator_session

RECOVERY_ROUTE_ALLOWLIST = frozenset(
    {
        "admin-auth-logout",
        "admin-security-status",
        "admin-totp-confirmation",
        "admin-totp-enrollment",
    }
)


class AdministratorSessionMiddleware(MiddlewareMixin):
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        super().__init__(get_response)

    def process_request(self, request: HttpRequest) -> None:
        request.administrator_session = enforce_administrator_session(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        metadata = getattr(request, "administrator_session", None)
        if (
            settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED
            and metadata is not None
            and metadata.recovery_restricted
            and request.resolver_match.view_name not in RECOVERY_ROUTE_ALLOWLIST
        ):
            if request.resolver_match.view_name in {
                "admin-integration-list",
                "admin-integration-credential",
                "admin-integration-configuration",
                "admin-integration-test",
                "admin-integration-disable",
                "admin-integration-audit",
            }:
                from integrations.views import recovery_restricted_response

                return recovery_restricted_response(request)
            return JsonResponse(
                {
                    "type": "https://civicloop.karthikkannan.ca/problems/recovery-restricted",
                    "title": "Recovery required",
                    "status": 403,
                    "detail": "Complete administrator account recovery to continue.",
                    "instance": request.path,
                    "code": "recovery_restricted",
                    "message": "Complete administrator account recovery to continue.",
                },
                status=403,
                content_type="application/problem+json",
                headers={"Cache-Control": "no-store"},
            )
        return None
