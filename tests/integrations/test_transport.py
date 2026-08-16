import pytest
from integrations.providers import SafeProbeError
from integrations.transport import MAX_RESPONSE_BYTES, BoundedHTTPSProbeTransport


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
        BoundedHTTPSProbeTransport().get("https://example.test/probe", headers={})

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
        BoundedHTTPSProbeTransport().get(url, headers={})

    assert raised.value.category == "network"


def test_transport_bounds_response_bytes_and_classifies_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Opener:
        def open(self, *args: object, **kwargs: object):
            raise TimeoutError

    monkeypatch.setattr("integrations.transport.build_opener", lambda *_args: Opener())
    with pytest.raises(SafeProbeError) as raised:
        BoundedHTTPSProbeTransport().get("https://api.openai.com/v1/models", headers={})
    assert raised.value.category == "timeout"

    with pytest.raises(SafeProbeError) as oversized:
        BoundedHTTPSProbeTransport._read(Response(b"x" * (MAX_RESPONSE_BYTES + 1)))
    assert oversized.value.category == "invalid_response"
