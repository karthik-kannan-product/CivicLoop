"""Collector boundary evidence for trusted worker/MCP tracing and safe outages."""

import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from agents import mcp, tasks
from agents.capabilities import AuthorizationDenied
from agents.models import AgentRunControl, WorkflowCapability
from launchloop.models import Workflow
from observability.runtime import TelemetryConfig, build_runtime, set_runtime_for_testing
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.trace import Status, StatusCode

from tests.agents.test_hermes_tasks import FakeClient, install, queue
from tests.agents.test_hermes_tasks import inputs as inputs
from tests.agents.test_mcp_broker import broker as broker
from tests.agents.test_mcp_broker import call
from tests.observability.test_runtime import CaptureExporter, DownExporter

pytestmark = pytest.mark.django_db
CANARY = "private prompt constituent@example.org sk-prohibited-canary"


@pytest.fixture
def telemetry():
    exporter = CaptureExporter()
    runtime = build_runtime(TelemetryConfig(enabled=True, synchronous=True), exporter=exporter)
    set_runtime_for_testing(runtime)
    yield runtime, exporter
    set_runtime_for_testing(None)


class BrokerClient(FakeClient):
    def __init__(self, runtime, *, tamper=False, parent_tamper=False):
        super().__init__()
        self.runtime = runtime
        self.tamper = tamper
        self.parent_tamper = parent_tamper
        self.caller_trace_id = None

    def execute(self, run, *, capability, should_cancel):
        if self.parent_tamper:
            AgentRunControl.objects.filter(run=run).update(
                telemetry_traceparent="00-" + "f" * 32 + "-" + "a" * 16 + "-01"
            )
        # A separate caller trace mimics arbitrary incoming HTTP context. It must
        # not parent an authorized MCP span, even when its identifiers look valid.
        with self.runtime.start_span("civicloop.http.request", context=Context()) as caller:
            self.caller_trace_id = caller.get_span_context().trace_id
            caller.set_attribute("input.value", CANARY)
            caller.add_event(CANARY, {"payload": CANARY})
            caller.set_status(Status(StatusCode.ERROR, CANARY))
            arguments = {
                "workflow_id": str(run.workflow_id),
                "revision_id": run.event_revision_id,
                "actor_id": run.hermes_binding.actor_id,
                "correlation_id": str(run.hermes_binding.correlation_id),
                "request_id": str(uuid.uuid4()),
                "idempotency_key": uuid.uuid4().hex,
                "proposal": {
                    "event_copy": CANARY,
                    "invitation": {"subject": CANARY, "body": CANARY},
                    "reminder": {"subject": CANARY, "body": CANARY},
                    "social": {"body": CANARY},
                },
            }
            if self.tamper:
                arguments["correlation_id"] = str(uuid.uuid4())
                with pytest.raises(AuthorizationDenied):
                    mcp.dispatch_mcp_tool(
                        tool_name="propose_campaign_drafts",
                        arguments=arguments,
                        capability=capability,
                        service_identity="synthetic-service",
                    )
            else:
                mcp.dispatch_mcp_tool(
                    tool_name="propose_campaign_drafts",
                    arguments=arguments,
                    capability=capability,
                    service_identity="synthetic-service",
                )
        return super().execute(run, capability=capability, should_cancel=should_cancel)


def test_actual_worker_and_authorized_mcp_share_persisted_trace_at_export(
    inputs,
    monkeypatch,
    telemetry,
):
    runtime, exporter = telemetry
    monkeypatch.setattr(mcp, "authenticate_service", lambda _: None)
    client = BrokerClient(runtime)
    install(monkeypatch, client)
    run = queue(inputs)
    assert run.trace_id == "0" * 32
    before = Workflow.objects.get(pk=run.workflow_id).package_hash
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "succeeded"
    worker = next(span for span in exporter.spans if span.name == "civicloop.hermes.worker")
    tool = next(span for span in exporter.spans if span.name == "civicloop.mcp.tool")
    assert run.trace_id == f"{worker.context.trace_id:032x}"
    assert tool.context.trace_id == worker.context.trace_id != client.caller_trace_id
    assert tool.parent.span_id == worker.context.span_id
    assert AgentRunControl.objects.get(run=run).telemetry_traceparent == (
        f"00-{run.trace_id}-{worker.context.span_id:016x}-{int(worker.context.trace_flags):02x}"
    )
    for span in (worker, tool):
        assert span.attributes["civicloop.run_id"] == str(run.id)
        assert span.attributes["civicloop.workflow_id"] == str(run.workflow_id)
        assert span.attributes["civicloop.revision_id"] == run.event_revision_id
        assert span.attributes["civicloop.correlation_id"] == str(run.hermes_binding.correlation_id)
        assert span.attributes["civicloop.package_hash"] == before
        assert span.attributes["civicloop.outcome"] == "succeeded"
    exported = repr(
        [
            (span.name, dict(span.attributes), span.events, span.links, span.status)
            for span in exporter.spans
        ]
    )
    assert CANARY not in exported
    assert "constituent@example.org" not in exported
    assert "sk-prohibited-canary" not in exported
    assert Workflow.objects.get(pk=run.workflow_id).package_hash == before


def test_denied_correlation_cannot_join_worker_trace(inputs, monkeypatch, telemetry):
    runtime, exporter = telemetry
    monkeypatch.setattr(mcp, "authenticate_service", lambda _: None)
    install(monkeypatch, BrokerClient(runtime, tamper=True))
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    assert not any(span.name == "civicloop.mcp.tool" for span in exporter.spans)


def test_standalone_capability_cannot_adopt_caller_trace_or_claim_run(broker, telemetry):
    runtime, exporter = telemetry
    with runtime.start_span("civicloop.http.request", context=Context()) as caller:
        call(broker)
    tool = next(span for span in exporter.spans if span.name == "civicloop.mcp.tool")
    assert tool.context.trace_id != caller.get_span_context().trace_id
    assert tool.parent is None
    assert "civicloop.run_id" not in tool.attributes
    assert "civicloop.correlation_id" not in tool.attributes


def test_collector_outage_preserves_worker_success_and_deterministic_package(
    inputs,
    monkeypatch,
    caplog,
):
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True), exporter=DownExporter()
    )
    set_runtime_for_testing(runtime)
    try:
        monkeypatch.setattr(mcp, "authenticate_service", lambda _: None)
        install(monkeypatch, BrokerClient(runtime))
        run = queue(inputs)
        before = Workflow.objects.get(pk=run.workflow_id).package_hash
        tasks.execute_hermes_run(str(run.id))
        run.refresh_from_db()
        assert run.status == "succeeded"
        assert Workflow.objects.get(pk=run.workflow_id).package_hash == before
        assert WorkflowCapability.objects.get(pk=run.control.capability_id).revoked_at is not None
        assert runtime.force_flush() is False
        assert "sk-prohibited-canary" not in caplog.text
    finally:
        set_runtime_for_testing(None)


def test_invalid_persisted_trace_is_not_used_as_mcp_parent(inputs, monkeypatch, telemetry):
    runtime, exporter = telemetry
    monkeypatch.setattr(mcp, "authenticate_service", lambda _: None)
    install(monkeypatch, BrokerClient(runtime, parent_tamper=True))
    run = queue(inputs)
    tasks.execute_hermes_run(str(run.id))
    run.refresh_from_db()
    assert run.status == "succeeded"
    tool = next(span for span in exporter.spans if span.name == "civicloop.mcp.tool")
    assert tool.parent is None
    assert f"{tool.context.trace_id:032x}" not in {"f" * 32, run.trace_id}
    assert "civicloop.run_id" not in tool.attributes


def test_disabled_telemetry_has_no_fabricated_worker_trace(inputs, monkeypatch):
    exporter = CaptureExporter()
    runtime = build_runtime(TelemetryConfig(enabled=False), exporter=exporter)
    set_runtime_for_testing(runtime)
    try:
        install(monkeypatch, FakeClient())
        run = queue(inputs)
        tasks.execute_hermes_run(str(run.id))
        run.refresh_from_db()
        assert run.status == "succeeded"
        assert run.trace_id == "0" * 32
        assert run.control.telemetry_traceparent == ""
        assert exporter.spans == []
    finally:
        set_runtime_for_testing(None)


def test_otlp_collector_receives_only_content_free_correlated_worker_and_mcp(
    inputs,
    monkeypatch,
):
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(body)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    exporter = OTLPSpanExporter(endpoint=f"http://127.0.0.1:{server.server_port}/v1/traces")
    runtime = build_runtime(TelemetryConfig(enabled=True, synchronous=True), exporter=exporter)
    set_runtime_for_testing(runtime)
    try:
        monkeypatch.setattr(mcp, "authenticate_service", lambda _: None)
        install(monkeypatch, BrokerClient(runtime))
        run = queue(inputs)
        tasks.execute_hermes_run(str(run.id))
        run.refresh_from_db()
        assert run.status == "succeeded"
        assert runtime.force_flush()
        spans = []
        for body in received:
            message = ExportTraceServiceRequest()
            message.ParseFromString(body)
            spans.extend(
                span
                for resource in message.resource_spans
                for scope in resource.scope_spans
                for span in scope.spans
            )
        worker = next(span for span in spans if span.name == "civicloop.hermes.worker")
        tool = next(span for span in spans if span.name == "civicloop.mcp.tool")
        assert worker.trace_id.hex() == tool.trace_id.hex() == run.trace_id
        assert tool.parent_span_id == worker.span_id
        assert dict((item.key, item.value.string_value) for item in tool.attributes)[
            "civicloop.correlation_id"
        ] == str(run.hermes_binding.correlation_id)
        assert all(not span.events and not span.links and not span.status.message for span in spans)
        assert CANARY.encode() not in b"".join(received)
        assert b"constituent@example.org" not in b"".join(received)
        assert b"sk-prohibited-canary" not in b"".join(received)
    finally:
        set_runtime_for_testing(None)
        exporter.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
