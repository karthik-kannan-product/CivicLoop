from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
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
    _MAX_EVENTS = 2

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
                f"{self._BASE}/users/me/organizations/",
                "organizations",
                token,
                10,
                include_page_size=False,
            )
            rows: list[EventbriteEventMetadata] = []
            for organization in organizations:
                if len(rows) >= self._MAX_EVENTS:
                    break
                organization_id = organization.get("id") if isinstance(organization, dict) else None
                if not isinstance(organization_id, str) or not self._ID.fullmatch(organization_id):
                    raise EventbriteReadError("invalid_response")
                event_rows = self._page_rows(
                    f"{self._BASE}/organizations/{organization_id}/events/",
                    "events",
                    token,
                    self._MAX_EVENTS - len(rows),
                    status="draft,live",
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
        *,
        include_page_size: bool = True,
        **filters: str,
    ) -> list[object]:
        rows: list[object] = []
        for page in range(1, 6):
            page_size = min(50, limit - len(rows))
            if page_size <= 0:
                break
            query: dict[str, str | int] = {**filters, "page": page}
            if include_page_size:
                query["page_size"] = page_size
            payload = self._get_json(f"{base_url}?{urlencode(query)}", token)
            values = payload.get(key)
            pagination = payload.get("pagination")
            if not isinstance(values, list) or not isinstance(pagination, dict):
                raise EventbriteReadError("invalid_response")
            rows.extend(values)
            has_more = pagination.get("has_more_items")
            if not isinstance(has_more, bool):
                raise EventbriteReadError("invalid_response")
            if len(rows) >= limit:
                return rows[:limit]
            if not has_more:
                return rows
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
        except OSError, URLError:
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
        except UnicodeDecodeError, json.JSONDecodeError, RecursionError:
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


@dataclass(frozen=True)
class EventbriteEventPage:
    events: tuple[EventbriteEventMetadata, ...]
    next_cursor: str | None

    @property
    def complete(self) -> bool:
        return self.next_cursor is None


class PaginatedEventbriteReader(BoundedEventbriteReader):
    """Product browsing; at most four provider requests per user-driven page.

    Organizations use provider defaults (that endpoint does not accept page_size).
    The encrypted cursor carries position only, never credentials/provider content.
    """

    _MAX_REQUESTS = 4

    def list_page(
        self,
        credential: SecretLease | None,
        *,
        account: str,
        cursor: str | None = None,
        page_size: int = 20,
        statuses: tuple[str, ...] = ("draft",),
        changed_since: str | None = None,
        changed_until: str | None = None,
        created_since: str | None = None,
        created_until: str | None = None,
    ) -> EventbriteEventPage:
        if not isinstance(credential, SecretLease):
            raise EventbriteReadError("authentication")
        if type(page_size) is not int or not 1 <= page_size <= 20:
            raise EventbriteReadError("invalid_response")
        if not statuses or any(status not in self._STATUSES for status in statuses):
            raise EventbriteReadError("invalid_response")
        bounds = [changed_since, changed_until, created_since, created_until]
        parsed_bounds = [self._datetime(value) if value is not None else None for value in bounds]
        if any(
            value is not None and parsed is None
            for value, parsed in zip(bounds, parsed_bounds, strict=True)
        ):
            raise EventbriteReadError("invalid_response")
        for lower, upper in (parsed_bounds[:2], parsed_bounds[2:]):
            if lower is not None and upper is not None and lower > upper:
                raise EventbriteReadError("invalid_response")
        query = [account, page_size, sorted(set(statuses)), *bounds]
        binding = hashlib.sha256(json.dumps(query).encode()).hexdigest()
        cipher = Fernet(
            base64.urlsafe_b64encode(
                hashlib.sha256(
                    (settings.SECRET_KEY + ":eventbrite-pagination-v1").encode()
                ).digest()
            )
        )
        position = [1, 0, 1]
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 2048:
                    raise ValueError
                state = json.loads(cipher.decrypt(cursor.encode(), ttl=3600))
                position = state["position"]
                if (
                    state["binding"] != binding
                    or not isinstance(position, list)
                    or len(position) != 3
                ):
                    raise ValueError
                if any(type(value) is not int or not 0 <= value <= 1000000 for value in position):
                    raise ValueError
                if position[0] < 1 or position[2] < 1:
                    raise ValueError
            except ValueError, KeyError, TypeError, InvalidToken:
                raise EventbriteReadError("invalid_response") from None

        def read(secret: memoryview) -> EventbriteEventPage:
            token = str(secret, "utf-8")
            if not token or "\r" in token or "\n" in token:
                raise EventbriteReadError("authentication")
            org_page, org_index, event_page = position
            rows: dict[str, EventbriteEventMetadata] = {}
            requests = 0
            deadline = time.monotonic() + 25
            complete = False
            while requests < self._MAX_REQUESTS and len(rows) < page_size:
                if time.monotonic() >= deadline:
                    raise EventbriteReadError("timeout")
                payload = self._get_json(
                    f"{self._BASE}/users/me/organizations/?page={org_page}", token
                )
                requests += 1
                organizations, more_orgs = self._product_rows(payload, "organizations", 50)
                if org_index >= len(organizations):
                    if org_index > len(organizations):
                        raise EventbriteReadError("invalid_response")
                    if not more_orgs:
                        complete = True
                        break
                    org_page, org_index, event_page = org_page + 1, 0, 1
                    continue
                while (
                    org_index < len(organizations)
                    and requests < self._MAX_REQUESTS
                    and len(rows) < page_size
                ):
                    organization = organizations[org_index]
                    org_id = organization.get("id") if isinstance(organization, dict) else None
                    if not isinstance(org_id, str) or not self._ID.fullmatch(org_id):
                        raise EventbriteReadError("invalid_response")
                    filters = {
                        "page": event_page,
                        "page_size": page_size,
                        "status": ",".join(sorted(set(statuses))),
                        "order_by": "created_desc",
                    }
                    if time.monotonic() >= deadline:
                        raise EventbriteReadError("timeout")
                    payload = self._get_json(
                        f"{self._BASE}/organizations/{org_id}/events/?{urlencode(filters)}", token
                    )
                    requests += 1
                    values, more_events = self._product_rows(payload, "events", page_size)
                    for value in values:
                        event = self._parse_event(value)
                        if event.status not in statuses:
                            raise EventbriteReadError("invalid_response")
                        created = (
                            self._datetime(value.get("created"))
                            if isinstance(value, dict)
                            else None
                        )
                        if any(
                            bound is not None
                            and (
                                timestamp is None
                                or (timestamp < bound if lower else timestamp > bound)
                            )
                            for timestamp, bound, lower in (
                                (event.changed_at, parsed_bounds[0], True),
                                (event.changed_at, parsed_bounds[1], False),
                                (created, parsed_bounds[2], True),
                                (created, parsed_bounds[3], False),
                            )
                        ):
                            continue
                        rows[event.provider_event_id] = event
                    if more_events:
                        event_page += 1
                    else:
                        org_index, event_page = org_index + 1, 1
                    if rows:
                        break
                if org_index == len(organizations):
                    if not more_orgs:
                        complete = True
                        break
                    org_page, org_index, event_page = org_page + 1, 0, 1
                if rows:
                    break
            if time.monotonic() > deadline:
                raise EventbriteReadError("timeout")
            next_cursor = (
                None
                if complete
                else cipher.encrypt(
                    json.dumps(
                        {
                            "binding": binding,
                            "position": [org_page, org_index, event_page],
                        }
                    ).encode()
                ).decode()
            )
            return EventbriteEventPage(
                tuple(
                    sorted(
                        rows.values(),
                        key=lambda event: (-event.changed_at.timestamp(), event.provider_event_id),
                    )
                ),
                next_cursor,
            )

        return credential.use(read)

    @staticmethod
    def _product_rows(
        payload: dict[str, object], key: str, limit: int
    ) -> tuple[list[object], bool]:
        rows, pagination = payload.get(key), payload.get("pagination")
        if not isinstance(rows, list) or len(rows) > limit or not isinstance(pagination, dict):
            raise EventbriteReadError("invalid_response")
        more = pagination.get("has_more_items")
        if not isinstance(more, bool) or (more and not rows):
            raise EventbriteReadError("invalid_response")
        return rows, more
