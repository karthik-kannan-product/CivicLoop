from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from deploy.hermes import adapter
from deploy.hermes.transport_contracts import scope_digest
from tests.agents.test_hermes_runtime_contract import _policy, _request
from tests.agents.test_hermes_transport import binding


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
