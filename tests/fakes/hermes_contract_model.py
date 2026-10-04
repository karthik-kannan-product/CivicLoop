"""Scripted model upstream; every tool result comes from the real CivicLoop MCP.

Requests and synthetic authority remain in memory. Only closed counters are exposed.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STEPS = (
    "get_event_revision",
    "get_policy_context",
    "propose_campaign_drafts",
    "validate_proposal",
    "request_eventbrite_draft",
    "request_iterable_drafts",
)
SCHEMA_ID = "urn:civicloop:schema:campaign-proposal:v1.0"
PROHIBITED_MARKER = "TASK8_SYNTHETIC_CONTENT_MARKER_4d70ae"
BINDING_FIELDS = {"workflow_id", "revision_id", "actor_id", "correlation_id"}
FAILURE_CATEGORIES = (
    "request_schema", "binding_missing", "binding_mismatch", "binding_uuid",
    "revision_invalid", "actor_invalid", "step_invalid", "tool_result_count",
    "proposal_missing", "advertised_tool_missing", "advertised_tool_duplicate",
    "hold_deadline", "mode_schema", "internal_contract",
)
REQUEST_SHAPE_FIELDS = (
    "message_count", "binding_count", "assistant_call_count", "tool_result_count",
    "advertised_tool_count", "expected_next_tool_count", "system_message_count",
    "user_message_count",
)


class FixtureFailure(Exception):
    def __init__(self, category="internal_contract"):
        self.category = category if category in FAILURE_CATEGORIES else "internal_contract"
        super().__init__("fixture_contract_failed")


def _objects(value):
    """Extract JSON objects from real upstream messages, including MCP wrappers."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, str) and len(value) <= 262144:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", value):
            try:
                parsed, _ = decoder.raw_decode(value[match.start() :])
            except ValueError:
                continue
            yield from _objects(parsed)


def completion(request: dict, *, invalid=False) -> dict:
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
        raise FixtureFailure("request_schema")
    messages = request.get("messages", [])
    objects = list(_objects(messages))
    bindings = [
        x
        for x in objects
        if set(x)
        == {
            "workflow_id",
            "revision_id",
            "actor_id",
            "correlation_id",
        }
    ]
    if not bindings:
        raise FixtureFailure("binding_missing")
    if any(x != bindings[0] for x in bindings):
        raise FixtureFailure("binding_mismatch")
    binding = bindings[0]
    for field in ("workflow_id", "correlation_id"):
        try:
            valid = str(uuid.UUID(binding[field])) == binding[field]
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise FixtureFailure("binding_uuid")
    if type(binding["revision_id"]) is not int or binding["revision_id"] < 1:
        raise FixtureFailure("revision_invalid")
    if not isinstance(binding["actor_id"], str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]{1,50}", binding["actor_id"]
    ):
        raise FixtureFailure("actor_invalid")
    calls = [
        call
        for message in messages
        if isinstance(message, dict)
        for call in message.get("tool_calls", [])
    ]
    step = len(calls)
    tool_results = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    if step > len(STEPS):
        raise FixtureFailure("step_invalid")
    if len(tool_results) != step:
        raise FixtureFailure("tool_result_count")
    proposal = next((x for x in objects if {"proposal_id", "proposal_digest"} <= set(x)), None)
    if step == len(STEPS):
        if proposal is None:
            raise FixtureFailure("proposal_missing")
        reference = {key: proposal[key] for key in ("proposal_id", "proposal_digest")}
        reference["schema_id"] = SCHEMA_ID
        if invalid:
            reference["proposal_id"] = str(uuid.uuid4())
        message = {"role": "assistant", "content": json.dumps({"proposal_references": [reference]})}
        finish = "stop"
    else:
        name = STEPS[step]
        advertised = [
            x.get("function", {}).get("name", "")
            for x in request.get("tools", [])
            if isinstance(x, dict)
        ]
        matches = [x for x in advertised if x == "mcp__civicloop__" + name]
        if not matches:
            raise FixtureFailure("advertised_tool_missing")
        if len(matches) != 1:
            raise FixtureFailure("advertised_tool_duplicate")
        arguments = {
            **binding,
            "request_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
        }
        if name == "propose_campaign_drafts":
            arguments["proposal"] = {
                "event_copy": PROHIBITED_MARKER,
                "invitation": {"subject": "Synthetic invitation", "body": PROHIBITED_MARKER},
                "reminder": {"subject": "Synthetic reminder", "body": PROHIBITED_MARKER},
                "social": {"body": PROHIBITED_MARKER},
            }
        elif step >= 3:
            if proposal is None:
                raise FixtureFailure("proposal_missing")
            arguments["proposal_id"] = proposal["proposal_id"]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_" + uuid.uuid4().hex,
                    "type": "function",
                    "function": {"name": matches[0], "arguments": json.dumps(arguments)},
                }
            ],
        }
        finish = "tool_calls"
    return {
        "id": "chatcmpl-task8",
        "object": "chat.completion",
        "created": 1,
        "model": "synthetic-upstream",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
    }


def request_shape(request):
    """Retain only bounded structural counts; no names, identifiers or content."""
    counts = dict.fromkeys(REQUEST_SHAPE_FIELDS, 0)
    if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
        return counts
    messages = request["messages"]
    calls = [
        call for message in messages
        if isinstance(message, dict) and isinstance(message.get("tool_calls", []), list)
        for call in message.get("tool_calls", [])
    ]
    tools = request.get("tools", [])
    tools = tools if isinstance(tools, list) else []
    names = [
        tool["function"].get("name") for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    ]
    step = len(calls)
    counts.update(
        message_count=len(messages),
        binding_count=sum(set(value) == BINDING_FIELDS for value in _objects(messages)),
        assistant_call_count=step,
        tool_result_count=sum(isinstance(m, dict) and m.get("role") == "tool" for m in messages),
        advertised_tool_count=len(tools),
        expected_next_tool_count=names.count("mcp__civicloop__" + STEPS[step])
        if step < len(STEPS) else 0,
        system_message_count=sum(
            isinstance(m, dict) and m.get("role") == "system" for m in messages
        ),
        user_message_count=sum(isinstance(m, dict) and m.get("role") == "user" for m in messages),
    )
    return {key: min(value, 4096) for key, value in counts.items()}


class ModelServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, Handler)
        self.mode = "success"
        self.calls = 0
        self.failures = 0
        self.failure_categories = dict.fromkeys(FAILURE_CATEGORIES, 0)
        self.last_request_shape = {}
        self.blocked = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        payload = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError, ConnectionResetError:
            pass

    def do_GET(self):  # noqa: N802
        if self.path != "/fixture/state":
            return self.reply(404, {"status": "denied"})
        with self.server.lock:
            state = {
                "call_count": self.server.calls,
                "failure_count": self.server.failures,
                "blocked": self.server.blocked.is_set(),
                "failure_categories": self.server.failure_categories.copy(),
                "request_shape": self.server.last_request_shape.copy(),
            }
        self.reply(200, state)

    def do_POST(self):  # noqa: N802
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1048576:
                raise FixtureFailure("request_schema")
            body = json.loads(self.rfile.read(size))
            if self.path == "/fixture/mode":
                if set(body) != {"mode"} or body["mode"] not in {
                    "success",
                    "invalid",
                    "hold",
                    "release",
                }:
                    raise FixtureFailure("mode_schema")
                self.server.mode = body["mode"]
                self.server.blocked.clear()
                if body["mode"] == "hold":
                    self.server.release.clear()
                else:
                    self.server.release.set()
                return self.reply(200, {"status": "accepted"})
            if self.path != "/v1/chat/completions":
                return self.reply(404, {"status": "denied"})
            with self.server.lock:
                self.server.calls += 1
                mode = self.server.mode
                self.server.last_request_shape = request_shape(body)
            if mode == "hold":
                self.server.blocked.set()
                if not self.server.release.wait(150):
                    raise FixtureFailure("hold_deadline")
            result = completion(body, invalid=mode == "invalid")
            self.reply(200, result)
        except Exception as error:
            with self.server.lock:
                self.server.failures += 1
                category = (
                    error.category if isinstance(error, FixtureFailure) else "internal_contract"
                )
                self.server.failure_categories[category] += 1
            self.reply(503, {"error": {"message": "fixture_contract_failed"}})


if __name__ == "__main__":
    ModelServer(("0.0.0.0", 8088)).serve_forever()
