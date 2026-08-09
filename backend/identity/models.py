import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django_otp.models import Device, ThrottlingMixin


class AdministratorProfile(models.Model):
    class Status(models.TextChoices):
        ENROLLMENT_REQUIRED = "enrollment_required", "Enrollment required"
        ACTIVE = "active", "Active"
        RECOVERY_REQUIRED = "recovery_required", "Recovery required"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="administrator_profile",
        on_delete=models.PROTECT,
    )
    status = models.CharField(max_length=32, choices=Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                models.Value(1),
                condition=~Q(status="disabled"),
                name="identity_one_enabled_owner",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.username} ({self.status})"


class AdministratorTOTPDevice(ThrottlingMixin, Device):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="administrator_totp_devices",
        on_delete=models.PROTECT,
    )
    profile = models.ForeignKey(
        AdministratorProfile,
        related_name="totp_devices",
        on_delete=models.PROTECT,
    )
    seed_envelope = models.JSONField()
    digits = models.PositiveSmallIntegerField(default=6, editable=False)
    step = models.PositiveSmallIntegerField(default=30, editable=False)
    drift = models.SmallIntegerField(default=0)
    last_t = models.BigIntegerField(default=-1)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    replaced_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(digits=6),
                name="identity_totp_six_digits",
            ),
            models.CheckConstraint(
                condition=Q(step=30),
                name="identity_totp_thirty_seconds",
            ),
        ]

    def get_throttle_factor(self) -> float:
        return 1.0


class RecoveryCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        AdministratorProfile,
        related_name="recovery_codes",
        on_delete=models.PROTECT,
    )
    batch_id = models.UUIDField()
    public_id = models.CharField(max_length=8, unique=True)
    encoded_secret = models.CharField(max_length=256)
    created_at = models.DateTimeField(auto_now_add=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("profile", "batch_id", "consumed_at"),
                name="identity_recovery_batch_idx",
            )
        ]

    def __str__(self) -> str:
        return f"Recovery code {self.public_id}"


class AdministratorSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        AdministratorProfile,
        related_name="administrator_sessions",
        on_delete=models.PROTECT,
    )
    session_key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    authenticated_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    mfa_verified_at = models.DateTimeField(null=True, blank=True)
    fresh_verified_at = models.DateTimeField(null=True, blank=True)
    absolute_expires_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    device_label = models.CharField(max_length=160)
    user_agent = models.CharField(max_length=512, blank=True)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    recovery_restricted = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(
                fields=("profile", "revoked_at", "absolute_expires_at"),
                name="identity_session_owner_idx",
            ),
            models.Index(
                fields=("expires_at", "revoked_at"),
                name="identity_session_expiry_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Administrator session {self.id}"


class AdministratorSecurityEvent(models.Model):
    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILURE = "failure", "Failure"
        DENIED = "denied", "Denied"
        UNAVAILABLE = "unavailable", "Unavailable"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        AdministratorProfile,
        related_name="security_events",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="administrator_security_events",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    action = models.CharField(max_length=100)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(default=dict)
    source_ip = models.GenericIPAddressField(null=True, blank=True)
    session_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("profile", "-created_at", "-id"),
                name="identity_event_owner_idx",
            ),
            models.Index(
                fields=("action", "-created_at"),
                name="identity_event_action_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action}: {self.outcome}"
