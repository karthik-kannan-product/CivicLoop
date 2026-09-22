"""Capability-bound internal tool broker. No provider adapters are imported here."""

import hashlib
import hmac
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from launchloop.models import AuditEvent, Workflow
from observability.runtime import get_runtime

from agents.capabilities import AUDIENCE, AuthorizationDenied, token_digest, verify_token
from agents.models import DraftOperation, MCPInvocation, MCPSubmission, WorkflowCapability
from agents.tool_schemas import (
    InvalidToolArguments,
    bounded_json,
    validate_tool_arguments,
)


def authenticate_service(service_identity: str) -> None:
    try:
        path = Path(settings.CIVICLOOP_MCP_TOKEN_FILE)
        with path.open("rb") as source:
            expected = source.read(257).strip()
        supplied = service_identity.encode() if isinstance(service_identity, str) else b""
        valid = 32 <= len(expected) <= 256 and hmac.compare_digest(expected, supplied)
    except AttributeError, OSError, TypeError, ValueError:
        valid = False
    if not valid:
        raise AuthorizationDenied()


def digest(value):
    return hashlib.sha256(bounded_json(value).encode()).hexdigest()


def _audit(record, action, tool, arguments):
    AuditEvent.objects.create(
        actor=record.actor,
        action=action,
        target_type="workflow",
        target_id=str(record.workflow_id),
        details={
            "capability_id": str(record.id),
            "tool": tool,
            "correlation_id": arguments["correlation_id"],
            "request_id": arguments["request_id"],
        },
    )


def dispatch_mcp_tool(
    *, tool_name: str, arguments: dict[str, object], service_identity: str, capability: str
) -> dict[str, object]:
    try:
        authenticate_service(service_identity)
        if not isinstance(capability, str) or len(capability) > 160:
            raise AuthorizationDenied()
        verify_token(capability)
        encoded = validate_tool_arguments(tool_name, arguments)
        with get_runtime().start_span(
            "civicloop.mcp.tool",
            record_exception=False,
            set_status_on_exception=False,
            attributes={"openinference.span.kind": "TOOL", "civicloop.stage": tool_name},
        ) as span:
            result = _dispatch(tool_name, arguments, capability, encoded)
            span.set_attribute("civicloop.outcome", "succeeded")
            return result
    except AuthorizationDenied:
        # Persist outside the failed transaction; never retain untrusted identifiers or credentials.
        AuditEvent.objects.create(
            action="mcp.tool_denied",
            target_type="mcp",
            target_id="broker",
            details={"reason": "authorization_failed"},
        )
        raise
    except InvalidToolArguments:
        AuditEvent.objects.create(
            action="mcp.tool_invalid",
            target_type="mcp",
            target_id="broker",
            details={"reason": "invalid_arguments"},
        )
        raise


@transaction.atomic
def _dispatch(tool, arguments, token, encoded):
    record = (
        WorkflowCapability.objects.select_for_update(of=("self",))
        .select_related("actor__user")
        .filter(token_digest=token_digest(token))
        .first()
    )
    now = timezone.now()
    if (
        record is None
        or record.audience != AUDIENCE
        or record.revoked_at is not None
        or not record.issued_at <= now < record.expires_at
        or not 0 < (record.expires_at - record.issued_at).total_seconds() <= 300
        or tool not in record.tools
        or str(record.workflow_id) != arguments["workflow_id"]
        or record.revision_id != arguments["revision_id"]
        or record.actor_id != arguments["actor_id"]
        or record.actor.role != "operator"
        or record.actor.user is None
        or not record.actor.user.is_active
    ):
        raise AuthorizationDenied()
    workflow = (
        Workflow.objects.select_for_update().select_related("revision").get(pk=record.workflow_id)
    )
    if (
        workflow.revision_id != record.revision_id
        or digest(workflow.revision.snapshot) != record.revision_digest
        or workflow.event_id != workflow.revision.event_id
        or (
            record.correlation_id is not None
            and str(record.correlation_id) != arguments["correlation_id"]
        )
    ):
        raise AuthorizationDenied()
    if record.correlation_id is None:
        record.correlation_id = UUID(arguments["correlation_id"])
        record.save(update_fields=["correlation_id"])
    argument_digest = hashlib.sha256(encoded.encode()).hexdigest()
    idem = digest([str(record.id), arguments["idempotency_key"]])
    existing = MCPInvocation.objects.filter(
        Q(request_id=arguments["request_id"]) | Q(idempotency_digest=idem)
    ).first()
    if existing:
        if (
            existing.capability_id != record.id
            or existing.tool_name != tool
            or existing.argument_digest != argument_digest
            or existing.idempotency_digest != idem
        ):
            raise AuthorizationDenied()
        _audit(record, "mcp.tool_replayed", tool, arguments)
        return existing.result
    result = _execute(tool, arguments, record, workflow)
    result["correlation_id"] = arguments["correlation_id"]
    bounded_json(result)
    MCPInvocation.objects.create(
        capability=record,
        request_id=arguments["request_id"],
        idempotency_digest=idem,
        argument_digest=argument_digest,
        tool_name=tool,
        result=result,
    )
    _audit(record, "mcp.tool_succeeded", tool, arguments)
    return result


def _proposal(arguments, record):
    proposal = MCPSubmission.objects.filter(
        pk=arguments["proposal_id"],
        kind="proposal",
        capability__workflow_id=record.workflow_id,
        capability__revision_id=record.revision_id,
        capability__revision_digest=record.revision_digest,
        capability__actor_id=record.actor_id,
    ).first()
    if proposal is None:
        raise AuthorizationDenied()
    bounded_json(proposal.content)
    if proposal.digest != digest(proposal.content):
        raise AuthorizationDenied()
    return proposal


def _operation_payload(operation):
    return {
        "operation_id": str(operation.id),
        "workflow_id": str(operation.workflow_id),
        "revision_id": operation.revision_id,
        "provider": operation.provider,
        "operation_kind": operation.operation_kind,
        "action_digest": operation.action_digest,
        "idempotency_key": operation.idempotency_key,
        "status": operation.status,
        "approval": None,
        "receipt": None,
        "schema_version": "1.0",
    }


def _execute(tool, arguments, record, workflow):
    if tool == "get_event_revision":
        # Export only event facts, never arbitrary provider/constituent metadata.
        allowed = {
            "title",
            "date",
            "start_time",
            "end_time",
            "timezone",
            "venue_name",
            "venue_address",
            "access_instructions",
            "signup_url",
            "city",
            "region",
            "country",
        }
        snapshot = workflow.revision.snapshot
        bounded_json(snapshot)
        if type(snapshot) is not dict:
            raise InvalidToolArguments()
        return {
            "revision_id": workflow.revision_id,
            "event": {
                key: value
                for key, value in snapshot.items()
                if key in allowed and type(value) is str and len(value) <= 4000
            },
            "content_trust": "untrusted",
        }
    if tool == "get_policy_context":
        return {
            "policy_version": "mcp-drafts-v1",
            "provider_execution_allowed": False,
            "approval_required": "four_eyes_exact_revision_and_action_digest",
            "prohibited_actions": [
                "publish",
                "schedule",
                "send",
                "ticket_economics",
                "create_segment",
                "export_constituents",
            ],
        }
    if tool in {"request_clarification", "propose_campaign_drafts"}:
        kind = "clarification" if tool == "request_clarification" else "proposal"
        content = arguments["questions" if kind == "clarification" else "proposal"]
        submission = MCPSubmission.objects.create(
            capability=record, kind=kind, content=content, digest=digest(content)
        )
        return {
            f"{kind}_id": str(submission.id),
            f"{kind}_digest": submission.digest,
            "status": "pending",
            "content_trust": "untrusted",
        }
    if tool == "get_operation_status":
        operation = DraftOperation.objects.filter(
            pk=arguments["operation_id"],
            workflow_id=record.workflow_id,
            revision_id=record.revision_id,
            actor_id=record.actor_id,
            proposal__capability__revision_digest=record.revision_digest,
        ).first()
        if operation is None:
            raise AuthorizationDenied()
        return _operation_payload(operation)
    proposal = _proposal(arguments, record)
    if tool == "validate_proposal":
        # Full policy/readiness validation belongs to the deterministic workflow lane.
        from agents.tool_schemas import PROPOSAL, validate

        validate(proposal.content, PROPOSAL)
        return {
            "proposal_id": str(proposal.id),
            "schema_valid": True,
            "execution_authorized": False,
            "required_review": "deterministic_policy_and_four_eyes",
        }
    provider = "eventbrite" if tool == "request_eventbrite_draft" else "iterable"
    kinds = (
        ["create_eventbrite_draft"]
        if provider == "eventbrite"
        else ["create_iterable_email_draft", "create_iterable_reminder_draft"]
    )
    operations = []
    for kind in kinds:
        action = digest(
            {
                "workflow_id": str(record.workflow_id),
                "revision_id": record.revision_id,
                "revision_digest": record.revision_digest,
                "provider": provider,
                "operation_kind": kind,
                "proposal_digest": proposal.digest,
            }
        )
        idem = digest([str(record.workflow_id), record.revision_id, record.actor_id, kind, action])
        operation, _ = DraftOperation.objects.get_or_create(
            idempotency_key=idem,
            defaults={
                "workflow_id": record.workflow_id,
                "revision_id": record.revision_id,
                "actor_id": record.actor_id,
                "proposal": proposal,
                "provider": provider,
                "operation_kind": kind,
                "action_digest": action,
            },
        )
        operations.append(_operation_payload(operation))
    return {"operations": operations}
