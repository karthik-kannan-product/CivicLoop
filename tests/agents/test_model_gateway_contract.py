from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from deploy.litellm.gateway import (
    DurableBudgetLedger,
    GatewayPolicy,
    LiteLLMSupervisor,
    PolicyError,
    _Handler,
    issue_budget_assertion,
    prepare_request,
    provider_neutral_error,
)

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.agent.yaml"
CONFIG = ROOT / "deploy/litellm/config.yaml"
FAKE_SERVER = ROOT / "tests/fakes/openai_compatible_server.py"
ASSERTION_KEY = b"test-only-budget-assertion-key-32-bytes-minimum"
NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def _body(**updates: object) -> dict[str, object]:
    body: dict[str, object] = {
        "model": "civicloop-default",
        "messages": [{"role": "user", "content": "Create an event outline."}],
        "max_tokens": 32,
        "temperature": 0,
    }
    body.update(updates)
    return body


def _tool(name: str = "get_event_revision") -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Read one bounded CivicLoop event revision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "maxLength": 128},
                },
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
    }


def _assertion(*, nonce: str, ceiling: int = 128, run_id: str = "run-123") -> str:
    nonce = f"{nonce}-0000000000000000"
    return issue_budget_assertion(
        key=ASSERTION_KEY,
        run_id=run_id,
        model_alias="civicloop-default",
        token_ceiling=ceiling,
        expires_at=NOW + timedelta(minutes=2),
        nonce=nonce,
    )


def _post(
    port: int,
    body: dict[str, object],
    *,
    assertion: str,
    path: str = "/v1/chat/completions",
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer gateway-test-token",
            "Content-Type": "application/json",
            "X-CivicLoop-Budget-Assertion": assertion,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def fake_provider(
    tmp_path: Path,
) -> tuple[int, Path, Path, subprocess.Popen[str]]:
    port_file = tmp_path / "provider-port"
    capture_file = tmp_path / "provider-capture.json"
    mode_file = tmp_path / "provider-mode"
    mode_file.write_text("compatible", encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "PORT_FILE": str(port_file),
            "CAPTURE_FILE": str(capture_file),
            "MODE_FILE": str(mode_file),
        }
    )
    process = subprocess.Popen(
        [sys.executable, str(FAKE_SERVER)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if port_file.exists():
                yield int(port_file.read_text()), capture_file, mode_file, process
                break
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            time.sleep(0.02)
        else:
            raise AssertionError("fake provider did not start")
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.fixture
def gateway_port(
    fake_provider: tuple[int, Path, Path, subprocess.Popen[str]], tmp_path: Path
) -> int:
    provider_port, _, _, _ = fake_provider
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.policy = GatewayPolicy(  # type: ignore[attr-defined]
        alias="civicloop-default", max_tokens=2000, timeout_seconds=1
    )
    server.ledger = DurableBudgetLedger(tmp_path / "budget-ledger.sqlite3")  # type: ignore[attr-defined]
    server.assertion_key = ASSERTION_KEY  # type: ignore[attr-defined]
    server.now = lambda: NOW  # type: ignore[attr-defined]
    server.inference_slot = threading.BoundedSemaphore(1)  # type: ignore[attr-defined]
    server.client_token = "gateway-test-token"  # type: ignore[attr-defined]
    server.litellm_master_key = "internal-test-key"  # type: ignore[attr-defined]
    server.response_secrets = (  # type: ignore[attr-defined]
        "provider-test-key",
        "internal-test-key",
        "gateway-test-token",
        ASSERTION_KEY.decode("utf-8"),
    )
    server.upstream = f"http://127.0.0.1:{provider_port}"  # type: ignore[attr-defined]
    server.supervisor = SimpleNamespace(is_running=lambda: True)  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("api_base", "http://attacker.invalid/v1"),
        ("base_url", "http://attacker.invalid/v1"),
        ("api_key", "stolen"),
        ("custom_llm_provider", "attacker"),
        ("deployment", "other"),
        ("routing_strategy", "attacker"),
        ("session_id", "other-session"),
        ("max_completion_tokens", 32),
        ("metadata", {"tags": ["forged"]}),
        ("user", "forged-user"),
        ("parallel_tool_calls", False),
    ],
)
def test_request_schema_rejects_every_override_and_alternate_token_field(
    tmp_path: Path, field: str, value: object
) -> None:
    with pytest.raises(PolicyError, match="unsupported request field"):
        prepare_request(
            _body(**{field: value}),
            budget_assertion=_assertion(nonce=f"nonce-{field}"),
            assertion_key=ASSERTION_KEY,
            policy=GatewayPolicy("civicloop-default", 2000, 60),
            ledger=DurableBudgetLedger(tmp_path / "ledger.sqlite3"),
            now=NOW,
        )


def test_hermes_non_streaming_tool_round_trip_is_reconstructed(
    tmp_path: Path,
) -> None:
    ledger = DurableBudgetLedger(tmp_path / "tool-ledger.sqlite3")
    policy = GatewayPolicy("civicloop-default", 2000, 60)
    first = prepare_request(
        _body(tools=[_tool()], tool_choice="auto", stream=False),
        budget_assertion=_assertion(
            nonce="tool-first", ceiling=128, run_id="tool-round-trip"
        ),
        assertion_key=ASSERTION_KEY,
        policy=policy,
        ledger=ledger,
        now=NOW,
    )
    assert first["tools"] == [_tool()]
    assert first["tool_choice"] == "auto"
    assert "stream" not in first

    second = prepare_request(
        _body(
            tools=[_tool()],
            messages=[
                {"role": "user", "content": "Read event evt-123."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_evt_123",
                            "type": "function",
                            "function": {
                                "name": "get_event_revision",
                                "arguments": '{"event_id":"evt-123"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_evt_123",
                    "content": '{"revision":7}',
                },
            ],
            stream=False,
        ),
        budget_assertion=_assertion(
            nonce="tool-second", ceiling=128, run_id="tool-round-trip"
        ),
        assertion_key=ASSERTION_KEY,
        policy=policy,
        ledger=ledger,
        now=NOW,
    )
    assert second["messages"][1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_evt_123",
                "type": "function",
                "function": {
                    "name": "get_event_revision",
                    "arguments": '{"event_id":"evt-123"}',
                },
            }
        ],
    }
    assert second["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_evt_123",
        "content": '{"revision":7}',
    }


@pytest.mark.parametrize(
    ("updates", "match"),
    [
        ({"stream": True}, "stream"),
        ({"stream": "false"}, "stream"),
        ({"tools": [_tool(), _tool()]}, "unique"),
        ({"tools": [_tool(f"tool_{index}") for index in range(9)]}, "tools"),
        ({"tools": [{**_tool(), "provider": "forged"}]}, "tool fields"),
        (
            {
                "tools": [_tool()],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "undeclared_tool"},
                },
            },
            "tool choice",
        ),
        (
            {"messages": [{"role": "assistant", "content": None}]},
            "assistant content",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read event."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "duplicate_call",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            },
                            {
                                "id": "duplicate_call",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            },
                        ],
                    },
                ],
            },
            "unique",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read event."},
                    {
                        "role": "tool",
                        "tool_call_id": "call_missing",
                        "content": "{}",
                    },
                ],
            },
            "prior tool call",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read event."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_without_result",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                ],
            },
            "exactly one result",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read event."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_bad_args",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "not-json",
                                },
                            }
                        ],
                    },
                ],
            },
            "arguments",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read event."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_interleaved",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    },
                    {"role": "user", "content": "Interrupt the tool result."},
                ],
            },
            "contiguous and ordered",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read two events."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_first",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            },
                            {
                                "id": "call_second",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            },
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_second", "content": "{}"},
                    {"role": "tool", "tool_call_id": "call_first", "content": "{}"},
                ],
            },
            "contiguous and ordered",
        ),
        (
            {
                "tools": [_tool()],
                "messages": [
                    {"role": "user", "content": "Read two events."},
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_resolved",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            },
                            {
                                "id": "call_unresolved",
                                "type": "function",
                                "function": {
                                    "name": "get_event_revision",
                                    "arguments": "{}",
                                },
                            },
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_resolved", "content": "{}"},
                ],
            },
            "exactly one result",
        ),
    ],
)
def test_hermes_tool_schema_rejects_unsafe_shapes(
    tmp_path: Path, updates: dict[str, object], match: str
) -> None:
    with pytest.raises(PolicyError, match=match):
        prepare_request(
            _body(**updates),
            budget_assertion=_assertion(nonce=f"unsafe-{match.replace(' ', '-')}") ,
            assertion_key=ASSERTION_KEY,
            policy=GatewayPolicy("civicloop-default", 2000, 60),
            ledger=DurableBudgetLedger(tmp_path / "unsafe-ledger.sqlite3"),
            now=NOW,
        )


def test_tool_schema_depth_size_and_arguments_are_bounded(tmp_path: Path) -> None:
    too_deep: dict[str, object] = {"type": "string"}
    for index in range(10):
        too_deep = {f"level_{index}": too_deep}
    deeply_nested_arguments: dict[str, object] = {"value": "bounded"}
    for index in range(10):
        deeply_nested_arguments = {f"level_{index}": deeply_nested_arguments}
    cases = [
        _body(tools=[{**_tool(), "function": {**_tool()["function"], "parameters": too_deep}}]),
        _body(
            tools=[_tool()],
            messages=[
                {"role": "user", "content": "Read event."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_large_args",
                            "type": "function",
                            "function": {
                                "name": "get_event_revision",
                                "arguments": json.dumps({"value": "x" * 20_000}),
                            },
                        }
                    ],
                },
            ],
        ),
        _body(
            tools=[_tool()],
            messages=[
                {"role": "user", "content": "Read event."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_deep_args",
                            "type": "function",
                            "function": {
                                "name": "get_event_revision",
                                "arguments": json.dumps(deeply_nested_arguments),
                            },
                        }
                    ],
                },
            ],
        ),
    ]
    for index, body in enumerate(cases):
        with pytest.raises(PolicyError):
            prepare_request(
                body,
                budget_assertion=_assertion(nonce=f"bounded-{index}"),
                assertion_key=ASSERTION_KEY,
                policy=GatewayPolicy("civicloop-default", 2000, 60),
                ledger=DurableBudgetLedger(tmp_path / f"bounded-{index}.sqlite3"),
                now=NOW,
            )


def test_request_body_depth_is_bounded_before_sanitization(gateway_port: int) -> None:
    deeply_nested: dict[str, object] = {"value": "bounded"}
    for index in range(18):
        deeply_nested = {f"level_{index}": deeply_nested}
    status, response = _post(
        gateway_port,
        _body(metadata=deeply_nested),
        assertion=_assertion(nonce="deep-request-body"),
    )
    assert status == 400
    assert response["error"]["code"] == "invalid_model_request"


def test_strict_request_reaches_downstream_with_only_allowlisted_fields(
    gateway_port: int,
    fake_provider: tuple[int, Path, Path, subprocess.Popen[str]],
) -> None:
    _, capture_file, _, _ = fake_provider
    status, response = _post(
        gateway_port,
        _body(stop=["END"], seed=7, response_format={"type": "json_object"}),
        assertion=_assertion(nonce="capture-nonce"),
    )
    assert status == 200
    assert response["model"] == "configured-upstream"
    captured = json.loads(capture_file.read_text())
    assert set(captured) == {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "stop",
        "seed",
        "response_format",
        "timeout",
        "metadata",
    }
    assert captured["metadata"] == {
        "civicloop_run_id": "run-123",
        "civicloop_run_token_ceiling": 128,
    }


def test_budget_assertion_rejects_tampering_replay_and_restart(tmp_path: Path) -> None:
    ledger_path = tmp_path / "durable-ledger.sqlite3"
    assertion = _assertion(nonce="durable-nonce", ceiling=64)
    prepared = prepare_request(
        _body(max_tokens=32),
        budget_assertion=assertion,
        assertion_key=ASSERTION_KEY,
        policy=GatewayPolicy("civicloop-default", 2000, 60),
        ledger=DurableBudgetLedger(ledger_path),
        now=NOW,
    )
    assert prepared["max_tokens"] == 32

    payload, signature = assertion.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=="))
    decoded["token_ceiling"] = 64000
    tampered_payload = base64.urlsafe_b64encode(
        json.dumps(decoded, sort_keys=True, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    with pytest.raises(PolicyError, match="signature"):
        prepare_request(
            _body(max_tokens=32),
            budget_assertion=f"{tampered_payload}.{signature}",
            assertion_key=ASSERTION_KEY,
            policy=GatewayPolicy("civicloop-default", 2000, 60),
            ledger=DurableBudgetLedger(ledger_path),
            now=NOW,
        )

    with pytest.raises(PolicyError, match="replayed"):
        prepare_request(
            _body(max_tokens=32),
            budget_assertion=assertion,
            assertion_key=ASSERTION_KEY,
            policy=GatewayPolicy("civicloop-default", 2000, 60),
            ledger=DurableBudgetLedger(ledger_path),
            now=NOW,
        )

    with pytest.raises(PolicyError, match="budget exhausted"):
        prepare_request(
            _body(max_tokens=40),
            budget_assertion=_assertion(nonce="second-nonce", ceiling=64),
            assertion_key=ASSERTION_KEY,
            policy=GatewayPolicy("civicloop-default", 2000, 60),
            ledger=DurableBudgetLedger(ledger_path),
            now=NOW,
        )


def test_expired_run_id_stays_retired_across_process_restart(tmp_path: Path) -> None:
    ledger_path = tmp_path / "durable-ledger.sqlite3"
    policy = GatewayPolicy("civicloop-default", 2000, 60)
    prepare_request(
        _body(max_tokens=32),
        budget_assertion=_assertion(nonce="expiring-run", ceiling=64),
        assertion_key=ASSERTION_KEY,
        policy=policy,
        ledger=DurableBudgetLedger(ledger_path),
        now=NOW,
    )
    later = NOW + timedelta(minutes=3)
    replacement = issue_budget_assertion(
        key=ASSERTION_KEY,
        run_id="run-123",
        model_alias="civicloop-default",
        token_ceiling=64000,
        expires_at=later + timedelta(minutes=2),
        nonce="same-run-after-expiry-0001",
    )
    with pytest.raises(PolicyError, match="retired"):
        prepare_request(
            _body(max_tokens=32),
            budget_assertion=replacement,
            assertion_key=ASSERTION_KEY,
            policy=policy,
            ledger=DurableBudgetLedger(ledger_path),
            now=later,
        )


def test_supervisor_fails_when_child_exits_before_readiness() -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('bounded startup diagnostic must-not-appear'); sys.exit(7)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    supervisor = LiteLLMSupervisor(child, secrets=("must-not-appear",))
    with pytest.raises(RuntimeError, match="exited before readiness"):
        supervisor.wait_until_ready(
            readiness_probe=lambda: False,
            timeout_seconds=2,
            poll_seconds=0.01,
        )
    diagnostics = supervisor.diagnostics()
    assert "bounded startup diagnostic" in diagnostics
    assert len(diagnostics) <= 16_384
    assert "must-not-appear" not in diagnostics
    assert "[REDACTED]" in diagnostics

def test_assertion_is_bound_to_alias_expiry_and_nonce(tmp_path: Path) -> None:
    policy = GatewayPolicy("civicloop-default", 2000, 60)
    cases = [
        issue_budget_assertion(
            key=ASSERTION_KEY,
            run_id="run-123",
            model_alias="other-alias",
            token_ceiling=64,
            expires_at=NOW + timedelta(minutes=1),
            nonce="alias-nonce-0000000000000000",
        ),
        issue_budget_assertion(
            key=ASSERTION_KEY,
            run_id="run-123",
            model_alias="civicloop-default",
            token_ceiling=64,
            expires_at=NOW - timedelta(seconds=1),
            nonce="expired-nonce-0000000000000000",
        ),
    ]
    for index, assertion in enumerate(cases):
        with pytest.raises(PolicyError):
            prepare_request(
                _body(),
                budget_assertion=assertion,
                assertion_key=ASSERTION_KEY,
                policy=policy,
                ledger=DurableBudgetLedger(tmp_path / f"ledger-{index}.sqlite3"),
                now=NOW,
            )


def test_http_bypass_is_rejected_before_downstream_capture(
    gateway_port: int,
    fake_provider: tuple[int, Path, Path, subprocess.Popen[str]],
) -> None:
    _, capture_file, _, _ = fake_provider
    status, response = _post(
        gateway_port,
        _body(api_base="http://attacker.invalid"),
        assertion=_assertion(nonce="http-bypass"),
    )
    assert status == 400
    assert response["error"]["code"] == "invalid_model_request"
    assert not capture_file.exists()

    status, response = _post(
        gateway_port,
        _body(),
        assertion=_assertion(nonce="management-route"),
        path="/v1/models",
    )
    assert status == 404
    assert response == {"error": {"code": "route_not_found"}}


def test_provider_errors_are_neutral_and_redacted() -> None:
    mapped = provider_neutral_error(
        status=429,
        detail="OpenAI rejected provider-secret-must-not-escape",
    )
    assert mapped == {
        "error": {
            "code": "model_provider_unavailable",
            "message": "The model service is temporarily unavailable.",
            "retryable": True,
        }
    }
    assert "OpenAI" not in json.dumps(mapped)
    assert "provider-secret" not in json.dumps(mapped)

    rejected = provider_neutral_error(status=400)
    assert rejected["error"]["code"] == "model_request_rejected"
    assert rejected["error"]["retryable"] is False


def test_litellm_request_timeout_is_a_retryable_provider_failure(
    gateway_port: int,
    fake_provider: tuple[int, Path, Path, subprocess.Popen[str]],
) -> None:
    _, _, mode_file, _ = fake_provider
    mode_file.write_text("request-timeout", encoding="utf-8")

    status, response = _post(
        gateway_port,
        _body(),
        assertion=_assertion(nonce="litellm-request-timeout"),
    )

    assert status == 503
    assert response == {
        "error": {
            "code": "model_provider_unavailable",
            "message": "The model service is temporarily unavailable.",
            "retryable": True,
        }
    }


def test_compose_uses_root_handoff_durable_ledger_and_physical_startup_gate() -> None:
    compose = yaml.safe_load(COMPOSE.read_text())
    service = compose["services"]["litellm"]
    initializer = compose["services"]["model-gateway-init"]
    expected_image = (
        "ghcr.io/berriai/litellm-non_root:v1.100.1@"
        "sha256:c36f3b27a5a817329e0fcf9c4e3a7bf58b62a5a736ad5352f0a5771c1be58404"
    )
    assert initializer["image"] == expected_image
    assert service["image"] == expected_image
    assert "LITELLM_IMAGE" not in COMPOSE.read_text()
    assert service["depends_on"]["model-gateway-init"]["condition"] == (
        "service_completed_successfully"
    )
    assert service["networks"] == ["hermes-runtime", "provider-egress"]
    assert compose["networks"]["hermes-runtime"]["internal"] is True
    assert "ports" not in service
    assert service["user"] == "65534:65534"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["cpus"] == "0.50"
    assert service["mem_limit"] == "1g"
    assert service["environment"]["LITELLM_STARTUP_TIMEOUT_SECONDS"] == "180"
    mounts = {item["target"]: item for item in service["volumes"]}
    assert mounts["/run/model-gateway"]["read_only"] is True
    assert mounts["/var/lib/civicloop-model-gateway"]["read_only"] is False
    assert "secrets" not in service
    assert service["environment"]["MODEL_GATEWAY_STARTUP_RECEIPT"].endswith(
        "/current/receipt.json"
    )


def test_checked_in_litellm_config_is_provider_neutral_and_locked_down() -> None:
    config = yaml.safe_load(CONFIG.read_text())
    assert [item["model_name"] for item in config["model_list"]] == [
        "civicloop-default"
    ]
    params = config["model_list"][0]["litellm_params"]
    assert params["model"] == "os.environ/LITELLM_UPSTREAM_MODEL"
    assert params["api_base"] == "os.environ/LITELLM_UPSTREAM_BASE_URL"
    assert params["api_key"] == "os.environ/LITELLM_UPSTREAM_API_KEY"
    assert config["litellm_settings"]["turn_off_message_logging"] is True
    assert config["general_settings"]["store_model_in_db"] is False
    assert config["general_settings"]["store_prompts_in_spend_logs"] is False


def test_exact_pinned_litellm_runtime_contract() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/fakes/run_pinned_litellm_contract.py"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=480,
    )
    assert result.returncode == 0, result.stdout + result.stderr
