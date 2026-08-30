import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from agents.models import (
    BudgetLedgerRecord,
    BudgetPeriod,
    BudgetReservation,
    ModelProfile,
    RoutingPolicy,
)


class BudgetError(Exception):
    pass


class UnknownModelPrice(BudgetError):
    pass


class BudgetExhausted(BudgetError):
    pass


class BudgetRequestConflict(BudgetError):
    pass


def _month_start(value: datetime) -> datetime.date:
    return value.date().replace(day=1)


def _cost(profile: ModelProfile, input_tokens: int, output_tokens: int) -> int:
    if (
        profile.input_price_microusd_per_million is None
        or profile.output_price_microusd_per_million is None
    ):
        raise UnknownModelPrice("The selected model has no approved price.")
    if input_tokens < 0 or output_tokens < 0:
        raise BudgetRequestConflict("Token counts cannot be negative.")
    numerator = (
        input_tokens * profile.input_price_microusd_per_million
        + output_tokens * profile.output_price_microusd_per_million
    )
    return (numerator + 999_999) // 1_000_000


def _profile_and_policy(
    profile_id: str, profile_revision: int
) -> tuple[ModelProfile, RoutingPolicy]:
    try:
        profile = ModelProfile.objects.get(profile_id=profile_id, revision=profile_revision)
        policy = RoutingPolicy.objects.get(model_profile=profile, purpose=profile.purpose)
    except (ModelProfile.DoesNotExist, RoutingPolicy.DoesNotExist) as error:
        raise UnknownModelPrice("The selected model and price are not allowlisted.") from error
    if (
        profile.input_price_microusd_per_million is None
        or profile.output_price_microusd_per_million is None
    ):
        raise UnknownModelPrice("The selected model has no approved price.")
    return profile, policy


def _period_for_update(policy: RoutingPolicy, now: datetime) -> BudgetPeriod:
    period, _ = BudgetPeriod.objects.get_or_create(
        month=_month_start(now), defaults={"limit_microusd": policy.monthly_limit_microusd}
    )
    period = BudgetPeriod.objects.select_for_update().get(pk=period.pk)
    if period.limit_microusd != policy.monthly_limit_microusd:
        raise BudgetRequestConflict("The monthly budget policy changed within an active period.")
    return period


def reserve_budget(
    *,
    run_id: uuid.UUID,
    profile_id: str,
    profile_revision: int,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    expires_at: datetime | None = None,
) -> BudgetReservation:
    now = timezone.now()
    with transaction.atomic():
        existing = BudgetReservation.objects.select_for_update().filter(run_id=run_id).first()
        if existing is not None:
            expected = (
                existing.model_profile.profile_id,
                existing.model_profile.revision,
                existing.estimated_input_tokens,
                existing.estimated_output_tokens,
            )
            requested = (
                profile_id,
                profile_revision,
                estimated_input_tokens,
                estimated_output_tokens,
            )
            if expected != requested:
                raise BudgetRequestConflict("The run already has a different budget request.")
            return existing

        profile, policy = _profile_and_policy(profile_id, profile_revision)
        if (
            estimated_input_tokens > profile.max_input_tokens
            or estimated_output_tokens > profile.max_output_tokens
        ):
            raise BudgetRequestConflict("Estimated tokens exceed the approved profile limit.")
        cost = _cost(profile, estimated_input_tokens, estimated_output_tokens)
        if cost > policy.per_run_limit_microusd:
            raise BudgetExhausted("The per-run budget is exhausted.")

        period = _period_for_update(policy, now)
        _expire_period_reservations(period=period, now=now)
        period.refresh_from_db()
        if period.reserved_microusd + period.settled_microusd + cost > period.limit_microusd:
            raise BudgetExhausted("The monthly budget is exhausted.")

        reservation = BudgetReservation.objects.create(
            run_id=run_id,
            model_profile=profile,
            routing_policy=policy,
            period=period,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            reserved_cost_microusd=cost,
            expires_at=expires_at or now + timedelta(minutes=15),
        )
        period.reserved_microusd += cost
        period.save(update_fields=["reserved_microusd", "updated_at"])
        BudgetLedgerRecord.objects.create(
            run_id=run_id,
            reservation=reservation,
            model_profile_id_snapshot=profile.profile_id,
            model_profile_revision=profile.revision,
            entry_type=BudgetLedgerRecord.EntryType.RESERVED,
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
            cost_microusd=cost,
        )
        return reservation


def settle_budget(*, run_id: uuid.UUID, input_tokens: int, output_tokens: int) -> BudgetReservation:
    with transaction.atomic():
        try:
            reservation = (
                BudgetReservation.objects.select_for_update()
                .select_related("model_profile", "period")
                .get(run_id=run_id)
            )
        except BudgetReservation.DoesNotExist as error:
            raise BudgetRequestConflict("The run has no budget reservation.") from error
        if reservation.status == BudgetReservation.Status.RELEASED:
            raise BudgetRequestConflict("The run budget reservation has expired.")
        if reservation.status == BudgetReservation.Status.SETTLED:
            if (
                reservation.settled_input_tokens != input_tokens
                or reservation.settled_output_tokens != output_tokens
            ):
                raise BudgetRequestConflict("The run already has different settled usage.")
            return reservation

        actual_cost = _cost(reservation.model_profile, input_tokens, output_tokens)
        if (
            input_tokens > reservation.model_profile.max_input_tokens
            or output_tokens > reservation.model_profile.max_output_tokens
        ):
            raise BudgetRequestConflict("Settled tokens exceed the approved profile limit.")
        if actual_cost > reservation.routing_policy.per_run_limit_microusd:
            raise BudgetExhausted("The per-run budget is exhausted.")
        period = BudgetPeriod.objects.select_for_update().get(pk=reservation.period_id)
        projected = period.settled_microusd + period.reserved_microusd
        projected += actual_cost - reservation.reserved_cost_microusd
        if projected > period.limit_microusd:
            raise BudgetExhausted("The monthly budget is exhausted.")

        period.reserved_microusd -= reservation.reserved_cost_microusd
        period.settled_microusd += actual_cost
        period.save(update_fields=["reserved_microusd", "settled_microusd", "updated_at"])
        reservation.status = BudgetReservation.Status.SETTLED
        reservation.settled_input_tokens = input_tokens
        reservation.settled_output_tokens = output_tokens
        reservation.settled_cost_microusd = actual_cost
        reservation.save(
            update_fields=[
                "status",
                "settled_input_tokens",
                "settled_output_tokens",
                "settled_cost_microusd",
                "updated_at",
            ]
        )
        BudgetLedgerRecord.objects.create(
            run_id=run_id,
            reservation=reservation,
            model_profile_id_snapshot=reservation.model_profile.profile_id,
            model_profile_revision=reservation.model_profile.revision,
            entry_type=BudgetLedgerRecord.EntryType.SETTLED,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_microusd=actual_cost,
        )
        return reservation


def _expire_period_reservations(*, period: BudgetPeriod, now: datetime) -> int:
    expired = list(
        BudgetReservation.objects.select_for_update().filter(
            period=period,
            status=BudgetReservation.Status.RESERVED,
            expires_at__lte=now,
        )
    )
    for reservation in expired:
        reservation.status = BudgetReservation.Status.RELEASED
        reservation.save(update_fields=["status", "updated_at"])
        period.reserved_microusd -= reservation.reserved_cost_microusd
        BudgetLedgerRecord.objects.create(
            run_id=reservation.run_id,
            reservation=reservation,
            model_profile_id_snapshot=reservation.model_profile.profile_id,
            model_profile_revision=reservation.model_profile.revision,
            entry_type=BudgetLedgerRecord.EntryType.RELEASED,
            cost_microusd=reservation.reserved_cost_microusd,
        )
    if expired:
        period.save(update_fields=["reserved_microusd", "updated_at"])
    return len(expired)


def expire_reservations(*, now: datetime | None = None) -> int:
    current = now or timezone.now()
    total = 0
    with transaction.atomic():
        period_ids = BudgetReservation.objects.filter(
            status=BudgetReservation.Status.RESERVED, expires_at__lte=current
        ).values_list("period_id", flat=True)
        for period in BudgetPeriod.objects.select_for_update().filter(pk__in=period_ids):
            total += _expire_period_reservations(period=period, now=current)
    return total
