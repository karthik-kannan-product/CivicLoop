from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from deploy.hermes import adapter
from deploy.hermes.transport_contracts import scope_digest
from tests.agents.test_hermes_runtime_contract import _policy, _request
from tests.agents.test_hermes_transport import binding


def completed_usage(usage):
    return {
        "status": "completed",
        "output": json.dumps(
            {
                "proposal_references": [
                    {
                        "proposal_id": "12345678-1234-5678-1234-567812345678",
                        "proposal_digest": "a" * 64,
                        "schema_id": "urn:civicloop:schema:campaign-proposal:v1.0",
                    }
                ]
            }
        ),
        "usage": usage,
    }


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 1},
        {"output_tokens": 1},
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": 1_000_001, "output_tokens": 1},
        {"input_tokens": 1, "output_tokens": 100_001},
        {"input_tokens": 1, "output_tokens": 1, "cost_microusd": None},
    ],
)
def test_completed_malformed_usage_fails_closed(usage):
    result = adapter.map_upstream_result(_request(), completed_usage(usage))
    assert result["status"] == "failed"
    assert result["failure_category"] == "invalid_output"
    assert result["proposal_references"] == []


@pytest.mark.parametrize("cost", [None, 17])
def test_completed_usage_preserves_absent_or_supplied_cost(cost):
    usage = {"input_tokens": 700, "output_tokens": 700, "total_tokens": 1400}
    if cost is not None:
        usage["cost_microusd"] = cost
    result = adapter.map_upstream_result(_request(), completed_usage(usage))
    assert result["status"] == "succeeded"
    assert result["usage"] == {key: value for key, value in usage.items() if key != "total_tokens"}


def trusted_binding(body):
    return binding(
        run_id=adapter.map_upstream_result(body, {})["run_id"],
        workflow_id=adapter.uuid.UUID(body["workflow_id"]),
        revision_id=body["revision_id"],
        actor_id=body["actor_id"],
        capability=body["capability_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=body["budgets"]["timeout_seconds"]),
        max_input_tokens=body["budgets"]["max_input_tokens"],
        max_output_tokens=body["budgets"]["max_output_tokens"],
        max_cost_microusd=body["budgets"]["max_cost_microusd"],
    )


class Client:
    def __init__(self):
        self.events = []
        self.lock = None

    def register_scope(self, *, token, binding):
        assert self.lock.locked()
        self.events.append(("register", token, binding))

    def revoke_scope(self, *, token):
        assert self.lock.locked()
        self.events.append(("revoke", token))


def make_adapter(client, resolver=trusted_binding):
    server = adapter.HermesAdapter(
        ("127.0.0.1", 0),
        adapter._Handler,
        service_token="synthetic-service-token-000000",
        upstream_token="synthetic-upstream-token-000000",
        policy=_policy(),
        allowed_tools=adapter.ALLOWED_TOOLS,
        transport_client=client,
        binding_resolver=resolver,
    )
    if client:
        client.lock = server.run_lock
    return server


@pytest.mark.parametrize("failure", [None, "admission", "polling", "registration"])
def test_scope_registers_before_admission_and_revokes_before_lock_release(monkeypatch, failure):
    client = Client()
    server = make_adapter(client)
    calls = []

    def request(url, **kwargs):
        assert client.events[0][0] == "register"
        calls.append(kwargs)
        if (failure == "admission" and len(calls) == 1) or failure == "polling":
            raise adapter.UpstreamError("unavailable")
        return {"run_id": "run_test"} if len(calls) == 1 else {"status": "failed"}

    if failure == "registration":

        def reject(**kwargs):
            client.events.append(("register", kwargs["token"], kwargs["binding"]))
            raise adapter.TransportError("Transport dependency unavailable", status=502)

        client.register_scope = reject
    monkeypatch.setattr(adapter, "_json_request", request)
    try:
        with server.run_lock:
            if failure:
                with pytest.raises(adapter.UpstreamError):
                    server.execute(_request())
            else:
                server.execute(_request())
        token = client.events[0][1]
        assert token.startswith("scope_") and len(token) == 49
        assert client.events[-1] == ("revoke", token)
        if calls:
            assert calls[0]["transport_scope"] == token
            assert token not in json.dumps(calls[0]["body"])
            assert _request()["capability_token"] not in json.dumps(calls[0]["body"])
        assert scope_digest(token) != token
    finally:
        server.server_close()


@pytest.mark.parametrize(
    "resolver", [None, lambda body: replace(trusted_binding(body), actor_id="other")]
)
def test_missing_or_mismatched_trusted_binding_fails_before_admission(monkeypatch, resolver):
    client = Client()
    server = make_adapter(client, resolver)
    calls = []
    monkeypatch.setattr(adapter, "_json_request", lambda *a, **k: calls.append(k))
    try:
        with server.run_lock, pytest.raises(adapter.UpstreamError):
            server.execute(_request())
        assert calls == []
        assert client.events == []
    finally:
        server.server_close()


def test_revocation_failure_quarantines_future_admission(monkeypatch):
    client = Client()
    server = make_adapter(client)

    def revoke(**kwargs):
        raise adapter.TransportError("Transport dependency unavailable", status=502)

    client.revoke_scope = revoke
    monkeypatch.setattr(
        adapter, "_json_request", lambda *a, **k: {"run_id": "run_test", "status": "failed"}
    )
    try:
        with server.run_lock, pytest.raises(adapter.UpstreamError):
            server.execute(_request())
        assert server.transport_healthy is False
        with server.run_lock, pytest.raises(adapter.UpstreamError):
            server.execute(_request())
        assert len(client.events) == 1
    finally:
        server.server_close()


def test_adapter_uses_one_run_controller_when_configured():
    class Controller:
        def __init__(self):
            self.calls = []

        def execute(self, body, *, scope_token, deadline):
            self.calls.append((body, scope_token, deadline))
            return adapter.map_upstream_result(body, {"status": "failed"})

    client = Client()
    controller = Controller()
    server = make_adapter(client)
    server.process_controller = controller
    try:
        with server.run_lock:
            result = server.execute(_request())
        assert result["status"] == "failed"
        assert len(controller.calls) == 1
        assert controller.calls[0][1] == client.events[0][1]
        assert controller.calls[0][2] > time.monotonic()
        assert client.events[-1] == ("revoke", controller.calls[0][1])
    finally:
        server.server_close()


def test_binding_expiry_deadline_reaches_controller():
    class Controller:
        quarantined = False
        deadline = None

        def execute(self, body, *, scope_token, deadline):
            self.deadline = deadline
            return adapter.map_upstream_result(body, {"status": "failed"})

    controller = Controller()
    client = Client()
    server = make_adapter(
        client,
        resolver=lambda body: replace(
            trusted_binding(body), expires_at=datetime.now(UTC) + timedelta(seconds=0.2)
        ),
    )
    server.process_controller = controller
    started = time.monotonic()
    try:
        with server.run_lock:
            server.execute(_request())
        assert started < controller.deadline <= started + 0.22
    finally:
        server.server_close()
