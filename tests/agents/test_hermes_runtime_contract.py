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
        "CIVICLOOP_MODEL_GATEWAY_TOKEN_FILE": str(compose_dir / "gateway-token-backing"),
        "CIVICLOOP_MODEL_GATEWAY_BUDGET_ASSERTION_KEY_FILE": str(
            compose_dir / "assertion-key-backing"
        ),
        "CIVICLOOP_HERMES_ENV_FILE": source_path,
        "CIVICLOOP_HERMES_SERVICE_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_UPSTREAM_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_MCP_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_CONTROLLER_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_SHIM_CLIENT_TOKEN_FILE": source_path,
        "CIVICLOOP_HERMES_TRANSPORT_CONTROL_TOKEN_FILE": source_path,
    }
    # Test the shipped disabled defaults regardless of workstation overrides.
    for flag in ("CIVICLOOP_HERMES_ENABLED", "CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED"):
        environment.pop(flag, None)
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
        "base_url": "http://127.0.0.1:1/v1",
        "api_mode": "chat_completions",
        "streaming": False,
    }
    assert config["platform_toolsets"] == {"api_server": ["civicloop"]}
    assert config["plugins"] == {"enabled": []}
    assert "*" not in config["agent"]["disabled_toolsets"]
    assert "all" not in config["agent"]["disabled_toolsets"]
    assert config["gateway"]["api_server"]["max_concurrent_runs"] == 1
    assert list(config["mcp_servers"]) == ["civicloop"]
    server = config["mcp_servers"]["civicloop"]
    assert server["url"] == "http://127.0.0.1:1/mcp"
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
    transport = services["hermes-transport"]
    assert hermes["image"] == "${CIVICLOOP_HERMES_SCOPED_IMAGE:-civicloop-hermes-scoped:local}"
    dockerfile = (ROOT / "deploy/hermes/Dockerfile.scoped").read_text()
    assert "@sha256:9469b3e78b9545b6d576eb8887a95352e9a0ea83730eaf31431cf862ca1010e1" in dockerfile
    assert hermes["build"]["dockerfile"] == "deploy/hermes/Dockerfile.scoped"
    assert hermes["init"] is True
    assert hermes["command"] == [
        "/opt/hermes/.venv/bin/python",
        "-m",
        "deploy.hermes.controller_service",
    ]
    assert hermes["profiles"] == ["agent"]
    assert hermes["networks"] == ["hermes-runtime"]
    assert adapter["networks"] == ["agent-control", "hermes-runtime"]
    assert set(transport["networks"]) == {"agent-control", "hermes-runtime", "hermes-data"}
    assert services["worker"]["networks"] == ["default", "agent-control"]
    assert services["litellm"]["networks"] == ["hermes-data", "provider-egress"]
    assert services["mcp"]["networks"] == ["default", "hermes-data"]
    assert adapter["depends_on"]["hermes"]["condition"] == "service_healthy"
    assert hermes["depends_on"]["hermes-transport"]["condition"] == "service_healthy"
    assert hermes["environment"]["HERMES_DASHBOARD"] == "false"
    assert all(mount["target"] != "/opt/data" for mount in hermes.get("volumes", []))
    assert any(mount.startswith("/opt/data:rw,noexec,nosuid,nodev,") for mount in hermes["tmpfs"])
    assert transport["environment"]["HERMES_TRANSPORT_MCP_URL"] == "http://mcp:8000/internal/v1/mcp"
    for name in ("agent-control", "hermes-runtime", "hermes-data"):
        assert compose["networks"][name]["internal"] is True
    for service in (hermes, adapter, transport, services["mcp"]):
        assert not service.get("ports")
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["tmpfs"] and service["pids_limit"] > 0
        assert service["cpus"] and service["mem_limit"]
        assert not service.get("secrets")
        assert all("TOKEN=" not in str(value) for value in service.get("environment", {}).values())
    for name in ("worker", "hermes-transport"):
        for flag in ("CIVICLOOP_HERMES_ENABLED", "CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED"):
            assert services[name]["environment"][flag] == "${" + flag + ":-false}"
    assert (
        services["worker"]["environment"]["CIVICLOOP_HERMES_PROFILE_ID"]
        == "${CIVICLOOP_HERMES_PROFILE_ID:-}"
    )
    assert (
        services["hermes-adapter"]["image"]
        == services["hermes-transport"]["image"]
        == services["mcp"]["image"]
    )


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
    } == {"worker", "hermes-adapter", "hermes-transport"}


def test_merged_compose_separates_control_data_and_disables_activation(tmp_path: Path) -> None:
    result = _render_merged_compose(tmp_path, include_identity_key_path=True)
    assert result.returncode == 0, result.stderr
    compose = json.loads(result.stdout)
    services = compose["services"]
    memberships = {
        network: {
            name for name, service in services.items() if network in service.get("networks", {})
        }
        for network in ("agent-control", "hermes-runtime", "hermes-data")
    }
    assert memberships == {
        "agent-control": {"worker", "hermes-adapter", "hermes-transport"},
        "hermes-runtime": {"hermes", "hermes-adapter", "hermes-transport"},
        "hermes-data": {"hermes-transport", "mcp", "litellm"},
    }
    for name in ("worker", "hermes-transport"):
        for flag in ("CIVICLOOP_HERMES_ENABLED", "CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED"):
            assert services[name]["environment"][flag] == "false"
    for name in ("web", "scheduler", "migrate"):
        assert not set(services[name].get("depends_on", {})) & {
            "hermes",
            "hermes-adapter",
            "hermes-transport",
            "mcp",
            "litellm",
        }
    for name in ("hermes", "hermes-adapter", "hermes-transport", "mcp"):
        service = services[name]
        assert not service.get("ports")
        assert "provider-egress" not in service["networks"]
        assert not any(
            any(provider in secret["source"].lower() for provider in ("eventbrite", "iterable"))
            for secret in service.get("secrets", [])
        )
    # The shim signs/asserts against the exact credentials installed into LiteLLM.
    init_mounts = {
        mount["target"]: mount["source"]
        for mount in services["model-gateway-init"]["volumes"]
        if mount["type"] == "bind"
    }
    assert (
        compose["secrets"]["civicloop-hermes-gateway-token"]["file"]
        == (init_mounts["/source/gateway-token"])
    )
    assert (
        compose["secrets"]["civicloop-hermes-budget-assertion-key"]["file"]
        == (init_mounts["/source/budget-assertion-key"])
    )
    assert init_mounts["/source/gateway-token"] != init_mounts["/source/budget-assertion-key"]
    assert services["hermes"]["init"] is True
    assert all(mount["target"] != "/opt/data" for mount in services["hermes"].get("volumes", []))


def test_compose_stages_owner_readable_identity_volumes_without_profile_dependency() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    init = services["hermes-identities-init"]
    assert init["network_mode"] == "none"
    assert init["user"] == "0:0"
    assert init["cap_drop"] == ["ALL"]
    assert init["cap_add"] == ["CHOWN"]
    assert init["read_only"] is True
    assert init["security_opt"] == ["no-new-privileges:true"]
    assert init["profiles"] == ["agent"]
    assert init["restart"] == "no"
    assert init["entrypoint"] == ["python", "-m", "deploy.hermes.identity_init"]
    expected = {
        "worker": "worker",
        "hermes-adapter": "adapter",
        "hermes": "controller",
        "hermes-transport": "transport",
        "mcp": "mcp",
    }
    for name, consumer in expected.items():
        service = services[name]
        assert not service.get("secrets")
        identity_mount = next(
            mount for mount in service["volumes"] if mount["target"] == "/run/secrets"
        )
        assert identity_mount == {
            "type": "volume",
            "source": f"hermes-{consumer}-identities",
            "target": "/run/secrets",
            "read_only": True,
        }
        staged_mount = next(
            mount for mount in init["volumes"] if mount["target"] == f"/handoff/{consumer}"
        )
        assert staged_mount["source"] == identity_mount["source"]
        assert staged_mount["read_only"] is False
        if name == "worker":
            assert "hermes-identities-init" not in service.get("depends_on", {})
        else:
            assert (
                service["depends_on"]["hermes-identities-init"]["condition"]
                == "service_completed_successfully"
            )
    assert {item["target"] for item in init["secrets"]} == {
        "/source/" + name
        for name in (
            "civicloop-hermes-service-token",
            "civicloop-hermes-upstream-token",
            "civicloop-hermes-controller-token",
            "civicloop-hermes-transport-control-token",
            "civicloop-hermes-shim-client-token",
            "civicloop-mcp-token",
            "civicloop-hermes-gateway-token",
            "civicloop-hermes-budget-assertion-key",
        )
    }


def test_compose_hermes_bootstrap_permissions_are_limited_to_vendor_uid_drop() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    hermes = services["hermes"]
    assert hermes["init"] is True
    assert hermes["cap_drop"] == ["ALL"]
    assert hermes["cap_add"] == ["SETUID", "SETGID", "DAC_OVERRIDE"]
    assert hermes["security_opt"] == ["no-new-privileges:true"]
    assert hermes["read_only"] is True
    assert "/run:rw,noexec,nosuid,nodev,uid=0,gid=0,mode=0755,size=8m" in hermes["tmpfs"]
    assert "/opt/data:rw,noexec,nosuid,nodev,uid=10000,gid=10000,mode=0700" in hermes["tmpfs"]
    assert hermes["environment"]["HERMES_UID"] == "10000"
    assert hermes["environment"]["HERMES_GID"] == "10000"
    assert hermes["command"] == [
        "/opt/hermes/.venv/bin/python",
        "-m",
        "deploy.hermes.controller_service",
    ]
    assert not hermes.get("entrypoint")  # Preserve the reviewed vendor UID drop.
    for name in ("mcp", "hermes-adapter", "hermes-transport", "worker"):
        assert not services[name].get("cap_add")
    assert services["hermes-identities-init"]["cap_add"] == ["CHOWN"]


def test_offline_bootstrap_probe_preserves_entrypoint_and_reports_only_safe_evidence() -> None:
    from tests.fakes.hermes_bootstrap_probe import probe_command

    command = probe_command("sha256:" + "a" * 64, "civicloop-test-bootstrap")
    assert "--entrypoint" not in command
    assert command[command.index("--network") + 1] == "none"
    assert "--init" in command and "--read-only" in command
    assert command.count("--cap-add") == 3
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
    hermes = yaml.safe_load(COMPOSE.read_text())["services"]["hermes"]
    assert [command[i + 1] for i, value in enumerate(command) if value == "--tmpfs"] == hermes[
        "tmpfs"
    ]
    assert "API_SERVER_KEY=synthetic-offline-bootstrap-probe-only" in command
    assert "--volume" not in command and "--mount" not in command
    with pytest.raises(ValueError, match="exact image"):
        probe_command("hermes:latest", "civicloop-test-bootstrap")


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


def test_compose_tmpfs_mounts_are_absolute_and_mcp_options_stay_together() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for mount in service.get("tmpfs", []):
            assert mount.split(":", 1)[0].startswith("/"), (name, mount)
    assert compose["services"]["mcp"]["tmpfs"] == [
        "/tmp:rw,noexec,nosuid,nodev,uid=10001,gid=10001,mode=0700"
    ]


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
    assert set(mcp["networks"]) == {"default", "hermes-data"}
    assert not set(services["hermes"]["networks"]) & set(mcp["networks"])
    assert set(services["hermes-transport"]["networks"]) & set(mcp["networks"]) == {"hermes-data"}
    assert not mcp.get("ports")
    for name in ("web", "scheduler", "migrate"):
        assert "hermes-runtime" not in services[name]["networks"]
    assert mcp["environment"]["DJANGO_SETTINGS_MODULE"] == "agents.mcp_settings"
    assert mcp["environment"]["CIVICLOOP_MCP_TOKEN_FILE"] == "/run/secrets/civicloop-mcp-token"
    assert not mcp.get("secrets")
    assert any(mount["source"] == "hermes-mcp-identities" for mount in mcp["volumes"])
    assert mcp["read_only"] and mcp["cap_drop"] == ["ALL"]
    assert services["hermes-transport"]["depends_on"]["mcp"]["condition"] == "service_healthy"
    assert mcp["healthcheck"]["test"] == ["CMD", "python", "-m", "agents.mcp_probe"]


def test_hermes_loader_starts_controller_with_distinct_identities(monkeypatch) -> None:
    import runpy
    from types import SimpleNamespace

    from deploy.hermes import controller_service

    module = runpy.run_path(str(ROOT / "deploy/hermes/start-hermes.py"))
    calls = []
    monkeypatch.setenv("HERMES_CONTROLLER_TOKEN_FILE", "/run/secrets/controller")
    monkeypatch.setenv("HERMES_SHIM_CLIENT_TOKEN_FILE", "/run/secrets/shim-client")
    monkeypatch.setenv("HERMES_SHIM_URL", "http://hermes-transport:8080")
    monkeypatch.setattr(controller_service, "_read_token", lambda path, **kw: path)
    monkeypatch.setattr(controller_service, "ProcessController", lambda **kw: calls.append(kw))
    server = SimpleNamespace(active=None, serve_forever=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(controller_service, "ControllerService", lambda *a, **kw: server)
    module["main"]()
    assert calls == [
        {"shim_base_url": "http://hermes-transport:8080", "shim_token": "/run/secrets/shim-client"}
    ]


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
    from deploy.hermes.transport import binding_payload
    from tests.agents.test_hermes_adapter import trusted_binding

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/internal/v1/hermes/runs",
        data=json.dumps(_request()).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-CivicLoop-Run-Binding": json.dumps(binding_payload(trusted_binding(_request()))),
        },
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
