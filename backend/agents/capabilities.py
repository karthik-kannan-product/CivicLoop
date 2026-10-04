"""Server-issued, short-lived authority; no claims or actor IDs in bearer tokens."""

import base64
import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from django.core import signing
from django.db import transaction
from django.utils import timezone
from launchloop.models import AuditEvent, DemoActor, Workflow

from agents.models import WorkflowCapability
from agents.tool_schemas import bounded_json

AUDIENCE = "civicloop-hermes"
TOOLS = frozenset(
    {
        "get_event_revision",
        "get_policy_context",
        "request_clarification",
        "propose_campaign_drafts",
        "validate_proposal",
        "request_eventbrite_draft",
        "request_iterable_drafts",
        "get_operation_status",
    }
)


class AuthorizationDenied(Exception):
    def __init__(self):
        super().__init__("Authorization failed.")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signer():
    return signing.Signer(salt="civicloop.mcp.capability.v1", sep=".")


def verify_token(token: str) -> None:
    try:
        if not token.startswith("cap_"):
            raise ValueError
        encoded = token[4:]
        signed = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        signer().unsign(signed)
    except ValueError, UnicodeError, signing.BadSignature:
        raise AuthorizationDenied() from None


@transaction.atomic
def issue_workflow_capability(
    *,
    workflow_id: UUID,
    revision_id: int,
    actor_id: str,
    tools: frozenset[str],
    lifetime_seconds: int,
) -> str:
    if (
        type(lifetime_seconds) is not int
        or type(revision_id) is not int
        or not 1 <= lifetime_seconds <= 300
        or not isinstance(tools, frozenset)
        or not tools
        or not tools <= TOOLS
    ):
        raise AuthorizationDenied()
    workflow = Workflow.objects.select_for_update().filter(pk=workflow_id).first()
    actor = DemoActor.objects.select_related("user").filter(pk=actor_id).first()
    if (
        workflow is None
        or workflow.revision_id != revision_id
        or workflow.revision.event_id != workflow.event_id
        or actor is None
        or actor.user is None
        or not actor.user.is_active
        or actor.role != DemoActor.Role.OPERATOR
    ):
        raise AuthorizationDenied()
    now = timezone.now()
    signed = signer().sign(secrets.token_urlsafe(32))
    token = "cap_" + base64.urlsafe_b64encode(signed.encode()).decode().rstrip("=")
    record = WorkflowCapability.objects.create(
        token_digest=token_digest(token),
        revision_digest=hashlib.sha256(
            bounded_json(workflow.revision.snapshot).encode()
        ).hexdigest(),
        workflow=workflow,
        revision_id=revision_id,
        actor=actor,
        tools=sorted(tools),
        audience=AUDIENCE,
        issued_at=now,
        expires_at=now + timedelta(seconds=lifetime_seconds),
    )
    AuditEvent.objects.create(
        actor=actor,
        action="mcp.capability_issued",
        target_type="workflow",
        target_id=str(workflow.id),
        details={"capability_id": str(record.id)},
    )
    return token


@transaction.atomic
def revoke_workflow_capability(*, capability: str) -> None:
    record = (
        WorkflowCapability.objects.select_for_update()
        .filter(token_digest=token_digest(capability))
        .first()
    )
    if record is None:
        raise AuthorizationDenied()
    record.revoked_at = timezone.now()
    record.save(update_fields=["revoked_at"])
    AuditEvent.objects.create(
        actor=record.actor,
        action="mcp.capability_revoked",
        target_type="workflow",
        target_id=str(record.workflow_id),
        details={"capability_id": str(record.id)},
    )
