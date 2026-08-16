import re
import uuid
from abc import ABC, abstractmethod
from datetime import timedelta
from typing import cast

from django.db import transaction
from django.utils import timezone

from integrations.crypto import EncryptedEnvelope, decrypt_secret, encrypt_secret
from integrations.exceptions import IntegrationCryptoError, SecretUnavailable
from integrations.models import EncryptedSecret, Provider, SecretStatus
from integrations.types import SecretLease, SecretMetadata, SecretReference

MAX_LEASE_SECONDS = 5 * 60
PURPOSE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
APPROVED_PURPOSES = frozenset(
    {"connection_test", "draft_create", "evaluation_judge", "inference", "metadata_read"}
)


class SecretStore(ABC):
    @abstractmethod
    def put(self, *, provider: str, scope: str, value: bytes) -> SecretReference:
        """Store a new encrypted credential and return its opaque reference."""

    @abstractmethod
    def lease(
        self, reference: SecretReference, *, purpose: str, ttl: timedelta
    ) -> SecretLease:
        """Return a short-lived provider- and purpose-bound credential lease."""

    @abstractmethod
    def replace(self, reference: SecretReference, *, value: bytes) -> SecretReference:
        """Replace a credential transactionally and invalidate stale references."""

    @abstractmethod
    def disable(self, reference: SecretReference) -> None:
        """Disable a credential without deleting its audit metadata."""

    @abstractmethod
    def metadata(self, reference: SecretReference) -> SecretMetadata:
        """Return metadata only; never credential material."""


class PostgresSecretStore(SecretStore):
    def put(self, *, provider: str, scope: str, value: bytes) -> SecretReference:
        self._validate_provider_and_scope(provider, scope)
        secret_id = uuid.uuid4()
        envelope = encrypt_secret(value, secret_id=secret_id, provider=provider, scope=scope)
        secret = EncryptedSecret.objects.create(
            id=secret_id,
            provider=provider,
            scope=scope,
            ciphertext=envelope.ciphertext,
            nonce=envelope.nonce,
            algorithm=envelope.algorithm,
            key_id=envelope.key_id,
            envelope_version=envelope.version,
        )
        return self._reference(secret)

    def lease(self, reference: SecretReference, *, purpose: str, ttl: timedelta) -> SecretLease:
        self._validate_lease_request(reference, purpose, ttl)
        secret = self._secret_for_reference(reference, current_version=True)
        if secret.status != SecretStatus.ACTIVE:
            raise SecretUnavailable()
        try:
            plaintext = decrypt_secret(
                self._envelope(secret),
                secret_id=secret.id,
                provider=secret.provider,
                scope=secret.scope,
            )
        except IntegrationCryptoError:
            raise SecretUnavailable() from None
        return SecretLease(
            reference=self._reference(secret),
            purpose=purpose,
            expires_at=timezone.now() + ttl,
            _plaintext=plaintext,
        )

    def replace(self, reference: SecretReference, *, value: bytes) -> SecretReference:
        with transaction.atomic():
            secret = self._secret_for_reference(reference, current_version=True, for_update=True)
            if secret.status != SecretStatus.ACTIVE:
                raise SecretUnavailable()
            envelope = encrypt_secret(
                value, secret_id=secret.id, provider=secret.provider, scope=secret.scope
            )
            secret.ciphertext = envelope.ciphertext
            secret.nonce = envelope.nonce
            secret.algorithm = envelope.algorithm
            secret.key_id = envelope.key_id
            secret.envelope_version = envelope.version
            secret.version += 1
            secret.replaced_at = timezone.now()
            secret.save(
                update_fields=[
                    "ciphertext",
                    "nonce",
                    "algorithm",
                    "key_id",
                    "envelope_version",
                    "version",
                    "replaced_at",
                    "updated_at",
                ]
            )
        return self._reference(secret)

    def disable(self, reference: SecretReference) -> None:
        with transaction.atomic():
            secret = self._secret_for_reference(reference, current_version=True, for_update=True)
            if secret.status == SecretStatus.DISABLED:
                return
            secret.status = SecretStatus.DISABLED
            secret.disabled_at = timezone.now()
            secret.version += 1
            secret.save(update_fields=["status", "disabled_at", "version", "updated_at"])

    def metadata(self, reference: SecretReference) -> SecretMetadata:
        secret = self._secret_for_reference(reference, current_version=False)
        return SecretMetadata(
            id=secret.id,
            provider=secret.provider,
            scope=secret.scope,
            status=secret.status,
            version=secret.version,
            created_at=secret.created_at,
            replaced_at=secret.replaced_at,
            disabled_at=secret.disabled_at,
        )

    @staticmethod
    def _reference(secret: EncryptedSecret) -> SecretReference:
        return SecretReference(
            id=secret.id, provider=secret.provider, scope=secret.scope, version=secret.version
        )

    @staticmethod
    def _envelope(secret: EncryptedSecret) -> EncryptedEnvelope:
        return EncryptedEnvelope(
            version=secret.envelope_version,
            algorithm=secret.algorithm,
            key_id=secret.key_id,
            nonce=bytes(secret.nonce),
            ciphertext=bytes(secret.ciphertext),
        )

    @staticmethod
    def _validate_provider_and_scope(provider: str, scope: str) -> None:
        if provider not in Provider.values or PURPOSE_PATTERN.fullmatch(scope) is None:
            raise SecretUnavailable()

    @staticmethod
    def _validate_lease_request(reference: SecretReference, purpose: str, ttl: timedelta) -> None:
        if (
            not isinstance(reference, SecretReference)
            or purpose not in APPROVED_PURPOSES
            or PURPOSE_PATTERN.fullmatch(purpose) is None
            or not isinstance(ttl, timedelta)
            or not timedelta(0) < ttl <= timedelta(seconds=MAX_LEASE_SECONDS)
        ):
            raise SecretUnavailable()

    @staticmethod
    def _secret_for_reference(
        reference: SecretReference, *, current_version: bool, for_update: bool = False
    ) -> EncryptedSecret:
        if not isinstance(reference, SecretReference):
            raise SecretUnavailable()
        queryset = EncryptedSecret.objects
        if for_update:
            queryset = queryset.select_for_update()
        try:
            secret = cast(EncryptedSecret, queryset.get(id=reference.id))
        except EncryptedSecret.DoesNotExist:
            raise SecretUnavailable() from None
        if (
            secret.provider != reference.provider
            or secret.scope != reference.scope
            or (current_version and secret.version != reference.version)
        ):
            raise SecretUnavailable()
        return secret
