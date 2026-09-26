from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "deploy" / "hermes" / "config.yaml"
IMAGE = (
    "docker.io/nousresearch/hermes-agent:v2026.9.11@"
    "sha256:9469b3e78b9545b6d576eb8887a95352e9a0ea83730eaf31431cf862ca1010e1"
)

ALLOWED_TOOLS = [
    "mcp__civicloop__get_event_revision",
    "mcp__civicloop__get_policy_context",
    "mcp__civicloop__request_clarification",
    "mcp__civicloop__propose_campaign_drafts",
    "mcp__civicloop__validate_proposal",
    "mcp__civicloop__request_eventbrite_draft",
    "mcp__civicloop__request_iterable_drafts",
    "mcp__civicloop__get_operation_status",
]


def main() -> int:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload = {
        "allowed_tools": ALLOWED_TOOLS,
        "disabled_toolsets": config["agent"]["disabled_toolsets"],
    }
    probe = """
import json
from hermes_cli.tools_config import _get_platform_tools
from model_tools import get_tool_definitions
from tools.registry import registry
from toolsets import TOOLSETS

payload = json.load(open('/probe/payload.json', encoding='utf-8'))
config = json.load(open('/probe/config.json', encoding='utf-8'))
disabled = payload['disabled_toolsets']
assert set(disabled) == set(TOOLSETS), (
    set(TOOLSETS) - set(disabled),
    set(disabled) - set(TOOLSETS),
)
assert config['plugins']['enabled'] == []
for name in payload['allowed_tools']:
    registry.register(
        name=name,
        toolset='mcp-civicloop',
        schema={
            'name': name,
            'description': 'contract probe',
            'parameters': {'type': 'object', 'properties': {}},
        },
        handler=lambda: None,
    )
registry.register_toolset_alias('civicloop', 'mcp-civicloop')
enabled = sorted(_get_platform_tools(config, 'api_server'))
definitions = get_tool_definitions(
    enabled_toolsets=enabled,
    disabled_toolsets=disabled,
    quiet_mode=True,
    skip_tool_search_assembly=True,
)
actual = sorted(item['function']['name'] for item in definitions)
expected = sorted(payload['allowed_tools'])
assert enabled == ['civicloop'], enabled
assert actual == expected, {'actual': actual, 'expected': expected}
print(json.dumps({'enabled_toolsets': enabled, 'effective_tools': actual}))
"""
    with tempfile.TemporaryDirectory(prefix="civicloop-hermes-probe-") as temp_dir:
        probe_dir = Path(temp_dir)
        (probe_dir / "payload.json").write_text(json.dumps(payload), encoding="utf-8")
        (probe_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--mount",
                f"type=bind,source={probe_dir},target=/probe,readonly",
                "--entrypoint",
                "python",
                IMAGE,
                "-c",
                probe,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    print(result.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
