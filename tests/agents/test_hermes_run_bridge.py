from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deploy.hermes.run_bridge import BridgeRejected, RunBridge

SCOPE = "scope_" + "a" * 43


@pytest.fixture
def fake_shim():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, dict(self.headers), raw))
            time.sleep(server.delay)
            response = server.response
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            if server.status == 302:
                self.send_header("Location", "http://evil.example/mcp")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.response = b'{"jsonrpc":"2.0","result":{}}'
    server.status = 200
    server.delay = 0
    server.requests = requests
    server.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _bridge(fake_shim, **kwargs):
    return RunBridge(
        scope_token=SCOPE,
        shim_base_url=fake_shim.url,
        hermes_token="hermes-client-token-32-bytes-long",
        deadline=time.monotonic() + 5,
        **kwargs,
    )


def test_bridge_injects_immutable_scope_on_mcp_and_model_calls(fake_shim):
    bridge = _bridge(fake_shim)
    with pytest.raises(AttributeError):
        bridge.scope_token = "scope_" + "b" * 43
    bridge.forward("/mcp", b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
    bridge.forward(
        "/v1/chat/completions",
        b'{"model":"civicloop-default","max_tokens":64,"stream":false}',
    )
    assert [r[1]["X-Civicloop-Transport-Scope"] for r in fake_shim.requests] == [SCOPE, SCOPE]
    assert all(SCOPE not in r[2].decode() for r in fake_shim.requests)
    assert all(
        r[1]["Authorization"] != "Bearer " + bridge.hermes_token for r in fake_shim.requests
    )
    assert SCOPE not in repr(bridge)


@pytest.mark.parametrize("path", ["/mcp/other", "/v1/models", "http://evil/mcp", "/mcp?x=1"])
def test_bridge_rejects_other_routes(fake_shim, path):
    bridge = _bridge(fake_shim)
    with pytest.raises(BridgeRejected):
        bridge.forward(path, b"{}")
    assert fake_shim.requests == []


@pytest.mark.parametrize(
    "body",
    [
        b'{"model":"other","max_tokens":1}',
        b'{"model":"civicloop-default","max_tokens":1,"stream":true}',
        b'{"model":"civicloop-default","max_tokens":1,"base_url":"http://evil"}',
        json.dumps({"method": "tools/list", "token": SCOPE}).encode(),
    ],
)
def test_bridge_rejects_model_override_streaming_and_authority(fake_shim, body):
    bridge = _bridge(fake_shim)
    path = "/mcp" if b"tools/list" in body else "/v1/chat/completions"
    with pytest.raises(BridgeRejected):
        bridge.forward(path, body)
    assert fake_shim.requests == []


def test_closed_bridge_rejects_late_a_after_b_starts(fake_shim):
    a = _bridge(fake_shim)
    a.start()
    a.close_admissions()
    assert a.drain(0.1)
    b = RunBridge(
        scope_token="scope_" + "b" * 43,
        shim_base_url=fake_shim.url,
        hermes_token="other-client-token-32-bytes-long",
        deadline=time.monotonic() + 5,
    )
    b.start()
    with pytest.raises(BridgeRejected):
        a.forward("/mcp", b'{"method":"tools/list"}')
    b.forward("/mcp", b'{"method":"tools/list"}')
    assert len(fake_shim.requests) == 1
    assert fake_shim.requests[0][1]["X-Civicloop-Transport-Scope"] == "scope_" + "b" * 43
    a.close()
    b.close()


def test_bridge_rejects_authority_echo_and_oversized_response(fake_shim):
    bridge = _bridge(fake_shim)
    fake_shim.response = json.dumps({"scope": SCOPE}).encode()
    with pytest.raises(BridgeRejected):
        bridge.forward("/mcp", b'{"method":"tools/list"}')
    fake_shim.response = b"x" * 262_145
    with pytest.raises(BridgeRejected):
        bridge.forward("/mcp", b'{"method":"tools/list"}')


def test_bridge_rejects_redirect_large_request_and_expired_deadline(fake_shim):
    bridge = _bridge(fake_shim)
    fake_shim.status = 302
    with pytest.raises(BridgeRejected):
        bridge.forward("/mcp", b'{"method":"tools/list"}')
    assert len(fake_shim.requests) == 1
    with pytest.raises(BridgeRejected):
        bridge.forward("/mcp", b"x" * 262_145)
    object.__setattr__(bridge, "deadline", time.monotonic() - 1)
    with pytest.raises(BridgeRejected):
        bridge.forward("/mcp", b'{"method":"tools/list"}')
    assert len(fake_shim.requests) == 1


def test_bridge_http_rejects_alternate_host_and_caller_scope(fake_shim):
    import urllib.error
    import urllib.request

    bridge = _bridge(fake_shim)
    url = bridge.start() + "/mcp"

    def post(headers):
        request = urllib.request.Request(
            url,
            data=b'{"method":"tools/list"}',
            headers={"Authorization": "Bearer " + bridge.hermes_token, **headers},
        )
        with pytest.raises((urllib.error.HTTPError, OSError)):
            urllib.request.urlopen(request, timeout=2)

    post({"Host": "evil.example"})
    post({"X-CivicLoop-Transport-Scope": SCOPE})
    assert fake_shim.requests == []
    bridge.close()


def test_bridge_close_drains_in_flight_before_reuse(fake_shim):
    bridge = _bridge(fake_shim)
    fake_shim.delay = 0.1
    errors = []

    def forward():
        try:
            bridge.forward("/mcp", b'{"method":"tools/list"}')
        except BridgeRejected as error:
            errors.append(error)

    thread = threading.Thread(target=forward)
    thread.start()
    until = time.monotonic() + 2
    while not fake_shim.requests and time.monotonic() < until:
        time.sleep(0.001)
    assert fake_shim.requests
    bridge.close_admissions()
    assert bridge.drain(0.01) is False
    thread.join(timeout=2)
    assert not errors
    assert bridge.drain(0.1) is True
    with pytest.raises(BridgeRejected):
        bridge.forward("/mcp", b'{"method":"tools/list"}')
