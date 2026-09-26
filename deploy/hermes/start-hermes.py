"""Load only the Hermes-to-MCP identity before the normal Hermes CLI startup."""

import os
import re
from pathlib import Path


def main() -> None:
    try:
        with Path("/run/secrets/civicloop-mcp-token").open("r", encoding="ascii") as source:
            token = source.read(257).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise ValueError
    except (OSError, UnicodeError, ValueError):
        raise SystemExit("MCP service identity unavailable.") from None
    environment = {**os.environ, "CIVICLOOP_MCP_TOKEN": token}
    os.execvpe("hermes", ["hermes", "gateway", "run"], environment)


if __name__ == "__main__":
    main()
