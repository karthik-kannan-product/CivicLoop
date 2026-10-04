"""Authenticated, bounded worker-to-adapter calls; never log authority or content."""

from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import time
import uuid
from collections.abc import Callable
from datetime import timedelta
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from agents.capabilities import TOOLS
from agents.models import BudgetReservation, WorkflowCapability

RUN_PATH = "/internal/v1/hermes/runs"
MAX_BODY_BYTES = 32_768
MODEL_ALIAS = "civicloop-default"
_IO_SLOT = threading.BoundedSemaphore(1)
_CANCEL_IO_SLOT = threading.BoundedSemaphore(1)
_CATEGORIES = frozenset(
    {
        "budget_exhausted",
        "cancelled",
        "dependency_unavailable",
        "invalid_output",
        "provider_unavailable",
        "timeout",
    }
)


class SafeRunFailure(Exception):
    def __init__(self, category="dependency_unavailable", *, status="failed"):
        if category not in _CATEGORIES or status not in {"failed", "cancelled"}:
            category, status = "dependency_unavailable", "failed"
        self.category = category
        self.status = status
        super().__init__("Hermes run failed.")


def _cancelled(should_cancel):
    if should_cancel is not None:
        try:
            if should_cancel():
                raise SafeRunFailure("cancelled", status="cancelled")
        except SafeRunFailure:
            raise
        except Exception:
            raise SafeRunFailure() from None


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SafeRunFailure("invalid_output")
        value[key] = item
    return value


def _decode(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY_BYTES:
        raise SafeRunFailure("invalid_output")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeError, ValueError, RecursionError:
        raise SafeRunFailure("invalid_output") from None
    if not isinstance(value, dict):
        raise SafeRunFailure("invalid_output")
    return value


class _DeadlineIO:
    def __init__(self, deadline):
        self.deadline = deadline
        self.lock = threading.Lock()
        self.cancelled = False
        self.sock = None

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if self.cancelled or remaining <= 0:
            raise TimeoutError()
        return remaining

    @staticmethod
    def close(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def attach(self, sock):
        with self.lock:
            try:
                self.remaining()
            except TimeoutError:
                self.close(sock)
                raise
            self.sock = sock

    def connect(self, address, timeout, source_address=None):
        sock = socket.create_connection(address, min(timeout, self.remaining()), source_address)
        self.attach(sock)
        return sock

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.sock is not None:
                self.close(self.sock)
                self.sock = None


class _DeadlineConnection:
    def __init__(self, *args, io, **kwargs):
        self.io = io
        super().__init__(*args, **kwargs)
        self._create_connection = io.connect

    def connect(self):
        self.io.remaining()
        super().connect()
        self.io.attach(self.sock)


class _HTTP(_DeadlineConnection, HTTPConnection):
    pass


class _HTTPS(_DeadlineConnection, HTTPSConnection):
    pass


class HermesClient:
    def __init__(self, *, url, token, timeout_seconds=120, max_inferences=8):
        try:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError
            if (
                not isinstance(token, str)
                or not 16 <= len(token) <= 256
                or any(ord(c) < 33 or ord(c) > 126 for c in token)
                or type(timeout_seconds) is not int
                or not 1 <= timeout_seconds <= 300
                or type(max_inferences) is not int
                or not 1 <= max_inferences <= 1000
            ):
                raise ValueError
        except ValueError, TypeError, AttributeError:
            raise SafeRunFailure() from None
        self.url = url.rstrip("/")
        self._token = token
        self.timeout_seconds = timeout_seconds
        self.max_inferences = max_inferences

    @classmethod
    def from_settings(cls):
        try:
            path = Path(settings.CIVICLOOP_HERMES_ADAPTER_TOKEN_FILE)
            if path.stat().st_size > 256 or path.stat().st_mode & 0o077:
                raise ValueError
            token = path.read_text(encoding="utf-8").strip()
            return cls(
                url=settings.CIVICLOOP_HERMES_ADAPTER_URL,
                token=token,
                timeout_seconds=getattr(settings, "CIVICLOOP_HERMES_TIMEOUT_SECONDS", 120),
                max_inferences=getattr(settings, "CIVICLOOP_HERMES_MAX_INFERENCES", 8),
            )
        except Exception:
            raise SafeRunFailure() from None

    def _load_capability(self, run, capability):
        binding = run.hermes_binding
        record = WorkflowCapability.objects.filter(
            token_digest=hashlib.sha256(capability.encode()).hexdigest(),
            workflow_id=run.workflow_id,
            revision_id=run.event_revision_id,
            revision_digest=binding.revision_digest,
            actor_id=binding.actor_id,
            correlation_id=binding.correlation_id,
            revoked_at__isnull=True,
            audience="civicloop-hermes",
            expires_at__gt=timezone.now(),
        ).first()
        if (
            record is None
            or not isinstance(record.tools, list)
            or len(record.tools) != len(TOOLS)
            or set(record.tools) != TOOLS
        ):
            raise SafeRunFailure()
        return record.expires_at

    def _load_reservation(self, run):
        reservation = BudgetReservation.objects.filter(
            run_id=run.id,
            model_profile_id=run.model_profile_id,
            routing_policy_id=run.routing_policy_id,
            status=BudgetReservation.Status.RESERVED,
            expires_at__gt=timezone.now(),
        ).first()
        if reservation is None or reservation.reserved_cost_microusd <= 0:
            raise SafeRunFailure("budget_exhausted")
        return reservation

    def _request(self, *, method, path, raw, headers, deadline, should_cancel, control=False):
        # Direct HTTPConnection deliberately ignores environment proxies and never redirects.
        slot = _CANCEL_IO_SLOT if control else _IO_SLOT
        if not slot.acquire(blocking=False):
            raise SafeRunFailure()
        io = _DeadlineIO(deadline)
        done = threading.Event()
        result = []

        def perform():
            connection = None
            try:
                parsed = urlsplit(self.url)
                connection_type = _HTTPS if parsed.scheme == "https" else _HTTP
                connection = connection_type(
                    parsed.hostname, parsed.port, timeout=io.remaining(), io=io
                )
                connection.request(method, path, body=raw, headers=headers)
                with connection.getresponse() as response:
                    data = response.read(MAX_BODY_BYTES + 1)
                    io.remaining()
                    result.append((response.status, data))
            except Exception:
                pass
            finally:
                if connection is not None:
                    connection.close()
                slot.release()
                done.set()

        try:
            threading.Thread(target=perform, daemon=True).start()
        except Exception:
            slot.release()
            raise SafeRunFailure() from None
        try:
            while not done.is_set():
                _cancelled(should_cancel)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SafeRunFailure("timeout")
                done.wait(min(0.05, remaining))
            _cancelled(should_cancel)
            if time.monotonic() >= deadline:
                raise SafeRunFailure("timeout")
            if not result:
                raise SafeRunFailure()
            return result[0]
        finally:
            io.cancel()
            # Allow socket teardown to release the slot before bounded cancellation.
            # A stalled resolver retains the slot and makes cleanup fail closed.
            done.wait(0.05)

    def cancel(self, run):
        try:
            run_id = self._run_id(run)
            deadline = time.monotonic() + 2
            status, raw = self._request(
                method="POST",
                path=f"{RUN_PATH}/{run_id}/cancel",
                raw=b"{}",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                deadline=deadline,
                should_cancel=None,
                control=True,
            )
            if status != 200 or _decode(raw) != {
                "schema_version": "1.0",
                "run_id": run_id,
                "status": "cancelled",
            }:
                raise SafeRunFailure()
            # Cleanup must be able to reach the server while the execution socket
            # awaits its terminal response. Keep control bounded separately, then
            # require execution I/O to retire before acknowledging cleanup locally.
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not _IO_SLOT.acquire(timeout=remaining):
                raise SafeRunFailure()
            _IO_SLOT.release()
        except Exception:
            raise SafeRunFailure() from None
        return True

    @staticmethod
    def _run_id(run):
        return str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"urn:civicloop:run:{run.hermes_binding.correlation_id}")
        )

    def execute(self, run, *, capability: str, should_cancel: Callable | None = None):
        _cancelled(should_cancel)
        try:
            if not isinstance(capability, str) or not re.fullmatch(
                r"cap_[\w-]{43,125}", capability, re.ASCII
            ):
                raise SafeRunFailure()
            binding = run.hermes_binding
            reservation = self._load_reservation(run)
            budgets = {
                "max_input_tokens": run.model_profile.max_input_tokens,
                "max_output_tokens": run.model_profile.max_output_tokens,
                "max_cost_microusd": min(
                    run.routing_policy.per_run_limit_microusd, reservation.reserved_cost_microusd
                ),
                "timeout_seconds": self.timeout_seconds,
            }
            ceilings = (1_000_000, 100_000, 1_000_000_000, 300)
            if any(
                type(v) is not int or not 1 <= v <= ceiling
                for v, ceiling in zip(budgets.values(), ceilings, strict=True)
            ):
                raise SafeRunFailure()
            now = timezone.now()
            expiry = min(
                self._load_capability(run, capability),
                reservation.expires_at,
                now + timedelta(seconds=self.timeout_seconds),
            )
            remaining = (expiry - timezone.now()).total_seconds()
            deadline = time.monotonic() + remaining
            if remaining <= 0:
                raise SafeRunFailure("timeout")
            body = {
                "schema_version": "1.0",
                "workflow_id": str(run.workflow_id),
                "revision_id": run.event_revision_id,
                "actor_id": binding.actor.slug,
                "correlation_id": str(binding.correlation_id),
                "capability_token": capability,
                "model_alias": MODEL_ALIAS,
                "budgets": budgets,
            }
            scope = {
                "run_id": self._run_id(run),
                "workflow_id": body["workflow_id"],
                "revision_id": body["revision_id"],
                "revision_digest": binding.revision_digest,
                "actor_id": body["actor_id"],
                "model_alias": MODEL_ALIAS,
                "expires_at": expiry.isoformat(),
                "max_inferences": self.max_inferences,
                **{k: v for k, v in budgets.items() if k != "timeout_seconds"},
            }
            raw = json.dumps(body, separators=(",", ":")).encode()
            if len(raw) > MAX_BODY_BYTES:
                raise SafeRunFailure()
        except SafeRunFailure:
            raise
        except Exception:
            raise SafeRunFailure() from None
        try:
            status, response = self._request(
                method="POST",
                path=RUN_PATH,
                raw=raw,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "X-CivicLoop-Run-Binding": json.dumps(scope, separators=(",", ":")),
                },
                deadline=deadline,
                should_cancel=should_cancel,
            )
        except SafeRunFailure as failure:
            self.cancel(run)
            raise failure from None
        except Exception:
            self.cancel(run)
            raise SafeRunFailure() from None
        try:
            _cancelled(should_cancel)
        except SafeRunFailure:
            self.cancel(run)
            raise
        if status != 200:
            raise SafeRunFailure()
        value = _decode(response)
        usage = value.get("usage")
        if isinstance(usage, dict) and set(usage) == {"input_tokens", "output_tokens"}:
            from agents.budgets import _cost

            try:
                if any(type(v) is not int or v < 0 for v in usage.values()):
                    raise ValueError
                usage["cost_microusd"] = _cost(
                    run.model_profile, usage["input_tokens"], usage["output_tokens"]
                )
            except Exception:
                raise SafeRunFailure("invalid_output") from None
        self._validate_result(body, value)
        return value

    def _validate_result(self, request, value):
        fields = {
            "schema_version",
            "run_id",
            "workflow_id",
            "revision_id",
            "status",
            "proposal_references",
            "usage",
            "failure_category",
        }
        expected_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"urn:civicloop:run:{request['correlation_id']}")
        )
        if (
            set(value) != fields
            or value["schema_version"] != "1.0"
            or value["run_id"] != expected_id
            or value["workflow_id"] != request["workflow_id"]
            or type(value["revision_id"]) is not int
            or value["revision_id"] != request["revision_id"]
            or not isinstance(value["status"], str)
            or value["status"] not in {"succeeded", "failed", "cancelled"}
        ):
            raise SafeRunFailure("invalid_output")
        usage = value["usage"]
        ceilings = {
            "input_tokens": request["budgets"]["max_input_tokens"],
            "output_tokens": request["budgets"]["max_output_tokens"],
            "cost_microusd": request["budgets"]["max_cost_microusd"],
        }
        if (
            not isinstance(usage, dict)
            or set(usage) != set(ceilings)
            or any(
                type(usage[k]) is not int or not 0 <= usage[k] <= maximum
                for k, maximum in ceilings.items()
            )
        ):
            raise SafeRunFailure("invalid_output")
        refs = value["proposal_references"]
        category = value["failure_category"]
        if value["status"] != "succeeded":
            if (
                refs != []
                or not isinstance(category, str)
                or category not in _CATEGORIES | {"capability_rejected"}
                or (value["status"] == "cancelled" and category != "cancelled")
                or (value["status"] == "failed" and category == "cancelled")
            ):
                raise SafeRunFailure("invalid_output")
            return
        if category is not None or not isinstance(refs, list) or not 1 <= len(refs) <= 20:
            raise SafeRunFailure("invalid_output")
        ids = set()
        for ref in refs:
            try:
                if (
                    not isinstance(ref, dict)
                    or set(ref) != {"proposal_id", "schema_id", "proposal_digest"}
                    or not isinstance(ref["proposal_id"], str)
                    or str(uuid.UUID(ref["proposal_id"])) != ref["proposal_id"]
                    or ref["proposal_id"] in ids
                    or not isinstance(ref["schema_id"], str)
                    or not re.fullmatch(
                        r"urn:civicloop:schema:[A-Za-z0-9._:-]{1,135}", ref["schema_id"]
                    )
                    or not isinstance(ref["proposal_digest"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", ref["proposal_digest"])
                ):
                    raise ValueError
                ids.add(ref["proposal_id"])
            except ValueError, TypeError, AttributeError:
                raise SafeRunFailure("invalid_output") from None
