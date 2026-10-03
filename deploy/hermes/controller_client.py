"""Adapter client for the internal controller; no authority enters its body."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

from deploy.hermes.adapter import MAX_BODY_BYTES, _proposal_references, map_upstream_result
from deploy.hermes.process_controller import ControllerUnavailable
from deploy.hermes.run_bridge import _BoundedHTTP, _json


def validate_result(request, result):
    expected = map_upstream_result(request, {})
    if not isinstance(result, dict) or set(result) != set(expected):
        raise ControllerUnavailable()
    if any(
        result[key] != expected[key]
        for key in ("schema_version", "run_id", "workflow_id", "revision_id")
    ):
        raise ControllerUnavailable()
    if result["status"] not in {"succeeded", "failed", "cancelled"}:
        raise ControllerUnavailable()
    usage = result["usage"]
    ceilings = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cost_microusd": 1_000_000_000}
    if not isinstance(usage, dict) or set(usage) != set(ceilings):
        raise ControllerUnavailable()
    if any(type(usage[k]) is not int or not 0 <= usage[k] <= v for k, v in ceilings.items()):
        raise ControllerUnavailable()
    if result["status"] == "succeeded":
        _proposal_references(json.dumps({"proposal_references": result["proposal_references"]}))
        if result["failure_category"] is not None:
            raise ControllerUnavailable()
    elif result["proposal_references"] != [] or result["failure_category"] not in {
        "invalid_output",
        "cancelled",
        "provider_unavailable",
        "capability_rejected",
        "timeout",
        "dependency_unavailable",
    }:
        raise ControllerUnavailable()
    if len(json.dumps(result).encode()) > MAX_BODY_BYTES:
        raise ControllerUnavailable()


class RemoteProcessController:
    def __init__(self, *, url, token, poll_interval=0.1):
        self.url = url.rstrip("/")
        self.token = token
        self.poll_interval = poll_interval
        self.quarantined = False

    def _call(self, path, method, body, deadline, scope=None):
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if scope is not None:
            headers["X-CivicLoop-Transport-Scope"] = scope
        raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        try:
            status, data = _BoundedHTTP().request(
                url=self.url + path,
                method=method,
                raw=raw,
                headers=headers,
                deadline=min(deadline, time.monotonic() + 3),
            )
            if status not in {200, 202}:
                raise ControllerUnavailable()
            return _json(data)
        except Exception:
            raise ControllerUnavailable() from None

    def execute(self, body, *, scope_token, deadline=None):
        from deploy.hermes.controller_service import RUN_PATH

        deadline = min(
            deadline or float("inf"), time.monotonic() + body["budgets"]["timeout_seconds"]
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ControllerUnavailable()
        run_id = map_upstream_result(body, {})["run_id"]
        path = RUN_PATH + "/" + run_id
        envelope = {
            "request": {k: v for k, v in body.items() if k != "capability_token"},
            "expires_at": (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat(),
        }
        try:
            admission = self._call(RUN_PATH, "POST", envelope, deadline, scope_token)
            if admission.get("run_id") != run_id:
                raise ControllerUnavailable()
            while time.monotonic() < deadline:
                status = self._call(path, "GET", None, deadline)
                if status.get("run_id") != run_id:
                    raise ControllerUnavailable()
                if status.get("status") in {"succeeded", "failed", "cancelled"}:
                    result = status.get("result")
                    validate_result(body, result)
                    return result
                if status.get("status") not in {"running", "cancelling"}:
                    raise ControllerUnavailable()
                time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
            raise ControllerUnavailable()
        except Exception:
            try:
                self._call(path + "/cancel", "POST", {}, time.monotonic() + 2)
            except Exception:
                self.quarantined = True
            raise ControllerUnavailable() from None
