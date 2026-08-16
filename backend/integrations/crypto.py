import base64
import binascii
import json
import os
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

from integrations.exceptions import IntegrationCryptoError

ALGORITHM = "AES-256-GCM"
ENVELOPE_VERSION = 1
ENVELOPE_FIELDS = frozenset({"version", "algorithm", "key_id", "nonce", "ciphertext"})
KEY_RING_FIELDS = frozenset({"active_key_id", "keys"})
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_KEY_RING_BYTES = 64 * 1024
MAX_KEYS = 16
MAX_SECRET_BYTES = 16 * 1024


@dataclass(frozen=True, repr=False)
class IntegrationKeyRing:
    active_key_id: str
    keys: Mapping[str, bytes]

    def __repr__(self) -> str:
        return "IntegrationKeyRing(redacted)"


@dataclass(frozen=True, repr=False)
class EncryptedEnvelope:
    version: int
    algorithm: str
    key_id: str
    nonce: bytes
    ciphertext: bytes

    def __repr__(self) -> str:
        return "EncryptedEnvelope(redacted)"


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


def _key_file_has_owner_only_permissions(path: Path) -> bool:
    if os.name == "nt":
        return True
    return not bool(path.stat().st_mode & 0o077)


def load_integration_key_ring(path: Path) -> IntegrationKeyRing:
    try:
        if not path.is_file() or not os.access(path, os.R_OK):
            raise ValueError("Key ring is unavailable")
        if not _key_file_has_owner_only_permissions(path):
            raise ValueError("Key ring permissions are unsafe")
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
        return IntegrationKeyRing(active_key_id=active_key_id, keys=MappingProxyType(keys))
    except (OSError, UnicodeError, json.JSONDecodeError, binascii.Error, TypeError, ValueError):
        raise IntegrationCryptoError() from None


def _configured_key_ring() -> IntegrationKeyRing:
    path = settings.CIVICLOOP_INTEGRATION_KEY_FILE
    if not isinstance(path, Path):
        raise IntegrationCryptoError()
    return load_integration_key_ring(path)


def _aad(*, secret_id: UUID, provider: str, scope: str, key_id: str) -> bytes:
    if not (
        isinstance(secret_id, UUID) and isinstance(provider, str) and isinstance(scope, str)
    ):
        raise ValueError("Invalid secret metadata")
    if not provider or not scope or "\0" in provider or "\0" in scope:
        raise ValueError("Invalid secret metadata")
    return (
        f"civicloop.integrations.secret.v{ENVELOPE_VERSION}\0{secret_id}\0{provider}\0"
        f"{scope}\0{ALGORITHM}\0{key_id}"
    ).encode("ascii")


def encrypt_secret(
    plaintext: bytes,
    *,
    secret_id: UUID,
    provider: str,
    scope: str,
) -> EncryptedEnvelope:
    if not isinstance(plaintext, bytes) or not 1 <= len(plaintext) <= MAX_SECRET_BYTES:
        raise IntegrationCryptoError()
    try:
        key_ring = _configured_key_ring()
        nonce = secrets.token_bytes(12)
        key_id = key_ring.active_key_id
        ciphertext = AESGCM(key_ring.keys[key_id]).encrypt(
            nonce,
            plaintext,
            _aad(secret_id=secret_id, provider=provider, scope=scope, key_id=key_id),
        )
        return EncryptedEnvelope(
            version=ENVELOPE_VERSION,
            algorithm=ALGORITHM,
            key_id=key_id,
            nonce=nonce,
            ciphertext=ciphertext,
        )
    except IntegrationCryptoError:
        raise
    except Exception:
        raise IntegrationCryptoError() from None


def decrypt_secret(
    envelope: EncryptedEnvelope,
    *,
    secret_id: UUID,
    provider: str,
    scope: str,
) -> bytes:
    try:
        if not isinstance(envelope, EncryptedEnvelope):
            raise ValueError("Invalid envelope")
        if envelope.version != ENVELOPE_VERSION or envelope.algorithm != ALGORITHM:
            raise ValueError("Unsupported envelope")
        if KEY_ID_PATTERN.fullmatch(envelope.key_id) is None:
            raise ValueError("Invalid key id")
        if len(envelope.nonce) != 12 or len(envelope.ciphertext) < 17:
            raise ValueError("Invalid encrypted value")
        key_ring = _configured_key_ring()
        key = key_ring.keys[envelope.key_id]
        return AESGCM(key).decrypt(
            envelope.nonce,
            envelope.ciphertext,
            _aad(
                secret_id=secret_id,
                provider=provider,
                scope=scope,
                key_id=envelope.key_id,
            ),
        )
    except (IntegrationCryptoError, InvalidTag, KeyError, TypeError, ValueError):
        raise IntegrationCryptoError() from None
