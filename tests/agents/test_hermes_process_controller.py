from __future__ import annotations

import json
import secrets
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from deploy.hermes.process_controller import ControllerUnavailable, ProcessController
from tests.agents.test_hermes_runtime_contract import _request as valid_request


def _scope(letter):
    return "scope_" + letter * 43


def _request(run_id):
    return {"run_id": run_id, "model_alias": "civicloop-default", "timeout_seconds": 2}


class FakeChild:
    def __init__(self, *, exit_code=None, ready=True):
        self.exit_code = exit_code
        self.ready = ready
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        if self.exit_code is None:
            raise TimeoutError
        return self.exit_code


def test_one_slot_exact_replay_and_changed_scope_conflict():
    children = []

    def factory(*args, **kwargs):
        child = FakeChild()
        children.append(child)
        return child

    controller = ProcessController(child_factory=factory, readiness_probe=lambda child, url: True)
    first = controller.admit(_request("run-a"), scope_token=_scope("a"))
    assert controller.admit(_request("run-a"), scope_token=_scope("a")) == first
    with pytest.raises(ControllerUnavailable):
        controller.admit(_request("run-a"), scope_token=_scope("b"))
    with pytest.raises(ControllerUnavailable):
        controller.admit(_request("run-b"), scope_token=_scope("b"))
    assert len(children) == 1


def test_uncertain_child_exit_quarantines_without_second_admission():
    controller = ProcessController(
        child_factory=lambda *args, **kwargs: FakeChild(),
        readiness_probe=lambda child, url: True,
        termination_timeout=0.01,
    )
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    controller.stop("run-a")
    assert controller.quarantined is True
    with pytest.raises(ControllerUnavailable):
        controller.admit(_request("run-b"), scope_token=_scope("b"))


def test_term_timeout_uses_bounded_kill_and_allows_retirement():
    class KillableChild(FakeChild):
        def kill(self):
            self.killed = True
            self.exit_code = -9

    child = KillableChild()
    controller = ProcessController(
        child_factory=lambda *args, **kwargs: child,
        readiness_probe=lambda child, url: True,
        termination_timeout=0.01,
    )
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    assert controller.stop("run-a").status == "cancelled"
    assert child.terminated and child.killed
    assert controller.quarantined is False


def test_early_exit_and_readiness_timeout_fail_closed():
    exited = ProcessController(child_factory=lambda *a, **k: FakeChild(exit_code=1))
    with pytest.raises(ControllerUnavailable):
        exited.admit(_request("run-a"), scope_token=_scope("a"))
    assert exited.quarantined is False

    child = FakeChild(exit_code=0)
    never_ready = ProcessController(
        child_factory=lambda *a, **k: child,
        readiness_probe=lambda child, url: False,
        readiness_timeout=0.01,
    )
    with pytest.raises(ControllerUnavailable):
        never_ready.admit(_request("run-b"), scope_token=_scope("b"))


def test_terminal_child_is_retired_before_next_run():
    children = []

    def factory(*args, **kwargs):
        child = FakeChild()
        children.append(child)
        return child

    controller = ProcessController(child_factory=factory, readiness_probe=lambda child, url: True)
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    children[0].exit_code = 0
    assert controller.status("run-a").status == "failed"
    controller.admit(_request("run-b"), scope_token=_scope("b"))
    assert len(children) == 2


def test_child_receives_only_loopback_routes_and_no_transport_scope():
    launched = []

    def factory(argv, **kwargs):
        launched.append((argv, kwargs))
        return FakeChild(exit_code=0)

    controller = ProcessController(child_factory=factory)
    with pytest.raises(ControllerUnavailable):
        controller.admit(_request("run-a"), scope_token=_scope("a"))
    argv, options = launched[0]
    assert options["shell"] is False
    assert argv[1].endswith("run_child.py")
    assert _scope("a") not in str((argv, options))
    assert options["env"]["API_SERVER_HOST"] == "127.0.0.1"
    assert "HTTP_PROXY" not in options["env"]
    assert "OPENAI_API_KEY" not in options["env"]


def test_revocation_failure_quarantines():
    class BrokenTransport:
        def revoke_scope(self, *, token):
            raise RuntimeError("secret must never escape")

    child = FakeChild()
    controller = ProcessController(
        child_factory=lambda *a, **k: child,
        readiness_probe=lambda child, url: True,
        transport_client=BrokenTransport(),
    )
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    child.exit_code = 0
    assert controller.status("run-a").status == "failed"
    assert controller.quarantined
    with pytest.raises(ControllerUnavailable, match="^Hermes controller unavailable$"):
        controller.admit(_request("run-b"), scope_token=_scope("b"))


def test_unexpected_termination_error_quarantines_and_keeps_one_slot():
    child = FakeChild()

    def broken_terminate():
        raise RuntimeError("sensitive child error")

    child.terminate = broken_terminate
    child.kill = broken_terminate
    controller = ProcessController(
        child_factory=lambda *a, **k: child,
        readiness_probe=lambda child, url: True,
    )
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    assert controller.stop("run-a").status == "failed"
    assert controller.quarantined
    with pytest.raises(ControllerUnavailable, match="^Hermes controller unavailable$"):
        controller.admit(_request("run-b"), scope_token=_scope("b"))


def test_history_capacity_fails_closed_after_retirement():
    child = FakeChild()
    controller = ProcessController(
        child_factory=lambda *a, **k: child,
        readiness_probe=lambda child, url: True,
        maximum_records=1,
    )
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    child.exit_code = 0
    assert controller.status("run-a").status == "failed"
    with pytest.raises(ControllerUnavailable):
        controller.admit(_request("run-b"), scope_token=_scope("b"))


@pytest.mark.parametrize("upstream_status", ["failed", "completed"])
def test_controller_executes_adapter_protocol_on_owned_child_only(upstream_status):
    received = []
    server = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body):
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):  # noqa: N802
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self._send({"run_id": "run_test"})

        def do_GET(self):  # noqa: N802
            self._send(
                {
                    "status": upstream_status,
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
                }
            )

    class ExitingChild(FakeChild):
        def terminate(self):
            self.exit_code = 0

    def factory(argv, **kwargs):
        nonlocal server
        server = ThreadingHTTPServer(("127.0.0.1", int(kwargs["env"]["API_SERVER_PORT"])), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return ExitingChild()

    controller = ProcessController(child_factory=factory, readiness_probe=lambda child, url: True)
    try:
        result = controller.execute(valid_request(), scope_token=_scope("a"))
        expected = "succeeded" if upstream_status == "completed" else "failed"
        assert result["status"] == expected
        assert len(received) == 1
        assert _scope("a").encode() not in received[0]
        assert controller.quarantined is False
        assert controller.status(result["run_id"]).status == expected
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def test_child_bootstrap_uses_only_fixed_hermes_command(monkeypatch):
    from deploy.hermes import run_child

    calls = []
    monkeypatch.setattr(run_child.os, "execvpe", lambda *args: calls.append(args))
    run_child.main()
    assert calls[0][:2] == ("hermes", ["hermes", "gateway", "run"])


def test_partial_body_handler_cannot_survive_into_run_b():
    class Exiting(FakeChild):
        def terminate(self):
            self.exit_code = 0

    bridges = []

    def factory(argv, **kwargs):
        config = yaml.safe_load((Path(kwargs["cwd"]) / "config.yaml").read_text())
        civicloop = config["mcp_servers"]["civicloop"]
        bridges.append((civicloop["url"], civicloop["headers"]["Authorization"]))
        return Exiting()

    controller = ProcessController(child_factory=factory, readiness_probe=lambda *a: True)
    controller.admit(_request("run-a"), scope_token=_scope("a"))
    port = int(bridges[0][0].split(":")[2].split("/")[0])
    bearer = bridges[0][1]
    assert bearer.startswith("Bearer ")
    with socket.create_connection(("127.0.0.1", port)) as client:
        client.sendall(
            b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:"
            + str(port).encode()
            + b"\r\nAuthorization: "
            + bearer.encode()
            + b"\r\nContent-Length: 100\r\n\r\n{"
        )
        bridge = controller._active.bridge
        until = time.monotonic() + 1
        while bridge.drain(0) and time.monotonic() < until:
            time.sleep(0.001)
        assert bridge.drain(0) is False
        client.settimeout(0.05)
        with pytest.raises(TimeoutError):
            client.recv(1)
        controller.stop("run-a")
        if bridge.drain(0):
            controller.admit(_request("run-b"), scope_token=_scope("b"))
        else:
            assert controller.quarantined
            with pytest.raises(ControllerUnavailable):
                controller.admit(_request("run-b"), scope_token=_scope("b"))


def test_real_health_after_readiness_cutoff_cannot_admit_child():
    server = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            time.sleep(0.08)
            raw = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except OSError:
                pass

    class Exiting(FakeChild):
        def terminate(self):
            self.exit_code = 0

    def factory(argv, **kwargs):
        nonlocal server
        server = ThreadingHTTPServer(("127.0.0.1", int(kwargs["env"]["API_SERVER_PORT"])), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return Exiting()

    controller = ProcessController(child_factory=factory, readiness_timeout=0.03)
    started = time.monotonic()
    try:
        with pytest.raises(ControllerUnavailable):
            controller.admit(_request("run-a"), scope_token=_scope("a"))
        assert time.monotonic() - started < 0.2
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def test_replaced_home_on_startup_failure_quarantines_without_external_delete(
    monkeypatch,
):
    from deploy.hermes import process_controller

    external = Path.cwd() / ".superpowers" / ("external-" + secrets.token_hex(8))
    external.mkdir()
    sentinel = external / "keep"
    sentinel.write_text("unchanged")
    replaced = []

    def replace(home, bridge):
        home.rmdir()
        home.symlink_to(external, target_is_directory=True)
        replaced.append(home)
        raise RuntimeError("startup failed")

    monkeypatch.setattr(process_controller, "_write_config", replace)
    controller = ProcessController(child_factory=lambda *a, **k: FakeChild())
    try:
        with pytest.raises(ControllerUnavailable):
            controller.admit(_request("run-a"), scope_token=_scope("a"))
        assert sentinel.read_text() == "unchanged"
        assert controller.quarantined
        with pytest.raises(ControllerUnavailable):
            controller.admit(_request("run-b"), scope_token=_scope("b"))
    finally:
        for home in replaced:
            home.unlink()
        shutil.rmtree(external)


def test_trickled_child_status_cannot_outlive_scope_deadline():
    server = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            raw = b'{"run_id":"run_test"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            raw = b'{"status":"running"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                for byte in raw:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.02)
            except OSError:
                pass

    class Exiting(FakeChild):
        def terminate(self):
            self.exit_code = 0

    def factory(argv, **kwargs):
        nonlocal server
        server = ThreadingHTTPServer(("127.0.0.1", int(kwargs["env"]["API_SERVER_PORT"])), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return Exiting()

    controller = ProcessController(child_factory=factory, readiness_probe=lambda *a: True)
    started = time.monotonic()
    try:
        with pytest.raises(ControllerUnavailable):
            controller.execute(
                valid_request(),
                scope_token=_scope("a"),
                deadline=started + 0.12,
            )
        assert time.monotonic() - started < 0.35
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


def test_near_expired_scope_caps_readiness_below_body_timeout():
    controller = ProcessController(
        child_factory=lambda *a, **k: FakeChild(),
        readiness_probe=lambda *a: (time.sleep(0.04) or True),
    )
    start = time.monotonic()
    with pytest.raises(ControllerUnavailable):
        controller.admit(
            _request("run-a"), scope_token=_scope("a"), deadline=start + 0.02
        )
    assert time.monotonic() - start < 0.3
