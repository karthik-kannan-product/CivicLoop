from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from integrations.types import SecretLease


@dataclass(frozen=True)
class EventbriteEventMetadata:
    provider_event_id: str
    title: str
    status: str
    changed_at: datetime
    start_at: datetime | None
    end_at: datetime | None
    timezone: str


class EventbriteReader(Protocol):
    def list_events(
        self, credential: SecretLease | None
    ) -> tuple[EventbriteEventMetadata, ...]: ...


class EventbriteReadError(Exception):
    def __init__(self, category: str) -> None:
        self.category = (
            category
            if category
            in {
                "authentication",
                "authorization",
                "rate_limit",
                "timeout",
                "network",
                "invalid_response",
                "provider_unavailable",
            }
            else "invalid_response"
        )
        super().__init__(self.category)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class BoundedEventbriteReader:
    _BASE = "https://www.eventbriteapi.com/v3"
    _ID = re.compile(r"^[0-9]{1,32}$")
    _STATUSES = frozenset({"draft", "live", "started", "ended", "completed", "canceled"})
    _MAX_BODY = 256 * 1024
    _MAX_EVENTS = 100

    def list_events(self, credential: SecretLease | None) -> tuple[EventbriteEventMetadata, ...]:
        if not isinstance(credential, SecretLease):
            raise EventbriteReadError("authentication")
        return credential.use(self._list_with_credential)

    def _list_with_credential(self, credential: memoryview) -> tuple[EventbriteEventMetadata, ...]:
        token = str(credential, "utf-8")
        if not token or "\r" in token or "\n" in token:
            raise EventbriteReadError("authentication")
        try:
            organizations = self._page_rows(
                f"{self._BASE}/users/me/organizations/", "organizations", token, 10
            )
            rows: list[EventbriteEventMetadata] = []
            for organization in organizations:
                organization_id = organization.get("id") if isinstance(organization, dict) else None
                if not isinstance(organization_id, str) or not self._ID.fullmatch(organization_id):
                    raise EventbriteReadError("invalid_response")
                event_rows = self._page_rows(
                    f"{self._BASE}/organizations/{organization_id}/events/",
                    "events",
                    token,
                    self._MAX_EVENTS - len(rows),
                    status="draft,live,started,ended,completed,canceled",
                )
                rows.extend(self._parse_event(item) for item in event_rows)
            return tuple(
                sorted(
                    rows,
                    key=lambda item: (item.start_at or item.changed_at, item.provider_event_id),
                )
            )
        finally:
            token = ""

    def _page_rows(
        self,
        base_url: str,
        key: str,
        token: str,
        limit: int,
        **filters: str,
    ) -> list[object]:
        rows: list[object] = []
        for page in range(1, 6):
            page_size = min(50, limit - len(rows))
            if page_size <= 0:
                break
            payload = self._get_json(
                f"{base_url}?{urlencode({**filters, 'page': page, 'page_size': page_size})}",
                token,
            )
            values = payload.get(key)
            pagination = payload.get("pagination")
            if not isinstance(values, list) or not isinstance(pagination, dict):
                raise EventbriteReadError("invalid_response")
            rows.extend(values)
            has_more = pagination.get("has_more_items")
            if not isinstance(has_more, bool):
                raise EventbriteReadError("invalid_response")
            if not has_more:
                return rows
            if len(rows) >= limit:
                raise EventbriteReadError("invalid_response")
        raise EventbriteReadError("invalid_response")

    def _get_json(self, url: str, token: str) -> dict[str, object]:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.eventbriteapi.com"
            or parsed.port is not None
        ):
            raise EventbriteReadError("network")
        if parsed.path != "/v3/users/me/organizations/" and not re.fullmatch(
            r"/v3/organizations/[0-9]{1,32}/events/", parsed.path
        ):
            raise EventbriteReadError("network")
        request = Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
        try:
            with build_opener(_NoRedirects()).open(request, timeout=5) as response:
                body = response.read(self._MAX_BODY + 1)
                status = response.status
        except HTTPError as exc:
            status = exc.code
            body = b""
        except TimeoutError:
            raise EventbriteReadError("timeout") from None
        except (OSError, URLError):
            raise EventbriteReadError("network") from None
        finally:
            request.headers.clear()
            request.unredirected_hdrs.clear()
        if status == 401:
            raise EventbriteReadError("authentication")
        if status == 403:
            raise EventbriteReadError("authorization")
        if status == 429:
            raise EventbriteReadError("rate_limit")
        if 500 <= status <= 599:
            raise EventbriteReadError("provider_unavailable")
        if status != 200 or len(body) > self._MAX_BODY:
            raise EventbriteReadError("invalid_response")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise EventbriteReadError("invalid_response") from None
        if not isinstance(payload, dict):
            raise EventbriteReadError("invalid_response")
        return payload

    def _parse_event(self, value: object) -> EventbriteEventMetadata:
        if not isinstance(value, dict):
            raise EventbriteReadError("invalid_response")
        event_id = value.get("id")
        name = value.get("name")
        title = name.get("text") if isinstance(name, dict) else None
        status = value.get("status")
        changed = self._datetime(value.get("changed"))
        start = value.get("start")
        end = value.get("end")
        timezone = start.get("timezone", "") if isinstance(start, dict) else ""
        if (
            not isinstance(event_id, str)
            or not self._ID.fullmatch(event_id)
            or not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 240
            or not isinstance(status, str)
            or status not in self._STATUSES
            or changed is None
            or not isinstance(timezone, str)
            or len(timezone) > 64
        ):
            raise EventbriteReadError("invalid_response")
        return EventbriteEventMetadata(
            provider_event_id=event_id,
            title=title.strip(),
            status=status,
            changed_at=changed,
            start_at=self._datetime(start.get("utc")) if isinstance(start, dict) else None,
            end_at=self._datetime(end.get("utc")) if isinstance(end, dict) else None,
            timezone=timezone,
        )

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if not isinstance(value, str) or len(value) > 40:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
