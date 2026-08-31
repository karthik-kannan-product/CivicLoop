from __future__ import annotations

from collections.abc import Sequence

from observability.runtime import TelemetryConfig, build_runtime


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
        raise OSError("collector unavailable with sk-prohibited-canary")


def test_disabled_runtime_never_calls_exporter() -> None:
    exporter = CaptureExporter()
    runtime = build_runtime(TelemetryConfig(enabled=False), exporter=exporter)

    with runtime.start_span("launchloop.request") as span:
        span.set_attribute("civicloop.workflow_id", "workflow-1")
    runtime.force_flush()

    assert exporter.spans == []
    assert runtime.enabled is False


def test_redaction_processor_allowlists_keys_and_removes_prohibited_values() -> None:
    exporter = CaptureExporter()
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True),
        exporter=exporter,
    )

    with runtime.start_span("launchloop.request") as span:
        span.set_attribute("civicloop.workflow_id", "workflow-1")
        span.set_attribute("civicloop.workflow_status", "draft")
        span.set_attribute("input.value", "private prompt body")
        span.set_attribute("event.title", "private event title")
        span.set_attribute("civicloop.summary", "contact sk-prohibited-canary")
    runtime.force_flush()

    assert len(exporter.spans) == 1
    attributes = dict(exporter.spans[0].attributes)
    exported = repr(attributes)
    assert attributes["civicloop.workflow_id"] == "workflow-1"
    assert attributes["civicloop.workflow_status"] == "draft"
    assert "input.value" not in attributes
    assert "event.title" not in attributes
    assert "private prompt body" not in exported
    assert "private event title" not in exported
    assert "sk-prohibited-canary" not in exported


def test_oversize_attributes_are_bounded_before_export() -> None:
    exporter = CaptureExporter()
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True, max_attribute_length=64),
        exporter=exporter,
    )

    with runtime.start_span("launchloop.policy") as span:
        span.set_attribute("civicloop.workflow_id", "a" * 4_096)
    runtime.force_flush()

    value = dict(exporter.spans[0].attributes)["civicloop.workflow_id"]
    assert isinstance(value, str)
    assert len(value) <= 64
    assert value.endswith("...[truncated]")


def test_exporter_outage_never_escapes_the_business_span() -> None:
    runtime = build_runtime(
        TelemetryConfig(enabled=True, synchronous=True),
        exporter=DownExporter(),
    )

    with runtime.start_span("launchloop.request") as span:
        span.set_attribute("civicloop.workflow_id", "workflow-1")

    assert runtime.force_flush() is False
