"""Offline pure vendor/SDK contract; execute inside the exact cached Hermes image.

Mount the reviewed source read-only at /review and set PYTHONPATH=/review:/opt/hermes.
This probe creates no Hermes agent, model completion, or external provider request.
"""

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import yaml
from openai import OpenAI

from deploy.hermes.process_controller import _write_config


def main():
    with tempfile.TemporaryDirectory(prefix="civicloop-config-contract-") as directory:
        home = Path(directory)
        bridge = SimpleNamespace(
            base_url="http://127.0.0.1:43123", hermes_token="offline-contract-placeholder"
        )
        _write_config(home, bridge)
        config = yaml.safe_load((home / "config.yaml").read_text())
        os.environ["HERMES_HOME"] = str(home)
        from agent.agent_init import (
            _custom_provider_extra_body_for_agent,
            _merge_custom_provider_extra_body,
        )
        from agent.auxiliary_client import _build_call_kwargs, _get_task_extra_body
        from agent.transports.chat_completions import ChatCompletionsTransport
        from hermes_cli import runtime_provider
        from hermes_cli.config import get_compatible_custom_providers

        # Vendor runtime resolver chooses the existing explicit loopback alias.
        # The actual agent_init config merger then restores endpoint-bound extras.
        runtime_provider.load_config = lambda: config
        runtime = runtime_provider.resolve_runtime_provider(
            requested=config["model"]["provider"],
            explicit_base_url=config["model"]["base_url"],
            explicit_api_key="offline-contract-placeholder",
            target_model=config["model"]["default"],
        )
        agent = SimpleNamespace(
            provider=runtime["provider"],
            model=config["model"]["default"],
            base_url=runtime["base_url"],
            request_overrides=runtime.get("request_overrides", {}),
            max_tokens=None,
        )
        providers = get_compatible_custom_providers(config)
        _merge_custom_provider_extra_body(agent, providers)
        assert agent.request_overrides["extra_body"] == {"max_tokens": 2000}
        assert (
            _custom_provider_extra_body_for_agent(
                provider=agent.provider,
                model=agent.model,
                base_url="http://127.0.0.1:43124/v1",
                custom_providers=providers,
            )
            is None
        )
        main_kwargs = ChatCompletionsTransport().build_kwargs(
            model=agent.model,
            messages=[{"role": "user", "content": "synthetic-contract-input"}],
            base_url=agent.base_url,
            max_tokens=agent.max_tokens,
            request_overrides=agent.request_overrides,
            is_custom_provider=True,
        )
        auxiliary_extra = _get_task_extra_body("compression")
        assert auxiliary_extra["max_tokens"] == 2000
        auxiliary_kwargs = _build_call_kwargs(
            "custom",
            agent.model,
            [{"role": "user", "content": "synthetic-contract-input"}],
            max_tokens=None,
            base_url=agent.base_url,
            task="compression",
            extra_body=auxiliary_extra,
        )
        captured = []

        def capture(request):
            body = json.loads(request.content)
            assert type(body["max_tokens"]) is int and body["max_tokens"] == 2000
            assert "extra_body" not in body
            assert request.url.host == "127.0.0.1" and request.url.port == 43123
            captured.append(body)
            return httpx.Response(
                200,
                json={
                    "id": "synthetic",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "civicloop-default",
                    "choices": [],
                },
            )

        with OpenAI(
            api_key="offline-contract-placeholder",
            base_url=agent.base_url,
            http_client=httpx.Client(transport=httpx.MockTransport(capture)),
        ) as client:
            client.chat.completions.create(**main_kwargs)
            client.chat.completions.create(**auxiliary_kwargs)
        assert len(captured) == 2
        print(
            json.dumps(
                {
                    "status": "passed",
                    "main_top_level_max_tokens": captured[0]["max_tokens"],
                    "compression_top_level_max_tokens": captured[1]["max_tokens"],
                    "sdk_mock_request_count": len(captured),
                    "endpoint_mismatch_override_denied": True,
                    "provider_call_count": 0,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
