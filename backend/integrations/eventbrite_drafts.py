"""Server-only, draft-only Eventbrite writes; callers enforce durable approval.

Contract: https://www.eventbrite.com/platform/new/api#event
Creation and update use POST; publishing is a separate, prohibited endpoint.
Eventbrite documents eventual consistency, so successful POST without matching
GET readback is UNKNOWN, never permission to repeat a create. No provider
idempotency header is assumed. The create field minimum is our conservative
local contract; a credential-backed smoke must confirm account acceptance.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from integrations.types import SecretLease

BASE_URL = "https://www.eventbriteapi.com/v3"
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REQUEST_BYTES = 32 * 1024
_ID = re.compile(r"[1-9][0-9]{0,39}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"name", "summary", "start", "end", "online_event", "venue_id", "listed", "shareable"}


class EventbriteDraftError(Exception):
    """Safe category only: never retain provider bodies or exception text."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class EventbriteDraftReceipt:
    outcome: str
    provider_id: str | None
    provider_status: str | None
    request_digest: str
    readback_digest: str | None = None
    error_category: str | None = None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise EventbriteDraftError("invalid_request")
    return value


def validate_payload(payload: object, *, create: bool) -> dict[str, Any]:
    """Validate and copy a bounded JSON payload before any credential is used."""
    if not isinstance(payload, dict) or set(payload) != {"event"}:
        raise EventbriteDraftError("invalid_request")
    event = payload["event"]
    allowed = _FIELDS | ({"currency"} if create else set())
    if not isinstance(event, dict) or not event or set(event) - allowed:
        raise EventbriteDraftError("invalid_request")
    if create and not {"name", "start", "end", "currency"} <= set(event):
        raise EventbriteDraftError("invalid_request")
    if "name" in event:
        name = event["name"]
        if (
            not isinstance(name, dict)
            or set(name) != {"html"}
            or not isinstance(name["html"], str)
            or not name["html"].strip()
            or len(name["html"]) > 200
        ):
            raise EventbriteDraftError("invalid_request")
    if "summary" in event and (
        not isinstance(event["summary"], str) or len(event["summary"]) > 140
    ):
        raise EventbriteDraftError("invalid_request")
    dates = {}
    for field in ("start", "end"):
        if field not in event:
            continue
        value = event[field]
        if not isinstance(value, dict) or set(value) != {"utc", "timezone"}:
            raise EventbriteDraftError("invalid_request")
        if not all(isinstance(v, str) for v in value.values()):
            raise EventbriteDraftError("invalid_request")
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value["utc"]):
                raise ValueError
            dates[field] = datetime.strptime(value["utc"], "%Y-%m-%dT%H:%M:%SZ")
            ZoneInfo(value["timezone"])
        except ValueError, ZoneInfoNotFoundError:
            raise EventbriteDraftError("invalid_request") from None
    if bool("start" in event) != bool("end" in event):
        raise EventbriteDraftError("invalid_request")
    if dates and dates["start"] >= dates["end"]:
        raise EventbriteDraftError("invalid_request")
    if "currency" in event and (
        not isinstance(event["currency"], str) or not re.fullmatch(r"[A-Z]{3}", event["currency"])
    ):
        raise EventbriteDraftError("invalid_request")
    if "online_event" in event and type(event["online_event"]) is not bool:
        raise EventbriteDraftError("invalid_request")
    for field in ("listed", "shareable"):
        if field in event and event[field] is not False:
            raise EventbriteDraftError("invalid_request")
    if "venue_id" in event:
        _identifier(event["venue_id"])
        if event.get("online_event") is True:
            raise EventbriteDraftError("invalid_request")
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_REQUEST_BYTES:
        raise EventbriteDraftError("invalid_request")
    result = json.loads(encoded)
    if create:
        result["event"].setdefault("listed", False)
        result["event"].setdefault("shareable", False)
    return result


def compute_request_digest(
    action: str, organization_id: str, event_id: str | None, payload: object
) -> str:
    if action not in {"create", "update"}:
        raise EventbriteDraftError("invalid_request")
    _identifier(organization_id)
    if action == "update":
        _identifier(event_id)
    elif event_id is not None:
        raise EventbriteDraftError("invalid_request")
    return _digest(
        {
            "action": action,
            "organization_id": organization_id,
            "event_id": event_id,
            "payload": validate_payload(payload, create=action == "create"),
        }
    )


def readback_digest(event: dict[str, Any]) -> str:
    """Fingerprint revision plus safe event fields; unrelated expansions excluded."""
    return _digest(
        {
            key: event.get(key)
            for key in sorted(
                _FIELDS | {"id", "organization_id", "status", "published", "changed", "currency"}
            )
        }
    )


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class EventbriteDraftAdapter:
    def _request(
        self, method: str, path: str, token: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not (
            method in {"GET", "POST"}
            and re.fullmatch(r"/events/[1-9][0-9]{0,39}/", path)
            or method == "POST"
            and re.fullmatch(r"/organizations/[1-9][0-9]{0,39}/events/", path)
        ):
            raise EventbriteDraftError("invalid_request")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        request = Request(
            BASE_URL + path,
            method=method,
            headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None,
        )
        # Do not inherit a machine proxy which could receive the authorization header.
        opener = build_opener(ProxyHandler({}), _NoRedirects())
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise EventbriteDraftError("invalid_response")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                    raise EventbriteDraftError("invalid_response")
                try:
                    result = json.loads(raw)
                except ValueError, UnicodeDecodeError, RecursionError:
                    raise EventbriteDraftError("invalid_response") from None
                if not isinstance(result, dict):
                    raise EventbriteDraftError("invalid_response")
                return result
        except HTTPError as exc:
            category = {
                400: "provider_validation",
                401: "authentication",
                403: "forbidden",
                404: "not_found",
                429: "rate_limited",
            }.get(exc.code, "provider_error")
            if 300 <= exc.code < 400:
                category = "redirect"
            exc.close()
            raise EventbriteDraftError(category) from None
        except TimeoutError:
            raise EventbriteDraftError("timeout") from None
        except HTTPException, OSError, URLError:
            raise EventbriteDraftError("network") from None
        finally:
            headers.clear()
            request.headers.clear()
            request.unredirected_hdrs.clear()

    @staticmethod
    def _token(
        credential: SecretLease, operation: Callable[[str], EventbriteDraftReceipt]
    ) -> EventbriteDraftReceipt:
        if (
            not isinstance(credential, SecretLease)
            or credential.reference.provider != "eventbrite"
            or credential.purpose != "eventbrite_draft_write"
            or not isinstance(credential.workflow_id, UUID)
        ):
            raise EventbriteDraftError("invalid_credential")

        def use(view: memoryview) -> EventbriteDraftReceipt:
            try:
                token = str(view, "utf-8")
            except UnicodeDecodeError:
                raise EventbriteDraftError("invalid_credential") from None
            if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise EventbriteDraftError("invalid_credential")
            try:
                return operation(token)
            finally:
                token = ""

        return credential.use(use)

    @staticmethod
    def _draft(event: dict[str, Any], organization_id: str, event_id: str | None) -> str:
        provider_id = _identifier(event.get("id"))
        if (
            event_id is not None
            and provider_id != event_id
            or event.get("organization_id") != organization_id
        ):
            raise EventbriteDraftError("identity_mismatch")
        if (
            event.get("status") != "draft"
            or "published" not in event
            or event.get("published") is not None
            or event.get("is_series") is True
            or event.get("is_series_parent") is True
            or event.get("series_id") is not None
        ):
            raise EventbriteDraftError("unsafe_provider_state")
        return provider_id

    def _confirm(
        self, token: str, organization_id: str, event_id: str, payload: dict[str, Any], digest: str
    ) -> EventbriteDraftReceipt:
        try:
            event = self._request("GET", f"/events/{event_id}/", token)
            self._draft(event, organization_id, event_id)
            for key, expected in payload["event"].items():
                actual = event.get(key)
                if isinstance(expected, dict) and isinstance(actual, dict):
                    actual = {k: actual.get(k) for k in expected}
                if actual != expected:
                    raise EventbriteDraftError("readback_mismatch")
            return EventbriteDraftReceipt(
                "CONFIRMED", event_id, "draft", digest, readback_digest(event)
            )
        except EventbriteDraftError as exc:
            return EventbriteDraftReceipt(
                "UNKNOWN", event_id, None, digest, error_category=exc.category
            )

    def create(
        self, credential: SecretLease, *, organization_id: str, payload: object
    ) -> EventbriteDraftReceipt:
        validated = validate_payload(payload, create=True)
        digest = compute_request_digest("create", organization_id, None, validated)

        def execute(token: str) -> EventbriteDraftReceipt:
            event_id = None
            try:
                event = self._request(
                    "POST", f"/organizations/{organization_id}/events/", token, validated
                )
                event_id = _identifier(event.get("id"))
                self._draft(event, organization_id, event_id)
            except EventbriteDraftError as exc:
                if exc.category in {
                    "provider_validation",
                    "authentication",
                    "forbidden",
                    "not_found",
                    "rate_limited",
                    "redirect",
                }:
                    raise
                return EventbriteDraftReceipt(
                    "UNKNOWN", event_id, None, digest, error_category=exc.category
                )
            return self._confirm(token, organization_id, event_id, validated, digest)

        return self._token(credential, execute)

    def update(
        self,
        credential: SecretLease,
        *,
        organization_id: str,
        event_id: str,
        payload: object,
        expected_readback_digest: str,
    ) -> EventbriteDraftReceipt:
        validated = validate_payload(payload, create=False)
        digest = compute_request_digest("update", organization_id, event_id, validated)
        if not isinstance(expected_readback_digest, str) or not _DIGEST.fullmatch(
            expected_readback_digest
        ):
            raise EventbriteDraftError("invalid_request")

        def execute(token: str) -> EventbriteDraftReceipt:
            before = self._request("GET", f"/events/{event_id}/", token)
            self._draft(before, organization_id, event_id)
            if readback_digest(before) != expected_readback_digest:
                raise EventbriteDraftError("stale_revision")
            try:
                event = self._request("POST", f"/events/{event_id}/", token, validated)
                self._draft(event, organization_id, event_id)
            except EventbriteDraftError as exc:
                if exc.category in {
                    "provider_validation",
                    "authentication",
                    "forbidden",
                    "not_found",
                    "rate_limited",
                    "redirect",
                }:
                    raise
                return EventbriteDraftReceipt(
                    "UNKNOWN", event_id, None, digest, error_category=exc.category
                )
            return self._confirm(token, organization_id, event_id, validated, digest)

        return self._token(credential, execute)

    def reconcile(
        self,
        credential: SecretLease,
        *,
        organization_id: str,
        event_id: str | None,
        payload: object,
        request_digest: str,
        action: str = "create",
    ) -> EventbriteDraftReceipt:
        validated = validate_payload(payload, create=action == "create")
        expected = compute_request_digest(
            action, organization_id, event_id if action == "update" else None, validated
        )
        if expected != request_digest:
            raise EventbriteDraftError("invalid_request")
        if event_id is None:
            return EventbriteDraftReceipt(
                "UNKNOWN", None, None, expected, error_category="human_reconciliation_required"
            )
        _identifier(event_id)
        return self._token(
            credential,
            lambda token: self._confirm(token, organization_id, event_id, validated, expected),
        )


request_digest = compute_request_digest
