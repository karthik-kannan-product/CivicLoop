"""Bounded, read-only provider connection probes.

Probe implementations deliberately expose only a boolean outcome and a fixed error
category.  They never retain credentials or provider response bodies.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from integrations.types import SecretLease

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


class ProbeResponseShape(Enum):
    EVENTBRITE_IDENTITY = "eventbrite_identity"
    ITERABLE_LISTS = "iterable_lists"
    MODEL_LIST = "model_list"


@dataclass(frozen=True, repr=False)
class ProbeResponse:
    status: int
    shape: ProbeResponseShape | None

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
    def get(self, url: str, *, credential: SecretLease) -> ProbeResponse:
        """Use one lease for one bounded HTTPS GET and return only parsed shape metadata."""


class ProviderProbe:
    def __init__(self, transport: ProbeTransport) -> None:
        self._transport = transport

    def probe(self, credential: SecretLease, *, configuration: dict[str, str]) -> ProbeResult:
        if not isinstance(credential, SecretLease):
            return ProbeResult(ok=False, error_category="invalid_response")
        try:
            url = self._url(configuration)
            response = self._transport.get(url, credential=credential)
        except SafeProbeError as exc:
            return ProbeResult(ok=False, error_category=exc.category)
        except (TypeError, UnicodeError, ValueError):
            return ProbeResult(ok=False, error_category="invalid_response")

        category = _status_category(response.status)
        if category is not None:
            return ProbeResult(ok=False, error_category=category)
        if response.status != 200 or response.shape is not self._expected_shape:
            return ProbeResult(ok=False, error_category="invalid_response")
        return ProbeResult(ok=True)

    @property
    def _expected_shape(self) -> ProbeResponseShape:
        raise NotImplementedError

    def _url(self, configuration: dict[str, str]) -> str:
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


class EventbriteProbe(ProviderProbe):
    @property
    def _expected_shape(self) -> ProbeResponseShape:
        return ProbeResponseShape.EVENTBRITE_IDENTITY

    def _url(self, configuration: dict[str, str]) -> str:
        if configuration != {}:
            raise ValueError("invalid configuration")
        return "https://www.eventbriteapi.com/v3/users/me/"


class IterableProbe(ProviderProbe):
    _URLS = {
        "us": "https://api.iterable.com/api/lists",
        "eu": "https://api.eu.iterable.com/api/lists",
    }

    @property
    def _expected_shape(self) -> ProbeResponseShape:
        return ProbeResponseShape.ITERABLE_LISTS

    def _url(self, configuration: dict[str, str]) -> str:
        region = configuration.get("region")
        if set(configuration) != {"region"} or region not in self._URLS:
            raise ValueError("invalid configuration")
        return self._URLS[region]


class _ModelsProbe(ProviderProbe):
    _URL = ""

    @property
    def _expected_shape(self) -> ProbeResponseShape:
        return ProbeResponseShape.MODEL_LIST

    def _url(self, configuration: dict[str, str]) -> str:
        if configuration != {"model": "openai/gpt-oss-20b"}:
            raise ValueError("invalid configuration")
        return self._URL


class OpenAIProbe(_ModelsProbe):
    _URL = "https://api.openai.com/v1/models"


class GroqProbe(_ModelsProbe):
    _URL = "https://api.groq.com/openai/v1/models"
