from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_runtime_pins_are_stable_and_aligned() -> None:
    version = (ROOT / ".python-version").read_text().strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)

    major, minor, _patch = (int(part) for part in version.split("."))
    minor_version = f"{major}.{minor}"
    next_minor = f"{major}.{minor + 1}"
    compact_minor = f"py{major}{minor}"

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["requires-python"] == f">={minor_version},<{next_minor}"
    assert project["tool"]["ruff"]["target-version"] == compact_minor
    assert project["tool"]["mypy"]["python_version"] == minor_version
    assert sys.version_info[:2] == (major, minor)

    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.count(f"FROM python:{version}-slim-bookworm") == 2
