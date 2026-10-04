import json
import socket
import threading
import time
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from agents.hermes import HermesClient, SafeRunFailure
from django.test import override_settings
from django.utils import timezone


@pytest.fixture
def run():
    correlation = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"urn:civicloop:run:{correlation}"),
        workflow_id=uuid.uuid4(),
        event_revision_id=7,
        hermes_binding=SimpleNamespace(
            correlation_id=correlation,
            revision_digest="a" * 64,
            actor=SimpleNamespace(slug="synthetic-operator"),
        ),
        model_profile=SimpleNamespace(max_input_tokens=1000, max_output_tokens=100),
        routing_policy=SimpleNamespace(per_run_limit_microusd=5000),
    )


@pytest.fixture
def client(monkeypatch):
    value = HermesClient(url="http://adapter:8080", token="synthetic-adapter-0000")
    monkeypatch.setattr(
        value, "_load_capability", lambda run, capability: timezone.now() + timedelta(seconds=100)
    )
    monkeypatch.setattr(
        value,
        "_load_reservation",
        lambda run: SimpleNamespace(
            reserved_cost_microusd=5000, expires_at=timezone.now() + timedelta(seconds=100)
        ),
    )
    return value


def result(run):
    return {
        "schema_version": "1.0",
        "run_id": str(run.id),
        "workflow_id": str(run.workflow_id),
        "revision_id": run.event_revision_id,
        "status": "succeeded",
        "failure_category": None,
        "proposal_references": [
            {
                "proposal_id": str(uuid.uuid4()),
                "schema_id": "urn:civicloop:schema:campaign",
                "proposal_digest": "b" * 64,
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 20, "cost_microusd": 30},
    }


def respond(monkeypatch, client, payload):
    monkeypatch.setattr(client, "_request", lambda **kwargs: (200, json.dumps(payload).encode()))


def test_success_uses_only_immutable_binding_and_trusted_scope(client, run, monkeypatch):
    captured = {}
    expected = result(run)

    def request(**kwargs):
        captured.update(kwargs)
        return 200, json.dumps(expected).encode()

    monkeypatch.setattr(client, "_request", request)
    assert client.execute(run, capability="cap_" + "x" * 43) == expected
    body = json.loads(captured["raw"])
    scope = json.loads(captured["headers"]["X-CivicLoop-Run-Binding"])
    assert body["actor_id"] == "synthetic-operator"
    assert body["correlation_id"] == str(run.hermes_binding.correlation_id)
    assert body["budgets"]["max_cost_microusd"] == 5000
    assert scope["revision_digest"] == "a" * 64
    assert scope["run_id"] == str(run.id)
    assert scope["max_inferences"] == 8
    assert "capability" not in scope
    assert captured["headers"]["Authorization"] == "Bearer synthetic-adapter-0000"


def test_bound_reservation_caps_the_runtime_cost(client, run, monkeypatch):
    monkeypatch.setattr(
        client,
        "_load_reservation",
        lambda run: SimpleNamespace(
            reserved_cost_microusd=40, expires_at=timezone.now() + timedelta(seconds=100)
        ),
    )
    payload = result(run)
    payload["usage"]["cost_microusd"] = 41
    respond(monkeypatch, client, payload)
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43)
    assert failure.value.category == "invalid_output"


@pytest.mark.django_db
def test_missing_reservation_blocks_admission():
    from tests.agents.test_runs import create_run

    client = HermesClient(url="http://adapter:8080", token="synthetic-adapter-0000")
    with pytest.raises(SafeRunFailure):
        client._load_reservation(create_run())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(extra="private-content"),
        lambda r: r.update(run_id=str(uuid.uuid4())),
        lambda r: r.update(workflow_id=str(uuid.uuid4())),
        lambda r: r.update(revision_id=True),
        lambda r: r.update(status="running"),
        lambda r: r.update(status=[]),
        lambda r: r.update(failure_category="private-content"),
        lambda r: r.update(proposal_references=[]),
        lambda r: r["usage"].update(input_tokens=True),
        lambda r: r["usage"].update(output_tokens=-1),
        lambda r: r["usage"].update(cost_microusd=5001),
        lambda r: r["usage"].update(input_tokens=1001),
        lambda r: r["usage"].update(output_tokens=101),
        lambda r: r["usage"].update(extra=0),
        lambda r: r["proposal_references"][0].update(extra="private-content"),
        lambda r: r["proposal_references"][0].update(proposal_digest="bad"),
        lambda r: r["proposal_references"].append(dict(r["proposal_references"][0])),
    ],
)
def test_invalid_bound_results_fail_safely(client, run, monkeypatch, mutation):
    payload = result(run)
    mutation(payload)
    respond(monkeypatch, client, payload)
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43)
    assert failure.value.category == "invalid_output"
    assert "private-content" not in str(failure.value)
    assert failure.value.__cause__ is None


def test_duplicate_json_keys_are_rejected(client, run, monkeypatch):
    raw = json.dumps(result(run)).replace(
        '"schema_version": "1.0"', '"schema_version":"0", "schema_version":"1.0"'
    )
    monkeypatch.setattr(client, "_request", lambda **kwargs: (200, raw.encode()))
    with pytest.raises(SafeRunFailure, match="Hermes run failed"):
        client.execute(run, capability="cap_" + "x" * 43)


def test_cancellation_before_admission_sends_nothing(client, run, monkeypatch):
    monkeypatch.setattr(client, "_request", lambda **kwargs: pytest.fail("unexpected admission"))
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43, should_cancel=lambda: True)
    assert (failure.value.status, failure.value.category) == ("cancelled", "cancelled")


@pytest.mark.parametrize(
    "ack", [None, {"status": "cancelled"}, {"schema_version": "1.0", "status": "running"}]
)
def test_uncertain_cleanup_is_dependency_failure(client, run, monkeypatch, ack):
    respond(monkeypatch, client, ack)
    with pytest.raises(SafeRunFailure) as failure:
        client.cancel(run)
    assert failure.value.category == "dependency_unavailable"


def test_cancel_accepts_only_bound_cleanup_confirmation(client, run, monkeypatch):
    respond(
        monkeypatch, client, {"schema_version": "1.0", "run_id": str(run.id), "status": "cancelled"}
    )
    assert client.cancel(run) is True


@pytest.mark.parametrize(
    "url",
    [
        "ftp://adapter",
        "http://user:password@adapter",
        "http://adapter/?secret=1",
        "http://adapter/#x",
    ],
)
def test_client_rejects_unsafe_endpoint_configuration(url):
    with pytest.raises(SafeRunFailure):
        HermesClient(url=url, token="synthetic-adapter-0000")


def test_settings_load_token_without_disclosing_it(monkeypatch):
    monkeypatch.setattr(
        "agents.hermes.Path.read_text", lambda self, **kwargs: "synthetic-adapter-0000"
    )
    monkeypatch.setattr(
        "agents.hermes.Path.stat", lambda self: SimpleNamespace(st_size=22, st_mode=0o100600)
    )
    with override_settings(
        CIVICLOOP_HERMES_ADAPTER_URL="http://adapter:8080",
        CIVICLOOP_HERMES_ADAPTER_TOKEN_FILE="synthetic-token-file",
        CIVICLOOP_HERMES_TIMEOUT_SECONDS=120,
    ):
        client = HermesClient.from_settings()
    assert "synthetic-adapter" not in repr(client)


def test_oversized_body_rejected(client, run, monkeypatch):
    monkeypatch.setattr(client, "_request", lambda **kwargs: (200, b" " * 32769))
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43)
    assert failure.value.category == "invalid_output"


def test_inflight_cancellation_requires_cleanup_ack(client, run, monkeypatch):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        if kwargs["path"].endswith("/cancel"):
            return 200, json.dumps(
                {"schema_version": "1.0", "run_id": str(run.id), "status": "cancelled"}
            ).encode()
        raise SafeRunFailure("cancelled", status="cancelled")

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43)
    assert failure.value.status == "cancelled"
    assert len(calls) == 2


@pytest.mark.parametrize("category", ["timeout", "dependency_unavailable"])
def test_network_failure_requires_cleanup_before_original_failure(
    client, run, monkeypatch, category
):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        if kwargs["path"].endswith("/cancel"):
            return 200, json.dumps(
                {"schema_version": "1.0", "run_id": str(run.id), "status": "cancelled"}
            ).encode()
        raise SafeRunFailure(category)

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43)
    assert failure.value.category == category
    assert len(calls) == 2


def test_ambiguous_network_cleanup_overrides_timeout(client, run, monkeypatch):
    monkeypatch.setattr(
        client, "_request", lambda **kwargs: (_ for _ in ()).throw(SafeRunFailure("timeout"))
    )
    with pytest.raises(SafeRunFailure) as failure:
        client.execute(run, capability="cap_" + "x" * 43)
    assert failure.value.category == "dependency_unavailable"


@pytest.mark.parametrize(
    "status,category",
    [("failed", "timeout"), ("cancelled", "cancelled"), ("failed", "capability_rejected")],
)
def test_valid_terminal_failures_are_content_free(client, run, monkeypatch, status, category):
    payload = result(run)
    payload.update(status=status, failure_category=category, proposal_references=[])
    respond(monkeypatch, client, payload)
    assert client.execute(run, capability="cap_" + "x" * 43) == payload


def test_transport_returns_redirect_without_following(monkeypatch):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/must-not-be-followed")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HermesClient(
        url=f"http://127.0.0.1:{server.server_port}", token="synthetic-adapter-0000"
    )
    try:
        assert client._request(
            method="POST",
            path="/",
            raw=b"{}",
            headers={},
            deadline=time.monotonic() + 1,
            should_cancel=None,
        ) == (302, b"{}")
        assert calls == ["/"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mismatch", [None, "revoked", "revision", "tools"])
@pytest.mark.django_db
def test_capability_requires_live_exact_binding(monkeypatch, mismatch):
    from agents.capabilities import TOOLS, token_digest
    from agents.models import HermesRunBinding, WorkflowCapability

    from tests.agents.test_runs import create_run

    run = create_run()
    binding = HermesRunBinding.objects.create(run=run, actor=run.event_revision.author)
    capability = "cap_" + "x" * 43
    record = WorkflowCapability.objects.create(
        token_digest=token_digest(capability),
        workflow_id=run.workflow_id,
        revision_id=run.event_revision_id,
        revision_digest=binding.revision_digest,
        actor=binding.actor,
        tools=sorted(TOOLS),
        correlation_id=binding.correlation_id,
        expires_at=timezone.now() + timedelta(seconds=100),
    )
    if mismatch == "revoked":
        record.revoked_at = timezone.now()
    elif mismatch == "revision":
        record.revision_digest = "c" * 64
    elif mismatch == "tools":
        record.tools = ["get_event_revision"]
    record.save()
    client = HermesClient(url="http://adapter:8080", token="synthetic-adapter-0000")
    if mismatch:
        with pytest.raises(SafeRunFailure):
            client._load_capability(run, capability)
    else:
        assert client._load_capability(run, capability) == record.expires_at


def test_absolute_deadline_aborts_slow_drip_and_ignores_proxy(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.03)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    client = HermesClient(
        url=f"http://127.0.0.1:{server.server_port}", token="synthetic-adapter-0000"
    )
    started = time.monotonic()
    try:
        with pytest.raises(SafeRunFailure) as failure:
            client._request(
                method="POST",
                path="/",
                raw=b"{}",
                headers={},
                deadline=started + 0.15,
                should_cancel=None,
            )
        assert failure.value.category == "timeout"
        assert time.monotonic() - started < 0.5
    finally:
        server.shutdown()
        server.server_close()


def test_late_dns_connection_cannot_send_authority(monkeypatch):
    left, right = socket.socketpair()

    def late(*args, **kwargs):
        time.sleep(0.15)
        return left

    monkeypatch.setattr(socket, "create_connection", late)
    client = HermesClient(url="http://adapter:8080", token="synthetic-adapter-0000")
    try:
        with pytest.raises(SafeRunFailure):
            client._request(
                method="POST",
                path="/",
                raw=b"{}",
                headers={},
                deadline=time.monotonic() + 0.05,
                should_cancel=None,
            )
        right.settimeout(0.5)
        assert right.recv(1) == b""
    finally:
        left.close()
        right.close()


def test_server_cleanup_ack_cannot_hide_non_draining_execution_dns(run, monkeypatch):
    """Control can reach cleanup, but stalled execution still prevents a local ACK."""
    from agents import hermes

    release = threading.Event()
    entered = threading.Event()
    left, right = socket.socketpair()
    connect = socket.create_connection
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            requests.append(self.path == f"/internal/v1/hermes/runs/{run.id}/cancel")
            raw = json.dumps({
                "schema_version": "1.0", "run_id": str(run.id), "status": "cancelled",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    def stalled(address, *args, **kwargs):
        if address[0] == "adapter":
            entered.set()
            release.wait(5)
            return left
        return connect(address, *args, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(socket, "create_connection", stalled)
    client = HermesClient(url="http://adapter:8080", token="synthetic-adapter-0000")
    try:
        with pytest.raises(SafeRunFailure):
            client._request(
                method="POST", path="/", raw=b"{}", headers={},
                deadline=time.monotonic() + 0.05, should_cancel=None,
            )
        assert entered.is_set()
        client.url = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(SafeRunFailure):
            client.cancel(run)
        assert requests == [True]
    finally:
        release.set()
        # Reclaim the real execution worker before allowing another test to run.
        drained = hermes._IO_SLOT.acquire(timeout=1)
        if drained:
            hermes._IO_SLOT.release()
        server.shutdown()
        server.server_close()
        right.settimeout(0.5)
        try:
            assert right.recv(1) == b""
            assert drained
        finally:
            left.close()
            right.close()


def test_only_one_cleanup_request_can_use_reserved_control_lane(run):
    entered = threading.Event()
    release = threading.Event()
    requests = []
    result = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            requests.append(self.path.endswith("/cancel"))
            entered.set()
            release.wait(2)
            raw = json.dumps({
                "schema_version": "1.0", "run_id": str(run.id), "status": "cancelled",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    client = HermesClient(
        url=f"http://127.0.0.1:{server.server_port}", token="synthetic-adapter-0000",
    )
    first = threading.Thread(target=lambda: result.append(client.cancel(run)), daemon=True)
    try:
        first.start()
        assert entered.wait(1)
        with pytest.raises(SafeRunFailure):
            client.cancel(run)
        assert requests == [True]
        release.set()
        first.join(3)
        assert not first.is_alive() and result == [True]
    finally:
        release.set()
        first.join(3)
        server.shutdown()
        server.server_close()
