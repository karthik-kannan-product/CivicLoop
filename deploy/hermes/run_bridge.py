"""A non-reusable loopback boundary for one Hermes run.

The transport scope lives here, never in the child environment or config.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from deploy.hermes.transport import (
    MAX_BODY_BYTES,
    SCOPE_HEADER,
    _contains_authority,
    _DeadlineHTTPConnection,
    _DeadlineHTTPSConnection,
    _DeadlineIO,
)

_SCOPE = re.compile(r"scope_[A-Za-z0-9_-]{43}")
_ROUTES = frozenset({"/mcp", "/v1/chat/completions"})


class BridgeRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Hermes bridge request rejected")


class _BoundedHTTP:
    """One cancellable physical request at a time; late workers block reuse."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._flights: set[_DeadlineIO] = set()
        self._closed = False

    def request(
        self,
        *,
        url: str,
        method: str,
        raw: bytes | None,
        headers: dict[str, str],
        deadline: float,
    ) -> tuple[int, bytes]:
        io = _DeadlineIO(deadline)
        with self._condition:
            if self._closed or self._flights or time.monotonic() >= deadline:
                raise BridgeRejected()
            self._flights.add(io)
        done = threading.Event()
        result: list[tuple[int, bytes]] = []

        def perform() -> None:
            connection = None
            try:
                parsed = urlsplit(url)
                connection_type = (
                    _DeadlineHTTPSConnection
                    if parsed.scheme == "https"
                    else _DeadlineHTTPConnection
                )
                connection = connection_type(
                    parsed.hostname, parsed.port, timeout=io.remaining(), io=io
                )
                connection.request(method, parsed.path or "/", body=raw, headers=headers)
                with connection.getresponse() as response:
                    content = response.read(MAX_BODY_BYTES + 1)
                    io.remaining()
                    result.append((response.status, content))
            except Exception:
                pass  # Never retain or expose dependency bodies or errors.
            finally:
                if connection is not None:
                    connection.close()
                with self._condition:
                    self._flights.discard(io)
                    self._condition.notify_all()
                done.set()

        try:
            threading.Thread(target=perform, daemon=True).start()
        except Exception:
            with self._condition:
                self._flights.discard(io)
                self._condition.notify_all()
            raise BridgeRejected() from None
        try:
            if not done.wait(max(0, deadline - time.monotonic())) or not result:
                raise BridgeRejected()
            io.remaining()
            return result[0]
        finally:
            io.cancel()

    def close_admissions(self) -> None:
        with self._condition:
            self._closed = True
            flights = tuple(self._flights)
        for io in flights:
            io.cancel()

    def drain(self, timeout: float) -> bool:
        end = time.monotonic() + max(0, timeout)
        with self._condition:
            while self._flights:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self.bridge._accept_socket(request):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.bridge._release_socket(request)
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.bridge._release_socket(request)

    def handle_error(self, request: object, client_address: object) -> None:
        # BaseHTTPServer otherwise prints request-bearing tracebacks.
        return


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BridgeRejected()
        result[key] = value
    return result


def _json(raw: bytes) -> dict[str, object]:
    if not 0 < len(raw) <= MAX_BODY_BYTES:
        raise BridgeRejected()
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, RecursionError):
        raise BridgeRejected() from None
    if not isinstance(parsed, dict):
        raise BridgeRejected()
    return parsed


@dataclass(slots=True)
class RunBridge:
    scope_token: str = field(repr=False)
    shim_base_url: str
    hermes_token: str = field(repr=False)
    deadline: float
    shim_token: str | None = field(default=None, repr=False)
    model_alias: str = "civicloop-default"
    _condition: threading.Condition = field(init=False, repr=False)
    _server: ThreadingHTTPServer | None = field(init=False, default=None, repr=False)
    _thread: threading.Thread | None = field(init=False, default=None, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _accepted: dict[socket.socket, threading.Timer] = field(init=False, repr=False)
    _io: _BoundedHTTP = field(init=False, repr=False)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {
            "scope_token",
            "shim_base_url",
            "hermes_token",
            "shim_token",
            "deadline",
            "model_alias",
        } and hasattr(self, name):
            raise AttributeError("Bridge authority is immutable")
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if self.shim_token is None:
            object.__setattr__(self, "shim_token", secrets.token_urlsafe(32))
        parsed = urlsplit(self.shim_base_url)
        if (
            _SCOPE.fullmatch(self.scope_token) is None
            or parsed.scheme != "http"
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or len(self.hermes_token) < 16
            or len(self.shim_token) < 16
            or self.shim_token == self.hermes_token
            or self.deadline <= time.monotonic()
        ):
            raise ValueError("Hermes bridge configuration is invalid")
        self._condition = threading.Condition()
        self._accepted = {}
        self._io = _BoundedHTTP()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise BridgeRejected()
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> str:
        with self._condition:
            if self._closed:
                raise BridgeRejected()
            if self._server is None:
                server = _BridgeServer(("127.0.0.1", 0), _Handler)
                server.bridge = self
                self._server = server
                self._thread = threading.Thread(
                    target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
                )
                self._thread.start()
            return self.base_url

    def _accept_socket(self, sock: socket.socket) -> bool:
        with self._condition:
            remaining = self.deadline - time.monotonic()
            if self._closed or remaining <= 0 or len(self._accepted) >= 16:
                return False
            timer = threading.Timer(remaining, self._abort_socket, args=(sock,))
            timer.daemon = True
            self._accepted[sock] = timer
            sock.settimeout(remaining)
            timer.start()
            return True

    @staticmethod
    def _abort_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def _release_socket(self, sock: socket.socket) -> None:
        with self._condition:
            timer = self._accepted.pop(sock, None)
            if timer is not None:
                timer.cancel()
            self._condition.notify_all()

    def forward(
        self, path: str, raw: bytes, *, caller_headers: dict[str, str] | None = None
    ) -> bytes:
        if path not in _ROUTES or not isinstance(raw, bytes):
            raise BridgeRejected()
        if caller_headers and any(
            name.lower() not in {"content-type", "accept"} for name in caller_headers
        ):
            raise BridgeRejected()
        body = _json(raw)
        authorities = (self.scope_token, self.hermes_token, self.shim_token)
        if _contains_authority(body, authorities):
            raise BridgeRejected()
        if path != "/mcp" and (
            body.get("model") != self.model_alias
            or type(body.get("max_tokens")) is not int
            or not 1 <= body["max_tokens"] <= 100_000
            or body.get("stream", False) is not False
            or any(key in body for key in ("base_url", "api_key", "provider", "url"))
        ):
            raise BridgeRejected()
        with self._condition:
            if self._closed or time.monotonic() >= self.deadline:
                raise BridgeRejected()
        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                SCOPE_HEADER: self.scope_token,
                "Authorization": f"Bearer {self.shim_token}",
            }
            status, result = self._io.request(
                url=self.shim_base_url.rstrip("/") + path,
                method="POST",
                raw=raw,
                headers=headers,
                deadline=min(self.deadline, time.monotonic() + 10),
            )
            if status not in ({200, 202} if path == "/mcp" else {200}):
                raise BridgeRejected()
            parsed = _json(result)
            if _contains_authority(parsed, authorities):
                raise BridgeRejected()
            if time.monotonic() >= self.deadline:
                raise BridgeRejected()
            return result
        except (OSError, ValueError, urllib.error.URLError):
            raise BridgeRejected() from None

    def close_admissions(self) -> None:
        with self._condition:
            self._closed = True
            sockets = tuple(self._accepted)
        self._io.close_admissions()
        for sock in sockets:
            self._abort_socket(sock)

    def drain(self, timeout: float) -> bool:
        end = time.monotonic() + max(0, timeout)
        with self._condition:
            while self._accepted:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return self._io.drain(max(0, end - time.monotonic()))

    def close(self) -> bool:
        self.close_admissions()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
                return not self._thread.is_alive() and self.drain(0)
        return self.drain(0)


class _Handler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, *args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        bridge: RunBridge = self.server.bridge
        status = 400
        result = b'{"error":"bridge_rejected"}'
        try:
            supplied = self.headers.get_all("Authorization", [])
            if len(supplied) != 1 or not hmac.compare_digest(
                supplied[0], f"Bearer {bridge.hermes_token}"
            ):
                status = 401
                raise BridgeRejected()
            hosts = self.headers.get_all("Host", [])
            if hosts != [f"127.0.0.1:{self.server.server_port}"]:
                raise BridgeRejected()
            length = self.headers.get_all("Content-Length", [])
            if (
                len(length) != 1
                or not length[0].isascii()
                or not length[0].isdigit()
                or not 0 < int(length[0]) <= MAX_BODY_BYTES
                or self.headers.get_all("Transfer-Encoding")
            ):
                raise BridgeRejected()
            forbidden = {
                "x-civicloop-transport-scope",
                "x-civicloop-capability",
                "x-civicloop-budget-assertion",
                "x-forwarded-host",
                "forwarded",
            }
            if any(name.lower() in forbidden for name in self.headers):
                raise BridgeRejected()
            raw = self.rfile.read(int(length[0]))
            result = bridge.forward(self.path, raw)
            status = 200
        except BridgeRejected:
            pass
        except Exception:
            status = 502
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(result)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(result)
