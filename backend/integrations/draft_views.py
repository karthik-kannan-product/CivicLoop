"""Owner-only, create-only Eventbrite review HTTP boundary."""

import html
import json
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agents.models import AgentRun, DraftOperation
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from identity.models import AdministratorSession
from launchloop.models import DemoActor

from integrations import draft_operations as ledger
from integrations.eventbrite_drafts import EventbriteDraftError
from integrations.models import DraftExecution


def _response(value, status=200):
    return JsonResponse(value, status=status, headers={"Cache-Control": "no-store"})


def _error(status):
    return _response({"message": "The owner draft action could not be completed."}, status)


def _owner(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        raise PermissionDenied
    session = getattr(request, "administrator_session", None)
    if not isinstance(session, AdministratorSession):
        raise PermissionDenied
    ledger._session(user, session.pk)
    actor = DemoActor.objects.filter(user_id=user.pk, role=DemoActor.Role.OPERATOR).first()
    if actor is None or request.GET:
        raise PermissionDenied
    return actor, session


def _intent(request, run_id, intent_id):
    actor, session = _owner(request)
    run = AgentRun.objects.get(pk=run_id)
    intent = DraftOperation.objects.select_related("proposal", "workflow__revision").get(
        pk=intent_id
    )
    if (
        intent.actor_id != actor.pk
        or intent.provider != "eventbrite"
        or intent.operation_kind != "create_eventbrite_draft"
    ):
        raise PermissionDenied
    ledger.validate_intent(intent, run)
    return intent, run, session


def _operation(request, operation_id):
    actor, session = _owner(request)
    operation = DraftExecution.objects.select_related("intent", "run").get(pk=operation_id)
    if (
        operation.submitter_id != request.user.pk
        or operation.intent.actor_id != actor.pk
        or operation.action != "create"
    ):
        raise PermissionDenied
    ledger.validate_execution(operation, require_live_approval=False)
    return operation, session


def _body(request, keys):
    length = int(request.META.get("CONTENT_LENGTH") or "0")
    if request.content_type != "application/json" or not 0 <= length <= 4096:
        raise ValueError
    raw = request.read(4097)
    if len(raw) > 4096:
        raise ValueError

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError
    return value


def _utc(local_value, zone_name):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", local_value):
        raise ValueError
    local = datetime.fromisoformat(local_value)
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        raise ValueError from None
    first, second = local.replace(tzinfo=zone, fold=0), local.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError
    utc = first.astimezone(UTC)
    if utc.astimezone(zone).replace(tzinfo=None) != local:
        raise ValueError
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize(operation):
    return {
        "operation_id": str(operation.pk),
        "intent_id": str(operation.intent_id),
        "run_id": str(operation.run_id),
        "revision_id": operation.intent.revision_id,
        "review_digest": operation.review_digest,
        "request_digest": operation.request_digest,
        "status": operation.status,
        "organization_id": operation.organization_id,
        "payload": operation.payload,
        "provider_id": operation.provider_id or None,
        "receipt": operation.receipt,
    }


def _safe(view):
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except PermissionDenied:
            return _error(
                401
                if not getattr(request, "user", None) or not request.user.is_authenticated
                else 403
            )
        except ObjectDoesNotExist:
            return _error(404)
        except ValueError, TypeError, UnicodeError, EventbriteDraftError:
            return _error(400)
        except Exception:
            return _error(503)

    return wrapped


@require_http_methods(["GET", "POST"])
@_safe
def prepare(request, run_id, intent_id):
    intent, run, session = _intent(request, run_id, intent_id)
    existing = DraftExecution.objects.filter(intent=intent).first()
    if request.method == "GET":
        if existing:
            if existing.submitter_id != request.user.pk or existing.action != "create":
                raise PermissionDenied
            ledger.validate_execution(existing, require_live_approval=False)
            return _response({"execution": _serialize(existing)})
        snapshot = run.event_revision.snapshot
        copy = intent.proposal.content.get("event_copy", "")
        return _response(
            {
                "execution": None,
                "intent_id": str(intent.pk),
                "revision_id": intent.revision_id,
                "title": snapshot.get("title", ""),
                "timezone": snapshot.get("timezone", ""),
                "date": snapshot.get("date", ""),
                "start_time": snapshot.get("start_time", ""),
                "end_time": snapshot.get("end_time", ""),
                "proposal_copy": copy,
                "summary": copy if isinstance(copy, str) and len(copy) <= 140 else "",
            }
        )
    fields = _body(
        request,
        ["title", "summary", "organization_id", "start_local", "end_local", "timezone", "currency"],
    )
    if not all(isinstance(value, str) and value.strip() for value in fields.values()):
        raise ValueError
    if len(fields["title"]) > 200 or len(fields["summary"]) > 140 or len(fields["timezone"]) > 64:
        raise ValueError
    if not re.fullmatch(r"[1-9][0-9]{0,39}", fields["organization_id"]):
        raise ValueError
    payload = {
        "event": {
            "name": {"html": html.escape(fields["title"])},
            "summary": fields["summary"],
            "start": {
                "utc": _utc(fields["start_local"], fields["timezone"]),
                "timezone": fields["timezone"],
            },
            "end": {
                "utc": _utc(fields["end_local"], fields["timezone"]),
                "timezone": fields["timezone"],
            },
            "currency": fields["currency"],
            "listed": False,
            "shareable": False,
        }
    }
    operation = ledger.submit_draft(
        user=request.user,
        intent_id=intent.pk,
        run_id=run.pk,
        action="create",
        organization_id=fields["organization_id"],
        payload=payload,
    )
    return _response(_serialize(operation), 201)


@require_GET
@_safe
def detail(request, operation_id):
    operation, _ = _operation(request, operation_id)
    return _response(_serialize(operation))


@require_POST
@_safe
def approve(request, operation_id):
    operation, session = _operation(request, operation_id)
    body = _body(request, ["review_digest", "revision_id"])
    if not isinstance(body["review_digest"], str) or type(body["revision_id"]) is not int:
        raise ValueError
    result = ledger.approve_draft(
        user=request.user, administrator_session_id=session.pk, operation_id=operation.pk, **body
    )
    return _response(_serialize(result))


@require_POST
@_safe
def execute(request, operation_id):
    operation, session = _operation(request, operation_id)
    _body(request, [])
    result = ledger.execute_draft(
        user=request.user, administrator_session_id=session.pk, operation_id=operation.pk
    )
    return _response(_serialize(result))


@require_POST
@_safe
def reconcile(request, operation_id):
    operation, session = _operation(request, operation_id)
    _body(request, [])
    result = ledger.reconcile_draft(
        user=request.user, administrator_session_id=session.pk, operation_id=operation.pk
    )
    return _response(_serialize(result))
