from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.http import require_GET
from integrations.crypto import load_integration_key_ring
from integrations.exceptions import IntegrationCryptoError


def _require_features() -> None:
    if not (
        settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED
        and settings.CIVICLOOP_INTEGRATIONS_ENABLED
    ):
        raise Http404


@require_GET
def integration_readiness(_request: HttpRequest) -> JsonResponse:
    _require_features()
    key_file = settings.CIVICLOOP_INTEGRATION_KEY_FILE
    try:
        if key_file is None:
            raise IntegrationCryptoError()
        load_integration_key_ring(key_file)
    except IntegrationCryptoError:
        return JsonResponse(
            {"status": "not_ready"},
            status=503,
            headers={"Cache-Control": "no-store"},
        )
    return JsonResponse({"status": "ready"}, headers={"Cache-Control": "no-store"})
