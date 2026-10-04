import pytest

from deploy.hermes import transport_service as service


def configuration(monkeypatch):
    monkeypatch.setattr(service, "_read_token", lambda path, **kwargs: path)
    monkeypatch.setattr(service, "_assertion_key", lambda path: b"s" * 32)
    return {
        "HERMES_TRANSPORT_CONTROL_TOKEN_FILE": "synthetic-control-token",
        "HERMES_TRANSPORT_CLIENT_TOKEN_FILE": "synthetic-client-token",
        "HERMES_TRANSPORT_MCP_TOKEN_FILE": "synthetic-mcp-token",
        "HERMES_TRANSPORT_GATEWAY_TOKEN_FILE": "synthetic-gateway-token",
        "HERMES_TRANSPORT_ASSERTION_KEY_FILE": "synthetic-key-file",
        "HERMES_TRANSPORT_MCP_URL": "http://mcp:8000/internal/v1/mcp",
        "HERMES_TRANSPORT_GATEWAY_URL": "http://litellm:4000/v1/chat/completions",
        "PORT": "0",
    }


@pytest.mark.parametrize(
    "hermes,pending", [(False, False), (True, False), (False, True), (True, True)]
)
def test_daemon_configuration_requires_both_activation_gates(monkeypatch, hermes, pending):
    env = configuration(monkeypatch)
    env["CIVICLOOP_HERMES_ENABLED"] = str(hermes).lower()
    env["CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED"] = str(pending).lower()
    server = service.build_server(env)
    try:
        assert server.enabled() is (hermes and pending)
        assert server.mcp_url == env["HERMES_TRANSPORT_MCP_URL"]
        assert server.gateway_url == env["HERMES_TRANSPORT_GATEWAY_URL"]
        assert server.registry.maximum_active == 1
    finally:
        server.server_close()


def test_missing_identity_configuration_fails_without_echo(monkeypatch):
    env = configuration(monkeypatch)
    del env["HERMES_TRANSPORT_CONTROL_TOKEN_FILE"]
    with pytest.raises(RuntimeError, match="Transport configuration unavailable"):
        service.build_server(env)


def test_transport_liveness_is_closed_and_does_not_enable_forwarding(monkeypatch):
    import threading
    import urllib.request

    env = configuration(monkeypatch)
    server = service.build_server(env)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/health/live", timeout=2
        ) as reply:
            assert reply.read() == b'{"status":"ok"}'
        assert server.enabled() is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
