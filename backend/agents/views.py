import json
import uuid
from collections.abc import Callable

from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET
from identity.models import AdministratorSession
from launchloop.models import DemoActor

from agents.models import AgentRun, BudgetLedgerRecord, BudgetReservation


def _problem(request: HttpRequest, status: int, code: str, title: str, detail: str) -> JsonResponse:
    return JsonResponse(
        {
            "type": f"https://civicloop.karthikkannan.ca/problems/{code.replace('_', '-')}",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": request.path,
            "code": code,
            "message": detail,
        },
        status=status,
        content_type="application/problem+json",
        headers={"Cache-Control": "no-store"},
    )


def _authorized(request: HttpRequest) -> JsonResponse | None:
    metadata = getattr(request, "administrator_session", None)
    if isinstance(metadata, AdministratorSession) and not metadata.recovery_restricted:
        return None
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return _problem(
            request,
            401,
            "authentication_required",
            "Authentication required",
            "Owner or reviewer authentication is required.",
        )
    try:
        actor = user.launchloop_actor
    except DemoActor.DoesNotExist:
        actor = None
    if actor is None or actor.role != DemoActor.Role.APPROVER:
        return _problem(
            request,
            403,
            "reviewer_required",
            "Reviewer required",
            "Owner or authorized reviewer access is required.",
        )
    return None


def _run_or_problem(
    request: HttpRequest, run_id: uuid.UUID
) -> tuple[AgentRun | None, JsonResponse | None]:
    denied = _authorized(request)
    if denied is not None:
        return None, denied
    try:
        run = AgentRun.objects.select_related(
            "workflow",
            "event_revision",
            "routing_policy",
            "model_profile",
        ).get(pk=run_id)
    except AgentRun.DoesNotExist:
        return None, _problem(
            request,
            404,
            "agent_run_not_found",
            "Agent run not found",
            "The requested agent run does not exist.",
        )
    return run, None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _read_view(
    serializer: Callable[[AgentRun], dict[str, object]],
) -> Callable[[HttpRequest, uuid.UUID], JsonResponse]:
    @require_GET
    def view(request: HttpRequest, run_id: uuid.UUID) -> JsonResponse:
        run, error = _run_or_problem(request, run_id)
        if error is not None:
            return error
        assert run is not None
        return JsonResponse(serializer(run), headers={"Cache-Control": "no-store"})

    return view


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "run_id": str(run.id),
        "workflow_id": str(run.workflow_id),
        "event_revision_id": run.event_revision_id,
        "package_hash": run.package_hash,
        "routing_policy": {
            "id": run.routing_policy.policy_id,
            "revision": run.routing_policy.revision,
        },
        "model_profile": {
            "id": run.model_profile.profile_id,
            "revision": run.model_profile.revision,
        },
        "privacy_mode": run.privacy_mode,
        "status": run.status,
        "attempt": run.attempt,
        "queued_at": run.queued_at.isoformat(),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "failure_category": run.failure_category or None,
        "trace": {"trace_id": run.trace_id, "span_count": run.span_count},
        "usage": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "cost_microusd": run.cost_microusd,
        },
    }


def _steps_payload(run: AgentRun) -> dict[str, object]:
    return {
        "run_id": str(run.id),
        "steps": [
            {
                "step_id": str(step.id),
                "sequence": step.sequence,
                "kind": step.kind,
                "status": step.status,
                "started_at": _iso(step.started_at),
                "finished_at": _iso(step.finished_at),
                "input_summary": step.input_summary or None,
                "output_summary": step.output_summary or None,
                "failure_category": step.failure_category or None,
                "telemetry": {
                    "duration_ms": step.duration_ms,
                    "input_tokens": step.input_tokens,
                    "output_tokens": step.output_tokens,
                },
            }
            for step in run.steps.all()[:1000]
        ],
    }


def _evaluations_payload(run: AgentRun) -> dict[str, object]:
    return {
        "run_id": str(run.id),
        "results": [
            {
                "result_id": str(result.id),
                "evaluator": result.evaluator,
                "outcome": result.outcome,
                "score": float(result.score) if result.score is not None else None,
                "reason_codes": result.reason_codes,
                "summary": result.summary,
                "created_at": result.created_at.isoformat(),
                "advisory_only": result.advisory_only,
            }
            for result in run.evaluation_results.order_by("created_at")[:100]
        ],
    }


def _usage_payload(run: AgentRun) -> dict[str, object]:
    reservation = BudgetReservation.objects.filter(run_id=run.id).first()
    records = BudgetLedgerRecord.objects.filter(run_id=run.id).order_by("recorded_at")[:4]
    return {
        "run_id": str(run.id),
        "reservation_status": reservation.status if reservation else None,
        "reserved_cost_microusd": reservation.reserved_cost_microusd if reservation else 0,
        "settled_cost_microusd": reservation.settled_cost_microusd if reservation else None,
        "ledger": [
            {
                "record_id": str(record.id),
                "entry_type": record.entry_type,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "cost_microusd": record.cost_microusd,
                "recorded_at": record.recorded_at.isoformat(),
            }
            for record in records
        ],
    }


run_detail = _read_view(_run_payload)
run_steps = _read_view(_steps_payload)
run_evaluations = _read_view(_evaluations_payload)
run_usage = _read_view(_usage_payload)


@csrf_exempt
@sensitive_post_parameters()
def mcp_endpoint(request: HttpRequest) -> JsonResponse:
    """Stateless MCP JSON-RPC; only installed by the dedicated internal URLconf."""
    from agents.capabilities import AuthorizationDenied
    from agents.mcp import authenticate_service, dispatch_mcp_tool
    from agents.tool_schemas import (
        MAX_PAYLOAD_BYTES,
        TOOL_SCHEMAS,
        InvalidToolArguments,
        bounded_json,
    )

    def response(payload, status=200):
        return JsonResponse(payload, status=status, headers={"Cache-Control": "no-store"})

    rpc_id = None
    try:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise AuthorizationDenied()
        identity = authorization[7:]
        authenticate_service(identity)
        request.get_host()
        if request.method != "POST":
            return response({"error": "Method not allowed."}, 405)
        # Service-to-service only; browsers cannot supply an accepted Origin.
        if request.headers.get("Origin"):
            raise AuthorizationDenied()
        if request.content_type != "application/json":
            raise InvalidToolArguments()
        length = int(request.META.get("CONTENT_LENGTH") or "0")
        if not 0 < length <= MAX_PAYLOAD_BYTES:
            raise InvalidToolArguments()
        raw = request.read(MAX_PAYLOAD_BYTES + 1)
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise InvalidToolArguments()
        body = json.loads(raw)
        bounded_json(body)
        if (
            type(body) is not dict
            or set(body) - {"jsonrpc", "id", "method", "params"}
            or body.get("jsonrpc") != "2.0"
        ):
            raise InvalidToolArguments()
        rpc_id = body.get("id")
        if rpc_id is not None and not (
            (type(rpc_id) is int and 0 <= rpc_id <= 2**53)
            or (type(rpc_id) is str and 1 <= len(rpc_id) <= 128)
        ):
            raise InvalidToolArguments()
        method = body.get("method")
        params = body.get("params", {})
        if type(params) is not dict:
            raise InvalidToolArguments()
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "civicloop", "version": "1.0"},
            }
        elif method == "notifications/initialized":
            return response({}, 202)
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            if params:
                raise InvalidToolArguments()
            result = {
                "tools": [
                    {"name": name, "description": name.replace("_", " "), "inputSchema": schema}
                    for name, schema in TOOL_SCHEMAS.items()
                ]
            }
        elif method == "tools/call":
            if set(params) != {"name", "arguments"}:
                raise InvalidToolArguments()
            result_body = dispatch_mcp_tool(
                tool_name=params["name"],
                arguments=params["arguments"],
                service_identity=identity,
                capability=request.headers.get("X-CivicLoop-Capability", ""),
            )
            result = {
                "content": [{"type": "text", "text": json.dumps(result_body)}],
                "structuredContent": result_body,
                "isError": False,
            }
        else:
            return response(
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": "Method not found."},
                }
            )
        return response({"jsonrpc": "2.0", "id": rpc_id, "result": result})
    except AuthorizationDenied:
        from launchloop.models import AuditEvent

        AuditEvent.objects.create(
            action="mcp.transport_denied",
            target_type="mcp",
            target_id="broker",
            details={"reason": "authorization_failed"},
        )
        return response({"error": "Authorization failed."}, 401)
    except InvalidToolArguments, ValueError, TypeError, RecursionError, UnicodeError:
        return response(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32602, "message": "Invalid tool arguments."},
            },
            400,
        )
    except Exception:
        # No exception detail, body, or authorization headers may enter logs or traces.
        return response(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "error": {"code": -32603, "message": "Tool unavailable."},
            },
            503,
        )
