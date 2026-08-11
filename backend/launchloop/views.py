import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import DemoActor
from .services import (
    DemoError,
    actor_for_user,
    answer_questions,
    current_workflow,
    decide_approval,
    reset_demo,
    run_workflow,
    seed_demo_users,
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


def _actor(request: HttpRequest) -> DemoActor:
    if not request.user.is_authenticated:
        raise DemoError("authentication_required", "Sign in to use the demo workspace.", 401)
    return actor_for_user(request.user)


def _operator(request: HttpRequest) -> DemoActor:
    actor = _actor(request)
    if actor.role != DemoActor.Role.OPERATOR:
        raise DemoError("operator_required", "Only the operator can reset the demo workspace.", 403)
    return actor


def _problem(request: HttpRequest, error: DemoError) -> JsonResponse:
    title = error.code.replace("_", " ").capitalize()

    payload = {
        "type": f"urn:civicloop:problem:{error.code}",
        "title": title,
        "status": error.status,
        "detail": error.message,
        "instance": request.path,
        "code": error.code,
        "message": error.message,
    }
    return JsonResponse(
        payload,
        status=error.status,
        content_type="application/problem+json",
    )


def _respond(
    request: HttpRequest,
    operation: Callable[[], dict[str, Any]],
) -> JsonResponse:
    try:
        return JsonResponse(operation())
    except DemoError as error:
        return _problem(request, error)


def _session_payload(actor: DemoActor) -> dict[str, Any]:
    return {
        "user": {
            "username": actor.user.username,
            "display_name": actor.display_name,
            "role": actor.role,
        }
    }


@ensure_csrf_cookie
@require_GET
def auth_session(request: HttpRequest) -> JsonResponse:
    return _respond(request, lambda: _session_payload(_actor(request)))


@require_POST
def auth_login(request: HttpRequest) -> JsonResponse:
    def operation() -> dict[str, Any]:
        seed_demo_users()
        body = _body(request)
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        user = authenticate(request, username=username, password=password)
        if user is None:
            raise DemoError("invalid_credentials", "Use one of the temporary demo accounts.", 401)
        django_login(request, user)
        if not DemoActor.objects.filter(user=user).exists():
            reset_demo()
        return _session_payload(actor_for_user(user))

    return _respond(request, operation)


@require_POST
def auth_logout(request: HttpRequest) -> JsonResponse:
    django_logout(request)
    return JsonResponse({"logged_out": True})


@require_GET
def demo_state(request: HttpRequest) -> JsonResponse:
    return _respond(request, lambda: (_actor(request), serialize_demo(current_workflow()))[1])


@require_POST
def demo_reset(request: HttpRequest) -> JsonResponse:
    return _respond(request, lambda: (_operator(request), serialize_demo(reset_demo()))[1])


@require_POST
def workflow_run(request: HttpRequest, workflow_id: UUID) -> JsonResponse:
    return _respond(
        request,
        lambda: serialize_demo(run_workflow(workflow_id, _actor(request))),
    )


@require_POST
def workflow_answers(request: HttpRequest, workflow_id: UUID) -> JsonResponse:
    return _respond(
        request,
        lambda: serialize_demo(answer_questions(workflow_id, _actor(request), _body(request))),
    )


@require_POST
def workflow_submit(request: HttpRequest, workflow_id: UUID) -> JsonResponse:
    def operation() -> dict[str, Any]:
        submit_workflow(workflow_id, _actor(request))
        return serialize_demo(workflow_for(workflow_id))

    return _respond(request, operation)


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

    return _respond(request, operation)
