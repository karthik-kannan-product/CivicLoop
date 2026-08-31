from django.core.management.base import BaseCommand, CommandError
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes

from observability.runtime import get_runtime


class Command(BaseCommand):
    help = "Emit one content-free synthetic trace and verify OTLP acceptance."

    def handle(self, *args: object, **options: object) -> None:
        runtime = get_runtime()
        if not runtime.enabled:
            raise CommandError("Telemetry is disabled; no synthetic trace was emitted.")
        with runtime.start_span(
            "civicloop.synthetic_smoke",
            attributes={
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.CHAIN.value,
                "civicloop.fixture_id": "checkpoint-c-smoke",
                "civicloop.outcome": "passed",
            },
        ):
            pass
        if not runtime.force_flush(5_000):
            raise CommandError("Telemetry collector did not accept the synthetic trace.")
        self.stdout.write("Synthetic telemetry smoke exported.")

