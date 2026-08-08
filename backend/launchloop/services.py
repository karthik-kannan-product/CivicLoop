import hashlib
import json
from typing import Any
from uuid import UUID

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .engine import prepare_package
from .models import (
    ApprovalRequest,
    AuditEvent,
    ConnectorExecution,
    DemoActor,
    Event,
    EventRevision,
    Workflow,
    WorkflowTransition,
)

NEW_YORK_EVENT = {
    "title": "New York International Youth Day Networking Breakfast",
    "city": "New York",
    "region": "NY",
    "country": "US",
    "date": "2026-08-12",
    "start_time": "09:00",
    "end_time": "12:00",
    "timezone": "America/New_York",
    "venue_name": "",
    "venue_address": "",
    "access_instructions": "",
    "description": "A breakfast networking program for young nonprofit professionals.",
    "general_ticket_price": 40,
    "signup_url": "https://example.test/eventbrite/ny-youth-day",
    "sponsor_tier": "gold",
    "sponsor_discount_percent": 25,
}


class DemoError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


DEMO_PASSWORD = "civicloop-demo"
DEMO_USERS = {
    "maya.operator": {"display_name": "Maya Chen", "role": DemoActor.Role.OPERATOR},
    "jordan.approver": {"display_name": "Jordan Brooks", "role": DemoActor.Role.APPROVER},
}


def seed_demo_users() -> dict[str, User]:
    users: dict[str, User] = {}
    for username, details in DEMO_USERS.items():
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "first_name": details["display_name"].split()[0],
                "last_name": details["display_name"].split()[1],
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=("password",))
        users[username] = user
    return users


def package_hash(package: dict[str, Any]) -> str:
    encoded = json.dumps(package, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _audit(
    actor: DemoActor | None,
    action: str,
    target_type: str,
    target_id: object,
    details: dict[str, Any] | None = None,
) -> None:
    AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=details or {},
    )


def _transition(
    workflow: Workflow,
    actor: DemoActor,
    to_status: str,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    previous = workflow.status
    workflow.status = to_status
    workflow.save(update_fields=("status", "updated_at"))
    WorkflowTransition.objects.create(
        workflow=workflow,
        actor=actor,
        from_status=previous,
        to_status=to_status,
        action=action,
        details=details or {},
    )
    _audit(actor, action, "workflow", workflow.id, details)


@transaction.atomic
def reset_demo() -> Workflow:
    AuditEvent.objects.all().delete()
    ConnectorExecution.objects.all().delete()
    ApprovalRequest.objects.all().delete()
    WorkflowTransition.objects.all().delete()
    Workflow.objects.all().delete()
    EventRevision.objects.all().delete()
    Event.objects.all().delete()
    DemoActor.objects.all().delete()
    users = seed_demo_users()

    operator = DemoActor.objects.create(
        slug="maya",
        display_name="Maya Chen",
        role=DemoActor.Role.OPERATOR,
        user=users["maya.operator"],
    )
    DemoActor.objects.create(
        slug="jordan",
        display_name="Jordan Brooks",
        role=DemoActor.Role.APPROVER,
        user=users["jordan.approver"],
    )
    event = Event.objects.create(
        slug="ny-youth-day",
        title=NEW_YORK_EVENT["title"],
    )
    revision = EventRevision.objects.create(
        event=event,
        version=1,
        snapshot=NEW_YORK_EVENT,
        author=operator,
    )
    workflow = Workflow.objects.create(event=event, revision=revision)
    WorkflowTransition.objects.create(
        workflow=workflow,
        actor=operator,
        from_status="",
        to_status=Workflow.Status.DRAFT,
        action="demo_reset",
        details={"revision": 1},
    )
    _audit(operator, "demo_reset", "workflow", workflow.id, {"revision": 1})
    return workflow


def current_workflow() -> Workflow:
    workflow = Workflow.objects.select_related("event", "revision", "revision__author").first()
    if workflow is None:
        workflow = reset_demo()
    return workflow


def actor_for_user(user: User) -> DemoActor:
    try:
        return DemoActor.objects.get(user=user)
    except DemoActor.DoesNotExist:
        raise DemoError(
            "demo_role_not_found",
            "This account is not assigned to the seeded demo workspace.",
            403,
        ) from None


def workflow_for(workflow_id: UUID) -> Workflow:
    try:
        return Workflow.objects.select_related("event", "revision").get(id=workflow_id)
    except Workflow.DoesNotExist:
        raise DemoError("workflow_not_found", "The workflow does not exist.", 404) from None


@transaction.atomic
def run_workflow(workflow_id: UUID, actor: DemoActor) -> Workflow:
    workflow = Workflow.objects.select_for_update().select_related("revision").get(id=workflow_id)
    if actor.role != DemoActor.Role.OPERATOR:
        raise DemoError("operator_required", "Only the operator can run LaunchLoop.", 403)
    if workflow.status != Workflow.Status.DRAFT:
        raise DemoError("invalid_workflow_state", "LaunchLoop can only run from Draft.", 409)

    package = prepare_package(workflow.revision.snapshot)
    workflow.package = package
    workflow.package_hash = package_hash(package)
    workflow.save(update_fields=("package", "package_hash", "updated_at"))
    destination = (
        Workflow.Status.READY_FOR_REVIEW
        if package["status"] == "ready_for_review"
        else Workflow.Status.NEEDS_INPUT
    )
    _transition(
        workflow,
        actor,
        destination,
        "launchloop_ran",
        {
            "revision": workflow.revision.version,
            "missing_fields": package["missing_fields"],
        },
    )
    return workflow


@transaction.atomic
def answer_questions(
    workflow_id: UUID,
    actor: DemoActor,
    answers: dict[str, Any],
) -> Workflow:
    workflow = (
        Workflow.objects.select_for_update()
        .select_related("revision", "event")
        .get(id=workflow_id)
    )
    if actor.role != DemoActor.Role.OPERATOR:
        raise DemoError("operator_required", "Only the operator can resolve event facts.", 403)
    if workflow.status != Workflow.Status.NEEDS_INPUT:
        raise DemoError("invalid_workflow_state", "This workflow is not waiting for input.", 409)

    required_answers = ("venue_name", "venue_address", "access_instructions")
    answered_fields = {
        field: str(answers.get(field, "")).strip()
        for field in required_answers
        if str(answers.get(field, "")).strip()
    }
    if not answered_fields:
        raise DemoError(
            "answers_incomplete",
            "Save at least one event fact before continuing.",
        )

    snapshot = dict(workflow.revision.snapshot)
    snapshot.update(answered_fields)
    missing_answers = [field for field in required_answers if not str(snapshot.get(field, "")).strip()]
    revision = EventRevision.objects.create(
        event=workflow.event,
        version=workflow.revision.version + 1,
        snapshot=snapshot,
        author=actor,
    )
    workflow.revision = revision
    workflow.package = None
    workflow.package_hash = ""
    workflow.save(update_fields=("revision", "package", "package_hash", "updated_at"))
    ApprovalRequest.objects.filter(workflow=workflow).delete()
    destination = Workflow.Status.NEEDS_INPUT if missing_answers else Workflow.Status.DRAFT
    _transition(
        workflow,
        actor,
        destination,
        "event_facts_saved" if missing_answers else "event_facts_resolved",
        {
            "revision": revision.version,
            "fields": list(answered_fields),
            "missing_fields": missing_answers,
        },
    )
    return workflow


@transaction.atomic
def submit_workflow(workflow_id: UUID, actor: DemoActor) -> ApprovalRequest:
    workflow = Workflow.objects.select_for_update().get(id=workflow_id)
    if actor.role != DemoActor.Role.OPERATOR:
        raise DemoError("operator_required", "Only the operator can submit this package.", 403)
    if workflow.status != Workflow.Status.READY_FOR_REVIEW or not workflow.package_hash:
        raise DemoError(
            "invalid_workflow_state",
            "Only a review-ready package can be submitted.",
            409,
        )

    approval = ApprovalRequest.objects.create(
        workflow=workflow,
        submitter=actor,
        package_hash=workflow.package_hash,
    )
    _transition(
        workflow,
        actor,
        Workflow.Status.IN_REVIEW,
        "package_submitted",
        {"package_hash": workflow.package_hash},
    )
    return approval


@transaction.atomic
def decide_approval(
    approval_id: UUID,
    actor: DemoActor,
    decision: str,
    submitted_hash: str,
    reason: str = "",
) -> ApprovalRequest:
    try:
        approval = (
            ApprovalRequest.objects.select_for_update()
            .select_related("workflow", "submitter")
            .get(id=approval_id)
        )
    except ApprovalRequest.DoesNotExist:
        raise DemoError("approval_not_found", "The approval request does not exist.", 404) from None

    if actor.pk == approval.submitter_id:
        raise DemoError(
            "self_approval_forbidden",
            "The package submitter cannot approve their own work.",
            403,
        )
    if actor.role != DemoActor.Role.APPROVER:
        raise DemoError("approver_required", "An approver persona is required.", 403)
    if approval.status != ApprovalRequest.Status.PENDING:
        raise DemoError("approval_already_decided", "This request is already decided.", 409)
    if submitted_hash != approval.package_hash:
        raise DemoError(
            "package_hash_mismatch",
            "The package changed after review. Reload before deciding.",
            409,
        )
    if decision not in {"approve", "reject"}:
        raise DemoError("invalid_decision", "Decision must be approve or reject.")

    approval.approver = actor
    approval.reason = reason
    approval.decided_at = timezone.now()
    workflow = approval.workflow
    if decision == "reject":
        approval.status = ApprovalRequest.Status.REJECTED
        approval.save()
        workflow.package = None
        workflow.package_hash = ""
        workflow.save(update_fields=("package", "package_hash", "updated_at"))
        _transition(
            workflow,
            actor,
            Workflow.Status.NEEDS_INPUT,
            "package_rejected",
            {"reason": reason},
        )
        return approval

    approval.status = ApprovalRequest.Status.APPROVED
    approval.save()
    _transition(
        workflow,
        actor,
        Workflow.Status.APPROVED,
        "package_approved",
        {"package_hash": approval.package_hash},
    )
    audience_count = int((workflow.package or {}).get("audience", {}).get("member_count", 0))
    receipt = {
        "connector": "sandbox_iterable",
        "campaign": "New York Youth Day invitation and reminder",
        "audience_count": audience_count,
        "mode": "simulation",
        "external_actions": 0,
        "message": "Sandbox delivery recorded. No email or social post was sent.",
    }
    ConnectorExecution.objects.create(
        approval=approval,
        idempotency_key=f"launchloop:{approval.package_hash}",
        status=ConnectorExecution.Status.DELIVERED,
        receipt=receipt,
    )
    _transition(
        workflow,
        actor,
        Workflow.Status.COMPLETED,
        "sandbox_receipt_recorded",
        {"connector": "sandbox_iterable", "audience_count": audience_count},
    )
    return approval


def serialize_demo(workflow: Workflow | None = None) -> dict[str, Any]:
    workflow = workflow or current_workflow()
    workflow.refresh_from_db()
    revision = workflow.revision
    actors = [
        {"slug": actor.slug, "display_name": actor.display_name, "role": actor.role}
        for actor in DemoActor.objects.order_by("role", "slug")
    ]
    approval = ApprovalRequest.objects.filter(workflow=workflow).select_related(
        "submitter", "approver"
    ).first()
    execution = (
        ConnectorExecution.objects.filter(approval=approval).first() if approval else None
    )
    transitions = workflow.transitions.select_related("actor").order_by("created_at", "id")
    return {
        "deployment_mode": "server",
        "actors": actors,
        "event": {
            "id": workflow.event.slug,
            "title": workflow.event.title,
            "revision": {
                "id": revision.id,
                "version": revision.version,
                "facts": revision.snapshot,
                "author": revision.author_id,
            },
        },
        "workflow": {
            "id": str(workflow.id),
            "status": workflow.status,
            "package": workflow.package,
            "package_hash": workflow.package_hash or None,
        },
        "approval": (
            {
                "id": str(approval.id),
                "status": approval.status,
                "package_hash": approval.package_hash,
                "submitter": approval.submitter_id,
                "approver": approval.approver_id,
                "reason": approval.reason,
            }
            if approval
            else None
        ),
        "execution": (
            {
                "id": str(execution.id),
                "status": execution.status,
                "receipt": execution.receipt,
            }
            if execution
            else None
        ),
        "timeline": [
            {
                "id": transition.id,
                "actor": transition.actor.display_name,
                "action": transition.action,
                "from_status": transition.from_status,
                "to_status": transition.to_status,
                "details": transition.details,
                "created_at": transition.created_at.isoformat(),
            }
            for transition in transitions
        ],
    }
