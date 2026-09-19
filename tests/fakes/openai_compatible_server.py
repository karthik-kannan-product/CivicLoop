from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def compatible_response(request: Mapping[str, object], mode: str) -> dict[str, object]:
    if not isinstance(request.get("model"), str):
        return {"error": {"message": "unknown model"}}
    return {
        "id": "chatcmpl-civicloop-fixture",
        "object": "chat.completion",
        "created": 1_788_739_200,
        "model": "configured-upstream",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "A bounded CivicLoop event outline.",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 6, "total_tokens": 14},
        "fixture_mode": mode,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.endswith("/v1/chat/completions"):
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size))
        mode = os.environ.get("FAKE_PROVIDER_MODE", "compatible")
        mode_file = os.environ.get("MODE_FILE")
        if mode_file and Path(mode_file).exists():
            mode = Path(mode_file).read_text(encoding="utf-8").strip()
        if self.path.startswith("/recorded/"):
            mode = "recorded-openai"
        elif self.path.startswith("/error/"):
            mode = "error"
        elif self.path.startswith("/timeout/"):
            mode = "timeout"
        capture_file = os.environ.get("CAPTURE_FILE")
        if capture_file:
            Path(capture_file).write_text(json.dumps(request), encoding="utf-8")
        if mode == "timeout":
            time.sleep(float(os.environ.get("FAKE_PROVIDER_TIMEOUT_SECONDS", "5")))
        if mode == "error":
            self.send_response(503)
            payload = b'{"error":{"message":"provider-specific failure"}}'
        else:
            self.send_response(200)
            payload = json.dumps(compatible_response(request, mode)).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--one-shot")
    args = parser.parse_args()
    mode = os.environ.get("FAKE_PROVIDER_MODE", "compatible")
    if args.one_shot:
        print(json.dumps(compatible_response(json.loads(args.one_shot), mode)))
        return 0
    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    port_file = os.environ.get("PORT_FILE")
    if port_file:
        Path(port_file).write_text(str(server.server_port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
