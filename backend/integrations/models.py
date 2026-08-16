import json
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Provider(models.TextChoices):
    EVENTBRITE = "eventbrite", "Eventbrite"
    GROQ = "groq", "Groq"
    ITERABLE = "iterable", "Iterable"
    OPENAI = "openai", "OpenAI"


class SecretStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DISABLED = "disabled", "Disabled"


class ConnectionState(models.TextChoices):
    NOT_CONFIGURED = "not_configured", "Not configured"
    CONFIGURED = "configured", "Configured"
    HEALTHY = "healthy", "Healthy"
    DEGRADED = "degraded", "Degraded"
    DISABLED = "disabled", "Disabled"


class HealthOutcome(models.TextChoices):
    HEALTHY = "healthy", "Healthy"
    DEGRADED = "degraded", "Degraded"


class HealthErrorCategory(models.TextChoices):
    AUTHENTICATION = "authentication", "Authentication"
    AUTHORIZATION = "authorization", "Authorization"
    RATE_LIMIT = "rate_limit", "Rate limit"
    TIMEOUT = "timeout", "Timeout"
    NETWORK = "network", "Network"
    INVALID_RESPONSE = "invalid_response", "Invalid response"
    PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"


CONFIGURATION_BY_PROVIDER = {
    Provider.EVENTBRITE: {},
    Provider.ITERABLE: {"region": frozenset({"us", "eu"})},
    Provider.OPENAI: {"model": frozenset({"openai/gpt-oss-20b"})},
    Provider.GROQ: {"model": frozenset({"openai/gpt-oss-20b"})},
}
CAPABILITIES = frozenset(
    {"connection_test", "draft_create", "evaluation_judge", "inference", "metadata_read"}
)


class EncryptedSecret(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, choices=Provider.choices)
    scope = models.CharField(max_length=64)
    ciphertext = models.BinaryField()
    nonce = models.BinaryField(max_length=12)
    algorithm = models.CharField(max_length=32, default="AES-256-GCM", editable=False)
    key_id = models.CharField(max_length=64, editable=False)
    envelope_version = models.PositiveSmallIntegerField(default=1, editable=False)
    status = models.CharField(
        max_length=16, choices=SecretStatus.choices, default=SecretStatus.ACTIVE
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    replaced_at = models.DateTimeField(null=True, blank=True)
    disabled_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="created_integration_secrets",
        on_delete=models.PROTECT,
    )
    replaced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="replaced_integration_secrets",
        on_delete=models.PROTECT,
    )
    disabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="disabled_integration_secrets",
        on_delete=models.PROTECT,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(provider__in=Provider.values), name="integrations_secret_provider"
            ),
            models.CheckConstraint(
                condition=Q(status__in=SecretStatus.values), name="integrations_secret_status"
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1), name="integrations_secret_version_positive"
            ),
            models.CheckConstraint(
                condition=Q(algorithm="AES-256-GCM"), name="integrations_secret_algorithm"
            ),
            models.CheckConstraint(
                condition=Q(envelope_version=1), name="integrations_secret_envelope_version"
            ),
        ]

    def __str__(self) -> str:
        return f"Encrypted integration secret {self.id}"


class IntegrationConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, choices=Provider.choices, unique=True)
    state = models.CharField(
        max_length=32, choices=ConnectionState.choices, default=ConnectionState.NOT_CONFIGURED
    )
    capabilities = models.JSONField(default=list)
    configuration = models.JSONField(default=dict)
    secret = models.ForeignKey(
        EncryptedSecret,
        null=True,
        blank=True,
        related_name="connections",
        on_delete=models.PROTECT,
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_successful_test_at = models.DateTimeField(null=True, blank=True)
    last_failure_category = models.CharField(
        max_length=32, choices=HealthErrorCategory.choices, blank=True
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(provider__in=Provider.values), name="integrations_connection_provider"
            ),
            models.CheckConstraint(
                condition=Q(state__in=ConnectionState.values), name="integrations_connection_state"
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1), name="integrations_connection_version_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider} integration ({self.state})"

    def clean(self) -> None:
        super().clean()
        configuration = self.configuration
        allowed_configuration = CONFIGURATION_BY_PROVIDER.get(self.provider)
        if not isinstance(configuration, dict) or allowed_configuration is None:
            raise ValidationError({"configuration": "Integration configuration is invalid."})
        if set(configuration) != set(allowed_configuration):
            raise ValidationError({"configuration": "Integration configuration is invalid."})
        for key, allowed_values in allowed_configuration.items():
            if configuration.get(key) not in allowed_values:
                raise ValidationError({"configuration": "Integration configuration is invalid."})
        capabilities = self.capabilities
        if (
            not isinstance(capabilities, list)
            or len(capabilities) > len(CAPABILITIES)
            or len(capabilities) != len(set(capabilities))
            or set(capabilities) - CAPABILITIES
        ):
            raise ValidationError({"capabilities": "Integration capabilities are invalid."})
        if len(json.dumps(configuration, separators=(",", ":"))) > 256:
            raise ValidationError({"configuration": "Integration configuration is invalid."})

class IntegrationHealthCheck(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        IntegrationConnection,
        related_name="health_checks",
        on_delete=models.PROTECT,
    )
    outcome = models.CharField(max_length=16, choices=HealthOutcome.choices)
    error_category = models.CharField(
        max_length=32, choices=HealthErrorCategory.choices, blank=True
    )
    duration_ms = models.PositiveIntegerField()
    correlation_id = models.UUIDField()
    tested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(outcome__in=HealthOutcome.values), name="integrations_health_outcome"
            ),
            models.CheckConstraint(
                condition=Q(duration_ms__gte=0) & Q(duration_ms__lte=30000),
                name="integrations_health_duration_bounded",
            ),
            models.CheckConstraint(
                condition=(
                    Q(outcome=HealthOutcome.HEALTHY, error_category="")
                    | Q(
                        outcome=HealthOutcome.DEGRADED,
                        error_category__in=HealthErrorCategory.values,
                    )
                ),
                name="integrations_health_safe_error_category",
            ),
        ]

    def __str__(self) -> str:
        return f"Integration health check {self.id} ({self.outcome})"
