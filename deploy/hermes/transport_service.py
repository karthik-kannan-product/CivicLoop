"""Protected configuration and bounded serving for the internal transport shim."""

import os
import threading
from pathlib import Path

from deploy.hermes.adapter import _read_token
from deploy.hermes.transport import ScopeRegistry, TransportPricing, TransportServer


class BoundedTransportServer(TransportServer):
    def __init__(self, *args, **kwargs):
        self._handlers = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._handlers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._handlers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._handlers.release()


def _assertion_key(path_value):
    path = Path(path_value)
    value = path.read_bytes().strip()
    if not 32 <= len(value) <= 4096 or path.stat().st_mode & 0o077:
        raise RuntimeError("Transport assertion identity unavailable")
    return value


def build_server(environment=None):
    env = os.environ if environment is None else environment
    try:
        identities = {
            name: _read_token(
                env[f"HERMES_TRANSPORT_{prefix}_TOKEN_FILE"], label="transport identity"
            )
            for name, prefix in (
                ("control_token", "CONTROL"),
                ("client_token", "CLIENT"),
                ("mcp_token", "MCP"),
                ("gateway_token", "GATEWAY"),
            )
        }
        assertion_key = _assertion_key(env["HERMES_TRANSPORT_ASSERTION_KEY_FILE"])
        port = int(env.get("PORT", "8080"))
        if not 0 <= port <= 65535:
            raise ValueError
        enabled = (
            env.get("CIVICLOOP_HERMES_ENABLED", "false").lower() == "true"
            and env.get("CIVICLOOP_HERMES_PENDING_OPERATIONS_ENABLED", "false").lower() == "true"
        )
        pricing = TransportPricing(
            input_microusd_per_million=int(
                env.get("HERMES_TRANSPORT_INPUT_MICROUSD_PER_MILLION", "400000")
            ),
            output_microusd_per_million=int(
                env.get("HERMES_TRANSPORT_OUTPUT_MICROUSD_PER_MILLION", "1600000")
            ),
        )
        return BoundedTransportServer(
            ("0.0.0.0", port),
            registry=ScopeRegistry(),
            **identities,
            assertion_key=assertion_key,
            mcp_url=env["HERMES_TRANSPORT_MCP_URL"],
            gateway_url=env["HERMES_TRANSPORT_GATEWAY_URL"],
            pricing=pricing,
            enabled=lambda: enabled,
        )
    except KeyError, ValueError, OSError, RuntimeError:
        raise RuntimeError("Transport configuration unavailable") from None


def main():
    server = build_server()
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
