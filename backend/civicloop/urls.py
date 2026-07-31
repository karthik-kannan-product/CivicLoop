from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, Http404, HttpRequest
from django.urls import include, path, re_path
from django.views.decorators.http import require_GET


@require_GET
def spa_index(_request: HttpRequest) -> FileResponse:
    try:
        index_file = settings.FRONTEND_INDEX.open("rb")
    except OSError:
        raise Http404("Frontend application is unavailable.") from None

    return FileResponse(index_file, content_type="text/html")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", include("health.urls")),
    re_path(
        r"^(?!api(?:/|$)|admin(?:/|$)|assets(?:/|$)|static(?:/|$)).*$",
        spa_index,
        name="spa-index",
    ),
]
