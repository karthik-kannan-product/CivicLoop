"""Internal admission/status/cancellation for one ephemeral Hermes child."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from deploy.hermes.adapter import (
    MAX_BODY_BYTES,
    MODEL_ALIAS,
    REQUEST_FIELDS,
    AdapterPolicy,
    _read_token,
    map_upstream_result,
    validate_run_request,
)
from deploy.hermes.process_controller import ProcessController
from deploy.hermes.run_bridge import _json

RUN_PATH = "/internal/v1/controller/runs"
SCOPE_HEADER = "X-CivicLoop-Transport-Scope"
_SCOPE = re.compile(r"scope_[A-Za-z0-9_-]{43}")
_POLICY = AdapterPolicy(MODEL_ALIAS, "", 0.1, 600)


class AdmissionConflict(RuntimeError):
    pass


@dataclass
class Record:
    digest: str
    status: str = "running"
    cancelled: bool = False
    result: dict[str, Any] | None = field(default=None, repr=False)


def validate_envelope(body: dict[str, Any]) -> tuple[dict[str, Any], float]:
    if set(body) != {"request", "expires_at"}:
        raise ValueError
    request = body["request"]
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS - {"capability_token"}:
        raise ValueError
    # Reuse the frozen launch validation without receiving or retaining authority.
    validate_run_request(request | {"capability_token": "cap_" + "x" * 43}, policy=_POLICY)
    expiry = body["expires_at"]
    if not isinstance(expiry, str) or len(expiry) > 40:
        raise ValueError
    date = datetime.fromisoformat(expiry)
    if date.tzinfo is None:
        raise ValueError
    remaining = (date - datetime.now(UTC)).total_seconds()
    if remaining > request["budgets"]["timeout_seconds"]:
        raise ValueError
    return request, time.monotonic() + remaining


class ControllerService(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        *args,
        service_token: str,
        controller: ProcessController,
        maximum_records: int = 10_000,
        **kwargs,
    ):
        if not 16 <= len(service_token) <= 256 or not 1 <= maximum_records <= 100_000:
            raise ValueError("Invalid controller configuration")
        self.service_token = service_token
        self.controller = controller
        self.maximum_records = maximum_records
        self.records: dict[str, Record] = {}
        self.active: str | None = None
        self.lock = threading.RLock()
        self.handler_slots = threading.BoundedSemaphore(16)
        self.handler_lock = threading.Lock()
        self.handler_timers = {}
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.handler_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return

        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(3, expire)
        timer.daemon = True
        with self.handler_lock:
            self.handler_timers[request] = timer
        timer.start()
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release_handler(request)
            raise

    def _release_handler(self, request):
        with self.handler_lock:
            timer = self.handler_timers.pop(request)
        timer.cancel()
        self.handler_slots.release()

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release_handler(request)

    def handle_error(self, request, client_address):
        pass  # Never emit request-bearing tracebacks.

    def payload(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.records[run_id]
            payload = {"run_id": run_id, "status": record.status}
            if record.result is not None:
                payload["result"] = record.result
            return payload

    def start(self, envelope: dict[str, Any], scope: str) -> str:
        if _SCOPE.fullmatch(scope) is None:
            raise ValueError
        request, deadline = validate_envelope(envelope)
        run_id = map_upstream_result(request, {})["run_id"]
        digest = hashlib.sha256((json.dumps(envelope, sort_keys=True) + scope).encode()).hexdigest()
        with self.lock:
            prior = self.records.get(run_id)
            if prior is not None:
                if not hmac.compare_digest(prior.digest, digest):
                    raise AdmissionConflict
                return run_id
            if deadline <= time.monotonic():
                raise ValueError
            if (
                self.active is not None
                or self.controller.quarantined
                or len(self.records) >= self.maximum_records
            ):
                raise AdmissionConflict
            self.records[run_id] = Record(digest)
            self.active = run_id
            try:
                threading.Thread(
                    target=self._execute, args=(run_id, request, scope, deadline), daemon=True
                ).start()
            except Exception:
                self.records[run_id].status = "failed"
                self.active = None
                raise RuntimeError from None
        return run_id

    def _execute(self, run_id, request, scope, deadline):
        result = None
        try:
            self.controller.admit(request, scope_token=scope, deadline=deadline)
            with self.lock:
                cancelled = self.records[run_id].cancelled
            if not cancelled:
                result = self.controller.execute(request, scope_token=scope, deadline=deadline)
                # Only the closed adapter result shape may cross this boundary.
                from deploy.hermes.controller_client import validate_result

                validate_result(request, result)
        except Exception:
            result = None
        finally:
            try:
                self.controller.stop(run_id)
            except Exception:
                # Failed admission can legitimately have no controller record.
                # A live child on an ambiguous path must prevent reuse.
                if getattr(self.controller, "_active", None) is not None:
                    self.controller.quarantined = True
            with self.lock:
                record = self.records[run_id]
                if record.cancelled:
                    record.status = "cancelled"
                elif result is not None and not self.controller.quarantined:
                    record.status = result["status"]
                    record.result = result
                else:
                    record.status = "failed"
                self.active = None

    def cancel(self, run_id: str):
        with self.lock:
            record = self.records[run_id]
            if self.active != run_id:
                return
            record.cancelled = True
            record.status = "cancelling"
        try:
            self.controller.stop(run_id)
        except Exception:
            pass  # The owned thread completes admission/cleanup before releasing its slot.


class Handler(BaseHTTPRequestHandler):
    server: ControllerService

    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, format, *args):
        pass

    def _header(self, name):
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else ""

    def _send(self, status, body):
        raw = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def _error(self, status):
        self._send(status, {"status": "unavailable"})

    def _auth(self):
        return hmac.compare_digest(
            self._header("Authorization").encode(), f"Bearer {self.server.service_token}".encode()
        )

    def _run_id(self, cancel=False):
        pattern = re.escape(RUN_PATH) + r"/([0-9a-f-]{36})" + ("/cancel" if cancel else "")
        match = re.fullmatch(pattern, self.path)
        return match[1] if match else None

    def do_GET(self):  # noqa: N802
        if self.path in {"/health/live", "/health/ready"}:
            unavailable = self.path.endswith("ready") and self.server.controller.quarantined
            self._send(
                503 if unavailable else 200, {"status": "unavailable" if unavailable else "ok"}
            )
            return
        if not self._auth():
            self._error(401)
            return
        run_id = self._run_id()
        try:
            self._send(200, self.server.payload(run_id))
        except KeyError:
            self._error(404)

    def _body(self):
        length = self._header("Content-Length")
        if (
            self.headers.get_all("Transfer-Encoding")
            or not length.isascii()
            or not length.isdigit()
            or len(length) > 8
            or not 0 < int(length) <= MAX_BODY_BYTES
        ):
            raise ValueError
        deadline = time.monotonic() + 3
        raw = bytearray()
        while len(raw) < int(length):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError
            self.connection.settimeout(remaining)
            chunk = self.rfile.read1(int(length) - len(raw))
            if not chunk:
                raise ValueError
            raw.extend(chunk)
        return _json(bytes(raw))

    def do_POST(self):  # noqa: N802
        if not self._auth():
            self._error(401)
            return
        try:
            body = self._body()
            if self.path == RUN_PATH:
                run_id = self.server.start(body, self._header(SCOPE_HEADER))
            else:
                run_id = self._run_id(cancel=True)
                if run_id is None:
                    self._error(404)
                    return
                if body:
                    raise ValueError
                self.server.cancel(run_id)
            self._send(202, self.server.payload(run_id))
        except KeyError:
            self._error(404)
        except AdmissionConflict:
            self._error(409)
        except Exception:
            self._error(400)


def main():
    token = _read_token(os.environ["HERMES_CONTROLLER_TOKEN_FILE"], label="controller identity")
    shim_token = _read_token(os.environ["HERMES_SHIM_CLIENT_TOKEN_FILE"], label="shim identity")
    controller = ProcessController(
        shim_base_url=os.environ["HERMES_SHIM_URL"], shim_token=shim_token
    )
    server = ControllerService(
        ("0.0.0.0", int(os.getenv("PORT", "8642"))),
        Handler,
        service_token=token,
        controller=controller,
    )
    try:
        server.serve_forever()
    finally:
        if server.active is not None:
            server.cancel(server.active)
        server.server_close()


if __name__ == "__main__":
    main()
