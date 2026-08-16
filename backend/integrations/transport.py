"""A deliberately small HTTPS-only transport for harmless provider probes."""

from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from integrations.providers import ProbeResponse, SafeProbeError

CONNECT_TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 64 * 1024
ALLOWED_PROBE_URLS = frozenset(
    {
        "https://www.eventbriteapi.com/v3/users/me/",
        "https://api.iterable.com/api/lists",
        "https://api.eu.iterable.com/api/lists",
        "https://api.openai.com/v1/models",
        "https://api.groq.com/openai/v1/models",
    }
)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class BoundedHTTPSProbeTransport:
    def get(self, url: str, *, headers: dict[str, str]) -> ProbeResponse:
        if url not in ALLOWED_PROBE_URLS:
            raise SafeProbeError("network")
        request = Request(url, headers=headers, method="GET")
        opener = build_opener(_NoRedirects())
        try:
            with opener.open(request, timeout=CONNECT_TIMEOUT_SECONDS) as response:
                return ProbeResponse(status=response.status, body=self._read(response))
        except HTTPError as exc:
            return ProbeResponse(status=exc.code, body=self._read(exc))
        except TimeoutError:
            raise SafeProbeError("timeout") from None
        except (OSError, URLError):
            raise SafeProbeError("network") from None

    @staticmethod
    def _read(response: object) -> bytes:
        read = getattr(response, "read", None)
        if not callable(read):
            raise SafeProbeError("invalid_response")
        body = read(MAX_RESPONSE_BYTES + 1)
        if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
            raise SafeProbeError("invalid_response")
        return body
