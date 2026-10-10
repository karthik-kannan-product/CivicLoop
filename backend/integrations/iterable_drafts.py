"""Server-only creation of UNSCHEDULED Iterable email blast campaigns.

Contract verified against https://raw.githubusercontent.com/Iterable/api-client/main/api-docs.json.
scheduleSend defaults true upstream: this adapter requires explicit false.
CampaignDetails.startAt is optional; absent/null plus Draft/Ready is required.
The provider copies the source template, so copied content is verified by GET.
No provider idempotency is assumed. Ambiguous writes are never retried here.
Callers must enforce durable approval before acquiring the scoped lease.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from integrations.types import SecretLease

BASE_URLS = {"us": "https://api.iterable.com", "eu": "https://api.eu.iterable.com"}
TIMEOUT_SECONDS = 5
MAX_RESPONSE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 16 * 1024
_FIELDS = {"name", "templateId", "listIds", "suppressionListIds", "scheduleSend"}
_CONTENT_FIELDS = {
    "fromEmail",
    "fromName",
    "replyToEmail",
    "subject",
    "html",
    "plainText",
    "preheaderText",
    "bccEmails",
    "ccEmails",
    "messageTypeId",
    "dataFeedIds",
    "dataFeedId",
    "linkParams",
    "googleAnalyticsCampaignName",
    "cacheDataFeed",
    "mergeDataFeedContext",
    "campaignDataFields",
}


class IterableDraftError(Exception):
    """Safe category only; provider bodies and exception text are discarded."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class IterableDraftReceipt:
    outcome: str
    provider_id: str | None
    provider_status: str | None
    request_digest: str
    readback_digest: str | None = None
    error_category: str | None = None


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _identifier(value: object) -> int:
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise IterableDraftError("invalid_request")
    return value


def _region(region: str) -> str:
    if not isinstance(region, str) or region not in BASE_URLS:
        raise IterableDraftError("invalid_request")
    return region


def validate_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise IterableDraftError("invalid_request")
    if (
        not isinstance(payload["name"], str)
        or not payload["name"].strip()
        or len(payload["name"]) > 200
        or payload["scheduleSend"] is not False
    ):
        raise IterableDraftError("invalid_request")
    _identifier(payload["templateId"])
    for key in ("listIds", "suppressionListIds"):
        ids = payload[key]
        if not isinstance(ids, list) or len(ids) > 100 or (key == "listIds" and not ids):
            raise IterableDraftError("invalid_request")
        for value in ids:
            _identifier(value)
        if len(set(ids)) != len(ids):
            raise IterableDraftError("invalid_request")
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    if len(encoded) > MAX_REQUEST_BYTES:
        raise IterableDraftError("invalid_request")
    return json.loads(encoded)


def compute_request_digest(region: str, payload: object, expected_template_digest: str) -> str:
    if not isinstance(expected_template_digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_template_digest
    ):
        raise IterableDraftError("invalid_request")
    return _digest(
        {
            "region": _region(region),
            "payload": validate_payload(payload),
            "expected_template_digest": expected_template_digest,
        }
    )


def template_content_digest(template: dict[str, Any]) -> str:
    return _digest({key: template.get(key) for key in sorted(_CONTENT_FIELDS)})


def readback_digest(campaign: dict[str, Any], template_digest: str) -> str:
    return _digest(
        {
            "campaign": {
                key: campaign.get(key)
                for key in (
                    "id",
                    "name",
                    "campaignState",
                    "messageMedium",
                    "type",
                    "startAt",
                    "endedAt",
                    "templateId",
                    "listIds",
                    "suppressionListIds",
                    "updatedAt",
                    "workflowId",
                    "recurringCampaignId",
                )
            },
            "template_digest": template_digest,
        }
    )


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class IterableDraftAdapter:
    def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        region: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _region(region)
        if not (
            method == "POST"
            and path == "/api/campaigns/create"
            or method == "GET"
            and re.fullmatch(r"/api/campaigns/[1-9][0-9]{0,18}", path)
            or method == "GET"
            and re.fullmatch(r"/api/templates/email/get\?templateId=[1-9][0-9]{0,18}", path)
        ):
            raise IterableDraftError("invalid_request")
        if method == "POST":
            payload = validate_payload(payload)
        elif payload is not None:
            raise IterableDraftError("invalid_request")
        headers = {"Api-Key": token, "Content-Type": "application/json"}
        request = Request(
            BASE_URLS[region] + path,
            method=method,
            headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None,
        )
        opener = build_opener(ProxyHandler({}), _NoRedirects())
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise IterableDraftError("invalid_response")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
                    raise IterableDraftError("invalid_response")
                try:
                    result = json.loads(raw)
                except ValueError, UnicodeDecodeError, RecursionError:
                    raise IterableDraftError("invalid_response") from None
                if not isinstance(result, dict):
                    raise IterableDraftError("invalid_response")
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
            raise IterableDraftError(category) from None
        except TimeoutError:
            raise IterableDraftError("timeout") from None
        except OSError, URLError, HTTPException:
            raise IterableDraftError("network") from None
        finally:
            headers.clear()
            request.headers.clear()
            request.unredirected_hdrs.clear()

    @staticmethod
    def _token(
        credential: SecretLease, operation: Callable[[str], IterableDraftReceipt]
    ) -> IterableDraftReceipt:
        if (
            not isinstance(credential, SecretLease)
            or credential.reference.provider != "iterable"
            or credential.purpose != "iterable_draft_write"
            or not isinstance(credential.workflow_id, UUID)
        ):
            raise IterableDraftError("invalid_credential")

        def use(view: memoryview) -> IterableDraftReceipt:
            try:
                token = str(view, "utf-8")
            except UnicodeDecodeError:
                raise IterableDraftError("invalid_credential") from None
            if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise IterableDraftError("invalid_credential")
            try:
                return operation(token)
            finally:
                token = ""

        return credential.use(use)

    def _template(self, token: str, region: str, template_id: int) -> str:
        template = self._request(
            "GET", f"/api/templates/email/get?templateId={template_id}", token, region=region
        )
        if type(template.get("templateId")) is not int or template["templateId"] != template_id:
            raise IterableDraftError("identity_mismatch")
        return template_content_digest(template)

    def _confirm(
        self,
        token: str,
        region: str,
        campaign_id: int,
        payload: dict[str, Any],
        digest: str,
        template_digest: str,
    ) -> IterableDraftReceipt:
        status = None
        try:
            campaign = self._request("GET", f"/api/campaigns/{campaign_id}", token, region=region)
            status = campaign.get("campaignState")
            if not isinstance(status, str):
                status = None
            if type(campaign.get("id")) is not int or campaign["id"] != campaign_id:
                raise IterableDraftError("identity_mismatch")
            if (
                status not in {"Draft", "Ready"}
                or campaign.get("type") != "Blast"
                or campaign.get("messageMedium") != "Email"
                or campaign.get("startAt") is not None
                or campaign.get("endedAt") is not None
                or campaign.get("workflowId") is not None
                or campaign.get("recurringCampaignId") is not None
            ):
                raise IterableDraftError("unsafe_provider_state")
            if campaign.get("name") != payload["name"]:
                raise IterableDraftError("readback_mismatch")
            for key in ("listIds", "suppressionListIds"):
                if key not in campaign or (
                    not isinstance(campaign[key], list)
                    or any(type(v) is not int for v in campaign[key])
                    or sorted(campaign[key]) != sorted(payload[key])
                ):
                    raise IterableDraftError("readback_mismatch")
            if "templateId" in campaign:
                copied_id = _identifier(campaign["templateId"])
                if self._template(token, region, copied_id) != template_digest:
                    raise IterableDraftError("readback_mismatch")
            else:
                raise IterableDraftError("unverifiable_template")
            return IterableDraftReceipt(
                "CONFIRMED",
                str(campaign_id),
                status,
                digest,
                readback_digest(campaign, template_digest),
            )
        except IterableDraftError as exc:
            # Store only recognized state strings, never arbitrary provider strings.
            status = (
                status
                if status
                in {
                    "Draft",
                    "Ready",
                    "Scheduled",
                    "Running",
                    "Finished",
                    "Starting",
                    "Aborted",
                    "Recurring",
                    "Archived",
                }
                else None
            )
            return IterableDraftReceipt(
                "UNKNOWN", str(campaign_id), status, digest, error_category=exc.category
            )

    def create(
        self,
        credential: SecretLease,
        *,
        payload: object,
        expected_template_digest: str,
        region: str = "us",
    ) -> IterableDraftReceipt:
        validated = validate_payload(payload)
        digest = compute_request_digest(region, validated, expected_template_digest)

        def execute(token: str) -> IterableDraftReceipt:
            template_digest = self._template(token, region, validated["templateId"])
            if template_digest != expected_template_digest:
                raise IterableDraftError("stale_template")
            try:
                result = self._request(
                    "POST", "/api/campaigns/create", token, region=region, payload=validated
                )
                campaign_id = _identifier(result.get("campaignId"))
            except IterableDraftError as exc:
                if exc.category in {
                    "provider_validation",
                    "authentication",
                    "forbidden",
                    "not_found",
                    "rate_limited",
                    "redirect",
                }:
                    raise
                return IterableDraftReceipt(
                    "UNKNOWN", None, None, digest, error_category=exc.category
                )
            return self._confirm(token, region, campaign_id, validated, digest, template_digest)

        return self._token(credential, execute)

    def reconcile(
        self,
        credential: SecretLease,
        *,
        campaign_id: str | None,
        payload: object,
        request_digest: str,
        expected_template_digest: str,
        region: str = "us",
    ) -> IterableDraftReceipt:
        validated = validate_payload(payload)
        digest = compute_request_digest(region, validated, expected_template_digest)
        if digest != request_digest:
            raise IterableDraftError("invalid_request")
        if campaign_id is None:
            return IterableDraftReceipt(
                "UNKNOWN", None, None, digest, error_category="human_reconciliation_required"
            )
        if not isinstance(campaign_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", campaign_id):
            raise IterableDraftError("invalid_request")
        identifier = _identifier(int(campaign_id))
        return self._token(
            credential,
            lambda token: self._confirm(
                token,
                region,
                identifier,
                validated,
                digest,
                expected_template_digest,
            ),
        )


request_digest = compute_request_digest
