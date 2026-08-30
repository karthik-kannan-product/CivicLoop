import uuid

import pytest
from agents.models import AgentRun, AgentStep
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from evaluations.models import EvaluationResult
from launchloop.models import ApprovalRequest, DemoActor, Event, EventRevision, Workflow

from tests.agents.test_budgets import create_policy, create_profile


def create_workflow() -> tuple[Workflow, EventRevision, DemoActor, DemoActor]:
    suffix = uuid.uuid4().hex[:8]
    operator_user = User.objects.create_user(username=f"run.operator.{suffix}", password="not-used")
    approver_user = User.objects.create_user(username=f"run.approver.{suffix}", password="not-used")
    operator = DemoActor.objects.create(
        slug=f"run-operator-{suffix}",
        display_name="Run Operator",
        role=DemoActor.Role.OPERATOR,
        user=operator_user,
    )
    approver = DemoActor.objects.create(
        slug=f"run-approver-{suffix}",
        display_name="Run Approver",
        role=DemoActor.Role.APPROVER,
        user=approver_user,
    )
    event = Event.objects.create(slug=f"run-event-{suffix}", title="Synthetic run event")
    revision = EventRevision.objects.create(
        event=event,
        version=1,
        snapshot={"synthetic": True},
        author=operator,
    )
    workflow = Workflow.objects.create(
        event=event,
        revision=revision,
        status=Workflow.Status.IN_REVIEW,
        package={"synthetic": True},
        package_hash="a" * 64,
    )
    return workflow, revision, operator, approver


def create_run(*, workflow: Workflow | None = None) -> AgentRun:
    if workflow is None:
        workflow, revision, _operator, _approver = create_workflow()
    else:
        revision = workflow.revision
    profile = create_profile(profile_id=f"profile_{uuid.uuid4().hex[:8]}")
    policy = create_policy(profile)
    return AgentRun.objects.create(
        workflow=workflow,
        event_revision=revision,
        package_hash=workflow.package_hash,
        routing_policy=policy,
        model_profile=profile,
        fixture_manifest_id="launchloop_synthetic_v1",
        fixture_manifest_revision=3,
        fixture_manifest_digest="b" * 64,
        privacy_mode="synthetic_full",
        status="queued",
        attempt=1,
        trace_id="c" * 32,
    )


@pytest.mark.django_db
def test_run_binding_cannot_be_reassigned_after_creation() -> None:
    run = create_run()
    other_workflow, other_revision, _operator, _approver = create_workflow()
    run.workflow = other_workflow
    run.event_revision = other_revision

    with pytest.raises(ValueError, match="binding is immutable"):
        run.save()


@pytest.mark.django_db
def test_run_status_usage_and_trace_progress_without_rebinding() -> None:
    run = create_run()
    run.status = AgentRun.Status.SUCCEEDED
    run.started_at = timezone.now()
    run.finished_at = timezone.now()
    run.input_tokens = 100
    run.output_tokens = 25
    run.cost_microusd = 150
    run.span_count = 4
    run.save()

    run.refresh_from_db()
    assert run.status == AgentRun.Status.SUCCEEDED
    assert run.input_tokens == 100
    assert run.span_count == 4


@pytest.mark.django_db
def test_run_lifecycle_shape_fails_closed() -> None:
    run = create_run()
    run.status = AgentRun.Status.SUCCEEDED
    run.finished_at = timezone.now()

    with pytest.raises(ValidationError, match="started_at"):
        run.save()


@pytest.mark.django_db
def test_step_rejects_credential_bearing_or_provider_body_content() -> None:
    run = create_run()

    with pytest.raises(ValidationError, match="prohibited telemetry content"):
        AgentStep.objects.create(
            run=run,
            sequence=1,
            kind="model_inference",
            status="failed",
            started_at=timezone.now(),
            finished_at=timezone.now(),
            input_summary="Authorization: Bearer synthetic-secret",
            failure_category="provider_unavailable",
        )

    with pytest.raises(ValidationError, match="prohibited telemetry content"):
        AgentStep.objects.create(
            run=run,
            sequence=2,
            kind="validation",
            status="failed",
            started_at=timezone.now(),
            finished_at=timezone.now(),
            output_summary="provider_response_body={unsafe}",
            failure_category="invalid_output",
        )


@pytest.mark.django_db
def test_evaluation_is_advisory_and_does_not_mutate_approval_state() -> None:
    workflow, _revision, operator, approver = create_workflow()
    approval = ApprovalRequest.objects.create(
        workflow=workflow,
        submitter=operator,
        approver=approver,
        package_hash=workflow.package_hash,
        status=ApprovalRequest.Status.PENDING,
    )
    run = create_run(workflow=workflow)

    result = EvaluationResult.objects.create(
        run=run,
        evaluator="deterministic",
        outcome="failed",
        score="0.25",
        reason_codes=["policy_violation"],
        summary="Synthetic policy check failed.",
        dataset_id="launchloop_synthetic_v1",
        dataset_version=3,
        example_id=uuid.uuid4(),
        prompt_reference="deterministic_checks",
        prompt_version=1,
        evaluation_policy_id="launchloop_eval",
        evaluation_policy_version=1,
        evaluated_schema_id="urn:civicloop:schema:agents:agent-run:v1.0",
        evaluated_schema_version="1.0",
        candidate_id="candidate_v1",
        candidate_version=1,
        rubric_id="launchloop_rubric",
        rubric_version=1,
        deterministic_checks=[{"check": "policy", "passed": False}],
        trace_id=run.trace_id,
    )

    approval.refresh_from_db()
    workflow.refresh_from_db()
    assert result.advisory_only is True
    assert approval.status == ApprovalRequest.Status.PENDING
    assert workflow.status == Workflow.Status.IN_REVIEW


@pytest.mark.django_db
def test_evaluation_summary_rejects_credential_bearing_content() -> None:
    run = create_run()
    with pytest.raises(ValidationError, match="prohibited telemetry content"):
        EvaluationResult.objects.create(
            run=run,
            evaluator="deterministic",
            outcome="failed",
            reason_codes=["tool_error"],
            summary="api_key=synthetic-secret",
            dataset_id="launchloop_synthetic_v1",
            dataset_version=3,
            example_id=uuid.uuid4(),
            prompt_reference="deterministic_checks",
            prompt_version=1,
            evaluation_policy_id="launchloop_eval",
            evaluation_policy_version=1,
            evaluated_schema_id="urn:civicloop:schema:agents:agent-run:v1.0",
            evaluated_schema_version="1.0",
            candidate_id="candidate_v1",
            candidate_version=1,
            rubric_id="launchloop_rubric",
            rubric_version=1,
            deterministic_checks=[{"check": "tool", "passed": False}],
            trace_id=run.trace_id,
        )


@pytest.mark.django_db
def test_evaluation_rejects_unbounded_judge_and_check_payloads() -> None:
    run = create_run()
    common = {
        "run": run,
        "evaluator": "deterministic",
        "outcome": "failed",
        "reason_codes": ["schema_invalid"],
        "summary": "Synthetic schema check failed.",
        "dataset_id": "launchloop_synthetic_v1",
        "dataset_version": 3,
        "example_id": uuid.uuid4(),
        "prompt_reference": "deterministic_checks",
        "prompt_version": 1,
        "evaluation_policy_id": "launchloop_eval",
        "evaluation_policy_version": 1,
        "evaluated_schema_id": "urn:civicloop:schema:agents:agent-run:v1.0",
        "evaluated_schema_version": "1.0",
        "candidate_id": "candidate_v1",
        "candidate_version": 1,
        "rubric_id": "launchloop_rubric",
        "rubric_version": 1,
        "trace_id": run.trace_id,
    }
    with pytest.raises(ValidationError, match="Judge metadata"):
        EvaluationResult.objects.create(
            **common,
            judge={"provider_response_body": "unsafe"},
            deterministic_checks=[{"check": "schema", "passed": False}],
        )
    with pytest.raises(ValidationError, match="Deterministic checks"):
        EvaluationResult.objects.create(
            **common,
            deterministic_checks=[{"check": "schema", "passed": False, "raw": "unsafe"}],
        )
