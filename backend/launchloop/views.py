import json
from typing import Any
from uuid import UUID

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .services import (
    DemoError,
    actor_for,
    answer_questions,
    current_workflow,
    decide_approval,
    reset_demo,
    run_workflow,
    serialize_demo,
    submit_workflow,
    workflow_for,
)


def _body(request: HttpRequest) -> dict[str, Any]:
    try:
        parsed = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        raise DemoError("invalid_json", "Request body must be valid JSON.") from None
    if not isinstance(parsed, dict):
        raise DemoError("invalid_json", "Request body must be a JSON object.")
    return parsed


def _actor(request: HttpRequest):
    return actor_for(request.headers.get("X-Demo-Actor", "maya"))


def _respond(operation):
    try:
        return JsonResponse(operation())
    except DemoError as error:
        return JsonResponse(
            {"code": error.code, "message": error.message},
            status=error.status,
        )


@require_GET
def demo_state(_request: HttpRequest) -> JsonResponse:
    return _respond(lambda: serialize_demo(current_workflow()))


@csrf_exempt
@require_POST
def demo_reset(_request: HttpRequest) -> JsonResponse:
    return _respond(lambda: serialize_demo(reset_demo()))


@csrf_exempt
@require_POST
def workflow_run(request: HttpRequest, workflow_id: UUID) -> JsonResponse:
    return _respond(lambda: serialize_demo(run_workflow(workflow_id, _actor(request))))


@csrf_exempt
@require_POST
def workflow_answers(request: HttpRequest, workflow_id: UUID) -> JsonResponse:
    return _respond(
        lambda: serialize_demo(
            answer_questions(workflow_id, _actor(request), _body(request))
        )
    )


@csrf_exempt
@require_POST
def workflow_submit(request: HttpRequest, workflow_id: UUID) -> JsonResponse:
    def operation() -> dict[str, Any]:
        submit_workflow(workflow_id, _actor(request))
        return serialize_demo(workflow_for(workflow_id))

    return _respond(operation)


@csrf_exempt
@require_POST
def approval_decision(request: HttpRequest, approval_id: UUID) -> JsonResponse:
    def operation() -> dict[str, Any]:
        body = _body(request)
        approval = decide_approval(
            approval_id,
            _actor(request),
            str(body.get("decision", "")),
            str(body.get("package_hash", "")),
            str(body.get("reason", "")),
        )
        return serialize_demo(approval.workflow)

    return _respond(operation)
