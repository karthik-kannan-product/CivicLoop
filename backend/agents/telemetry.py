"""Content-free trace metadata from trusted durable worker and capability bindings."""

import re

from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context

from agents.models import AgentRunControl

_TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})")


def run_attributes(run):
    return {
        "civicloop.run_id": str(run.id),
        "civicloop.workflow_id": str(run.workflow_id),
        "civicloop.revision_id": run.event_revision_id,
        "civicloop.correlation_id": str(run.hermes_binding.correlation_id),
        "civicloop.package_hash": run.package_hash,
    }


def worker_traceparent(span):
    context = span.get_span_context()
    if not context.is_valid:
        return ""
    return f"00-{context.trace_id:032x}-{context.span_id:016x}-{int(context.trace_flags):02x}"


def mcp_trace_binding(record):
    """Called only after broker authorization. Caller context is always discarded."""
    control = (
        AgentRunControl.objects.select_related("run__hermes_binding")
        .filter(
            capability=record,
            run__hermes_lane=True,
            run__status="running",
            run__workflow_id=record.workflow_id,
            run__event_revision_id=record.revision_id,
            run__hermes_binding__actor_id=record.actor_id,
            run__hermes_binding__correlation_id=record.correlation_id,
            run__hermes_binding__revision_digest=record.revision_digest,
        )
        .first()
    )
    if control is None:
        return Context(), {}
    matched = _TRACEPARENT.fullmatch(control.telemetry_traceparent)
    if matched is None or matched[1] != control.run.trace_id:
        return Context(), {}
    parent = SpanContext(
        trace_id=int(matched[1], 16),
        span_id=int(matched[2], 16),
        is_remote=True,
        trace_flags=TraceFlags(int(matched[3], 16)),
    )
    if not parent.is_valid:
        return Context(), {}
    return set_span_in_context(NonRecordingSpan(parent), Context()), run_attributes(control.run)
