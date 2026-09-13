import json
import uuid
from dataclasses import dataclass

import pytest
from agents.models import (
    AgentRun,
    BudgetPeriod,
    BudgetReservation,
    ModelProfile,
    RoutingPolicy,
)
from django.contrib.auth.models import User
from django.utils import timezone
from evaluations.judge import JudgeResponse, run_fixed_judge
from integrations.models import ConnectionState, EncryptedSecret, IntegrationConnection
from launchloop.models import ApprovalRequest, DemoActor, Event, EventRevision, Workflow


@dataclass
class FakeAdministrator:
    id: uuid.UUID


class FakeJudgeClient:
    def __init__(self, response: JudgeResponse | Exception) -> None:
        self.response = response
        self.calls = 0

    def evaluate(self, *, credential, package, model):
        self.calls += 1
        assert model == "gpt-5.5-2026-04-23"
        assert set(package) == {"status", "assets", "audience", "sponsor", "evidence"}
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _workflow() -> tuple[Workflow, ApprovalRequest]:
    suffix = uuid.uuid4().hex[:8]
    operator_user = User.objects.create_user(username=f"judge-operator-{suffix}")
    approver_user = User.objects.create_user(username=f"judge-approver-{suffix}")
    operator = DemoActor.objects.create(
        slug=f"judge-operator-{suffix}",
        display_name="Judge operator",
        role=DemoActor.Role.OPERATOR,
        user=operator_user,
    )
    DemoActor.objects.create(
        slug=f"judge-approver-{suffix}",
        display_name="Judge approver",
        role=DemoActor.Role.APPROVER,
        user=approver_user,
    )
    event = Event.objects.create(slug=f"judge-event-{suffix}", title="Synthetic event")
    revision = EventRevision.objects.create(
        event=event,
        version=1,
        snapshot={"synthetic": True},
        author=operator,
    )
    package = {
        "status": "ready_for_review",
        "assets": {
            "invitation": {"subject": "Synthetic invitation", "body": "Safe body"},
            "reminder": {"subject": "Synthetic reminder", "body": "Safe reminder"},
            "social": {"body": "Safe social"},
        },
        "audience": {"name": "Synthetic audience", "member_count": 10, "language": "English"},
        "sponsor": {"passed": True, "tier": "gold"},
        "evidence": ["Synthetic evidence"],
    }
    workflow = Workflow.objects.create(
        event=event,
        revision=revision,
        status=Workflow.Status.IN_REVIEW,
        package=package,
        package_hash="a" * 64,
    )
    approval = ApprovalRequest.objects.create(
        workflow=workflow,
        submitter=operator,
        package_hash=workflow.package_hash,
        status=ApprovalRequest.Status.PENDING,
    )
    return workflow, approval


def _configured_openai(monkeypatch) -> None:
    secret = EncryptedSecret.objects.create(
        provider="openai",
        scope="organization",
        ciphertext=b"synthetic",
        nonce=b"0" * 12,
        key_id="synthetic-key",
    )
    IntegrationConnection.objects.create(
        provider="openai",
        state=ConnectionState.HEALTHY,
        secret=secret,
        configuration={"model": "openai/gpt-oss-20b"},
        capabilities=["connection_test", "evaluation_judge", "inference"],
    )

    class Lease:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(
        "evaluations.judge.PostgresSecretStore.lease",
        lambda *_args, **_kwargs: Lease(),
    )


@pytest.mark.django_db
def test_fixed_judge_uses_versioned_accessible_openai_profile() -> None:
    profile = ModelProfile.objects.get(profile_id="launchloop_openai_judge", revision=2)
    policy = RoutingPolicy.objects.get(policy_id="launchloop_openai_judge", revision=2)

    assert profile.model == "gpt-5.5-2026-04-23"
    assert profile.input_price_microusd_per_million == 5_000_000
    assert profile.output_price_microusd_per_million == 30_000_000
    assert policy.model_profile == profile
    assert policy.per_run_limit_microusd == 500_000
    assert policy.monthly_limit_microusd == 25_000_000


def test_openai_response_schema_uses_only_supported_constraints() -> None:
    from evaluations.judge import _structured_output_schema

    schema = _structured_output_schema()
    labels = schema["properties"]["labels"]
    rationale = schema["properties"]["rationale"]

    assert "uniqueItems" not in labels
    assert "maxLength" not in rationale


def test_openai_judge_disables_reasoning_and_prompts_for_bounded_rationale(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit):
            return json.dumps(
                {
                    "output": [
                        {
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(
                                        {
                                            "outcome": "passed",
                                            "score": 1,
                                            "labels": ["expected_output"],
                                            "rationale": "Within the limit.",
                                        }
                                    ),
                                }
                            ]
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                }
            ).encode()

    class Opener:
        def open(self, request, *, timeout):
            assert timeout > 0
            captured.update(json.loads(request.data))
            return Response()

    class Lease:
        def use(self, operation):
            return operation(memoryview(b"synthetic"))

    monkeypatch.setattr("evaluations.judge.build_opener", lambda *_args: Opener())

    from evaluations.judge import OpenAIResponsesJudgeClient

    result = OpenAIResponsesJudgeClient().evaluate(
        credential=Lease(), package={"status": "ready_for_review"}, model="synthetic-model"
    )

    assert result.outcome == "passed"
    assert captured["reasoning"] == {"effort": "none"}
    assert "at most 500 characters" in captured["input"][0]["content"]


@pytest.mark.django_db
def test_fixed_judge_persists_typed_advisory_result_and_settles_budget(monkeypatch) -> None:
    workflow, approval = _workflow()
    workflow.telemetry_traceparent = f"00-{'d' * 32}-{'e' * 16}-01"
    workflow.save(update_fields=["telemetry_traceparent"])
    _configured_openai(monkeypatch)
    client = FakeJudgeClient(
        JudgeResponse(
            outcome="passed",
            score=0.94,
            labels=["expected_output"],
            rationale="The synthetic package satisfies the fixed rubric.",
            input_tokens=320,
            output_tokens=60,
            latency_ms=125,
        )
    )

    run = run_fixed_judge(workflow, FakeAdministrator(uuid.uuid4()), client=client)

    result = run.evaluation_results.get()
    approval.refresh_from_db()
    assert run.status == AgentRun.Status.SUCCEEDED
    assert result.advisory_only is True
    assert result.rubric_id == "launchloop_package_quality"
    assert result.rubric_version == 1
    assert result.reason_codes == ["expected_output"]
    assert run.trace_id == "d" * 32
    assert BudgetReservation.objects.get(run_id=run.id).status == "settled"
    assert run.cost_microusd > 0
    assert approval.status == ApprovalRequest.Status.PENDING
    assert client.calls == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("setup", "response", "category"),
    [
        (False, None, "dependency_unavailable"),
        (True, RuntimeError("provider body must not escape"), "provider_unavailable"),
        (
            True,
            JudgeResponse("passed", 2.0, ["unknown"], "unsafe", 1, 1, 1),
            "invalid_output",
        ),
    ],
)
def test_fixed_judge_failure_is_sanitized_and_preserves_package(
    monkeypatch, setup, response, category
) -> None:
    workflow, approval = _workflow()
    if setup:
        _configured_openai(monkeypatch)
    client = FakeJudgeClient(response or RuntimeError("not called"))
    package_hash = workflow.package_hash

    run = run_fixed_judge(workflow, FakeAdministrator(uuid.uuid4()), client=client)

    workflow.refresh_from_db()
    approval.refresh_from_db()
    assert run.status == AgentRun.Status.FAILED
    assert run.failure_category == category
    assert not run.evaluation_results.exists()
    assert workflow.package_hash == package_hash
    assert approval.status == ApprovalRequest.Status.PENDING


@pytest.mark.django_db
def test_fixed_judge_exhausted_monthly_budget_never_calls_provider(monkeypatch) -> None:
    workflow, _approval = _workflow()
    _configured_openai(monkeypatch)
    period = BudgetPeriod.objects.create(
        month=timezone.now().date().replace(day=1),
        limit_microusd=25_000_000,
        settled_microusd=25_000_000,
    )
    assert period
    client = FakeJudgeClient(RuntimeError("must not be called"))

    run = run_fixed_judge(workflow, FakeAdministrator(uuid.uuid4()), client=client)

    assert run.status == AgentRun.Status.FAILED
    assert run.failure_category == AgentRun.FailureCategory.BUDGET_EXHAUSTED
    assert client.calls == 0


@pytest.mark.django_db
def test_fixed_judge_refuses_non_synthetic_package_before_provider_access(monkeypatch) -> None:
    workflow, _approval = _workflow()
    workflow.revision.snapshot = {"title": "Live event"}
    workflow.revision.save(update_fields=["snapshot"])
    _configured_openai(monkeypatch)
    client = FakeJudgeClient(RuntimeError("must not be called"))

    with pytest.raises(ValueError, match="evaluation_synthetic_only"):
        run_fixed_judge(workflow, FakeAdministrator(uuid.uuid4()), client=client)

    assert client.calls == 0
    assert not workflow.agent_runs.exists()
