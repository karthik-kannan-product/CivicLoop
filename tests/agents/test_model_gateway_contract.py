from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from deploy.litellm.gateway import (
    BudgetLedger,
    GatewayPolicy,
    PolicyError,
    _Handler,
    prepare_request,
    provider_neutral_error,
)

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.agent.yaml"
CONFIG = ROOT / "deploy/litellm/config.yaml"
FAKE_SERVER = ROOT / "tests/fakes/openai_compatible_server.py"


def _post(
    port: int,
    body: dict[str, object],
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def fake_provider_port(tmp_path: Path) -> int:
    port_file = tmp_path / "port"
    environment = os.environ.copy()
    environment.update({"FAKE_PROVIDER_MODE": "compatible", "PORT_FILE": str(port_file)})
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
                yield int(port_file.read_text())
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
def gateway_port(fake_provider_port: int) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.policy = GatewayPolicy(  # type: ignore[attr-defined]
        alias="civicloop-default", max_tokens=2000, timeout_seconds=1
    )
    server.ledger = BudgetLedger()  # type: ignore[attr-defined]
    server.inference_slot = threading.BoundedSemaphore(1)  # type: ignore[attr-defined]
    server.client_token = "gateway-test-token"  # type: ignore[attr-defined]
    server.litellm_master_key = "internal-test-key"  # type: ignore[attr-defined]
    server.upstream = f"http://127.0.0.1:{fake_provider_port}"  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_gateway_routes_one_alias_and_switches_provider_by_configuration_only(
    fake_provider_port: int,
    gateway_port: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {
        "model": "civicloop-default",
        "messages": [{"role": "user", "content": "Create an event outline."}],
        "max_tokens": 32,
    }
    status, compatible = _post(fake_provider_port, request)
    assert status == 200
    assert compatible["model"] == "configured-upstream"

    status, through_gateway = _post(
        gateway_port,
        request,
        {
            "Authorization": "Bearer gateway-test-token",
            "X-CivicLoop-Run-Id": "run-compatible",
            "X-CivicLoop-Run-Token-Budget": "64",
        },
    )
    assert status == 200
    assert through_gateway["choices"] == compatible["choices"]

    monkeypatch.setenv("FAKE_PROVIDER_MODE", "recorded-openai")
    recorded = subprocess.run(
        [sys.executable, str(FAKE_SERVER), "--one-shot", json.dumps(request)],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    recorded_body = json.loads(recorded.stdout)
    assert (
        recorded_body["choices"][0]["message"]["content"]
        == (compatible["choices"][0]["message"]["content"])
    )

    config = yaml.safe_load(CONFIG.read_text())
    assert [item["model_name"] for item in config["model_list"]] == ["civicloop-default"]
    params = config["model_list"][0]["litellm_params"]
    assert params["model"] == "os.environ/LITELLM_UPSTREAM_MODEL"
    assert params["api_base"] == "os.environ/LITELLM_UPSTREAM_BASE_URL"
    assert params["api_key"] == "os.environ/LITELLM_UPSTREAM_API_KEY"


def test_request_policy_enforces_alias_token_ceiling_timeout_and_trusted_metadata() -> None:
    policy = GatewayPolicy(alias="civicloop-default", max_tokens=2000, timeout_seconds=60)
    ledger = BudgetLedger()
    body = {
        "model": "civicloop-default",
        "messages": [{"role": "user", "content": "Draft"}],
        "max_tokens": 100,
        "metadata": {"tags": ["attacker"], "run_id": "forged"},
        "timeout": 999,
    }
    prepared = prepare_request(
        body,
        headers={
            "x-civicloop-run-id": "run-123",
            "x-civicloop-run-token-budget": "250",
        },
        policy=policy,
        ledger=ledger,
    )
    assert prepared["model"] == "civicloop-default"
    assert prepared["max_tokens"] == 100
    assert prepared["timeout"] == 60
    assert prepared["metadata"] == {
        "civicloop_run_id": "run-123",
        "civicloop_run_token_budget": 250,
    }

    with pytest.raises(PolicyError, match="model alias"):
        prepare_request(
            {**body, "model": "direct-provider"},
            headers={},
            policy=policy,
            ledger=ledger,
        )
    with pytest.raises(PolicyError, match="token limit"):
        prepare_request({**body, "max_tokens": 2001}, headers={}, policy=policy, ledger=ledger)


def test_budget_is_server_enforced_and_fails_closed() -> None:
    policy = GatewayPolicy(alias="civicloop-default", max_tokens=2000, timeout_seconds=60)
    ledger = BudgetLedger()
    headers = {
        "x-civicloop-run-id": "run-budget",
        "x-civicloop-run-token-budget": "120",
    }
    request = {"model": "civicloop-default", "messages": [], "max_tokens": 80}
    prepare_request(request, headers=headers, policy=policy, ledger=ledger)
    with pytest.raises(PolicyError, match="budget exhausted"):
        prepare_request(request, headers=headers, policy=policy, ledger=ledger)
    with pytest.raises(PolicyError, match="required"):
        prepare_request(request, headers={}, policy=policy, ledger=BudgetLedger())


def test_provider_errors_are_neutral_and_do_not_echo_bodies_or_credentials() -> None:
    secret = "provider-secret-must-not-escape"
    mapped = provider_neutral_error(
        status=429,
        detail=f"OpenAI rejected key {secret}: raw provider body",
    )
    rendered = json.dumps(mapped)
    assert mapped == {
        "error": {
            "code": "model_provider_unavailable",
            "message": "The model service is temporarily unavailable.",
            "retryable": True,
        }
    }
    assert secret not in rendered
    assert "OpenAI" not in rendered
    assert "raw provider body" not in rendered


def test_compose_is_internal_hardened_and_credential_isolated() -> None:
    compose = yaml.safe_load(COMPOSE.read_text())
    service = compose["services"]["litellm"]
    assert service["profiles"] == ["agent"]
    assert service["networks"] == ["agent-control", "provider-egress"]
    assert compose["networks"]["agent-control"]["internal"] is True
    assert "ports" not in service
    assert service["user"] == "65534:65534"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert sorted(service["tmpfs"]) == sorted(
        [
            "/app/cache:rw,noexec,nosuid,nodev,uid=65534,gid=65534,mode=0700",
            "/app/migrations:rw,noexec,nosuid,nodev,uid=65534,gid=65534,mode=0700",
        ]
    )
    assert "/health/liveliness" in service["healthcheck"]["test"][-1]
    assert service["environment"]["DISABLE_ADMIN_UI"] == "True"
    assert service["environment"]["NO_DOCS"] == "True"
    assert service["environment"]["NO_REDOC"] == "True"
    assert service["environment"]["NO_OPENAPI"] == "True"

    serialized = COMPOSE.read_text() + CONFIG.read_text()
    assert "provider-secret-must-not-escape" not in serialized
    assert "OPENAI_API_KEY=" not in serialized
    secret_targets = {item["target"]: item for item in service["secrets"]}
    assert secret_targets["/run/secrets/litellm-provider-credential"]["mode"] == "0600"
    assert secret_targets["/run/secrets/litellm-master-key"]["mode"] == "0600"


def test_litellm_config_disables_body_storage_callbacks_cache_and_db_models() -> None:
    config = yaml.safe_load(CONFIG.read_text())
    settings = config["litellm_settings"]
    general = config["general_settings"]
    assert settings["turn_off_message_logging"] is True
    assert settings["redact_user_api_key_info"] is True
    assert settings["cache"] is False
    assert settings["callbacks"] == []
    assert general["store_prompts_in_spend_logs"] is False
    assert general["store_model_in_db"] is False
    assert general["reject_clientside_metadata_tags"] is True
    assert general["global_max_parallel_requests"] == 1
    assert config["router_settings"]["timeout"] == 60
