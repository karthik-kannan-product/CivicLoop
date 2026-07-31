from django.http import JsonResponse
from django.views.decorators.http import require_GET

from .checks import readiness_status


@require_GET
def live(_request) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@require_GET
def ready(_request) -> JsonResponse:
    statuses = readiness_status()
    dependencies = {status.name: {"ready": status.ready} for status in statuses}
    all_ready = all(status.ready for status in statuses)
    return JsonResponse(
        {
            "status": "ready" if all_ready else "not_ready",
            "dependencies": dependencies,
        },
        status=200 if all_ready else 503,
    )
