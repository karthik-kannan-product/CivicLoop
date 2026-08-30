import uuid

import pytest
from agents.budgets import reserve_budget, settle_budget
from agents.models import AgentRun, BudgetLedgerRecord
from django.core.cache import cache
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings
from evaluations.models import EvaluationResult

from tests.agents.test_runs import create_run


def test_control_plane_invariant_migrations_are_present() -> None:
    loader = MigrationLoader(None, ignore_no_migrations=True)

    assert ("agents", "0002_database_invariants") in loader.disk_migrations
    assert ("evaluations", "0002_evaluation_results_append_only") in loader.disk_migrations
    assert AgentRun._meta.get_field("workflow").remote_field.on_delete.__name__ == "PROTECT"
    assert (
        BudgetLedgerRecord._meta.get_field("reservation").remote_field.on_delete.__name__
        == "PROTECT"
    )
    assert EvaluationResult._meta.get_field("run").remote_field.on_delete.__name__ == "PROTECT"


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "bundle-b-restart-test",
        }
    }
)
@pytest.mark.django_db(transaction=True)
def test_budget_and_evaluation_records_survive_cache_and_connection_restart() -> None:
    run = create_run()
    reservation = reserve_budget(
        run_id=run.id,
        profile_id=run.model_profile.profile_id,
        profile_revision=run.model_profile.revision,
        estimated_input_tokens=100,
        estimated_output_tokens=20,
    )
    settle_budget(run_id=run.id, input_tokens=80, output_tokens=10)
    result = EvaluationResult.objects.create(
        run=run,
        evaluator="deterministic",
        outcome="passed",
        reason_codes=["expected_output"],
        summary="Synthetic checks passed.",
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
        deterministic_checks=[{"check": "schema", "passed": True}],
        trace_id=run.trace_id,
    )
    cache.set("synthetic-disposable-state", "discarded")
    cache.clear()
    close_old_connections()

    reservation.refresh_from_db()
    result.refresh_from_db()
    assert reservation.status == "settled"
    assert result.outcome == "passed"
    assert cache.get("synthetic-disposable-state") is None


@pytest.mark.django_db(transaction=True)
def test_postgresql_rejects_run_rebinding_and_evaluation_mutation() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL trigger enforcement is verified in CI and Compose.")

    run = create_run()
    other = create_run()
    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE agents_agentrun SET workflow_id = %s WHERE id = %s",
                [str(other.workflow_id), str(run.id)],
            )

    result = EvaluationResult.objects.create(
        run=run,
        evaluator="deterministic",
        outcome="passed",
        reason_codes=["expected_output"],
        summary="Synthetic checks passed.",
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
        deterministic_checks=[{"check": "schema", "passed": True}],
        trace_id=run.trace_id,
    )
    with pytest.raises(DatabaseError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE evaluations_evaluationresult SET summary = %s WHERE id = %s",
                ["changed", str(result.id)],
            )
