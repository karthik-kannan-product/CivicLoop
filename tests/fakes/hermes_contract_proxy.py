"""Bounded fixture observers forwarding to real transport and Phoenix.

Authority and OTLP bodies remain in memory and never enter fixture diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONTROL = "/internal/control/v1/scopes"
REVOKE = CONTROL + "/revoke"
SCOPE_HEADER = "X-CivicLoop-Transport-Scope"
MARKER = b"TASK8_SYNTHETIC_CONTENT_MARKER_4d70ae"


class State:
    def __init__(self):
        self.scopes = []
        self.spans = []
        self.scan_failures = 0
        self.scan_count = 0
        self.exports = 0
        self.lock = threading.Lock()
        self.secrets = [
            p.read_bytes().strip() for p in Path("/run/secrets").iterdir() if p.is_file()
        ]
        self.phoenix_token = Path("/telemetry-identity/otlp-token").read_text().strip()
        self.secrets.append(self.phoenix_token.encode())


STATE = None


def forward(url, body, headers):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=3) as reply:
            return reply.status, reply.read(1048577)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1048577)
    except Exception:
        return 503, b'{"status":"dependency_unavailable"}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, raw):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path != "/fixture/state":
            return self.reply(404, b'{"status":"denied"}')
        with STATE.lock:
            spans = list(STATE.spans)
            worker = [s for s in spans if s[2] == "civicloop.hermes.worker"]
            correlated = sum(
                1
                for s in spans
                if s[2] == "civicloop.mcp.tool"
                and any(s[0] == w[0] and s[3] == w[1] for w in worker)
            )
            evidence = {
                "scope_count": len(STATE.scopes),
                "span_count": len(spans),
                "correlated_span_count": correlated,
                "trace_scan_count": STATE.scan_count,
                "trace_scan_failure_count": STATE.scan_failures,
                "accepted_export_count": STATE.exports,
                "trace_digest": hashlib.sha256(repr(sorted(spans)).encode()).hexdigest(),
            }
        self.reply(200, json.dumps(evidence).encode())

    def do_POST(self):  # noqa: N802
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1048576:
                raise ValueError
            raw = self.rfile.read(size)
            if self.path == "/fixture/replay":
                if json.loads(raw) != {}:
                    raise ValueError
                with STATE.lock:
                    if not STATE.scopes:
                        raise ValueError
                    scope, expiry = STATE.scopes[-1]
                if time.monotonic() > expiry:
                    raise ValueError
                token = Path("/run/secrets/civicloop-hermes-shim-client-token").read_text().strip()
                status, _ = forward(
                    "http://hermes-transport:8080/v1/chat/completions",
                    b'{"model":"civicloop-default","messages":[{"role":"user","content":"synthetic"}]}',
                    {
                        "Authorization": "Bearer " + token,
                        SCOPE_HEADER: scope,
                        "Content-Type": "application/json",
                    },
                )
                return self.reply(200, json.dumps({"replay_status": status}).encode())
            if self.path in {CONTROL, REVOKE}:
                scope = self.headers.get(SCOPE_HEADER, "")
                if self.path == CONTROL:
                    with STATE.lock:
                        if (
                            len(STATE.scopes) >= 16
                            or not scope.startswith("scope_")
                            or len(scope) != 49
                        ):
                            raise ValueError
                        STATE.scopes.append((scope, time.monotonic() + 300))
                headers = {
                    k: self.headers[k]
                    for k in (
                        "Authorization",
                        SCOPE_HEADER,
                        "X-CivicLoop-Capability",
                        "Content-Type",
                    )
                    if k in self.headers
                }
                status, response = forward("http://hermes-transport:8080" + self.path, raw, headers)
                return self.reply(status, response)
            if self.path == "/v1/traces":
                if self.headers.get("Authorization") != "Bearer " + STATE.phoenix_token:
                    return self.reply(401, b'{"status":"denied"}')
                from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                    ExportTraceServiceRequest,
                )

                request = ExportTraceServiceRequest()
                request.ParseFromString(raw)
                spans = [
                    (s.trace_id.hex(), s.span_id.hex(), s.name, s.parent_span_id.hex())
                    for resource in request.resource_spans
                    for scope in resource.scope_spans
                    for s in scope.spans
                ]
                with STATE.lock:
                    STATE.scan_count += 1
                    STATE.scan_failures += int(
                        any(marker in raw for marker in [MARKER, *STATE.secrets])
                    )
                    if len(STATE.spans) + len(spans) > 32768:
                        raise ValueError
                status, response = forward(
                    "http://phoenix:6006/v1/traces",
                    raw,
                    {
                        "Content-Type": "application/x-protobuf",
                        "Authorization": "Bearer " + STATE.phoenix_token,
                    },
                )
                if status == 200:
                    with STATE.lock:
                        STATE.spans.extend(spans)
                        STATE.exports += 1
                return self.reply(status, response)
            self.reply(404, b'{"status":"denied"}')
        except Exception:
            self.reply(503, b'{"status":"fixture_unavailable"}')


if __name__ == "__main__":
    STATE = State()
    ThreadingHTTPServer(("0.0.0.0", 8089), Handler).serve_forever()
