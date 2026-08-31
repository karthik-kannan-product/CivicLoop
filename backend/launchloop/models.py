import uuid

from django.contrib.auth.models import User
from django.db import models


class DemoActor(models.Model):
    class Role(models.TextChoices):
        OPERATOR = "operator", "Operator"
        APPROVER = "approver", "Approver"

    slug = models.SlugField(primary_key=True)
    display_name = models.CharField(max_length=120)
    role = models.CharField(max_length=20, choices=Role.choices)
    user = models.OneToOneField(
        User,
        related_name="launchloop_actor",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )

    def __str__(self) -> str:
        return self.display_name


class Event(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.title


class ProviderEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, default="eventbrite", editable=False)
    provider_event_id = models.CharField(max_length=64)
    current_snapshot = models.ForeignKey(
        "ProviderEventSnapshot",
        null=True,
        blank=True,
        related_name="current_for_sources",
        on_delete=models.PROTECT,
    )
    local_event = models.ForeignKey(
        "Event",
        null=True,
        blank=True,
        related_name="provider_sources",
        on_delete=models.PROTECT,
    )
    available = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "provider_event_id"),
                name="launchloop_unique_provider_event",
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.provider_event_id}"


class ProviderEventSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.ForeignKey(ProviderEvent, related_name="snapshots", on_delete=models.PROTECT)
    fingerprint = models.CharField(max_length=64)
    title = models.CharField(max_length=240)
    status = models.CharField(max_length=24)
    provider_changed_at = models.DateTimeField()
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    observed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("source", "fingerprint"),
                name="launchloop_unique_provider_snapshot",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source} at {self.provider_changed_at.isoformat()}"


class EventRevision(models.Model):
    event = models.ForeignKey(Event, related_name="revisions", on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    author = models.ForeignKey(DemoActor, on_delete=models.PROTECT)
    source_snapshot = models.ForeignKey(
        ProviderEventSnapshot,
        null=True,
        blank=True,
        related_name="event_revisions",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("event", "version"),
                name="launchloop_unique_event_revision",
            )
        ]

    def __str__(self) -> str:
        return f"{self.event.slug} v{self.version}"


class Workflow(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        NEEDS_INPUT = "needs_input", "Needs input"
        READY_FOR_REVIEW = "ready_for_review", "Ready for review"
        IN_REVIEW = "in_review", "In review"
        APPROVED = "approved", "Approved"
        COMPLETED = "completed", "Completed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField(Event, related_name="workflow", on_delete=models.CASCADE)
    revision = models.ForeignKey(EventRevision, on_delete=models.PROTECT)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DRAFT)
    package = models.JSONField(null=True, blank=True)
    package_hash = models.CharField(max_length=64, blank=True)
    telemetry_traceparent = models.CharField(max_length=55, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.event.slug}: {self.status}"


class WorkflowTransition(models.Model):
    workflow = models.ForeignKey(Workflow, related_name="transitions", on_delete=models.CASCADE)
    actor = models.ForeignKey(DemoActor, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=32)
    to_status = models.CharField(max_length=32)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.workflow_id}: {self.action}"


class ApprovalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workflow = models.OneToOneField(Workflow, related_name="approval", on_delete=models.CASCADE)
    submitter = models.ForeignKey(
        DemoActor,
        related_name="submitted_approvals",
        on_delete=models.PROTECT,
    )
    approver = models.ForeignKey(
        DemoActor,
        related_name="decided_approvals",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    package_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.workflow_id}: {self.status}"


class ConnectorExecution(models.Model):
    class Status(models.TextChoices):
        DELIVERED = "delivered", "Delivered"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    approval = models.OneToOneField(
        ApprovalRequest,
        related_name="execution",
        on_delete=models.CASCADE,
    )
    idempotency_key = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices)
    receipt = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.approval_id}: {self.status}"


class AuditEvent(models.Model):
    actor = models.ForeignKey(DemoActor, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=50)
    target_id = models.CharField(max_length=100)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.action}: {self.target_type}/{self.target_id}"
