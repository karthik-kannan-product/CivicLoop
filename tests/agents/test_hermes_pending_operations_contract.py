"""Cheap fixture checks and the separately opted-in exact application gate."""

import json
import os
import threading
import urllib.request
import uuid

import pytest

from tests.fakes.hermes_contract_model import STEPS, FixtureFailure, ModelServer, completion
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
        assert state == {"call_count": 1, "failure_count": 0, "blocked": True}
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
    config = standalone_config(candidate, 8765)
    services = config["services"]
    assert services["worker"]["command"] == ["worker"]
    assert services["litellm"]["entrypoint"] == ["python", "/app/gateway.py"]
    assert services["litellm"]["command"] == []
    assert services["hermes"]["command"][-1] == "deploy.hermes.controller_service"
    assert services["hermes"]["init"] is True
    assert set(services["hermes"]["networks"]) == {"hermes-runtime"}
    assert all(network["internal"] for network in config["networks"].values())
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
