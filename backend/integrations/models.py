import json
import uuid
from typing import Any, cast

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
CAPABILITIES_BY_PROVIDER = {
    Provider.EVENTBRITE: ["connection_test", "metadata_read"],
    Provider.ITERABLE: ["connection_test", "draft_create", "metadata_read"],
    Provider.OPENAI: ["connection_test", "evaluation_judge", "inference"],
    Provider.GROQ: ["connection_test", "evaluation_judge", "inference"],
}


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


class IntegrationConnectionQuerySet(models.QuerySet):
    def bulk_create(self, objs: list[IntegrationConnection], **kwargs: Any) -> list[Any]:
        for connection in objs:
            connection.clean()
        return cast(list[Any], super().bulk_create(objs, **kwargs))

    def update(self, **kwargs: Any) -> int:
        if kwargs:
            raise ValidationError("Integration connections must be changed through save().")
        return 0

    def bulk_update(
        self,
        objs: list[IntegrationConnection],
        fields: list[str],
        **kwargs: Any,
    ) -> int:
        raise ValidationError("Integration connections must be changed through save().")


IntegrationConnectionManager = models.Manager.from_queryset(IntegrationConnectionQuerySet)


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
    objects = IntegrationConnectionManager()

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

    def save(self, *args: object, **kwargs: object) -> None:
        self.clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        configuration = self.configuration
        allowed_configuration = CONFIGURATION_BY_PROVIDER.get(self.provider)
        if not isinstance(configuration, dict) or allowed_configuration is None:
            raise ValidationError({"configuration": "Integration configuration is invalid."})
        if configuration and set(configuration) != set(allowed_configuration):
            raise ValidationError({"configuration": "Integration configuration is invalid."})
        if configuration:
            for key, allowed_values in allowed_configuration.items():
                if configuration.get(key) not in allowed_values:
                    raise ValidationError(
                        {"configuration": "Integration configuration is invalid."}
                    )
        capabilities = self.capabilities
        expected_capabilities = CAPABILITIES_BY_PROVIDER.get(self.provider)
        if not isinstance(capabilities, list) or expected_capabilities is None:
            raise ValidationError({"capabilities": "Integration capabilities are invalid."})
        if self.state == ConnectionState.NOT_CONFIGURED:
            if self.secret_id is not None or capabilities:
                raise ValidationError({"state": "Integration lifecycle is invalid."})
        elif self.state in {
            ConnectionState.CONFIGURED,
            ConnectionState.HEALTHY,
            ConnectionState.DEGRADED,
            ConnectionState.DISABLED,
        }:
            if self.secret_id is None or capabilities != expected_capabilities:
                raise ValidationError({"state": "Integration lifecycle is invalid."})
        else:
            raise ValidationError({"state": "Integration lifecycle is invalid."})
        if len(json.dumps(configuration, separators=(",", ":"))) > 256:
            raise ValidationError({"configuration": "Integration configuration is invalid."})
        if self.secret_id is not None and self.secret.provider != self.provider:
            raise ValidationError({"secret": "Integration secret provider is invalid."})


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


class DraftExecution(models.Model):
    """Human reviewed execution record, separate from the immutable broker intent."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        EXECUTING = "executing", "Executing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.OneToOneField("agents.DraftOperation", on_delete=models.PROTECT)
    run = models.ForeignKey("agents.AgentRun", on_delete=models.PROTECT)
    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="submitted_drafts", on_delete=models.PROTECT
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="approved_drafts",
        null=True,
        on_delete=models.PROTECT,
    )
    action = models.CharField(max_length=16)
    organization_id = models.CharField(max_length=40)
    event_id = models.CharField(max_length=40, blank=True)
    expected_readback_digest = models.CharField(max_length=64, blank=True)
    provider_configuration = models.JSONField(default=dict)
    payload = models.JSONField()
    request_digest = models.CharField(max_length=64)
    review_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    receipt = models.JSONField(null=True)
    provider_id = models.CharField(max_length=40, blank=True)
    error_category = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True)
    approval_session = models.ForeignKey(
        "identity.AdministratorSession", null=True, on_delete=models.PROTECT
    )
    claimed_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    status__in=(
                        "pending",
                        "approved",
                        "executing",
                        "succeeded",
                        "failed",
                        "unknown",
                    )
                ),
                name="integrations_draft_status",
            ),
            models.CheckConstraint(
                condition=Q(action__in=("create", "update")), name="integrations_draft_action"
            ),
            models.CheckConstraint(
                condition=Q(
                    status="pending",
                    approver__isnull=True,
                    approval_session__isnull=True,
                    approved_at__isnull=True,
                    claimed_at__isnull=True,
                )
                | (
                    ~Q(status="pending")
                    & Q(
                        approver__isnull=False,
                        approved_at__isnull=False,
                        approval_session__isnull=False,
                    )
                ),
                name="integrations_draft_approval_required",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=("pending", "approved"),
                        claimed_at__isnull=True,
                        completed_at__isnull=True,
                        receipt__isnull=True,
                        provider_id="",
                        error_category="",
                    )
                    | Q(
                        status="executing",
                        claimed_at__isnull=False,
                        completed_at__isnull=True,
                        receipt__isnull=True,
                        error_category="",
                    )
                    | Q(
                        status__in=("unknown", "failed"),
                        claimed_at__isnull=False,
                        completed_at__isnull=False,
                    )
                    | (
                        Q(
                            status="succeeded",
                            claimed_at__isnull=False,
                            completed_at__isnull=False,
                            receipt__isnull=False,
                        )
                        & ~Q(provider_id="")
                    )
                ),
                name="integrations_draft_execution_lifecycle",
            ),
        ]

    def __str__(self):
        return f"Draft execution {self.pk}: {self.status}"

    def save(self, *args, **kwargs):
        fields = (
            "intent_id",
            "run_id",
            "submitter_id",
            "action",
            "organization_id",
            "event_id",
            "expected_readback_digest",
            "payload",
            "provider_configuration",
            "request_digest",
            "review_digest",
        )
        old = (
            type(self)
            .objects.filter(pk=self.pk)
            .values(
                *fields,
                "approver_id",
                "approved_at",
                "approval_session_id",
                "status",
                "claimed_at",
                "completed_at",
            )
            .first()
        )
        if old and (
            any(old[f] != getattr(self, f) for f in fields)
            or (
                old["approver_id"]
                and (
                    old["approver_id"] != self.approver_id
                    or old["approved_at"] != self.approved_at
                    or old["approval_session_id"] != self.approval_session_id
                )
            )
        ):
            raise ValidationError("Draft execution review and approval are immutable.")
        if old:
            transitions = {
                "pending": {"pending", "approved"},
                "approved": {"approved", "executing"},
                "executing": {"executing", "unknown", "failed", "succeeded"},
                "unknown": {"unknown", "succeeded"},
                "failed": {"failed"},
                "succeeded": {"succeeded"},
            }
            if (
                self.status not in transitions[old["status"]]
                or (old["claimed_at"] is not None and self.claimed_at != old["claimed_at"])
                or (old["completed_at"] is not None and self.completed_at is None)
            ):
                raise ValidationError("Execution claim history cannot be reset.")
        super().save(*args, **kwargs)


class TemplateExecution(models.Model):
    """Human reviewed execution record, separate from the immutable broker intent."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        EXECUTING = "executing", "Executing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.OneToOneField("agents.DraftOperation", on_delete=models.PROTECT)
    run = models.ForeignKey("agents.AgentRun", on_delete=models.PROTECT)
    submitter = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="submitted_templates", on_delete=models.PROTECT
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="approved_templates",
        null=True,
        on_delete=models.PROTECT,
    )
    action = models.CharField(max_length=16)
    organization_id = models.CharField(max_length=40)
    event_id = models.CharField(max_length=40, blank=True)
    expected_readback_digest = models.CharField(max_length=64, blank=True)
    provider_configuration = models.JSONField(default=dict)
    payload = models.JSONField()
    request_digest = models.CharField(max_length=64)
    review_digest = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    receipt = models.JSONField(null=True)
    provider_id = models.CharField(max_length=40, blank=True)
    error_category = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True)
    approval_session = models.ForeignKey(
        "identity.AdministratorSession", null=True, on_delete=models.PROTECT
    )
    claimed_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    status__in=(
                        "pending",
                        "approved",
                        "executing",
                        "succeeded",
                        "failed",
                        "unknown",
                    )
                ),
                name="integrations_template_status",
            ),
            models.CheckConstraint(
                condition=Q(action__in=("create", "update")), name="integrations_template_action"
            ),
            models.CheckConstraint(
                condition=Q(
                    status="pending",
                    approver__isnull=True,
                    approval_session__isnull=True,
                    approved_at__isnull=True,
                    claimed_at__isnull=True,
                )
                | (
                    ~Q(status="pending")
                    & Q(
                        approver__isnull=False,
                        approved_at__isnull=False,
                        approval_session__isnull=False,
                    )
                ),
                name="integrations_template_approval_required",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status__in=("pending", "approved"),
                        claimed_at__isnull=True,
                        completed_at__isnull=True,
                        receipt__isnull=True,
                        provider_id="",
                        error_category="",
                    )
                    | Q(
                        status="executing",
                        claimed_at__isnull=False,
                        completed_at__isnull=True,
                        receipt__isnull=True,
                        error_category="",
                    )
                    | Q(
                        status__in=("unknown", "failed"),
                        claimed_at__isnull=False,
                        completed_at__isnull=False,
                    )
                    | (
                        Q(
                            status="succeeded",
                            claimed_at__isnull=False,
                            completed_at__isnull=False,
                            receipt__isnull=False,
                        )
                        & ~Q(provider_id="")
                    )
                ),
                name="integrations_template_execution_lifecycle",
            ),
        ]

    def __str__(self):
        return f"Template execution {self.pk}: {self.status}"

    def save(self, *args, **kwargs):
        fields = (
            "intent_id",
            "run_id",
            "submitter_id",
            "action",
            "organization_id",
            "event_id",
            "expected_readback_digest",
            "payload",
            "provider_configuration",
            "request_digest",
            "review_digest",
        )
        old = (
            type(self)
            .objects.filter(pk=self.pk)
            .values(
                *fields,
                "approver_id",
                "approved_at",
                "approval_session_id",
                "status",
                "claimed_at",
                "completed_at",
            )
            .first()
        )
        if old and (
            any(old[f] != getattr(self, f) for f in fields)
            or (
                old["approver_id"]
                and (
                    old["approver_id"] != self.approver_id
                    or old["approved_at"] != self.approved_at
                    or old["approval_session_id"] != self.approval_session_id
                )
            )
        ):
            raise ValidationError("Template execution review and approval are immutable.")
        if old:
            transitions = {
                "pending": {"pending", "approved"},
                "approved": {"approved", "executing"},
                "executing": {"executing", "unknown", "failed", "succeeded"},
                "unknown": {"unknown", "succeeded"},
                "failed": {"failed"},
                "succeeded": {"succeeded"},
            }
            if (
                self.status not in transitions[old["status"]]
                or (old["claimed_at"] is not None and self.claimed_at != old["claimed_at"])
                or (old["completed_at"] is not None and self.completed_at is None)
            ):
                raise ValidationError("Execution claim history cannot be reset.")
        super().save(*args, **kwargs)
