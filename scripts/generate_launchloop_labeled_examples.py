import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPOSITORY_ROOT / "loops" / "launchloop" / "eval_cases.json"
OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "loops"
    / "launchloop"
    / "evaluations"
    / "labeled_examples.json"
)
LABELED_EXAMPLE_COUNT = 100


def build_labeled_examples() -> dict[str, object]:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    examples = []
    for index in range(LABELED_EXAMPLE_COUNT):
        case = cases[index % len(cases)]
        expected = case["expected"]
        examples.append(
            {
                "example_id": f"example_{index + 1:03d}",
                "case_id": case["case_id"],
                "event_id": case["event_id"],
                "case_type": case["type"],
                "input_variant": f"synthetic_variant_{index // len(cases) + 1:02d}",
                "expected_status": expected["status"],
                "expected_risk_flags": expected["must_have_risk_flags"],
                "expected_human_handoff": expected["must_require_human_handoff"],
                "synthetic": True,
            }
        )
    return {"schema_version": "1.0", "examples": examples}


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(build_labeled_examples(), indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {LABELED_EXAMPLE_COUNT} labeled examples at {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
