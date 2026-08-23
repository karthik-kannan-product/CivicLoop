import base64
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.test import RequestFactory, override_settings
from integrations.exceptions import IntegrationCryptoError, SecretUnavailable
from integrations.models import ConnectionState, IntegrationHealthCheck
from integrations.secret_store import PostgresSecretStore
from integrations.services import replace_credential
from integrations.services import test_connection as run_connection_test

from tests.identity.test_security_actions_api import create_authenticated_owner


@pytest.fixture
def integration_settings(tmp_path: Path) -> Iterator[None]:
    key = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    key_file = tmp_path / "integration-keyring.json"
    key_file.write_text(json.dumps({"active_key_id": "test", "keys": {"test": key}}))
    if os.name != "nt":
        key_file.chmod(0o600)
    with override_settings(
        CIVICLOOP_ADMIN_IDENTITY_ENABLED=True,
        CIVICLOOP_INTEGRATIONS_ENABLED=True,
        CIVICLOOP_INTEGRATION_KEY_FILE=key_file,
    ):
        yield


@pytest.mark.django_db
@pytest.mark.parametrize("failure", [SecretUnavailable(), IntegrationCryptoError()])
def test_connection_propagates_local_credential_failures_without_recording_degradation(
    integration_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    _client, _profile, actor, _password = create_authenticated_owner()
    request = RequestFactory().post(
        "/api/v1/admin/integrations/eventbrite/test", REMOTE_ADDR="192.0.2.44"
    )
    connection = replace_credential(
        provider="eventbrite",
        credential=b"synthetic-eventbrite-token",
        expected_version=1,
        actor=actor,
        request=request,
    )

    def unavailable_lease(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(PostgresSecretStore, "lease", unavailable_lease)

    with pytest.raises(type(failure)):
        run_connection_test(
            provider="eventbrite",
            expected_version=connection.version,
            actor=actor,
            request=request,
        )

    connection.refresh_from_db()
    assert connection.state == ConnectionState.CONFIGURED
    assert not IntegrationHealthCheck.objects.exists()
