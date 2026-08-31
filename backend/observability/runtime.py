from __future__ import annotations

import logging
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Tracer

from .redaction import sanitize_span_attributes

logger = logging.getLogger(__name__)


class Exporter(Protocol):
    def export(self, spans: Any) -> object: ...

    def shutdown(self) -> None: ...

    def force_flush(self, timeout_millis: int = 30_000) -> bool: ...


@dataclass(frozen=True)
class TelemetryConfig:
    enabled: bool
    endpoint: str = ""
    headers_file: str = ""
    service_name: str = "civicloop"
    environment: str = "development"
    max_attribute_length: int = 256
    max_queue_size: int = 256
    max_export_batch_size: int = 32
    schedule_delay_millis: int = 500
    export_timeout_millis: int = 1_000
    synchronous: bool = False


class RedactingSpanExporter(SpanExporter):
    def __init__(self, delegate: Exporter, *, max_attribute_length: int) -> None:
        self._delegate = delegate
        self._max_attribute_length = max_attribute_length

    def export(self, spans: Any) -> SpanExportResult:
        sanitized = tuple(self._sanitize_span(span) for span in spans)
        try:
            result = self._delegate.export(sanitized)
        except Exception:
            logger.warning("telemetry_export_failed", extra={"event": "telemetry_export_failed"})
            return SpanExportResult.FAILURE
        if isinstance(result, SpanExportResult):
            return result
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception:
            logger.warning(
                "telemetry_shutdown_failed",
                extra={"event": "telemetry_shutdown_failed"},
            )

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            return bool(self._delegate.force_flush(timeout_millis))
        except Exception:
            return True

    def _sanitize_span(self, span: ReadableSpan) -> ReadableSpan:
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=sanitize_span_attributes(
                span.attributes,
                max_length=self._max_attribute_length,
            ),
            events=(),
            links=(),
            kind=span.kind,
            instrumentation_scope=span.instrumentation_scope,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
        )


class TelemetryRuntime:
    def __init__(self, provider: TracerProvider | None, tracer: Tracer) -> None:
        self._provider = provider
        self._tracer = tracer
        self.enabled = provider is not None

    def start_span(self, name: str, **kwargs: Any) -> AbstractContextManager[Span]:
        return cast(
            AbstractContextManager[Span],
            self._tracer.start_as_current_span(name, **kwargs),
        )

    def inject_context(self, carrier: dict[str, str]) -> None:
        inject(carrier)

    def extract_context(self, carrier: dict[str, str]) -> object:
        return extract(carrier)

    def force_flush(self, timeout_millis: int = 5_000) -> bool:
        if self._provider is None:
            return True
        try:
            return bool(self._provider.force_flush(timeout_millis))
        except Exception:
            return True


def _headers_from_file(path: str) -> dict[str, str] | None:
    if not path:
        return None
    header_path = Path(path)
    try:
        raw = header_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ImproperlyConfigured("Telemetry authorization file is unavailable.") from exc
    if not raw or "\n" in raw or "\r" in raw or "=" not in raw:
        raise ImproperlyConfigured("Telemetry authorization file is invalid.")
    name, value = raw.split("=", 1)
    if name.strip().lower() != "authorization" or not value.strip():
        raise ImproperlyConfigured("Telemetry authorization file is invalid.")
    return {"authorization": value.strip()}


def build_runtime(
    config: TelemetryConfig,
    *,
    exporter: Exporter | None = None,
) -> TelemetryRuntime:
    if not config.enabled:
        provider = trace.NoOpTracerProvider()
        return TelemetryRuntime(None, provider.get_tracer("civicloop"))
    if not 32 <= config.max_attribute_length <= 1_024:
        raise ImproperlyConfigured("Telemetry attribute limit must be between 32 and 1024.")
    if not 1 <= config.max_export_batch_size <= config.max_queue_size <= 2_048:
        raise ImproperlyConfigured("Telemetry queue and batch limits are invalid.")
    if exporter is None:
        if not config.endpoint.startswith(("http://", "https://")):
            raise ImproperlyConfigured("Enabled telemetry requires an HTTP(S) OTLP endpoint.")
        exporter = OTLPSpanExporter(
            endpoint=config.endpoint,
            headers=_headers_from_file(config.headers_file),
            timeout=config.export_timeout_millis / 1_000,
        )
    safe_exporter = RedactingSpanExporter(
        exporter,
        max_attribute_length=config.max_attribute_length,
    )
    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: config.service_name,
                "deployment.environment.name": config.environment,
            }
        )
    )
    if config.synchronous:
        provider.add_span_processor(SimpleSpanProcessor(safe_exporter))
    else:
        provider.add_span_processor(
            BatchSpanProcessor(
                safe_exporter,
                max_queue_size=config.max_queue_size,
                max_export_batch_size=config.max_export_batch_size,
                schedule_delay_millis=config.schedule_delay_millis,
                export_timeout_millis=config.export_timeout_millis,
            )
        )
    return TelemetryRuntime(provider, provider.get_tracer("civicloop", "1.0"))


_runtime: TelemetryRuntime | None = None
_runtime_lock = Lock()


def configure_from_django_settings() -> TelemetryRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = build_runtime(
                TelemetryConfig(
                    enabled=bool(settings.CIVICLOOP_TELEMETRY_ENABLED),
                    endpoint=str(settings.CIVICLOOP_TELEMETRY_ENDPOINT),
                    headers_file=str(settings.CIVICLOOP_TELEMETRY_HEADERS_FILE),
                    service_name=str(settings.CIVICLOOP_TELEMETRY_SERVICE_NAME),
                    environment=str(settings.ENVIRONMENT),
                )
            )
        return _runtime


def get_runtime() -> TelemetryRuntime:
    return configure_from_django_settings()


def set_runtime_for_testing(runtime: TelemetryRuntime | None) -> None:
    global _runtime
    with _runtime_lock:
        _runtime = runtime
