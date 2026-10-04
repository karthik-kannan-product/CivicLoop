"""Cheap fixture checks and the separately opted-in exact application gate."""

import json
import os
import secrets
import subprocess
import sys
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.fakes.hermes_contract_http import owner_http
from tests.fakes.hermes_contract_model import (
    FAILURE_CATEGORIES,
    REQUEST_SHAPE_FIELDS,
    STEPS,
    FixtureFailure,
    ModelServer,
    completion,
    request_shape,
)
from tests.fakes.hermes_pending_operations_contract import (
    SCENARIOS,
    ContractFailure,
    load_candidate,
    run_pending_operations_contract,
    standalone_config,
)


def request():
    binding = {
        "workflow_id": str(uuid.uuid4()),
        "revision_id": 1,
        "actor_id": "synthetic_owner",
        "correlation_id": str(uuid.uuid4()),
    }
    return {
        "model": "synthetic",
        "messages": [{"role": "user", "content": json.dumps(binding)}],
        "tools": [
            {"type": "function", "function": {"name": "mcp__civicloop__" + name}} for name in STEPS
        ],
    }


def test_scripted_model_uses_advertised_exact_names_and_real_proposal_result():
    body = request()
    proposal_id = str(uuid.uuid4())
    for index, name in enumerate(STEPS):
        response = completion(body)
        message = response["choices"][0]["message"]
        call = message["tool_calls"][0]
        assert call["function"]["name"] == "mcp__civicloop__" + name
        arguments = json.loads(call["function"]["arguments"])
        assert str(uuid.UUID(arguments["request_id"])) == arguments["request_id"]
        assert str(uuid.UUID(arguments["idempotency_key"])) == arguments["idempotency_key"]
        if index >= 3:
            assert arguments["proposal_id"] == proposal_id
        body["messages"].extend(
            [
                message,
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(
                        {"proposal_id": proposal_id, "proposal_digest": "a" * 64}
                        if index == 2
                        else {"status": "accepted"}
                    ),
                },
            ]
        )
    reference = json.loads(completion(body)["choices"][0]["message"]["content"])[
        "proposal_references"
    ][0]
    assert reference["proposal_id"] == proposal_id
    assert reference["proposal_digest"] == "a" * 64
    assert (
        json.loads(completion(body, invalid=True)["choices"][0]["message"]["content"])[
            "proposal_references"
        ][0]["proposal_id"]
        != proposal_id
    )


def test_fixture_fails_closed_without_binding_or_advertised_tool():
    with pytest.raises(FixtureFailure, match="fixture_contract_failed"):
        completion({"messages": [], "tools": []})
    body = request()
    body["tools"] = []
    with pytest.raises(FixtureFailure, match="fixture_contract_failed"):
        completion(body)


@pytest.mark.parametrize("category", [
    "binding_missing", "binding_mismatch", "binding_uuid", "revision_invalid", "actor_invalid",
    "advertised_tool_missing", "advertised_tool_duplicate", "tool_result_count", "step_invalid",
    "proposal_missing", "request_schema",
])
def test_model_failure_reasons_are_closed_and_identify_rejected_contract(category):
    body = request()
    binding = json.loads(body["messages"][0]["content"])
    if category == "request_schema":
        body = []
    elif category == "binding_missing":
        body["messages"] = []
    elif category == "binding_mismatch":
        body["messages"].append({"role": "user", "content": json.dumps(
            binding | {"revision_id": 2})})
    elif category in {"binding_uuid", "revision_invalid", "actor_invalid"}:
        field, value = {
            "binding_uuid": ("workflow_id", "synthetic_sensitive_marker"),
            "revision_invalid": ("revision_id", True),
            "actor_invalid": ("actor_id", "synthetic sensitive marker"),
        }[category]
        body["messages"][0]["content"] = json.dumps(binding | {field: value})
    elif category == "advertised_tool_missing":
        body["tools"] = []
    elif category == "advertised_tool_duplicate":
        body["tools"].append(body["tools"][0])
    else:
        count = 7 if category == "step_invalid" else 3 if category == "proposal_missing" else 1
        body["messages"].append({"role": "assistant", "tool_calls": [{}] * count})
        if category != "tool_result_count":
            body["messages"].extend([{"role": "tool", "content": "{}"}] * count)
    with pytest.raises(FixtureFailure) as caught:
        completion(body)
    assert caught.value.category == category
    assert str(caught.value) == "fixture_contract_failed"
    assert FixtureFailure("synthetic_sensitive_marker").category == "internal_contract"


def test_model_http_failure_state_contains_counts_and_closed_categories_only():
    server = ModelServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = request()
    marker = "synthetic_sensitive_marker_" + secrets.token_hex(16)
    body["tools"] = [{"function": {"name": marker}}]
    base = f"http://127.0.0.1:{server.server_port}"
    wire_request = urllib.request.Request(base + "/v1/chat/completions",
                                          data=json.dumps(body).encode(),
                                          headers={"Authorization": "Bearer " + marker})
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(wire_request, timeout=2)
        assert caught.value.code == 503
        with urllib.request.urlopen(base + "/fixture/state", timeout=2) as response:
            state = json.loads(response.read())
        assert state["call_count"] == state["failure_count"] == 1
        assert set(state["failure_categories"]) == set(FAILURE_CATEGORIES)
        assert state["failure_categories"]["advertised_tool_missing"] == 1
        assert sum(state["failure_categories"].values()) == 1
        assert set(state["request_shape"]) == set(REQUEST_SHAPE_FIELDS)
        assert state["request_shape"]["binding_count"] == 1
        assert state["request_shape"]["advertised_tool_count"] == 1
        assert state["request_shape"]["expected_next_tool_count"] == 0
        assert all(type(value) is int for value in state["request_shape"].values())
        assert marker not in json.dumps(state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("module_mode", [False, True])
def test_harness_help_supports_direct_script_and_package(module_mode):
    from tests.fakes.hermes_pending_operations_contract import ROOT

    target = ["-m", "tests.fakes.hermes_pending_operations_contract"] if module_mode else [
        str(ROOT / "tests/fakes/hermes_pending_operations_contract.py")
    ]
    result = subprocess.run([sys.executable, *target, "--help"], cwd=ROOT,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=10)
    assert result.returncode == 0
    assert "--help" in result.stdout


@pytest.mark.parametrize(
    "field,value",
    [
        ("workflow_id", "bad"),
        ("revision_id", True),
        ("actor_id", "with space"),
        ("correlation_id", "bad"),
    ],
)
def test_scripted_model_rejects_invalid_trusted_input(field, value):
    body = request()
    identifiers = json.loads(body["messages"][0]["content"])
    identifiers[field] = value
    body["messages"][0]["content"] = json.dumps(identifiers)
    with pytest.raises((FixtureFailure, ValueError)):
        completion(body)


def test_model_hold_release_exposes_closed_counters_only():
    server = ModelServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def post(path, body):
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as reply:
            return json.loads(reply.read())

    result = []
    caller = None
    try:
        assert post("/fixture/mode", {"mode": "hold"}) == {"status": "accepted"}
        caller = threading.Thread(
            target=lambda: result.append(post("/v1/chat/completions", request()))
        )
        caller.start()
        assert server.blocked.wait(2)
        with urllib.request.urlopen(base + "/fixture/state", timeout=2) as reply:
            state = json.loads(reply.read())
        assert state == {
            "call_count": 1, "failure_count": 0, "blocked": True,
            "failure_categories": dict.fromkeys(FAILURE_CATEGORIES, 0),
            "request_shape": request_shape(request()),
        }
        assert post("/fixture/mode", {"mode": "release"}) == {"status": "accepted"}
        caller.join(2)
        assert len(result) == 1
    finally:
        server.release.set()
        if caller is not None:
            caller.join(2)
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_real_owner_http_preserves_cookie_csrf_idempotency_and_json_body():
    session, csrf, key = secrets.token_hex(16), secrets.token_hex(16), str(uuid.uuid4())
    checks = []

    class OwnerHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            checks.append(
                {
                    "cookie": self.headers.get("Cookie")
                    == f"sessionid={session}; csrftoken={csrf}",
                    "csrf": self.headers.get("X-CSRFToken") == csrf,
                    "idempotency": self.headers.get("Idempotency-Key") == key,
                    "body": body == {"revision_id": 1},
                    "path": self.path == "/api/v1/example",
                }
            )
            raw = b'{"schema_version":"1.0","status":"queued"}'
            self.send_response(202)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), OwnerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = {
            "path": "/api/v1/example",
            "body": {"revision_id": 1},
            "session": session,
            "csrf": csrf,
            "idempotency_key": key,
        }
        reply = owner_http(payload, base_url=f"http://127.0.0.1:{server.server_port}")
        assert reply == {"http_status": 202, "body": {"schema_version": "1.0", "status": "queued"}}
        assert len(checks) == 1
        assert all(checks[0].values())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("oversized", "owner_response_bound"),
        ("invalid_json", "owner_response_schema"),
        ("csrf_html", "owner_response_schema"),
        ("server_html", "owner_response_schema"),
        ("echo", "prohibited_authority"),
    ],
)
def test_owner_http_bounds_and_private_header_echo_are_closed(kind, expected):
    session = secrets.token_hex(16)

    class BoundaryHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            raw = {
                "oversized": b"x" * 65537,
                "invalid_json": b"not json",
                "echo": json.dumps({"echo": session}).encode(),
                "csrf_html": b"<html>CSRF verification failed SYNTHETIC_FORBIDDEN_BODY</html>",
                "server_html": b"<html>Server Error SYNTHETIC_FORBIDDEN_BODY</html>",
            }[kind]
            status = 403 if kind == "csrf_html" else 500 if kind == "server_html" else 200
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except BrokenPipeError, ConnectionResetError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), BoundaryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reply = owner_http(
            {
                "path": "/api/v1/example",
                "body": None,
                "session": session,
                "csrf": "",
                "idempotency_key": "",
            },
            base_url=f"http://127.0.0.1:{server.server_port}",
        )
        assert reply["failure_category"] == expected
        if kind == "invalid_json":
            assert reply["response_evidence"]["body_length"] == 8
        if kind in {"csrf_html", "server_html"}:
            evidence = reply["response_evidence"]
            assert evidence["http_status"] == (403 if kind == "csrf_html" else 500)
            assert evidence["content_type"] == "text/html"
            assert evidence["csrf_rejected"] == (kind == "csrf_html")
            assert evidence["server_error"] == (kind == "server_html")
            assert len(evidence["body_digest"]) == 64
            assert "SYNTHETIC_FORBIDDEN_BODY" not in json.dumps(reply)
        assert session not in json.dumps(reply)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_owner_command_transfers_authority_only_through_stdin():
    from tests.fakes import hermes_pending_operations_contract as harness

    stack = harness.Stack.__new__(harness.Stack)
    captured = []
    stack.scan = lambda _: None

    def compose(*arguments, **kwargs):
        payload = json.loads(kwargs["input_text"])
        captured.append(
            {
                "argv_safe": all(
                    payload[field] not in json.dumps(arguments)
                    for field in ("session", "csrf", "idempotency_key")
                ),
                "body": payload["body"] == {"revision_id": 1},
            }
        )
        return '{"http_status":202,"body":{"status":"queued"}}'

    stack.compose = compose
    seed = {"session": secrets.token_hex(16), "csrf": secrets.token_hex(16)}
    assert stack.owner(seed, "/api/v1/example", {"revision_id": 1}, str(uuid.uuid4())) == (
        202,
        {"status": "queued"},
    )
    assert captured == [{"argv_safe": True, "body": True}]


def test_owner_stdin_bound_failure_never_emits_request_material():
    import sys
    from pathlib import Path

    from tests.fakes import hermes_pending_operations_contract as harness

    raw = harness.command(
        [sys.executable, str(Path(owner_http.__globals__["__file__"]))],
        input_text="SYNTHETIC_FORBIDDEN_STDIN_VALUE" * 3000,
    )
    assert json.loads(raw) == {"failure_category": "owner_request_invalid"}
    assert "SYNTHETIC_FORBIDDEN_STDIN_VALUE" not in raw


def test_manifest_missing_is_safe_and_does_not_launch():
    with pytest.raises(ContractFailure, match="manifest_unavailable"):
        load_candidate("TASK8_NONEXISTENT_SYNTHETIC_MANIFEST.json")


def test_mutable_image_manifest_is_rejected_before_any_command(monkeypatch):
    from pathlib import Path

    from tests.fakes import hermes_pending_operations_contract as harness

    candidate = {
        "public_sha": "a" * 40,
        "operations_sha": "a" * 40,
        "source_tree_digest": "b" * 64,
        "runtime_tree_digest": "b" * 64,
        "production_compose": "/synthetic/compose.production.yaml",
        "production_compose_digest": "b" * 64,
        "images": {name: "sha256:" + "b" * 64 for name in harness.IMAGE_KEYS},
    }
    candidate["images"]["app"] = "civicloop:local"
    monkeypatch.setattr(Path, "read_text", lambda *_: json.dumps(candidate))

    def forbidden_command(*_, **__):
        pytest.fail("manifest validation attempted a command before rejecting mutable image")

    monkeypatch.setattr(harness, "command", forbidden_command)
    with pytest.raises(ContractFailure, match="mutable_image"):
        load_candidate("synthetic-manifest")


def test_failure_diagnostics_emit_only_closed_flags_counts_and_hash():
    from tests.fakes.hermes_pending_operations_contract import diagnostic

    raw = b"Permission denied; unrecognized arguments; SYNTHETIC_FORBIDDEN_DIAGNOSTIC_VALUE"
    evidence = diagnostic(raw)
    assert evidence["category_flags"]["permission_denied"] is True
    assert evidence["category_flags"]["cli_arguments_invalid"] is True
    assert evidence["byte_count"] == len(raw)
    assert len(evidence["digest"]) == 64
    assert "SYNTHETIC_FORBIDDEN_DIAGNOSTIC_VALUE" not in json.dumps(evidence)


def test_failed_subprocess_output_is_categorized_without_text(monkeypatch):
    import subprocess

    from tests.fakes import hermes_pending_operations_contract as harness

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess(
            ["synthetic"], 2, stdout="", stderr="unrecognized arguments; SYNTHETIC_FORBIDDEN_VALUE"
        ),
    )
    with pytest.raises(ContractFailure) as failure:
        harness.command(["synthetic"])
    assert failure.value.category == "command_failed"
    assert failure.value.details["return_code"] == 2
    assert failure.value.details["output"]["category_flags"]["cli_arguments_invalid"] is True
    assert "SYNTHETIC_FORBIDDEN_VALUE" not in json.dumps(failure.value.details)


def test_real_subprocess_utf8_output_and_invalid_bytes_remain_safe():
    import sys

    from tests.fakes import hermes_pending_operations_contract as harness

    # The goat glyph includes byte0x90, undefined in Windows cp1252. An invalid
    # UTF8 suffix also exercises replacement without dropping closed diagnostics.
    success = harness.command(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes([240,159,144,144]))"]
    )
    assert success == "\U0001f410"
    with pytest.raises(ContractFailure) as failure:
        harness.command(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(bytes([240,159,144,144,255])); "
                "sys.stderr.buffer.write(b'Usage: synthetic; SYNTHETIC_FORBIDDEN_UNICODE_BODY'); "
                "sys.exit(2)",
            ]
        )
    assert failure.value.category == "command_failed"
    assert failure.value.details["return_code"] == 2
    assert failure.value.details["output"]["category_flags"]["cli_arguments_invalid"] is True
    assert len(failure.value.details["output"]["digest"]) == 64
    assert "SYNTHETIC_FORBIDDEN_UNICODE_BODY" not in json.dumps(failure.value.details)
    assert "\U0001f410" not in json.dumps(failure.value.details, ensure_ascii=False)


def test_failure_snapshot_includes_exited_services_and_safe_log_categories(monkeypatch):
    from tests.fakes import hermes_pending_operations_contract as harness

    stack = harness.Stack.__new__(harness.Stack)
    stack.stage = "stack_startup"
    stack.config = {"services": {"litellm": {}}}
    stack.compose = lambda *_, **__: "fixture-container-id"

    def fake_command(arguments, **_):
        if arguments[1] == "logs":
            return "Usage: litellm; SYNTHETIC_FORBIDDEN_VALUE"
        if "Health.Log" in arguments[3]:
            return "[]"
        return json.dumps(
            {
                "service": "litellm",
                "status": "exited",
                "exit_code": 2,
                "oom": False,
                "restarts": 0,
                "health": "none",
            }
        )

    monkeypatch.setattr(harness, "command", fake_command)
    evidence = stack.failure_snapshot()
    assert evidence["stage"] == "stack_startup"
    assert evidence["snapshot_status"] == "complete"
    assert evidence["services"]["litellm"]["exit_code"] == 2
    assert (
        evidence["services"]["litellm"]["log_evidence"]["category_flags"]["cli_arguments_invalid"]
        is True
    )
    assert "SYNTHETIC_FORBIDDEN_VALUE" not in json.dumps(evidence)


@pytest.mark.django_db
def test_container_seed_creates_synthetic_owner_and_ready_deterministic_binding():
    from agents.models import ModelProfile, RoutingPolicy
    from identity.models import AdministratorSession
    from launchloop.models import Workflow

    from tests.fakes.hermes_contract_fixture import seed

    # Session and CSRF values remain in memory and never enter assertion evidence.
    payload = seed()
    workflow = Workflow.objects.get(pk=payload["workflow_id"])
    assert workflow.status == "ready_for_review"
    assert workflow.package["status"] == "ready_for_review"
    assert workflow.package_hash == payload["package_digest"]
    assert workflow.revision.snapshot["synthetic"] is True
    assert AdministratorSession.objects.count() == 1
    profile = ModelProfile.objects.get(profile_id="task8_fixture")
    assert profile.max_input_tokens == 500000
    assert profile.max_output_tokens == 100000
    assert RoutingPolicy.objects.get(model_profile=profile).per_run_limit_microusd == 500000


@pytest.mark.django_db
def test_seeded_session_csrf_and_actual_owner_start_use_canonical_actor(settings, monkeypatch):
    from types import SimpleNamespace

    from django.test import Client
    from launchloop.models import DemoActor, Workflow

    from tests.fakes.hermes_contract_fixture import seed

    settings.CIVICLOOP_ADMIN_IDENTITY_ENABLED = True
    settings.CIVICLOOP_HERMES_ENABLED = True
    settings.CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED = True
    payload = seed()
    actor = Workflow.objects.get(pk=payload["workflow_id"]).revision.author
    client = Client(enforce_csrf_checks=True)
    client.cookies["sessionid"] = payload["session"]
    client.cookies["csrftoken"] = payload["csrf"]
    path = f"/api/v1/workflows/{payload['workflow_id']}/hermes-runs"
    arguments = {
        "data": json.dumps({"revision_id": payload["revision_id"]}),
        "content_type": "application/json",
        "HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4()),
    }
    calls = []
    run_id = uuid.uuid4()

    def dispatch(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id=run_id)

    # Only asynchronous admission/dispatch is isolated; real middleware, CSRF,
    # seeded session, owner authorization and canonical actor DB update execute.
    monkeypatch.setattr("agents.tasks.start_hermes_run", dispatch)
    rejected = client.post(path, HTTP_X_CSRFTOKEN=secrets.token_hex(16), **arguments)
    assert rejected.status_code == 403
    assert calls == []
    accepted = client.post(path, HTTP_X_CSRFTOKEN=payload["csrf"], **arguments)
    assert accepted.status_code == 202
    assert accepted.json() == {"schema_version": "1.0", "run_id": str(run_id), "status": "queued"}
    assert len(calls) == 1
    assert calls[0]["actor_slug"] == actor.slug
    assert DemoActor.objects.filter(user=actor.user).count() == 1


@pytest.mark.django_db
def test_repeated_scenario_seed_reuses_single_owner_session_and_actor():
    from identity.models import AdministratorProfile, AdministratorSession
    from launchloop.models import DemoActor, Workflow

    from tests.fakes.hermes_contract_fixture import seed

    first, second = seed(), seed()
    assert first["workflow_id"] != second["workflow_id"]
    assert first["session"] == second["session"]
    assert AdministratorProfile.objects.exclude(status="disabled").count() == 1
    assert AdministratorSession.objects.count() == 1
    assert DemoActor.objects.count() == 1
    assert Workflow.objects.get(pk=first["workflow_id"]).revision.author_id == (
        Workflow.objects.get(pk=second["workflow_id"]).revision.author_id
    )


@pytest.mark.django_db
def test_old_fixture_actor_slug_reproduces_actual_owner_operator_collision():
    from django.contrib.auth.models import User
    from django.db import IntegrityError, transaction
    from identity.models import AdministratorProfile, AdministratorSession
    from launchloop.models import DemoActor
    from launchloop.pilot import owner_operator

    user = User.objects.create(username="synthetic-collision-proof")
    profile = AdministratorProfile.objects.create(user=user, status="active")
    DemoActor.objects.create(slug="old-fixture-owner", user=user, role="operator")
    with pytest.raises(IntegrityError), transaction.atomic():
        owner_operator(AdministratorSession(profile=profile))
    assert DemoActor.objects.filter(user=user).count() == 1


def test_isolated_config_preserves_actual_commands_and_network_boundaries():
    from pathlib import Path

    private = Path(__file__).resolve().parents[2] / "compose.agent.yaml"
    candidate = {
        "production_compose": str(private),
        "operations_sha": "a" * 40,
        "images": {
            name: "sha256:" + "b" * 64
            for name in ("app", "hermes", "litellm", "phoenix", "postgres", "valkey")
        },
    }
    config = standalone_config(candidate)
    services = config["services"]
    assert all(
        services[name]["environment"]["VALKEY_URL"] == "redis://valkey:6379/0"
        for name in ("web", "worker", "mcp", "migrate")
    )
    assert services["worker"]["command"] == ["worker"]
    assert services["litellm"]["entrypoint"] == ["python", "/app/gateway.py"]
    assert services["litellm"]["command"] == []
    assert services["hermes"]["command"][-1] == "deploy.hermes.controller_service"
    assert services["hermes"]["init"] is True
    assert set(services["hermes"]["networks"]) == {"hermes-runtime"}
    assert all(network["internal"] for network in config["networks"].values())
    assert all("ports" not in service for service in services.values())
    assert all(service["restart"] == "no" for service in services.values())
    assert all(
        "env_file" not in service and "build" not in service for service in services.values()
    )
    assert "scheduler" not in services
    assert "provider-egress" not in services["hermes"]["networks"]
    assert services["phoenix"]["environment"]["PHOENIX_ENABLE_AUTH"] == "True"
    assert services["phoenix"]["environment"]["PHOENIX_USE_SECURE_COOKIES"] == "True"
    assert services["phoenix"]["environment"]["PHOENIX_DEFAULT_RETENTION_POLICY_DAYS"] == "14"
    assert services["phoenix"]["environment"]["PHOENIX_WORKING_DIR"] == "/data"
    assert services["phoenix"]["user"] == "65532:65532"
    assert services["phoenix"]["read_only"] is True
    assert set(SCENARIOS) == {
        "delayed_revoked_a",
        "success_b",
        "invalid_output",
        "cancellation",
        "kill_switch",
        "mcp_outage",
        "litellm_outage",
        "phoenix_outage",
        "restored_success",
    }


def test_real_django_cache_configuration_uses_fixture_valkey():
    import subprocess
    import sys
    from pathlib import Path

    candidate = {
        "production_compose": str(Path(__file__).resolve().parents[2] / "compose.agent.yaml"),
        "operations_sha": "a" * 40,
        "images": {
            name: "sha256:" + "b" * 64
            for name in ("app", "hermes", "litellm", "phoenix", "postgres", "valkey")
        },
    }
    environment = dict(os.environ)
    environment.update(standalone_config(candidate)["services"]["web"]["environment"])
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "backend")
    environment["DJANGO_SETTINGS_MODULE"] = "civicloop.settings"
    # This offline cache resolver has no mounted synthetic owner identity.
    environment["CIVICLOOP_ADMIN_IDENTITY_ENABLED"] = "false"
    # Real Django RedisCache resolves the fixture host without opening a socket.
    code = (
        "from django.core.cache import caches; "
        "c=caches['default']._cache.get_client(); "
        "k=c.connection_pool.connection_kwargs; "
        "print(k['host']=='valkey' and k['port']==6379 and k['db']==0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=15,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "True"


def test_readiness_preserves_safe_real_health_response_at_deadline(monkeypatch):
    from django.test import RequestFactory
    from health import checks, views

    from tests.fakes import hermes_pending_operations_contract as harness

    monkeypatch.setattr(checks, "postgres_is_ready", lambda: True)
    monkeypatch.setattr(checks, "valkey_is_ready", lambda: False)
    response = views.ready(RequestFactory().get("/api/v1/health/ready"))
    stack = harness.Stack.__new__(harness.Stack)
    stack.owner = lambda *_: (response.status_code, json.loads(response.content))
    with pytest.raises(ContractFailure) as caught:
        stack.await_readiness(timeout=0)
    assert caught.value.category == "stack_readiness_deadline"
    assert caught.value.details == {
        "failure_category": "deterministic_readiness",
        "details": {"http_status": 503, "dependencies": {"postgres": True, "valkey": False}},
    }


def test_readiness_preserves_closed_helper_command_failure():
    from tests.fakes import hermes_pending_operations_contract as harness

    stack = harness.Stack.__new__(harness.Stack)

    def unavailable(*_, **__):
        raise ContractFailure("command_failed", details={"return_code": 2})

    stack.compose = unavailable
    with pytest.raises(ContractFailure) as caught:
        stack.await_readiness(timeout=0)
    assert caught.value.details == {
        "failure_category": "command_failed", "details": {"return_code": 2}
    }


def test_hold_wait_aborts_on_real_terminal_category_before_model_poll():
    from tests.fakes import hermes_pending_operations_contract as harness

    stack = harness.Stack.__new__(harness.Stack)
    stack.active_run_id = str(uuid.uuid4())
    stack.readiness = lambda: None
    snapshot = {
        "terminal_status": "failed", "failure_category": "dependency_unavailable",
        "event_count": 2, "events_digest": "a" * 64,
    }
    stack.fixture = lambda *args: snapshot
    stack.internal = lambda *_: pytest.fail("terminal run must not poll model")
    with pytest.raises(ContractFailure) as caught:
        stack.held()
    assert caught.value.category == "run_terminal_before_model_hold"
    assert caught.value.details == snapshot


def test_failed_stage_retains_started_run_before_cleanup():
    from tests.fakes import hermes_pending_operations_contract as harness

    stack = harness.Stack.__new__(harness.Stack)
    stack.stage = "delayed_revoked_a"
    stack.active_run_id = str(uuid.uuid4())
    stack.config = {"services": {}}
    stack.compose = lambda *_, **__: ""
    stack.fixture = lambda *args: {"terminal_status": "failed", "event_count": 2}
    stack.controller_counts = lambda: {"child_count": 0, "home_count": 0}
    stack.controller_phase = lambda: {"phase": "not_registered", "failure_count": 0}
    stack.internal = lambda service, _: (
        {"call_count": 0, "failure_count": 0, "blocked": False,
         "failure_categories": dict.fromkeys(FAILURE_CATEGORIES, 0), "request_shape": {}}
        if service.startswith("fixture-model") else {"scope_count": 0}
    )
    stack.memory_events = lambda _: {"oom": 0, "oom_kill": 1}
    evidence = stack.failure_snapshot()
    assert evidence["started_run"]["terminal_status"] == "failed"
    assert evidence["controller_phase"]["phase"] == "not_registered"
    assert evidence["observer_counts"]["model_call_count"] == 0
    assert evidence["model_failure_categories"] == dict.fromkeys(FAILURE_CATEGORIES, 0)
    assert evidence["memory_events"]["hermes"]["oom_kill"] == 1
    assert stack.active_run_id not in json.dumps(evidence)


@pytest.mark.django_db
def test_actual_run_inspection_emits_only_closed_event_categories(settings, monkeypatch):
    from agents.models import AgentRunEvent

    from tests.agents import test_hermes_tasks
    from tests.fakes.hermes_contract_fixture import inspect

    inputs = test_hermes_tasks.inputs.__wrapped__(settings, monkeypatch)
    run = test_hermes_tasks.queue(inputs)
    AgentRunEvent.objects.create(
        run=run, sequence=2, event_type="SYNTHETIC_FORBIDDEN_VALUE",
        outcome="SYNTHETIC_FORBIDDEN_VALUE", detail_digest="a" * 64,
    )
    evidence = inspect(str(run.id))
    assert evidence["terminal_status"] == "queued"
    assert evidence["event_categories"]["queued"] == 1
    assert evidence["event_outcomes"]["accepted"] == 1
    assert evidence["other_event_count"] == 1
    assert "SYNTHETIC_FORBIDDEN_VALUE" not in json.dumps(evidence)


@pytest.mark.django_db
def test_controller_phase_queries_real_derived_binding_uuid_privately(settings, monkeypatch):
    from agents.hermes import HermesClient

    from tests.agents import test_hermes_tasks
    from tests.fakes import hermes_pending_operations_contract as harness
    from tests.fakes.hermes_contract_fixture import controller_binding

    inputs = test_hermes_tasks.inputs.__wrapped__(settings, monkeypatch)
    run = test_hermes_tasks.queue(inputs)
    binding = controller_binding(str(run.id))
    expected = HermesClient._run_id(run)
    assert binding == {"controller_run_id": expected}
    # Current admission deliberately uses this same UUID as AgentRun.id.
    assert expected == str(run.id)
    stack = harness.Stack.__new__(harness.Stack)
    stack.active_run_id = str(run.id)
    stack.fixture = lambda name, run_id: controller_binding(run_id)
    captured = []

    def compose(*arguments, **kwargs):
        captured.append({
            "derived_id": json.loads(kwargs["input_text"]) == expected,
            "argv_private": expected not in json.dumps(arguments),
            "proxies_disabled": "ProxyHandler({})" in arguments[-1],
        })
        return '{"phase":"failed","failure_count":1}'

    stack.compose = compose
    evidence = stack.controller_phase()
    assert captured == [{"derived_id": True, "argv_private": True, "proxies_disabled": True}]
    assert evidence == {"phase": "failed", "failure_count": 1}
    assert expected not in json.dumps(evidence)


@pytest.mark.skipif(
    os.environ.get("CIVICLOOP_RUN_EXACT_HERMES_CONTRACT") != "1",
    reason="exact image application gate requires reviewed frozen manifest",
)
def test_exact_candidate_creates_only_pending_operations():
    evidence = run_pending_operations_contract()
    assert evidence["status"] == "passed", evidence.get("failure_category", "contract_failed")
    assert evidence["proposal_count"] >= 1
    assert evidence["pending_operation_count"] == 3
    assert evidence["pending_provider_count"] == 2
    assert (
        evidence["provider_call_count"]
        == evidence["approval_count"]
        == evidence["receipt_count"]
        == 0
    )
    assert evidence["distinct_assertion_nonces"] == evidence["inference_attempts"] == 7
    assert evidence["cleanup_status"] == "clean"
