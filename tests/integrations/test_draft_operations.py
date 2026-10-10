import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from agents.models import (
    AgentRun,
    AgentRunControl,
    DraftOperation,
    HermesRunBinding,
    MCPSubmission,
    WorkflowCapability,
)
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.utils import timezone
from integrations import draft_operations as service
from integrations.eventbrite_drafts import EventbriteDraftReceipt
from integrations.models import DraftExecution

from tests.agents.test_runs import create_run, create_workflow

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def accepted():
    workflow, revision, operator, approver = create_workflow()
    run = create_run(workflow=workflow)
    AgentRun.objects.filter(pk=run.pk).update(
        hermes_lane=True, status="succeeded", started_at=timezone.now(), finished_at=timezone.now()
    )
    run.refresh_from_db()
    binding = HermesRunBinding.objects.create(run=run, actor=operator)
    capability = WorkflowCapability.objects.create(
        token_digest="1" * 64,
        revision_digest=binding.revision_digest,
        workflow=workflow,
        revision=revision,
        actor=operator,
        correlation_id=binding.correlation_id,
        tools=["request_eventbrite_draft"],
        expires_at=timezone.now() + timedelta(seconds=60),
    )
    AgentRunControl.objects.create(run=run, capability=capability)
    proposal = MCPSubmission.objects.create(
        capability=capability,
        kind="proposal",
        content={"synthetic": True},
        digest=service.digest({"synthetic": True}),
    )
    action = service.digest(
        {
            "workflow_id": str(workflow.pk),
            "revision_id": revision.pk,
            "revision_digest": binding.revision_digest,
            "provider": "eventbrite",
            "operation_kind": "create_eventbrite_draft",
            "proposal_digest": proposal.digest,
        }
    )
    intent = DraftOperation.objects.create(
        workflow=workflow,
        revision=revision,
        actor=operator,
        proposal=proposal,
        provider="eventbrite",
        operation_kind="create_eventbrite_draft",
        action_digest=action,
        idempotency_key=service.digest(
            [str(workflow.pk), revision.pk, operator.pk, "create_eventbrite_draft", action]
        ),
    )
    payload = {
        "event": {
            "name": {"html": "Synthetic event"},
            "start": {"utc": "2027-01-01T12:00:00Z", "timezone": "UTC"},
            "end": {"utc": "2027-01-01T13:00:00Z", "timezone": "UTC"},
            "currency": "USD",
        }
    }
    return workflow, operator, approver, run, intent, payload


def submit(accepted):
    _, operator, _, run, intent, payload = accepted
    return service.submit_draft(
        user=operator.user,
        intent_id=intent.pk,
        run_id=run.pk,
        action="create",
        organization_id="123",
        payload=payload,
    )


def approve(accepted):
    operation = submit(accepted)
    return service.approve_draft(
        administrator_session_id=owner_session(accepted).pk,
        user=accepted[1].user,
        operation_id=operation.pk,
        review_digest=operation.review_digest,
        revision_id=accepted[0].revision_id,
    )


def test_exact_owner_approval_and_idempotent_submission(accepted):
    operation = submit(accepted)
    assert submit(accepted).pk == operation.pk
    with pytest.raises(PermissionDenied):
        service.approve_draft(
            administrator_session_id=owner_session(accepted).pk,
            user=accepted[2].user,
            operation_id=operation.pk,
            review_digest=operation.review_digest,
            revision_id=accepted[0].revision_id,
        )
    with pytest.raises(PermissionDenied):
        service.approve_draft(
            administrator_session_id=owner_session(accepted).pk,
            user=accepted[1].user,
            operation_id=operation.pk,
            review_digest="0" * 64,
            revision_id=accepted[0].revision_id,
        )
    operation = approve(accepted)
    assert operation.status == "approved"
    accepted[4].refresh_from_db()
    assert accepted[4].status == "pending" and accepted[4].approval_id is None


@pytest.mark.parametrize("field", ["action_digest", "idempotency_key"])
def test_intent_digest_tampering_denied(accepted, field):
    DraftOperation.objects.filter(pk=accepted[4].pk).update(**{field: "f" * 64})
    with pytest.raises(PermissionDenied):
        submit(accepted)


def test_stale_revision_and_payload_denied(accepted):
    operation = approve(accepted)
    operation.payload = {"event": {"summary": "mutated"}}
    with pytest.raises(ValidationError):
        operation.save()
    DraftExecution.objects.filter(pk=operation.pk).update(payload={"event": {"summary": "mutated"}})
    with pytest.raises(PermissionDenied):
        service.validate_execution(DraftExecution.objects.get(pk=operation.pk))


def test_stale_revision_denied_on_approval(accepted):
    operation = submit(accepted)
    workflow = accepted[0]
    revision = type(workflow.revision).objects.create(
        event=workflow.event, version=2, snapshot={"changed": True}, author=accepted[1]
    )
    workflow.revision = revision
    workflow.save()
    with pytest.raises(PermissionDenied):
        service.approve_draft(
            administrator_session_id=owner_session(accepted).pk,
            user=accepted[1].user,
            operation_id=operation.pk,
            review_digest=operation.review_digest,
            revision_id=operation.intent.revision_id,
        )


class Store:
    @contextmanager
    def lease(self, reference, **kwargs):
        assert kwargs["purpose"] == "eventbrite_draft_write"
        yield object()


@pytest.mark.parametrize("ambiguous", [False, True])
def test_claim_commits_before_http_and_never_retries(accepted, monkeypatch, ambiguous):
    operation = approve(accepted)
    monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.setattr(service, "_session", lambda *args: SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr(service, "_reference", lambda: object())
    calls = []

    class Adapter:
        def create(self, credential, **kwargs):
            assert not connection.in_atomic_block
            assert DraftExecution.objects.get(pk=operation.pk).status == "executing"
            assert kwargs["payload"] == operation.payload
            calls.append(1)
            if ambiguous:
                raise TimeoutError("body must never be retained")
            return EventbriteDraftReceipt(
                "CONFIRMED", "456", "draft", operation.request_digest, "d" * 64
            )

    result = service.execute_draft(
        user=accepted[1].user,
        administrator_session_id=uuid.uuid4(),
        operation_id=operation.pk,
        store=Store(),
        adapter=Adapter(),
    )
    assert result.status == ("unknown" if ambiguous else "succeeded")
    with pytest.raises(PermissionDenied):
        service.execute_draft(
            user=accepted[1].user,
            administrator_session_id=uuid.uuid4(),
            operation_id=operation.pk,
            store=Store(),
            adapter=Adapter(),
        )
    assert calls == [1]


def test_disabled_and_no_arbitrary_reconciliation_id(accepted, monkeypatch):
    operation = approve(accepted)
    monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", "TRUE")
    with pytest.raises(PermissionDenied):
        service.execute_draft(
            user=accepted[1].user, administrator_session_id=uuid.uuid4(), operation_id=operation.pk
        )
    monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.setattr(service, "_session", lambda *args: object())
    DraftExecution.objects.filter(pk=operation.pk).update(
        status="unknown", claimed_at=timezone.now(), completed_at=timezone.now()
    )
    with pytest.raises(PermissionDenied):
        service.reconcile_draft(
            user=accepted[1].user, administrator_session_id=uuid.uuid4(), operation_id=operation.pk
        )


@pytest.mark.parametrize("canonical", [None, "false"])
@pytest.mark.parametrize("reconcile", [False, True])
def test_bare_eventbrite_flag_cannot_enable_dispatch(accepted, monkeypatch, canonical, reconcile):
    operation = approve(accepted)
    monkeypatch.setenv("EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.delenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", raising=False)
    if canonical is not None:
        monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", canonical)
    monkeypatch.setattr(service, "_session", lambda *args: pytest.fail("Owner admission reached"))
    monkeypatch.setattr(service, "_reference", lambda: pytest.fail("Secret admission reached"))
    dispatch = service.reconcile_draft if reconcile else service.execute_draft
    with pytest.raises(PermissionDenied, match="Eventbrite draft writes are disabled"):
        dispatch(
            user=accepted[1].user,
            administrator_session_id=uuid.uuid4(),
            operation_id=operation.pk,
            adapter=object(),
            store=object(),
        )
    operation.refresh_from_db()
    assert operation.status == "approved" and operation.claimed_at is None


@pytest.mark.parametrize("change", ["proposal", "capability", "run", "package", "inactive"])
def test_changed_accepted_authority_rejected(accepted, change):
    workflow, operator, _, run, intent, _ = accepted
    if change == "proposal":
        MCPSubmission.objects.filter(pk=intent.proposal_id).update(content={"mutated": True})
    elif change == "capability":
        WorkflowCapability.objects.filter(pk=intent.proposal.capability_id).update(
            revision_digest="0" * 64
        )
    elif change == "run":
        AgentRun.objects.filter(pk=run.pk).update(status="failed")
    elif change == "package":
        workflow.package_hash = "0" * 64
        workflow.save()
    else:
        operator.user.is_active = False
        operator.user.save()
    with pytest.raises(PermissionDenied):
        submit(accepted)


@pytest.mark.parametrize("change", ["recovery", "revoked", "no_mfa", "inactive", "expired"])
def test_full_owner_session_required(accepted, change):
    from identity.models import AdministratorProfile, AdministratorSession

    user = accepted[1].user
    profile, _ = AdministratorProfile.objects.get_or_create(
        user=user, defaults={"status": "active"}
    )
    session = AdministratorSession.objects.create(
        profile=profile,
        session_key=uuid.uuid4().hex,
        mfa_verified_at=timezone.now(),
        expires_at=timezone.now() + timedelta(minutes=5),
        absolute_expires_at=timezone.now() + timedelta(minutes=5),
        device_label="synthetic",
    )
    assert service._session(user, session.pk).pk == session.pk
    if change == "recovery":
        session.recovery_restricted = True
    elif change == "revoked":
        session.revoked_at = timezone.now()
    elif change == "no_mfa":
        session.mfa_verified_at = None
    elif change == "expired":
        session.expires_at = timezone.now() - timedelta(seconds=1)
    else:
        user.is_active = False
        user.save()
    session.save()
    with pytest.raises(PermissionDenied):
        service._session(user, session.pk)


def test_crash_after_claim_cannot_resend(accepted, monkeypatch):
    operation = approve(accepted)
    monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.setattr(service, "_session", lambda *args: object())
    monkeypatch.setattr(service, "_reference", lambda: object())
    DraftExecution.objects.filter(pk=operation.pk).update(
        status="executing", claimed_at=timezone.now()
    )
    with pytest.raises(PermissionDenied):
        service.execute_draft(
            user=accepted[1].user, administrator_session_id=uuid.uuid4(), operation_id=operation.pk
        )


@pytest.mark.parametrize("change", ["unapproved", "tampered", "recovery"])
def test_write_lease_requires_actual_approved_operation(accepted, change):
    from identity.models import AdministratorProfile, AdministratorSession
    from integrations.exceptions import SecretUnavailable
    from integrations.models import EncryptedSecret, IntegrationConnection
    from integrations.secret_store import PostgresSecretStore
    from integrations.types import SecretReference

    operation = approve(accepted)
    DraftExecution.objects.filter(pk=operation.pk).update(
        status="executing", claimed_at=timezone.now()
    )
    secret = EncryptedSecret.objects.create(
        provider="eventbrite",
        scope="draft",
        ciphertext=b"synthetic",
        nonce=b"0" * 12,
        key_id="synthetic",
    )
    IntegrationConnection.objects.create(
        provider="eventbrite",
        state="healthy",
        capabilities=["connection_test", "metadata_read"],
        secret=secret,
    )
    profile, _ = AdministratorProfile.objects.get_or_create(
        user=accepted[1].user, defaults={"status": "active"}
    )
    session = AdministratorSession.objects.create(
        profile=profile,
        session_key=uuid.uuid4().hex,
        mfa_verified_at=timezone.now(),
        expires_at=timezone.now() + timedelta(minutes=5),
        absolute_expires_at=timezone.now() + timedelta(minutes=5),
        device_label="synthetic",
    )
    reference = SecretReference(secret.pk, "eventbrite", "draft", secret.version)
    kwargs = {
        "reference": reference,
        "caller_id": session.pk,
        "workflow_id": accepted[0].pk,
        "purpose": "eventbrite_draft_write",
        "execution_id": operation.pk,
        "execution_kind": "draft",
        "ttl": timedelta(seconds=10),
    }
    # A stale operation from the same workflow must not veto this exact claim.
    import copy

    stale_intent = copy.deepcopy(operation.intent)
    stale_intent.pk = uuid.uuid4()
    stale_intent.idempotency_key = uuid.uuid4().hex * 2
    stale_intent.save()
    stale = copy.deepcopy(operation)
    stale.pk = uuid.uuid4()
    stale.intent = stale_intent
    stale.status = "unknown"
    stale.claimed_at = timezone.now()
    stale.completed_at = timezone.now()
    stale.request_digest = "0" * 64
    stale.save()
    PostgresSecretStore._validate_lease_request(**kwargs)
    with pytest.raises(SecretUnavailable):
        PostgresSecretStore._validate_lease_request(**{**kwargs, "execution_id": stale.pk})
    with pytest.raises(SecretUnavailable):
        PostgresSecretStore._validate_lease_request(**{**kwargs, "execution_id": None})
    lease_context = PostgresSecretStore().lease(**kwargs)
    if change == "unapproved":
        DraftExecution.objects.filter(pk=operation.pk).update(
            status="pending",
            approver=None,
            approved_at=None,
            approval_session=None,
            claimed_at=None,
        )
    elif change == "tampered":
        DraftExecution.objects.filter(pk=operation.pk).update(request_digest="0" * 64)
    else:
        session.recovery_restricted = True
        session.save()
    with pytest.raises(SecretUnavailable):
        PostgresSecretStore._validate_lease_request(**kwargs)
    with pytest.raises(SecretUnavailable), lease_context:
        pytest.fail("A changed claim must fail before credential decryption")


def owner_session(accepted):
    from identity.models import AdministratorProfile, AdministratorSession

    profile, _ = AdministratorProfile.objects.get_or_create(
        user=accepted[1].user, defaults={"status": "active"}
    )
    session = AdministratorSession.objects.filter(profile=profile).first()
    if session:
        return session
    return AdministratorSession.objects.create(
        profile=profile,
        session_key=uuid.uuid4().hex,
        mfa_verified_at=timezone.now(),
        expires_at=timezone.now() + timedelta(minutes=5),
        absolute_expires_at=timezone.now() + timedelta(minutes=5),
        device_label="synthetic",
    )


def test_reconcile_with_fresh_owner_session_after_approval_session_expires(accepted, monkeypatch):
    from identity.models import AdministratorSession

    operation = approve(accepted)
    now = timezone.now()
    old = operation.approval_session
    AdministratorSession.objects.filter(pk=old.pk).update(
        mfa_verified_at=now - timedelta(minutes=3), expires_at=now - timedelta(seconds=1)
    )
    DraftExecution.objects.filter(pk=operation.pk).update(
        status="unknown",
        completed_at=timezone.now(),
        provider_id="456",
        claimed_at=now - timedelta(minutes=1),
        approved_at=now - timedelta(minutes=2),
    )
    fresh = AdministratorSession.objects.create(
        profile=old.profile,
        session_key=uuid.uuid4().hex,
        mfa_verified_at=now,
        expires_at=now + timedelta(minutes=5),
        absolute_expires_at=now + timedelta(minutes=5),
        device_label="fresh synthetic",
    )
    monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.setattr(service, "_reference", lambda: object())

    class Adapter:
        def reconcile(self, credential, **kwargs):
            assert kwargs["event_id"] == "456"
            return EventbriteDraftReceipt(
                "CONFIRMED", "456", "draft", operation.request_digest, "f" * 64
            )

        def create(self, *args, **kwargs):
            pytest.fail("Reconciliation must never POST")

    result = service.reconcile_draft(
        user=accepted[1].user,
        administrator_session_id=fresh.pk,
        operation_id=operation.pk,
        adapter=Adapter(),
        store=Store(),
    )
    assert result.status == "succeeded"


@pytest.fixture
def iterable_accepted(accepted):
    from integrations.models import CAPABILITIES_BY_PROVIDER, EncryptedSecret, IntegrationConnection

    workflow, operator, _, run, intent, _ = accepted
    content = {
        "event_copy": "Event",
        "invitation": {"subject": "Invitation", "body": "Join <us>"},
        "reminder": {"subject": "Reminder", "body": "Tomorrow"},
        "social": {"body": "Join"},
    }
    MCPSubmission.objects.filter(pk=intent.proposal_id).update(
        content=content, digest=service.digest(content)
    )
    WorkflowCapability.objects.filter(pk=intent.proposal.capability_id).update(
        tools=["request_iterable_drafts"]
    )
    intent.refresh_from_db()
    kind = "create_iterable_email_draft"
    action = service.digest(
        {
            "workflow_id": str(workflow.pk),
            "revision_id": workflow.revision_id,
            "revision_digest": run.hermes_binding.revision_digest,
            "provider": "iterable",
            "operation_kind": kind,
            "proposal_digest": intent.proposal.digest,
        }
    )
    DraftOperation.objects.filter(pk=intent.pk).update(
        provider="iterable",
        operation_kind=kind,
        action_digest=action,
        idempotency_key=service.digest(
            [str(workflow.pk), workflow.revision_id, operator.pk, kind, action]
        ),
    )
    intent.refresh_from_db()
    secret = EncryptedSecret.objects.create(
        provider="iterable",
        scope="draft_write",
        ciphertext=b"synthetic",
        nonce=b"0" * 12,
        key_id="synthetic",
    )
    IntegrationConnection.objects.create(
        provider="iterable",
        state="healthy",
        secret=secret,
        configuration={"region": "us"},
        capabilities=CAPABILITIES_BY_PROVIDER["iterable"],
    )
    return accepted


def template_submit(accepted):
    return service.submit_template(
        user=accepted[1].user,
        intent_id=accepted[4].pk,
        run_id=accepted[3].pk,
        region="us",
        sender={
            "fromEmail": "sender@example.test",
            "fromName": "Sender",
            "replyToEmail": "reply@example.test",
            "messageTypeId": 7,
        },
    )


def template_approve(accepted):
    operation = template_submit(accepted)
    return service.approve_template(
        user=accepted[1].user,
        administrator_session_id=owner_session(accepted).pk,
        operation_id=operation.pk,
        review_digest=operation.review_digest,
        revision_id=accepted[0].revision_id,
    )


class IterableStore:
    @contextmanager
    def lease(self, reference, **kwargs):
        assert kwargs["purpose"] == "iterable_draft_write"
        yield object()


def template_execute(accepted, monkeypatch, *, ambiguous=False):
    from integrations.iterable_drafts import IterableDraftReceipt, template_content_digest
    from integrations.models import TemplateExecution

    operation = template_approve(accepted)
    monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", "true")
    calls = []

    class Adapter:
        def create(self, credential, **kwargs):
            assert not connection.in_atomic_block
            assert TemplateExecution.objects.get(pk=operation.pk).status == "executing"
            calls.append(1)
            if ambiguous:
                raise TimeoutError("sensitive response body")
            return IterableDraftReceipt(
                "CONFIRMED",
                "44",
                "template",
                operation.request_digest,
                template_content_digest(operation.payload),
            )

    kwargs = dict(
        user=accepted[1].user,
        administrator_session_id=owner_session(accepted).pk,
        operation_id=operation.pk,
        adapter=Adapter(),
        store=IterableStore(),
    )
    result = service.execute_template(**kwargs)
    with pytest.raises(PermissionDenied):
        service.execute_template(**kwargs)
    assert calls == [1]
    return result


@pytest.mark.parametrize("ambiguous", [False, True])
def test_iterable_template_claim_and_at_most_once(iterable_accepted, monkeypatch, ambiguous):
    result = template_execute(iterable_accepted, monkeypatch, ambiguous=ambiguous)
    assert result.status == ("unknown" if ambiguous else "succeeded")


def test_iterable_campaign_requires_template_then_exact_review(iterable_accepted, monkeypatch):
    from integrations.iterable_drafts import IterableDraftReceipt

    template = template_execute(iterable_accepted, monkeypatch)
    configuration = {
        "region": "us",
        "expected_template_digest": template.receipt["readback_digest"],
    }
    payload = {
        "name": "Invitation",
        "templateId": 44,
        "listIds": [9],
        "suppressionListIds": [10],
        "scheduleSend": False,
    }
    kwargs = dict(
        user=iterable_accepted[1].user,
        intent_id=iterable_accepted[4].pk,
        run_id=iterable_accepted[3].pk,
        action="create",
        payload=payload,
        provider_configuration=configuration,
    )
    operation = service.submit_draft(**kwargs)
    assert operation.pk != template.pk
    operation = service.approve_draft(
        user=iterable_accepted[1].user,
        administrator_session_id=owner_session(iterable_accepted).pk,
        operation_id=operation.pk,
        review_digest=operation.review_digest,
        revision_id=iterable_accepted[0].revision_id,
    )

    class Adapter:
        def create(self, credential, **kwargs):
            assert not connection.in_atomic_block
            assert DraftExecution.objects.get(pk=operation.pk).status == "executing"
            assert kwargs == {
                "payload": payload,
                "region": "us",
                "expected_template_digest": configuration["expected_template_digest"],
            }
            return IterableDraftReceipt(
                "CONFIRMED", "55", "Ready", operation.request_digest, "f" * 64
            )

    result = service.execute_draft(
        user=iterable_accepted[1].user,
        administrator_session_id=owner_session(iterable_accepted).pk,
        operation_id=operation.pk,
        adapter=Adapter(),
        store=IterableStore(),
    )
    assert result.status == "succeeded" and result.provider_id == "55"
    iterable_accepted[4].refresh_from_db()
    assert iterable_accepted[4].status == "pending" and iterable_accepted[4].receipt is None


def test_iterable_campaign_before_template_denied(iterable_accepted):
    with pytest.raises(PermissionDenied):
        service.submit_draft(
            user=iterable_accepted[1].user,
            intent_id=iterable_accepted[4].pk,
            run_id=iterable_accepted[3].pk,
            action="create",
            payload={
                "name": "Invitation",
                "templateId": 44,
                "listIds": [9],
                "suppressionListIds": [],
                "scheduleSend": False,
            },
            provider_configuration={"region": "us", "expected_template_digest": "a" * 64},
        )


@pytest.mark.parametrize("canonical", [None, "false"])
@pytest.mark.parametrize("reconcile", [False, True])
def test_bare_iterable_flag_cannot_enable_dispatch(
    iterable_accepted, monkeypatch, canonical, reconcile
):
    operation = template_approve(iterable_accepted)
    monkeypatch.setenv("ITERABLE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.delenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", raising=False)
    if canonical is not None:
        monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", canonical)
    monkeypatch.setattr(service, "_session", lambda *args: pytest.fail("Owner admission reached"))
    dispatch = service.reconcile_template if reconcile else service.execute_template
    with pytest.raises(PermissionDenied, match="Iterable draft writes are disabled"):
        dispatch(
            user=iterable_accepted[1].user,
            administrator_session_id=uuid.uuid4(),
            operation_id=operation.pk,
            adapter=object(),
            store=object(),
        )
    operation.refresh_from_db()
    assert operation.status == "approved" and operation.claimed_at is None


@pytest.mark.parametrize("change", ["content", "region", "actor", "run", "flag"])
def test_iterable_changed_authority_denied(iterable_accepted, monkeypatch, change):
    from integrations.models import TemplateExecution

    operation = template_approve(iterable_accepted)
    monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", "true")
    if change == "content":
        TemplateExecution.objects.filter(pk=operation.pk).update(
            payload={**operation.payload, "subject": "Other"}
        )
    elif change == "region":
        TemplateExecution.objects.filter(pk=operation.pk).update(
            provider_configuration={"region": "eu"}
        )
    elif change == "actor":
        DraftOperation.objects.filter(pk=operation.intent_id).update(actor=iterable_accepted[2])
    elif change == "run":
        AgentRun.objects.filter(pk=operation.run_id).update(status="failed")
    else:
        monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", "TRUE")
    with pytest.raises(PermissionDenied):
        service.execute_template(
            user=iterable_accepted[1].user,
            administrator_session_id=owner_session(iterable_accepted).pk,
            operation_id=operation.pk,
            adapter=object(),
            store=IterableStore(),
        )


@pytest.mark.parametrize("change", ["wrong_owner", "unapproved", "tampered", "flag", "recovery"])
def test_iterable_lease_requires_owned_exact_claim(iterable_accepted, monkeypatch, change):
    from integrations.exceptions import SecretUnavailable
    from integrations.models import EncryptedSecret, TemplateExecution
    from integrations.secret_store import PostgresSecretStore
    from integrations.types import SecretReference

    operation = template_approve(iterable_accepted)
    TemplateExecution.objects.filter(pk=operation.pk).update(
        status="executing", claimed_at=timezone.now()
    )
    monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", "true")
    session = owner_session(iterable_accepted)
    secret = EncryptedSecret.objects.get(provider="iterable")
    kwargs = dict(
        reference=SecretReference(secret.pk, "iterable", secret.scope, secret.version),
        caller_id=session.pk,
        workflow_id=iterable_accepted[0].pk,
        purpose="iterable_draft_write",
        execution_id=operation.pk,
        execution_kind="template",
        ttl=timedelta(seconds=10),
    )
    PostgresSecretStore._validate_lease_request(**kwargs)
    if change == "wrong_owner":
        TemplateExecution.objects.filter(pk=operation.pk).update(approver=iterable_accepted[2].user)
    elif change == "unapproved":
        TemplateExecution.objects.filter(pk=operation.pk).update(
            status="pending",
            approver=None,
            approved_at=None,
            approval_session=None,
            claimed_at=None,
        )
    elif change == "tampered":
        TemplateExecution.objects.filter(pk=operation.pk).update(request_digest="0" * 64)
    elif change == "flag":
        monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", "false")
    else:
        session.recovery_restricted = True
        session.save()
    with pytest.raises(SecretUnavailable):
        PostgresSecretStore._validate_lease_request(**kwargs)


@pytest.mark.parametrize("step", ["template", "campaign"])
def test_iterable_recovery_fresh_owner_mfa_session_get_only(iterable_accepted, monkeypatch, step):
    from identity.models import AdministratorSession
    from integrations.iterable_drafts import IterableDraftReceipt
    from integrations.models import TemplateExecution

    template = template_execute(iterable_accepted, monkeypatch)
    operation = template
    model = TemplateExecution
    if step == "campaign":
        operation = service.submit_draft(
            user=iterable_accepted[1].user,
            intent_id=iterable_accepted[4].pk,
            run_id=iterable_accepted[3].pk,
            action="create",
            payload={
                "name": "Invitation",
                "templateId": 44,
                "listIds": [9],
                "suppressionListIds": [],
                "scheduleSend": False,
            },
            provider_configuration={
                "region": "us",
                "expected_template_digest": template.receipt["readback_digest"],
            },
        )
        operation = service.approve_draft(
            user=iterable_accepted[1].user,
            administrator_session_id=owner_session(iterable_accepted).pk,
            operation_id=operation.pk,
            review_digest=operation.review_digest,
            revision_id=iterable_accepted[0].revision_id,
        )
        model = DraftExecution
    now = timezone.now()
    old = operation.approval_session
    # Both steps have valid historical proof although their original session expired.
    for ledger in (TemplateExecution, DraftExecution):
        ledger.objects.filter(intent=iterable_accepted[4]).update(
            approved_at=now - timedelta(minutes=2)
        )
    AdministratorSession.objects.filter(pk=old.pk).update(
        mfa_verified_at=now - timedelta(minutes=3), expires_at=now - timedelta(seconds=1)
    )
    model.objects.filter(pk=operation.pk).update(
        status="unknown",
        completed_at=timezone.now(),
        claimed_at=now - timedelta(minutes=1),
        provider_id="" if step == "template" else "55",
    )
    fresh = AdministratorSession.objects.create(
        profile=old.profile,
        session_key=uuid.uuid4().hex,
        mfa_verified_at=now,
        expires_at=now + timedelta(minutes=5),
        absolute_expires_at=now + timedelta(minutes=5),
        device_label="synthetic",
    )

    class Adapter:
        def create(self, *args, **kwargs):
            pytest.fail("Recovery cannot POST")

        def reconcile(self, credential, **kwargs):
            assert kwargs["request_digest"] == operation.request_digest
            if step == "template":
                assert kwargs["provider_id"] is None
            else:
                assert kwargs["campaign_id"] == "55"
            return IterableDraftReceipt(
                "CONFIRMED",
                "44" if step == "template" else "55",
                "template" if step == "template" else "Draft",
                operation.request_digest,
                template.receipt["readback_digest"] if step == "template" else "a" * 64,
            )

    reconcile = service.reconcile_template if step == "template" else service.reconcile_draft
    result = reconcile(
        user=iterable_accepted[1].user,
        administrator_session_id=fresh.pk,
        operation_id=operation.pk,
        adapter=Adapter(),
        store=IterableStore(),
    )
    assert result.status == "succeeded"


def test_iterable_replayed_submitter_rejected(iterable_accepted):
    from django.contrib.auth import get_user_model
    from integrations.models import TemplateExecution
    from launchloop.models import DemoActor

    operation = template_approve(iterable_accepted)
    other = get_user_model().objects.create_user(username="other-operator")
    DemoActor.objects.create(
        slug="other-operator",
        display_name="Other operator",
        user=other,
        role=DemoActor.Role.OPERATOR,
    )
    TemplateExecution.objects.filter(pk=operation.pk).update(submitter=other)
    with pytest.raises(PermissionDenied):
        service.validate_execution(TemplateExecution.objects.get(pk=operation.pk))


def test_iterable_write_lease_ignores_stale_unknown_history(iterable_accepted, monkeypatch):
    accepted = iterable_accepted
    provider = "iterable"
    from integrations.exceptions import SecretUnavailable
    from integrations.models import EncryptedSecret, TemplateExecution
    from integrations.secret_store import PostgresSecretStore
    from integrations.types import SecretReference

    monkeypatch.setenv("CIVICLOOP_ITERABLE_DRAFT_WRITE_ENABLED", "true")
    operation = template_approve(iterable_accepted)
    model = TemplateExecution
    secret = EncryptedSecret.objects.get(provider=provider)
    model.objects.filter(pk=operation.pk).update(status="executing", claimed_at=timezone.now())
    stale = DraftExecution.objects.create(
        intent=operation.intent,
        run=operation.run,
        submitter=operation.submitter,
        approver=operation.approver,
        approval_session=operation.approval_session,
        approved_at=operation.approved_at,
        action="create",
        organization_id="",
        payload={},
        request_digest="0" * 64,
        review_digest="0" * 64,
        provider_configuration={"region": "eu"},
        status="unknown",
        claimed_at=timezone.now(),
        completed_at=timezone.now(),
    )
    kwargs = dict(
        reference=SecretReference(secret.pk, provider, secret.scope, secret.version),
        caller_id=owner_session(accepted).pk,
        workflow_id=accepted[0].pk,
        purpose="iterable_draft_write",
        ttl=timedelta(seconds=10),
        execution_id=operation.pk,
        execution_kind="template",
    )
    PostgresSecretStore._validate_lease_request(**kwargs)
    for changed in (
        {"execution_id": stale.pk, "execution_kind": "draft"},
        {"execution_id": uuid.uuid4()},
        {"execution_id": None},
        {"workflow_id": uuid.uuid4()},
        {"execution_kind": "draft"},
    ):
        with pytest.raises(SecretUnavailable):
            PostgresSecretStore._validate_lease_request(**{**kwargs, **changed})


@pytest.mark.parametrize("kind", ["draft", "template"])
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "executing"},
        {"status": "unknown", "completed_at": timezone.now()},
        {"claimed_at": timezone.now()},
        {"completed_at": timezone.now()},
        {"receipt": {"old": "receipt"}},
        {"provider_id": "old-id"},
    ],
)
def test_execution_lifecycle_database_constraints(iterable_accepted, kind, changes):
    from django.db import IntegrityError, transaction

    operation = (
        template_approve(iterable_accepted)
        if kind == "template"
        else approved_iterable_campaign_ledger(iterable_accepted)
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        type(operation).objects.filter(pk=operation.pk).update(**changes)


def approved_iterable_campaign_ledger(accepted):
    # Construct an approved campaign ledger to test its database shape independently.
    template = template_approve(accepted)
    return DraftExecution.objects.create(
        intent=template.intent,
        run=template.run,
        submitter=template.submitter,
        approver=template.approver,
        approval_session=template.approval_session,
        approved_at=template.approved_at,
        status="approved",
        action="create",
        organization_id="",
        payload={},
        request_digest="0" * 64,
        review_digest="0" * 64,
    )


@pytest.mark.parametrize("kind", ["draft", "template"])
@pytest.mark.parametrize("reset", ["status", "claimed_at", "completed_at"])
def test_execution_claim_history_cannot_be_reset(iterable_accepted, kind, reset):
    operation = (
        template_approve(iterable_accepted)
        if kind == "template"
        else approved_iterable_campaign_ledger(iterable_accepted)
    )
    type(operation).objects.filter(pk=operation.pk).update(
        status="unknown", claimed_at=timezone.now(), completed_at=timezone.now()
    )
    operation.refresh_from_db()
    setattr(operation, reset, "approved" if reset == "status" else None)
    with pytest.raises(ValidationError, match="claim history"):
        operation.save()


def test_eventbrite_reconcile_rejects_outer_transaction_before_lease(accepted, monkeypatch):
    from django.db import transaction

    operation = approve(accepted)
    monkeypatch.setenv("CIVICLOOP_EVENTBRITE_DRAFT_WRITE_ENABLED", "true")
    monkeypatch.setattr(
        service, "_session", lambda *args: pytest.fail("No session or lease admission")
    )
    with transaction.atomic(), pytest.raises(PermissionDenied, match="independent committed claim"):
        service.reconcile_draft(
            user=accepted[1].user,
            administrator_session_id=uuid.uuid4(),
            operation_id=operation.pk,
            adapter=object(),
            store=object(),
        )
