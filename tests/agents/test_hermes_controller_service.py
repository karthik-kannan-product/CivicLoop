from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

import pytest

from deploy.hermes.adapter import map_upstream_result
from deploy.hermes.controller_client import RemoteProcessController
from deploy.hermes.controller_service import RUN_PATH, ControllerService, Handler
from deploy.hermes.process_controller import ControllerRun, ControllerUnavailable
from tests.agents.test_hermes_runtime_contract import _request

TOKEN = "controller-test-identity-123456789"
SCOPE = "scope_" + "a" * 43


class FakeController:
    quarantined = False

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls = 0
        self.stopped = False
        self.result = None

    def admit(self, body, **kwargs):
        self.calls += 1
        self.entered.set()
        return ControllerRun(map_upstream_result(body, {})["run_id"], "running")

    def execute(self, body, **kwargs):
        self.release.wait(2)
        return self.result or map_upstream_result(body, {"status": "failed"})

    def stop(self, run_id):
        self.stopped = True
        self.release.set()
        return ControllerRun(run_id, "cancelled")


@pytest.fixture
def service():
    controller = FakeController()
    server = ControllerService(
        ("127.0.0.1", 0), Handler, service_token=TOKEN, controller=controller
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, controller, f"http://127.0.0.1:{server.server_port}"
    controller.release.set()
    server.shutdown()
    server.server_close()
    thread.join(2)


def envelope():
    body = _request()
    body.pop("capability_token")
    return {"request": body, "expires_at": (datetime.now(UTC) + timedelta(seconds=2)).isoformat()}


def call(url, path, body=None, token=TOKEN, scope=SCOPE):
    req = urllib.request.Request(
        url + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "X-CivicLoop-Transport-Scope": scope},
    )
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def test_authentication_and_authority_rejection(service):
    _, controller, url = service
    assert call(url, RUN_PATH, envelope(), token="wrong")[0] == 401
    body = envelope()
    body["request"]["capability_token"] = "cap_" + "x" * 43
    assert call(url, RUN_PATH, body)[0] == 400
    assert call(url, RUN_PATH, envelope(), scope="invalid")[0] == 400
    assert controller.calls == 0


def test_replay_conflict_status_and_single_slot(service):
    _, controller, url = service
    body = envelope()
    status, first = call(url, RUN_PATH, body)
    assert status == 202
    assert call(url, RUN_PATH, body)[1]["run_id"] == first["run_id"]
    assert call(url, RUN_PATH, body, scope="scope_" + "b" * 43)[0] == 409
    changed = envelope()
    changed["request"]["correlation_id"] = "00000000-0000-4000-8000-000000000001"
    assert call(url, RUN_PATH, changed)[0] == 409
    assert call(url, RUN_PATH + "/" + first["run_id"], token="bad")[0] == 401
    result = call(url, RUN_PATH + "/" + first["run_id"])[1]
    assert SCOPE not in json.dumps(result)
    assert "capability" not in json.dumps(result)
    assert controller.calls == 1


def test_cancel_wins_and_is_idempotent(service):
    server, controller, url = service
    _, first = call(url, RUN_PATH, envelope())
    controller.result = map_upstream_result(
        _request(),
        {
            "status": "completed",
            "output": json.dumps(
                {
                    "proposal_references": [
                        {
                            "proposal_id": "00000000-0000-4000-8000-000000000001",
                            "schema_id": "urn:civicloop:schema:campaign",
                            "proposal_digest": "a" * 64,
                        }
                    ]
                }
            ),
        },
    )
    assert controller.entered.wait(1)
    path = RUN_PATH + "/" + first["run_id"]
    assert call(url, path + "/cancel", {})[0] == 202
    assert call(url, path + "/cancel", {})[0] == 202
    deadline = time.monotonic() + 2
    while server.active is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    result = call(url, path)[1]
    assert result["status"] == "cancelled"
    assert "result" not in result
    assert controller.stopped


def test_quarantine_expiry_and_extra_fields_reject(service):
    _, controller, url = service
    body = envelope()
    body["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert call(url, RUN_PATH, body)[0] == 400
    body = envelope() | {"scope": SCOPE}
    assert call(url, RUN_PATH, body)[0] == 400
    controller.quarantined = True
    assert call(url, "/health/ready")[0] == 503
    assert call(url, RUN_PATH, envelope())[0] == 409


def test_remote_client_polls_and_returns_terminal_result(service):
    _, controller, url = service
    controller.release.set()
    client = RemoteProcessController(url=url, token=TOKEN, poll_interval=0.01)
    result = client.execute(_request(), scope_token=SCOPE, deadline=time.monotonic() + 2)
    assert result["status"] == "failed"
    assert controller.calls == 1


def test_remote_timeout_cancels(service):
    _, controller, url = service
    client = RemoteProcessController(url=url, token=TOKEN, poll_interval=0.01)
    with pytest.raises(ControllerUnavailable):
        client.execute(_request(), scope_token=SCOPE, deadline=time.monotonic() + 0.15)
    assert controller.stopped


def test_invalid_terminal_result_is_discarded(service):
    server, controller, url = service
    controller.result = {"status": "succeeded", "raw_output": SCOPE}
    controller.release.set()
    _, first = call(url, RUN_PATH, envelope())
    deadline = time.monotonic() + 2
    while server.active is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    result = call(url, RUN_PATH + "/" + first["run_id"])[1]
    assert result["status"] == "failed"
    assert "result" not in result
    assert SCOPE not in json.dumps(server.records, default=str)


def test_duplicate_keys_and_oversized_body_are_rejected(service):
    _, controller, url = service
    for raw in [b'{"request":{},"request":{}}', b"x" * 32769]:
        req = urllib.request.Request(
            url + RUN_PATH,
            data=raw,
            headers={"Authorization": f"Bearer {TOKEN}", "X-CivicLoop-Transport-Scope": SCOPE},
        )
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=3)
        assert error.value.code == 400
    assert controller.calls == 0


def test_expired_exact_replay_returns_retained_result(service):
    server, controller, url = service
    body = envelope()
    body["expires_at"] = (datetime.now(UTC) + timedelta(seconds=0.15)).isoformat()
    controller.release.set()
    status, first = call(url, RUN_PATH, body)
    assert status == 202
    time.sleep(0.2)
    status, replay = call(url, RUN_PATH, body)
    assert status == 202
    assert replay["run_id"] == first["run_id"]
    assert controller.calls == 1
    assert server.active is None


def test_header_drip_hits_absolute_connection_deadline(service):
    server, _, _ = service
    with socket.create_connection(server.server_address, timeout=1) as client:
        client.sendall(b"GET /health/live HTTP/1.1\r\nX-Drip: ")
        for _ in range(8):
            time.sleep(0.45)
            try:
                client.sendall(b"x")
            except OSError:
                break
        try:
            client.sendall(b"\r\n\r\n")
            response = client.recv(1024)
        except OSError:
            response = b""
        assert b"200" not in response
