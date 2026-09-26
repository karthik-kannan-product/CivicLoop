from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deploy.hermes.process_controller import ProcessController

from deploy.hermes.transport import TransportClient, TransportError, _opener, _validate_binding
from deploy.hermes.transport_contracts import ScopeBinding

RUN_PATH = "/internal/v1/hermes/runs"
MAX_BODY_BYTES = 32_768
MODEL_ALIAS = "civicloop-default"
CAPABILITY_TOKEN = re.compile(r"cap_[A-Za-z0-9_-]{43,125}")
SCHEMA_ID = re.compile(r"urn:civicloop:schema:[A-Za-z0-9._:-]{1,135}")
DIGEST = re.compile(r"[a-f0-9]{64}")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
ALLOWED_TOOLS = (
    "mcp__civicloop__get_event_revision",
    "mcp__civicloop__get_policy_context",
    "mcp__civicloop__request_clarification",
    "mcp__civicloop__propose_campaign_drafts",
    "mcp__civicloop__validate_proposal",
    "mcp__civicloop__request_eventbrite_draft",
    "mcp__civicloop__request_iterable_drafts",
    "mcp__civicloop__get_operation_status",
)
REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "workflow_id",
        "revision_id",
        "actor_id",
        "correlation_id",
        "capability_token",
        "model_alias",
        "budgets",
    }
)
BUDGET_FIELDS = frozenset(
    {"max_input_tokens", "max_output_tokens", "max_cost_microusd", "timeout_seconds"}
)


class PolicyError(ValueError):
    pass


class UpstreamError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdapterPolicy:
    model_alias: str
    upstream_url: str
    poll_interval_seconds: float
    maximum_timeout_seconds: int


def _uuid(value: object, *, label: str) -> str:
    if not isinstance(value, str) or len(value) > 36:
        raise PolicyError(f"{label} is invalid")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise PolicyError(f"{label} is invalid") from error


def _integer(value: object, *, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PolicyError(f"{label} is invalid")
    return value


def validate_run_request(body: object, *, policy: AdapterPolicy) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != REQUEST_FIELDS:
        raise PolicyError("request fields are invalid")
    if body.get("schema_version") != "1.0":
        raise PolicyError("schema version is invalid")
    for field in ("workflow_id", "correlation_id"):
        _uuid(body.get(field), label=field.replace("_", " "))
    _integer(body.get("revision_id"), label="revision id", minimum=1, maximum=2**63 - 1)
    actor = body.get("actor_id")
    if not isinstance(actor, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,50}", actor) is None:
        raise PolicyError("actor id is invalid")
    capability = body.get("capability_token")
    if not isinstance(capability, str) or CAPABILITY_TOKEN.fullmatch(capability) is None:
        raise PolicyError("capability token is invalid")
    if body.get("model_alias") != policy.model_alias:
        raise PolicyError("model alias is not allowed")
    budgets = body.get("budgets")
    if not isinstance(budgets, dict) or set(budgets) != BUDGET_FIELDS:
        raise PolicyError("budget fields are invalid")
    _integer(budgets["max_input_tokens"], label="input token budget", minimum=1, maximum=1_000_000)
    _integer(budgets["max_output_tokens"], label="output token budget", minimum=1, maximum=100_000)
    _integer(budgets["max_cost_microusd"], label="cost budget", minimum=1, maximum=1_000_000_000)
    _integer(
        budgets["timeout_seconds"],
        label="timeout budget",
        minimum=1,
        maximum=policy.maximum_timeout_seconds,
    )
    return body


def build_upstream_request(
    body: dict[str, Any], *, allowed_tools: list[str] | tuple[str, ...]
) -> dict[str, str]:
    identifiers = {
        key: body[key] for key in ("workflow_id", "revision_id", "actor_id", "correlation_id")
    }
    instructions = (
        "Operate only through these exact CivicLoop MCP tools: "
        + ", ".join(allowed_tools)
        + ". Do not use resources, prompts, terminal, process, filesystem-write, browser, web, "
        "code-execution, cron, delegation, messaging, computer-use, skill-mutation, or "
        "general-memory capabilities. Return JSON containing only proposal_references. Never "
        "publish, send, schedule, "
        "change ticket economics, create segments, or export constituent data. MCP authentication "
        "is transport-managed; never request, repeat, or emit credentials or capability tokens."
    )
    return {
        "input": json.dumps(identifiers, sort_keys=True, separators=(",", ":")),
        "model": MODEL_ALIAS,
        "instructions": instructions,
    }


def _safe_usage(value: object) -> dict[str, int]:
    usage = value if isinstance(value, dict) else {}
    return {
        "input_tokens": _bounded_nonnegative(usage.get("input_tokens"), 1_000_000),
        "output_tokens": _bounded_nonnegative(usage.get("output_tokens"), 100_000),
        "cost_microusd": _bounded_nonnegative(usage.get("cost_microusd"), 1_000_000_000),
    }


def _bounded_nonnegative(value: object, maximum: int) -> int:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum
        else 0
    )


def _proposal_references(output: object) -> list[dict[str, str]]:
    if not isinstance(output, str) or len(output.encode()) > MAX_BODY_BYTES:
        raise PolicyError("Hermes output is invalid")
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as error:
        raise PolicyError("Hermes output is invalid") from error
    values = parsed.get("proposal_references") if isinstance(parsed, dict) else None
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise PolicyError("Hermes proposal references are invalid")
    result = []
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "proposal_id",
            "schema_id",
            "proposal_digest",
        }:
            raise PolicyError("Hermes proposal reference is invalid")
        proposal_id = _uuid(value["proposal_id"], label="proposal ID")
        schema_id = value["schema_id"]
        digest = value["proposal_digest"]
        if not isinstance(schema_id, str) or SCHEMA_ID.fullmatch(schema_id) is None:
            raise PolicyError("proposal schema ID is invalid")
        if not isinstance(digest, str) or DIGEST.fullmatch(digest) is None:
            raise PolicyError("proposal digest is invalid")
        result.append(
            {"proposal_id": proposal_id, "schema_id": schema_id, "proposal_digest": digest}
        )
    if len({json.dumps(item, sort_keys=True) for item in result}) != len(result):
        raise PolicyError("duplicate proposal reference")
    return result


def map_upstream_result(request: dict[str, Any], upstream: object) -> dict[str, Any]:
    payload = upstream if isinstance(upstream, dict) else {}
    status = payload.get("status")
    mapped_status = {
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "cancelled",
        "interrupted": "cancelled",
    }.get(status, "failed")
    references: list[dict[str, str]] = []
    failure_category: str | None = None
    if mapped_status == "succeeded":
        try:
            references = _proposal_references(payload.get("output"))
        except PolicyError:
            mapped_status = "failed"
            failure_category = "invalid_output"
    elif mapped_status == "cancelled":
        failure_category = "cancelled"
    else:
        failure_category = "provider_unavailable"
    return {
        "schema_version": "1.0",
        "run_id": str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"urn:civicloop:run:{request['correlation_id']}")
        ),
        "workflow_id": request["workflow_id"],
        "revision_id": request["revision_id"],
        "status": mapped_status,
        "proposal_references": references,
        "usage": _safe_usage(payload.get("usage")),
        "failure_category": failure_category,
    }


def _json_request(
    url: str,
    *,
    token: str,
    method: str,
    body: object | None = None,
    timeout: float,
    idempotency_key: str | None = None,
    transport_scope: str | None = None,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if transport_scope is not None:
        headers["X-CivicLoop-Transport-Scope"] = transport_scope
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(request, timeout=timeout) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise UpstreamError("Hermes dependency unavailable") from error
    if len(raw) > MAX_BODY_BYTES:
        raise UpstreamError("Hermes response is too large")
    try:
        value = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UpstreamError("Hermes response is invalid") from error
    if not isinstance(value, dict):
        raise UpstreamError("Hermes response is invalid")
    return value


class HermesAdapter(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *args: Any,
        service_token: str,
        upstream_token: str,
        policy: AdapterPolicy,
        allowed_tools: list[str] | tuple[str, ...],
        transport_client: TransportClient | None = None,
        binding_resolver: Callable[[dict[str, Any]], ScopeBinding] | None = None,
        process_controller: ProcessController | None = None,
        **kwargs: Any,
    ) -> None:
        if len(service_token) < 16 or len(upstream_token) < 16:
            raise ValueError("adapter tokens must be at least 16 characters")
        if tuple(allowed_tools) != ALLOWED_TOOLS:
            raise ValueError("Hermes tool policy does not match the frozen allowlist")
        self.service_token = service_token
        self.upstream_token = upstream_token
        self.policy = policy
        self.allowed_tools = tuple(allowed_tools)
        self.transport_client = transport_client
        self.binding_resolver = binding_resolver
        self.process_controller = process_controller
        self.transport_healthy = True
        self.run_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def is_ready(self) -> bool:
        if self.process_controller is not None:
            return not self.process_controller.quarantined and self.transport_healthy
        try:
            health = _json_request(
                f"{self.policy.upstream_url.rstrip('/')}/health",
                token=self.upstream_token,
                method="GET",
                timeout=2,
            )
        except UpstreamError:
            return False
        return health.get("status") in {"ok", "healthy", "ready"}

    def execute(self, body: dict[str, Any]) -> dict[str, Any]:
        # The Task 8 worker supplies immutable revision/run authority. The launch
        # request deliberately has neither revision_digest nor max_inferences.
        if (
            self.transport_client is None
            or self.binding_resolver is None
            or not self.transport_healthy
        ):
            raise UpstreamError("Hermes transport unavailable")
        try:
            binding = _validate_binding(self.binding_resolver(body), datetime.now(UTC))
            expected = {
                "run_id": map_upstream_result(body, {})["run_id"],
                "workflow_id": uuid.UUID(body["workflow_id"]),
                "revision_id": body["revision_id"],
                "actor_id": body["actor_id"],
                "capability": body["capability_token"],
                "model_alias": body["model_alias"],
                **{
                    key: body["budgets"][key]
                    for key in ("max_input_tokens", "max_output_tokens", "max_cost_microusd")
                },
            }
            if any(getattr(binding, key) != value for key, value in expected.items()):
                raise TransportError()
            remaining = (binding.expires_at - datetime.now(UTC)).total_seconds()
            if remaining > body["budgets"]["timeout_seconds"]:
                raise TransportError()
            lease_deadline = time.monotonic() + remaining
        except Exception:
            raise UpstreamError("Hermes transport unavailable") from None
        token = "scope_" + secrets.token_urlsafe(32)
        try:
            self.transport_client.register_scope(token=token, binding=binding)
            if self.process_controller is not None:
                return self.process_controller.execute(
                    body, scope_token=token, deadline=lease_deadline
                )
            return self._execute_scoped(body, transport_scope=token, timeout_seconds=remaining)
        except Exception:
            raise UpstreamError("Hermes transport unavailable") from None
        finally:
            # Even ambiguous registration failures require revocation. A failed
            # revoke quarantines this adapter until restart; no new lease may run.
            try:
                self.transport_client.revoke_scope(token=token)
            except Exception:
                self.transport_healthy = False
                raise UpstreamError("Hermes transport unavailable") from None

    def _execute_scoped(
        self,
        body: dict[str, Any],
        *,
        transport_scope: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        created = _json_request(
            f"{self.policy.upstream_url.rstrip('/')}/v1/runs",
            token=self.upstream_token,
            method="POST",
            body=build_upstream_request(body, allowed_tools=self.allowed_tools),
            timeout=min(10, max(0.1, deadline - time.monotonic())),
            idempotency_key=body["correlation_id"],
            transport_scope=transport_scope,
        )
        upstream_run_id = created.get("run_id")
        if not isinstance(upstream_run_id, str) or not re.fullmatch(
            r"run_[A-Za-z0-9]{1,128}", upstream_run_id
        ):
            raise UpstreamError("Hermes run admission is invalid")
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            status = _json_request(
                f"{self.policy.upstream_url.rstrip('/')}/v1/runs/{upstream_run_id}",
                token=self.upstream_token,
                method="GET",
                timeout=min(10, max(0.1, remaining)),
            )
            if status.get("status") in TERMINAL_STATUSES:
                return map_upstream_result(body, status)
            if status.get("status") == "waiting_for_approval":
                failed = map_upstream_result(body, {"status": "failed"})
                failed["failure_category"] = "capability_rejected"
                return failed
            time.sleep(min(self.policy.poll_interval_seconds, max(0, deadline - time.monotonic())))
        failed = map_upstream_result(body, {"status": "failed"})
        failed["failure_category"] = "timeout"
        return failed


class _Handler(BaseHTTPRequestHandler):
    server: HermesAdapter
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # Do not log paths, authorization headers, request bodies, or upstream output.
        return

    def _send(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def _problem(self, status: int, title: str) -> None:
        self._send(status, {"type": "about:blank", "title": title, "status": status})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health/live":
            self._send(200, {"status": "ok"})
            return
        if self.path == "/health/ready":
            if self.server.is_ready():
                self._send(200, {"status": "ready"})
            else:
                self._send(503, {"status": "unavailable"})
            return
        self._problem(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != RUN_PATH:
            self._problem(404, "Not found")
            return
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.service_token}"
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            self._problem(401, "Unauthorized")
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or not 0 < int(length) <= MAX_BODY_BYTES:
            self._problem(400, "Invalid request")
            return
        if not self.server.run_lock.acquire(blocking=False):
            self._problem(429, "Hermes run already active")
            return
        try:
            try:
                body = json.loads(self.rfile.read(int(length)))
                validated = validate_run_request(body, policy=self.server.policy)
            except UnicodeError, json.JSONDecodeError, PolicyError:
                self._problem(400, "Invalid request")
                return
            try:
                result = self.server.execute(validated)
            except UpstreamError:
                result = map_upstream_result(validated, {"status": "failed"})
                result["failure_category"] = "dependency_unavailable"
            self._send(200, result)
        finally:
            self.server.run_lock.release()


def _read_token(path_value: str, *, label: str) -> str:
    path = Path(path_value)
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 16 or path.stat().st_mode & 0o077:
        raise RuntimeError(f"{label} is unavailable or has unsafe permissions")
    return value


def main() -> None:
    policy = AdapterPolicy(
        model_alias=MODEL_ALIAS,
        upstream_url=os.getenv("HERMES_UPSTREAM_URL", "http://hermes:8642"),
        poll_interval_seconds=float(os.getenv("HERMES_POLL_INTERVAL_SECONDS", "0.5")),
        maximum_timeout_seconds=600,
    )
    server = HermesAdapter(
        ("0.0.0.0", int(os.getenv("PORT", "8080"))),
        _Handler,
        service_token=_read_token(os.environ["HERMES_ADAPTER_TOKEN_FILE"], label="adapter token"),
        upstream_token=_read_token(
            os.environ["HERMES_UPSTREAM_TOKEN_FILE"], label="upstream token"
        ),
        policy=policy,
        allowed_tools=ALLOWED_TOOLS,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
