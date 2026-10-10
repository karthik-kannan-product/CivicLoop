"""Owner review of Hermes content and a separate unscheduled Iterable campaign."""

from functools import wraps

from agents.models import AgentRun, DraftOperation
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from integrations import draft_operations as ledger
from integrations.draft_views import _body, _owner, _response
from integrations.iterable_drafts import IterableDraftError
from integrations.models import DraftExecution, TemplateExecution


def _endpoint(methods):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if request.method not in methods:
                    return _error(405)
                return view(request, *args, **kwargs)
            except PermissionDenied:
                return _error(
                    401
                    if not getattr(request, "user", None) or not request.user.is_authenticated
                    else 403
                )
            except ObjectDoesNotExist:
                return _error(404)
            except ValueError, TypeError, UnicodeError, IterableDraftError, KeyError:
                return _error(400)
            except Exception:
                return _error(503)

        return wrapped

    return decorate


def _error(status):
    return _response({"message": "The owner Iterable action could not be completed."}, status)


def _intent(request, run_id, intent_id):
    actor, session = _owner(request)
    run = AgentRun.objects.get(pk=run_id)
    intent = DraftOperation.objects.select_related("proposal", "workflow__revision").get(
        pk=intent_id
    )
    if (
        intent.actor_id != actor.pk
        or intent.provider != "iterable"
        or intent.operation_kind
        not in {"create_iterable_email_draft", "create_iterable_reminder_draft"}
    ):
        raise PermissionDenied
    ledger.validate_intent(intent, run)
    return intent, run, session


def _validate_owned(request, operation, actor):
    if (
        operation.submitter_id != request.user.pk
        or operation.intent.actor_id != actor.pk
        or operation.intent.provider != "iterable"
        or operation.action != "create"
    ):
        raise PermissionDenied
    ledger.validate_execution(operation, require_live_approval=False)


def _operation(request, operation_id, model):
    actor, session = _owner(request)
    operation = model.objects.select_related("intent", "run").get(pk=operation_id)
    _validate_owned(request, operation, actor)
    return operation, session


def _serialize(operation):
    return {
        "operation_id": str(operation.pk),
        "intent_id": str(operation.intent_id),
        "run_id": str(operation.run_id),
        "revision_id": operation.intent.revision_id,
        "review_digest": operation.review_digest,
        "request_digest": operation.request_digest,
        "status": operation.status,
        "payload": operation.payload,
        "provider_configuration": operation.provider_configuration,
        "provider_id": operation.provider_id or None,
        "receipt": operation.receipt,
        "step": "template" if isinstance(operation, TemplateExecution) else "campaign",
    }


@_endpoint({"GET", "POST"})
def prepare(request, run_id, intent_id):
    intent, run, _ = _intent(request, run_id, intent_id)
    if request.method == "POST":
        fields = _body(request, ["sender", "region"])
        operation = ledger.submit_template(
            user=request.user, intent_id=intent.pk, run_id=run.pk, **fields
        )
        return _response(_serialize(operation), 201)
    actor, _ = _owner(request)
    template = TemplateExecution.objects.filter(intent=intent).first()
    campaign = DraftExecution.objects.filter(intent=intent).first()
    for operation in (template, campaign):
        if operation:
            _validate_owned(request, operation, actor)
    kind = "invitation" if intent.operation_kind == "create_iterable_email_draft" else "reminder"
    content = intent.proposal.content[kind]
    if not isinstance(content, dict) or not all(
        isinstance(content.get(key), str) for key in ("subject", "body")
    ):
        raise ValueError
    return _response(
        {
            "revision_id": intent.revision_id,
            "kind": kind,
            "subject": content["subject"],
            "body": content["body"],
            "template_execution": _serialize(template) if template else None,
            "campaign_execution": _serialize(campaign) if campaign else None,
        }
    )


@_endpoint({"POST"})
def prepare_campaign(request, run_id, intent_id):
    intent, run, _ = _intent(request, run_id, intent_id)
    fields = _body(request, ["name", "listIds", "suppressionListIds"])
    template = TemplateExecution.objects.get(intent=intent)
    actor, _ = _owner(request)
    _validate_owned(request, template, actor)
    if (
        template.status != "succeeded"
        or not template.receipt
        or template.receipt.get("outcome") != "CONFIRMED"
    ):
        raise PermissionDenied
    payload = {**fields, "templateId": int(template.provider_id), "scheduleSend": False}
    operation = ledger.submit_draft(
        user=request.user,
        intent_id=intent.pk,
        run_id=run.pk,
        action="create",
        payload=payload,
        provider_configuration={
            "region": template.provider_configuration["region"],
            "expected_template_digest": template.receipt["readback_digest"],
        },
    )
    return _response(_serialize(operation), 201)


def _action(request, operation_id, model, function, approval=False):
    operation, session = _operation(request, operation_id, model)
    fields = _body(request, ["review_digest", "revision_id"] if approval else [])
    if approval and (
        not isinstance(fields["review_digest"], str) or type(fields["revision_id"]) is not int
    ):
        raise ValueError
    result = function(
        user=request.user, administrator_session_id=session.pk, operation_id=operation.pk, **fields
    )
    return _response(_serialize(result))


@_endpoint({"GET"})
def template_detail(request, operation_id):
    operation, _ = _operation(request, operation_id, TemplateExecution)
    return _response(_serialize(operation))


@_endpoint({"POST"})
def template_approve(request, operation_id):
    return _action(request, operation_id, TemplateExecution, ledger.approve_template, True)


@_endpoint({"POST"})
def template_execute(request, operation_id):
    return _action(request, operation_id, TemplateExecution, ledger.execute_template)


@_endpoint({"POST"})
def template_reconcile(request, operation_id):
    return _action(request, operation_id, TemplateExecution, ledger.reconcile_template)


@_endpoint({"GET"})
def campaign_detail(request, operation_id):
    operation, _ = _operation(request, operation_id, DraftExecution)
    return _response(_serialize(operation))


@_endpoint({"POST"})
def campaign_approve(request, operation_id):
    return _action(request, operation_id, DraftExecution, ledger.approve_draft, True)


@_endpoint({"POST"})
def campaign_execute(request, operation_id):
    return _action(request, operation_id, DraftExecution, ledger.execute_draft)


@_endpoint({"POST"})
def campaign_reconcile(request, operation_id):
    return _action(request, operation_id, DraftExecution, ledger.reconcile_draft)
