"""Real owner HTTP from inside the isolated web container.

Synthetic cookies, CSRF and request identity arrive only through bounded stdin;
they never occur in subprocess arguments or public diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.error
import urllib.request

MAX_BYTES = 65536
FIELDS = {"path", "body", "session", "csrf", "idempotency_key"}


def response_evidence(status, content_type, raw):
    return {
        "http_status": status,
        "content_type": content_type if content_type in {
            "application/json", "application/problem+json", "text/html", "text/plain"
        } else "other",
        "body_length": len(raw),
        "body_digest": hashlib.sha256(raw).hexdigest(),
        "csrf_rejected": status == 403 and b"csrf" in raw.lower(),
        "server_error": status >= 500,
    }


def owner_http(payload, *, base_url="http://127.0.0.1:8000"):
    try:
        if type(payload) is not dict or set(payload) != FIELDS:
            raise ValueError
        path = payload["path"]
        if (
            not isinstance(path, str)
            or len(path) > 256
            or not re.fullmatch(r"/api/v1/[a-zA-Z0-9/-]+", path)
        ):
            raise ValueError
        for field, maximum in (("session", 128), ("csrf", 64), ("idempotency_key", 36)):
            value = payload[field]
            if (
                not isinstance(value, str)
                or len(value) > maximum
                or not re.fullmatch(r"[A-Za-z0-9_-]*", value)
            ):
                raise ValueError
        body = payload["body"]
        if body is not None and type(body) is not dict:
            raise ValueError
        encoded = json.dumps(body).encode() if body is not None else None
        if encoded is not None and len(encoded) > MAX_BYTES:
            raise ValueError
        headers = {
            "Cookie": "sessionid=" + payload["session"] + "; csrftoken=" + payload["csrf"],
            "X-CSRFToken": payload["csrf"],
            "Content-Type": "application/json",
        }
        if payload["idempotency_key"]:
            headers["Idempotency-Key"] = payload["idempotency_key"]
        request = urllib.request.Request(base_url + path, data=encoded, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as response:
                content_type = response.headers.get_content_type()
                status, raw = response.status, response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            content_type = error.headers.get_content_type()
            status, raw = error.code, error.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return {"failure_category": "owner_response_bound"}
        try:
            value = json.loads(raw)
            if type(value) is not dict:
                raise ValueError
        except Exception:
            return {
                "failure_category": "owner_response_schema",
                "response_evidence": response_evidence(status, content_type, raw),
            }
        # An accidental echo of the private session/header must not cross even
        # this private command boundary. The API schemas do not include either.
        if any(
            secret and secret.encode() in raw for secret in (payload["session"], payload["csrf"])
        ):
            return {"failure_category": "prohibited_authority"}
        return {"http_status": status, "body": value}
    except ValueError:
        return {"failure_category": "owner_request_invalid"}
    except Exception:
        return {"failure_category": "owner_http_unavailable"}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError
        evidence = owner_http(json.loads(raw))
    except Exception:
        evidence = {"failure_category": "owner_request_invalid"}
    print(json.dumps(evidence, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
