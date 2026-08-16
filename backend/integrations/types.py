from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from django.utils import timezone

from integrations.exceptions import SecretUnavailable

CredentialUseResult = TypeVar("CredentialUseResult")


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
    caller_id: UUID
    workflow_id: UUID | None
    purpose: str
    expires_at: datetime
    _plaintext: bytearray | None = field(repr=False)
    _used: bool = field(default=False, init=False, repr=False)

    def use(
        self, operation: Callable[[memoryview], CredentialUseResult]
    ) -> CredentialUseResult:
        """Expose a read-only view to one scoped operation, then destroy the lease."""
        self._ensure_available()
        plaintext = self._plaintext
        if plaintext is None:
            raise SecretUnavailable()
        self._used = True
        writable_view = memoryview(plaintext)
        scoped_view = writable_view.toreadonly()
        try:
            return operation(scoped_view)
        finally:
            plaintext[:] = b"\0" * len(plaintext)
            scoped_view.release()
            writable_view.release()
            self._plaintext = None

    def _ensure_available(self) -> None:
        if self._used or self._plaintext is None or timezone.now() >= self.expires_at:
            self._close()
            raise SecretUnavailable()

    def _close(self) -> None:
        if self._plaintext is not None:
            self._plaintext[:] = b"\0" * len(self._plaintext)
            self._plaintext = None

    def __enter__(self) -> "SecretLease":
        self._ensure_available()
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self._close()

    def __repr__(self) -> str:
        return "SecretLease(redacted)"
