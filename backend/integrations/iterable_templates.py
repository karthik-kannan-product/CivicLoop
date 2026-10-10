"""Create-only Iterable email content. No send, proof, update or activation APIs.

Official contract: https://raw.githubusercontent.com/Iterable/api-client/main/api-docs.json
Upsert updates ALL matching client IDs. Only an empty successful lookup allows POST.
A committed server claim is required by the service. Ambiguous writes use GET recovery.
"""

import html
import json
import re
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID

from integrations.iterable_drafts import (
    BASE_URLS,
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    IterableDraftAdapter,
    IterableDraftError,
    IterableDraftReceipt,
    _digest,
    _identifier,
    _NoRedirects,
    _region,
    template_content_digest,
)

FIELDS = {
    "clientTemplateId",
    "name",
    "fromEmail",
    "fromName",
    "replyToEmail",
    "subject",
    "html",
    "plainText",
    "messageTypeId",
}


def validate_template_payload(payload):
    if type(payload) is not dict or set(payload) != FIELDS:
        raise IterableDraftError("invalid_request")
    if _identifier(payload["messageTypeId"]) > 2**31 - 1:
        raise IterableDraftError("invalid_request")
    for key, value in payload.items():
        if key == "messageTypeId":
            continue
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > (30000 if key == "html" else 12000)
        ):
            raise IterableDraftError("invalid_request")
    client = payload["clientTemplateId"]
    try:
        if client != "civicloop-" + str(UUID(client.removeprefix("civicloop-"))):
            raise ValueError
    except ValueError:
        raise IterableDraftError("invalid_request") from None
    for key in ("fromEmail", "replyToEmail"):
        if len(payload[key]) > 254 or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", payload[key]
        ):
            raise IterableDraftError("invalid_request")
    for key in ("fromName", "subject", "name"):
        if len(payload[key]) > 240 or any(ord(c) < 32 for c in payload[key]):
            raise IterableDraftError("invalid_request")
    # Content is plain text from the accepted proposal; no executable template language.
    if any(
        mark in payload["plainText"] or mark in payload["subject"]
        for mark in ("{{", "}}", "[[", "]]")
    ):
        raise IterableDraftError("invalid_request")
    if payload["html"] != plain_html(payload["plainText"]):
        raise IterableDraftError("invalid_request")
    if len(json.dumps(payload).encode()) > 65536:
        raise IterableDraftError("invalid_request")
    return dict(payload)


def plain_html(body):
    return "<p>" + html.escape(body, quote=True).replace("\n", "<br>\n") + "</p>"


def author_payload(intent, sender):
    if type(sender) is not dict or set(sender) != {
        "fromEmail",
        "fromName",
        "replyToEmail",
        "messageTypeId",
    }:
        raise IterableDraftError("invalid_request")
    kind = {
        "create_iterable_email_draft": "invitation",
        "create_iterable_reminder_draft": "reminder",
    }.get(intent.operation_kind)
    if kind is None:
        raise IterableDraftError("invalid_request")
    content = intent.proposal.content[kind]
    return validate_template_payload(
        {
            "clientTemplateId": "civicloop-" + str(intent.pk),
            "name": "CivicLoop " + kind + " " + str(intent.pk),
            **sender,
            "subject": content["subject"],
            "plainText": content["body"],
            "html": plain_html(content["body"]),
        }
    )


def compute_request_digest(region, payload):
    return _digest({"region": _region(region), "payload": validate_template_payload(payload)})


class IterableTemplateAdapter:
    _token = staticmethod(IterableDraftAdapter._token)

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
            and path == "/api/templates/email/upsert"
            or method == "GET"
            and re.fullmatch(
                r"/api/templates/getByClientTemplateId\?clientTemplateId=civicloop-[0-9a-f-]{36}",
                path,
            )
            or method == "GET"
            and re.fullmatch(r"/api/templates/email/get\?templateId=[1-9][0-9]{0,18}", path)
        ):
            raise IterableDraftError("invalid_request")
        if method == "POST":
            payload = validate_template_payload(payload)
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

    def _lookup(self, token, region, client_id):
        result = self._request(
            "GET",
            "/api/templates/getByClientTemplateId?clientTemplateId=" + client_id,
            token,
            region=region,
        )
        entries = result.get("templates")
        if type(entries) is not list or len(entries) > 100:
            raise IterableDraftError("invalid_response")
        for entry in entries:
            if type(entry) is not dict:
                raise IterableDraftError("invalid_response")
            _identifier(entry.get("templateId"))
        return entries

    def _confirm(self, token, region, payload, digest, provider_id=None):
        try:
            entries = self._lookup(token, region, payload["clientTemplateId"])
            if len(entries) != 1 or entries[0].get("campaignId") is not None:
                raise IterableDraftError("identity_mismatch")
            template_id = _identifier(entries[0]["templateId"])
            if provider_id and str(template_id) != provider_id:
                raise IterableDraftError("identity_mismatch")
            provider_id = str(template_id)
            template = self._request(
                "GET", f"/api/templates/email/get?templateId={template_id}", token, region=region
            )
            if type(template.get("templateId")) is not int or template["templateId"] != template_id:
                raise IterableDraftError("identity_mismatch")
            if any(template.get(key) != value for key, value in payload.items()):
                raise IterableDraftError("readback_mismatch")
            # Empty provider defaults are acceptable, but feeds, copied recipients,
            # external campaign fields and other unreviewed content are forbidden.
            for key in (
                "bccEmails",
                "ccEmails",
                "dataFeedIds",
                "dataFeedId",
                "linkParams",
                "googleAnalyticsCampaignName",
                "campaignDataFields",
                "preheaderText",
            ):
                value = template.get(key)
                if value is not None and value != [] and value != "":
                    raise IterableDraftError("readback_mismatch")
            for key in ("cacheDataFeed", "mergeDataFeedContext"):
                if template.get(key) is not None and template.get(key) is not False:
                    raise IterableDraftError("readback_mismatch")
            return IterableDraftReceipt(
                "CONFIRMED", provider_id, "template", digest, template_content_digest(template)
            )
        except IterableDraftError as exc:
            return IterableDraftReceipt(
                "UNKNOWN", provider_id, None, digest, error_category=exc.category
            )

    def create(self, credential, *, payload, region="us"):
        payload = validate_template_payload(payload)
        digest = compute_request_digest(region, payload)

        def execute(token):
            if self._lookup(token, region, payload["clientTemplateId"]):
                raise IterableDraftError("unowned_collision")
            try:
                self._request(
                    "POST", "/api/templates/email/upsert", token, region=region, payload=payload
                )
            except IterableDraftError as exc:
                # Even a timeout may have created content. Never repeat the upsert.
                return IterableDraftReceipt(
                    "UNKNOWN", None, None, digest, error_category=exc.category
                )
            return self._confirm(token, region, payload, digest)

        return self._token(credential, execute)

    def reconcile(self, credential, *, payload, request_digest, provider_id=None, region="us"):
        payload = validate_template_payload(payload)
        digest = compute_request_digest(region, payload)
        if digest != request_digest:
            raise IterableDraftError("invalid_request")
        return self._token(
            credential, lambda token: self._confirm(token, region, payload, digest, provider_id)
        )
