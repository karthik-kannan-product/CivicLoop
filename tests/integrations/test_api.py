import base64
import json
import os
from pathlib import Path

import pytest
from django.test import Client, override_settings
from identity.models import AdministratorSecurityEvent
from integrations.exceptions import SecretUnavailable
from integrations.models import IntegrationConnection, IntegrationHealthCheck
from integrations.types import SecretLease
from jsonschema import Draft202012Validator, FormatChecker

from tests.identity.test_security_actions_api import create_authenticated_owner

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "integration-api-tests",
    }
}


def json_request(client: Client, method: str, path: str, body: dict[str, object]):
    return getattr(client, method)(path, data=json.dumps(body), content_type="application/json")


@pytest.fixture
def integration_settings(tmp_path: Path):
    key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    key_file = tmp_path / "integration-keyring.json"
    key_file.write_text(json.dumps({"active_key_id": "test", "keys": {"test": key}}))
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
        CIVICLOOP_INTEGRATION_KEY_FILE=key_file,
        CACHES=LOCMEM_CACHE,
    ):
        yield


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("identity_enabled", "integrations_enabled"),
    [(False, True), (True, False), (False, False)],
)
def test_integration_apis_are_hidden_unless_both_features_are_enabled(
    identity_enabled: bool,
    integrations_enabled: bool,
) -> None:
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=identity_enabled,
        CIVICLOOP_INTEGRATIONS_ENABLED=integrations_enabled,
    ):
        client = Client()
        responses = [
            client.get("/api/v1/admin/integrations"),
            json_request(
                client,
                "put",
                "/api/v1/admin/integrations/eventbrite/credential",
                {"credential": "synthetic-token", "expected_version": 1},
            ),
            json_request(
                client,
                "patch",
                "/api/v1/admin/integrations/eventbrite/configuration",
                {"configuration": {}, "expected_version": 1},
            ),
            json_request(
                client,
                "post",
                "/api/v1/admin/integrations/eventbrite/test",
                {"expected_version": 1},
            ),
            json_request(
                client,
                "post",
                "/api/v1/admin/integrations/eventbrite/disable",
                {"expected_version": 1},
            ),
            client.get("/api/v1/admin/integrations/eventbrite/audit"),
        ]

    assert {response.status_code for response in responses} == {404}


@pytest.mark.django_db
def test_list_requires_full_administrator_and_is_no_store(integration_settings) -> None:
    response = Client().get("/api/v1/admin/integrations")

    assert response.status_code == 401
    assert response["Cache-Control"] == "no-store"
    assert response["Content-Type"].startswith("application/problem+json")


@pytest.mark.django_db
def test_denied_credential_authentication_is_safely_audited(integration_settings) -> None:
    credential_value = "synthetic-denied-secret"

    response = json_request(
        Client(),
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": credential_value, "expected_version": 1},
    )

    assert response.status_code == 401
    event = AdministratorSecurityEvent.objects.get(
        action="integration.credential_replaced",
        outcome="denied",
    )
    assert event.profile_id is None
    assert event.target_type == "integration_connection"
    assert event.target_id == "eventbrite"
    assert event.details["failure_category"] == "authentication"
    assert credential_value not in json.dumps(event.details)


@pytest.mark.django_db
def test_credential_replacement_requires_fresh_verification(integration_settings) -> None:
    client, profile, metadata, _password = create_authenticated_owner()
    metadata.fresh_verified_at = None
    metadata.save(update_fields=["fresh_verified_at"])

    response = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-eventbrite-token", "expected_version": 1},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "fresh_verification_required"
    assert not IntegrationConnection.objects.exists()
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="integration.credential_replaced",
        outcome="denied",
        details__failure_category="freshness",
    ).exists()


@pytest.mark.django_db
def test_credential_replacement_limits_utf8_encoded_bytes(integration_settings) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()

    response = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "é" * 8193, "expected_version": 1},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert not IntegrationConnection.objects.exists()


@pytest.mark.django_db
def test_credential_rate_limit_returns_stable_safe_problem_and_audits(
    integration_settings,
) -> None:
    client, profile, _metadata, _password = create_authenticated_owner()
    path = "/api/v1/admin/integrations/eventbrite/credential"
    for _ in range(5):
        assert json_request(client, "put", path, {}).status_code == 400

    first = json_request(client, "put", path, {})
    repeated = json_request(client, "put", path, {"credential": "must-not-leak"})

    assert first.status_code == 429
    assert first["Retry-After"] == "300"
    assert repeated.status_code == 429
    assert repeated["Retry-After"] == "300"
    assert first["Content-Type"].startswith("application/problem+json")
    assert first["Cache-Control"] == "no-store"
    assert first.json()["code"] == "rate_limited"
    event = AdministratorSecurityEvent.objects.get(
        profile=profile,
        action="integration.credential_replaced",
        outcome="denied",
        details__failure_category="rate_limit",
    )
    assert "must-not-leak" not in json.dumps(event.details)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "suffix", "body", "action"),
    [
        ("put", "credential", {}, "integration.credential_replaced"),
        ("patch", "configuration", {}, "integration.configuration_changed"),
        ("post", "test", {}, "integration.connection_tested"),
        ("post", "disable", {}, "integration.connection_disabled"),
    ],
)
def test_mutation_rate_limit_cache_outage_fails_closed(
    integration_settings,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    suffix: str,
    body: dict[str, object],
    action: str,
) -> None:
    client, profile, _metadata, _password = create_authenticated_owner()

    def unavailable(*args, **kwargs):
        raise ConnectionError("synthetic valkey detail")

    monkeypatch.setattr("integrations.rate_limits.cache.get", unavailable)
    response = json_request(
        client,
        method,
        f"/api/v1/admin/integrations/eventbrite/{suffix}",
        body,
    )

    assert response.status_code == 503
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == "integration_unavailable"
    assert "valkey" not in response.content.decode().lower()
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action=action,
        outcome="unavailable",
        details__failure_category="rate_limit_unavailable",
    ).exists()


@pytest.mark.django_db
def test_credential_replacement_is_write_only_versioned_and_audited(integration_settings) -> None:
    client, profile, _metadata, _password = create_authenticated_owner()

    response = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-eventbrite-token", "expected_version": 1},
    )

    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    payload = response.json()
    assert payload["provider"] == "eventbrite"
    assert payload["state"] == "configured"
    assert payload["version"] == 2
    assert payload["responsible_actor_id"] == str(profile.id)
    assert "synthetic-eventbrite-token" not in response.content.decode()
    schema = json.loads(
        Path("schemas/integrations/connection.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(
        {**schema, "$ref": "#/$defs/IntegrationConnection"}, format_checker=FormatChecker()
    ).validate(payload)
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="integration.credential_replaced",
        outcome="success",
        target_id="eventbrite",
    ).exists()

    conflict = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-replacement", "expected_version": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "version_conflict"
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="integration.credential_replaced",
        outcome="denied",
        target_id="eventbrite",
        details__failure_category="version_conflict",
        details__version=1,
    ).exists()


@pytest.mark.django_db
def test_unknown_provider_denial_never_persists_the_raw_path_value(integration_settings) -> None:
    client, profile, _metadata, _password = create_authenticated_owner()
    unsafe_provider = "synthetic-secret-provider"

    response = json_request(
        client,
        "put",
        f"/api/v1/admin/integrations/{unsafe_provider}/credential",
        {"credential": "must-not-leak", "expected_version": 1},
    )

    assert response.status_code == 404
    event = AdministratorSecurityEvent.objects.get(
        profile=profile,
        action="integration.credential_replaced",
        outcome="denied",
        details__failure_category="provider_not_found",
    )
    assert event.target_id == ""
    assert unsafe_provider not in json.dumps(event.details)


@pytest.mark.django_db
def test_credential_key_outage_returns_safe_503_and_is_audited(integration_settings) -> None:
    client, profile, _metadata, _password = create_authenticated_owner()

    with override_settings(CIVICLOOP_INTEGRATION_KEY_FILE=Path("missing-keyring.json")):
        response = json_request(
            client,
            "put",
            "/api/v1/admin/integrations/eventbrite/credential",
            {"credential": "must-not-leak", "expected_version": 1},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "integration_unavailable"
    assert "missing-keyring" not in response.content.decode()
    assert not IntegrationConnection.objects.exists()
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="integration.credential_replaced",
        outcome="unavailable",
        target_id="eventbrite",
        details__failure_category="key_unavailable",
    ).exists()


@pytest.mark.django_db
def test_credential_audit_outage_fails_closed_and_rolls_back(
    integration_settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()

    def unavailable_audit(**kwargs):
        raise RuntimeError("synthetic database detail")

    monkeypatch.setattr("integrations.services.record_security_event", unavailable_audit)
    response = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "must-not-leak", "expected_version": 1},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "integration_unavailable"
    assert "database" not in response.content.decode().lower()
    assert not IntegrationConnection.objects.exists()


@pytest.mark.django_db
def test_connection_test_records_only_sanitized_health_metadata(
    integration_settings, monkeypatch
) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-eventbrite-token", "expected_version": 1},
    )

    retained_leases: list[SecretLease] = []

    class HealthyProbe:
        def probe(self, credential: SecretLease, *, configuration: dict[str, str]):
            assert not hasattr(credential, "read")
            assert configuration == {}
            retained_leases.append(credential)
            from integrations.providers import ProbeResult

            return ProbeResult(ok=True)

    monkeypatch.setattr("integrations.services.probe_for", lambda provider: HealthyProbe())
    response = json_request(
        client,
        "post",
        "/api/v1/admin/integrations/eventbrite/test",
        {"expected_version": 2},
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "healthy"
    health_check = IntegrationHealthCheck.objects.get()
    assert health_check.error_category == ""
    assert len(retained_leases) == 1
    with pytest.raises(SecretUnavailable):
        retained_leases[0].use(lambda _credential: None)
    assert "synthetic-eventbrite-token" not in json.dumps(
        list(IntegrationHealthCheck.objects.values()), default=str
    )
    health_schema = json.loads(
        Path("schemas/integrations/health-check.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(health_schema, format_checker=FormatChecker()).validate(response.json())


@pytest.mark.django_db
def test_credential_replacement_reactivates_a_disabled_connection(integration_settings) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    first = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-first-token", "expected_version": 1},
    )
    disabled = json_request(
        client,
        "post",
        "/api/v1/admin/integrations/eventbrite/disable",
        {"expected_version": first.json()["version"]},
    )
    replacement = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {
            "credential": "synthetic-reactivated-token",
            "expected_version": disabled.json()["version"],
        },
    )

    assert disabled.json()["state"] == "disabled"
    assert replacement.status_code == 200
    assert replacement.json()["state"] == "configured"


@pytest.mark.django_db
def test_recovery_restricted_administrator_cannot_access_integration_routes(
    integration_settings,
) -> None:
    client, profile, metadata, _password = create_authenticated_owner()
    metadata.recovery_restricted = True
    metadata.save(update_fields=["recovery_restricted"])

    response = client.get("/api/v1/admin/integrations")

    assert response.status_code == 403
    assert response.json()["code"] == "recovery_restricted"
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="integration.connections_listed",
        outcome="denied",
        details__failure_category="recovery_restricted",
    ).exists()
