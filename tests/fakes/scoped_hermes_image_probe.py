"""Run inside the exact derived image with networking disabled; emit counts only."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import yaml

from deploy.hermes.adapter import ALLOWED_TOOLS
from deploy.hermes.process_controller import ProcessController, _child_env, _write_config
from deploy.hermes.run_bridge import RunBridge


class FakeMCP(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = body.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": body["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-civicloop", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name.removeprefix("mcp__civicloop__"),
                        "description": "Synthetic probe tool",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                    for name in ALLOWED_TOOLS
                ]
            }
        elif method == "ping":
            result = {}
        elif "id" not in body:
            self.send_response(202)
            self.end_headers()
            return
        else:
            raise AssertionError("Probe must not call tools or models")
        raw = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main():
    scope = "scope_" + "a" * 43
    with tempfile.TemporaryDirectory(prefix="civicloop-image-probe-") as directory:
        home = Path(directory)
        bridge = RunBridge(
            scope_token=scope,
            shim_base_url="http://127.0.0.1:1",
            hermes_token="b" * 43,
            deadline=time.monotonic() + 30,
        )
        bridge.start()
        try:
            _write_config(home, bridge)
            config = yaml.safe_load((home / "config.yaml").read_text())
            assert config["model"]["streaming"] is False
            assert config["model"]["base_url"] == bridge.base_url + "/v1"
            assert config["auxiliary"]["compression"]["base_url"] == bridge.base_url + "/v1"
            assert config["mcp_servers"]["civicloop"]["url"] == bridge.base_url + "/mcp"
            assert scope not in (home / "config.yaml").read_text()
            assert scope not in json.dumps(_child_env(home, 9001, "probe-key"))
            os.environ["HERMES_HOME"] = str(home)
            from agent import auxiliary_client as auxiliary

            calls = []

            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[])

            async def acreate(**kwargs):
                return create(**kwargs)

            client = SimpleNamespace(
                base_url=bridge.base_url + "/v1",
                chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            )
            asynchronous = SimpleNamespace(
                base_url=bridge.base_url + "/v1",
                chat=SimpleNamespace(completions=SimpleNamespace(create=acreate)),
            )
            kwargs = {"model": "civicloop-default", "max_tokens": 8, "stream": True}
            auxiliary._create_with_progress_once(client, kwargs, "compression", force_stream=True)
            asyncio.run(
                auxiliary._acreate_with_progress(
                    asynchronous, kwargs, "compression", force_stream=True
                )
            )
            assert len(calls) == 2 and all(call["stream"] is False for call in calls)
        finally:
            bridge.close_admissions()
            bridge.drain(1)
            bridge.close()
    children = []

    def factory(*args, **kwargs):
        kwargs["stderr"] = subprocess.PIPE
        kwargs["stdout"] = subprocess.PIPE
        child = subprocess.Popen(*args, **kwargs)
        children.append(child)
        return child

    mcp = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCP)
    threading.Thread(target=mcp.serve_forever, daemon=True).start()
    controller = ProcessController(
        child_factory=factory,
        shim_base_url=f"http://127.0.0.1:{mcp.server_port}",
    )
    try:
        admitted = controller.admit(
            {"run_id": "probe-child", "timeout_seconds": 30}, scope_token=scope
        )
        assert admitted.status == "running"
        assert controller.stop(admitted.run_id).status == "cancelled"
        assert not controller.quarantined
    finally:
        if controller._active is not None:
            controller.stop("probe-child")
        for child in children:
            output, error = child.communicate(timeout=2)
            text = (output + error).decode(errors="replace")
            # Diagnostic categories only, never raw child logs or credentials.
            errors = re.findall(r"[A-Za-z]+Error(?=:)", text)
            modules = re.findall(r"No module named '[A-Za-z0-9._]+'", text)
            frames = re.findall(r'File "([^"]+)", line ([0-9]+)', text)
            print(
                json.dumps(
                    {
                        "child_exit": child.returncode,
                        "errors": errors,
                        "missing_modules": modules,
                        "frames": frames[-8:],
                    }
                )
            )
        mcp.shutdown()
        mcp.server_close()
    print(
        json.dumps(
            {
                "auxiliary_nonstream_calls": 2,
                "child_ready": True,
                "child_cleanup": True,
                "provider_calls": 0,
                "uid": os.getuid(),
            }
        )
    )


if __name__ == "__main__":
    main()
