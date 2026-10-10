"""Fail-closed human approval and durable, at-most-once provider dispatch.

Callers must pass the authenticated request user. These are service functions;
they do not authenticate HTTP requests or provision an approver identity.
"""

import hashlib
import os
from dataclasses import asdict
from datetime import timedelta

from agents.models import AgentRun, DraftOperation
from agents.tool_schemas import bounded_json
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.utils import timezone
from identity.models import AdministratorProfile, AdministratorSession
from launchloop.models import DemoActor, Workflow

from integrations import iterable_drafts, iterable_templates
from integrations.eventbrite_drafts import (
    EventbriteDraftAdapter,
    EventbriteDraftError,
    EventbriteDraftReceipt,
    compute_request_digest,
    validate_payload,
)
from integrations.models import (
    DraftExecution,
    IntegrationConnection,
    Provider,
    SecretStatus,
    TemplateExecution,
)
from integrations.secret_store import PostgresSecretStore
from integrations.types import SecretReference


def digest(value):
    return hashlib.sha256(bounded_json(value).encode()).hexdigest()


def _actor(user, role):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Authenticated active user required.")
    actor = DemoActor.objects.filter(user_id=user.pk, role=role).first()
    if actor is None:
        raise PermissionDenied("User-bound actor role required.")
    return actor


def validate_intent(intent, run):
    capability = intent.proposal.capability
    workflow = Workflow.objects.get(pk=intent.workflow_id)
    revision_digest = digest(workflow.revision.snapshot)
    action = digest(
        {
            "workflow_id": str(workflow.pk),
            "revision_id": intent.revision_id,
            "revision_digest": revision_digest,
            "provider": intent.provider,
            "operation_kind": intent.operation_kind,
            "proposal_digest": intent.proposal.digest,
        }
    )
    idem = digest(
        [str(workflow.pk), intent.revision_id, intent.actor_id, intent.operation_kind, action]
    )
    if (
        run.status != AgentRun.Status.SUCCEEDED
        or not run.hermes_lane
        or run.workflow_id != workflow.pk
        or run.event_revision_id != workflow.revision_id
        or run.package_hash != workflow.package_hash
        or run.hermes_binding.actor_id != intent.actor_id
        or run.hermes_binding.revision_digest != revision_digest
        or run.control.capability_id != capability.pk
        or capability.workflow_id != workflow.pk
        or capability.revision_id != workflow.revision_id
        or capability.actor_id != intent.actor_id
        or capability.audience != "civicloop-hermes"
        or capability.correlation_id != run.hermes_binding.correlation_id
        or (
            "request_eventbrite_draft"
            if intent.provider == "eventbrite"
            else "request_iterable_drafts"
        )
        not in capability.tools
        or capability.revision_digest != revision_digest
        or intent.revision_id != workflow.revision_id
        or intent.proposal.kind != "proposal"
        or intent.proposal.digest != digest(intent.proposal.content)
        or (intent.provider, intent.operation_kind)
        not in {
            ("eventbrite", "create_eventbrite_draft"),
            ("iterable", "create_iterable_email_draft"),
            ("iterable", "create_iterable_reminder_draft"),
        }
        or intent.status != "pending"
        or intent.approval_id
        or intent.receipt is not None
        or intent.action_digest != action
        or intent.idempotency_key != idem
    ):
        raise PermissionDenied("Intent is not an accepted current provider proposal.")
    if intent.provider == "iterable":
        from agents.tool_schemas import PROPOSAL, InvalidToolArguments, validate

        try:
            validate(intent.proposal.content, PROPOSAL)
        except InvalidToolArguments:
            raise PermissionDenied("Accepted Iterable content schema required.") from None
    return revision_digest


def _review(intent, run, action, organization_id, event_id, payload, expected):
    revision_digest = validate_intent(intent, run)
    request = compute_request_digest(action, organization_id, event_id or None, payload)
    return request, digest(
        {
            "intent": str(intent.pk),
            "run": str(run.pk),
            "revision": intent.revision_id,
            "revision_digest": revision_digest,
            "action_digest": intent.action_digest,
            "idempotency_key": intent.idempotency_key,
            "request_digest": request,
            "expected_readback_digest": expected,
        }
    )


def validate_execution(operation, *, require_live_approval=True):
    try:
        if operation.intent.provider == "iterable":
            request, review = _iterable_review(operation)
        else:
            if operation.provider_configuration:
                raise PermissionDenied("Unexpected provider configuration.")
            request, review = _review(
                operation.intent,
                operation.run,
                operation.action,
                operation.organization_id,
                operation.event_id,
                operation.payload,
                operation.expected_readback_digest,
            )
    except EventbriteDraftError, iterable_drafts.IterableDraftError, KeyError, TypeError:
        raise PermissionDenied("Reviewed provider payload is invalid.") from None
    if request != operation.request_digest or review != operation.review_digest:
        raise PermissionDenied("Reviewed provider payload changed.")
    if _actor(operation.submitter, DemoActor.Role.OPERATOR).pk != operation.intent.actor_id:
        raise PermissionDenied("Execution submitter must match the accepted intent actor.")
    if operation.approver_id:
        if not operation.approval_session_id:
            raise PermissionDenied("Owner approval session proof required.")
        if require_live_approval:
            _session(operation.approver, operation.approval_session_id)
        else:
            proof = operation.approval_session
            if (
                not operation.approver.is_active
                or proof.profile.status != "active"
                or proof.profile.user_id != operation.approver_id
                or proof.recovery_restricted
                or not proof.mfa_verified_at
                or proof.mfa_verified_at > operation.approved_at
                or not proof.expires_at
                or operation.approved_at >= proof.expires_at
                or not proof.absolute_expires_at
                or operation.approved_at >= proof.absolute_expires_at
                or (proof.revoked_at and proof.revoked_at <= operation.approved_at)
            ):
                raise PermissionDenied("Original owner approval proof is invalid.")


@transaction.atomic
def submit_draft(
    *,
    user,
    intent_id,
    run_id,
    action,
    organization_id="",
    payload,
    event_id="",
    expected_readback_digest="",
    provider_configuration=None,
):
    if DraftOperation.objects.get(pk=intent_id).provider == "iterable":
        return _submit_iterable_campaign(
            user=user,
            intent_id=intent_id,
            run_id=run_id,
            action=action,
            organization_id=organization_id,
            event_id=event_id,
            expected=expected_readback_digest,
            payload=payload,
            configuration=provider_configuration,
        )
    if provider_configuration:
        raise PermissionDenied("Eventbrite does not accept Iterable configuration.")
    actor = _actor(user, DemoActor.Role.OPERATOR)
    intent = DraftOperation.objects.select_for_update().get(pk=intent_id)
    Workflow.objects.select_for_update().get(pk=intent.workflow_id)
    run = AgentRun.objects.get(pk=run_id)
    if actor.pk != intent.actor_id:
        raise PermissionDenied("Only the accepted intent operator can submit.")
    if action == "update" and (
        len(expected_readback_digest) != 64
        or any(c not in "0123456789abcdef" for c in expected_readback_digest)
    ):
        raise PermissionDenied("Update requires exact provider revision readback.")
    if action == "create" and expected_readback_digest:
        raise PermissionDenied("Create has no existing provider revision.")
    payload = validate_payload(payload, create=action == "create")
    request, review = _review(
        intent, run, action, organization_id, event_id, payload, expected_readback_digest
    )
    existing = DraftExecution.objects.filter(intent=intent).first()
    if existing:
        if existing.review_digest != review or existing.submitter_id != user.pk:
            raise PermissionDenied("Intent already submitted with a different review.")
        return existing
    return DraftExecution.objects.create(
        intent=intent,
        run=run,
        submitter=user,
        action=action,
        organization_id=organization_id,
        event_id=event_id,
        payload=payload,
        expected_readback_digest=expected_readback_digest,
        request_digest=request,
        review_digest=review,
    )


@transaction.atomic
def approve_draft(*, user, administrator_session_id, operation_id, review_digest, revision_id):
    session = _session(user, administrator_session_id)
    operation = DraftExecution.objects.select_for_update().get(pk=operation_id)
    Workflow.objects.select_for_update().get(pk=operation.intent.workflow_id)
    validate_execution(operation)
    if (
        operation.status != "pending"
        or review_digest != operation.review_digest
        or revision_id != operation.intent.revision_id
    ):
        raise PermissionDenied("Exact current owner review required.")
    operation.approver = user
    operation.approval_session = session
    operation.approved_at = timezone.now()
    operation.status = "approved"
    operation.save()
    return operation


def _enabled():
    if os.environ.get("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED") != "true":
        raise PermissionDenied("Eventbrite draft writes are disabled.")


def _session(user, session_id):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Authenticated owner required.")
    session = AdministratorSession.objects.filter(
        pk=session_id,
        profile__user_id=user.pk,
        profile__status=AdministratorProfile.Status.ACTIVE,
        profile__user__is_active=True,
        recovery_restricted=False,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
        absolute_expires_at__gt=timezone.now(),
        mfa_verified_at__isnull=False,
    ).first()
    if not session:
        raise PermissionDenied("Active full owner session required.")
    return session


def _reference(provider=Provider.EVENTBRITE):
    connection = IntegrationConnection.objects.get(provider=provider)
    secret = connection.secret
    if connection.state != "healthy" or not secret or secret.status != SecretStatus.ACTIVE:
        raise PermissionDenied("Healthy Eventbrite connection required.")
    return SecretReference(
        id=secret.id, provider=secret.provider, scope=secret.scope, version=secret.version
    )


def _store_receipt(operation, receipt):
    if (
        not isinstance(receipt, EventbriteDraftReceipt)
        or receipt.request_digest != operation.request_digest
        or receipt.outcome not in {"CONFIRMED", "UNKNOWN"}
        or (
            operation.action == "update"
            and receipt.provider_id
            and receipt.provider_id != operation.event_id
        )
        or (
            operation.provider_id
            and receipt.provider_id
            and receipt.provider_id != operation.provider_id
        )
        or (
            receipt.outcome == "CONFIRMED"
            and (
                not receipt.provider_id
                or receipt.provider_status != "draft"
                or not receipt.readback_digest
            )
        )
    ):
        raise ValueError("Invalid typed draft receipt.")
    operation.receipt = asdict(receipt)
    operation.provider_id = receipt.provider_id or operation.provider_id
    operation.status = "succeeded" if receipt.outcome == "CONFIRMED" else "unknown"
    operation.error_category = receipt.error_category or ""
    operation.completed_at = timezone.now()
    operation.save()
    return operation


def execute_draft(*, user, administrator_session_id, operation_id, adapter=None, store=None):
    if DraftExecution.objects.get(pk=operation_id).intent.provider == "iterable":
        return _execute_iterable(
            user=user,
            administrator_session_id=administrator_session_id,
            operation_id=operation_id,
            adapter=adapter,
            store=store,
        )
    _enabled()
    session = _session(user, administrator_session_id)
    reference = _reference()
    if connection.in_atomic_block:
        raise PermissionDenied("Dispatch requires an independent committed claim.")
    with transaction.atomic(durable=True):
        operation = DraftExecution.objects.select_for_update().get(pk=operation_id)
        Workflow.objects.select_for_update().get(pk=operation.intent.workflow_id)
        validate_execution(operation)
        if user.pk != operation.approver_id:
            raise PermissionDenied("Dispatch must be bound to the approving owner.")
        if operation.status != "approved" or not operation.approver_id:
            raise PermissionDenied("Operation cannot be dispatched again.")
        operation.status = "executing"
        operation.claimed_at = timezone.now()
        if operation.action == "update":
            operation.provider_id = operation.event_id
        operation.save()
    try:
        with (store or PostgresSecretStore()).lease(
            reference,
            caller_id=session.id,
            workflow_id=operation.intent.workflow_id,
            execution_id=operation.pk,
            execution_kind="draft",
            purpose="eventbrite_draft_write",
            ttl=timedelta(seconds=60),
        ) as credential:
            _enabled()
            validate_execution(operation)
            client = adapter or EventbriteDraftAdapter()
            kwargs = {"organization_id": operation.organization_id, "payload": operation.payload}
            if operation.action == "create":
                receipt = client.create(credential, **kwargs)
            else:
                receipt = client.update(
                    credential,
                    event_id=operation.event_id,
                    expected_readback_digest=operation.expected_readback_digest,
                    **kwargs,
                )
        return _store_receipt(operation, receipt)
    except EventbriteDraftError as exc:
        definite = exc.category in {
            "provider_validation",
            "authentication",
            "forbidden",
            "not_found",
            "rate_limited",
            "redirect",
            "invalid_request",
            "invalid_credential",
            "stale_revision",
            "unsafe_provider_state",
            "identity_mismatch",
        }
        DraftExecution.objects.filter(pk=operation.pk).update(
            status="failed" if definite else "unknown",
            error_category=exc.category if definite else "dispatch_unconfirmed",
            completed_at=timezone.now(),
        )
        return DraftExecution.objects.get(pk=operation.pk)
    except Exception:
        # No exception text or provider response is retained. A crash leaves executing;
        # both executing and unknown forbid resend, even when no ID was returned.
        DraftExecution.objects.filter(pk=operation.pk).update(
            status="unknown", error_category="dispatch_unconfirmed", completed_at=timezone.now()
        )
        return DraftExecution.objects.get(pk=operation.pk)


def reconcile_draft(*, user, administrator_session_id, operation_id, adapter=None, store=None):
    if connection.in_atomic_block:
        raise PermissionDenied("Reconciliation requires an independent committed claim.")
    if DraftExecution.objects.get(pk=operation_id).intent.provider == "iterable":
        return _execute_iterable(
            user=user,
            administrator_session_id=administrator_session_id,
            operation_id=operation_id,
            adapter=adapter,
            store=store,
            reconcile=True,
        )
    _enabled()
    session = _session(user, administrator_session_id)
    operation = DraftExecution.objects.get(pk=operation_id)
    validate_execution(operation, require_live_approval=False)
    if user.pk != operation.approver_id:
        raise PermissionDenied("Reconciliation must be bound to the approving owner.")
    if operation.status not in {"unknown", "executing"} or not operation.provider_id:
        raise PermissionDenied("Reconciliation requires an already recorded provider ID.")
    with (store or PostgresSecretStore()).lease(
        _reference(),
        caller_id=session.id,
        workflow_id=operation.intent.workflow_id,
        execution_id=operation.pk,
        execution_kind="draft",
        purpose="eventbrite_draft_write",
        ttl=timedelta(seconds=60),
    ) as credential:
        receipt = (adapter or EventbriteDraftAdapter()).reconcile(
            credential,
            organization_id=operation.organization_id,
            event_id=operation.provider_id,
            payload=operation.payload,
            request_digest=operation.request_digest,
            action=operation.action,
        )
    return _store_receipt(operation, receipt)


def _iterable_enabled():
    if os.environ.get("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED") != "true":
        raise PermissionDenied("Iterable draft writes are disabled.")


def _iterable_review(operation):
    revision_digest = validate_intent(operation.intent, operation.run)
    configuration = operation.provider_configuration
    if type(configuration) is not dict:
        raise PermissionDenied("Exact Iterable configuration required.")
    if (
        operation.action != "create"
        or operation.organization_id
        or operation.event_id
        or operation.expected_readback_digest
    ):
        raise PermissionDenied("Iterable only supports create-only operations.")
    if isinstance(operation, TemplateExecution):
        if set(configuration) != {"region"}:
            raise PermissionDenied("Exact template region required.")
        payload = operation.payload
        expected = iterable_templates.author_payload(
            operation.intent,
            {
                key: payload[key]
                for key in ("fromEmail", "fromName", "replyToEmail", "messageTypeId")
            },
        )
        if payload != expected:
            raise PermissionDenied("Template content must match the accepted Hermes proposal.")
        request = iterable_templates.compute_request_digest(configuration["region"], payload)
    else:
        if set(configuration) != {"region", "expected_template_digest"}:
            raise PermissionDenied("Exact campaign region and template digest required.")
        template = TemplateExecution.objects.get(intent=operation.intent)
        validate_execution(template, require_live_approval=False)
        if (
            template.status != "succeeded"
            or not template.receipt
            or template.receipt.get("outcome") != "CONFIRMED"
            or template.provider_configuration["region"] != configuration["region"]
            or template.receipt.get("readback_digest") != configuration["expected_template_digest"]
            or type(operation.payload.get("templateId")) is not int
            or str(operation.payload["templateId"]) != template.provider_id
        ):
            raise PermissionDenied("Campaign requires the confirmed owned Hermes template.")
        request = iterable_drafts.compute_request_digest(
            configuration["region"], operation.payload, configuration["expected_template_digest"]
        )
    return request, digest(
        {
            "intent": str(operation.intent_id),
            "run": str(operation.run_id),
            "revision": operation.intent.revision_id,
            "revision_digest": revision_digest,
            "action_digest": operation.intent.action_digest,
            "idempotency_key": operation.intent.idempotency_key,
            "step": "template" if isinstance(operation, TemplateExecution) else "campaign",
            "request_digest": request,
        }
    )


def _new_iterable_operation(*, model, user, intent_id, run_id, payload, configuration):
    actor = _actor(user, DemoActor.Role.OPERATOR)
    intent = DraftOperation.objects.select_for_update().get(pk=intent_id)
    Workflow.objects.select_for_update().get(pk=intent.workflow_id)
    if intent.provider != "iterable" or actor.pk != intent.actor_id:
        raise PermissionDenied("Accepted Iterable intent operator required.")
    operation = model(
        intent=intent,
        run=AgentRun.objects.get(pk=run_id),
        submitter=user,
        action="create",
        organization_id="",
        event_id="",
        payload=payload,
        provider_configuration=configuration,
    )
    try:
        operation.request_digest, operation.review_digest = _iterable_review(operation)
    except iterable_drafts.IterableDraftError, KeyError, TypeError, TemplateExecution.DoesNotExist:
        raise PermissionDenied("Invalid reviewed Iterable request.") from None
    existing = model.objects.filter(intent=intent).first()
    if existing:
        if existing.review_digest != operation.review_digest or existing.submitter_id != user.pk:
            raise PermissionDenied("Intent already submitted with another review.")
        return existing
    operation.save()
    return operation


@transaction.atomic
def submit_template(*, user, intent_id, run_id, sender, region):
    intent = DraftOperation.objects.get(pk=intent_id)
    try:
        payload = iterable_templates.author_payload(intent, sender)
    except iterable_drafts.IterableDraftError, KeyError, TypeError:
        raise PermissionDenied("Invalid template sender or Hermes content.") from None
    return _new_iterable_operation(
        model=TemplateExecution,
        user=user,
        intent_id=intent_id,
        run_id=run_id,
        payload=payload,
        configuration={"region": region},
    )


def _submit_iterable_campaign(
    *, user, intent_id, run_id, action, organization_id, event_id, expected, payload, configuration
):
    if action != "create" or organization_id or event_id or expected:
        raise PermissionDenied("Iterable campaign creation has no Eventbrite identity.")
    return _new_iterable_operation(
        model=DraftExecution,
        user=user,
        intent_id=intent_id,
        run_id=run_id,
        payload=payload,
        configuration=configuration,
    )


@transaction.atomic
def approve_template(*, user, administrator_session_id, operation_id, review_digest, revision_id):
    session = _session(user, administrator_session_id)
    operation = TemplateExecution.objects.select_for_update().get(pk=operation_id)
    Workflow.objects.select_for_update().get(pk=operation.intent.workflow_id)
    validate_execution(operation)
    if (
        operation.status != "pending"
        or review_digest != operation.review_digest
        or revision_id != operation.intent.revision_id
    ):
        raise PermissionDenied("Exact current owner template review required.")
    operation.approver = user
    operation.approval_session = session
    operation.approved_at = timezone.now()
    operation.status = "approved"
    operation.save()
    return operation


def _iterable_store_receipt(operation, receipt):
    template = isinstance(operation, TemplateExecution)
    if (
        not isinstance(receipt, iterable_drafts.IterableDraftReceipt)
        or receipt.request_digest != operation.request_digest
        or receipt.outcome not in {"CONFIRMED", "UNKNOWN"}
        or (
            operation.provider_id
            and receipt.provider_id
            and operation.provider_id != receipt.provider_id
        )
        or (
            receipt.outcome == "CONFIRMED"
            and (
                not receipt.provider_id
                or not receipt.readback_digest
                or receipt.provider_status not in ({"template"} if template else {"Draft", "Ready"})
            )
        )
    ):
        raise ValueError("Invalid typed Iterable receipt.")
    operation.receipt = asdict(receipt)
    operation.provider_id = receipt.provider_id or operation.provider_id
    operation.status = "succeeded" if receipt.outcome == "CONFIRMED" else "unknown"
    operation.error_category = receipt.error_category or ""
    operation.completed_at = timezone.now()
    operation.save()
    return operation


def execute_template(**kwargs):
    return _execute_iterable(model=TemplateExecution, **kwargs)


def reconcile_template(**kwargs):
    return _execute_iterable(model=TemplateExecution, reconcile=True, **kwargs)


def _execute_iterable(
    *,
    user,
    administrator_session_id,
    operation_id,
    model=DraftExecution,
    adapter=None,
    store=None,
    reconcile=False,
):
    _iterable_enabled()
    session = _session(user, administrator_session_id)
    if connection.in_atomic_block:
        raise PermissionDenied("Dispatch requires an independent committed claim.")
    with transaction.atomic(durable=True):
        operation = model.objects.select_for_update().get(pk=operation_id)
        Workflow.objects.select_for_update().get(pk=operation.intent.workflow_id)
        validate_execution(operation, require_live_approval=not reconcile)
        if operation.approver_id != user.pk:
            raise PermissionDenied("Iterable dispatch requires the approving owner.")
        if reconcile:
            if operation.status not in {"unknown", "executing"} or not operation.claimed_at:
                raise PermissionDenied("Only a previously claimed write can reconcile.")
            if model is DraftExecution and not operation.provider_id:
                raise PermissionDenied("Campaign reconciliation requires its recorded ID.")
        else:
            if operation.status != "approved" or not operation.approver_id:
                raise PermissionDenied("Operation cannot be dispatched again.")
            operation.status = "executing"
            operation.claimed_at = timezone.now()
            operation.save()
    try:
        reference = _reference(Provider.ITERABLE)
        integration = IntegrationConnection.objects.get(provider=Provider.ITERABLE)
        if integration.configuration.get("region") != operation.provider_configuration["region"]:
            raise PermissionDenied("Reviewed region does not match the healthy connection.")
        with (store or PostgresSecretStore()).lease(
            reference,
            caller_id=session.pk,
            workflow_id=operation.intent.workflow_id,
            execution_id=operation.pk,
            execution_kind="template" if model is TemplateExecution else "draft",
            purpose="iterable_draft_write",
            ttl=timedelta(seconds=60),
        ) as credential:
            _iterable_enabled()
            _session(user, administrator_session_id)
            validate_execution(operation, require_live_approval=not reconcile)
            kwargs = {
                "payload": operation.payload,
                "region": operation.provider_configuration["region"],
            }
            if model is TemplateExecution:
                client = adapter or iterable_templates.IterableTemplateAdapter()
                if reconcile:
                    kwargs.update(
                        provider_id=operation.provider_id or None,
                        request_digest=operation.request_digest,
                    )
            else:
                client = adapter or iterable_drafts.IterableDraftAdapter()
                kwargs["expected_template_digest"] = operation.provider_configuration[
                    "expected_template_digest"
                ]
                if reconcile:
                    kwargs.update(
                        campaign_id=operation.provider_id, request_digest=operation.request_digest
                    )
            receipt = (
                client.reconcile(credential, **kwargs)
                if reconcile
                else client.create(credential, **kwargs)
            )
        return _iterable_store_receipt(operation, receipt)
    except Exception:
        # Once claimed, errors never grant permission to send another POST.
        model.objects.filter(pk=operation.pk).update(
            status="unknown", error_category="dispatch_unconfirmed", completed_at=timezone.now()
        )
        return model.objects.get(pk=operation.pk)
