from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from deploy.hermes.runtime_patch.apply import HELPER, render_replacement

ROOT = Path(__file__).resolve().parents[1]


def test_patch_manifest_guards_exact_source():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["upstream_commit"] == "939e45c91d751fadd94dcd1b873ac3cb44846213"
    assert len(manifest["files"]) == 1
    entry = manifest["files"][0]
    assert entry["path"] == "agent/auxiliary_client.py"
    assert entry["source_sha256"] == (
        "becbb3f3a6b9b847b95791e0c43d2689d5f07726f75b106f13afad8d45c5c9c7"
    )
    assert len(entry["replacement_sha256"]) == 64


def test_patch_rejects_source_drift():
    with pytest.raises(ValueError, match="source drift"):
        render_replacement(b"# not the reviewed Hermes source\n")


def test_patch_exact_source_and_both_auxiliary_lanes():
    # Preserved immutable upstream evidence; the exact image repeats this gate.
    source = Path("D:/git files/hermes-scope-upstream-evidence/agent/auxiliary_client.py")
    if not source.exists():
        pytest.skip("exact upstream source checked by derived-image build")
    rendered = render_replacement(source.read_bytes())
    entry = json.loads((ROOT / "manifest.json").read_text())["files"][0]
    assert hashlib.sha256(rendered).hexdigest() == entry["replacement_sha256"]
    assert rendered.count(b"_civicloop_nonstream_kwargs(client, kwargs)") == 3
    assert b'bounded["stream"] = False' in rendered


def test_nonstreaming_helper_rejects_remote_routes_and_preserves_original(monkeypatch):
    package = ModuleType("hermes_cli")
    config = ModuleType("hermes_cli.config")
    config.load_config_readonly = lambda: {"auxiliary": {"civicloop_nonstreaming": True}}
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", config)
    namespace = {}
    exec(HELPER, namespace)
    helper = namespace["_civicloop_nonstream_kwargs"]
    original = {
        "model": "civicloop-default",
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    result = helper(SimpleNamespace(base_url="http://127.0.0.1:9001/v1/"), original)
    assert result == {"model": "civicloop-default", "stream": False}
    assert original["stream"] is True
    for url in [
        "http://litellm:4000/v1",
        "https://127.0.0.1:9001/v1",
        "http://127.0.0.1:9001/v1?redirect=remote",
    ]:
        with pytest.raises(RuntimeError):
            helper(SimpleNamespace(base_url=url), original)
