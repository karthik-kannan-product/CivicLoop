from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.trace import Span

from .runtime import get_runtime

if TYPE_CHECKING:
    from launchloop.models import Workflow


def _workflow_attributes(workflow: Workflow, stage: str) -> dict[str, str | int]:
    return {
        "civicloop.workflow_id": str(workflow.id),
        "civicloop.revision_id": str(workflow.revision_id),
        "civicloop.revision_version": workflow.revision.version,
        "civicloop.workflow_status": workflow.status,
        "civicloop.stage": stage,
    }


@contextmanager
def workflow_operation(workflow: Workflow, stage: str) -> Iterator[None]:
    runtime = get_runtime()
    attributes = _workflow_attributes(workflow, stage)
    attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND] = OpenInferenceSpanKindValues.CHAIN.value
    if not runtime.enabled:
        with runtime.start_span("launchloop.request", attributes=attributes):
            yield
        return

    if workflow.telemetry_traceparent:
        parent_context = runtime.extract_context(
            {"traceparent": workflow.telemetry_traceparent}
        )
        with runtime.start_span(
            "launchloop.request",
            context=parent_context,
            attributes=attributes,
        ):
            yield
        return

    with runtime.start_span("launchloop.workflow", attributes=attributes):
        carrier: dict[str, str] = {}
        runtime.inject_context(carrier)
        traceparent = carrier.get("traceparent", "")
        if traceparent:
            workflow.telemetry_traceparent = traceparent
            workflow.save(update_fields=("telemetry_traceparent", "updated_at"))
        with runtime.start_span("launchloop.request", attributes=attributes):
            yield


@contextmanager
def workflow_stage(
    workflow: Workflow,
    name: str,
    kind: OpenInferenceSpanKindValues,
) -> Iterator[Span]:
    attributes = _workflow_attributes(workflow, name.removeprefix("launchloop."))
    attributes[SpanAttributes.OPENINFERENCE_SPAN_KIND] = kind.value
    with get_runtime().start_span(name, attributes=attributes) as span:
        yield span


def trace_headers_for_workflow(workflow: Workflow) -> dict[str, str]:
    if not workflow.telemetry_traceparent:
        return {}
    return {"traceparent": workflow.telemetry_traceparent}
