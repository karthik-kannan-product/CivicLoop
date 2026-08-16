from datetime import timedelta
from uuid import UUID

import pytest
from django.utils import timezone
from integrations.exceptions import SecretUnavailable
from integrations.providers import ProbeResponseShape, SafeProbeError
from integrations.transport import MAX_RESPONSE_BYTES, BoundedHTTPSProbeTransport
from integrations.types import SecretLease, SecretReference

CREDENTIAL = b"synthetic-credential"


def credential_lease(value: bytes = CREDENTIAL) -> SecretLease:
    return SecretLease(
        reference=SecretReference(
            id=UUID("11cf05c1-90fe-4d0d-a458-eadbe7d448aa"),
            provider="eventbrite",
            scope="connection_test",
            version=1,
        ),
        caller_id=UUID("56077722-0ef6-4d6a-9c8c-512d2c914cad"),
        workflow_id=None,
        purpose="connection_test",
        expires_at=timezone.now() + timedelta(seconds=30),
        _plaintext=bytearray(value),
    )


class Response:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, count: int) -> bytes:
        return self.body[:count]


def test_transport_rejects_unapproved_urls_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.transport.build_opener",
        lambda *_args: pytest.fail("transport must not connect to an unapproved URL"),
    )

    with pytest.raises(SafeProbeError) as raised:
        BoundedHTTPSProbeTransport().get(
            "https://example.test/probe", credential=credential_lease()
        )

    assert raised.value.category == "network"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/models?unexpected=1",
        "https://api.openai.com:444/v1/models",
        "https://api.openai.com/v1/files",
    ],
)
def test_transport_rejects_non_probe_paths_queries_and_ports(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(
        "integrations.transport.build_opener",
        lambda *_args: pytest.fail("transport must not connect to a non-probe URL"),
    )

    with pytest.raises(SafeProbeError) as raised:
        BoundedHTTPSProbeTransport().get(url, credential=credential_lease())

    assert raised.value.category == "network"


def test_transport_bounds_response_bytes_and_classifies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Opener:
        def open(self, *args: object, **kwargs: object):
            raise TimeoutError

    monkeypatch.setattr("integrations.transport.build_opener", lambda *_args: Opener())
    lease = credential_lease()
    with pytest.raises(SafeProbeError) as raised:
        BoundedHTTPSProbeTransport().get(
            "https://api.openai.com/v1/models", credential=lease
        )
    assert raised.value.category == "timeout"
    with pytest.raises(SecretUnavailable):
        lease.use(lambda _credential: None)

    monkeypatch.setattr(
        "integrations.transport.build_opener", lambda *_args: StaticOpener(
            Response(b"x" * (MAX_RESPONSE_BYTES + 1))
        )
    )
    with pytest.raises(SafeProbeError) as oversized:
        BoundedHTTPSProbeTransport().get(
            "https://api.openai.com/v1/models", credential=credential_lease()
        )
    assert oversized.value.category == "invalid_response"


class StaticOpener:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.method = ""
        self.url = ""
        self.timeout: int | None = None
        self.headers: dict[str, str] = {}
        self.request: object | None = None
        self.calls = 0

    def open(self, request: object, *, timeout: int) -> Response:
        self.calls += 1
        self.request = request
        self.method = request.get_method()
        self.url = request.full_url
        self.timeout = timeout
        self.headers = {name.lower(): value for name, value in request.header_items()}
        return self.response


@pytest.mark.parametrize(
    ("url", "body", "expected_header", "expected_shape"),
    [
        (
            "https://www.eventbriteapi.com/v3/users/me/",
            b'{"id":"synthetic-user","echo":"synthetic-credential"}',
            ("authorization", "Bearer synthetic-credential"),
            ProbeResponseShape.EVENTBRITE_IDENTITY,
        ),
        (
            "https://api.iterable.com/api/lists",
            b'{"lists":[],"echo":"synthetic-credential"}',
            ("api-key", "synthetic-credential"),
            ProbeResponseShape.ITERABLE_LISTS,
        ),
        (
            "https://api.eu.iterable.com/api/lists",
            b'{"lists":[],"echo":"synthetic-credential"}',
            ("api-key", "synthetic-credential"),
            ProbeResponseShape.ITERABLE_LISTS,
        ),
        (
            "https://api.openai.com/v1/models",
            b'{"object":"list","data":[],"echo":"synthetic-credential"}',
            ("authorization", "Bearer synthetic-credential"),
            ProbeResponseShape.MODEL_LIST,
        ),
        (
            "https://api.groq.com/openai/v1/models",
            b'{"object":"list","data":[],"echo":"synthetic-credential"}',
            ("authorization", "Bearer synthetic-credential"),
            ProbeResponseShape.MODEL_LIST,
        ),
    ],
)
def test_transport_uses_one_exact_get_and_returns_only_minimal_parsed_shape(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    body: bytes,
    expected_header: tuple[str, str],
    expected_shape: ProbeResponseShape,
) -> None:
    opener = StaticOpener(Response(body))
    monkeypatch.setattr("integrations.transport.build_opener", lambda *_handlers: opener)
    lease = credential_lease()

    result = BoundedHTTPSProbeTransport().get(url, credential=lease)

    assert result.status == 200
    assert result.shape is expected_shape
    assert not hasattr(result, "body")
    assert not hasattr(result, "headers")
    assert not hasattr(result, "url")
    assert CREDENTIAL.decode("ascii") not in repr(result)
    assert opener.calls == 1
    assert opener.method == "GET"
    assert opener.url == url
    assert opener.timeout == 5
    assert opener.headers == {expected_header[0]: expected_header[1]}
    assert opener.request is not None
    assert opener.request.header_items() == []
    with pytest.raises(SecretUnavailable):
        lease.use(lambda _credential: None)


def test_transport_reduces_malformed_json_to_an_empty_safe_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "integrations.transport.build_opener", lambda *_handlers: StaticOpener(Response(b"no-json"))
    )

    result = BoundedHTTPSProbeTransport().get(
        "https://api.openai.com/v1/models", credential=credential_lease()
    )

    assert result.status == 200
    assert result.shape is None
