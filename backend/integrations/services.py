"""Redacted integration administration operations."""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from identity.models import AdministratorProfile, AdministratorSecurityEvent, AdministratorSession
from identity.request_context import source_ip
from identity.services.security import record_security_event

from integrations.exceptions import SecretUnavailable
from integrations.models import (
    ConnectionState,
    HealthOutcome,
    IntegrationConnection,
    IntegrationHealthCheck,
)
from integrations.providers import (
    EventbriteProbe,
    GroqProbe,
    IterableProbe,
    OpenAIProbe,
    ProviderProbe,
)
from integrations.secret_store import PostgresSecretStore
from integrations.transport import BoundedHTTPSProbeTransport
from integrations.types import SecretReference

PROVIDERS = frozenset({"eventbrite", "groq", "iterable", "openai"})
CAPABILITIES_BY_PROVIDER: dict[str, list[str]] = {
    "eventbrite": ["connection_test", "draft_create", "metadata_read"],
    "iterable": ["connection_test", "draft_create", "metadata_read"],
    "openai": ["connection_test", "evaluation_judge", "inference"],
    "groq": ["connection_test", "evaluation_judge", "inference"],
}
DEFAULT_CONFIGURATION: dict[str, dict[str, str]] = {
    "eventbrite": {},
    "iterable": {"region": "us"},
    "openai": {"model": "openai/gpt-oss-20b"},
    "groq": {"model": "openai/gpt-oss-20b"},
}
SCOPE_BY_PROVIDER: dict[str, str] = {
    "eventbrite": "private_token",
    "iterable": "api_key",
    "openai": "project_key",
    "groq": "api_key",
}
CURSOR_SALT = "civicloop.integrations.audit.v1"


class IntegrationServiceError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AuditPage:
    events: tuple[AdministratorSecurityEvent, ...]
    next_cursor: str | None


def probe_for(provider: str) -> ProviderProbe:
    transport = BoundedHTTPSProbeTransport()
    probes = {
        "eventbrite": EventbriteProbe,
        "iterable": IterableProbe,
        "openai": OpenAIProbe,
        "groq": GroqProbe,
    }
    try:
        return probes[provider](transport)
    except KeyError:
        raise IntegrationServiceError("provider_not_found") from None


def list_connections() -> list[IntegrationConnection]:
    return cast(
        list[IntegrationConnection],
        list(IntegrationConnection.objects.select_related("secret").order_by("provider")),
    )


def replace_credential(
    *,
    provider: str,
    credential: bytes,
    expected_version: int,
    actor: AdministratorSession,
    request: HttpRequest,
) -> IntegrationConnection:
    _validate_provider(provider)
    if not credential or len(credential) > 16384:
        raise IntegrationServiceError("invalid_request")
    with transaction.atomic():
        connection = _locked_or_new(provider)
        _expect_version(connection, expected_version)
        store = PostgresSecretStore()
        if connection.secret_id is None or connection.state == ConnectionState.DISABLED:
            reference = store.put(
                provider=provider, scope=SCOPE_BY_PROVIDER[provider], value=credential
            )
            connection.secret_id = reference.id
            action = "integration.credential_replaced"
            from integrations.models import EncryptedSecret

            EncryptedSecret.objects.filter(pk=reference.id).update(created_by=actor.profile.user)
        else:
            secret = connection.secret
            reference = SecretReference(
                id=secret.id, provider=secret.provider, scope=secret.scope, version=secret.version
            )
            reference = store.replace(reference, value=credential)
            from integrations.models import EncryptedSecret

            EncryptedSecret.objects.filter(pk=reference.id).update(replaced_by=actor.profile.user)
            action = "integration.credential_replaced"
        connection.state = ConnectionState.CONFIGURED
        connection.capabilities = CAPABILITIES_BY_PROVIDER[provider]
        connection.version += 1
        connection.save()
        _audit(action, "success", actor, request, provider, connection.version, None)
    return connection


def update_configuration(
    *,
    provider: str,
    configuration: dict[str, str],
    expected_version: int,
    actor: AdministratorSession,
    request: HttpRequest,
) -> IntegrationConnection:
    _validate_provider(provider)
    with transaction.atomic():
        connection = _locked_or_new(provider)
        _expect_version(connection, expected_version)
        if configuration != DEFAULT_CONFIGURATION[provider] and not _valid_configuration(
            provider, configuration
        ):
            raise IntegrationServiceError("invalid_request")
        connection.configuration = configuration
        connection.version += 1
        connection.save()
        _audit(
            "integration.configuration_changed",
            "success",
            actor,
            request,
            provider,
            connection.version,
            None,
        )
    return connection


def disable_connection(
    *, provider: str, expected_version: int, actor: AdministratorSession, request: HttpRequest
) -> IntegrationConnection:
    _validate_provider(provider)
    with transaction.atomic():
        try:
            connection = cast(
                IntegrationConnection,
                (
                    IntegrationConnection.objects.select_for_update(of=("self",))
                    .select_related("secret")
                    .get(provider=provider)
                ),
            )
        except IntegrationConnection.DoesNotExist:
            raise IntegrationServiceError("provider_not_found") from None
        _expect_version(connection, expected_version)
        if connection.secret_id is not None:
            secret = connection.secret
            PostgresSecretStore().disable(
                SecretReference(
                    id=secret.id,
                    provider=secret.provider,
                    scope=secret.scope,
                    version=secret.version,
                )
            )
        connection.state = ConnectionState.DISABLED
        connection.version += 1
        connection.save()
        _audit(
            "integration.connection_disabled",
            "success",
            actor,
            request,
            provider,
            connection.version,
            None,
        )
    return connection


def test_connection(
    *, provider: str, expected_version: int, actor: AdministratorSession, request: HttpRequest
) -> IntegrationHealthCheck:
    _validate_provider(provider)
    connection = _connection_for_test(provider, expected_version)
    started = time.monotonic()
    secret = connection.secret
    if secret is None:
        raise SecretUnavailable()
    reference = SecretReference(
        id=secret.id, provider=secret.provider, scope=secret.scope, version=secret.version
    )
    with PostgresSecretStore().lease(
        reference,
        caller_id=actor.id,
        workflow_id=None,
        purpose="connection_test",
        ttl=timedelta(seconds=15),
    ) as lease:
        result = probe_for(provider).probe(lease, configuration=connection.configuration)
    duration_ms = min(int((time.monotonic() - started) * 1000), 30000)
    with transaction.atomic():
        connection = cast(
            IntegrationConnection,
            IntegrationConnection.objects.select_for_update().get(pk=connection.pk),
        )
        _expect_version(connection, expected_version)
        outcome = HealthOutcome.HEALTHY if result.ok else HealthOutcome.DEGRADED
        error_category = "" if result.ok else result.error_category or "network"
        health_check = cast(
            IntegrationHealthCheck,
            IntegrationHealthCheck.objects.create(
                connection=connection,
                outcome=outcome,
                error_category=error_category,
                duration_ms=duration_ms,
                correlation_id=uuid.uuid4(),
            ),
        )
        connection.state = ConnectionState.HEALTHY if result.ok else ConnectionState.DEGRADED
        connection.last_failure_category = error_category
        connection.last_successful_test_at = (
            timezone.now() if result.ok else connection.last_successful_test_at
        )
        connection.save(
            update_fields=[
                "state",
                "last_failure_category",
                "last_successful_test_at",
                "updated_at",
            ]
        )
        _audit(
            "integration.connection_tested",
            "success" if result.ok else "failure",
            actor,
            request,
            provider,
            connection.version,
            error_category or None,
            correlation_id=health_check.correlation_id,
        )
    return health_check


def integration_audit(
    *, provider: str, owner: AdministratorProfile, cursor: str | None, limit: int
) -> AuditPage:
    _validate_provider(provider)
    if not 1 <= limit <= 100:
        raise IntegrationServiceError("invalid_pagination")
    query = AdministratorSecurityEvent.objects.filter(
        profile=owner, target_type="integration_connection", target_id=provider
    )
    if cursor is not None:
        created_at, event_id = _decode_cursor(cursor)
        query = query.filter(
            Q(created_at__lt=created_at) | Q(created_at=created_at, id__lt=event_id)
        )
    rows = list(query.order_by("-created_at", "-id")[: limit + 1])
    events = tuple(rows[:limit])
    next_cursor = _encode_cursor(events[-1]) if len(rows) > limit and events else None
    return AuditPage(events=events, next_cursor=next_cursor)


def _connection_for_test(provider: str, expected_version: int) -> IntegrationConnection:
    try:
        connection = cast(
            IntegrationConnection,
            IntegrationConnection.objects.select_related("secret").get(provider=provider),
        )
    except IntegrationConnection.DoesNotExist:
        raise IntegrationServiceError("provider_not_found") from None
    _expect_version(connection, expected_version)
    if connection.state == ConnectionState.DISABLED or connection.secret_id is None:
        raise IntegrationServiceError("integration_unavailable")
    return connection


def _locked_or_new(provider: str) -> IntegrationConnection:
    connection = cast(
        IntegrationConnection | None,
        IntegrationConnection.objects.select_for_update(of=("self",))
        .select_related("secret")
        .filter(provider=provider)
        .first(),
    )
    if connection is not None:
        return connection
    try:
        with transaction.atomic():
            return cast(
                IntegrationConnection,
                IntegrationConnection.objects.create(
                    provider=provider,
                    configuration=DEFAULT_CONFIGURATION[provider],
                    capabilities=[],
                ),
            )
    except IntegrityError:
        connection = cast(
            IntegrationConnection | None,
            IntegrationConnection.objects.select_for_update(of=("self",))
            .select_related("secret")
            .filter(provider=provider)
            .first(),
        )
        if connection is not None:
            return connection
        raise


def _expect_version(connection: IntegrationConnection, expected_version: int) -> None:
    if (
        not isinstance(expected_version, int)
        or isinstance(expected_version, bool)
        or expected_version < 1
    ):
        raise IntegrationServiceError("invalid_request")
    if connection.version != expected_version:
        raise IntegrationServiceError("version_conflict")


def _valid_configuration(provider: str, configuration: dict[str, str]) -> bool:
    if not isinstance(configuration, dict):
        return False
    if provider == "eventbrite":
        return configuration == {}
    if provider == "iterable":
        return set(configuration) == {"region"} and configuration.get("region") in {"us", "eu"}
    return configuration == {"model": "openai/gpt-oss-20b"}


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise IntegrationServiceError("provider_not_found")


def _audit(
    action: str,
    outcome: str,
    actor: AdministratorSession,
    request: HttpRequest,
    provider: str,
    version: int,
    failure_category: str | None,
    correlation_id: uuid.UUID | None = None,
) -> None:
    record_security_event(
        action=action,
        outcome=outcome,
        owner=actor.profile,
        source_ip=source_ip(request),
        session_id=actor.id,
        target_type="integration_connection",
        target_id=provider,
        details={
            "version": version,
            "failure_category": failure_category,
            "correlation_id": str(correlation_id or uuid.uuid4()),
        },
    )


def _encode_cursor(event: AdministratorSecurityEvent) -> str:
    return cast(
        str,
        signing.dumps(
            [event.created_at.isoformat(), str(event.id)], salt=CURSOR_SALT, compress=True
        ),
    )


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        values = signing.loads(cursor, salt=CURSOR_SALT)
        created_at = (
            parse_datetime(values[0]) if isinstance(values, list) and len(values) == 2 else None
        )
        event_id = UUID(values[1]) if isinstance(values, list) and len(values) == 2 else None
        if created_at is None or created_at.tzinfo is None or event_id is None:
            raise ValueError
        return created_at, event_id
    except (signing.BadSignature, TypeError, ValueError):
        raise IntegrationServiceError("invalid_pagination") from None
