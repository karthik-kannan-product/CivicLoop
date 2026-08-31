from collections.abc import Sequence
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from observability.runtime import TelemetryConfig, build_runtime, set_runtime_for_testing


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


@pytest.fixture(autouse=True)
def reset_runtime() -> None:
    set_runtime_for_testing(None)
    yield
    set_runtime_for_testing(None)


def test_smoke_command_exports_one_content_free_span() -> None:
    exporter = CaptureExporter()
    set_runtime_for_testing(
        build_runtime(
            TelemetryConfig(enabled=True, synchronous=True),
            exporter=exporter,
        )
    )
    stdout = StringIO()

    call_command("emit_synthetic_trace", stdout=stdout)

    assert stdout.getvalue().strip() == "Synthetic telemetry smoke exported."
    assert [span.name for span in exporter.spans] == ["civicloop.synthetic_smoke"]
    assert "prompt" not in repr(exporter.spans[0].attributes).lower()


def test_smoke_command_fails_clearly_when_telemetry_is_disabled() -> None:
    set_runtime_for_testing(build_runtime(TelemetryConfig(enabled=False)))

    with pytest.raises(CommandError, match="Telemetry is disabled"):
        call_command("emit_synthetic_trace")
