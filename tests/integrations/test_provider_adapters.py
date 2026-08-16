from datetime import timedelta
from uuid import UUID

import pytest
from django.utils import timezone
from integrations.exceptions import SecretUnavailable
from integrations.providers import (
    EventbriteProbe,
    GroqProbe,
    IterableProbe,
    OpenAIProbe,
    ProbeResponse,
    ProbeResponseShape,
    SafeProbeError,
)
from integrations.types import SecretLease, SecretReference

CREDENTIAL = b"synthetic-credential"
CALLER_ID = UUID("56077722-0ef6-4d6a-9c8c-512d2c914cad")


def credential_lease() -> SecretLease:
    return SecretLease(
        reference=SecretReference(
            id=UUID("11cf05c1-90fe-4d0d-a458-eadbe7d448aa"),
            provider="eventbrite",
            scope="connection_test",
            version=1,
        ),
        caller_id=CALLER_ID,
        workflow_id=None,
        purpose="connection_test",
        expires_at=timezone.now() + timedelta(seconds=30),
        _plaintext=bytearray(CREDENTIAL),
    )


class RecordingTransport:
    def __init__(self, response: ProbeResponse) -> None:
        self.response = response
        self.calls: list[str] = []
        self.credential_was_scoped = False
        self.retained_view: memoryview | None = None

    def get(self, url: str, *, credential: SecretLease) -> ProbeResponse:
        self.calls.append(url)
        assert not hasattr(credential, "read")

        def respond(scoped_credential: memoryview) -> ProbeResponse:
            self.credential_was_scoped = scoped_credential == CREDENTIAL
            self.retained_view = scoped_credential
            return self.response

        return credential.use(respond)


@pytest.mark.parametrize(
    ("probe_type", "configuration", "shape", "url"),
    [
        (
            EventbriteProbe,
            {},
            ProbeResponseShape.EVENTBRITE_IDENTITY,
            "https://www.eventbriteapi.com/v3/users/me/",
        ),
        (
            IterableProbe,
            {"region": "us"},
            ProbeResponseShape.ITERABLE_LISTS,
            "https://api.iterable.com/api/lists",
        ),
        (
            IterableProbe,
            {"region": "eu"},
            ProbeResponseShape.ITERABLE_LISTS,
            "https://api.eu.iterable.com/api/lists",
        ),
        (
            OpenAIProbe,
            {"model": "openai/gpt-oss-20b"},
            ProbeResponseShape.MODEL_LIST,
            "https://api.openai.com/v1/models",
        ),
        (
            GroqProbe,
            {"model": "openai/gpt-oss-20b"},
            ProbeResponseShape.MODEL_LIST,
            "https://api.groq.com/openai/v1/models",
        ),
    ],
)
def test_provider_probes_use_only_documented_safe_get_requests(
    probe_type: type[EventbriteProbe | IterableProbe | OpenAIProbe | GroqProbe],
    configuration: dict[str, str],
    shape: ProbeResponseShape,
    url: str,
) -> None:
    transport = RecordingTransport(ProbeResponse(status=200, shape=shape))
    lease = credential_lease()

    result = probe_type(transport).probe(lease, configuration=configuration)

    assert result.ok is True
    assert result.error_category is None
    assert transport.calls == [url]
    assert transport.credential_was_scoped is True
    assert transport.retained_view is not None
    with pytest.raises(ValueError):
        transport.retained_view.tobytes()
    with pytest.raises(SecretUnavailable):
        lease.use(lambda _credential: None)


@pytest.mark.parametrize(
    "status, expected_category",
    [
        (401, "authentication"),
        (403, "authorization"),
        (429, "rate_limit"),
        (500, "provider_unavailable"),
    ],
)
def test_provider_probe_redacts_status_failures(status: int, expected_category: str) -> None:
    transport = RecordingTransport(ProbeResponse(status=status, shape=None))

    result = EventbriteProbe(transport).probe(credential_lease(), configuration={})

    assert result.ok is False
    assert result.error_category == expected_category
    assert "credential" not in repr(result)
    assert "synthetic-credential" not in repr(result)


@pytest.mark.parametrize(
    ("probe_type", "configuration", "shape"),
    [
        (EventbriteProbe, {}, None),
        (IterableProbe, {"region": "us"}, ProbeResponseShape.EVENTBRITE_IDENTITY),
        (
            OpenAIProbe,
            {"model": "openai/gpt-oss-20b"},
            ProbeResponseShape.ITERABLE_LISTS,
        ),
        (GroqProbe, {"model": "openai/gpt-oss-20b"}, None),
    ],
)
def test_provider_probe_rejects_unexpected_parsed_response_shapes(
    probe_type: type[EventbriteProbe | IterableProbe | OpenAIProbe | GroqProbe],
    configuration: dict[str, str],
    shape: ProbeResponseShape | None,
) -> None:
    transport = RecordingTransport(ProbeResponse(status=200, shape=shape))

    result = probe_type(transport).probe(credential_lease(), configuration=configuration)

    assert result.ok is False
    assert result.error_category == "invalid_response"


def test_probe_response_cannot_carry_raw_transport_material() -> None:
    response = ProbeResponse(status=200, shape=ProbeResponseShape.EVENTBRITE_IDENTITY)

    assert not hasattr(response, "body")
    assert not hasattr(response, "headers")
    assert not hasattr(response, "url")
    assert CREDENTIAL.decode("ascii") not in repr(response)


def test_provider_probe_translates_transport_failures_without_leaking_exception_text() -> None:
    class FailingTransport:
        def get(self, url: str, *, credential: SecretLease) -> ProbeResponse:
            def fail(_scoped_credential: memoryview) -> ProbeResponse:
                raise SafeProbeError("network")

            return credential.use(fail)

    lease = credential_lease()
    result = EventbriteProbe(FailingTransport()).probe(lease, configuration={})

    assert result.ok is False
    assert result.error_category == "network"
    assert CREDENTIAL.decode("ascii") not in repr(result)
    with pytest.raises(SecretUnavailable):
        lease.use(lambda _credential: None)
