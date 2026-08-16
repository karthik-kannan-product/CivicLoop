import base64
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from identity.exceptions import IdentityError
from identity.services.security import record_security_event
from integrations.exceptions import SecretUnavailable
from integrations.secret_store import PostgresSecretStore

PLAINTEXT = b"synthetic-eventbrite-private-token"
REPLACEMENT = b"synthetic-rotated-eventbrite-private-token"
KEY = bytes(range(32))


def write_key_ring(path: Path) -> None:
    encoded_key = base64.urlsafe_b64encode(KEY).rstrip(b"=").decode("ascii")
    path.write_text(
        json.dumps(
            {"active_key_id": "integration-test", "keys": {"integration-test": encoded_key}}
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)


@pytest.fixture
def secret_store(settings, tmp_path: Path) -> PostgresSecretStore:
    path = tmp_path / "integration-keyring.json"
    write_key_ring(path)
    settings.CIVICLOOP_INTEGRATION_KEY_FILE = path
    return PostgresSecretStore()


@pytest.mark.django_db
def test_put_metadata_and_purpose_bound_lease(secret_store: PostgresSecretStore) -> None:
    reference = secret_store.put(provider="eventbrite", scope="private_token", value=PLAINTEXT)

    metadata = secret_store.metadata(reference)
    with secret_store.lease(
        reference, purpose="connection_test", ttl=timedelta(seconds=30)
    ) as lease:
        assert lease.read() == PLAINTEXT

    assert metadata.provider == "eventbrite"
    assert metadata.scope == "private_token"
    assert metadata.status == "active"
    assert PLAINTEXT.decode("ascii") not in repr(lease)


@pytest.mark.django_db
def test_mismatched_unknown_or_expired_leases_fail_closed(
    secret_store: PostgresSecretStore,
) -> None:
    reference = secret_store.put(provider="eventbrite", scope="private_token", value=PLAINTEXT)

    wrong_provider = reference.with_provider("iterable")
    with pytest.raises(SecretUnavailable):
        secret_store.lease(wrong_provider, purpose="connection_test", ttl=timedelta(seconds=30))
    with pytest.raises(SecretUnavailable):
        secret_store.lease(reference, purpose="not-an-approved-purpose", ttl=timedelta(seconds=30))
    with pytest.raises(SecretUnavailable):
        secret_store.lease(reference, purpose="connection_test", ttl=timedelta(seconds=0))


@pytest.mark.django_db
def test_replace_invalidates_stale_reference_and_preserves_audit_metadata(
    secret_store: PostgresSecretStore,
) -> None:
    reference = secret_store.put(provider="eventbrite", scope="private_token", value=PLAINTEXT)
    replacement = secret_store.replace(reference, value=REPLACEMENT)

    with pytest.raises(SecretUnavailable):
        secret_store.lease(reference, purpose="connection_test", ttl=timedelta(seconds=30))
    with secret_store.lease(
        replacement, purpose="connection_test", ttl=timedelta(seconds=30)
    ) as lease:
        assert lease.read() == REPLACEMENT

    metadata = secret_store.metadata(replacement)
    assert replacement.version == reference.version + 1
    assert metadata.replaced_at is not None
    assert metadata.created_at <= metadata.replaced_at


@pytest.mark.django_db
def test_disable_blocks_new_leases_without_deleting_metadata(
    secret_store: PostgresSecretStore,
) -> None:
    reference = secret_store.put(provider="groq", scope="api_key", value=PLAINTEXT)

    secret_store.disable(reference)

    metadata = secret_store.metadata(reference)
    assert metadata.status == "disabled"
    assert metadata.disabled_at is not None
    with pytest.raises(SecretUnavailable):
        secret_store.lease(reference, purpose="inference", ttl=timedelta(seconds=30))


@pytest.mark.django_db
def test_secret_bearing_lease_is_redacted_and_rejected_by_security_event_sanitizer(
    secret_store: PostgresSecretStore,
) -> None:
    reference = secret_store.put(provider="openai", scope="project_key", value=PLAINTEXT)
    lease = secret_store.lease(reference, purpose="inference", ttl=timedelta(seconds=30))

    with pytest.raises(IdentityError):
        record_security_event(
            action="integration_test",
            outcome="failure",
            owner=None,
            source_ip=None,
            session_id=None,
            details={"lease": lease},
        )

    assert PLAINTEXT.decode("ascii") not in repr(lease)
