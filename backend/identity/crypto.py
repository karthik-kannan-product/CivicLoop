import base64
import binascii
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

from .exceptions import IdentityCryptoError

ENVELOPE_FIELDS = frozenset({"version", "algorithm", "key_id", "nonce", "ciphertext"})
KEY_RING_FIELDS = frozenset({"active_key_id", "keys"})
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_KEY_RING_BYTES = 64 * 1024
MAX_KEYS = 16
MAX_TOTP_SEED_BYTES = 128


@dataclass(frozen=True)
class IdentityKeyRing:
    active_key_id: str
    keys: Mapping[str, bytes]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _decode_base64url(value: str) -> bytes:
    if not value or "=" in value:
        raise ValueError("Invalid base64url")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def load_identity_key_ring(path: Path) -> IdentityKeyRing:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_KEY_RING_BYTES:
            raise ValueError("Key ring is too large")
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        if not isinstance(parsed, dict) or set(parsed) != KEY_RING_FIELDS:
            raise ValueError("Invalid key ring fields")
        active_key_id = parsed["active_key_id"]
        raw_keys = parsed["keys"]
        if (
            not isinstance(active_key_id, str)
            or KEY_ID_PATTERN.fullmatch(active_key_id) is None
            or not isinstance(raw_keys, dict)
            or not 1 <= len(raw_keys) <= MAX_KEYS
            or active_key_id not in raw_keys
        ):
            raise ValueError("Invalid key ring")
        keys: dict[str, bytes] = {}
        for key_id, encoded_key in raw_keys.items():
            if (
                not isinstance(key_id, str)
                or KEY_ID_PATTERN.fullmatch(key_id) is None
                or not isinstance(encoded_key, str)
            ):
                raise ValueError("Invalid key entry")
            key = _decode_base64url(encoded_key)
            if len(key) != 32:
                raise ValueError("Invalid AES key length")
            keys[key_id] = key
        return IdentityKeyRing(active_key_id, MappingProxyType(keys))
    except (OSError, UnicodeError, json.JSONDecodeError, binascii.Error, ValueError, TypeError):
        raise IdentityCryptoError() from None


def _configured_key_ring() -> IdentityKeyRing:
    path = settings.CIVICLOOP_IDENTITY_KEY_FILE
    if not isinstance(path, Path):
        raise IdentityCryptoError()
    return load_identity_key_ring(path)


def _totp_aad(*, owner_id: UUID, device_id: UUID) -> bytes:
    return (
        f"civicloop.identity.totp-seed.v1\0{owner_id}\0{device_id}".encode("ascii")
    )


def encrypt_totp_seed(
    seed: bytes,
    *,
    owner_id: UUID,
    device_id: UUID,
) -> dict[str, object]:
    if not isinstance(seed, bytes) or not 1 <= len(seed) <= MAX_TOTP_SEED_BYTES:
        raise IdentityCryptoError()
    try:
        key_ring = _configured_key_ring()
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key_ring.keys[key_ring.active_key_id]).encrypt(
            nonce,
            seed,
            _totp_aad(owner_id=owner_id, device_id=device_id),
        )
        return {
            "version": 1,
            "algorithm": "AES-256-GCM",
            "key_id": key_ring.active_key_id,
            "nonce": _encode_base64url(nonce),
            "ciphertext": _encode_base64url(ciphertext),
        }
    except IdentityCryptoError:
        raise
    except Exception:
        raise IdentityCryptoError() from None


def decrypt_totp_seed(
    envelope: Mapping[str, object],
    *,
    owner_id: UUID,
    device_id: UUID,
) -> bytes:
    try:
        if set(envelope) != ENVELOPE_FIELDS:
            raise ValueError("Invalid envelope fields")
        if envelope["version"] != 1 or envelope["algorithm"] != "AES-256-GCM":
            raise ValueError("Unsupported envelope")
        key_id = envelope["key_id"]
        encoded_nonce = envelope["nonce"]
        encoded_ciphertext = envelope["ciphertext"]
        if not all(isinstance(value, str) for value in (key_id, encoded_nonce, encoded_ciphertext)):
            raise ValueError("Invalid envelope types")
        key_ring = _configured_key_ring()
        if key_id not in key_ring.keys:
            raise ValueError("Unknown key")
        nonce = _decode_base64url(encoded_nonce)
        ciphertext = _decode_base64url(encoded_ciphertext)
        if len(nonce) != 12 or len(ciphertext) < 17:
            raise ValueError("Invalid encrypted value")
        return AESGCM(key_ring.keys[key_id]).decrypt(
            nonce,
            ciphertext,
            _totp_aad(owner_id=owner_id, device_id=device_id),
        )
    except (IdentityCryptoError, InvalidTag, binascii.Error, KeyError, TypeError, ValueError):
        raise IdentityCryptoError() from None
