"""Trusted, bounded worker orchestration. Untrusted output never conveys execution authority."""

import hashlib
import uuid
from datetime import timedelta
from uuid import UUID

from celery import shared_task
from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Max
from django.utils import timezone
from launchloop.engine import prepare_package
from launchloop.models import AuditEvent, DemoActor, Workflow
from launchloop.services import package_hash

from agents.budgets import (
    BudgetError,
    _cost,
    charge_reserved_budget,
    release_budget,
    reserve_budget,
    settle_budget,
)
from agents.capabilities import (
    TOOLS,
    issue_workflow_capability,
    revoke_workflow_capability,
    token_digest,
)
from agents.models import (
    AgentRun,
    AgentRunControl,
    AgentRunEvent,
    DraftOperation,
    HermesAdmissionLane,
    HermesRunBinding,
    MCPSubmission,
    ModelProfile,
    RoutingPolicy,
    WorkflowCapability,
)
from agents.tool_schemas import PROPOSAL, bounded_json, validate

PROPOSAL_SCHEMA_ID = "urn:civicloop:schema:campaign-proposal:v1.0"
ACTIVE = (AgentRun.Status.QUEUED, AgentRun.Status.RUNNING)
# These are the frozen synthetic-corpus identities, not caller-supplied metadata.
FIXTURE_ID = "launchloop_synthetic_v1"
FIXTURE_REVISION = 3
FIXTURE_DIGEST = "815d14762306d96bdc6449eb58a3e5739fb0ad95e08dc70b9025bd2cc8099d5f"


class HermesAdmissionDenied(Exception):
    def __init__(self):
        super().__init__("Hermes admission unavailable.")


class WorkerFailure(Exception):
    def __init__(self, category, *, quarantine=False):
        self.category = category
        self.quarantine = quarantine
        super().__init__("Hermes run failed.")


def _digest(value):
    return hashlib.sha256(bounded_json(value).encode()).hexdigest()


def _lane():
    # The migration seeds this row. Recreate after a test database flush; uniqueness
    # makes concurrent bootstrap fail closed on databases lacking row locks.
    HermesAdmissionLane.objects.get_or_create(pk=1)
    return HermesAdmissionLane.objects.select_for_update().get(pk=1)


def _owns_run(run, lane):
    if not run.hermes_lane:
        return False
    if (
        HermesRunBinding.objects.filter(run=run).exists()
        and AgentRunControl.objects.filter(run=run).exists()
    ):
        return True
    # Missing authority/control cannot establish cleanup or permit a new admission.
    lane.admission_disabled = True
    lane.save(update_fields=["admission_disabled", "updated_at"])
    return False


def _operator(actor):
    return (
        actor.user_id is not None and actor.user.is_active and actor.role == DemoActor.Role.OPERATOR
    )


def _ready(workflow, revision_id, expected_hash=None):
    try:
        return (
            workflow.revision_id == revision_id
            and workflow.revision.event_id == workflow.event_id
            and workflow.revision.snapshot.get("synthetic") is True
            and workflow.status in (Workflow.Status.READY_FOR_REVIEW, Workflow.Status.IN_REVIEW)
            and workflow.package.get("status") == "ready_for_review"
            and prepare_package(workflow.revision.snapshot) == workflow.package
            and package_hash(workflow.package) == workflow.package_hash
            and (expected_hash is None or expected_hash == workflow.package_hash)
        )
    except AttributeError, KeyError, TypeError, ValueError:
        return False


def _event(run, event_type, outcome):
    sequence = (run.events.aggregate(value=Max("sequence"))["value"] or 0) + 1
    AgentRunEvent.objects.create(
        run=run,
        sequence=sequence,
        event_type=event_type,
        outcome=outcome,
        detail_digest=_digest({"event_type": event_type, "outcome": outcome}),
    )
    AuditEvent.objects.create(
        actor=run.hermes_binding.actor,
        action="hermes." + event_type,
        target_type="agent_run",
        target_id=str(run.id),
        details={"outcome": outcome, "sequence": sequence},
    )


def _timeout():
    timeout = getattr(settings, "CIVICLOOP_HERMES_TIMEOUT_SECONDS", 120)
    inferences = getattr(settings, "CIVICLOOP_HERMES_MAX_INFERENCES", 8)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise HermesAdmissionDenied()
    if type(inferences) is not int or not 1 <= inferences <= 8:
        raise HermesAdmissionDenied()
    return timeout


def queue_hermes_run(*, workflow_id: UUID, revision_id: int, actor_slug: str) -> AgentRun:
    try:
        with transaction.atomic():
            lane = _lane()
            if (
                not settings.CIVICLOOP_HERMES_ENABLED
                or lane.admission_disabled
                or AgentRunControl.objects.filter(admission_disabled=True).exists()
                or lane.active_run_id is not None
                or HermesRunBinding.objects.filter(run__status__in=ACTIVE).exists()
            ):
                raise HermesAdmissionDenied()
            timeout = _timeout()
            workflow = (
                Workflow.objects.select_for_update().select_related("revision").get(pk=workflow_id)
            )
            actor = DemoActor.objects.select_related("user").get(pk=actor_slug)
            if not _operator(actor) or not _ready(workflow, revision_id):
                raise HermesAdmissionDenied()
            profile = ModelProfile.objects.get(
                profile_id=settings.CIVICLOOP_HERMES_PROFILE_ID,
                revision=settings.CIVICLOOP_HERMES_PROFILE_REVISION,
                purpose="workflow",
            )
            policy = RoutingPolicy.objects.get(model_profile=profile, purpose="workflow")
            correlation_id = uuid.uuid4()
            run_id = uuid.uuid5(uuid.NAMESPACE_URL, "urn:civicloop:run:" + str(correlation_id))
            lease = timezone.now() + timedelta(seconds=timeout)
            reserve_budget(
                run_id=run_id,
                profile_id=profile.profile_id,
                profile_revision=profile.revision,
                estimated_input_tokens=profile.max_input_tokens,
                estimated_output_tokens=profile.max_output_tokens,
                expires_at=lease,
            )
            run = AgentRun.objects.create(
                id=run_id,
                hermes_lane=True,
                workflow=workflow,
                event_revision=workflow.revision,
                package_hash=workflow.package_hash,
                model_profile=profile,
                routing_policy=policy,
                fixture_manifest_id=FIXTURE_ID,
                fixture_manifest_revision=FIXTURE_REVISION,
                fixture_manifest_digest=FIXTURE_DIGEST,
                privacy_mode="synthetic_full",
                status="queued",
                trace_id=uuid.uuid4().hex,
            )
            HermesRunBinding.objects.create(run=run, actor=actor, correlation_id=correlation_id)
            AgentRunControl.objects.create(run=run, lease_expires_at=lease)
            lane.active_run = run
            lane.save(update_fields=["active_run", "updated_at"])
            _event(run, "queued", "accepted")
            transaction.on_commit(lambda: _dispatch(run.id))
            return run
    except (
        Workflow.DoesNotExist,
        DemoActor.DoesNotExist,
        ModelProfile.DoesNotExist,
        RoutingPolicy.DoesNotExist,
        BudgetError,
        DatabaseError,
        AttributeError,
        ValueError,
        TypeError,
    ):
        raise HermesAdmissionDenied() from None


def _dispatch(run_id):
    try:
        execute_hermes_run.delay(str(run_id))
    except Exception:
        # No worker owns this queued run yet; release admission and its unspent budget.
        _finish(run_id, "dependency_unavailable", billable=False, cleanup_ok=True)


def _check(run, control, workflow, capability=None):
    if control.cancel_requested_at or not settings.CIVICLOOP_HERMES_ENABLED:
        raise WorkerFailure("cancelled")
    if control.lease_expires_at is None or timezone.now() >= control.lease_expires_at:
        raise WorkerFailure("timeout")
    if not _operator(run.hermes_binding.actor):
        raise WorkerFailure("invalid_output")
    if (
        not _ready(workflow, run.event_revision_id, run.package_hash)
        or _digest(workflow.revision.snapshot) != run.hermes_binding.revision_digest
    ):
        raise WorkerFailure("invalid_output")
    if capability is not None and (
        capability.revoked_at
        or capability.expires_at <= timezone.now()
        or capability.correlation_id != run.hermes_binding.correlation_id
        or capability.revision_digest != run.hermes_binding.revision_digest
        or capability.workflow_id != run.workflow_id
        or capability.revision_id != run.event_revision_id
        or capability.actor_id != run.hermes_binding.actor_id
    ):
        raise WorkerFailure("invalid_output")


def should_cancel_hermes_run(run_id):
    run = AgentRun.objects.select_related("hermes_binding__actor__user").get(pk=run_id)
    control = AgentRunControl.objects.get(run=run)
    workflow = Workflow.objects.select_related("revision").get(pk=run.workflow_id)
    try:
        _check(run, control, workflow, control.capability)
        return run.status != "running"
    except WorkerFailure:
        return True


@transaction.atomic
def cancel_hermes_run(run_id):
    lane = _lane()
    run = AgentRun.objects.select_for_update(of=("self",)).get(pk=run_id)
    if not _owns_run(run, lane):
        return run
    if run.status not in ACTIVE:
        return run
    control = AgentRunControl.objects.select_for_update().get(run=run)
    control.cancel_requested_at = control.cancel_requested_at or timezone.now()
    control.save(update_fields=["cancel_requested_at", "updated_at"])
    try:
        with transaction.atomic():
            _revoke_record(control)
    except Exception:
        lane.admission_disabled = True
        control.admission_disabled = True
        lane.save(update_fields=["admission_disabled", "updated_at"])
        control.save(update_fields=["admission_disabled", "updated_at"])
    _event(run, "cancel_requested", "accepted")
    if run.status == "queued":
        _terminal(run, lane, "cancelled", billable=False)
    return run


def _revoke_record(control):
    if control.capability_id:
        record = WorkflowCapability.objects.select_for_update().get(pk=control.capability_id)
        if record.revoked_at is None:
            record.revoked_at = timezone.now()
            record.save(update_fields=["revoked_at"])
            AuditEvent.objects.create(
                actor=record.actor,
                action="mcp.capability_revoked",
                target_type="workflow",
                target_id=str(record.workflow_id),
                details={"capability_id": str(record.id)},
            )


def _terminal(run, lane, category, *, billable):
    try:
        with transaction.atomic():
            reservation = (charge_reserved_budget if billable else release_budget)(run_id=run.id)
    except Exception:
        # Retain the reservation and close admissions if accounting cannot be reconciled.
        reservation = None
        lane.admission_disabled = True
        AgentRunControl.objects.filter(run=run).update(admission_disabled=True)
    if reservation and reservation.settled_cost_microusd is not None:
        run.input_tokens = reservation.settled_input_tokens
        run.output_tokens = reservation.settled_output_tokens
        run.cost_microusd = reservation.settled_cost_microusd
    run.status = "cancelled" if category == "cancelled" else "failed"
    run.failure_category = category
    run.started_at = run.started_at or timezone.now()
    run.finished_at = timezone.now()
    run.save()
    _event(run, run.status, category)
    if lane.active_run_id == run.id:
        lane.active_run = None
    lane.save(update_fields=["active_run", "admission_disabled", "updated_at"])


@transaction.atomic
def _finish(run_id, category, *, billable, cleanup_ok):
    lane = _lane()
    run = AgentRun.objects.select_for_update(of=("self",)).get(pk=run_id)
    if not _owns_run(run, lane):
        return
    if run.status not in ACTIVE:
        return
    control = AgentRunControl.objects.select_for_update().get(run=run)
    if run.status == "running" and not billable:
        billable = True
        cleanup_ok = False
    try:
        with transaction.atomic():
            _revoke_record(control)
    except Exception:
        cleanup_ok = False
    if not cleanup_ok:
        lane.admission_disabled = True
        control.admission_disabled = True
        control.save(update_fields=["admission_disabled", "updated_at"])
    _terminal(run, lane, category, billable=billable)


def _validate_result(run, control, result):
    if (
        result["run_id"] != str(run.id)
        or result["workflow_id"] != str(run.workflow_id)
        or result["revision_id"] != run.event_revision_id
    ):
        raise WorkerFailure("invalid_output")
    if result["status"] != "succeeded":
        category = result.get("failure_category")
        if category not in AgentRun.FailureCategory.values:
            category = "invalid_output"
        raise WorkerFailure(category)
    references = result["proposal_references"]
    if not references or len({ref["proposal_id"] for ref in references}) != len(references):
        raise WorkerFailure("invalid_output")
    proposal_ids = []
    for reference in references:
        proposal = MCPSubmission.objects.filter(
            pk=reference["proposal_id"],
            capability_id=control.capability_id,
            kind="proposal",
        ).first()
        if (
            proposal is None
            or reference["schema_id"] != PROPOSAL_SCHEMA_ID
            or reference["proposal_digest"] != proposal.digest
            or _digest(proposal.content) != proposal.digest
        ):
            raise WorkerFailure("invalid_output")
        validate(proposal.content, PROPOSAL)
        proposal_ids.append(proposal.id)
    # Validate every operation of this run, including proposals omitted from the final answer.
    operations = DraftOperation.objects.filter(proposal__capability_id=control.capability_id)
    for operation in operations:
        if (
            operation.proposal_id not in proposal_ids
            or operation.workflow_id != run.workflow_id
            or operation.revision_id != run.event_revision_id
            or operation.actor_id != run.hermes_binding.actor_id
            or operation.status != "pending"
            or operation.approval_id
            or operation.receipt is not None
            or (operation.provider, operation.operation_kind)
            not in {
                ("eventbrite", "create_eventbrite_draft"),
                ("iterable", "create_iterable_email_draft"),
                ("iterable", "create_iterable_reminder_draft"),
            }
        ):
            raise WorkerFailure("invalid_output")
        expected_action = _digest(
            {
                "workflow_id": str(run.workflow_id),
                "revision_id": run.event_revision_id,
                "revision_digest": run.hermes_binding.revision_digest,
                "provider": operation.provider,
                "operation_kind": operation.operation_kind,
                "proposal_digest": operation.proposal.digest,
            }
        )
        expected_key = _digest(
            [
                str(run.workflow_id),
                run.event_revision_id,
                run.hermes_binding.actor_id,
                operation.operation_kind,
                expected_action,
            ]
        )
        if operation.action_digest != expected_action or operation.idempotency_key != expected_key:
            raise WorkerFailure("invalid_output")
    usage = result["usage"]
    if usage["cost_microusd"] != _cost(
        run.model_profile, usage["input_tokens"], usage["output_tokens"]
    ):
        raise WorkerFailure("invalid_output")


@shared_task(acks_late=True, reject_on_worker_lost=True)
def execute_hermes_run(run_id: UUID) -> None:
    from agents.hermes import HermesClient

    client = None
    token = None
    billable = False
    redelivery = False
    try:
        with transaction.atomic():
            lane = _lane()
            run = (
                AgentRun.objects.select_for_update(of=("self",))
                .select_related(
                    "hermes_binding__actor__user",
                    "model_profile",
                    "routing_policy",
                )
                .get(pk=run_id)
            )
            if not _owns_run(run, lane):
                return
            if run.status not in ACTIVE:
                return
            if run.status == "running":
                # Redelivery cannot establish whether the old worker still owns a process.
                billable = True
                redelivery = True
                raise WorkerFailure("dependency_unavailable")
            control = AgentRunControl.objects.select_for_update().get(run=run)
            workflow = (
                Workflow.objects.select_for_update()
                .select_related("revision")
                .get(pk=run.workflow_id)
            )
            if (
                lane.active_run_id != run.id
                or lane.admission_disabled
                or control.admission_disabled
            ):
                raise WorkerFailure("dependency_unavailable")
            _check(run, control, workflow)
            client = HermesClient.from_settings()
            remaining = max(1, int((control.lease_expires_at - timezone.now()).total_seconds()))
            token = issue_workflow_capability(
                workflow_id=run.workflow_id,
                revision_id=run.event_revision_id,
                actor_id=run.hermes_binding.actor_id,
                tools=TOOLS,
                lifetime_seconds=min(remaining, 300),
            )
            capability = WorkflowCapability.objects.select_for_update().get(
                token_digest=token_digest(token)
            )
            capability.correlation_id = run.hermes_binding.correlation_id
            capability.save(update_fields=["correlation_id"])
            control.capability = capability
            control.save(update_fields=["capability", "updated_at"])
            run.status = "running"
            run.started_at = timezone.now()
            run.save()
            _event(run, "running", "started")
        billable = True
        result = client.execute(
            run, capability=token, should_cancel=lambda: should_cancel_hermes_run(run.id)
        )
        with transaction.atomic():
            lane = _lane()
            run = (
                AgentRun.objects.select_for_update(of=("self",))
                .select_related(
                    "hermes_binding__actor__user",
                    "model_profile",
                )
                .get(pk=run_id)
            )
            if run.status != "running":
                raise WorkerFailure("invalid_output")
            control = AgentRunControl.objects.select_for_update().get(run=run)
            capability = WorkflowCapability.objects.select_for_update().get(
                pk=control.capability_id
            )
            workflow = (
                Workflow.objects.select_for_update()
                .select_related("revision")
                .get(pk=run.workflow_id)
            )
            if lane.admission_disabled or lane.active_run_id != run.id:
                raise WorkerFailure("dependency_unavailable")
            _check(run, control, workflow, capability)
            try:
                _validate_result(run, control, result)
            except WorkerFailure:
                raise
            except Exception:
                raise WorkerFailure("invalid_output") from None
            try:
                revoke_workflow_capability(capability=token)
            except Exception:
                raise WorkerFailure("dependency_unavailable", quarantine=True) from None
            settlement = settle_budget(
                run_id=run.id,
                input_tokens=result["usage"]["input_tokens"],
                output_tokens=result["usage"]["output_tokens"],
            )
            run.status = "succeeded"
            run.finished_at = timezone.now()
            run.input_tokens = settlement.settled_input_tokens
            run.output_tokens = settlement.settled_output_tokens
            run.cost_microusd = settlement.settled_cost_microusd
            run.save()
            _event(run, "succeeded", "accepted")
            lane.active_run = None
            lane.save(update_fields=["active_run", "updated_at"])
    except AgentRun.DoesNotExist:
        return
    except Exception as error:
        category = getattr(error, "category", "dependency_unavailable")
        if category not in AgentRun.FailureCategory.values:
            category = "invalid_output"
        cleanup_ok = not billable
        if billable:
            try:
                client = client or HermesClient.from_settings()
                cleanup_ok = (
                    client.cancel(run) is True
                    and not redelivery
                    and not getattr(error, "quarantine", False)
                )
            except Exception:
                cleanup_ok = False
        _finish(run_id, category, billable=billable, cleanup_ok=cleanup_ok)
