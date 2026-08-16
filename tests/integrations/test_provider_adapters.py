import json

import pytest

from integrations.providers import (
    EventbriteProbe,
    GroqProbe,
    IterableProbe,
    OpenAIProbe,
    ProbeResponse,
    SafeProbeError,
)


class RecordingTransport:
    def __init__(self, response: ProbeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers: dict[str, str]) -> ProbeResponse:
        self.calls.append((url, headers))
        return self.response


@pytest.mark.parametrize(
    ("probe_type", "configuration", "response", "url", "headers"),
    [
        (
            EventbriteProbe,
            {},
            {"id": "synthetic-user"},
            "https://www.eventbriteapi.com/v3/users/me/",
            {"Authorization": "Bearer synthetic-credential"},
        ),
        (
            IterableProbe,
            {"region": "us"},
            {"lists": []},
            "https://api.iterable.com/api/lists",
            {"Api-Key": "synthetic-credential"},
        ),
        (
            IterableProbe,
            {"region": "eu"},
            {"lists": []},
            "https://api.eu.iterable.com/api/lists",
            {"Api-Key": "synthetic-credential"},
        ),
        (
            OpenAIProbe,
            {"model": "openai/gpt-oss-20b"},
            {"object": "list", "data": []},
            "https://api.openai.com/v1/models",
            {"Authorization": "Bearer synthetic-credential"},
        ),
        (
            GroqProbe,
            {"model": "openai/gpt-oss-20b"},
            {"object": "list", "data": []},
            "https://api.groq.com/openai/v1/models",
            {"Authorization": "Bearer synthetic-credential"},
        ),
    ],
)
def test_provider_probes_use_only_documented_safe_get_requests(
    probe_type: type[object],
    configuration: dict[str, str],
    response: dict[str, object],
    url: str,
    headers: dict[str, str],
) -> None:
    transport = RecordingTransport(ProbeResponse(status=200, body=json.dumps(response).encode()))

    result = probe_type(transport).probe(b"synthetic-credential", configuration=configuration)

    assert result.ok is True
    assert result.error_category is None
    assert transport.calls == [(url, headers)]


@pytest.mark.parametrize("status, expected_category", [(401, "authentication"), (403, "authorization"), (429, "rate_limit"), (500, "provider_unavailable")])
def test_provider_probe_redacts_status_failures(status: int, expected_category: str) -> None:
    transport = RecordingTransport(ProbeResponse(status=status, body=b'{"credential":"do-not-return"}'))

    result = EventbriteProbe(transport).probe(b"synthetic-credential", configuration={})

    assert result.ok is False
    assert result.error_category == expected_category
    assert "credential" not in repr(result)
    assert "synthetic-credential" not in repr(result)


@pytest.mark.parametrize(
    ("probe_type", "configuration", "body"),
    [
        (EventbriteProbe, {}, b"{}"),
        (IterableProbe, {"region": "us"}, b'{"lists": {}}'),
        (OpenAIProbe, {"model": "openai/gpt-oss-20b"}, b'{"object": "list", "data": {}}'),
        (GroqProbe, {"model": "openai/gpt-oss-20b"}, b"not-json"),
    ],
)
def test_provider_probe_rejects_unexpected_response_shapes(
    probe_type: type[object], configuration: dict[str, str], body: bytes
) -> None:
    transport = RecordingTransport(ProbeResponse(status=200, body=body))

    result = probe_type(transport).probe(b"synthetic-credential", configuration=configuration)

    assert result.ok is False
    assert result.error_category == "invalid_response"


def test_provider_probe_translates_transport_failures_without_leaking_exception_text() -> None:
    class FailingTransport:
        def get(self, url: str, *, headers: dict[str, str]) -> ProbeResponse:
            raise SafeProbeError("network")

    result = EventbriteProbe(FailingTransport()).probe(b"synthetic-credential", configuration={})

    assert result.ok is False
    assert result.error_category == "network"
    assert "synthetic-credential" not in repr(result)
