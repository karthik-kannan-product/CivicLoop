"""Trusted config keeps the pinned custom provider's output request bounded."""

from types import SimpleNamespace

import yaml

from deploy.hermes.process_controller import _write_config


def test_scoped_config_custom_provider_limit_matches_ephemeral_bridge(tmp_path):
    bridge = SimpleNamespace(
        base_url="http://127.0.0.1:43123", hermes_token="synthetic-bridge-identity"
    )
    _write_config(tmp_path, bridge)
    config = yaml.safe_load((tmp_path / "config.yaml").read_text())
    provider = config["providers"]["custom"]
    assert config["model"]["provider"] == "custom"
    assert provider["base_url"] == config["model"]["base_url"] == bridge.base_url + "/v1"
    assert provider["base_url"] == config["auxiliary"]["compression"]["base_url"]
    assert provider["extra_body"] == {"max_tokens": 2000}
    assert config["auxiliary"]["compression"]["extra_body"] == {"max_tokens": 2000}
    assert provider["default_model"] == config["model"]["default"] == "civicloop-default"
    assert provider["key_env"] == "OPENAI_API_KEY"
    assert provider["api_mode"] == "chat_completions"
    assert config["model"]["streaming"] is False
