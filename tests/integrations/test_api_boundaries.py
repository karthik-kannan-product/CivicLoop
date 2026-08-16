import base64
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone
from identity.models import AdministratorProfile, AdministratorSecurityEvent
from integrations.exceptions import IntegrationCryptoError
from integrations.models import IntegrationConnection, IntegrationHealthCheck
from jsonschema import Draft202012Validator, FormatChecker

from tests.identity.test_security_actions_api import create_authenticated_owner

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "integration-api-boundary-tests",
    }
}


def json_request(
    client: Client,
    method: str,
    path: str,
    body: dict[str, object],
    **headers: str,
):
    return getattr(client, method)(
        path,
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


@pytest.fixture
def integration_configuration(tmp_path: Path):
    key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    key_file = tmp_path / "integration-keyring.json"
    key_file.write_text(json.dumps({"active_key_id": "test", "keys": {"test": key}}))
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
        CIVICLOOP_INTEGRATION_KEY_FILE=key_file,
        CACHES=LOCMEM_CACHE,
    ):
        cache.clear()
        yield
        cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("identity_enabled", "integrations_enabled"),
    [(False, True), (True, False), (False, False)],
)
def test_every_integration_api_is_hidden_unless_both_features_are_enabled(
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
def test_recovery_session_still_gets_404_when_integration_feature_is_disabled(
    integration_configuration,
) -> None:
    client, _profile, metadata, _password = create_authenticated_owner()
    metadata.recovery_restricted = True
    metadata.save(update_fields=["recovery_restricted"])

    with override_settings(CIVICLOOP_INTEGRATIONS_ENABLED=False):
        response = client.get("/api/v1/admin/integrations")

    assert response.status_code == 404


@pytest.mark.django_db
def test_recovery_denial_is_audited_without_broadening_access(
    integration_configuration,
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


@pytest.mark.django_db
def test_integration_mutations_enforce_django_csrf(integration_configuration) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    client.handler.enforce_csrf_checks = True
    path = "/api/v1/admin/integrations/eventbrite/credential"
    body = {"credential": "synthetic-token", "expected_version": 1}

    denied = json_request(client, "put", path, body)
    assert denied.status_code == 403
    assert not IntegrationConnection.objects.exists()

    csrf_token = "a" * 32
    client.cookies["csrftoken"] = csrf_token
    allowed = json_request(client, "put", path, body, HTTP_X_CSRFTOKEN=csrf_token)
    assert allowed.status_code == 200


@pytest.mark.django_db
def test_demo_and_password_only_sessions_cannot_access_integrations(
    integration_configuration,
) -> None:
    demo = Client()
    demo_login = json_request(
        demo,
        "post",
        "/api/v1/auth/login",
        {"username": "maya.operator", "password": "civicloop-demo"},
    )
    assert demo_login.status_code == 200
    assert demo.get("/api/v1/admin/integrations").status_code == 401

    password = "Synthetic-Password-Only-Passphrase-934!"
    owner = AdministratorProfile.objects.create(
        user=User.objects.create_user(username="synthetic.password.only", password=password),
        status=AdministratorProfile.Status.ENROLLMENT_REQUIRED,
    )
    password_only = Client()
    challenge = json_request(
        password_only,
        "post",
        "/api/v1/admin/auth/password",
        {"username": owner.user.username, "password": password},
    )
    assert challenge.status_code == 200
    assert challenge.json()["stage"] == "password_verified"
    assert password_only.get("/api/v1/admin/integrations").status_code == 401


@pytest.mark.django_db
def test_stale_verification_denial_is_safely_audited(integration_configuration) -> None:
    client, profile, metadata, _password = create_authenticated_owner()
    metadata.fresh_verified_at = timezone.now() - timedelta(minutes=11)
    metadata.save(update_fields=["fresh_verified_at"])

    response = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "must-not-leak", "expected_version": 1},
    )

    assert response.status_code == 403
    event = AdministratorSecurityEvent.objects.get(
        profile=profile,
        action="integration.credential_replaced",
        outcome="denied",
    )
    assert event.details["failure_category"] == "freshness"
    assert "must-not-leak" not in json.dumps(event.details)


@pytest.mark.django_db
def test_unknown_provider_audit_does_not_persist_raw_path_data(
    integration_configuration,
) -> None:
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
    )
    assert event.target_id == ""
    assert event.details["failure_category"] == "provider_not_found"
    assert unsafe_provider not in json.dumps(event.details)


@pytest.mark.django_db
def test_live_rate_limit_has_stable_retry_after_and_one_bounded_audit_transition(
    integration_configuration,
) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    path = "/api/v1/admin/integrations/eventbrite/credential"
    for _ in range(5):
        assert json_request(client, "put", path, {}).status_code == 400

    first = json_request(client, "put", path, {})
    repeated = json_request(client, "put", path, {"credential": "must-not-leak"})

    assert first.status_code == 429
    assert repeated.status_code == 429
    assert first["Retry-After"] == repeated["Retry-After"] == "300"
    assert first["Content-Type"].startswith("application/problem+json")
    assert first["Cache-Control"] == "no-store"
    assert first.json()["code"] == "rate_limited"
    audit = client.get("/api/v1/admin/integrations/eventbrite/audit")
    rate_events = [
        event
        for event in audit.json()["events"]
        if event["failure_category"] == "rate_limit"
    ]
    assert len(rate_events) == 1


@pytest.mark.django_db
def test_configuration_updates_are_versioned_validated_and_audited(
    integration_configuration,
) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    created = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/iterable/credential",
        {"credential": "synthetic-iterable-token", "expected_version": 1},
    )
    updated = json_request(
        client,
        "patch",
        "/api/v1/admin/integrations/iterable/configuration",
        {"configuration": {"region": "eu"}, "expected_version": created.json()["version"]},
    )
    invalid = json_request(
        client,
        "patch",
        "/api/v1/admin/integrations/iterable/configuration",
        {"configuration": {"region": "apac"}, "expected_version": updated.json()["version"]},
    )

    assert updated.status_code == 200
    assert updated.json()["configuration"] == {"region": "eu"}
    assert updated.json()["version"] == 3
    assert invalid.status_code == 400
    assert IntegrationConnection.objects.get(provider="iterable").version == 3
    audit = client.get("/api/v1/admin/integrations/iterable/audit")
    assert {
        (event["action"], event["outcome"], event["failure_category"])
        for event in audit.json()["events"]
    } >= {
        ("configuration_changed", "success", None),
        ("configuration_changed", "denied", "invalid_request"),
    }


@pytest.mark.django_db
def test_disable_then_credential_replacement_reenables_connection(
    integration_configuration,
) -> None:
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

    assert disabled.status_code == 200
    assert disabled.json()["state"] == "disabled"
    assert disabled.json()["version"] == 3
    assert replacement.status_code == 200
    assert replacement.json()["state"] == "configured"
    assert replacement.json()["version"] == 4


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("put", "credential", {"credential": "must-not-leak", "expected_version": 1}),
        ("patch", "configuration", {"configuration": {}, "expected_version": 1}),
    ],
)
def test_consequential_mutation_audit_outage_fails_closed_and_rolls_back(
    integration_configuration,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    suffix: str,
    body: dict[str, object],
) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()

    def unavailable_audit(**kwargs):
        raise RuntimeError("synthetic database detail")

    monkeypatch.setattr("integrations.services.record_security_event", unavailable_audit)
    response = json_request(
        client,
        method,
        f"/api/v1/admin/integrations/eventbrite/{suffix}",
        body,
    )

    assert response.status_code == 503
    assert response.json()["code"] == "integration_unavailable"
    assert "database" not in response.content.decode().lower()
    assert not IntegrationConnection.objects.exists()


@pytest.mark.django_db
def test_disable_audit_outage_preserves_enabled_connection(
    integration_configuration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    created = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-eventbrite-token", "expected_version": 1},
    )

    def unavailable_audit(**kwargs):
        raise RuntimeError("synthetic database detail")

    monkeypatch.setattr("integrations.services.record_security_event", unavailable_audit)
    response = json_request(
        client,
        "post",
        "/api/v1/admin/integrations/eventbrite/disable",
        {"expected_version": created.json()["version"]},
    )

    assert response.status_code == 503
    connection = IntegrationConnection.objects.get(provider="eventbrite")
    assert connection.state == "configured"
    assert connection.version == 2


@pytest.mark.django_db
def test_audit_cursor_paginates_live_redacted_events(integration_configuration) -> None:
    client, _profile, _metadata, _password = create_authenticated_owner()
    credential = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/iterable/credential",
        {"credential": "synthetic-iterable-token", "expected_version": 1},
    )
    json_request(
        client,
        "patch",
        "/api/v1/admin/integrations/iterable/configuration",
        {"configuration": {"region": "eu"}, "expected_version": credential.json()["version"]},
    )

    first = client.get("/api/v1/admin/integrations/iterable/audit?limit=1")
    second = client.get(
        "/api/v1/admin/integrations/iterable/audit",
        {"limit": 1, "cursor": first.json()["next_cursor"]},
    )

    assert first.status_code == 200
    assert len(first.json()["events"]) == 1
    assert first.json()["next_cursor"] is not None
    assert second.status_code == 200
    assert len(second.json()["events"]) == 1
    assert second.json()["events"][0] != first.json()["events"][0]
    schema = json.loads(
        Path("schemas/integrations/health-check.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(
        {**schema, "$ref": "#/$defs/IntegrationAuditPage"},
        format_checker=FormatChecker(),
    ).validate(first.json())


@pytest.mark.django_db
def test_local_decrypt_outage_returns_safe_503_instead_of_degraded_health(
    integration_configuration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, profile, _metadata, _password = create_authenticated_owner()
    created = json_request(
        client,
        "put",
        "/api/v1/admin/integrations/eventbrite/credential",
        {"credential": "synthetic-eventbrite-token", "expected_version": 1},
    )

    def unavailable_decrypt(*args, **kwargs):
        raise IntegrationCryptoError()

    monkeypatch.setattr("integrations.secret_store.decrypt_secret", unavailable_decrypt)
    response = json_request(
        client,
        "post",
        "/api/v1/admin/integrations/eventbrite/test",
        {"expected_version": created.json()["version"]},
    )

    assert response.status_code == 503
    assert response.json()["code"] == "integration_unavailable"
    assert "decrypt" not in response.content.decode().lower()
    assert not IntegrationHealthCheck.objects.exists()
    connection = IntegrationConnection.objects.get(provider="eventbrite")
    assert connection.state == "configured"
    assert AdministratorSecurityEvent.objects.filter(
        profile=profile,
        action="integration.connection_tested",
        outcome="unavailable",
        details__failure_category="key_unavailable",
    ).exists()
