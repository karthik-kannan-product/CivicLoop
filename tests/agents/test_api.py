import json
from pathlib import Path

import pytest
from agents.budgets import reserve_budget, settle_budget
from agents.models import AgentStep
from django.test import Client, override_settings
from django.utils import timezone
from evaluations.models import EvaluationResult
from jsonschema import Draft202012Validator, FormatChecker
from launchloop.models import DemoActor

from tests.agents.test_runs import create_run
from tests.identity.test_security_actions_api import create_authenticated_owner

ROOT = Path(__file__).resolve().parents[2]


def validate_schema(payload: dict, relative_path: str) -> None:
    schema = json.loads((ROOT / "schemas" / relative_path).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


@pytest.mark.django_db
def test_safe_run_reads_require_owner_or_approver_and_are_no_store() -> None:
    run = create_run()
    base = f"/api/v1/agent-runs/{run.id}"
    anonymous = Client().get(base)
    operator = Client()
    operator.force_login(run.workflow.revision.author.user)
    denied = operator.get(base)
    approver = Client()
    approver_actor = DemoActor.objects.get(role=DemoActor.Role.APPROVER)
    approver.force_login(approver_actor.user)
    allowed = approver.get(base)

    assert anonymous.status_code == 401
    assert denied.status_code == 403
    assert allowed.status_code == 200
    for response in [anonymous, denied, allowed]:
        assert response["Cache-Control"] == "no-store"
    validate_schema(allowed.json(), "agents/run-read.schema.json")


@pytest.mark.django_db
def test_owner_can_read_safe_steps_evaluations_and_usage() -> None:
    run = create_run()
    AgentStep.objects.create(
        run=run,
        sequence=1,
        kind="validation",
        status="succeeded",
        started_at=timezone.now(),
        finished_at=timezone.now(),
        input_summary="Validated synthetic fixture.",
        output_summary="No missing fields.",
        duration_ms=25,
    )
    EvaluationResult.objects.create(
        run=run,
        evaluator="deterministic",
        outcome="passed",
        score="1.0",
        reason_codes=["expected_output"],
        summary="Synthetic checks passed.",
        dataset_id="launchloop_synthetic_v1",
        dataset_version=3,
        example_id="11111111-1111-4111-8111-111111111111",
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
        deterministic_checks=[{"check": "schema", "passed": True}],
        trace_id=run.trace_id,
    )
    reserve_budget(
        run_id=run.id,
        profile_id=run.model_profile.profile_id,
        profile_revision=run.model_profile.revision,
        estimated_input_tokens=100,
        estimated_output_tokens=20,
    )
    settle_budget(run_id=run.id, input_tokens=80, output_tokens=10)

    with override_settings(CIVICLOOP_ADMIN_IDENTITY_ENABLED=True):
        owner, _profile, _metadata, _password = create_authenticated_owner()
        responses = {
            "steps": owner.get(f"/api/v1/agent-runs/{run.id}/steps"),
            "evaluations": owner.get(f"/api/v1/agent-runs/{run.id}/evaluations"),
            "usage": owner.get(f"/api/v1/agent-runs/{run.id}/usage"),
        }

    assert {response.status_code for response in responses.values()} == {200}
    assert {response["Cache-Control"] for response in responses.values()} == {"no-store"}
    validate_schema(responses["steps"].json(), "agents/step-page.schema.json")
    validate_schema(responses["evaluations"].json(), "evaluations/result-page.schema.json")
    validate_schema(responses["usage"].json(), "agents/usage-read.schema.json")
    combined = json.dumps({key: response.json() for key, response in responses.items()}).lower()
    for prohibited in ["prompt", "judge", "provider_response", "credential", "authorization"]:
        assert prohibited not in combined


@pytest.mark.django_db
def test_unknown_run_returns_safe_rfc_9457_problem() -> None:
    path = "/api/v1/agent-runs/11111111-1111-4111-8111-111111111111"
    response = Client().get(path)

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == "authentication_required"

    with override_settings(CIVICLOOP_ADMIN_IDENTITY_ENABLED=True):
        owner, _profile, _metadata, _password = create_authenticated_owner()
        missing = owner.get(path)
    assert missing.status_code == 404
    assert missing["Cache-Control"] == "no-store"
    assert missing.json()["code"] == "agent_run_not_found"
