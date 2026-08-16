from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.utils import timezone

from integrations.exceptions import SecretUnavailable


@dataclass(frozen=True, repr=False)
class SecretReference:
    id: UUID
    provider: str
    scope: str
    version: int

    def with_provider(self, provider: str) -> "SecretReference":
        return SecretReference(
            id=self.id, provider=provider, scope=self.scope, version=self.version
        )

    def __repr__(self) -> str:
        return "SecretReference(redacted)"


@dataclass(frozen=True, repr=False)
class SecretMetadata:
    id: UUID
    provider: str
    scope: str
    status: str
    version: int
    created_at: datetime
    replaced_at: datetime | None
    disabled_at: datetime | None

    def __repr__(self) -> str:
        return "SecretMetadata(redacted)"


@dataclass(repr=False)
class SecretLease:
    reference: SecretReference
    purpose: str
    expires_at: datetime
    _plaintext: bytes | None = field(repr=False)

    def read(self) -> bytes:
        if self._plaintext is None or timezone.now() >= self.expires_at:
            raise SecretUnavailable()
        return self._plaintext

    def __enter__(self) -> "SecretLease":
        self.read()
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self._plaintext = None

    def __repr__(self) -> str:
        return "SecretLease(redacted)"
