import uuid

from django.contrib.auth.models import User
from django.db import models

AGENT_SPECIALISTS = (
    "event_readiness",
    "campaign_composer",
    "audience_policy",
)


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


class EventRevision(models.Model):
    event = models.ForeignKey(Event, related_name="revisions", on_delete=models.CASCADE)
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    author = models.ForeignKey(DemoActor, on_delete=models.PROTECT)
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


class AgentRun(models.Model):
    class Specialist(models.TextChoices):
        EVENT_READINESS = "event_readiness", "Event Readiness"
        CAMPAIGN_COMPOSER = "campaign_composer", "Campaign Composer"
        AUDIENCE_POLICY = "audience_policy", "Audience and Policy"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    workflow = models.ForeignKey(Workflow, related_name="agent_runs", on_delete=models.CASCADE)
    revision = models.ForeignKey(EventRevision, on_delete=models.PROTECT)
    specialist = models.CharField(max_length=32, choices=Specialist.choices)
    provider = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices)
    summary = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("workflow", "revision", "specialist"),
                name="launchloop_unique_agent_run_per_revision",
            ),
            models.CheckConstraint(
                condition=models.Q(specialist__in=AGENT_SPECIALISTS),
                name="launchloop_known_agent_specialist",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.workflow_id}: {self.specialist} ({self.status})"


class AgentActivity(models.Model):
    class Kind(models.TextChoices):
        QUEUED = "queued", "Queued"
        ANALYZING = "analyzing", "Analyzing"
        COMPLETED = "completed", "Completed"

    agent_run = models.ForeignKey(AgentRun, related_name="activity", on_delete=models.CASCADE)
    sequence = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("agent_run", "sequence"),
                name="launchloop_unique_agent_activity_sequence",
            )
        ]
        ordering = ("sequence", "id")

    def __str__(self) -> str:
        return f"{self.agent_run_id}: {self.kind}"


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
