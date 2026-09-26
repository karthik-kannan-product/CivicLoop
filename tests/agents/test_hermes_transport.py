from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import UUID

import pytest

from deploy.hermes.transport import (
    CONTROL_PATH,
    INFERENCE_PATH,
    MAX_BODY_BYTES,
    REVOKE_PATH,
    SCOPE_HEADER,
    HTTPTransportClient,
    ScopeRegistry,
    TransportError,
    TransportPricing,
    TransportServer,
)
from deploy.hermes.transport_contracts import ScopeBinding, scope_digest
from deploy.litellm.assertions import issue_budget_assertion
from deploy.litellm.gateway import _verify_budget_assertion

NOW = datetime.now(UTC)
TOKEN = "scope_" + "a" * 43
TOKEN_B = "scope_" + "b" * 43
CAPABILITY = "cap_" + "c" * 43
CONTROL = "synthetic-control-token-000000"
CLIENT = "synthetic-client-token-0000000"
MCP = "synthetic-mcp-token-0000000000"
GATEWAY = "synthetic-gateway-token-000000"
KEY = b"synthetic-assertion-key-00000000000000000"


def binding(**updates):
    return replace(
        ScopeBinding(
            run_id="run-a",
            workflow_id=UUID("843a756b-b9a4-4fb7-89ee-05be3f38fc6d"),
            revision_id=1,
            revision_digest="d" * 64,
            actor_id="operator",
            capability=CAPABILITY,
            model_alias="civicloop-default",
            expires_at=NOW + timedelta(minutes=5),
            max_inferences=3,
            max_input_tokens=4000,
            max_output_tokens=100,
            max_cost_microusd=10000,
        ),
        **updates,
    )


def reserve(registry, **updates):
    values = dict(input_token_ceiling=10, output_token_ceiling=10, cost_ceiling_microusd=10)
    return registry.reserve_inference(token=TOKEN, **(values | updates))


def test_registry_exact_replay_conflicts_retirement_and_digest_only_storage():
    registry = ScopeRegistry(clock=lambda: NOW)
    registry.register(token=TOKEN, binding=binding())
    registry.register(token=TOKEN, binding=binding())
    assert registry.authorize(token=TOKEN) == binding()
    assert list(registry._scopes) == [scope_digest(TOKEN)]
    assert TOKEN not in repr(registry._scopes)
    assert CAPABILITY not in repr(registry._scopes)
    for token, value in [(TOKEN, binding(actor_id="other")), (TOKEN_B, binding())]:
        with pytest.raises(TransportError, match="^Transport authorization rejected$"):
            registry.register(token=token, binding=value)
    registry.revoke(token=TOKEN)
    registry.revoke(token=TOKEN)
    assert registry._scopes[scope_digest(TOKEN)].binding is None
    for token in (TOKEN, TOKEN_B):
        with pytest.raises(TransportError):
            registry.register(token=token, binding=binding())


def test_registry_expiry_restart_and_capacity_fail_closed():
    current = [NOW]
    registry = ScopeRegistry(clock=lambda: current[0], maximum_active=1, maximum_records=2)
    registry.register(token=TOKEN, binding=binding())
    with pytest.raises(TransportError):
        registry.register(token=TOKEN_B, binding=binding(run_id="run-b"))
    with pytest.raises(TransportError):
        ScopeRegistry().authorize(token=TOKEN)
    current[0] = NOW + timedelta(minutes=5)
    with pytest.raises(TransportError):
        registry.authorize(token=TOKEN)
    assert registry._scopes[scope_digest(TOKEN)].binding is None
    registry.register(
        token=TOKEN_B,
        binding=binding(run_id="run-b", expires_at=current[0] + timedelta(minutes=1)),
    )
    registry.revoke(token=TOKEN_B)
    with pytest.raises(TransportError):
        registry.register(
            token="scope_" + "z" * 43,
            binding=binding(run_id="run-c", expires_at=current[0] + timedelta(minutes=1)),
        )


def test_revoke_before_delayed_registration_tombstones_unknown_scope():
    registry = ScopeRegistry(clock=lambda: NOW)
    registry.revoke(token=TOKEN)
    with pytest.raises(TransportError):
        registry.register(token=TOKEN, binding=binding())


def test_lease_loss_retires_authority_permanently():
    lease = [True]
    registry = ScopeRegistry(clock=lambda: NOW, lease_active=lambda value: lease[0])
    registry.register(token=TOKEN, binding=binding())
    lease[0] = False
    with pytest.raises(TransportError):
        registry.authorize(token=TOKEN)
    lease[0] = True
    with pytest.raises(TransportError):
        registry.authorize(token=TOKEN)


def test_revoke_serializes_with_inflight_forward():
    registry = ScopeRegistry(clock=lambda: NOW)
    registry.register(token=TOKEN, binding=binding())
    entered = threading.Event()
    completed = threading.Event()

    def revoke():
        entered.set()
        registry.revoke(token=TOKEN)
        completed.set()

    with registry.forward(token=TOKEN):
        thread = threading.Thread(target=revoke)
        thread.start()
        assert entered.wait(1)
        assert not completed.is_set()
    thread.join(1)
    assert completed.is_set()
    with pytest.raises(TransportError), registry.forward(token=TOKEN):
        pytest.fail("retired scope forwarded")


@pytest.mark.parametrize(
    "limits, reservation",
    [
        ({"max_inferences": 1}, {}),
        ({"max_input_tokens": 15}, {}),
        ({"max_output_tokens": 15}, {}),
        ({"max_cost_microusd": 15}, {}),
    ],
)
def test_registry_cumulative_limits_are_reserved_atomically(limits, reservation):
    registry = ScopeRegistry(clock=lambda: NOW)
    registry.register(token=TOKEN, binding=binding(**limits))
    UUID(reserve(registry, **reservation))
    with pytest.raises(TransportError, match="^Transport budget exhausted$"):
        reserve(registry, **reservation)


def test_concurrent_reservations_cannot_overspend():
    registry = ScopeRegistry(clock=lambda: NOW)
    registry.register(token=TOKEN, binding=binding(max_inferences=1))

    def attempt(_):
        try:
            return reserve(registry)
        except TransportError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(value is not None for value in pool.map(attempt, range(30))) == 1


def test_invalid_reservation_does_not_consume_budget():
    registry = ScopeRegistry(clock=lambda: NOW)
    registry.register(token=TOKEN, binding=binding(max_inferences=1))
    with pytest.raises(TransportError):
        reserve(registry, input_token_ceiling=True)
    assert reserve(registry)


def test_pricing_uses_integer_ceiling_and_rejects_invalid_rates():
    pricing = TransportPricing(1, 2)
    assert pricing.worst_case_cost(input_bytes=1, max_output_tokens=1) == 1
    assert (
        TransportPricing(1_000_001, 2_000_001).worst_case_cost(input_bytes=2, max_output_tokens=3)
        == 9
    )
    for invalid in (-1, True, 0, 1.5):
        with pytest.raises(ValueError):
            TransportPricing(invalid, 1)


@pytest.fixture
def transport():
    calls = []
    reply = {"body": b'{"ok":true}', "status": 200}

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append((self.path, dict(self.headers), raw))
            if reply.get("trickle"):
                prefix = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 11\r\nConnection: close\r\n\r\n"
                )
                data = b'{"ok":true}'
                if reply["trickle"] == "headers":
                    data = prefix + data
                else:
                    self.connection.sendall(prefix)
                reply["started"].set()
                try:
                    for byte in data:
                        self.connection.sendall(bytes([byte]))
                        time.sleep(0.08)
                except OSError:
                    pass
                return
            self.send_response(reply["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "X-Private")
            self.send_header("X-Private", "do-not-forward")
            self.send_header("X-CivicLoop-Capability", CAPABILITY)
            if reply.get("location"):
                self.send_header("Location", reply["location"])
            self.send_header("Content-Length", str(len(reply["body"])))
            self.end_headers()
            self.wfile.write(reply["body"])

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    enabled = [True]
    registry = ScopeRegistry(clock=lambda: NOW)
    server = TransportServer(
        ("127.0.0.1", 0),
        registry=registry,
        control_token=CONTROL,
        client_token=CLIENT,
        mcp_token=MCP,
        gateway_token=GATEWAY,
        assertion_key=KEY,
        mcp_url=f"http://127.0.0.1:{upstream.server_port}/broker",
        gateway_url=f"http://127.0.0.1:{upstream.server_port}/inference",
        pricing=TransportPricing(1000000, 1000000),
        enabled=lambda: enabled[0],
    )
    threads = [
        threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01})
        for s in (upstream, server)
    ]
    for thread in threads:
        thread.start()
    yield server, calls, reply, enabled
    for service in (server, upstream):
        service.shutdown()
        service.server_close()
    for thread in threads:
        thread.join()


def post(server, path, body=None, *, auth=CLIENT, token=TOKEN, extra=None, raw=None):
    headers = {
        "Authorization": f"Bearer {auth}",
        SCOPE_HEADER: token,
        "Content-Type": "application/json",
    }
    headers.update(extra or {})
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=raw if raw is not None else json.dumps(body or {}).encode(),
        headers=headers,
    )
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read()


def register(server, *, token=TOKEN, value=None):
    value = value or binding()
    body = value.safe_metadata()
    del body["scope_digest"]
    return post(
        server,
        CONTROL_PATH,
        body,
        auth=CONTROL,
        token=token,
        extra={"X-CivicLoop-Capability": value.capability},
    )


def inference():
    return {
        "model": "civicloop-default",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "Synthetic \u2603"}],
    }


def test_control_and_data_auth_are_distinct_and_precede_body_parsing(transport):
    server, calls, _, _ = transport
    for path, auth in [
        (CONTROL_PATH, CLIENT),
        (REVOKE_PATH, CLIENT),
        ("/mcp", CONTROL),
        (INFERENCE_PATH, CONTROL),
    ]:
        status, _, raw = post(server, path, auth=auth, raw=b"not-json")
        assert status == 401
        assert json.loads(raw)["error"]["code"] == "transport_unauthorized"
    assert calls == []


def test_registration_mcp_headers_and_response_are_secret_free(transport, caplog, capsys):
    server, calls, _, _ = transport
    assert register(server)[0] == 200
    status, headers, raw = post(
        server,
        "/mcp",
        {"jsonrpc": "2.0", "method": "tools/list"},
        extra={
            "Connection": "X-Injected",
            "X-Injected": "unsafe",
            "X-CivicLoop-Budget-Assertion": "spoof",
        },
    )
    assert status == 200
    forwarded = {key.lower(): value for key, value in calls[0][1].items()}
    assert forwarded["authorization"] == f"Bearer {MCP}"
    assert forwarded["x-civicloop-capability"] == CAPABILITY
    assert "x-injected" not in forwarded and SCOPE_HEADER.lower() not in forwarded
    assert "x-civicloop-budget-assertion" not in forwarded
    assert "X-Private" not in headers and "X-CivicLoop-Capability" not in headers
    for secret in (TOKEN, CAPABILITY, CONTROL, CLIENT, MCP, GATEWAY):
        assert secret not in raw.decode() + calls[0][2].decode() + caplog.text
        assert secret not in str(capsys.readouterr())


def test_inference_retries_get_fresh_wire_compatible_assertions(transport):
    server, calls, reply, _ = transport
    assert register(server)[0] == 200
    reply["status"] = 503
    assert post(server, INFERENCE_PATH, inference())[0] == 502
    reply["status"] = 200
    assert post(server, INFERENCE_PATH, inference())[0] == 200
    assertions = []
    for _, headers, raw in calls:
        headers = {key.lower(): value for key, value in headers.items()}
        assert headers["authorization"] == f"Bearer {GATEWAY}"
        assertion = headers["x-civicloop-budget-assertion"]
        assertions.append(
            _verify_budget_assertion(assertion, key=KEY, alias="civicloop-default", now=NOW)
        )
        assert assertion.encode() not in raw
    assert assertions[0].nonce != assertions[1].nonce
    assert assertions[0].token_ceiling == binding().max_output_tokens


def test_delayed_run_a_cannot_borrow_run_b_authority(transport):
    server, calls, _, _ = transport
    assert register(server)[0] == 200
    assert post(server, "/mcp", {"method": "tools/list"})[0] == 200
    assert post(server, REVOKE_PATH, auth=CONTROL)[0] == 200
    assert register(server, token=TOKEN_B, value=binding(run_id="run-b"))[0] == 200
    for path, body in [("/mcp", {}), (INFERENCE_PATH, inference())]:
        assert post(server, path, body)[0] == 403
    assert len(calls) == 1
    assert post(server, "/mcp", token=TOKEN_B)[0] == 200


@pytest.mark.parametrize(
    "raw",
    [b"x" * (MAX_BODY_BYTES + 1), b"not-json", b'{"secret":"' + CAPABILITY.encode() + b'"}'],
    ids=["oversized", "invalid-json", "authority"],
)
def test_invalid_or_authority_bearing_body_never_reaches_upstream(transport, raw):
    server, calls, _, _ = transport
    assert register(server)[0] == 200
    if len(raw) > MAX_BODY_BYTES:
        # Announce an oversized body without sending it: rejection must happen
        # before reading, and avoids a Windows reset for unread buffered input.
        status = post(server, "/mcp", raw=b"", extra={"Content-Length": str(len(raw))})[0]
    else:
        status = post(server, "/mcp", raw=raw)[0]
    assert status == 400
    assert calls == []


def test_response_limits_secret_echo_and_upstream_errors_are_fixed(transport):
    server, _, reply, _ = transport
    assert register(server)[0] == 200
    responses = []
    for body in (
        b"x" * (MAX_BODY_BYTES + 1),
        json.dumps({"leak": CAPABILITY}).encode(),
        b"private-provider-error",
    ):
        reply["body"] = body
        status, _, raw = post(server, "/mcp")
        assert status == 502
        responses.append(raw)
    assert len(set(responses)) == 1


def test_timeout_kill_switch_model_and_budget_fail_before_forward(transport, monkeypatch):
    server, calls, _, enabled = transport
    assert register(server)[0] == 200
    assert post(server, INFERENCE_PATH, inference() | {"model": "other"})[0] == 400
    assert post(server, INFERENCE_PATH, inference() | {"max_tokens": True})[0] == 400
    assert calls == []

    def timeout(*args, **kwargs):
        raise TimeoutError("raw-provider-secret")

    monkeypatch.setattr(server, "upstream_request", timeout)
    status, _, raw = post(server, INFERENCE_PATH, inference())
    assert status == 502 and b"raw-provider-secret" not in raw
    enabled[0] = False
    assert post(server, "/mcp")[0] == 403


def test_config_requires_distinct_control_and_data_credentials(transport):
    server, _, _, _ = transport
    with pytest.raises(ValueError):
        TransportServer(
            ("127.0.0.1", 0),
            registry=server.registry,
            control_token=CLIENT,
            client_token=CLIENT,
            mcp_token=MCP,
            gateway_token=GATEWAY,
            assertion_key=KEY,
            mcp_url=server.mcp_url,
            gateway_url=server.gateway_url,
            pricing=server.pricing,
        )


def test_http_control_client_round_trip_preserves_header_only_authority(transport):
    server, calls, _, _ = transport
    client = HTTPTransportClient(
        base_url=f"http://127.0.0.1:{server.server_port}", control_token=CONTROL
    )
    client.register_scope(token=TOKEN, binding=binding())
    assert server.registry.authorize(token=TOKEN) == binding()
    client.revoke_scope(token=TOKEN)
    assert post(server, "/mcp")[0] == 403
    assert calls == []


def test_prior_assertion_cannot_be_returned_or_forwarded_as_content(transport):
    server, calls, reply, _ = transport
    assert register(server)[0] == 200
    prior = issue_budget_assertion(
        key=KEY,
        run_id="prior-run",
        model_alias="civicloop-default",
        token_ceiling=100,
        expires_at=NOW + timedelta(seconds=60),
        nonce="prior-nonce-0000000000000000",
    )
    assert post(server, "/mcp", {"leak": prior})[0] == 400
    assert calls == []
    reply["body"] = json.dumps({"leak": prior}).encode()
    assert post(server, "/mcp")[0] == 502


def test_http_input_bytes_output_and_cost_reservations_are_conservative(transport):
    server, calls, _, _ = transport
    raw = json.dumps(inference(), ensure_ascii=False).encode("utf-8")
    assert (
        register(server, value=binding(max_input_tokens=len(raw), max_cost_microusd=len(raw) + 10))[
            0
        ]
        == 200
    )
    assert post(server, INFERENCE_PATH, raw=raw)[0] == 200
    assert post(server, INFERENCE_PATH, raw=raw)[0] == 429
    assert len(calls) == 1


def test_redirects_cannot_relay_authority_to_another_endpoint(transport):
    server, calls, reply, _ = transport
    assert register(server)[0] == 200
    reply.update(status=307, location=server.mcp_url + "/redirect-target")
    assert post(server, "/mcp")[0] == 502
    assert len(calls) == 1


def test_duplicate_json_keys_cannot_hide_authority_in_raw_content(transport):
    server, calls, reply, _ = transport
    assert register(server)[0] == 200
    hidden = b'{"value":"' + CAPABILITY.encode() + b'","value":"safe"}'
    assert post(server, "/mcp", raw=hidden)[0] == 400
    assert calls == []
    reply["body"] = hidden
    assert post(server, "/mcp")[0] == 502


def test_mcp_initialized_notification_preserves_broker_202(transport):
    server, calls, reply, _ = transport
    assert register(server)[0] == 200
    # Matches backend/agents/views.py notifications/initialized response({}, 202).
    reply.update(status=202, body=b"{}")
    status, _, raw = post(server, "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202 and json.loads(raw) == {}
    assert len(calls) == 1
    assert post(server, INFERENCE_PATH, inference())[0] == 502
    reply["status"] = 201
    assert post(server, "/mcp")[0] == 502


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize(
    "timeout,lifetime", [(0.15, 0.4), (0.8, 0.15)], ids=["configured-timeout", "scope-expiry"]
)
def test_absolute_deadline_stops_trickles_and_unblocks_revoke(transport, phase, timeout, lifetime):
    server, calls, reply, _ = transport
    server.timeout = timeout
    server.registry.clock = lambda: datetime.now(UTC)
    assert (
        register(server, value=binding(expires_at=datetime.now(UTC) + timedelta(seconds=lifetime)))[
            0
        ]
        == 200
    )
    reply.update(trickle=phase, started=threading.Event())
    result = []
    started = time.monotonic()
    request_thread = threading.Thread(
        target=lambda: result.append(post(server, INFERENCE_PATH, inference()))
    )
    request_thread.start()
    assert reply["started"].wait(0.4)
    revoked = threading.Event()

    def revoke():
        server.registry.revoke(token=TOKEN)
        revoked.set()

    revoke_thread = threading.Thread(target=revoke)
    revoke_thread.start()
    try:
        assert revoked.wait(0.35), "Trickling I/O kept revocation locked beyond deadline"
        request_thread.join(0.1)
        assert not request_thread.is_alive()
        assert time.monotonic() - started < min(timeout, lifetime) + 0.25
        assert result[0][0] == 502
        assert len(calls) == 1
        entry = server.registry._scopes[scope_digest(TOKEN)]
        assert entry.binding is None
        assert entry.inferences == 1 and entry.input_tokens > 0 and entry.cost_microusd > 0
    finally:
        request_thread.join(2)
        revoke_thread.join(2)


def test_connect_deadline_rejects_late_authority_and_bounds_pending_workers(transport, monkeypatch):
    server, calls, _, _ = transport
    server.timeout = 0.15
    assert register(server)[0] == 200
    upstream_port = urlsplit(server.gateway_url).port
    connect = socket.create_connection
    release = threading.Event()
    attempts = []

    def delayed_connect(address, *args, **kwargs):
        if address[1] == upstream_port:
            attempts.append(address)
            release.wait(2)
        return connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", delayed_connect)
    try:
        started = time.monotonic()
        assert post(server, INFERENCE_PATH, inference())[0] == 502
        assert time.monotonic() - started < 0.4
        # The still-pending connect occupies the single worker slot. No retry
        # worker may accumulate, and revocation does not wait for DNS/connect.
        assert post(server, INFERENCE_PATH, inference())[0] == 502
        assert len(attempts) == 1
        server.registry.revoke(token=TOKEN)
    finally:
        release.set()
        assert server._io_slot.acquire(timeout=1)
        server._io_slot.release()
    assert calls == []
