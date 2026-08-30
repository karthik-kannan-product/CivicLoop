from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpRequest
from django.urls import include, path, re_path
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from civicloop.integration_readiness import integration_readiness


@require_GET
def spa_index(_request: HttpRequest) -> FileResponse:
    try:
        index_file = settings.FRONTEND_INDEX.open("rb")
    except OSError:
        raise Http404("Frontend application is unavailable.") from None

    return FileResponse(index_file, content_type="text/html")


@require_GET
@ensure_csrf_cookie
def administrator_index(_request: HttpRequest) -> FileResponse:
    if not settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED:
        raise Http404
    try:
        index_file = settings.ADMIN_FRONTEND_INDEX.open("rb")
    except OSError:
        raise Http404("Administrator application is unavailable.") from None
    return FileResponse(index_file, content_type="text/html")


@require_GET
@ensure_csrf_cookie
def integrations_administrator_index(request: HttpRequest) -> FileResponse:
    if not (
        settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED
        and settings.CIVICLOOP_INTEGRATIONS_ENABLED
    ):
        raise Http404
    return administrator_index(request)

urlpatterns = [
    path("admin/security", administrator_index, name="administrator-index"),
    path("admin/security/", administrator_index, name="administrator-index-slash"),
    path(
        "admin/integrations",
        integrations_administrator_index,
        name="integrations-administrator-index",
    ),
    path(
        "admin/integrations/",
        integrations_administrator_index,
        name="integrations-administrator-index-slash",
    ),
    path("internal/django-admin/", admin.site.urls),
    path("api/", include("api_contracts.urls")),
    path("api/v1/", include("launchloop.urls")),
    path("api/v1/", include("agents.urls")),
    path("api/v1/admin/", include("identity.urls")),
    path("api/v1/admin/", include("integrations.urls")),
    path(
        "api/v1/admin/integrations/status",
        integration_readiness,
        name="admin-integrations-readiness",
    ),
    path("api/v1/health/", include("health.urls")),
    re_path(
        r"^(?!api(?:/|$)|admin(?:/|$)|internal(?:/|$)|assets(?:/|$)|static(?:/|$)).*$",
        spa_index,
        name="spa-index",
    ),
]
