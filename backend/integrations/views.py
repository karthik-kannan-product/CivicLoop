import json
from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods
from identity.models import AdministratorProfile
from identity.services.sessions import administrator_session_is_fresh

from integrations.exceptions import IntegrationCryptoError, SecretUnavailable
from integrations.models import IntegrationConnection, IntegrationHealthCheck
from integrations.services import (
    IntegrationServiceError,
    disable_connection,
    integration_audit,
    list_connections,
    replace_credential,
    test_connection,
    update_configuration,
)

MAX_REQUEST_BYTES = 20 * 1024


def _require_feature() -> None:
    if not settings.CIVICLOOP_INTEGRATIONS_ENABLED:
        raise Http404


def _problem(
    request: HttpRequest, status: int, code: str, title: str, message: str
) -> JsonResponse:
    return JsonResponse(
        {
            "type": f"https://civicloop.karthikkannan.ca/problems/{code}",
            "title": title,
            "status": status,
            "detail": message,
            "instance": request.path,
            "code": code,
            "message": message,
        },
        status=status,
        content_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


def _administrator(request: HttpRequest, *, fresh: bool = False):
    metadata = getattr(request, "administrator_session", None)
    if metadata is None or metadata.recovery_restricted:
        return None, _problem(
            request,
            401,
            "authentication_required",
            "Authentication required",
            "Administrator authentication is required.",
        )
    if fresh and not administrator_session_is_fresh(metadata):
        return None, _problem(
            request,
            403,
            "fresh_verification_required",
            "Fresh verification required",
            "Complete fresh password and authenticator verification to continue.",
        )
    return metadata, None


def _body(request: HttpRequest) -> dict[str, object] | None:
    if len(request.body) > MAX_REQUEST_BYTES:
        return None
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _expected_version(body: dict[str, object] | None) -> int | None:
    value = body.get("expected_version") if body is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _service_problem(request: HttpRequest, error: IntegrationServiceError) -> JsonResponse:
    mapping = {
        "provider_not_found": (
            404,
            "Integration not found",
            "The requested integration is unavailable.",
        ),
        "version_conflict": (
            409,
            "Version conflict",
            "The integration changed; refresh and try again.",
        ),
        "invalid_request": (400, "Invalid request", "The integration request is invalid."),
        "invalid_pagination": (
            400,
            "Invalid pagination",
            "The audit pagination parameters are invalid.",
        ),
        "integration_unavailable": (
            409,
            "Integration unavailable",
            "The integration is not configured for testing.",
        ),
    }
    status, title, message = mapping.get(
        error.code,
        (503, "Integration unavailable", "The integration service is temporarily unavailable."),
    )
    return _problem(request, status, error.code, title, message)


def _connection_payload(connection: IntegrationConnection) -> dict[str, object]:
    secret = connection.secret if connection.secret_id else None
    rotated_at = None
    actor_id = None
    if secret is not None:
        rotated = secret.replaced_at or secret.created_at
        rotated_at = rotated.isoformat() if rotated else None
        user_id = secret.replaced_by_id or secret.created_by_id
        actor_id = (
            str(
                AdministratorProfile.objects.filter(user_id=user_id)
                .values_list("id", flat=True)
                .first()
            )
            if user_id
            else None
        )
    return {
        "provider": connection.provider,
        "state": connection.state,
        "capabilities": connection.capabilities,
        "configuration": connection.configuration,
        "version": connection.version,
        "created_at": connection.created_at.isoformat(),
        "updated_at": connection.updated_at.isoformat(),
        "credential_rotated_at": rotated_at,
        "responsible_actor_id": actor_id,
        "last_successful_test_at": connection.last_successful_test_at.isoformat()
        if connection.last_successful_test_at
        else None,
        "last_failure_category": connection.last_failure_category or None,
    }


@require_GET
def connections(request: HttpRequest) -> JsonResponse:
    _require_feature()
    _metadata, denied = _administrator(request)
    if denied is not None:
        return denied
    return JsonResponse(
        {"connections": [_connection_payload(row) for row in list_connections()]},
        headers={"Cache-Control": "no-store"},
    )


@require_http_methods(["PUT"])
def credential(request: HttpRequest, provider: str) -> JsonResponse:
    _require_feature()
    metadata, denied = _administrator(request, fresh=True)
    if denied is not None:
        return denied
    body = _body(request)
    value = body.get("credential") if body is not None else None
    version = _expected_version(body)
    if not isinstance(value, str) or not 1 <= len(value) <= 16384 or version is None:
        return _problem(
            request,
            400,
            "invalid_request",
            "Invalid request",
            "The integration request is invalid.",
        )
    try:
        connection = replace_credential(
            provider=provider,
            credential=value.encode(),
            expected_version=version,
            actor=metadata,
            request=request,
        )
    except (IntegrationCryptoError, SecretUnavailable):
        return _problem(
            request,
            503,
            "integration_unavailable",
            "Integration unavailable",
            "The integration service is temporarily unavailable.",
        )
    except IntegrationServiceError as error:
        return _service_problem(request, error)
    return JsonResponse(_connection_payload(connection), headers={"Cache-Control": "no-store"})


@require_http_methods(["PATCH"])
def configuration(request: HttpRequest, provider: str) -> JsonResponse:
    _require_feature()
    metadata, denied = _administrator(request, fresh=True)
    if denied is not None:
        return denied
    body = _body(request)
    config = body.get("configuration") if body is not None else None
    version = _expected_version(body)
    if (
        not isinstance(config, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in config.items())
        or version is None
    ):
        return _problem(
            request,
            400,
            "invalid_request",
            "Invalid request",
            "The integration request is invalid.",
        )
    try:
        connection = update_configuration(
            provider=provider,
            configuration=config,
            expected_version=version,
            actor=metadata,
            request=request,
        )
    except (IntegrationCryptoError, SecretUnavailable):
        return _problem(
            request,
            503,
            "integration_unavailable",
            "Integration unavailable",
            "The integration service is temporarily unavailable.",
        )
    except IntegrationServiceError as error:
        return _service_problem(request, error)
    return JsonResponse(_connection_payload(connection), headers={"Cache-Control": "no-store"})


def _versioned_action(
    action: Callable[..., object], request: HttpRequest, provider: str
) -> JsonResponse:
    _require_feature()
    metadata, denied = _administrator(request, fresh=action is disable_connection)
    if denied is not None:
        return denied
    version = _expected_version(_body(request))
    if version is None:
        return _problem(
            request,
            400,
            "invalid_request",
            "Invalid request",
            "The integration request is invalid.",
        )
    try:
        value = action(provider=provider, expected_version=version, actor=metadata, request=request)
    except IntegrationServiceError as error:
        return _service_problem(request, error)
    if isinstance(value, IntegrationHealthCheck):
        return JsonResponse(
            {
                "provider": provider,
                "outcome": value.outcome,
                "error_category": value.error_category or None,
                "duration_ms": value.duration_ms,
                "correlation_id": str(value.correlation_id),
                "tested_at": value.tested_at.isoformat(),
            },
            headers={"Cache-Control": "no-store"},
        )
    return JsonResponse(_connection_payload(value), headers={"Cache-Control": "no-store"})


@require_http_methods(["POST"])
def test(request: HttpRequest, provider: str) -> JsonResponse:
    return _versioned_action(test_connection, request, provider)


@require_http_methods(["POST"])
def disable(request: HttpRequest, provider: str) -> JsonResponse:
    return _versioned_action(disable_connection, request, provider)


@require_GET
def audit(request: HttpRequest, provider: str) -> JsonResponse:
    _require_feature()
    metadata, denied = _administrator(request)
    if denied is not None:
        return denied
    try:
        limit = int(request.GET.get("limit", "50"))
        page = integration_audit(
            provider=provider, owner=metadata.profile, cursor=request.GET.get("cursor"), limit=limit
        )
    except (IntegrationServiceError, TypeError, ValueError) as error:
        if not isinstance(error, IntegrationServiceError):
            error = IntegrationServiceError("invalid_pagination")
        return _service_problem(request, error)
    return JsonResponse(
        {
            "events": [
                {
                    "action": event.action.removeprefix("integration.")
                    .replace("connection_", "connection_")
                    .replace("credential_", "credential_")
                    .replace("configuration_", "configuration_"),
                    "outcome": event.outcome,
                    "actor_id": str(event.profile_id) if event.profile_id else None,
                    "version": event.details.get("version"),
                    "failure_category": event.details.get("failure_category"),
                    "correlation_id": event.details.get("correlation_id"),
                    "created_at": event.created_at.isoformat(),
                }
                for event in page.events
            ],
            "next_cursor": page.next_cursor,
        },
        headers={"Cache-Control": "no-store"},
    )
