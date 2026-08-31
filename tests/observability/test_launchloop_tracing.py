from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from django.test import Client
from launchloop.models import DemoActor, Workflow
from launchloop.services import (
    answer_questions,
    decide_approval,
    reset_demo,
    run_workflow,
    submit_workflow,
)
from observability.launchloop import trace_headers_for_workflow
from observability.redaction import ALLOWED_SPAN_ATTRIBUTES
from observability.runtime import (
    TelemetryConfig,
    build_runtime,
    set_runtime_for_testing,
)


class CaptureExporter:
    def __init__(self) -> None:
        self.spans: list[object] = []

    def export(self, spans: Sequence[object]) -> object:
        self.spans.extend(spans)
        return "success"

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class DownExporter(CaptureExporter):
    def export(self, spans: Sequence[object]) -> object:
        raise ConnectionError("phoenix unavailable")


@pytest.fixture(autouse=True)
def reset_telemetry_runtime() -> None:
    set_runtime_for_testing(None)
    yield
    set_runtime_for_testing(None)


def _complete_workflow() -> tuple[object, object]:
    workflow = reset_demo()
    operator = DemoActor.objects.get(slug="maya")
    approver = DemoActor.objects.get(slug="jordan")
    run_workflow(workflow.id, operator)
    answer_questions(
        workflow.id,
        operator,
        {
            "venue_name": "Hudson Civic Center",
            "venue_address": "455 West 34th Street, New York, NY 10001",
            "access_instructions": "Use the 10th Avenue entrance.",
        },
    )
    ready = run_workflow(workflow.id, operator)
    approval = submit_workflow(workflow.id, operator)
    decide_approval(approval.id, approver, "approve", approval.package_hash)
    ready.refresh_from_db()
    approval.refresh_from_db()
    return ready, approval


@pytest.mark.django_db
def test_complete_deterministic_journey_exports_one_safe_trace() -> None:
    exporter = CaptureExporter()
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True),
        exporter=exporter,
    )
    set_runtime_for_testing(runtime)

    workflow, approval = _complete_workflow()
    runtime.force_flush()

    names = {span.name for span in exporter.spans}
    assert {
        "launchloop.workflow",
        "launchloop.request",
        "launchloop.deterministic_lane",
        "launchloop.policy",
        "launchloop.evaluation",
        "launchloop.approval",
        "launchloop.sandbox_connector",
    } <= names
    trace_ids = {span.context.trace_id for span in exporter.spans}
    assert len(trace_ids) == 1
    assert all(
        set((span.attributes or {}).keys()) <= ALLOWED_SPAN_ATTRIBUTES
        for span in exporter.spans
    )
    exported = repr([dict(span.attributes or {}) for span in exporter.spans])
    assert "Hudson Civic Center" not in exported
    assert "455 West 34th Street" not in exported
    assert approval.status == "approved"
    assert workflow.status == "completed"
    assert workflow.telemetry_traceparent.startswith("00-")
    assert trace_headers_for_workflow(workflow) == {
        "traceparent": workflow.telemetry_traceparent
    }


@pytest.mark.django_db
def test_telemetry_outage_does_not_change_workflow_result() -> None:
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True),
        exporter=DownExporter(),
    )
    set_runtime_for_testing(runtime)

    workflow, approval = _complete_workflow()

    assert workflow.status == "completed"
    assert workflow.package_hash == approval.package_hash
    assert approval.execution.receipt["external_actions"] == 0


@pytest.mark.django_db
def test_http_trace_context_continues_into_future_worker_headers() -> None:
    exporter = CaptureExporter()
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True),
        exporter=exporter,
    )
    set_runtime_for_testing(runtime)
    client = Client()
    client.post(
        "/api/v1/auth/login",
        data=json.dumps({"username": "maya.operator", "password": "civicloop-demo"}),
        content_type="application/json",
    )
    workflow_id = client.post("/api/v1/demo/reset").json()["workflow"]["id"]
    incoming_trace_id = "0af7651916cd43dd8448eb211c80319c"
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/runs",
        HTTP_TRACEPARENT=f"00-{incoming_trace_id}-b7ad6b7169203331-01",
    )

    assert response.status_code == 200
    assert response["traceparent"].split("-")[1] == incoming_trace_id
    persisted = trace_headers_for_workflow(Workflow.objects.get(id=workflow_id))
    assert persisted["traceparent"].split("-")[1] == incoming_trace_id
