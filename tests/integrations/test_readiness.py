import base64
import json
import os
from pathlib import Path

from django.test import Client, override_settings


def test_integration_readiness_fails_closed_for_a_missing_key_without_affecting_liveness() -> None:
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
        CIVICLOOP_INTEGRATION_KEY_FILE=Path("missing-integration-keyring.json"),
    ):
        integration_ready = Client().get("/api/v1/admin/integrations/status")
        live = Client().get("/api/v1/health/live")

    assert integration_ready.status_code == 503
    assert integration_ready.json() == {"status": "not_ready"}
    assert integration_ready["Cache-Control"] == "no-store"
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}


def test_integration_readiness_accepts_a_valid_keyring(tmp_path: Path) -> None:
    key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    key_file = tmp_path / "integration-keyring.json"
    key_file.write_text(json.dumps({"active_key_id": "test", "keys": {"test": key}}))

    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
        CIVICLOOP_INTEGRATION_KEY_FILE=key_file,
    ):
        response = Client().get("/api/v1/admin/integrations/status")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_integration_readiness_is_hidden_unless_both_features_are_enabled() -> None:
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=False,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
    ):
        response = Client().get("/api/v1/admin/integrations/status")

    assert response.status_code == 404
