import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from deploy.hermes import adapter
from deploy.hermes.transport import binding_payload
from tests.agents.test_hermes_adapter import Client, make_adapter, trusted_binding
from tests.agents.test_hermes_runtime_contract import _request


def test_worker_binding_header_is_closed_and_matches_launch():
    request = _request()
    value = binding_payload(trusted_binding(request))
    result = adapter.worker_binding(request, value)
    assert result.capability == request["capability_token"]
    assert result.revision_digest == value["revision_digest"]
    with pytest.raises(adapter.PolicyError):
        adapter.worker_binding(request, value | {"capability": request["capability_token"]})
    with pytest.raises(adapter.PolicyError):
        adapter.worker_binding(request, value | {"revision_id": request["revision_id"] + 1})


def test_cancel_discards_racing_success_and_waits_for_revoke():
    entered = threading.Event()
    stopped = threading.Event()

    class Controller:
        quarantined = False

        def execute(self, body, *, scope_token, deadline):
            entered.set()
            assert stopped.wait(2)
            return adapter.map_upstream_result(
                body,
                {
                    "status": "completed",
                    "output": json.dumps(
                        {
                            "proposal_references": [
                                {
                                    "proposal_id": "00000000-0000-4000-8000-000000000001",
                                    "schema_id": "urn:civicloop:schema:campaign-proposal:v1.0",
                                    "proposal_digest": "a" * 64,
                                }
                            ]
                        }
                    ),
                },
            )

        def cancel(self, run_id, *, deadline):
            stopped.set()
            return True

    client = Client()
    server = make_adapter(client)
    server.process_controller = Controller()
    request = _request()
    run_id = adapter.map_upstream_result(request, {})["run_id"]
    results = []

    def execute():
        with server.run_lock:
            results.append(server.execute(request))

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(2)
        assert server.cancel(run_id, deadline=time.monotonic() + 2) is True
        thread.join(2)
        assert not thread.is_alive()
        assert results[0]["status"] == "cancelled"
        assert results[0]["proposal_references"] == []
        assert client.events[-1][0] == "revoke"
    finally:
        stopped.set()
        thread.join(2)
        server.server_close()


def test_authenticated_http_cancellation_reaches_controller_and_drains_scope():
    from deploy.hermes.controller_client import RemoteProcessController
    from deploy.hermes.controller_service import ControllerService, Handler
    from tests.agents.test_hermes_controller_service import FakeController

    token = "controller-test-identity-123456789"
    child = FakeController()
    controller = ControllerService(("127.0.0.1", 0), Handler, service_token=token, controller=child)
    controller_thread = threading.Thread(target=controller.serve_forever, daemon=True)
    controller_thread.start()
    transport = Client()
    server = make_adapter(transport)
    server.process_controller = RemoteProcessController(
        url=f"http://127.0.0.1:{controller.server_port}",
        token=token,
        poll_interval=0.01,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    request = _request()
    results = []

    def call(path, body, *, binding=None, authorized=True):
        headers = {"Authorization": f"Bearer {server.service_token if authorized else 'wrong'}"}
        if binding is not None:
            headers[adapter.WORKER_BINDING_HEADER] = json.dumps(binding)
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}" + path,
            data=json.dumps(body).encode(),
            headers=headers,
        )
        try:
            response = urllib.request.urlopen(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    launch_thread = threading.Thread(
        target=lambda: results.append(
            call(
                adapter.RUN_PATH,
                request,
                binding=binding_payload(trusted_binding(request)),
            )
        )
    )
    try:
        assert call(adapter.RUN_PATH, request, authorized=False)[0] == 401
        assert call(adapter.RUN_PATH, request)[0] == 400
        launch_thread.start()
        assert child.entered.wait(2)
        run_id = adapter.map_upstream_result(request, {})["run_id"]
        cancel_path = adapter.RUN_PATH + "/" + run_id + "/cancel"
        assert call(cancel_path, {}, authorized=False)[0] == 401
        assert call(cancel_path, {}) == (
            200,
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "status": "cancelled",
            },
        )
        launch_thread.join(2)
        assert results[0][1]["status"] == "cancelled"
        assert child.stopped and controller.active is None
        assert transport.events[-1][0] == "revoke"
        assert server.transport_healthy
    finally:
        child.release.set()
        if launch_thread.ident:
            launch_thread.join(3)
        server.shutdown()
        server.server_close()
        controller.shutdown()
        controller.server_close()


def test_ambiguous_cleanup_disables_adapter():
    entered = threading.Event()
    release = threading.Event()

    class Controller:
        quarantined = False

        def execute(self, body, *, scope_token, deadline):
            entered.set()
            release.wait(2)
            return adapter.map_upstream_result(body, {"status": "failed"})

        def cancel(self, run_id, *, deadline):
            raise RuntimeError("untrusted error")

    client = Client()
    server = make_adapter(client)
    server.process_controller = Controller()
    request = _request()
    errors = []

    def execute():
        try:
            with server.run_lock:
                server.execute(request)
        except adapter.UpstreamError:
            errors.append(True)

    thread = threading.Thread(target=execute)
    try:
        thread.start()
        assert entered.wait(2)
        assert (
            server.cancel(
                adapter.map_upstream_result(request, {})["run_id"], deadline=time.monotonic() + 0.1
            )
            is False
        )
        assert server.transport_healthy is False
    finally:
        release.set()
        thread.join(2)
        server.server_close()
