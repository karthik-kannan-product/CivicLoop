from __future__ import annotations

import hmac
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INFERENCE_PATH = "/v1/chat/completions"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class GatewayPolicy:
    alias: str
    max_tokens: int
    timeout_seconds: int


class BudgetLedger:
    """Process-local, fail-closed reservation ledger for one-concurrent-run mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._budgets: dict[str, tuple[int, int]] = {}

    def reserve(self, run_id: str, declared_budget: int, requested_tokens: int) -> None:
        with self._lock:
            original_budget, used = self._budgets.get(run_id, (declared_budget, 0))
            if original_budget != declared_budget:
                raise PolicyError("run budget cannot change")
            if requested_tokens > original_budget - used:
                raise PolicyError("run token budget exhausted")
            self._budgets[run_id] = (original_budget, used + requested_tokens)


def _positive_integer(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PolicyError(f"{label} is invalid")
    return value


def prepare_request(
    body: Mapping[str, object],
    *,
    headers: Mapping[str, str],
    policy: GatewayPolicy,
    ledger: BudgetLedger,
) -> dict[str, object]:
    if body.get("model") != policy.alias:
        raise PolicyError("only the configured model alias is allowed")
    messages = body.get("messages")
    if not isinstance(messages, list) or len(messages) > 64:
        raise PolicyError("messages are invalid")
    max_tokens = _positive_integer(
        body.get("max_tokens"), label="request token limit", maximum=policy.max_tokens
    )
    normalized_headers = {name.lower(): value for name, value in headers.items()}
    run_id = normalized_headers.get("x-civicloop-run-id", "")
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise PolicyError("trusted run metadata is required")
    try:
        declared_budget = int(normalized_headers["x-civicloop-run-token-budget"])
    except (KeyError, TypeError, ValueError) as error:
        raise PolicyError("trusted run budget metadata is required") from error
    _positive_integer(declared_budget, label="run token budget", maximum=100_000)
    ledger.reserve(run_id, declared_budget, max_tokens)

    prepared = dict(body)
    prepared["model"] = policy.alias
    prepared["max_tokens"] = max_tokens
    prepared["timeout"] = policy.timeout_seconds
    prepared["metadata"] = {
        "civicloop_run_id": run_id,
        "civicloop_run_token_budget": declared_budget,
    }
    return prepared


def provider_neutral_error(*, status: int, detail: str = "") -> dict[str, object]:
    del detail
    retryable = status == 429 or status >= 500
    return {
        "error": {
            "code": "model_provider_unavailable" if retryable else "model_request_rejected",
            "message": (
                "The model service is temporarily unavailable."
                if retryable
                else "The model request could not be completed."
            ),
            "retryable": retryable,
        }
    }


def _read_private_value(path_value: str, label: str) -> str:
    path = Path(path_value)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} file is unavailable")
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) not in {0o400, 0o600}:
        raise RuntimeError(f"{label} file permissions are invalid")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise RuntimeError(f"{label} file is invalid")
    return value


class _Handler(BaseHTTPRequestHandler):
    server_version = "CivicLoopModelGateway/1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _json(self, status: int, value: Mapping[str, object]) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health/liveliness":
            self._json(404, {"error": {"code": "route_not_found"}})
            return
        try:
            with urllib.request.urlopen(
                f"{self.server.upstream}/health/liveliness",
                timeout=2,  # type: ignore[attr-defined]
            ) as response:
                healthy = response.status == 200
        except OSError, urllib.error.URLError:
            healthy = False
        self._json(200 if healthy else 503, {"status": "ok" if healthy else "unavailable"})

    def do_POST(self) -> None:  # noqa: N802
        server = self.server
        if self.path != INFERENCE_PATH:
            self._json(404, {"error": {"code": "route_not_found"}})
            return
        authorization = self.headers.get("Authorization", "")
        if not hmac.compare_digest(authorization, f"Bearer {server.client_token}"):  # type: ignore[attr-defined]
            self._json(401, {"error": {"code": "unauthorized"}})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 262_144:
                raise PolicyError("request size is invalid")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise PolicyError("request body is invalid")
            prepared = prepare_request(
                body,
                headers={name.lower(): value for name, value in self.headers.items()},
                policy=server.policy,  # type: ignore[attr-defined]
                ledger=server.ledger,  # type: ignore[attr-defined]
            )
        except (json.JSONDecodeError, PolicyError, ValueError) as error:
            self._json(400, {"error": {"code": "invalid_model_request", "message": str(error)}})
            return
        if not server.inference_slot.acquire(blocking=False):  # type: ignore[attr-defined]
            self._json(429, {"error": {"code": "model_gateway_busy", "retryable": True}})
            return
        try:
            request = urllib.request.Request(
                f"{server.upstream}{INFERENCE_PATH}",  # type: ignore[attr-defined]
                data=json.dumps(prepared).encode(),
                headers={
                    "Authorization": f"Bearer {server.litellm_master_key}",  # type: ignore[attr-defined]
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=server.policy.timeout_seconds,  # type: ignore[attr-defined]
                ) as response:
                    payload = response.read(1_048_577)
                    if len(payload) > 1_048_576:
                        raise ValueError("response too large")
                    parsed = json.loads(payload)
                    if not isinstance(parsed, dict):
                        raise ValueError("invalid response")
                    self._json(200, parsed)
            except urllib.error.HTTPError as error:
                self._json(
                    502 if error.code < 500 else 503,
                    provider_neutral_error(status=error.code),
                )
            except OSError, ValueError, urllib.error.URLError:
                self._json(503, provider_neutral_error(status=503))
        finally:
            server.inference_slot.release()  # type: ignore[attr-defined]


def _start_litellm(environment: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "litellm",
            "--config",
            environment.get("LITELLM_CONFIG_FILE", "/app/config.yaml"),
            "--host",
            "127.0.0.1",
            "--port",
            "4001",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def main() -> int:
    environment = os.environ.copy()
    environment["LITELLM_UPSTREAM_API_KEY"] = _read_private_value(
        environment["LITELLM_PROVIDER_CREDENTIAL_FILE"], "provider credential"
    )
    master_key = _read_private_value(environment["LITELLM_MASTER_KEY_FILE"], "LiteLLM master key")
    client_token = _read_private_value(environment["MODEL_GATEWAY_TOKEN_FILE"], "gateway token")
    environment["LITELLM_MASTER_KEY"] = master_key
    child = _start_litellm(environment)
    server = ThreadingHTTPServer(("0.0.0.0", 4000), _Handler)
    server.policy = GatewayPolicy(  # type: ignore[attr-defined]
        alias=environment.get("LITELLM_MODEL_ALIAS", "civicloop-default"),
        max_tokens=int(environment.get("LITELLM_REQUEST_MAX_TOKENS", "2000")),
        timeout_seconds=int(environment.get("LITELLM_REQUEST_TIMEOUT_SECONDS", "60")),
    )
    server.ledger = BudgetLedger()  # type: ignore[attr-defined]
    server.inference_slot = threading.BoundedSemaphore(1)  # type: ignore[attr-defined]
    server.client_token = client_token  # type: ignore[attr-defined]
    server.litellm_master_key = master_key  # type: ignore[attr-defined]
    server.upstream = "http://127.0.0.1:4001"  # type: ignore[attr-defined]

    def stop(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
