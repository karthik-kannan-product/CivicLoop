"""Opt-in exact-image application gate. Never builds or uses a developer .env.

All subprocess output stays in memory; public evidence contains closed categories,
counts and digests. Run only after candidate review and image identity freeze.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent
APP_SERVICES = {"web", "worker", "migrate", "mcp", "hermes-adapter", "hermes-transport"}
IMAGE_KEYS = {"app", "hermes", "litellm", "phoenix", "postgres", "valkey"}
SCENARIOS = (
    "delayed_revoked_a",
    "success_b",
    "invalid_output",
    "cancellation",
    "kill_switch",
    "mcp_outage",
    "litellm_outage",
    "phoenix_outage",
    "restored_success",
)


class ContractFailure(Exception):
    def __init__(self, category, *, details=None):
        self.category = category
        self.details = details or {}
        super().__init__(category)


def require(condition, category):
    if not condition:
        raise ContractFailure(category)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def tree_digest(directory):
    directory = Path(directory)
    rows = [
        (p.relative_to(directory).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in directory.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]
    return digest(sorted(rows))


def diagnostic(raw):
    """Classify captured ephemeral output without returning its constituent text."""
    if isinstance(raw, str):
        raw = raw.encode()
    raw = raw[-262144:]
    lowered = raw.lower()
    matches = {
        "permission_denied": (b"permission denied", b"access is denied"),
        "readonly_filesystem": (b"read-only file system", b"readonly filesystem"),
        "missing_module": (b"modulenotfounderror", b"no module named"),
        "missing_file": (b"no such file or directory", b"filenotfounderror"),
        "dependency_failed": (b"dependency failed", b"didn't complete successfully"),
        "cli_arguments_invalid": (
            b"unexpected extra arguments",
            b"unrecognized arguments",
            b"no such command",
            b"usage:",
        ),
        "configuration_invalid": (b"configuration unavailable", b"improperlyconfigured"),
        "port_conflict": (b"address already in use", b"port is already allocated"),
        "unhealthy": (b"is unhealthy", b"healthcheck failed"),
        "oom": (b"out of memory", b"oomkilled"),
        "daemon_unavailable": (b"cannot connect to the docker daemon",),
        "prohibited_content": (b"task8_synthetic_content_marker_4d70ae",),
    }
    return {
        "byte_count": len(raw),
        "digest": hashlib.sha256(raw).hexdigest(),
        "category_flags": {
            category: any(marker in lowered for marker in markers)
            for category, markers in matches.items()
        },
    }


def command(arguments, *, timeout=30, include_stderr=False, input_text=None):
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            input=input_text,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ContractFailure(
            "command_timeout",
            details={"output": diagnostic((error.stdout or b"") + (error.stderr or b""))},
        ) from None
    except OSError:
        raise ContractFailure("command_unavailable") from None
    if result.returncode != 0:
        raise ContractFailure(
            "command_failed",
            details={
                "return_code": result.returncode,
                "output": diagnostic(result.stdout + result.stderr),
            },
        )
    return result.stdout + result.stderr if include_stderr else result.stdout


def load_candidate(path):
    try:
        candidate = json.loads(Path(path).read_text())
        require(
            set(candidate)
            == {
                "public_sha",
                "operations_sha",
                "source_tree_digest",
                "runtime_tree_digest",
                "production_compose",
                "production_compose_digest",
                "images",
            },
            "manifest_schema",
        )
        require(set(candidate["images"]) == IMAGE_KEYS, "manifest_schema")
        for key in ("public_sha", "operations_sha"):
            require(re.fullmatch(r"[0-9a-f]{40}", candidate[key]), "manifest_schema")
        for key in ("source_tree_digest", "runtime_tree_digest", "production_compose_digest"):
            require(re.fullmatch(r"[0-9a-f]{64}", candidate[key]), "manifest_schema")
        for image in candidate["images"].values():
            require(re.fullmatch(r"(?:[^\s]+@)?sha256:[0-9a-f]{64}", image), "mutable_image")
        require(
            command(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).strip()
            == candidate["public_sha"],
            "candidate_sha_mismatch",
        )
        require(
            not command(["git", "-C", str(ROOT), "status", "--porcelain"]).strip(),
            "candidate_dirty",
        )
        tracked = command(["git", "-C", str(ROOT), "ls-files", "-z"]).split("\0")
        rows = [
            (name, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
            for name in tracked
            if name
        ]
        require(
            digest(sorted(rows)) == candidate["source_tree_digest"], "candidate_source_mismatch"
        )
        require(
            tree_digest(ROOT / "deploy") == candidate["runtime_tree_digest"],
            "candidate_runtime_mismatch",
        )
        config = Path(candidate["production_compose"])
        require(config.is_absolute(), "manifest_schema")
        require(
            hashlib.sha256(config.read_bytes()).hexdigest()
            == candidate["production_compose_digest"],
            "candidate_config_mismatch",
        )
        private_root = config.parents[2]
        require(
            command(["git", "-C", str(private_root), "rev-parse", "HEAD"]).strip()
            == candidate["operations_sha"],
            "operations_sha_mismatch",
        )
        require(
            not command(["git", "-C", str(private_root), "status", "--porcelain"]).strip(),
            "operations_dirty",
        )
        return candidate
    except ContractFailure:
        raise
    except Exception:
        raise ContractFailure("manifest_unavailable") from None


def merge(left, right):
    result = copy.deepcopy(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def standalone_config(candidate, *, enabled=True):
    """Retain production service commands/security/networks; isolate fixture data."""
    production = yaml.safe_load(Path(candidate["production_compose"]).read_text())
    base = yaml.safe_load((ROOT / "compose.yaml").read_text())
    config = merge(base, production)
    services = config["services"]
    services.pop("scheduler", None)
    services.pop("model-gateway-init", None)
    config.pop("secrets", None)
    for key in list(config):
        if key.startswith("x-") or key == "name":
            del config[key]
    config["networks"] = {
        name: {"internal": True}
        for name in ("default", "agent-control", "hermes-runtime", "hermes-data", "provider-egress")
    }
    config["volumes"] = {name: {} for name in config.get("volumes", {})}
    config["volumes"].update(
        {"owner-identity": {}, "phoenix-data": {}, "phoenix-identity": {}, "telemetry-identity": {}}
    )
    database_password = secrets.token_urlsafe(32)
    common = {
        "CIVICLOOP_ENV": "test",
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(48),
        "DATABASE_URL": f"postgres://fixture:{database_password}@db:5432/fixture",
        "CELERY_BROKER_URL": "redis://valkey:6379/1",
        "VALKEY_URL": "redis://valkey:6379/0",
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,web",
        "CIVICLOOP_HERMES_PROFILE_ID": "task8_fixture",
        "CIVICLOOP_HERMES_PROFILE_REVISION": "1",
        "CIVICLOOP_HERMES_TIMEOUT_SECONDS": "120",
        "CIVICLOOP_HERMES_MAX_INFERENCES": "8",
        "CIVICLOOP_TELEMETRY_ENABLED": "true",
        "CIVICLOOP_TELEMETRY_ENDPOINT": "http://fixture-proxy:8089/v1/traces",
        "CIVICLOOP_TELEMETRY_HEADERS_FILE": "/telemetry-identity/otlp-headers",
        "CIVICLOOP_HERMES_ENABLED": str(enabled).lower(),
        "CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED": str(enabled).lower(),
    }
    fixture_mount = {
        "type": "bind",
        "source": str(FIXTURES),
        "target": "/fixture",
        "read_only": True,
    }
    for name, service in services.items():
        for removed in ("build", "env_file", "profiles", "secrets", "ports"):
            service.pop(removed, None)
        service["restart"] = "no"
        environment = service.setdefault("environment", {})
        for key, value in list(environment.items()):
            if "${" in str(value):
                default = re.search(r":-(.*?)\}", str(value), re.S)
                environment[key] = default[1] if default else ""
        if name in APP_SERVICES:
            service["image"] = candidate["images"]["app"]
            environment.update(common)
            environment["PYTHONPATH"] = "/app/backend:/app/runtime"
        volumes = service.get("volumes", [])
        service["volumes"] = [
            v for v in volumes if isinstance(v, dict) and v.get("type") == "volume"
        ]
        if name in APP_SERVICES:
            service["volumes"].append("telemetry-identity:/telemetry-identity:ro")
        if name in {"web", "migrate"}:
            environment.update(
                {
                    "CIVICLOOP_ADMIN_IDENTITY_ENABLED": "true",
                    "CIVICLOOP_IDENTITY_KEY_FILE": "/owner/identity.json",
                }
            )
            service["volumes"].append("owner-identity:/owner:ro")
            service["depends_on"]["hermes-identities-init"] = {
                "condition": "service_completed_successfully"
            }
        if name == "hermes":
            service["image"] = candidate["images"]["hermes"]
        if name == "hermes-adapter":
            environment["HERMES_TRANSPORT_CONTROL_URL"] = "http://fixture-proxy:8089"
    services["db"]["image"] = candidate["images"]["postgres"]
    services["db"]["environment"] = {
        "POSTGRES_DB": "fixture",
        "POSTGRES_USER": "fixture",
        "POSTGRES_PASSWORD": database_password,
    }
    services["db"]["volumes"] = ["postgres-data:/var/lib/postgresql/data"]
    services["valkey"]["image"] = candidate["images"]["valkey"]
    init = services["hermes-identities-init"]
    init.update(
        {
            "image": candidate["images"]["app"],
            "entrypoint": ["python", "/fixture/hermes_contract_fixture.py"],
            "command": ["identities"],
            "tmpfs": ["/source:mode=0700", "/tmp:mode=0700"],
            "environment": {
                "PYTHONPATH": "/app/runtime:/app/backend",
                "CIVICLOOP_OPERATIONS_SHA": candidate["operations_sha"],
                "LITELLM_INDEX_DIGEST": candidate["images"]["litellm"].split("@")[-1],
                "LITELLM_PLATFORM_DIGEST": (
                    "sha256:" + "833abd09590afee0119dd574212fb01294dcc7d940ad21fabc0bf0681a9f4aea"
                ),
            },
        }
    )
    init["volumes"].extend(
        [
            fixture_mount,
            "model-gateway-handoff:/gateway",
            "model-gateway-ledger:/ledger",
            "owner-identity:/owner",
            "phoenix-identity:/phoenix-identity",
            "telemetry-identity:/telemetry-identity",
            "phoenix-data:/phoenix-data",
        ]
    )
    lite = services["litellm"]
    lite["image"] = candidate["images"]["litellm"]
    lite["depends_on"] = {"hermes-identities-init": {"condition": "service_completed_successfully"}}
    lite["environment"].update(
        {
            "CIVICLOOP_OPERATIONS_SHA": candidate["operations_sha"],
            "LITELLM_UPSTREAM_BASE_URL": "http://fixture-model:8088/v1",
            "LITELLM_UPSTREAM_MODEL": "openai/synthetic",
            "LITELLM_REQUEST_TIMEOUT_SECONDS": "60",
        }
    )
    lite["volumes"].extend(
        [
            {
                "type": "bind",
                "source": str(ROOT / "deploy/litellm/gateway.py"),
                "target": "/app/gateway.py",
                "read_only": True,
            },
            {
                "type": "bind",
                "source": str(ROOT / "deploy/litellm/config.yaml"),
                "target": "/app/config.yaml",
                "read_only": True,
            },
        ]
    )
    for name, filename, networks in (
        ("fixture-model", "hermes_contract_model.py", ["provider-egress"]),
        ("fixture-proxy", "hermes_contract_proxy.py", ["default", "agent-control"]),
    ):
        services[name] = {
            "image": candidate["images"]["app"],
            "entrypoint": ["python", "/fixture/" + filename],
            "restart": "no",
            "user": "10001:10001",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "networks": networks,
            "environment": {"PYTHONPATH": "/app/backend:/app/runtime"},
            "volumes": [fixture_mount],
            "tmpfs": ["/tmp:mode=0700,uid=10001,gid=10001"],
            "mem_limit": "128m",
            "pids_limit": 32,
        }
    services["fixture-proxy"]["volumes"].append("hermes-transport-identities:/run/secrets:ro")
    services["fixture-proxy"]["volumes"].append("telemetry-identity:/telemetry-identity:ro")
    services["fixture-proxy"]["depends_on"] = {
        "hermes-identities-init": {"condition": "service_completed_successfully"}
    }
    phoenix = copy.deepcopy(
        yaml.safe_load((ROOT / "compose.observability.yaml").read_text())["services"]["phoenix"]
    )
    for removed in ("profiles", "env_file", "ports"):
        phoenix.pop(removed, None)
    phoenix.update(
        {
            "image": candidate["images"]["phoenix"],
            "restart": "no",
            "networks": ["default"],
            "user": "65532:65532",
            "entrypoint": ["python", "/fixture/hermes_contract_fixture.py", "phoenix"],
            "command": [],
            "cap_drop": ["ALL"],
            "volumes": [
                fixture_mount,
                "phoenix-data:/data",
                "phoenix-identity:/phoenix-identity:ro",
            ],
            "depends_on": {
                "hermes-identities-init": {"condition": "service_completed_successfully"}
            },
        }
    )
    services["phoenix"] = phoenix
    return config


class Stack:
    def __init__(self, candidate, directory):
        self.candidate = candidate
        self.project = "hermes-contract-" + uuid.uuid4().hex[:12]
        self.path = Path(directory) / "compose.json"
        self.config = standalone_config(candidate)
        self.write()
        self.scan_count = 0
        self.scenarios = {}
        self.stage = "compose_created"

    def write(self):
        self.path.write_text(json.dumps(self.config))

    def compose(self, *arguments, timeout=30, input_text=None):
        return command(
            ["docker", "compose", "--project-name", self.project, "-f", str(self.path), *arguments],
            timeout=timeout,
            input_text=input_text,
        )

    def fixture(self, command_name, *arguments):
        return json.loads(
            self.compose(
                "exec",
                "-T",
                "web",
                "python",
                "/fixture/hermes_contract_fixture.py",
                command_name,
                *arguments,
            )
        )

    def controller_clean(self):
        code = (
            "import pathlib,json; "
            "homes=list(pathlib.Path('/tmp').glob('civicloop-hermes-run-*')); "
            "children=sum(b'start-hermes.py' in p.read_bytes() "
            "for p in pathlib.Path('/proc').glob('[0-9]*/cmdline') "
            "if p.parent.name!=str(__import__('os').getpid())); "
            "print(json.dumps({'child_count':children,'home_count':len(homes)}))"
        )
        counts = json.loads(self.compose("exec", "-T", "hermes", "python", "-c", code))
        require(counts == {"child_count": 0, "home_count": 0}, "controller_cleanup_incomplete")
        return "clean"

    def internal(self, service, path, body=None):
        # Only fixture endpoints and health probes are accepted by callers.
        code = (
            "import json,urllib.request; "
            f"r=urllib.request.Request({json.dumps('http://' + service + path)}, "
            f"data={repr(json.dumps(body).encode()) if body is not None else 'None'}, "
            "headers={'Content-Type':'application/json'}); "
            "print(urllib.request.urlopen(r,timeout=5).read().decode())"
        )
        return json.loads(self.compose("exec", "-T", service.split(":")[0], "python", "-c", code))

    def owner(self, seed, path, body=None, key=None):
        try:
            reply = json.loads(
                self.compose(
                    "exec",
                    "-T",
                    "web",
                    "python",
                    "/fixture/hermes_contract_http.py",
                    input_text=json.dumps(
                        {
                            "path": path,
                            "body": body,
                            "session": seed["session"],
                            "csrf": seed["csrf"],
                            "idempotency_key": key or "",
                        }
                    ),
                )
            )
        except ContractFailure:
            raise
        except Exception:
            raise ContractFailure("owner_http_unavailable") from None
        if set(reply) == {"failure_category"}:
            category = reply["failure_category"]
            require(
                category
                in {
                    "owner_response_bound",
                    "owner_response_schema",
                    "prohibited_authority",
                    "owner_request_invalid",
                    "owner_http_unavailable",
                },
                "owner_response_schema",
            )
            raise ContractFailure(category)
        require(
            set(reply) == {"http_status", "body"}
            and type(reply["http_status"]) is int
            and 100 <= reply["http_status"] <= 599
            and type(reply["body"]) is dict,
            "owner_response_schema",
        )
        self.scan(json.dumps(reply["body"]).encode())
        return reply["http_status"], reply["body"]

    def scan(self, raw):
        from tests.fakes.hermes_contract_model import PROHIBITED_MARKER

        self.scan_count += 1
        require(PROHIBITED_MARKER.encode() not in raw, "prohibited_content")
        require(
            re.search(rb"(?:scope_|cap_)[A-Za-z0-9_-]{43,125}", raw) is None, "prohibited_authority"
        )

    def validate(self, payload, schema):
        try:
            Draft202012Validator(
                json.loads((ROOT / "schemas/agents" / schema).read_text()),
                format_checker=FormatChecker(),
            ).validate(payload)
        except Exception:
            raise ContractFailure("owner_response_schema") from None

    def readiness(self):
        code, payload = self.owner({"session": "", "csrf": ""}, "/api/v1/health/ready")
        if code != 200:
            dependencies = payload.get("dependencies", {})
            raise ContractFailure(
                "deterministic_readiness",
                details={
                    "http_status": code,
                    "dependencies": {
                        name: dependencies[name]["ready"]
                        for name in ("postgres", "valkey")
                        if isinstance(dependencies, dict)
                        and isinstance(dependencies.get(name), dict)
                        and type(dependencies[name].get("ready")) is bool
                    },
                },
            )
        return digest(payload)

    def await_readiness(self, *, timeout=210):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.readiness()
                return
            except ContractFailure as error:
                self.last_readiness_failure = {
                    "failure_category": error.category,
                    "details": error.details,
                }
                if time.monotonic() >= deadline:
                    raise ContractFailure(
                        "stack_readiness_deadline", details=self.last_readiness_failure
                    ) from None
                time.sleep(0.5)

    def mode(self, mode):
        return self.internal("fixture-model:8088", "/fixture/mode", {"mode": mode})

    def start(self, seed):
        key = str(uuid.uuid4())
        path = f"/api/v1/workflows/{seed['workflow_id']}/hermes-runs"
        status, payload = self.owner(seed, path, {"revision_id": seed["revision_id"]}, key)
        require(status == 202, "owner_start_denied")
        self.validate(payload, "hermes-start.schema.json")
        replay_status, replay = self.owner(seed, path, {"revision_id": seed["revision_id"]}, key)
        require(replay_status == 202 and replay == payload, "idempotency_replay")
        return payload["run_id"]

    def wait(self, seed, run_id):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            self.readiness()
            status, payload = self.owner(seed, f"/api/v1/agent-runs/{run_id}")
            require(status == 200, "owner_status_failed")
            self.validate(payload, "hermes-status.schema.json")
            if payload["status"] in {"succeeded", "failed", "cancelled"}:
                return payload
            time.sleep(0.25)
        raise ContractFailure("run_deadline")

    def held(self):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            self.readiness()
            if self.internal("fixture-model:8088", "/fixture/state")["blocked"]:
                return
            time.sleep(0.25)
        raise ContractFailure("model_hold_unavailable")

    def healthy(self, service):
        deadline = time.monotonic() + 210
        while time.monotonic() < deadline:
            self.readiness()
            container_id = self.compose("ps", "-q", service).strip()
            require(bool(container_id), "component_unavailable")
            status = command(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id]
            ).strip()
            if status == "healthy":
                return
            time.sleep(0.5)
        raise ContractFailure("component_readiness_deadline")

    def resources(self):
        identifiers = self.compose("ps", "-a", "-q").split()
        counts = {"container_count": len(identifiers), "oom_count": 0, "restart_count": 0}
        for identifier in identifiers:
            state = json.loads(
                command(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        '{"oom":{{.State.OOMKilled}},"restart":{{.RestartCount}}}',
                        identifier,
                    ]
                )
            )
            counts["oom_count"] += int(state["oom"])
            counts["restart_count"] += state["restart"]
        return counts

    def failure_snapshot(self):
        """Inspect closed service/container fields and bounded log categories before cleanup."""
        evidence = {"stage": self.stage, "services": {}, "snapshot_status": "complete"}
        try:
            identifiers = self.compose("ps", "-a", "-q").split()
            for identifier in identifiers[:32]:
                state = json.loads(
                    command(
                        [
                            "docker",
                            "inspect",
                            "--format",
                            '{"service":"{{index .Config.Labels "com.docker.compose.service"}}",'
                            '"status":"{{.State.Status}}","exit_code":{{.State.ExitCode}},'
                            '"oom":{{.State.OOMKilled}},"restarts":{{.RestartCount}},'
                            '"health":"{{if .State.Health}}{{.State.Health.Status}}'
                            '{{else}}none{{end}}"}',
                            identifier,
                        ]
                    )
                )
                service = state.pop("service")
                require(service in self.config["services"], "snapshot_service_invalid")
                logs = command(["docker", "logs", "--tail", "100", identifier], include_stderr=True)
                state["log_evidence"] = diagnostic(logs)
                health_logs = command(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{if .State.Health}}{{json .State.Health.Log}}{{else}}[]{{end}}",
                        identifier,
                    ]
                )
                state["health_evidence"] = diagnostic(health_logs)
                evidence["services"][service] = state
        except Exception:
            evidence["snapshot_status"] = "incomplete"
        return evidence

    def record(self, name, seed, run_id, expected):
        status = self.wait(seed, run_id)
        require(status["status"] in expected, "unexpected_terminal_state")
        evidence = self.fixture("inspect", run_id)
        require(
            evidence["package_digest"] == seed["package_digest"], "deterministic_package_changed"
        )
        require(
            evidence["approval_count"]
            == evidence["receipt_count"]
            == evidence["provider_call_count"]
            == 0,
            "consequential_write",
        )
        require(evidence["capability_revoked"], "capability_live")
        require(evidence["reservation_status"] in {"settled", "released"}, "budget_unsettled")
        if status["status"] == "succeeded":
            require(
                evidence["proposal_count"] >= 1
                and evidence["pending_operation_count"] == 3
                and evidence["operation_count"] == 3
                and evidence["pending_provider_count"] == 2,
                "pending_operations_missing",
            )
        _, pending = self.owner(seed, f"/api/v1/agent-runs/{run_id}/pending-operations")
        self.validate(pending, "pending-operation-page.schema.json")
        evidence["controller_cleanup_status"] = self.controller_clean()
        evidence["failure_category"] = status["failure_category"]
        evidence["resources"] = self.resources()
        require(
            evidence["resources"]["oom_count"] == evidence["resources"]["restart_count"] == 0,
            "resource_failure",
        )
        logs = self.compose("logs", "--no-color", "--tail", "1000")
        self.scan(logs.encode())
        self.scenarios[name] = evidence
        return evidence

    def gates(self, enabled, *, include_worker=True):
        affected = (
            ("web", "worker", "hermes-transport") if include_worker else ("web", "hermes-transport")
        )
        for name in affected:
            for flag in ("CIVICLOOP_HERMES_ENABLED", "CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED"):
                self.config["services"][name]["environment"][flag] = str(enabled).lower()
        self.write()
        self.compose(
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            *affected,
            timeout=45,
        )

    def cleanup(self):
        try:
            self.compose("down", "--volumes", "--remove-orphans", "--timeout", "5", timeout=45)
            remaining = command(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    "label=com.docker.compose.project=" + self.project,
                ]
            )
            require(not remaining.strip(), "cleanup_incomplete")
            for resource in ("network", "volume"):
                remaining = command(
                    [
                        "docker",
                        resource,
                        "ls",
                        "-q",
                        "--filter",
                        "label=com.docker.compose.project=" + self.project,
                    ]
                )
                require(not remaining.strip(), "cleanup_incomplete")
            return "clean"
        except ContractFailure:
            return "incomplete"


def run_pending_operations_contract(candidate_manifest=None) -> dict[str, object]:
    path = candidate_manifest or os.environ.get("CIVICLOOP_HERMES_CANDIDATE_MANIFEST")
    require(bool(path), "manifest_required")
    candidate = load_candidate(path)
    identities = {}
    for name, reference in candidate["images"].items():
        data = json.loads(command(["docker", "image", "inspect", reference]))[0]
        identities[name] = data["Id"]
        if name in {"app", "hermes"}:
            require(
                data.get("Config", {}).get("Labels", {}).get("org.opencontainers.image.revision")
                == candidate["public_sha"],
                "image_sha_mismatch",
            )
    command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--entrypoint",
            "python",
            "-e",
            "PYTHONPATH=/app/runtime",
            candidate["images"]["app"],
            "-c",
            "import deploy.hermes.adapter,deploy.hermes.transport_service,"
            "deploy.hermes.identity_init",
        ]
    )
    with tempfile.TemporaryDirectory(prefix="hermes-contract-") as directory:
        stack = Stack(candidate, directory)
        evidence = {
            "status": "failed",
            "candidate_digest": digest(candidate),
            "image_ids": identities,
            "fixture_digest": tree_digest(FIXTURES),
            "scenarios": stack.scenarios,
        }
        try:
            # The helper mount contains frozen fixture code only; all runtime modules are baked.
            stack.config["services"]["web"]["volumes"].append(
                {"type": "bind", "source": str(FIXTURES), "target": "/fixture", "read_only": True}
            )
            stack.write()
            stack.stage = "compose_validation"
            stack.compose("config", "--quiet")
            stack.stage = "stack_startup"
            stack.compose("up", "-d", "--no-build", "--pull", "never", timeout=240)
            stack.stage = "deterministic_readiness"
            stack.await_readiness()
            stack.stage = "fixture_seed"
            seed = stack.fixture("seed")
            stack.stage = "delayed_revoked_a"
            stack.mode("hold")
            run_a = stack.start(seed)
            stack.held()
            code, _ = stack.owner(seed, f"/api/v1/agent-runs/{run_a}/cancel", {})
            require(code == 202, "owner_cancel_failed")
            stack.record("delayed_revoked_a", seed, run_a, {"cancelled"})
            before = stack.internal("fixture-model:8088", "/fixture/state")["call_count"]
            replay = stack.internal("fixture-proxy:8089", "/fixture/replay", {})
            require(replay["replay_status"] == 403, "revoked_scope_forwarded")
            require(
                stack.internal("fixture-model:8088", "/fixture/state")["call_count"] == before,
                "revoked_scope_forwarded",
            )
            stack.mode("release")
            time.sleep(0.5)
            require(stack.fixture("inspect", run_a)["operation_count"] == 0, "late_durable_write")
            stack.mode("success")
            stack.stage = "success_b"
            seed_b = stack.fixture("seed")
            before_calls = stack.internal("fixture-model:8088", "/fixture/state")["call_count"]
            before_nonce = json.loads(
                stack.compose(
                    "exec",
                    "-T",
                    "litellm",
                    "python",
                    "-c",
                    "import sqlite3,json; "
                    "c=sqlite3.connect('/var/lib/civicloop-model-gateway/ledger.sqlite3'); "
                    "print(json.dumps({'count':c.execute("
                    "'SELECT COUNT(*) FROM nonces').fetchone()[0]}))",
                )
            )["count"]
            stack.record("success_b", seed_b, stack.start(seed_b), {"succeeded"})
            after_calls = stack.internal("fixture-model:8088", "/fixture/state")["call_count"]
            nonce = json.loads(
                stack.compose(
                    "exec",
                    "-T",
                    "litellm",
                    "python",
                    "-c",
                    "import sqlite3,json; "
                    "c=sqlite3.connect('/var/lib/civicloop-model-gateway/ledger.sqlite3'); "
                    "print(json.dumps({'count':c.execute("
                    "'SELECT COUNT(*) FROM nonces').fetchone()[0], "
                    "'distinct':c.execute("
                    "'SELECT COUNT(DISTINCT nonce) FROM nonces').fetchone()[0],"
                    "'digest':__import__('hashlib').sha256(json.dumps(c.execute("
                    "'SELECT nonce FROM nonces ORDER BY nonce').fetchall())"
                    ".encode()).hexdigest()}))",
                )
            )
            require(
                after_calls - before_calls == nonce["count"] - before_nonce == 7,
                "inference_nonce_mismatch",
            )
            require(nonce["count"] == nonce["distinct"], "nonce_reuse")
            evidence.update(
                {
                    "fake_model_call_count": after_calls - before_calls,
                    "inference_attempts": 7,
                    "distinct_assertion_nonces": 7,
                    "nonce_evidence_digest": nonce["digest"],
                    "inference_evidence_scope": "success_b",
                }
            )
            stack.mode("invalid")
            stack.stage = "invalid_output"
            invalid_seed = stack.fixture("seed")
            stack.record("invalid_output", invalid_seed, stack.start(invalid_seed), {"failed"})
            stack.mode("hold")
            stack.stage = "cancellation"
            cancel_seed = stack.fixture("seed")
            cancel_run = stack.start(cancel_seed)
            stack.held()
            stack.owner(cancel_seed, f"/api/v1/agent-runs/{cancel_run}/cancel", {})
            stack.record("cancellation", cancel_seed, cancel_run, {"cancelled"})
            stack.mode("release")
            stack.mode("hold")
            stack.stage = "kill_switch"
            kill_seed = stack.fixture("seed")
            kill_run = stack.start(kill_seed)
            stack.held()
            stack.gates(False, include_worker=False)
            stack.mode("release")
            code, _ = stack.owner(
                kill_seed,
                f"/api/v1/workflows/{kill_seed['workflow_id']}/hermes-runs",
                {"revision_id": kill_seed["revision_id"]},
                str(uuid.uuid4()),
            )
            require(code == 503, "kill_switch_admission_open")
            stack.record("kill_switch", kill_seed, kill_run, {"failed"})
            stack.scenarios["kill_switch"]["web_transport_recreated"] = True
            stack.gates(False)
            stack.gates(True)
            stack.mode("success")
            for component in ("mcp", "litellm"):
                stack.stage = component + "_outage"
                stack.compose("stop", "--timeout", "3", component)
                outage_seed = stack.fixture("seed")
                stack.record(
                    component + "_outage", outage_seed, stack.start(outage_seed), {"failed"}
                )
                stack.compose("start", component)
                stack.healthy(component)
            stack.compose("stop", "--timeout", "3", "phoenix")
            stack.stage = "phoenix_outage"
            deterministic_seed = stack.fixture("seed-draft")
            code, _ = stack.owner(
                deterministic_seed,
                f"/api/v1/workflows/{deterministic_seed['workflow_id']}/runs",
                {},
            )
            require(code == 200, "deterministic_operation_failed")
            phoenix_seed = stack.fixture("seed")
            stack.record("phoenix_outage", phoenix_seed, stack.start(phoenix_seed), {"succeeded"})
            stack.compose("start", "phoenix")
            stack.stage = "restored_success"
            restored_seed = stack.fixture("seed")
            stack.record(
                "restored_success", restored_seed, stack.start(restored_seed), {"succeeded"}
            )
            time.sleep(2)
            traces = stack.internal("fixture-proxy:8089", "/fixture/state")
            stack.stage = "final_evidence"
            require(
                traces["trace_scan_failure_count"] == 0 and traces["correlated_span_count"] >= 6,
                "correlated_trace_missing",
            )
            logs = stack.compose("logs", "--no-color", "--tail", "1000")
            stack.scan(logs.encode())
            require(set(stack.scenarios) == set(SCENARIOS), "scenario_matrix_incomplete")
            evidence.update(traces)
            evidence.update(
                {
                    "status": "passed",
                    "provider_call_count": 0,
                    "approval_count": 0,
                    "receipt_count": 0,
                    "proposal_count": stack.scenarios["success_b"]["proposal_count"],
                    "pending_operation_count": 3,
                    "pending_provider_count": 2,
                    "content_scan_count": stack.scan_count,
                    "config_digest": digest(stack.config),
                    "control_proxy_used": True,
                }
            )
            load_candidate(path)
        except ContractFailure as error:
            evidence["status"] = "failed"
            evidence["failure_category"] = error.category
            evidence["command_evidence"] = error.details
        except Exception:
            evidence["status"] = "failed"
            evidence["failure_category"] = "contract_unavailable"
        finally:
            if evidence["status"] != "passed":
                evidence["failure_state"] = stack.failure_snapshot()
            evidence["cleanup_status"] = stack.cleanup()
            if evidence["cleanup_status"] != "clean":
                evidence["status"] = "failed"
                evidence["failure_category"] = "cleanup_incomplete"
        return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--evidence", required=True)
    arguments = parser.parse_args()
    try:
        evidence = run_pending_operations_contract(arguments.candidate_manifest)
    except ContractFailure as error:
        evidence = {
            "status": "failed",
            "failure_category": error.category,
            "cleanup_status": "not_started",
        }
    except Exception:
        evidence = {
            "status": "failed",
            "failure_category": "contract_unavailable",
            "cleanup_status": "not_started",
        }
    Path(arguments.evidence).write_text(json.dumps(evidence, sort_keys=True, indent=2))
    print(json.dumps(evidence, sort_keys=True))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
