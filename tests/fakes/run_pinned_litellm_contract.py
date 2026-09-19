from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.litellm.gateway import issue_budget_assertion  # noqa: E402

FAKE_SERVER = ROOT / "tests" / "fakes" / "openai_compatible_server.py"
CONFIG = ROOT / "deploy" / "litellm" / "config.yaml"
GATEWAY = ROOT / "deploy" / "litellm" / "gateway.py"
IMAGE = (
    "ghcr.io/berriai/litellm-non_root:v1.100.1@"
    "sha256:c36f3b27a5a817329e0fcf9c4e3a7bf58b62a5a736ad5352f0a5771c1be58404"
)
INDEX_DIGEST = "sha256:c36f3b27a5a817329e0fcf9c4e3a7bf58b62a5a736ad5352f0a5771c1be58404"
PLATFORM_DIGEST = "sha256:833abd09590afee0119dd574212fb01294dcc7d940ad21fabc0bf0681a9f4aea"
OPERATIONS_SHA = "a" * 40
CLIENT_TOKEN = "test-gateway-token"
MASTER_KEY = "sk-test-master-not-real-000000000000"
ASSERTION_KEY = b"test-budget-assertion-key-32-bytes-minimum"
STARTUP_TIMEOUT_SECONDS = 180
STARTUP_DEADLINE_GRACE_SECONDS = 30
STARTUP_POLL_SECONDS = 0.25


def _run(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        check=check,
        capture_output=True,
        text=True,
    )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _post(
    port: int,
    body: dict[str, object],
    nonce: str,
    *,
    timeout_seconds: int = 15,
    run_id: str = "runtime-contract-run",
) -> tuple[int, dict[str, object]]:
    assertion = issue_budget_assertion(
        key=ASSERTION_KEY,
        run_id=run_id,
        model_alias="civicloop-default",
        token_ceiling=512,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
        nonce=nonce,
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {CLIENT_TOKEN}",
            "Content-Type": "application/json",
            "X-CivicLoop-Budget-Assertion": assertion,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _seed(handoff_volume: str, ledger_volume: str) -> None:
    values = {
        "provider-credential": b"sk-test-provider-not-real-0000000000",
        "litellm-master-key": MASTER_KEY.encode(),
        "gateway-token": CLIENT_TOKEN.encode(),
        "budget-assertion-key": ASSERTION_KEY,
    }
    receipt = {
        "schema_version": 1,
        "component": "litellm",
        "environment": "production",
        "operations_sha": OPERATIONS_SHA,
        "target_index_digest": INDEX_DIGEST,
        "target_platform_digest": PLATFORM_DIGEST,
        "expires_at": int(time.time()) + 1800,
        "approval_digest": "sha256:" + "b" * 64,
        "signature_status": "verified",
        "files": {
            name: "sha256:" + hashlib.sha256(value).hexdigest()
            for name, value in values.items()
        },
    }
    payload = json.dumps(
        {
            "values": {name: value.decode() for name, value in values.items()},
            "receipt": receipt,
        },
        separators=(",", ":"),
    )
    code = (
        "import json,os,pathlib; p=json.loads(os.environ['SEED']); "
        "d=pathlib.Path('/handoff/current'); d.mkdir(parents=True); "
        "[(d/n).write_text(v) for n,v in p['values'].items()]; "
        "(d/'receipt.json').write_text(json.dumps(p['receipt'],separators=(',',':'))); "
        "[(os.chmod(x,0o400),os.chown(x,65534,65534)) for x in d.iterdir()]; "
        "os.chmod(d,0o500); os.chown(d,65534,65534); "
        "os.chmod('/ledger',0o700); os.chown('/ledger',65534,65534)"
    )
    _run(
        "run",
        "--rm",
        "--user",
        "0:0",
        "--entrypoint",
        "python",
        "-e",
        f"SEED={payload}",
        "-v",
        f"{handoff_volume}:/handoff",
        "-v",
        f"{ledger_volume}:/ledger",
        IMAGE,
        "-c",
        code,
    )


def _start(container: str, handoff: str, ledger: str, fake_port: int, prefix: str) -> int:
    result = _run(
        "run",
        "-d",
        "--name",
        container,
        "--user",
        "65534:65534",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "128",
        "--memory",
        "1g",
        "--cpus",
        "0.50",
        "--tmpfs",
        "/app/cache:rw,noexec,nosuid,nodev,uid=65534,gid=65534,mode=0700",
        "--tmpfs",
        "/app/migrations:rw,noexec,nosuid,nodev,uid=65534,gid=65534,mode=0700",
        "--mount",
        f"type=bind,source={CONFIG},target=/app/config.yaml,readonly",
        "--mount",
        f"type=bind,source={GATEWAY},target=/app/gateway.py,readonly",
        "-v",
        f"{handoff}:/run/model-gateway:ro",
        "-v",
        f"{ledger}:/var/lib/civicloop-model-gateway",
        "-p",
        "127.0.0.1::4000",
        "--add-host",
        "host.docker.internal:host-gateway",
        "-e",
        "LITELLM_CONFIG_FILE=/app/config.yaml",
        "-e",
        "LITELLM_MODEL_ALIAS=civicloop-default",
        "-e",
        "LITELLM_UPSTREAM_MODEL=openai/test-model",
        "-e",
        f"LITELLM_UPSTREAM_BASE_URL=http://host.docker.internal:{fake_port}/{prefix}/v1",
        "-e",
        "LITELLM_REQUEST_MAX_TOKENS=2000",
        "-e",
        "LITELLM_REQUEST_TIMEOUT_SECONDS=60",
        "-e",
        f"LITELLM_STARTUP_TIMEOUT_SECONDS={STARTUP_TIMEOUT_SECONDS}",
        "-e",
        f"CIVICLOOP_OPERATIONS_SHA={OPERATIONS_SHA}",
        "-e",
        f"LITELLM_INDEX_DIGEST={INDEX_DIGEST}",
        "-e",
        f"LITELLM_PLATFORM_DIGEST={PLATFORM_DIGEST}",
        "-e",
        "MODEL_GATEWAY_STARTUP_RECEIPT=/run/model-gateway/current/receipt.json",
        "-e",
        "MODEL_GATEWAY_LEDGER_PATH=/var/lib/civicloop-model-gateway/ledger.sqlite3",
        "--entrypoint",
        "python",
        IMAGE,
        "/app/gateway.py",
    )
    assert result.stdout.strip()
    deadline = (
        time.monotonic()
        + STARTUP_TIMEOUT_SECONDS
        + STARTUP_DEADLINE_GRACE_SECONDS
    )
    while time.monotonic() < deadline:
        port_result = _run("port", container, "4000/tcp", check=False)
        if port_result.returncode == 0 and port_result.stdout.strip():
            port = int(port_result.stdout.strip().rsplit(":", 1)[1])
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health/liveliness", timeout=1
                ) as response:
                    if response.status == 200:
                        return port
            except (OSError, urllib.error.URLError):
                pass
        state = _run(
            "inspect", "--format", "{{.State.Status}}", container, check=False
        )
        if state.returncode != 0 or state.stdout.strip() in {"exited", "dead"}:
            break
        time.sleep(STARTUP_POLL_SECONDS)
    log_result = _run("logs", container, check=False)
    logs = (log_result.stdout + log_result.stderr)[-4000:]
    state_text = _run(
        "inspect", "--format", "{{json .State}}", container, check=False
    ).stdout[-2000:]
    raise AssertionError(
        f"pinned LiteLLM container did not become ready: {state_text}\n{logs}"
    )


def _direct_litellm_diagnostic(container: str, body: dict[str, object]) -> str:
    code = (
        "import json,sys,urllib.error,urllib.request;"
        "body=json.loads(sys.argv[1]);"
        "request=urllib.request.Request("
        "'http://127.0.0.1:4001/v1/chat/completions',"
        "data=json.dumps(body).encode(),"
        "headers={'Authorization':'Bearer '+sys.argv[2],"
        "'Content-Type':'application/json'},method='POST');"
        "\ntry:\n"
        " response=urllib.request.urlopen(request,timeout=15);"
        " print(response.status,response.read().decode())\n"
        "except urllib.error.HTTPError as error:\n"
        " print(error.code,error.read().decode())\n"
    )
    result = _run(
        "exec",
        container,
        "python",
        "-c",
        code,
        json.dumps(body),
        MASTER_KEY,
        check=False,
    )
    return (result.stdout + result.stderr)[-4000:]


def main() -> int:
    suffix = uuid.uuid4().hex[:10]
    handoff = f"civicloop-litellm-handoff-{suffix}"
    ledger = f"civicloop-litellm-ledger-{suffix}"
    containers: list[str] = []
    _run("volume", "create", handoff)
    _run("volume", "create", ledger)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        port_file = root / "fake-port"
        capture_file = root / "capture.json"
        mode_file = root / "mode"
        mode_file.write_text("compatible", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "PORT_FILE": str(port_file),
                "CAPTURE_FILE": str(capture_file),
                "MODE_FILE": str(mode_file),
                "FAKE_PROVIDER_TIMEOUT_SECONDS": "65",
            }
        )
        fake = subprocess.Popen(
            [sys.executable, str(FAKE_SERVER)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                if port_file.exists():
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("fake provider did not start")
            fake_port = int(port_file.read_text())
            _seed(handoff, ledger)
            print("seeded production-equivalent handoff", flush=True)
            first = f"civicloop-litellm-{suffix}-a"
            containers.append(first)
            port = _start(first, handoff, ledger, fake_port, "compatible")
            print("pinned runtime ready with compatible config", flush=True)
            body = {
                "model": "civicloop-default",
                "messages": [{"role": "user", "content": "test-only request"}],
                "max_tokens": 32,
            }
            status, response = _post(port, body, "runtime-compatible-0001")
            if status != 200:
                direct_diagnostic = _direct_litellm_diagnostic(first, body)
                log_result = _run("logs", first, check=False)
                captured = (
                    capture_file.read_text(encoding="utf-8")
                    if capture_file.exists()
                    else "<fake provider was not reached>"
                )
                raise AssertionError(
                    "compatible request failed: "
                    f"status={status} response={response} capture={captured}\n"
                    f"direct LiteLLM response:\n{direct_diagnostic}\n"
                    f"container logs:\n{(log_result.stdout + log_result.stderr)[-4000:]}"
                )
            assert status == 200 and response["fixture_mode"] == "compatible", (
                status,
                response,
            )
            print("compatible request passed", flush=True)
            captured = json.loads(capture_file.read_text(encoding="utf-8"))
            assert captured["model"] == "test-model"
            assert set(captured) <= {"model", "messages", "max_tokens"}

            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/models", data=b"{}", method="POST"
            )
            try:
                urllib.request.urlopen(request, timeout=2)
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("management route was exposed")

            mode_file.write_text("error", encoding="utf-8")
            status, response = _post(port, body, "runtime-error-00000002")
            assert status in {502, 503}
            assert "provider-specific" not in json.dumps(response)
            mode_file.write_text("timeout", encoding="utf-8")
            status, response = _post(
                port,
                body,
                "runtime-timeout-000003",
                timeout_seconds=75,
            )
            assert status == 503 and response["error"]["code"] == "model_provider_unavailable"
            print("route, redacted error, and timeout passed", flush=True)

            _run("rm", "-f", first)
            containers.remove(first)
            mode_file.write_text("compatible", encoding="utf-8")
            second = f"civicloop-litellm-{suffix}-b"
            containers.append(second)
            port = _start(second, handoff, ledger, fake_port, "recorded")
            print("pinned runtime ready with recorded config", flush=True)
            status, response = _post(
                port,
                body,
                "runtime-recorded-00004",
                run_id="runtime-contract-recorded-run",
            )
            if status != 200:
                direct_diagnostic = _direct_litellm_diagnostic(second, body)
                raise AssertionError(
                    "recorded-provider request failed: "
                    f"status={status} response={response}\n"
                    f"direct LiteLLM response:\n{direct_diagnostic}"
                )
            assert response["fixture_mode"] == "recorded-openai", response
            print("configuration-only provider switch passed", flush=True)
        finally:
            fake.terminate()
            fake.wait(timeout=5)
            for container in containers:
                _run("rm", "-f", container, check=False)
            _run("volume", "rm", handoff, check=False)
            _run("volume", "rm", ledger, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
