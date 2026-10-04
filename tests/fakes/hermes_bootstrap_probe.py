"""Explicit offline vendor-bootstrap probe; no model or provider requests."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
_IMAGE = re.compile(r"(?:[a-z0-9][a-z0-9._:/-]*@)?sha256:[0-9a-f]{64}")
_MARKER = "CIVICLOOP_BOOTSTRAP_EVIDENCE="


def probe_command(image: str, name: str) -> list[str]:
    if _IMAGE.fullmatch(image) is None:
        raise ValueError("An exact image digest is required")
    hermes = yaml.safe_load((ROOT / "compose.agent.yaml").read_text())["services"]["hermes"]
    if (
        hermes.get("entrypoint")
        or hermes.get("cap_drop") != ["ALL"]
        or hermes.get("cap_add") != ["SETUID", "SETGID", "DAC_OVERRIDE"]
        or hermes.get("init") is not True
        or hermes.get("read_only") is not True
        or hermes.get("security_opt") != ["no-new-privileges:true"]
    ):
        raise ValueError("Bootstrap security contract is invalid")
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--init",
        "--read-only",
    ]
    for capability in hermes["cap_drop"]:
        command += ["--cap-drop", capability]
    for capability in hermes["cap_add"]:
        command += ["--cap-add", capability]
    for option in hermes["security_opt"]:
        command += ["--security-opt", option]
    for mount in hermes["tmpfs"]:
        command += ["--tmpfs", mount]
    command += [
        "--cpus",
        str(hermes["cpus"]),
        "--memory",
        hermes["mem_limit"],
        "--pids-limit",
        str(hermes["pids_limit"]),
    ]
    # Source identities and lease bindings are unnecessary for a UID-drop probe.
    # Keep all security/tmpfs options from Compose and supply only inert config.
    for key in (
        "HERMES_HOME",
        "HERMES_UID",
        "HERMES_GID",
        "HERMES_DASHBOARD",
        "HERMES_DISABLE_LAZY_INSTALLS",
        "HERMES_SAFE_MODE",
    ):
        command += ["--env", key + "=" + hermes["environment"][key]]
    command += ["--env", "API_SERVER_KEY=synthetic-offline-bootstrap-probe-only"]
    script = (
        "import os,json; "
        "caps=next(x.split(':',1)[1].strip() for x in open('/proc/self/status') "
        "if x.startswith('CapEff:')); "
        "print('"
        + _MARKER
        + "'+json.dumps({'uid':os.getuid(),'gid':os.getgid(),'cap_eff':int(caps,16)}))"
    )
    command += [image, "/opt/hermes/.venv/bin/python", "-c", script]
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="Exact repository@sha256:digest or cached sha256 image ID"
    )
    args = parser.parse_args()
    name = "civicloop-hermes-bootstrap-" + uuid.uuid4().hex[:12]
    evidence = None
    try:
        result = subprocess.run(
            probe_command(args.image, name),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0:
            lines = [
                line[len(_MARKER) :]
                for line in result.stdout.splitlines()
                if line.startswith(_MARKER)
            ]
            if len(lines) == 1:
                candidate = json.loads(lines[0])
                if candidate == {"uid": 10000, "gid": 10000, "cap_eff": 0}:
                    evidence = candidate
    except OSError, ValueError, subprocess.TimeoutExpired:
        pass
    finally:
        # Exact task-created name only; never expose vendor diagnostics or values.
        try:
            subprocess.run(
                ["docker", "rm", "--force", name], capture_output=True, timeout=15, check=False
            )
        except OSError, subprocess.TimeoutExpired:
            pass
    if evidence is None:
        print(json.dumps({"status": "failed", "code": "bootstrap_permission_probe_failed"}))
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "uid": 10000,
                "gid": 10000,
                "effective_capabilities": 0,
                "network": "none",
                "provider_calls": 0,
                "scope": "vendor_bootstrap_only",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
