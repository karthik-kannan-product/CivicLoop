import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_agent_read_contracts_are_closed_bounded_and_prompt_free() -> None:
    contract_paths = [
        ROOT / "schemas/agents/run-read.schema.json",
        ROOT / "schemas/agents/step-page.schema.json",
        ROOT / "schemas/agents/usage-read.schema.json",
        ROOT / "schemas/evaluations/result-page.schema.json",
    ]
    combined = ""
    for path in contract_paths:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["additionalProperties"] is False
        combined += json.dumps(schema).lower()

    for prohibited in ["raw_prompt", "raw_response", "provider_response_body", "credential"]:
        assert prohibited not in combined


def test_openapi_agent_reads_are_get_only_no_store_and_problem_shaped() -> None:
    specification = yaml.safe_load((ROOT / "openapi/civicloop-v1.yaml").read_text(encoding="utf-8"))
    agent_paths = {
        path: value
        for path, value in specification["paths"].items()
        if path.startswith("/api/v1/agent-runs/")
    }
    assert len(agent_paths) == 4
    for path_item in agent_paths.values():
        assert set(path_item) == {"get"}
        operation = path_item["get"]
        assert operation["responses"]["200"]["headers"]["Cache-Control"]
        for status in ["401", "403", "404"]:
            assert (
                operation["responses"][status]["$ref"] == "#/components/responses/ProblemResponse"
            )
