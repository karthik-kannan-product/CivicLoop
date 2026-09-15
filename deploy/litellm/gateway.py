from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INFERENCE_PATH = "/v1/chat/completions"
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
NONCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{15,127}")
DIGEST_PATTERN = re.compile(r"sha256:[a-f0-9]{64}")
SHA_PATTERN = re.compile(r"[a-f0-9]{40}")
ALLOWED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "top_p",
        "stop",
        "seed",
        "response_format",
    }
)
ASSERTION_FIELDS = frozenset(
    {"version", "run_id", "model_alias", "token_ceiling", "expires_at", "nonce"}
)
RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "component",
        "environment",
        "operations_sha",
        "target_index_digest",
        "target_platform_digest",
        "expires_at",
        "approval_digest",
        "signature_status",
        "files",
    }
)
HANDOFF_FILES = (
    "provider-credential",
    "litellm-master-key",
    "gateway-token",
    "budget-assertion-key",
)


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class GatewayPolicy:
    alias: str
    max_tokens: int
    timeout_seconds: int


@dataclass(frozen=True)
class BudgetAssertion:
    run_id: str
    model_alias: str
    token_ceiling: int
    expires_at: int
    nonce: str


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError) as error:
        raise PolicyError("budget assertion encoding is invalid") from error


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


def _verify_budget_assertion(
    value: str, *, key: bytes, alias: str, now: datetime
) -> BudgetAssertion:
    if len(key) < 32:
        raise PolicyError("budget assertion verifier is unavailable")
    try:
        encoded_payload, encoded_signature = value.split(".")
    except ValueError as error:
        raise PolicyError("budget assertion format is invalid") from error
    payload = _b64decode(encoded_payload)
    signature = _b64decode(encoded_signature)
    if not hmac.compare_digest(signature, hmac.digest(key, payload, "sha256")):
        raise PolicyError("budget assertion signature is invalid")
    try:
        decoded = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError("budget assertion payload is invalid") from error
    if not isinstance(decoded, dict) or set(decoded) != ASSERTION_FIELDS:
        raise PolicyError("budget assertion fields are invalid")
    if decoded.get("version") != 1:
        raise PolicyError("budget assertion version is invalid")
    run_id = decoded.get("run_id")
    model_alias = decoded.get("model_alias")
    nonce = decoded.get("nonce")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise PolicyError("budget assertion run ID is invalid")
    if model_alias != alias:
        raise PolicyError("budget assertion model alias mismatch")
    if not isinstance(nonce, str) or NONCE_PATTERN.fullmatch(nonce) is None:
        raise PolicyError("budget assertion nonce is invalid")
    token_ceiling = _positive_integer(
        decoded.get("token_ceiling"), label="budget token ceiling", maximum=100_000
    )
    expires_at = decoded.get("expires_at")
    current = int(now.astimezone(UTC).timestamp())
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise PolicyError("budget assertion expiry is invalid")
    if expires_at <= current:
        raise PolicyError("budget assertion is expired")
    if expires_at > current + 300:
        raise PolicyError("budget assertion lifetime is too long")
    return BudgetAssertion(run_id, model_alias, token_ceiling, expires_at, nonce)


class DurableBudgetLedger:
    """SQLite-backed nonce and token reservations that survive gateway restart."""

    def __init__(self, path: Path, *, maximum_records: int = 10_000) -> None:
        if maximum_records < 100:
            raise ValueError("budget ledger bound is too small")
        self.path = path
        self.maximum_records = maximum_records
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(path.parent, 0o700)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    model_alias TEXT NOT NULL,
                    token_ceiling INTEGER NOT NULL,
                    tokens_reserved INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nonces (
                    nonce TEXT PRIMARY KEY,
                    expires_at INTEGER NOT NULL
                );
                """
            )
        if os.name != "nt":
            os.chmod(path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA secure_delete=ON")
        return connection

    def reserve(
        self, assertion: BudgetAssertion, requested_tokens: int, *, now: datetime
    ) -> None:
        current = int(now.astimezone(UTC).timestamp())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM nonces WHERE expires_at <= ?", (current,))
            connection.execute("DELETE FROM runs WHERE expires_at <= ?", (current,))
            count = connection.execute("SELECT COUNT(*) FROM nonces").fetchone()[0]
            if count >= self.maximum_records:
                raise PolicyError("budget ledger capacity is exhausted")
            try:
                connection.execute(
                    "INSERT INTO nonces(nonce, expires_at) VALUES (?, ?)",
                    (assertion.nonce, assertion.expires_at),
                )
            except sqlite3.IntegrityError as error:
                raise PolicyError("budget assertion was replayed") from error
            run = connection.execute(
                "SELECT model_alias, token_ceiling, tokens_reserved FROM runs "
                "WHERE run_id = ?",
                (assertion.run_id,),
            ).fetchone()
            if run is None:
                used = 0
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
                    (
                        assertion.run_id,
                        assertion.model_alias,
                        assertion.token_ceiling,
                        0,
                        assertion.expires_at,
                    ),
                )
            else:
                model_alias, token_ceiling, used = run
                if (
                    model_alias != assertion.model_alias
                    or token_ceiling != assertion.token_ceiling
                ):
                    raise PolicyError("run budget binding changed")
            if requested_tokens > assertion.token_ceiling - used:
                raise PolicyError("run token budget exhausted")
            connection.execute(
                "UPDATE runs SET tokens_reserved = tokens_reserved + ?, "
                "expires_at = MAX(expires_at, ?) WHERE run_id = ?",
                (requested_tokens, assertion.expires_at, assertion.run_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _positive_integer(value: object, *, label: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise PolicyError(f"{label} is invalid")
    return value


def _bounded_number(value: object, *, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PolicyError(f"{label} is invalid")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise PolicyError(f"{label} is invalid")
    return normalized


def _messages(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise PolicyError("messages are invalid")
    result = []
    total = 0
    for message in value:
        if not isinstance(message, dict) or set(message) != {"role", "content"}:
            raise PolicyError("message fields are invalid")
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise PolicyError("message role is invalid")
        if not isinstance(content, str) or len(content) > 32_768:
            raise PolicyError("message content is invalid")
        total += len(content)
        if total > 131_072:
            raise PolicyError("message content is too large")
        result.append({"role": role, "content": content})
    return result


def _sanitize_request(body: Mapping[str, object], policy: GatewayPolicy) -> dict[str, object]:
    unknown = set(body) - ALLOWED_REQUEST_FIELDS
    if unknown:
        raise PolicyError(f"unsupported request field: {sorted(unknown)[0]}")
    if body.get("model") != policy.alias:
        raise PolicyError("only the configured model alias is allowed")
    sanitized: dict[str, object] = {
        "model": policy.alias,
        "messages": _messages(body.get("messages")),
        "max_tokens": _positive_integer(
            body.get("max_tokens"),
            label="request token limit",
            maximum=policy.max_tokens,
        ),
    }
    if "temperature" in body:
        sanitized["temperature"] = _bounded_number(
            body["temperature"], label="temperature", minimum=0, maximum=2
        )
    if "top_p" in body:
        sanitized["top_p"] = _bounded_number(
            body["top_p"], label="top_p", minimum=0, maximum=1
        )
    if "seed" in body:
        seed = body["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or abs(seed) > 2**31 - 1:
            raise PolicyError("seed is invalid")
        sanitized["seed"] = seed
    if "stop" in body:
        stop = body["stop"]
        values = [stop] if isinstance(stop, str) else stop
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 4
            or any(not isinstance(item, str) or len(item) > 256 for item in values)
        ):
            raise PolicyError("stop is invalid")
        sanitized["stop"] = list(values)
    if "response_format" in body:
        response_format = body["response_format"]
        if not isinstance(response_format, dict) or response_format not in (
            {"type": "text"},
            {"type": "json_object"},
        ):
            raise PolicyError("response format is invalid")
        sanitized["response_format"] = dict(response_format)
    return sanitized


def prepare_request(
    body: Mapping[str, object],
    *,
    budget_assertion: str,
    assertion_key: bytes,
    policy: GatewayPolicy,
    ledger: DurableBudgetLedger,
    now: datetime,
) -> dict[str, object]:
    sanitized = _sanitize_request(body, policy)
    assertion = _verify_budget_assertion(
        budget_assertion, key=assertion_key, alias=policy.alias, now=now
    )
    ledger.reserve(assertion, int(sanitized["max_tokens"]), now=now)
    sanitized["timeout"] = policy.timeout_seconds
    sanitized["metadata"] = {
        "civicloop_run_id": assertion.run_id,
        "civicloop_run_token_ceiling": assertion.token_ceiling,
    }
    return sanitized


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


def _read_private_value(path_value: str, label: str) -> bytes:
    path = Path(path_value)
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label} file is unavailable")
    metadata = path.stat()
    if os.name != "nt":
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o400:
            raise RuntimeError(f"{label} file ownership or permissions are invalid")
    value = path.read_bytes().strip()
    if not value or b"\n" in value or b"\r" in value:
        raise RuntimeError(f"{label} file is invalid")
    return value


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def verify_startup_receipt(
    path: Path,
    *,
    operations_sha: str,
    index_digest: str,
    platform_digest: str,
    now: datetime,
) -> dict[str, Path]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise RuntimeError("model gateway startup receipt is unavailable")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError("model gateway startup receipt is invalid") from error
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        raise RuntimeError("model gateway startup receipt fields are invalid")
    expected = {
        "schema_version": 1,
        "component": "litellm",
        "environment": "production",
        "operations_sha": operations_sha,
        "target_index_digest": index_digest,
        "target_platform_digest": platform_digest,
        "signature_status": "verified",
    }
    if any(receipt.get(field) != value for field, value in expected.items()):
        raise RuntimeError("model gateway startup receipt binding is invalid")
    if SHA_PATTERN.fullmatch(operations_sha) is None:
        raise RuntimeError("model gateway operations SHA is invalid")
    if DIGEST_PATTERN.fullmatch(index_digest) is None or DIGEST_PATTERN.fullmatch(
        platform_digest
    ) is None:
        raise RuntimeError("model gateway image digest is invalid")
    expires_at = receipt.get("expires_at")
    current = int(now.astimezone(UTC).timestamp())
    if isinstance(expires_at, bool) or not isinstance(expires_at, int) or expires_at <= current:
        raise RuntimeError("model gateway startup approval is expired")
    if expires_at > current + int(timedelta(hours=24).total_seconds()):
        raise RuntimeError("model gateway startup approval lifetime is invalid")
    approval_digest = receipt.get("approval_digest")
    if not isinstance(approval_digest, str) or DIGEST_PATTERN.fullmatch(
        approval_digest
    ) is None:
        raise RuntimeError("model gateway approval digest is invalid")
    files = receipt.get("files")
    if not isinstance(files, dict) or set(files) != set(HANDOFF_FILES):
        raise RuntimeError("model gateway handoff file receipt is invalid")
    resolved = {}
    for name in HANDOFF_FILES:
        candidate = path.parent / name
        if not candidate.is_file() or candidate.is_symlink():
            raise RuntimeError("model gateway handoff file is unavailable")
        if files.get(name) != _file_digest(candidate):
            raise RuntimeError("model gateway handoff file digest mismatch")
        resolved[name] = candidate
    return resolved


class _Handler(BaseHTTPRequestHandler):
    server_version = "CivicLoopModelGateway/2"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _json(self, status: int, value: Mapping[str, object]) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

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
        except (OSError, urllib.error.URLError):
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
                budget_assertion=self.headers.get("X-CivicLoop-Budget-Assertion", ""),
                assertion_key=server.assertion_key,  # type: ignore[attr-defined]
                policy=server.policy,  # type: ignore[attr-defined]
                ledger=server.ledger,  # type: ignore[attr-defined]
                now=server.now(),  # type: ignore[attr-defined]
            )
        except (json.JSONDecodeError, PolicyError, ValueError):
            self._json(
                400,
                {
                    "error": {
                        "code": "invalid_model_request",
                        "message": "The model request is invalid.",
                    }
                },
            )
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
            except (OSError, ValueError, urllib.error.URLError):
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
    paths = verify_startup_receipt(
        Path(environment["MODEL_GATEWAY_STARTUP_RECEIPT"]),
        operations_sha=environment["CIVICLOOP_OPERATIONS_SHA"],
        index_digest=environment["LITELLM_INDEX_DIGEST"],
        platform_digest=environment["LITELLM_PLATFORM_DIGEST"],
        now=datetime.now(UTC),
    )
    provider_key = _read_private_value(str(paths["provider-credential"]), "provider credential")
    master_key = _read_private_value(str(paths["litellm-master-key"]), "LiteLLM master key")
    client_token = _read_private_value(str(paths["gateway-token"]), "gateway token")
    assertion_key = _read_private_value(
        str(paths["budget-assertion-key"]), "budget assertion key"
    )
    environment["LITELLM_UPSTREAM_API_KEY"] = provider_key.decode("utf-8")
    environment["LITELLM_MASTER_KEY"] = master_key.decode("utf-8")
    child = _start_litellm(environment)
    server = ThreadingHTTPServer(("0.0.0.0", 4000), _Handler)
    server.policy = GatewayPolicy(  # type: ignore[attr-defined]
        alias=environment.get("LITELLM_MODEL_ALIAS", "civicloop-default"),
        max_tokens=int(environment.get("LITELLM_REQUEST_MAX_TOKENS", "2000")),
        timeout_seconds=int(environment.get("LITELLM_REQUEST_TIMEOUT_SECONDS", "60")),
    )
    server.ledger = DurableBudgetLedger(  # type: ignore[attr-defined]
        Path(environment["MODEL_GATEWAY_LEDGER_PATH"])
    )
    server.assertion_key = assertion_key  # type: ignore[attr-defined]
    server.now = lambda: datetime.now(UTC)  # type: ignore[attr-defined]
    server.inference_slot = threading.BoundedSemaphore(1)  # type: ignore[attr-defined]
    server.client_token = client_token.decode("utf-8")  # type: ignore[attr-defined]
    server.litellm_master_key = master_key.decode("utf-8")  # type: ignore[attr-defined]
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
