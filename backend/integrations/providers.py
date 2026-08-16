"""Bounded, read-only provider connection probes.

Probe implementations deliberately expose only a boolean outcome and a fixed error
category.  They never retain credentials or provider response bodies.
"""

import json
from dataclasses import dataclass
from typing import Protocol


SAFE_ERROR_CATEGORIES = frozenset(
    {
        "authentication",
        "authorization",
        "rate_limit",
        "timeout",
        "network",
        "invalid_response",
        "provider_unavailable",
    }
)


@dataclass(frozen=True, repr=False)
class ProbeResponse:
    status: int
    body: bytes

    def __repr__(self) -> str:
        return "ProbeResponse(redacted)"


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    error_category: str | None = None


class SafeProbeError(Exception):
    """A transport failure represented by a schema-approved error category."""

    def __init__(self, category: str) -> None:
        if category not in SAFE_ERROR_CATEGORIES:
            category = "network"
        self.category = category
        super().__init__(category)


class ProbeTransport(Protocol):
    def get(self, url: str, *, headers: dict[str, str]) -> ProbeResponse:
        """Issue one bounded HTTPS GET request without redirects."""


class ProviderProbe:
    def __init__(self, transport: ProbeTransport) -> None:
        self._transport = transport

    def probe(self, credential: bytes, *, configuration: dict[str, str]) -> ProbeResult:
        try:
            url, headers = self._request(credential, configuration)
            response = self._transport.get(url, headers=headers)
        except SafeProbeError as exc:
            return ProbeResult(ok=False, error_category=exc.category)
        except (TypeError, UnicodeError, ValueError):
            return ProbeResult(ok=False, error_category="invalid_response")

        category = _status_category(response.status)
        if category is not None:
            return ProbeResult(ok=False, error_category=category)
        if response.status != 200 or not self._valid_body(response.body):
            return ProbeResult(ok=False, error_category="invalid_response")
        return ProbeResult(ok=True)

    def _request(self, credential: bytes, configuration: dict[str, str]) -> tuple[str, dict[str, str]]:
        raise NotImplementedError

    def _valid_body(self, body: bytes) -> bool:
        raise NotImplementedError


def _status_category(status: int) -> str | None:
    if status == 401:
        return "authentication"
    if status == 403:
        return "authorization"
    if status == 429:
        return "rate_limit"
    if 500 <= status <= 599:
        return "provider_unavailable"
    if 400 <= status <= 499:
        return "invalid_response"
    return None


def _json_object(body: bytes) -> dict[str, object] | None:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _credential_text(credential: bytes) -> str:
    value = credential.decode("utf-8")
    if not value or "\r" in value or "\n" in value:
        raise ValueError("invalid credential")
    return value


class EventbriteProbe(ProviderProbe):
    def _request(self, credential: bytes, configuration: dict[str, str]) -> tuple[str, dict[str, str]]:
        if configuration != {}:
            raise ValueError("invalid configuration")
        return (
            "https://www.eventbriteapi.com/v3/users/me/",
            {"Authorization": f"Bearer {_credential_text(credential)}"},
        )

    def _valid_body(self, body: bytes) -> bool:
        payload = _json_object(body)
        return payload is not None and isinstance(payload.get("id"), str | int) and bool(payload["id"])


class IterableProbe(ProviderProbe):
    _URLS = {
        "us": "https://api.iterable.com/api/lists",
        "eu": "https://api.eu.iterable.com/api/lists",
    }

    def _request(self, credential: bytes, configuration: dict[str, str]) -> tuple[str, dict[str, str]]:
        region = configuration.get("region")
        if set(configuration) != {"region"} or region not in self._URLS:
            raise ValueError("invalid configuration")
        return self._URLS[region], {"Api-Key": _credential_text(credential)}

    def _valid_body(self, body: bytes) -> bool:
        payload = _json_object(body)
        return payload is not None and isinstance(payload.get("lists"), list)


class _ModelsProbe(ProviderProbe):
    _URL = ""

    def _request(self, credential: bytes, configuration: dict[str, str]) -> tuple[str, dict[str, str]]:
        if configuration != {"model": "openai/gpt-oss-20b"}:
            raise ValueError("invalid configuration")
        return self._URL, {"Authorization": f"Bearer {_credential_text(credential)}"}

    def _valid_body(self, body: bytes) -> bool:
        payload = _json_object(body)
        return payload is not None and payload.get("object") == "list" and isinstance(
            payload.get("data"), list
        )


class OpenAIProbe(_ModelsProbe):
    _URL = "https://api.openai.com/v1/models"


class GroqProbe(_ModelsProbe):
    _URL = "https://api.groq.com/openai/v1/models"
