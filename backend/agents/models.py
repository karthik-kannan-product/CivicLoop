import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from agents.redaction import validate_safe_summary


class ImmutableVersionedModel(models.Model):
    immutable_fields: tuple[str, ...] = ()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            persisted = type(self).objects.filter(pk=self.pk).values(*self.immutable_fields).first()
            if persisted is not None and any(
                persisted[field] != getattr(self, field) for field in self.immutable_fields
            ):
                raise ValueError(
                    f"{type(self).__name__} records are immutable; create a new revision."
                )
        super().save(*args, **kwargs)


class ModelProfile(ImmutableVersionedModel):
    class Provider(models.TextChoices):
        HERMES = "hermes", "Hermes"
        GROQ = "groq", "Groq"
        OPENAI = "openai", "OpenAI"

    class Purpose(models.TextChoices):
        WORKFLOW = "workflow", "Workflow"
        EVALUATION_JUDGE = "evaluation_judge", "Evaluation judge"
        FALLBACK = "fallback", "Fallback"

    profile_id = models.SlugField(max_length=64)
    revision = models.PositiveIntegerField()
    provider = models.CharField(max_length=16, choices=Provider.choices)
    model = models.CharField(max_length=120)
    purpose = models.CharField(max_length=24, choices=Purpose.choices)
    max_input_tokens = models.PositiveIntegerField()
    max_output_tokens = models.PositiveIntegerField()
    temperature = models.DecimalField(max_digits=3, decimal_places=2)
    input_price_microusd_per_million = models.PositiveBigIntegerField(null=True)
    output_price_microusd_per_million = models.PositiveBigIntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    immutable_fields = (
        "profile_id",
        "revision",
        "provider",
        "model",
        "purpose",
        "max_input_tokens",
        "max_output_tokens",
        "temperature",
        "input_price_microusd_per_million",
        "output_price_microusd_per_million",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("profile_id", "revision"), name="agents_unique_model_profile_revision"
            ),
            models.CheckConstraint(
                condition=Q(max_input_tokens__gt=0), name="agents_profile_input_tokens_positive"
            ),
            models.CheckConstraint(
                condition=Q(max_output_tokens__gt=0), name="agents_profile_output_tokens_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile_id} r{self.revision}"


class RoutingPolicy(ImmutableVersionedModel):
    policy_id = models.SlugField(max_length=64)
    revision = models.PositiveIntegerField()
    purpose = models.CharField(max_length=24, choices=ModelProfile.Purpose.choices)
    model_profile = models.ForeignKey(ModelProfile, on_delete=models.PROTECT)
    per_run_limit_microusd = models.PositiveBigIntegerField(default=500_000)
    monthly_limit_microusd = models.PositiveBigIntegerField(default=25_000_000)
    created_at = models.DateTimeField(auto_now_add=True)

    immutable_fields = (
        "policy_id",
        "revision",
        "purpose",
        "model_profile_id",
        "per_run_limit_microusd",
        "monthly_limit_microusd",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("policy_id", "revision"), name="agents_unique_routing_policy_revision"
            ),
            models.UniqueConstraint(
                fields=("model_profile",), name="agents_one_policy_per_profile_revision"
            ),
            models.CheckConstraint(
                condition=Q(per_run_limit_microusd__gt=0), name="agents_policy_run_limit_positive"
            ),
            models.CheckConstraint(
                condition=Q(monthly_limit_microusd__gt=0), name="agents_policy_month_limit_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.policy_id} r{self.revision}"


class BudgetPeriod(models.Model):
    month = models.DateField(unique=True)
    limit_microusd = models.PositiveBigIntegerField()
    reserved_microusd = models.PositiveBigIntegerField(default=0)
    settled_microusd = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(reserved_microusd__gte=0), name="agents_period_reserved_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(settled_microusd__gte=0), name="agents_period_settled_nonnegative"
            ),
        ]

    def __str__(self) -> str:
        return self.month.isoformat()


class BudgetReservation(models.Model):
    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        SETTLED = "settled", "Settled"
        RELEASED = "released", "Released"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_id = models.UUIDField(unique=True)
    model_profile = models.ForeignKey(ModelProfile, on_delete=models.PROTECT)
    routing_policy = models.ForeignKey(RoutingPolicy, on_delete=models.PROTECT)
    period = models.ForeignKey(BudgetPeriod, on_delete=models.PROTECT)
    estimated_input_tokens = models.PositiveIntegerField()
    estimated_output_tokens = models.PositiveIntegerField()
    reserved_cost_microusd = models.PositiveBigIntegerField()
    settled_input_tokens = models.PositiveIntegerField(null=True)
    settled_output_tokens = models.PositiveIntegerField(null=True)
    settled_cost_microusd = models.PositiveBigIntegerField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RESERVED)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(reserved_cost_microusd__gte=0),
                name="agents_reservation_cost_nonnegative",
            )
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.status}"


class BudgetLedgerRecord(models.Model):
    class EntryType(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        SETTLED = "settled", "Settled"
        RELEASED = "released", "Released"
        ADJUSTMENT = "adjustment", "Adjustment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run_id = models.UUIDField()
    reservation = models.ForeignKey(
        BudgetReservation, related_name="ledger_records", on_delete=models.PROTECT
    )
    model_profile_id_snapshot = models.SlugField(max_length=64)
    model_profile_revision = models.PositiveIntegerField()
    entry_type = models.CharField(max_length=16, choices=EntryType.choices)
    currency = models.CharField(max_length=3, default="USD")
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_microusd = models.PositiveBigIntegerField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run_id", "entry_type"), name="agents_unique_run_ledger_entry_type"
            ),
            models.CheckConstraint(
                condition=Q(cost_microusd__gte=0), name="agents_ledger_cost_nonnegative"
            ),
            models.CheckConstraint(condition=Q(currency="USD"), name="agents_ledger_currency_usd"),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: {self.entry_type}"


class AgentRun(models.Model):
    class PrivacyMode(models.TextChoices):
        SYNTHETIC_FULL = "synthetic_full", "Synthetic full"
        PILOT_MINIMIZED = "pilot_minimized", "Pilot minimized"
        DISABLED = "disabled", "Disabled"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class FailureCategory(models.TextChoices):
        BUDGET_EXHAUSTED = "budget_exhausted", "Budget exhausted"
        CANCELLED = "cancelled", "Cancelled"
        DEPENDENCY_UNAVAILABLE = "dependency_unavailable", "Dependency unavailable"
        INVALID_OUTPUT = "invalid_output", "Invalid output"
        PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"
        TIMEOUT = "timeout", "Timeout"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.ForeignKey(
        "launchloop.Workflow", related_name="agent_runs", on_delete=models.PROTECT
    )
    event_revision = models.ForeignKey("launchloop.EventRevision", on_delete=models.PROTECT)
    package_hash = models.CharField(max_length=64)
    routing_policy = models.ForeignKey(RoutingPolicy, on_delete=models.PROTECT)
    model_profile = models.ForeignKey(ModelProfile, on_delete=models.PROTECT)
    fixture_manifest_id = models.SlugField(max_length=64)
    fixture_manifest_revision = models.PositiveIntegerField()
    fixture_manifest_digest = models.CharField(max_length=64)
    privacy_mode = models.CharField(max_length=24, choices=PrivacyMode.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    attempt = models.PositiveSmallIntegerField(default=1)
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    failure_category = models.CharField(max_length=32, choices=FailureCategory.choices, blank=True)
    trace_id = models.CharField(max_length=32)
    span_count = models.PositiveSmallIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cost_microusd = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    immutable_binding_fields = (
        "workflow_id",
        "event_revision_id",
        "package_hash",
        "routing_policy_id",
        "model_profile_id",
        "fixture_manifest_id",
        "fixture_manifest_revision",
        "fixture_manifest_digest",
        "privacy_mode",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(attempt__gte=1) & Q(attempt__lte=10), name="agents_run_attempt_range"
            ),
            models.CheckConstraint(
                condition=Q(span_count__lte=1000), name="agents_run_span_count_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.id}: {self.status}"

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            persisted = (
                type(self).objects.filter(pk=self.pk).values(*self.immutable_binding_fields).first()
            )
            if persisted is not None and any(
                persisted[field] != getattr(self, field) for field in self.immutable_binding_fields
            ):
                raise ValueError("AgentRun binding is immutable after creation.")
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        if self.event_revision_id and self.workflow_id:
            if self.event_revision.event_id != self.workflow.event_id:
                raise ValidationError("Agent run revision must belong to its workflow event.")
        if self.model_profile_id and self.routing_policy_id:
            if self.routing_policy.model_profile_id != self.model_profile_id:
                raise ValidationError("Agent run profile must match its routing policy.")
        lifecycle_errors: dict[str, str] = {}
        if self.status == self.Status.QUEUED:
            if self.started_at is not None:
                lifecycle_errors["started_at"] = "Queued runs cannot have a start time."
            if self.finished_at is not None:
                lifecycle_errors["finished_at"] = "Queued runs cannot have a finish time."
        elif self.status == self.Status.RUNNING:
            if self.started_at is None:
                lifecycle_errors["started_at"] = "Running runs require a start time."
            if self.finished_at is not None:
                lifecycle_errors["finished_at"] = "Running runs cannot have a finish time."
        elif self.status in {self.Status.SUCCEEDED, self.Status.FAILED}:
            if self.started_at is None:
                lifecycle_errors["started_at"] = "Finished runs require a start time."
            if self.finished_at is None:
                lifecycle_errors["finished_at"] = "Finished runs require a finish time."
        elif self.status == self.Status.CANCELLED and self.finished_at is None:
            lifecycle_errors["finished_at"] = "Cancelled runs require a finish time."
        if self.status == self.Status.FAILED and not self.failure_category:
            lifecycle_errors["failure_category"] = "Failed runs require a failure category."
        if self.status == self.Status.CANCELLED and self.failure_category != "cancelled":
            lifecycle_errors["failure_category"] = "Cancelled runs require cancelled category."
        if self.status not in {self.Status.FAILED, self.Status.CANCELLED} and self.failure_category:
            lifecycle_errors["failure_category"] = "Non-failed runs cannot have a failure category."
        if lifecycle_errors:
            raise ValidationError(lifecycle_errors)


class AgentStep(models.Model):
    class Kind(models.TextChoices):
        PLANNING = "planning", "Planning"
        MODEL_INFERENCE = "model_inference", "Model inference"
        TOOL_CALL = "tool_call", "Tool call"
        VALIDATION = "validation", "Validation"
        REPAIR = "repair", "Repair"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class FailureCategory(models.TextChoices):
        INVALID_OUTPUT = "invalid_output", "Invalid output"
        PROVIDER_UNAVAILABLE = "provider_unavailable", "Provider unavailable"
        TIMEOUT = "timeout", "Timeout"
        TOOL_DENIED = "tool_denied", "Tool denied"
        TOOL_ERROR = "tool_error", "Tool error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(AgentRun, related_name="steps", on_delete=models.PROTECT)
    sequence = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=24, choices=Kind.choices)
    status = models.CharField(max_length=16, choices=Status.choices)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    input_summary = models.CharField(max_length=500, blank=True)
    output_summary = models.CharField(max_length=500, blank=True)
    failure_category = models.CharField(max_length=32, choices=FailureCategory.choices, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sequence",)
        constraints = [
            models.UniqueConstraint(
                fields=("run", "sequence"), name="agents_unique_run_step_sequence"
            ),
            models.CheckConstraint(
                condition=Q(sequence__gte=1) & Q(sequence__lte=1000),
                name="agents_step_sequence_range",
            ),
            models.CheckConstraint(
                condition=Q(duration_ms__lte=3_600_000), name="agents_step_duration_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id}: step {self.sequence}"

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        validate_safe_summary(self.input_summary)
        validate_safe_summary(self.output_summary)
