import base64
import json
from pathlib import Path
from uuid import UUID

import pytest
from django.test import override_settings
from identity.crypto import (
    IdentityCryptoError,
    decrypt_totp_seed,
    encrypt_totp_seed,
    load_identity_key_ring,
)

OWNER_ID = UUID("bdf16700-29e9-4bf7-93a7-35e2f7bb6f44")
OTHER_OWNER_ID = UUID("81266a78-4939-4ea2-b8f9-a7940adb0cb3")
DEVICE_ID = UUID("ee1e93d5-2065-4337-85b3-9d25e29e5564")
OTHER_DEVICE_ID = UUID("77c84f8b-95b0-4edf-95cf-31906762cc7f")
SYNTHETIC_KEY = bytes(range(32))
SYNTHETIC_SEED = b"synthetic-totp-seed"


def encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def write_key_ring(path: Path, *, key: bytes = SYNTHETIC_KEY) -> None:
    path.write_text(
        json.dumps(
            {
                "active_key_id": "identity-test-1",
                "keys": {"identity-test-1": encoded(key)},
            }
        ),
        encoding="utf-8",
    )


def test_key_ring_loads_a_single_active_256_bit_key(tmp_path: Path) -> None:
    key_path = tmp_path / "identity.json"
    write_key_ring(key_path)

    ring = load_identity_key_ring(key_path)

    assert ring.active_key_id == "identity-test-1"
    assert ring.keys == {"identity-test-1": SYNTHETIC_KEY}


@pytest.mark.parametrize(
    "contents",
    [
        "not-json",
        "[]",
        '{"active_key_id":"missing-keys"}',
        '{"active_key_id":"missing","keys":{"present":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}}',
        '{"active_key_id":"short","keys":{"short":"c2hvcnQ"}}',
        '{"active_key_id":"x","keys":{"x":"%%%"}}',
        '{"active_key_id":"x","keys":{"x":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},"extra":true}',
        '{"active_key_id":"x","active_key_id":"y","keys":{}}',
    ],
)
def test_key_ring_rejects_malformed_or_ambiguous_input(tmp_path: Path, contents: str) -> None:
    key_path = tmp_path / "identity.json"
    key_path.write_text(contents, encoding="utf-8")

    with pytest.raises(IdentityCryptoError, match="Identity credential protection is unavailable"):
        load_identity_key_ring(key_path)


def test_missing_key_ring_uses_the_same_redacted_error(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(IdentityCryptoError) as raised:
        load_identity_key_ring(missing_path)

    assert str(missing_path) not in str(raised.value)
    assert "missing.json" not in str(raised.value)


def test_encryption_round_trip_uses_a_fresh_nonce(tmp_path: Path) -> None:
    key_path = tmp_path / "identity.json"
    write_key_ring(key_path)

    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=key_path):
        first = encrypt_totp_seed(SYNTHETIC_SEED, owner_id=OWNER_ID, device_id=DEVICE_ID)
        second = encrypt_totp_seed(SYNTHETIC_SEED, owner_id=OWNER_ID, device_id=DEVICE_ID)
        plaintext = decrypt_totp_seed(first, owner_id=OWNER_ID, device_id=DEVICE_ID)

    assert plaintext == SYNTHETIC_SEED
    assert set(first) == {"version", "algorithm", "key_id", "nonce", "ciphertext"}
    assert first["version"] == 1
    assert first["algorithm"] == "AES-256-GCM"
    assert first["nonce"] != second["nonce"]
    assert first["ciphertext"] != second["ciphertext"]


@pytest.mark.parametrize(
    ("owner_id", "device_id"),
    [
        (OTHER_OWNER_ID, DEVICE_ID),
        (OWNER_ID, OTHER_DEVICE_ID),
    ],
)
def test_decryption_rejects_aad_substitution(
    tmp_path: Path,
    owner_id: UUID,
    device_id: UUID,
) -> None:
    key_path = tmp_path / "identity.json"
    write_key_ring(key_path)

    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=key_path):
        envelope = encrypt_totp_seed(
            SYNTHETIC_SEED,
            owner_id=OWNER_ID,
            device_id=DEVICE_ID,
        )
        with pytest.raises(IdentityCryptoError):
            decrypt_totp_seed(envelope, owner_id=owner_id, device_id=device_id)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("algorithm", "AES-128-GCM"),
        ("key_id", "unknown-key"),
        ("nonce", "%%%"),
        ("ciphertext", "%%%"),
    ],
)
def test_decryption_rejects_unknown_or_malformed_envelope_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    key_path = tmp_path / "identity.json"
    write_key_ring(key_path)

    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=key_path):
        envelope = encrypt_totp_seed(
            SYNTHETIC_SEED,
            owner_id=OWNER_ID,
            device_id=DEVICE_ID,
        )
        envelope[field] = value
        with pytest.raises(IdentityCryptoError):
            decrypt_totp_seed(envelope, owner_id=OWNER_ID, device_id=DEVICE_ID)


def test_decryption_rejects_tampered_ciphertext_without_exposing_values(tmp_path: Path) -> None:
    key_path = tmp_path / "identity.json"
    write_key_ring(key_path)

    with override_settings(CIVICLOOP_IDENTITY_KEY_FILE=key_path):
        envelope = encrypt_totp_seed(
            SYNTHETIC_SEED,
            owner_id=OWNER_ID,
            device_id=DEVICE_ID,
        )
        ciphertext = bytearray(base64.urlsafe_b64decode(str(envelope["ciphertext"]) + "=="))
        ciphertext[-1] ^= 1
        envelope["ciphertext"] = encoded(bytes(ciphertext))
        with pytest.raises(IdentityCryptoError) as raised:
            decrypt_totp_seed(envelope, owner_id=OWNER_ID, device_id=DEVICE_ID)

    message = str(raised.value)
    assert SYNTHETIC_SEED.decode("ascii") not in message
    assert str(envelope["ciphertext"]) not in message
