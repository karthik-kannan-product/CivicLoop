from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .runtime import get_runtime


class TelemetryMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self._get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        runtime = get_runtime()
        carrier: dict[str, str] = {}
        incoming_traceparent = request.headers.get("traceparent", "")
        if incoming_traceparent:
            carrier["traceparent"] = incoming_traceparent
        parent_context = runtime.extract_context(carrier)
        with runtime.start_span(
            "civicloop.http.request",
            context=parent_context,
            attributes={"http.request.method": request.method},
        ) as span:
            response = self._get_response(request)
            span.set_attribute("http.response.status_code", response.status_code)
            response_carrier: dict[str, str] = {}
            runtime.inject_context(response_carrier)
            if traceparent := response_carrier.get("traceparent"):
                response["traceparent"] = traceparent
            return response
