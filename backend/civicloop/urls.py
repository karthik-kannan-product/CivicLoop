from django.conf import settings
from django.contrib import admin
from django.http import FileResponse, HttpRequest
from django.urls import include, path
from django.views.decorators.http import require_GET


@require_GET
def spa_index(_request: HttpRequest) -> FileResponse:
    return FileResponse(settings.FRONTEND_INDEX.open("rb"), content_type="text/html")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", include("health.urls")),
    path("", spa_index, name="spa-index"),
]
