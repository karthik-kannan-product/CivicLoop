import json
import uuid
from collections.abc import Callable

from django.conf import settings
from django.http import Http404, HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods
from identity.models import AdministratorProfile, AdministratorSession
from identity.request_context import source_ip
from identity.services.security import record_security_event
from identity.services.sessions import administrator_session_is_fresh

from integrations.exceptions import IntegrationCryptoError, SecretUnavailable
from integrations.models import IntegrationConnection, IntegrationHealthCheck, Provider
from integrations.rate_limits import (
    IntegrationAction,
    IntegrationRateLimited,
    IntegrationRateLimitUnavailable,
    check_integration_limit,
)
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
SAFE_PROVIDERS = frozenset(Provider.values)
AUDIT_ACTION_BY_VIEW_NAME = {
    "admin-integration-list": "integration.connections_listed",
    "admin-integration-credential": "integration.credential_replaced",
    "admin-integration-configuration": "integration.configuration_changed",
    "admin-integration-test": "integration.connection_tested",
    "admin-integration-disable": "integration.connection_disabled",
    "admin-integration-audit": "integration.audit_listed",
}


def _require_feature() -> None:
    if not (
        settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED
        and settings.CIVICLOOP_INTEGRATIONS_ENABLED
    ):
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


def _audit_event(
    request: HttpRequest,
    *,
    action: str,
    outcome: str,
    provider: str | None,
    failure_category: str,
    version: int | None = None,
) -> None:
    metadata = getattr(request, "administrator_session", None)
    profile = metadata.profile if isinstance(metadata, AdministratorSession) else None
    if profile is None:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            profile = AdministratorProfile.objects.filter(user_id=user.pk).first()
    safe_provider = provider if provider in SAFE_PROVIDERS else None
    record_security_event(
        action=action,
        outcome=outcome,
        owner=profile,
        source_ip=source_ip(request),
        session_id=metadata.id if isinstance(metadata, AdministratorSession) else None,
        target_type="integration_connection" if safe_provider is not None else "integration_admin",
        target_id=safe_provider or "",
        details={
            "version": version,
            "failure_category": failure_category,
            "correlation_id": str(uuid.uuid4()),
        },
    )


def _audit_unavailable(request: HttpRequest) -> JsonResponse:
    return _problem(
        request,
        503,
        "integration_unavailable",
        "Integration unavailable",
        "The integration service is temporarily unavailable.",
    )


def recovery_restricted_response(request: HttpRequest) -> JsonResponse:
    _require_feature()
    resolver_match = request.resolver_match
    view_name = resolver_match.view_name if resolver_match is not None else ""
    action = AUDIT_ACTION_BY_VIEW_NAME.get(view_name, "integration.access_denied")
    provider_value = resolver_match.kwargs.get("provider") if resolver_match is not None else None
    provider = provider_value if isinstance(provider_value, str) else None
    try:
        _audit_event(
            request,
            action=action,
            outcome="denied",
            provider=provider,
            failure_category="recovery_restricted",
        )
    except Exception:
        return _audit_unavailable(request)
    return _problem(
        request,
        403,
        "recovery_restricted",
        "Recovery required",
        "Complete administrator account recovery to continue.",
    )


def _enforce_rate_limit(
    request: HttpRequest,
    *,
    metadata: AdministratorSession,
    provider: str,
    limit_action: IntegrationAction,
    audit_action: str,
) -> JsonResponse | None:
    try:
        check_integration_limit(
            action=limit_action,
            owner_id=metadata.profile_id,
            provider=provider,
            source_ip=source_ip(request),
        )
    except IntegrationRateLimited as error:
        if error.newly_limited:
            try:
                _audit_event(
                    request,
                    action=audit_action,
                    outcome="denied",
                    provider=provider,
                    failure_category="rate_limit",
                )
            except Exception:
                return _audit_unavailable(request)
        response = _problem(
            request,
            429,
            "rate_limited",
            "Too many attempts",
            "Too many integration administration attempts. Try again later.",
        )
        response["Retry-After"] = str(error.retry_after_seconds)
        return response
    except IntegrationRateLimitUnavailable:
        try:
            _audit_event(
                request,
                action=audit_action,
                outcome="unavailable",
                provider=provider,
                failure_category="rate_limit_unavailable",
            )
        except Exception:
            pass
        return _audit_unavailable(request)
    return None


def _administrator(
    request: HttpRequest,
    *,
    action: str,
    provider: str | None = None,
    fresh: bool = False,
) -> tuple[AdministratorSession | None, JsonResponse | None]:
    metadata = getattr(request, "administrator_session", None)
    if not isinstance(metadata, AdministratorSession) or metadata.recovery_restricted:
        try:
            _audit_event(
                request,
                action=action,
                outcome="denied",
                provider=provider,
                failure_category=(
                    "recovery_restricted"
                    if isinstance(metadata, AdministratorSession) and metadata.recovery_restricted
                    else "authentication"
                ),
            )
        except Exception:
            return None, _audit_unavailable(request)
        return None, _problem(
            request,
            401,
            "authentication_required",
            "Authentication required",
            "Administrator authentication is required.",
        )
    if fresh and not administrator_session_is_fresh(metadata):
        try:
            _audit_event(
                request,
                action=action,
                outcome="denied",
                provider=provider,
                failure_category="freshness",
            )
        except Exception:
            return None, _audit_unavailable(request)
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


def _service_failure_problem(
    request: HttpRequest,
    *,
    error: IntegrationServiceError,
    action: str,
    provider: str,
    version: int | None,
) -> JsonResponse:
    failure_category_by_code = {
        "provider_not_found": "provider_not_found",
        "version_conflict": "version_conflict",
        "invalid_request": "invalid_request",
        "integration_unavailable": "integration_unavailable",
    }
    failure_category = failure_category_by_code.get(error.code)
    if failure_category is not None:
        try:
            _audit_event(
                request,
                action=action,
                outcome="denied",
                provider=provider,
                failure_category=failure_category,
                version=version,
            )
        except Exception:
            return _audit_unavailable(request)
    return _service_problem(request, error)


def _key_unavailable_problem(
    request: HttpRequest,
    *,
    action: str,
    provider: str,
    version: int | None,
) -> JsonResponse:
    try:
        _audit_event(
            request,
            action=action,
            outcome="unavailable",
            provider=provider,
            failure_category="key_unavailable",
            version=version,
        )
    except Exception:
        pass
    return _audit_unavailable(request)


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
    _metadata, denied = _administrator(request, action="integration.connections_listed")
    if denied is not None:
        return denied
    return JsonResponse(
        {"connections": [_connection_payload(row) for row in list_connections()]},
        headers={"Cache-Control": "no-store"},
    )


@require_http_methods(["PUT"])
def credential(request: HttpRequest, provider: str) -> JsonResponse:
    _require_feature()
    metadata, denied = _administrator(
        request,
        action="integration.credential_replaced",
        provider=provider,
        fresh=True,
    )
    if denied is not None:
        return denied
    assert metadata is not None
    limited = _enforce_rate_limit(
        request,
        metadata=metadata,
        provider=provider,
        limit_action="credential",
        audit_action="integration.credential_replaced",
    )
    if limited is not None:
        return limited
    body = _body(request)
    value = body.get("credential") if body is not None else None
    version = _expected_version(body)
    encoded_value = value.encode("utf-8") if isinstance(value, str) else None
    if encoded_value is None or not 1 <= len(encoded_value) <= 16384 or version is None:
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
            credential=encoded_value,
            expected_version=version,
            actor=metadata,
            request=request,
        )
    except (IntegrationCryptoError, SecretUnavailable):
        return _key_unavailable_problem(
            request,
            action="integration.credential_replaced",
            provider=provider,
            version=version,
        )
    except IntegrationServiceError as error:
        return _service_failure_problem(
            request,
            error=error,
            action="integration.credential_replaced",
            provider=provider,
            version=version,
        )
    except Exception:
        return _audit_unavailable(request)
    return JsonResponse(_connection_payload(connection), headers={"Cache-Control": "no-store"})


@require_http_methods(["PATCH"])
def configuration(request: HttpRequest, provider: str) -> JsonResponse:
    _require_feature()
    metadata, denied = _administrator(
        request,
        action="integration.configuration_changed",
        provider=provider,
        fresh=True,
    )
    if denied is not None:
        return denied
    assert metadata is not None
    limited = _enforce_rate_limit(
        request,
        metadata=metadata,
        provider=provider,
        limit_action="configuration",
        audit_action="integration.configuration_changed",
    )
    if limited is not None:
        return limited
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
        return _key_unavailable_problem(
            request,
            action="integration.configuration_changed",
            provider=provider,
            version=version,
        )
    except IntegrationServiceError as error:
        return _service_failure_problem(
            request,
            error=error,
            action="integration.configuration_changed",
            provider=provider,
            version=version,
        )
    except Exception:
        return _audit_unavailable(request)
    return JsonResponse(_connection_payload(connection), headers={"Cache-Control": "no-store"})


def _versioned_action(
    action: Callable[..., IntegrationConnection | IntegrationHealthCheck],
    request: HttpRequest,
    provider: str,
) -> JsonResponse:
    _require_feature()
    audit_action = (
        "integration.connection_disabled"
        if action is disable_connection
        else "integration.connection_tested"
    )
    metadata, denied = _administrator(
        request,
        action=audit_action,
        provider=provider,
        fresh=action is disable_connection,
    )
    if denied is not None:
        return denied
    assert metadata is not None
    limit_action: IntegrationAction = "disable" if action is disable_connection else "test"
    limited = _enforce_rate_limit(
        request,
        metadata=metadata,
        provider=provider,
        limit_action=limit_action,
        audit_action=audit_action,
    )
    if limited is not None:
        return limited
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
    except (IntegrationCryptoError, SecretUnavailable):
        return _key_unavailable_problem(
            request,
            action=audit_action,
            provider=provider,
            version=version,
        )
    except IntegrationServiceError as error:
        return _service_failure_problem(
            request,
            error=error,
            action=audit_action,
            provider=provider,
            version=version,
        )
    except Exception:
        return _audit_unavailable(request)
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
    metadata, denied = _administrator(
        request,
        action="integration.audit_listed",
        provider=provider,
    )
    if denied is not None:
        return denied
    assert metadata is not None
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
