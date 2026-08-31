import hashlib
import json
from typing import Any
from uuid import UUID

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from observability.launchloop import workflow_operation, workflow_stage
from openinference.semconv.trace import OpenInferenceSpanKindValues
from opentelemetry.trace import Status, StatusCode

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
    "synthetic": True,
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
            user.set_password(settings.DEMO_PASSWORD)
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
    demo_event_ids = list(
        EventRevision.objects.filter(
            author__slug__in=("maya", "jordan"), source_snapshot__isnull=True
        ).values_list("event_id", flat=True)
    )
    demo_workflows = Workflow.objects.filter(event_id__in=demo_event_ids)
    ApprovalRequest.objects.filter(workflow__in=demo_workflows).delete()
    WorkflowTransition.objects.filter(workflow__in=demo_workflows).delete()
    demo_workflows.delete()
    EventRevision.objects.filter(event_id__in=demo_event_ids).delete()
    Event.objects.filter(id__in=demo_event_ids).delete()
    users = seed_demo_users()

    operator, _ = DemoActor.objects.update_or_create(
        slug="maya",
        defaults={
            "display_name": "Maya Chen",
            "role": DemoActor.Role.OPERATOR,
            "user": users["maya.operator"],
        },
    )
    DemoActor.objects.update_or_create(
        slug="jordan",
        defaults={
            "display_name": "Jordan Brooks",
            "role": DemoActor.Role.APPROVER,
            "user": users["jordan.approver"],
        },
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

    with workflow_operation(workflow, "run"):
        with workflow_stage(
            workflow,
            "launchloop.deterministic_lane",
            OpenInferenceSpanKindValues.CHAIN,
        ):
            package = prepare_package(workflow.revision.snapshot)
        workflow.package = package
        workflow.package_hash = package_hash(package)
        workflow.save(update_fields=("package", "package_hash", "updated_at"))
        with workflow_stage(
            workflow,
            "launchloop.policy",
            OpenInferenceSpanKindValues.GUARDRAIL,
        ) as policy_span:
            destination = (
                Workflow.Status.READY_FOR_REVIEW
                if package["status"] == "ready_for_review"
                else Workflow.Status.NEEDS_INPUT
            )
            policy_span.set_attribute("civicloop.outcome", destination)
        with workflow_stage(
            workflow,
            "launchloop.evaluation",
            OpenInferenceSpanKindValues.EVALUATOR,
        ) as evaluation_span:
            evaluation_span.set_attribute("civicloop.outcome", "passed")
            evaluation_span.set_status(Status(StatusCode.OK))
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
        Workflow.objects.select_for_update().select_related("revision", "event").get(id=workflow_id)
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

    with workflow_operation(workflow, "answer_questions"):
        snapshot = dict(workflow.revision.snapshot)
        snapshot.update(answered_fields)
        missing_answers = [
            field for field in required_answers if not str(snapshot.get(field, "")).strip()
        ]
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

    with workflow_operation(workflow, "submit"):
        with workflow_stage(
            workflow,
            "launchloop.approval",
            OpenInferenceSpanKindValues.CHAIN,
        ) as approval_span:
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
            approval_span.set_attribute("civicloop.approval_state", "pending")
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
    with workflow_operation(workflow, "approval_decision"):
        with workflow_stage(
            workflow,
            "launchloop.approval",
            OpenInferenceSpanKindValues.CHAIN,
        ) as approval_span:
            approval_span.set_attribute("civicloop.approval_state", decision)
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
        with workflow_stage(
            workflow,
            "launchloop.sandbox_connector",
            OpenInferenceSpanKindValues.TOOL,
        ) as connector_span:
            ConnectorExecution.objects.create(
                approval=approval,
                idempotency_key=f"launchloop:{approval.package_hash}",
                status=ConnectorExecution.Status.DELIVERED,
                receipt=receipt,
            )
            connector_span.set_attribute("civicloop.connector_category", "sandbox_iterable")
            connector_span.set_attribute("civicloop.outcome", "delivered")
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
    approval = (
        ApprovalRequest.objects.filter(workflow=workflow)
        .select_related("submitter", "approver")
        .first()
    )
    execution = ConnectorExecution.objects.filter(approval=approval).first() if approval else None
    latest_run = (
        workflow.agent_runs.select_related("model_profile")
        .filter(package_hash=workflow.package_hash)
        .order_by("-created_at")
        .first()
        if workflow.package_hash
        else None
    )
    evaluation = None
    if latest_run is not None:
        result = latest_run.evaluation_results.order_by("-created_at").first()
        if latest_run.status in {"queued", "running"}:
            evaluation_state = "pending"
        elif latest_run.status == "succeeded" and result is not None:
            evaluation_state = "passed" if result.outcome == "passed" else "failed"
        elif latest_run.failure_category == "budget_exhausted":
            evaluation_state = "denied"
        elif latest_run.failure_category in {
            "dependency_unavailable",
            "provider_unavailable",
            "timeout",
        }:
            evaluation_state = "unavailable"
        else:
            evaluation_state = "failed"
        evaluation = {
            "state": evaluation_state,
            "run_id": str(latest_run.id),
            "trace_id": latest_run.trace_id,
            "rubric_id": result.rubric_id if result else "launchloop_package_quality",
            "rubric_version": result.rubric_version if result else 1,
            "risk_labels": result.reason_codes if result else [],
            "summary": result.summary if result else "",
            "provider": latest_run.model_profile.provider,
            "model": latest_run.model_profile.model,
            "input_tokens": latest_run.input_tokens,
            "output_tokens": latest_run.output_tokens,
            "cost_microusd": latest_run.cost_microusd,
            "failure_category": latest_run.failure_category or None,
            "advisory_only": True,
        }
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
        "evaluation": evaluation,
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
