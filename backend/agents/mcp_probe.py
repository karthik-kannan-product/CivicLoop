"""Content-free authenticated MCP liveness probe; no credential logging."""

import json
import os
import urllib.request
from pathlib import Path


def main() -> int:
    try:
        with Path(os.environ["CIVICLOOP_MCP_TOKEN_FILE"]).open() as source:
            token = source.read(257).strip()
        request = urllib.request.Request(
            "http://localhost:8000/internal/v1/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
