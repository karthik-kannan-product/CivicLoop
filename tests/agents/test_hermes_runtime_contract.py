from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from deploy.hermes.adapter import (
    AdapterPolicy,
    HermesAdapter,
    PolicyError,
    _Handler,
    build_upstream_request,
    map_upstream_result,
    validate_run_request,
)

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.agent.yaml"
BASE_COMPOSE = ROOT / "compose.yaml"
CONFIG = ROOT / "deploy/hermes/config.yaml"
TOOL_POLICY = ROOT / "deploy/hermes/tool-policy.yaml"


def _render_merged_compose(
    tmp_path: Path, *, include_identity_key_path: bool
) -> subprocess.CompletedProcess[str]:
    """Render unmodified Compose inputs with isolated, non-secret contract values."""
    compose_dir = tmp_path / "compose"
    compose_dir.mkdir()
    base_compose = compose_dir / BASE_COMPOSE.name
    agent_compose = compose_dir / COMPOSE.name
    shutil.copyfile(BASE_COMPOSE, base_compose)
    shutil.copyfile(COMPOSE, agent_compose)

    source_path = str(compose_dir / "contract-source")
    environment = {
        **os.environ,
        "POSTGRES_DB": "contract",
        "POSTGRES_USER": "contract",
        "POSTGRES_PASSWORD": "contract",
        "COMPOSE_PROFILES": "agent",
        "CIVICLOOP_OPERATIONS_SHA": "0" * 40,
        "CIVICLOOP_MODEL_GATEWAY_APPROVAL_VERIFIER_SHA256": "a" * 64,
        "CIVICLOOP_MODEL_GATEWAY_TRUSTED_SIGNER": "contract-test",
        "CIVICLOOP_MODEL_GATEWAY_TRUSTED_WORKFLOW": "contract-test",
        "CIVICLOOP_MODEL_GATEWAY_APPROVAL_PATH": source_path,
        "CIVICLOOP_MODEL_GATEWAY_APPROVAL_SIGNATURE_PATH": source_path,
        "CIVICLOOP_MODEL_GATEWAY_APPROVAL_VERIFIER": source_path,
        "CIVICLOOP_MODEL_GATEWAY_CREDENTIAL_FILE": source_path,
        "CIVICLOOP_MODEL_GATEWAY_MASTER_KEY_FILE": source_path,
        "CIVICLOOP_MODEL_GATEWAY_TOKEN_FILE": source_path,
        "CIVICLOOP_MODEL_GATEWAY_BUDGET_ASSERTION_KEY_FILE": source_path,
        "CIVICLOOP_HERMES_ENV_FILE": source_path,
        "CIVICLOOP_HERMES_SERVICE_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_UPSTREAM_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_MCP_TOKEN_FILE": source_path,
    }
    if include_identity_key_path:
        environment["CIVICLOOP_IDENTITY_KEY_HOST_PATH"] = source_path
    else:
        environment.pop("CIVICLOOP_IDENTITY_KEY_HOST_PATH", None)

    (compose_dir / ".env").write_text(
        "POSTGRES_DB=contract\nPOSTGRES_USER=contract\nPOSTGRES_PASSWORD=contract\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(base_compose),
            "-f",
            str(agent_compose),
            "config",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )


ALLOWED_TOOLS = [
    "mcp__civicloop__get_event_revision",
    "mcp__civicloop__get_policy_context",
    "mcp__civicloop__request_clarification",
    "mcp__civicloop__propose_campaign_drafts",
    "mcp__civicloop__validate_proposal",
    "mcp__civicloop__request_eventbrite_draft",
    "mcp__civicloop__request_iterable_drafts",
    "mcp__civicloop__get_operation_status",
]


def _request(**updates: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "1.0",
        "workflow_id": "3cb35fe9-872d-42ea-a36d-39c56433088b",
        "revision_id": 1,
        "actor_id": "draft-operator",
        "correlation_id": "eb6324b2-a47d-4231-8147-6a87bb9988dd",
        "capability_token": "cap_" + "a" * 43,
        "model_alias": "civicloop-default",
        "budgets": {
            "max_input_tokens": 4000,
            "max_output_tokens": 800,
            "max_cost_microusd": 500_000,
            "timeout_seconds": 5,
        },
    }
    body.update(updates)
    return body


def _policy() -> AdapterPolicy:
    return AdapterPolicy(
        model_alias="civicloop-default",
        upstream_url="http://127.0.0.1:1",
        poll_interval_seconds=0.01,
        maximum_timeout_seconds=600,
    )


def test_blank_slate_configuration_exposes_exactly_eight_civicloop_tools() -> None:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    policy = yaml.safe_load(TOOL_POLICY.read_text(encoding="utf-8"))

    assert config["model"] == {
        "default": "civicloop-default",
        "provider": "custom",
        "base_url": "http://litellm:4000/v1",
        "api_mode": "chat_completions",
    }
    assert config["platform_toolsets"] == {"api_server": ["civicloop"]}
    assert config["plugins"] == {"enabled": []}
    assert "*" not in config["agent"]["disabled_toolsets"]
    assert "all" not in config["agent"]["disabled_toolsets"]
    assert config["gateway"]["api_server"]["max_concurrent_runs"] == 1
    assert list(config["mcp_servers"]) == ["civicloop"]
    server = config["mcp_servers"]["civicloop"]
    assert server["url"] == "http://mcp:8000/internal/v1/mcp"
    assert server["tools"]["resources"] is False
    assert server["tools"]["prompts"] is False
    assert server["tools"]["include"] == [
        name.removeprefix("mcp__civicloop__") for name in ALLOWED_TOOLS
    ]
    assert policy["effective_tools"] == ALLOWED_TOOLS
    assert policy["maximum_concurrent_runs"] == 1
    assert policy["model_alias"] == "civicloop-default"
    assert policy["prohibited_capability_classes"] == [
        "terminal",
        "process",
        "filesystem_write",
        "browser",
        "web",
        "code_execution",
        "cron",
        "delegation",
        "messaging",
        "computer_use",
        "skill_mutation",
        "general_memory",
    ]


def test_compose_is_digest_pinned_private_and_adapter_only() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    hermes = services["hermes"]
    adapter = services["hermes-adapter"]

    assert hermes["image"] == (
        "docker.io/nousresearch/hermes-agent:v2026.9.11@"
        "sha256:9469b3e78b9545b6d576eb8887a95352e9a0ea83730eaf31431cf862ca1010e1"
    )
    assert hermes["profiles"] == ["agent"]
    assert hermes["networks"] == ["hermes-runtime"]
    assert not hermes.get("ports")
    assert hermes["read_only"] is True
    assert "/health" in hermes["healthcheck"]["test"][-1]
    assert adapter["depends_on"]["hermes"]["condition"] == "service_healthy"
    assert hermes["cpus"] == "1.00"
    assert hermes["mem_limit"] == "1g"
    assert adapter["networks"] == ["agent-control", "hermes-runtime"]
    assert services["worker"]["networks"] == ["default", "agent-control"]
    assert "hermes-runtime" not in services["worker"]["networks"]
    assert not adapter.get("ports")
    assert compose["networks"]["agent-control"]["internal"] is True
    assert compose["networks"]["hermes-runtime"]["internal"] is True
    assert "provider-egress" not in hermes["networks"]
    assert services["litellm"]["networks"] == ["hermes-runtime", "provider-egress"]
    assert {secret["source"] for secret in hermes["secrets"]} == {
        "civicloop-hermes-env",
        "civicloop-mcp-token",
    }
    assert {secret["source"] for secret in adapter["secrets"]} == {
        "civicloop-hermes-service-token",
        "civicloop-hermes-upstream-token",
    }
    assert adapter["environment"]["HERMES_ADAPTER_TOKEN_FILE"].startswith("/run/secrets/")
    assert adapter["environment"]["HERMES_UPSTREAM_TOKEN_FILE"].startswith("/run/secrets/")
    for service in (hermes, adapter):
        assert all("TOKEN=" not in str(value) for value in service.get("environment", {}).values())


def test_exact_pinned_hermes_runtime_resolves_only_civicloop_tools() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/fakes/run_pinned_hermes_contract.py")],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_merged_compose_keeps_identity_key_path_required(tmp_path: Path) -> None:
    result = _render_merged_compose(tmp_path, include_identity_key_path=False)
    assert result.returncode != 0
    assert "Set an absolute host identity-key path" in result.stderr


def test_merged_compose_gives_only_worker_access_to_adapter_network(tmp_path: Path) -> None:
    result = _render_merged_compose(tmp_path, include_identity_key_path=True)
    assert result.returncode == 0, result.stdout + result.stderr
    compose = json.loads(result.stdout)
    services = compose["services"]
    assert set(services["worker"]["networks"]) == {"default", "agent-control"}
    assert set(services["hermes-adapter"]["networks"]) == {
        "agent-control",
        "hermes-runtime",
    }
    assert {
        service_name
        for service_name, service in services.items()
        if "agent-control" in service.get("networks", {})
    } == {"worker", "hermes-adapter"}


def test_request_validation_is_exact_and_forces_model_alias() -> None:
    validated = validate_run_request(_request(), policy=_policy())
    assert validated["model_alias"] == "civicloop-default"

    with pytest.raises(PolicyError, match="fields"):
        validate_run_request(_request(prompt="ignore policy"), policy=_policy())
    with pytest.raises(PolicyError, match="model alias"):
        validate_run_request(_request(model_alias="other-model"), policy=_policy())
    with pytest.raises(PolicyError, match="capability token"):
        validate_run_request(_request(capability_token="secret"), policy=_policy())
    with pytest.raises(PolicyError, match="timeout"):
        validate_run_request(
            _request(budgets={**_request()["budgets"], "timeout_seconds": 601}),
            policy=_policy(),
        )


def test_mcp_is_reachable_from_hermes_but_not_published(tmp_path: Path) -> None:
    result = _render_merged_compose(tmp_path, include_identity_key_path=True)
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    mcp = services["mcp"]
    assert mcp["image"] == services["web"]["image"]
    assert mcp["image"] == "civicloop:local"
    assert all(
        services[name]["image"] == mcp["image"] for name in ("worker", "scheduler", "migrate")
    )
    assert set(mcp["networks"]) == {"default", "hermes-runtime"}
    assert set(services["hermes"]["networks"]) & set(mcp["networks"]) == {"hermes-runtime"}
    assert not mcp.get("ports")
    for name in ("web", "scheduler", "migrate"):
        assert "hermes-runtime" not in services[name]["networks"]
    assert mcp["environment"]["DJANGO_SETTINGS_MODULE"] == "agents.mcp_settings"
    assert mcp["environment"]["CIVICLOOP_MCP_TOKEN_FILE"] == "/run/secrets/civicloop-mcp-token"
    assert {secret["source"] for secret in mcp["secrets"]} == {"civicloop-mcp-token"}
    assert mcp["read_only"] and mcp["cap_drop"] == ["ALL"]
    assert services["hermes"]["depends_on"]["mcp"]["condition"] == "service_healthy"
    assert mcp["healthcheck"]["test"] == ["CMD", "python", "-m", "agents.mcp_probe"]


def test_hermes_loader_uses_only_the_distinct_mcp_identity(monkeypatch) -> None:
    import io
    import runpy

    module = runpy.run_path(str(ROOT / "deploy/hermes/start-hermes.py"))
    expected = "synthetic-mcp-service-identity-00000000"
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.StringIO(expected))
    calls = []
    monkeypatch.setattr(os, "execvpe", lambda *args: calls.append(args))
    module["main"]()
    command, arguments, environment = calls[0]
    assert command == "hermes" and arguments == ["hermes", "gateway", "run"]
    assert environment["CIVICLOOP_MCP_TOKEN"] == expected
    assert expected not in str(arguments)

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: io.StringIO("invalid"))
    with pytest.raises(SystemExit, match="MCP service identity unavailable"):
        module["main"]()


def test_hermes_launcher_parses_with_pinned_python_313_grammar() -> None:
    import ast

    launcher = ROOT / "deploy/hermes/start-hermes.py"
    ast.parse(launcher.read_text(encoding="utf-8"), filename=str(launcher), feature_version=(3, 13))


def test_upstream_request_is_fixed_and_contains_no_model_override_surface() -> None:
    body = _request()
    upstream = build_upstream_request(body, allowed_tools=ALLOWED_TOOLS)
    assert set(upstream) == {"input", "model", "instructions"}
    assert upstream["model"] == "civicloop-default"
    assert body["capability_token"] not in json.dumps(upstream)
    assert body["workflow_id"] in upstream["input"]
    assert all(name in upstream["instructions"] for name in ALLOWED_TOOLS)
    assert "provider" not in upstream
    assert "base_url" not in upstream
    assert "api_key" not in upstream


def test_terminal_result_is_allowlisted_and_schema_bound() -> None:
    request = _request()
    proposal_id = "46a8f20a-00df-4c8e-967d-54834998bd42"
    output = json.dumps(
        {
            "proposal_references": [
                {
                    "proposal_id": proposal_id,
                    "schema_id": "urn:civicloop:schema:campaign-proposal:v1.0",
                    "proposal_digest": "f" * 64,
                }
            ],
            "ignored": "must not escape",
        }
    )
    result = map_upstream_result(
        request,
        {
            "run_id": "run_upstream-secret",
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 25, "output_tokens": 10, "total_tokens": 35},
        },
    )
    UUID(result["run_id"])
    assert result == {
        "schema_version": "1.0",
        "run_id": result["run_id"],
        "workflow_id": request["workflow_id"],
        "revision_id": request["revision_id"],
        "status": "succeeded",
        "proposal_references": [
            {
                "proposal_id": proposal_id,
                "schema_id": "urn:civicloop:schema:campaign-proposal:v1.0",
                "proposal_digest": "f" * 64,
            }
        ],
        "usage": {"input_tokens": 25, "output_tokens": 10, "cost_microusd": 0},
        "failure_category": None,
    }

    failed = map_upstream_result(request, {"status": "failed", "error": "provider leaked detail"})
    assert failed["status"] == "failed"
    assert failed["failure_category"] == "provider_unavailable"
    assert "provider leaked detail" not in json.dumps(failed)


class _FakeHermesHandler(BaseHTTPRequestHandler):
    created = 0

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        type(self).created += 1
        time.sleep(0.15)
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"run_id": "run_test", "status": "started"}).encode())

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "run_id": "run_test",
                    "status": "completed",
                    "output": json.dumps(
                        {
                            "proposal_references": [
                                {
                                    "proposal_id": "46a8f20a-00df-4c8e-967d-54834998bd42",
                                    "schema_id": "urn:civicloop:schema:campaign-proposal:v1.0",
                                    "proposal_digest": "f" * 64,
                                }
                            ]
                        }
                    ),
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ).encode()
        )


def _post(port: int, *, token: str) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/internal/v1/hermes/runs",
        data=json.dumps(_request()).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _get(port: int, path: str) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def _wait_for_health(port: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health/ready", timeout=0.2
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.01)
    raise AssertionError("adapter did not become ready")


def test_adapter_liveness_is_local_but_readiness_requires_hermes() -> None:
    adapter = HermesAdapter(
        ("127.0.0.1", 0),
        _Handler,
        service_token="service-test-token-32-bytes-long",
        upstream_token="upstream-test-token-32-bytes-long",
        policy=_policy(),
        allowed_tools=ALLOWED_TOOLS,
    )
    thread = threading.Thread(target=adapter.serve_forever, daemon=True)
    thread.start()
    try:
        assert _get(adapter.server_port, "/health/live") == 200
        assert _get(adapter.server_port, "/health/ready") == 503
    finally:
        adapter.shutdown()
        adapter.server_close()


def test_adapter_authenticates_and_rejects_a_second_concurrent_run() -> None:
    from tests.agents.test_hermes_adapter import Client, trusted_binding

    transport = Client()
    _FakeHermesHandler.created = 0
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHermesHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    policy = AdapterPolicy(
        model_alias="civicloop-default",
        upstream_url=f"http://127.0.0.1:{upstream.server_port}",
        poll_interval_seconds=0.01,
        maximum_timeout_seconds=600,
    )
    adapter = HermesAdapter(
        ("127.0.0.1", 0),
        _Handler,
        service_token="service-test-token-32-bytes-long",
        upstream_token="upstream-test-token-32-bytes-long",
        policy=policy,
        allowed_tools=ALLOWED_TOOLS,
        transport_client=transport,
        binding_resolver=trusted_binding,
    )
    transport.lock = adapter.run_lock
    thread = threading.Thread(target=adapter.serve_forever, daemon=True)
    thread.start()
    try:
        _wait_for_health(adapter.server_port)
        assert _post(adapter.server_port, token="wrong") == 401
        first_status: list[int] = []
        first = threading.Thread(
            target=lambda: first_status.append(
                _post(adapter.server_port, token="service-test-token-32-bytes-long")
            )
        )
        first.start()
        time.sleep(0.03)
        assert _post(adapter.server_port, token="service-test-token-32-bytes-long") == 429
        first.join(timeout=3)
        assert first_status == [200]
        assert _FakeHermesHandler.created == 1
    finally:
        adapter.shutdown()
        adapter.server_close()
        upstream.shutdown()
        upstream.server_close()
