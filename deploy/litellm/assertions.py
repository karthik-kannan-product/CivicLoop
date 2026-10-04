"""Version-1 gateway budget assertion issuer."""

from __future__ import annotations

import base64
import hmac
import json
import re
from datetime import UTC, datetime

RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
NONCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{15,127}")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def issue_budget_assertion(
    *,
    key: bytes,
    run_id: str,
    model_alias: str,
    token_ceiling: int,
    expires_at: datetime,
    nonce: str,
) -> str:
    """Issue the narrow assertion CivicLoop will attach to one gateway request."""
    if len(key) < 32:
        raise ValueError("budget assertion key is too short")
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("run ID is invalid")
    if RUN_ID_PATTERN.fullmatch(model_alias) is None:
        raise ValueError("model alias is invalid")
    if NONCE_PATTERN.fullmatch(nonce) is None:
        raise ValueError("budget assertion nonce is invalid")
    if isinstance(token_ceiling, bool) or not 1 <= token_ceiling <= 100_000:
        raise ValueError("token ceiling is invalid")
    if expires_at.tzinfo is None:
        raise ValueError("expiry must be timezone-aware")
    payload = json.dumps(
        {
            "version": 1,
            "run_id": run_id,
            "model_alias": model_alias,
            "token_ceiling": token_ceiling,
            "expires_at": int(expires_at.astimezone(UTC).timestamp()),
            "nonce": nonce,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"{_b64encode(payload)}.{_b64encode(hmac.digest(key, payload, 'sha256'))}"
