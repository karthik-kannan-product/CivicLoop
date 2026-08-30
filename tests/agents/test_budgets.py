import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from agents.budgets import (
    BudgetExhausted,
    BudgetRequestConflict,
    UnknownModelPrice,
    expire_reservations,
    reserve_budget,
    settle_budget,
)
from agents.models import BudgetLedgerRecord, BudgetReservation, ModelProfile, RoutingPolicy
from django.db import IntegrityError, close_old_connections, connection, connections
from django.utils import timezone


def create_profile(
    *,
    profile_id: str = "workflow_default",
    revision: int = 1,
    input_price: int | None = 1_000_000,
    output_price: int | None = 2_000_000,
) -> ModelProfile:
    return ModelProfile.objects.create(
        profile_id=profile_id,
        revision=revision,
        provider="openai",
        model="gpt-5-mini",
        purpose="workflow",
        max_input_tokens=10_000,
        max_output_tokens=2_000,
        temperature="0.20",
        input_price_microusd_per_million=input_price,
        output_price_microusd_per_million=output_price,
    )


def create_policy(profile: ModelProfile, **overrides: int) -> RoutingPolicy:
    values = {
        "policy_id": f"{profile.profile_id}_policy",
        "revision": 1,
        "purpose": "workflow",
        "model_profile": profile,
        "per_run_limit_microusd": 500_000,
        "monthly_limit_microusd": 25_000_000,
    }
    values.update(overrides)
    return RoutingPolicy.objects.create(**values)


@pytest.mark.django_db
def test_model_profiles_are_versioned_and_immutable() -> None:
    profile = create_profile()
    profile.model = "changed-model"

    with pytest.raises(ValueError, match="immutable"):
        profile.save()

    with pytest.raises(IntegrityError):
        create_profile()


@pytest.mark.django_db
def test_unknown_model_or_missing_price_fails_closed() -> None:
    with pytest.raises(UnknownModelPrice):
        reserve_budget(
            run_id=uuid.uuid4(),
            profile_id="not_allowlisted",
            profile_revision=1,
            estimated_input_tokens=1,
            estimated_output_tokens=1,
        )


@pytest.mark.django_db
def test_profile_token_limits_fail_closed_before_reservation() -> None:
    profile = create_profile()
    create_policy(profile)

    with pytest.raises(BudgetRequestConflict, match="profile limit"):
        reserve_budget(
            run_id=uuid.uuid4(),
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            estimated_input_tokens=profile.max_input_tokens + 1,
            estimated_output_tokens=0,
        )

    profile = create_profile(profile_id="missing_price", output_price=None)
    create_policy(profile)
    with pytest.raises(UnknownModelPrice):
        reserve_budget(
            run_id=uuid.uuid4(),
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            estimated_input_tokens=1,
            estimated_output_tokens=1,
        )


@pytest.mark.django_db(transaction=True)
def test_reservation_and_settlement_are_transactional_idempotent_and_auditable() -> None:
    profile = create_profile()
    create_policy(profile)
    run_id = uuid.uuid4()

    first = reserve_budget(
        run_id=run_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        estimated_input_tokens=1_000,
        estimated_output_tokens=500,
    )
    retry = reserve_budget(
        run_id=run_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        estimated_input_tokens=1_000,
        estimated_output_tokens=500,
    )

    assert first.pk == retry.pk
    assert first.reserved_cost_microusd == 2_000
    assert BudgetLedgerRecord.objects.filter(run_id=run_id, entry_type="reserved").count() == 1

    settled = settle_budget(run_id=run_id, input_tokens=800, output_tokens=400)
    settled_retry = settle_budget(run_id=run_id, input_tokens=800, output_tokens=400)
    assert settled.pk == settled_retry.pk
    assert settled.status == BudgetReservation.Status.SETTLED
    assert settled.settled_cost_microusd == 1_600
    assert BudgetLedgerRecord.objects.filter(run_id=run_id, entry_type="settled").count() == 1

    with pytest.raises(BudgetRequestConflict):
        settle_budget(run_id=run_id, input_tokens=801, output_tokens=400)


@pytest.mark.django_db
def test_per_run_and_monthly_budget_exhaustion_fail_closed() -> None:
    profile = create_profile()
    create_policy(profile, per_run_limit_microusd=1_500, monthly_limit_microusd=1_500)

    with pytest.raises(BudgetExhausted):
        reserve_budget(
            run_id=uuid.uuid4(),
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            estimated_input_tokens=1_501,
            estimated_output_tokens=500,
        )

    first = reserve_budget(
        run_id=uuid.uuid4(),
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        estimated_input_tokens=500,
        estimated_output_tokens=0,
    )
    assert first.reserved_cost_microusd == 500
    with pytest.raises(BudgetExhausted):
        reserve_budget(
            run_id=uuid.uuid4(),
            profile_id=profile.profile_id,
            profile_revision=profile.revision,
            estimated_input_tokens=1_001,
            estimated_output_tokens=0,
        )


@pytest.mark.django_db
def test_expired_reservations_are_released_once_and_restore_capacity() -> None:
    profile = create_profile()
    create_policy(profile, monthly_limit_microusd=1_000)
    reservation = reserve_budget(
        run_id=uuid.uuid4(),
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        estimated_input_tokens=1_000,
        estimated_output_tokens=0,
        expires_at=timezone.now() + timedelta(minutes=1),
    )
    BudgetReservation.objects.filter(pk=reservation.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )

    assert expire_reservations() == 1
    assert expire_reservations() == 0
    reservation.refresh_from_db()
    assert reservation.status == BudgetReservation.Status.RELEASED
    assert (
        BudgetLedgerRecord.objects.filter(run_id=reservation.run_id, entry_type="released").count()
        == 1
    )

    replacement = reserve_budget(
        run_id=uuid.uuid4(),
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        estimated_input_tokens=1_000,
        estimated_output_tokens=0,
    )
    assert replacement.status == BudgetReservation.Status.RESERVED


@pytest.mark.django_db(transaction=True)
def test_postgresql_concurrent_reservations_cannot_overspend_monthly_budget() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock concurrency is verified in CI and Compose.")
    profile = create_profile(profile_id="concurrency_profile")
    create_policy(profile, per_run_limit_microusd=1_000, monthly_limit_microusd=1_000)
    barrier = Barrier(2)

    def reserve(run_id: uuid.UUID) -> str:
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            reserve_budget(
                run_id=run_id,
                profile_id=profile.profile_id,
                profile_revision=profile.revision,
                estimated_input_tokens=1_000,
                estimated_output_tokens=0,
            )
        except BudgetExhausted:
            return "exhausted"
        finally:
            connections.close_all()
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reserve, [uuid.uuid4(), uuid.uuid4()]))

    assert sorted(outcomes) == ["exhausted", "reserved"]
