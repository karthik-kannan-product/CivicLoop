"""Volatile, fail-closed authority boundary between Hermes and CivicLoop services.

Only trusted control traffic may install a binding. Data traffic never selects
capabilities or signing keys. No request, response, header, or exception is logged.
"""

from __future__ import annotations

import hmac
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import urlsplit

from deploy.hermes.transport_contracts import ScopeBinding, scope_digest
from deploy.litellm.assertions import issue_budget_assertion

CONTROL_PATH = "/internal/control/v1/scopes"
REVOKE_PATH = CONTROL_PATH + "/revoke"
INFERENCE_PATH = "/v1/chat/completions"
SCOPE_HEADER = "X-CivicLoop-Transport-Scope"
MAX_BODY_BYTES = 262_144
_SCOPE = re.compile(r"scope_[A-Za-z0-9_-]{43}")
_AUTHORITY = re.compile(
    r"(?:scope_|cap_)[A-Za-z0-9_-]{43,125}|[A-Za-z0-9_-]{80,}\.[A-Za-z0-9_-]{43}"
)


class TransportError(RuntimeError):
    def __init__(self, message: str = "Transport authorization rejected", *, status: int = 403):
        super().__init__(message)
        self.status = status


def _digest(token: str) -> str:
    if not isinstance(token, str) or _SCOPE.fullmatch(token) is None:
        raise TransportError()
    return scope_digest(token)


def binding_payload(binding: ScopeBinding) -> dict[str, str | int]:
    """Control-plane body: authority is carried exclusively in dedicated headers."""
    value = binding.safe_metadata()
    del value["scope_digest"]
    return value


def _validate_binding(binding: ScopeBinding, now: datetime) -> ScopeBinding:
    try:
        return ScopeBinding.from_dict(
            binding_payload(binding) | {"capability": binding.capability}, now=now
        )
    except ValueError, TypeError, AttributeError, OverflowError:
        raise TransportError() from None


@dataclass(slots=True)
class _Scope:
    binding: ScopeBinding | None = field(repr=False)
    inferences: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_microusd: int = 0


class ScopeRegistry:
    """Bounded process-local leases and permanent process-lifetime tombstones.

    Capacity exhaustion requires an operator restart, which invalidates all
    scopes. Tombstones are never evicted to make room for replayed authority.
    The same lock serializes revocation with the final authorization and send.
    """

    def __init__(
        self,
        *,
        maximum_active: int = 1,
        maximum_records: int = 100_000,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lease_active: Callable[[ScopeBinding], bool] | None = None,
    ) -> None:
        for value in (maximum_active, maximum_records):
            if type(value) is not int or not 1 <= value <= 1_000_000:
                raise ValueError("Transport capacity is invalid")
        if maximum_active > maximum_records:
            raise ValueError("Transport capacity is invalid")
        self.maximum_active = maximum_active
        self.maximum_records = maximum_records
        self.clock = clock
        self.lease_active = lease_active
        self._lock = threading.RLock()
        self._scopes: dict[str, _Scope] = {}
        self._runs: set[str] = set()

    def _expire(self) -> None:
        now = self.clock()
        for entry in self._scopes.values():
            if entry.binding is not None and entry.binding.expires_at <= now:
                entry.binding = None

    def register(self, *, token: str, binding: ScopeBinding) -> None:
        digest = _digest(token)
        with self._lock:
            self._expire()
            checked = _validate_binding(binding, self.clock())
            if digest in self._scopes:
                if self._scopes[digest].binding == checked:
                    return
                raise TransportError()
            run_digest = scope_digest(checked.run_id)
            if run_digest in self._runs:
                raise TransportError()
            if (
                len(self._scopes) >= self.maximum_records
                or sum(entry.binding is not None for entry in self._scopes.values())
                >= self.maximum_active
            ):
                raise TransportError("Transport capacity exhausted", status=503)
            self._scopes[digest] = _Scope(checked)
            self._runs.add(run_digest)

    def authorize(self, *, token: str) -> ScopeBinding:
        digest = _digest(token)
        with self._lock:
            entry = self._scopes.get(digest)
            if entry is None or entry.binding is None:
                raise TransportError()
            active = entry.binding.expires_at > self.clock()
            if active and self.lease_active is not None:
                try:
                    active = self.lease_active(entry.binding) is True
                except Exception:
                    active = False
            if not active:
                entry.binding = None
                raise TransportError()
            return entry.binding

    @contextmanager
    def forward(self, *, token: str) -> Iterator[ScopeBinding]:
        # Revocation waits for an already authorized, bounded forward to finish;
        # no forward can begin after revoke returns, even with a stale binding.
        with self._lock:
            yield self.authorize(token=token)

    def reserve_inference(
        self,
        *,
        token: str,
        input_token_ceiling: int,
        output_token_ceiling: int,
        cost_ceiling_microusd: int,
    ) -> str:
        values = (input_token_ceiling, output_token_ceiling, cost_ceiling_microusd)
        if any(type(value) is not int or value <= 0 for value in values):
            raise TransportError("Transport budget exhausted", status=429)
        with self._lock:
            binding = self.authorize(token=token)
            entry = self._scopes[_digest(token)]
            if (
                entry.inferences + 1 > binding.max_inferences
                or entry.input_tokens + input_token_ceiling > binding.max_input_tokens
                or entry.output_tokens + output_token_ceiling > binding.max_output_tokens
                or entry.cost_microusd + cost_ceiling_microusd > binding.max_cost_microusd
            ):
                raise TransportError("Transport budget exhausted", status=429)
            nonce = str(uuid.uuid4())
            entry.inferences += 1
            entry.input_tokens += input_token_ceiling
            entry.output_tokens += output_token_ceiling
            entry.cost_microusd += cost_ceiling_microusd
            return nonce

    def revoke(self, *, token: str) -> None:
        digest = _digest(token)
        with self._lock:
            entry = self._scopes.get(digest)
            if entry is not None:
                entry.binding = None
            else:
                # A timed-out registration may still be queued behind revoke.
                if len(self._scopes) >= self.maximum_records:
                    raise TransportError("Transport capacity exhausted", status=503)
                self._scopes[digest] = _Scope(None)


@dataclass(frozen=True, slots=True)
class TransportPricing:
    input_microusd_per_million: int
    output_microusd_per_million: int

    def __post_init__(self) -> None:
        for rate in (self.input_microusd_per_million, self.output_microusd_per_million):
            if type(rate) is not int or not 1 <= rate <= 10**12:
                raise ValueError("Transport pricing is invalid")

    def worst_case_cost(self, *, input_bytes: int, max_output_tokens: int) -> int:
        if any(type(value) is not int or value <= 0 for value in (input_bytes, max_output_tokens)):
            raise ValueError("Transport reservation is invalid")
        numerator = (
            input_bytes * self.input_microusd_per_million
            + max_output_tokens * self.output_microusd_per_million
        )
        return (numerator + 999_999) // 1_000_000


class TransportClient(Protocol):
    def register_scope(self, *, token: str, binding: ScopeBinding) -> None: ...

    def revoke_scope(self, *, token: str) -> None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _opener() -> urllib.request.OpenerDirector:
    # Never route internal credentials through environment-configured proxies.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Transport endpoint is invalid")
    return value


def _credential(value: str) -> bool:
    return (
        isinstance(value, str)
        and 16 <= len(value) <= 512
        and all(33 <= ord(char) <= 126 for char in value)
    )


class HTTPTransportClient:
    def __init__(self, *, base_url: str, control_token: str, timeout: float = 10) -> None:
        self.base_url = _url(base_url).rstrip("/")
        if not _credential(control_token) or not 0 < timeout <= 30:
            raise ValueError("Transport control configuration is invalid")
        self.control_token = control_token
        self.timeout = timeout
        self.opener = _opener()

    def _request(self, path: str, token: str, binding: ScopeBinding | None = None) -> None:
        _digest(token)
        headers = {
            "Authorization": f"Bearer {self.control_token}",
            SCOPE_HEADER: token,
            "Content-Type": "application/json",
        }
        body = {}
        if binding is not None:
            headers["X-CivicLoop-Capability"] = binding.capability
            body = binding_payload(binding)
        request = urllib.request.Request(
            self.base_url + path, headers=headers, data=json.dumps(body).encode(), method="POST"
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.read(65) != b'{"status":"ok"}':
                    raise TransportError("Transport dependency unavailable", status=502)
        except OSError, ValueError, urllib.error.URLError:
            raise TransportError("Transport dependency unavailable", status=502) from None

    def register_scope(self, *, token: str, binding: ScopeBinding) -> None:
        self._request(CONTROL_PATH, token, binding)

    def revoke_scope(self, *, token: str) -> None:
        self._request(REVOKE_PATH, token)


def _contains_authority(value: object, secrets: tuple[str, ...]) -> bool:
    if isinstance(value, str):
        return bool(_AUTHORITY.search(value)) or any(secret in value for secret in secrets)
    if isinstance(value, dict):
        return any(
            _contains_authority(key, secrets) or _contains_authority(item, secrets)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_authority(item, secrets) for item in value)
    return False


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Transport JSON is invalid")
        value[key] = item
    return value


class _DeadlineIO:
    """Abort network I/O independently of a peer's socket activity.

    The caller waits only until the monotonic deadline, including DNS/connect.
    Cancellation closes attached sockets. A late DNS/connect completion must
    attach its socket before sending any HTTP bytes, and is rejected if expired.
    """

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self._lock = threading.Lock()
        self._cancelled = False
        self._socket: socket.socket | None = None

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if self._cancelled or remaining <= 0:
            raise TimeoutError("Transport deadline exceeded")
        return remaining

    @staticmethod
    def _close(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    def attach(self, sock: socket.socket) -> None:
        with self._lock:
            try:
                self.remaining()
            except TimeoutError:
                self._close(sock)
                raise
            self._socket = sock

    def connect(self, address: Any, timeout: float, source_address: Any = None) -> socket.socket:
        sock = socket.create_connection(address, min(timeout, self.remaining()), source_address)
        self.attach(sock)
        return sock

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._socket is not None:
                self._close(self._socket)
                self._socket = None


class _DeadlineConnection:
    def __init__(self, *args: Any, io: _DeadlineIO, **kwargs: Any) -> None:
        self.io = io
        super().__init__(*args, **kwargs)
        self._create_connection = io.connect

    def connect(self) -> None:
        self.io.remaining()
        super().connect()
        # HTTPS may replace the raw socket during its handshake. Re-check before
        # HTTPConnection sends any buffered headers on the resulting socket.
        self.io.attach(self.sock)


class _DeadlineHTTPConnection(_DeadlineConnection, HTTPConnection):
    pass


class _DeadlineHTTPSConnection(_DeadlineConnection, HTTPSConnection):
    pass


class TransportServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        registry: ScopeRegistry,
        control_token: str,
        client_token: str,
        mcp_token: str,
        gateway_token: str,
        assertion_key: bytes,
        mcp_url: str,
        gateway_url: str,
        pricing: TransportPricing,
        enabled: Callable[[], bool] = lambda: False,
        timeout: float = 10,
    ) -> None:
        tokens = (control_token, client_token, mcp_token, gateway_token)
        if (
            not all(_credential(token) for token in tokens)
            or len(set(tokens)) != 4
            or not isinstance(assertion_key, bytes)
            or len(assertion_key) < 32
            or not 0 < timeout <= 30
        ):
            raise ValueError("Transport credentials or timeout are invalid")
        self.registry = registry
        self.control_token = control_token
        self.client_token = client_token
        self.mcp_token = mcp_token
        self.gateway_token = gateway_token
        self.assertion_key = assertion_key
        self.mcp_url = _url(mcp_url)
        self.gateway_url = _url(gateway_url)
        self.pricing = pricing
        self.enabled = enabled
        self.timeout = timeout
        self._io_slot = threading.BoundedSemaphore(1)
        super().__init__(address, _Handler)

    def upstream_request(
        self,
        *,
        url: str,
        raw: bytes,
        headers: dict[str, str],
        deadline: float,
    ) -> tuple[int, bytes]:
        # A stalled resolver may outlive cancellation at OS level. Keep the slot
        # occupied until that worker exits, failing closed rather than spawning
        # more workers or letting late connects send cancelled authority.
        if not self._io_slot.acquire(blocking=False):
            raise TransportError(status=502)
        io = _DeadlineIO(deadline)
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
                connection.request("POST", parsed.path or "/", body=raw, headers=headers)
                with connection.getresponse() as response:
                    content = response.read(MAX_BODY_BYTES + 1)
                    io.remaining()
                    result.append((response.status, content))
            except Exception:
                pass  # Content-free failure; never retain/log dependency errors.
            finally:
                if connection is not None:
                    connection.close()
                self._io_slot.release()
                done.set()

        worker = threading.Thread(target=perform, daemon=True)
        try:
            worker.start()
        except Exception:
            self._io_slot.release()
            raise TransportError(status=502) from None
        try:
            if not done.wait(max(0, deadline - time.monotonic())) or not result:
                raise TransportError(status=502)
            io.remaining()
            return result[0]
        finally:
            io.cancel()

    def handle_error(self, request: Any, client_address: Any) -> None:
        # Base server tracebacks may contain body/header locals via instrumentation.
        return


class _Handler(BaseHTTPRequestHandler):
    server: TransportServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(self.server.timeout)

    def log_message(self, *args: Any) -> None:
        return

    def _send(self, status: int, raw: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._problem(code)

    def _problem(self, status: int) -> None:
        code = {
            401: "transport_unauthorized",
            403: "transport_rejected",
            429: "transport_budget_exhausted",
            502: "transport_dependency_unavailable",
            503: "transport_unavailable",
        }.get(status, "transport_request_rejected")
        self._send(
            status,
            json.dumps(
                {
                    "error": {
                        "code": code,
                        "message": "The transport request could not be completed.",
                    }
                },
                separators=(",", ":"),
            ).encode(),
        )

    def _header(self, name: str) -> str:
        values = self.headers.get_all(name, [])
        return values[0] if len(values) == 1 else ""

    def _body(self) -> tuple[bytes, dict[str, Any]]:
        length = self._header("Content-Length")
        if (
            self.headers.get_all("Transfer-Encoding")
            or not length.isascii()
            or not length.isdigit()
            or len(length) > 8
            or not 0 < int(length) <= MAX_BODY_BYTES
        ):
            raise TransportError(status=400)
        raw = self.rfile.read(int(length))
        if len(raw) != int(length):
            raise TransportError(status=400)
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(body, dict):
            raise TransportError(status=400)
        return raw, body

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {CONTROL_PATH, REVOKE_PATH, "/mcp", INFERENCE_PATH}:
            self._problem(404)
            return
        control = self.path in {CONTROL_PATH, REVOKE_PATH}
        expected = self.server.control_token if control else self.server.client_token
        if not hmac.compare_digest(
            self._header("Authorization").encode(), f"Bearer {expected}".encode()
        ):
            self._problem(401)
            return
        token = self._header(SCOPE_HEADER)
        try:
            _digest(token)
            if not control:
                self.server.registry.authorize(token=token)
            raw, body = self._body()
            if control:
                self._control(token, body)
            else:
                self._forward(token, raw, body)
        except TransportError as error:
            self._problem(error.status)
        except ValueError, UnicodeError, RecursionError:
            self._problem(400)
        except Exception:
            self._problem(502)

    def _control(self, token: str, body: dict[str, Any]) -> None:
        if self.path == REVOKE_PATH:
            if body:
                raise TransportError(status=400)
            self.server.registry.revoke(token=token)
        else:
            if "capability" in body or _contains_authority(body, (token,)):
                raise TransportError(status=400)
            binding = ScopeBinding.from_dict(
                body | {"capability": self._header("X-CivicLoop-Capability")},
                now=self.server.registry.clock(),
            )
            self.server.registry.register(token=token, binding=binding)
        self._send(200, b'{"status":"ok"}')

    def _forward(self, token: str, raw: bytes, body: dict[str, Any]) -> None:
        with self.server.registry.forward(token=token) as binding:
            if self.server.enabled() is not True:
                self.server.registry.revoke(token=token)
                raise TransportError()
            secrets = (
                token,
                binding.capability,
                self.server.control_token,
                self.server.client_token,
                self.server.mcp_token,
                self.server.gateway_token,
            )
            if _contains_authority(body, secrets):
                raise TransportError(status=400)
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.path == "/mcp":
                url = self.server.mcp_url
                headers["Authorization"] = f"Bearer {self.server.mcp_token}"
                headers["X-CivicLoop-Capability"] = binding.capability
            else:
                maximum = body.get("max_tokens")
                if (
                    body.get("model") != binding.model_alias
                    or type(maximum) is not int
                    or not 1 <= maximum <= 100_000
                    or body.get("stream", False) is not False
                ):
                    raise TransportError(status=400)
                nonce = self.server.registry.reserve_inference(
                    token=token,
                    input_token_ceiling=len(raw),
                    output_token_ceiling=maximum,
                    cost_ceiling_microusd=self.server.pricing.worst_case_cost(
                        input_bytes=len(raw), max_output_tokens=maximum
                    ),
                )
                assertion = issue_budget_assertion(
                    key=self.server.assertion_key,
                    run_id=binding.run_id,
                    model_alias=binding.model_alias,
                    token_ceiling=binding.max_output_tokens,
                    expires_at=min(
                        binding.expires_at, self.server.registry.clock() + timedelta(seconds=300)
                    ),
                    nonce=nonce,
                )
                secrets += (assertion,)
                url = self.server.gateway_url
                headers["Authorization"] = f"Bearer {self.server.gateway_token}"
                headers["X-CivicLoop-Budget-Assertion"] = assertion
            remaining = (binding.expires_at - self.server.registry.clock()).total_seconds()
            if remaining <= 0:
                raise TransportError()
            deadline = time.monotonic() + min(self.server.timeout, remaining)
            try:
                status, content = self.server.upstream_request(
                    url=url,
                    raw=raw,
                    headers=headers,
                    deadline=deadline,
                )
                accepted = {200, 202} if self.path == "/mcp" else {200}
                if status not in accepted or len(content) > MAX_BODY_BYTES:
                    raise TransportError(status=502)
                parsed = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object)
                if not isinstance(parsed, dict) or _contains_authority(parsed, secrets):
                    raise TransportError(status=502)
                if time.monotonic() >= deadline:
                    raise TransportError(status=502)
            except Exception:
                raise TransportError("Transport dependency unavailable", status=502) from None
            # Header allowlisting drops hop-by-hop and Connection-nominated fields,
            # upstream cookies, credentials, server diagnostics, and tracing data.
        # Downstream clients must not extend the authority/revocation lock by
        # trickling their reads. Upstream I/O and validation have already ended.
        self._send(status, content)
