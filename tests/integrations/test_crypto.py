import base64
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from django.test import override_settings
from integrations.crypto import (
    IntegrationCryptoError,
    decrypt_secret,
    encrypt_secret,
    load_integration_key_ring,
)

SECRET_ID = UUID("d09b1564-9e8c-4d25-b6a3-80c02e71a006")
OTHER_SECRET_ID = UUID("203db071-5fc4-49f8-8c93-6b8e4d51008d")
SYNTHETIC_KEY = bytes(range(32))
PREVIOUS_KEY = bytes(reversed(range(32)))
PLAINTEXT = b"synthetic-integration-credential"


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def write_key_ring(path: Path, *, active_key_id: str = "integration-current", keys=None) -> None:
    path.write_text(
        json.dumps(
            {
                "active_key_id": active_key_id,
                "keys": keys
                or {
                    "integration-current": encoded(SYNTHETIC_KEY),
                    "integration-previous": encoded(PREVIOUS_KEY),
                },
            }
        ),
        encoding="utf-8",
    )


def test_key_ring_loads_current_and_previous_256_bit_keys(tmp_path: Path) -> None:
    path = tmp_path / "integration-keyring.json"
    write_key_ring(path)

    key_ring = load_integration_key_ring(path)

    assert key_ring.active_key_id == "integration-current"
    assert key_ring.keys["integration-previous"] == PREVIOUS_KEY
    assert "integration-current" not in repr(key_ring)


@pytest.mark.parametrize(
    "contents",
    [
        "not-json",
        "[]",
        '{"active_key_id":"missing","keys":{"present":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}',
        '{"active_key_id":"short","keys":{"short":"c2hvcnQ"}}',
        '{"active_key_id":"x","keys":{"x":"%%%"}}',
        '{"active_key_id":"x","active_key_id":"y","keys":{}}',
    ],
)
def test_key_ring_rejects_malformed_or_ambiguous_material(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "integration-keyring.json"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(IntegrationCryptoError) as raised:
        load_integration_key_ring(path)

    assert str(path) not in str(raised.value)
    assert "integration-keyring" not in str(raised.value)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not provide POSIX key-file modes")
def test_key_ring_rejects_group_or_world_readable_file(tmp_path: Path) -> None:
    path = tmp_path / "integration-keyring.json"
    write_key_ring(path)
    path.chmod(0o644)

    with pytest.raises(IntegrationCryptoError):
        load_integration_key_ring(path)


def test_encryption_uses_fresh_nonces_and_binds_secret_metadata(tmp_path: Path) -> None:
    path = tmp_path / "integration-keyring.json"
    write_key_ring(path)

    with override_settings(CIVICLOOP_INTEGRATION_KEY_FILE=path):
        first = encrypt_secret(
            PLAINTEXT, secret_id=SECRET_ID, provider="eventbrite", scope="private_token"
        )
        second = encrypt_secret(
            PLAINTEXT, secret_id=SECRET_ID, provider="eventbrite", scope="private_token"
        )
        plaintext = decrypt_secret(
            first, secret_id=SECRET_ID, provider="eventbrite", scope="private_token"
        )

        with pytest.raises(IntegrationCryptoError):
            decrypt_secret(
                first, secret_id=OTHER_SECRET_ID, provider="eventbrite", scope="private_token"
            )

    assert plaintext == PLAINTEXT
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert PLAINTEXT.decode("ascii") not in repr(first)
    assert "integration-current" not in repr(first)


def test_tampering_and_unknown_key_fail_without_sensitive_error_details(tmp_path: Path) -> None:
    path = tmp_path / "integration-keyring.json"
    write_key_ring(path)

    with override_settings(CIVICLOOP_INTEGRATION_KEY_FILE=path):
        envelope = encrypt_secret(
            PLAINTEXT, secret_id=SECRET_ID, provider="iterable", scope="api_key"
        )
        tampered = replace(envelope, ciphertext=envelope.ciphertext[:-1] + b"x")
        with pytest.raises(IntegrationCryptoError) as raised:
            decrypt_secret(tampered, secret_id=SECRET_ID, provider="iterable", scope="api_key")

    message = str(raised.value)
    assert PLAINTEXT.decode("ascii") not in message
    assert encoded(tampered.ciphertext) not in message
    assert tampered.key_id not in message


def test_previous_key_decrypts_while_current_key_encrypts(tmp_path: Path) -> None:
    path = tmp_path / "integration-keyring.json"
    write_key_ring(path, active_key_id="integration-previous")

    with override_settings(CIVICLOOP_INTEGRATION_KEY_FILE=path):
        old_envelope = encrypt_secret(
            PLAINTEXT, secret_id=SECRET_ID, provider="openai", scope="project_key"
        )
        write_key_ring(path, active_key_id="integration-current")
        current_envelope = encrypt_secret(
            PLAINTEXT, secret_id=SECRET_ID, provider="openai", scope="project_key"
        )
        plaintext = decrypt_secret(
            old_envelope, secret_id=SECRET_ID, provider="openai", scope="project_key"
        )

    assert old_envelope.key_id == "integration-previous"
    assert current_envelope.key_id == "integration-current"
    assert plaintext == PLAINTEXT
