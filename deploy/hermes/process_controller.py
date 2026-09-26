"""Own exactly one short-lived Hermes child and its immutable loopback bridge."""

from __future__ import annotations

import copy
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from deploy.hermes.run_bridge import RunBridge
from deploy.hermes.transport import TransportClient, _opener
from deploy.hermes.transport_contracts import scope_digest

_ROOT = Path(__file__).resolve().parent
_CHILD = _ROOT / "run_child.py"
_CONFIG = _ROOT / "config.yaml"


class ControllerUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Hermes controller unavailable")


@dataclass(frozen=True, slots=True)
class ControllerRun:
    run_id: str
    status: str


@dataclass(slots=True)
class _OwnedRun:
    request_digest: str
    scope_digest: str
    scope_token: str = field(repr=False)
    run_id: str = ""
    child: Any = field(default=None, repr=False)
    bridge: RunBridge | None = field(default=None, repr=False)
    home: Path | None = field(default=None, repr=False)
    deadline: float = 0
    url: str = ""
    api_key: str = field(default="", repr=False)


def _local_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _create_home() -> Path:
    root = Path(tempfile.gettempdir()).resolve()
    for _ in range(3):
        path = root / ("civicloop-hermes-run-" + secrets.token_hex(16))
        try:
            path.mkdir()
        except FileExistsError:
            continue
        if os.name != "nt":
            path.chmod(0o700)
        return path
    raise ControllerUnavailable()


def _cleanup_home(path: Path) -> None:
    root = Path(tempfile.gettempdir()).resolve()
    resolved = path.resolve()
    if resolved.parent != root or not resolved.name.startswith("civicloop-hermes-run-"):
        raise ControllerUnavailable()
    shutil.rmtree(resolved)


def _ready(child: Any, url: str) -> bool:
    if child is not None and child.poll() is not None:
        return False
    try:
        with _opener().open(url + "/health", timeout=0.2) as response:
            raw = response.read(1025)
            return (
                response.status == 200
                and len(raw) <= 1024
                and json.loads(raw).get("status") in {"ok", "healthy", "ready"}
            )
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _child_env(home: Path, port: int, api_key: str) -> dict[str, str]:
    # Explicit allowlist prevents inherited provider, proxy, and host credentials.
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    environment.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home),
            "API_SERVER_HOST": "127.0.0.1",
            "API_SERVER_PORT": str(port),
            "API_SERVER_MODEL_NAME": "civicloop-default",
            "API_SERVER_KEY": api_key,
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "HERMES_SAFE_MODE": "0",
        }
    )
    return environment


def _write_config(home: Path, bridge: RunBridge) -> None:
    config = copy.deepcopy(yaml.safe_load(_CONFIG.read_text(encoding="utf-8")))
    config["model"]["base_url"] = bridge.base_url + "/v1"
    config["model"]["default"] = "civicloop-default"
    config["mcp_servers"]["civicloop"]["url"] = bridge.base_url + "/mcp"
    config["mcp_servers"]["civicloop"]["headers"] = {
        "Authorization": f"Bearer {bridge.hermes_token}"
    }
    config["agent"]["disabled_toolsets"] = list(config["agent"]["disabled_toolsets"])
    target = home / "config.yaml"
    target.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)
    # Hermes's custom provider reads its non-authoritative bridge identity.
    env = home / ".env"
    env.write_text(f"OPENAI_API_KEY={bridge.hermes_token}\n", encoding="ascii")
    if os.name != "nt":
        env.chmod(0o600)


class ProcessController:
    def __init__(
        self,
        *,
        shim_base_url: str = "http://127.0.0.1:1",
        shim_token: str | None = None,
        child_factory: Callable[..., Any] = subprocess.Popen,
        readiness_probe: Callable[[Any, str], bool] = _ready,
        port_factory: Callable[[], int] = _local_port,
        transport_client: TransportClient | None = None,
        readiness_timeout: float = 5,
        termination_timeout: float = 1,
        drain_timeout: float = 1,
        maximum_records: int = 10_000,
    ) -> None:
        self.shim_base_url = shim_base_url
        self.shim_token = shim_token
        self.child_factory = child_factory
        self.readiness_probe = readiness_probe
        self.port_factory = port_factory
        self.transport_client = transport_client
        self.readiness_timeout = readiness_timeout
        self.termination_timeout = termination_timeout
        self.drain_timeout = drain_timeout
        if type(maximum_records) is not int or not 1 <= maximum_records <= 100_000:
            raise ValueError("Hermes controller capacity is invalid")
        self.maximum_records = maximum_records
        self.quarantined = False
        self._lock = threading.RLock()
        self._active: _OwnedRun | None = None
        self._history: dict[str, tuple[str, str, ControllerRun]] = {}
        self._quarantine_hold: list[_OwnedRun] = []

    def admit(self, request: dict[str, object], *, scope_token: str) -> ControllerRun:
        try:
            run_id = request.get("run_id")
            if run_id is None:
                run_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL, f"urn:civicloop:run:{request['correlation_id']}"
                    )
                )
            if not isinstance(run_id, str) or not 0 < len(run_id) <= 128:
                raise ValueError
            digest = scope_digest(scope_token)
            request_digest = scope_digest(json.dumps(request, sort_keys=True))
        except (KeyError, TypeError, ValueError):
            raise ControllerUnavailable() from None
        with self._lock:
            prior = self._history.get(run_id)
            if prior is not None:
                if prior[:2] != (request_digest, digest):
                    raise ControllerUnavailable()
                return prior[2] if self._active is None else self.status(run_id)
            if (
                self.quarantined
                or self._active is not None
                or len(self._history) >= self.maximum_records
            ):
                raise ControllerUnavailable()
            try:
                owned = self._start_owned_run(run_id, request, scope_token, request_digest, digest)
            except Exception:
                raise ControllerUnavailable() from None
            self._active = owned
            result = ControllerRun(run_id, "running")
            self._history[run_id] = (request_digest, digest, result)
            return result

    def _start_owned_run(
        self, run_id: str, request: dict[str, object], token: str, request_digest: str, digest: str
    ) -> _OwnedRun:
        timeout = request.get("timeout_seconds")
        if timeout is None and isinstance(request.get("budgets"), dict):
            timeout = request["budgets"].get("timeout_seconds")
        if type(timeout) is not int or not 0 < timeout <= 600:
            raise ControllerUnavailable()
        deadline = time.monotonic() + timeout
        bridge = RunBridge(
            scope_token=token,
            shim_base_url=self.shim_base_url,
            hermes_token=secrets.token_urlsafe(32),
            shim_token=self.shim_token,
            deadline=deadline,
        )
        home = _create_home()
        owned = _OwnedRun(
            request_digest, digest, token, run_id, bridge=bridge, home=home, deadline=deadline
        )
        try:
            bridge.start()
            home_path = home
            _write_config(home_path, bridge)
            port = self.port_factory()
            if type(port) is not int or not 1 <= port <= 65535:
                raise ControllerUnavailable()
            url = f"http://127.0.0.1:{port}"
            if _ready(None, url):
                raise ControllerUnavailable()
            api_key = secrets.token_urlsafe(32)
            owned.url = url
            owned.api_key = api_key
            owned.child = self.child_factory(
                [sys.executable, str(_CHILD)],
                env=_child_env(home_path, port, api_key),
                cwd=str(home_path),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            ready_by = min(deadline, time.monotonic() + self.readiness_timeout)
            while time.monotonic() < ready_by:
                if owned.child.poll() is not None:
                    raise ControllerUnavailable()
                if self.readiness_probe(owned.child, url):
                    return owned
                time.sleep(min(0.02, max(0, ready_by - time.monotonic())))
            raise ControllerUnavailable()
        except Exception:
            if not self._retire(owned):
                self.quarantined = True
                self._quarantine_hold.append(owned)
            raise

    def _retire(self, owned: _OwnedRun) -> bool:
        safe = True
        if owned.bridge is not None:
            try:
                owned.bridge.close_admissions()
            except Exception:
                safe = False
        if owned.child is not None:
            try:
                if owned.child.poll() is None:
                    try:
                        owned.child.terminate()
                        owned.child.wait(timeout=self.termination_timeout)
                    except Exception:
                        owned.child.kill()
                        owned.child.wait(timeout=self.termination_timeout)
            except Exception:
                safe = False
        if owned.bridge is not None:
            try:
                safe = owned.bridge.drain(self.drain_timeout) and safe
                owned.bridge.close()
            except Exception:
                safe = False
        if self.transport_client is not None:
            try:
                self.transport_client.revoke_scope(token=owned.scope_token)
            except Exception:
                safe = False
        if safe and owned.home is not None:
            try:
                _cleanup_home(owned.home)
            except OSError:
                safe = False
        return safe

    def status(self, run_id: str) -> ControllerRun:
        with self._lock:
            prior = self._history.get(run_id)
            if prior is None:
                raise ControllerUnavailable()
            if self._active is None or self._active.run_id != run_id:
                return prior[2]
            owned = self._active
            exit_code = owned.child.poll()
            if exit_code is None and time.monotonic() < owned.deadline:
                return ControllerRun(run_id, "running")
            # A clean child exit is not proof that a Hermes run completed.
            state = "failed"
            if exit_code is None:
                state = "cancelled"
            safe = self._retire(owned)
            self.quarantined |= not safe
            if not safe:
                self._quarantine_hold.append(owned)
            self._active = None
            result = ControllerRun(run_id, state if safe else "failed")
            self._history[run_id] = (prior[0], prior[1], result)
            return result

    def stop(self, run_id: str) -> ControllerRun:
        with self._lock:
            prior = self._history.get(run_id)
            if prior is None:
                raise ControllerUnavailable()
            if self._active is None or self._active.run_id != run_id:
                return prior[2]
            safe = self._retire(self._active)
            self.quarantined |= not safe
            if not safe:
                self._quarantine_hold.append(self._active)
            self._active = None
            result = ControllerRun(run_id, "cancelled" if safe else "failed")
            self._history[run_id] = (prior[0], prior[1], result)
            return result

    def _finish(self, run_id: str, state: str) -> None:
        with self._lock:
            prior = self._history[run_id]
            owned = self._active
            if owned is None or owned.run_id != run_id:
                raise ControllerUnavailable()
            safe = self._retire(owned)
            self.quarantined |= not safe
            if not safe:
                self._quarantine_hold.append(owned)
            self._active = None
            self._history[run_id] = (
                prior[0],
                prior[1],
                ControllerRun(run_id, state if safe else "failed"),
            )
            if not safe:
                raise ControllerUnavailable()

    def execute(self, body: dict[str, object], *, scope_token: str) -> dict[str, object]:
        """Run the existing adapter protocol against this run's child only."""
        from deploy.hermes.adapter import (
            ALLOWED_TOOLS,
            TERMINAL_STATUSES,
            _json_request,
            build_upstream_request,
            map_upstream_result,
        )

        admitted = self.admit(body, scope_token=scope_token)
        with self._lock:
            owned = self._active
            if owned is None or owned.run_id != admitted.run_id:
                raise ControllerUnavailable()
            url, key, deadline = owned.url, owned.api_key, owned.deadline
        try:
            created = _json_request(
                url + "/v1/runs",
                token=key,
                method="POST",
                body=build_upstream_request(body, allowed_tools=ALLOWED_TOOLS),
                timeout=min(10, max(0.1, deadline - time.monotonic())),
                idempotency_key=str(body["correlation_id"]),
            )
            upstream_id = created.get("run_id")
            if not isinstance(upstream_id, str) or re.fullmatch(
                r"run_[A-Za-z0-9]{1,128}", upstream_id
            ) is None:
                raise ControllerUnavailable()
            while time.monotonic() < deadline:
                if self.status(admitted.run_id).status != "running":
                    raise ControllerUnavailable()
                result = _json_request(
                    url + "/v1/runs/" + upstream_id,
                    token=key,
                    method="GET",
                    timeout=min(10, max(0.1, deadline - time.monotonic())),
                )
                if result.get("status") in TERMINAL_STATUSES:
                    mapped = map_upstream_result(body, result)
                    self._finish(admitted.run_id, mapped["status"])
                    return mapped
                if result.get("status") == "waiting_for_approval":
                    failed = map_upstream_result(body, {"status": "failed"})
                    failed["failure_category"] = "capability_rejected"
                    self._finish(admitted.run_id, "failed")
                    return failed
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            failed = map_upstream_result(body, {"status": "failed"})
            failed["failure_category"] = "timeout"
            self._finish(admitted.run_id, "failed")
            return failed
        except Exception:
            raise ControllerUnavailable() from None
        finally:
            stopped = self.stop(admitted.run_id)
            if stopped.status == "failed" and self.quarantined:
                raise ControllerUnavailable()
