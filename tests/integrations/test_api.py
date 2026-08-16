import base64
import json
import os
from pathlib import Path

import pytest
from django.test import Client, override_settings
from identity.models import AdministratorSecurityEvent
from integrations.models import IntegrationConnection, IntegrationHealthCheck
from jsonschema import Draft202012Validator, FormatChecker

from tests.identity.test_security_actions_api import create_authenticated_owner


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
    ):
        yield


@pytest.mark.django_db
def test_list_requires_full_administrator_and_is_no_store(integration_settings) -> None:
    response = Client().get("/api/v1/admin/integrations")

    assert response.status_code == 401
    assert response["Cache-Control"] == "no-store"
    assert response["Content-Type"].startswith("application/problem+json")


@pytest.mark.django_db
def test_credential_replacement_requires_fresh_verification(integration_settings) -> None:
    client, _profile, metadata, _password = create_authenticated_owner()
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

    class HealthyProbe:
        def probe(self, credential: bytes, *, configuration: dict[str, str]):
            assert credential == b"synthetic-eventbrite-token"
            assert configuration == {}
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
    assert "synthetic-eventbrite-token" not in json.dumps(
        list(IntegrationHealthCheck.objects.values()), default=str
    )
    health_schema = json.loads(
        Path("schemas/integrations/health-check.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(health_schema, format_checker=FormatChecker()).validate(response.json())


@pytest.mark.django_db
def test_recovery_restricted_administrator_cannot_access_integration_routes(
    integration_settings,
) -> None:
    client, _profile, metadata, _password = create_authenticated_owner()
    metadata.recovery_restricted = True
    metadata.save(update_fields=["recovery_restricted"])

    response = client.get("/api/v1/admin/integrations")

    assert response.status_code == 403
    assert response.json()["code"] == "recovery_restricted"
