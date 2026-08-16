"""A deliberately small HTTPS-only transport for harmless provider probes."""

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from integrations.providers import ProbeResponse, ProbeResponseShape, SafeProbeError
from integrations.types import SecretLease

CONNECT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 64 * 1024


@dataclass(frozen=True)
class _ProbePolicy:
    header_name: str
    header_prefix: str
    response_shape: ProbeResponseShape


_PROBE_POLICIES = {
    "https://www.eventbriteapi.com/v3/users/me/": _ProbePolicy(
        "Authorization", "Bearer ", ProbeResponseShape.EVENTBRITE_IDENTITY
    ),
    "https://api.iterable.com/api/lists": _ProbePolicy(
        "Api-Key", "", ProbeResponseShape.ITERABLE_LISTS
    ),
    "https://api.eu.iterable.com/api/lists": _ProbePolicy(
        "Api-Key", "", ProbeResponseShape.ITERABLE_LISTS
    ),
    "https://api.openai.com/v1/models": _ProbePolicy(
        "Authorization", "Bearer ", ProbeResponseShape.MODEL_LIST
    ),
    "https://api.groq.com/openai/v1/models": _ProbePolicy(
        "Authorization", "Bearer ", ProbeResponseShape.MODEL_LIST
    ),
}
ALLOWED_PROBE_URLS = frozenset(_PROBE_POLICIES)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class BoundedHTTPSProbeTransport:
    def get(self, url: str, *, credential: SecretLease) -> ProbeResponse:
        policy = _PROBE_POLICIES.get(url)
        if policy is None:
            raise SafeProbeError("network")

        return credential.use(
            lambda scoped_credential: self._get(url, policy, scoped_credential)
        )

    def _get(
        self, url: str, policy: _ProbePolicy, scoped_credential: memoryview
    ) -> ProbeResponse:
        credential_text = str(scoped_credential, "utf-8")
        if not credential_text or "\r" in credential_text or "\n" in credential_text:
            raise ValueError("invalid credential")
        header_value = f"{policy.header_prefix}{credential_text}"
        headers = {policy.header_name: header_value}
        request = Request(url, headers=headers, method="GET")
        opener = build_opener(_NoRedirects())
        try:
            with opener.open(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
                return self._parse(response.status, response, policy.response_shape)
        except HTTPError as exc:
            return self._parse(exc.code, exc, policy.response_shape)
        except TimeoutError:
            raise SafeProbeError("timeout") from None
        except (OSError, URLError):
            raise SafeProbeError("network") from None
        finally:
            # urllib/http.client requires immutable header strings and may make
            # runtime-owned copies that Python cannot zeroize. Remove every
            # application-owned reference as soon as the one call finishes.
            headers.clear()
            request.headers.clear()
            request.unredirected_hdrs.clear()
            credential_text = ""
            header_value = ""

    @staticmethod
    def _parse(
        status: int, response: object, expected_shape: ProbeResponseShape
    ) -> ProbeResponse:
        if not isinstance(status, int):
            raise SafeProbeError("invalid_response")
        body = BoundedHTTPSProbeTransport._read(response)
        try:
            shape = (
                BoundedHTTPSProbeTransport._response_shape(body, expected_shape)
                if status == 200
                else None
            )
            return ProbeResponse(status=status, shape=shape)
        finally:
            body[:] = b"\0" * len(body)

    @staticmethod
    def _read(response: object) -> bytearray:
        read = getattr(response, "read", None)
        if not callable(read):
            raise SafeProbeError("invalid_response")
        raw_body = read(MAX_RESPONSE_BYTES + 1)
        if not isinstance(raw_body, bytes) or len(raw_body) > MAX_RESPONSE_BYTES:
            raise SafeProbeError("invalid_response")
        body = bytearray(raw_body)
        raw_body = b""
        return body

    @staticmethod
    def _response_shape(
        body: bytearray, expected_shape: ProbeResponseShape
    ) -> ProbeResponseShape | None:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return None
        if not isinstance(payload, dict):
            return None
        if expected_shape is ProbeResponseShape.EVENTBRITE_IDENTITY:
            identity = payload.get("id")
            valid = isinstance(identity, str | int) and bool(identity)
        elif expected_shape is ProbeResponseShape.ITERABLE_LISTS:
            valid = isinstance(payload.get("lists"), list)
        else:
            valid = payload.get("object") == "list" and isinstance(payload.get("data"), list)
        return expected_shape if valid else None
