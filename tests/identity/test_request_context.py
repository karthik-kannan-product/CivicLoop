import pytest
from django.test import RequestFactory, override_settings
from identity.request_context import source_ip


def request(*, remote_addr: str, forwarded_for: str | None = None):
    metadata = {"REMOTE_ADDR": remote_addr}
    if forwarded_for is not None:
        metadata["HTTP_X_FORWARDED_FOR"] = forwarded_for
    return RequestFactory().get("/api/v1/admin/security/status", **metadata)


def test_uses_direct_peer_and_ignores_spoofed_forwarding_header() -> None:
    incoming = request(remote_addr="198.51.100.10", forwarded_for="192.0.2.25")

    assert source_ip(incoming) == "198.51.100.10"


@override_settings(ADMIN_TRUSTED_PROXY_IPS=frozenset({"127.0.0.1", "::1"}))
@pytest.mark.parametrize(
    ("remote_addr", "forwarded_for", "expected"),
    [
        ("127.0.0.1", "192.0.2.25", "192.0.2.25"),
        ("::1", "2001:db8::25", "2001:db8::25"),
    ],
)
def test_trusts_one_canonical_ip_from_an_explicit_proxy(
    remote_addr: str,
    forwarded_for: str,
    expected: str,
) -> None:
    assert source_ip(request(remote_addr=remote_addr, forwarded_for=forwarded_for)) == expected


@override_settings(ADMIN_TRUSTED_PROXY_IPS=frozenset({"127.0.0.1"}))
@pytest.mark.parametrize(
    "forwarded_for",
    ["", "192.0.2.1, 192.0.2.2", "not-an-ip", "192.0.2.1\nspoofed"],
)
def test_rejects_missing_ambiguous_or_invalid_proxy_value(forwarded_for: str) -> None:
    assert source_ip(request(remote_addr="127.0.0.1", forwarded_for=forwarded_for)) is None


@pytest.mark.parametrize("remote_addr", ["", "not-an-ip", "192.0.2.1,192.0.2.2"])
def test_invalid_direct_peer_returns_no_source_ip(remote_addr: str) -> None:
    assert source_ip(request(remote_addr=remote_addr)) is None
