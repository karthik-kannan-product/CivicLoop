"""Fixed child entry point; no request data or transport scope is accepted."""

from __future__ import annotations

import os


def main() -> None:
    os.execvpe("hermes", ["hermes", "gateway", "run"], os.environ.copy())


if __name__ == "__main__":
    main()
