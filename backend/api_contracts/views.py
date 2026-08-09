from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

MEDIA_TYPES = {
    ".json": "application/schema+json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def _contract_path(group: str, relative_path: str) -> tuple[Path, str]:
    root = settings.API_CONTRACT_ROOTS.get(group)
    if root is None:
        raise Http404("Unknown contract group.")

    resolved_root = Path(root).resolve()
    candidate = (resolved_root / relative_path).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise Http404("Contract path is outside the allowed root.")
    if candidate.suffix not in MEDIA_TYPES or not candidate.is_file():
        raise Http404("Contract asset does not exist.")
    return candidate, MEDIA_TYPES[candidate.suffix]


@require_GET
def contract_asset(
    _request: HttpRequest,
    group: str,
    relative_path: str,
) -> FileResponse:
    path, media_type = _contract_path(group, relative_path)
    return FileResponse(path.open("rb"), content_type=media_type)


@ensure_csrf_cookie
@require_GET
def swagger_ui(_request: HttpRequest) -> HttpResponse:
    try:
        source = settings.SWAGGER_INDEX.read_text(encoding="utf-8")
    except OSError:
        raise Http404("Swagger UI is unavailable.") from None
    return HttpResponse(source, content_type="text/html; charset=utf-8")
