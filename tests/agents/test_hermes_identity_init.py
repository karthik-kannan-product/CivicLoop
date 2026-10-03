from pathlib import Path

import pytest

from deploy.hermes import identity_init


def test_controller_identity_allowlist_excludes_gateway_and_mcp():
    assert identity_init.CONSUMERS["controller"] == (
        10000,
        ("civicloop-hermes-controller-token", "civicloop-hermes-shim-client-token"),
    )
    assert set(identity_init.CONSUMERS) == {"worker", "adapter", "controller", "transport", "mcp"}


def test_staging_rejects_duplicate_identities_before_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(identity_init, "_read_identity", lambda path, **kwargs: b"s" * 32)
    destination = tmp_path / "handoff"
    with pytest.raises(RuntimeError, match="Identity staging unavailable"):
        identity_init.stage_identities(source=Path("unused"), handoff=destination)
    assert not destination.exists()
