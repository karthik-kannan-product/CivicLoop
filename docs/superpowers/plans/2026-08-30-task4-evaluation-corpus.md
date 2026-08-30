# Task 4 Evaluation Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand LaunchLoop to 16 deterministic executable cases and exactly 100 versioned labeled examples while preserving the original six results.

**Architecture:** Keep policy decisions deterministic in `loops/launchloop/launchloop.py`. Store executable cases in `eval_cases.json` and labeled examples in a manifest-tracked `evaluations/labeled_examples.json`; validate identifiers, counts, schema versions, expected outcomes, and synthetic-only content at the existing fixture boundary.

**Tech Stack:** Python 3.11, JSON, pytest, Ruff, mypy, existing SHA-256 fixture manifest.

## Global Constraints

- Use synthetic data only and make no external API calls.
- Preserve the original six case IDs and expected results.
- Consequential actions remain human-approved; the evaluator cannot send, publish, schedule, price, segment, or export.
- Every new fixture is versioned, checksum-bound, and covered by integrity tests.

---

### Task 1: Expand deterministic scenario behavior and executable cases

**Files:**
- Modify: `loops/launchloop/launchloop.py`
- Modify: `loops/launchloop/eval_cases.json`
- Modify: `tests/launchloop/test_synthetic_data.py`

**Interfaces:**
- Consumes: the 15 Task 3 event fixtures and their `scenario_tags`.
- Produces: `run_evals() -> dict` with passed/total counts, schema version, case IDs, and results.

- [ ] Add failing tests requiring 16 unique executable cases, all 15 scenario tags, unchanged original six outcomes, and summary metadata.
- [ ] Run `uv run pytest tests/launchloop/test_synthetic_data.py -q` and confirm failure at the old six-case count.
- [ ] Add deterministic risk/status handling for DST ambiguity, free events, reschedules, accessibility conflicts, suppressed audiences, inert prompt injection, invalid links, and stale/duplicate delivery.
- [ ] Add one executable case for each previously uncovered scenario and strengthen expected risk assertions.
- [ ] Run the focused test and `uv run python loops/launchloop/launchloop.py --eval`; require 16/16.
- [ ] Commit with `feat: expand LaunchLoop deterministic evaluations`.

### Task 2: Add and validate 100 labeled examples

**Files:**
- Create: `loops/launchloop/evaluations/labeled_examples.json`
- Create: `scripts/generate_launchloop_labeled_examples.py`
- Modify: `scripts/validate_synthetic_data.py`
- Modify: `loops/launchloop/data/manifest.json`
- Modify: `loops/launchloop/data/fixture_metadata.json`
- Modify: `tests/launchloop/test_synthetic_data.py`

**Interfaces:**
- Consumes: valid executable `case_id` and `event_id` pairs.
- Produces: exactly 100 unique `example_id` records under schema version `1.0`.

- [ ] Add failing tests for count, uniqueness, case/event references, expected status, and schema version.
- [ ] Run the focused test and confirm the labeled fixture is absent.
- [ ] Add a deterministic generator and commit its generated JSON output.
- [ ] Extend fixture discovery and validation to cover the evaluation directory and labeled-example invariants.
- [ ] Increment the manifest revision, update fixture metadata, and regenerate canonical fixture/manifest hashes.
- [ ] Run the validator and require emitted counts for 16 cases and 100 labeled examples.
- [ ] Commit with `feat: add versioned LaunchLoop labeled corpus`.

### Task 3: Close Checkpoint A and synchronize documentation

**Files:**
- Modify: `README.md`
- Modify: `loops/launchloop/README.md`
- Modify: `.github/workflows/ci.yml`
- Modify: private observable-agent checklist after public verification.

**Interfaces:**
- Consumes: verified Task 4 counts and runner summary.
- Produces: accurate public status and a permanent CI gate.

- [ ] Update all six-case roadmap text to the actual 15-event, 16-case, 100-example state.
- [ ] Update CI to assert the versioned summary and labeled-example count.
- [ ] Run focused tests, full SQLite pytest, Ruff, mypy, API contracts, synthetic validator, deterministic evaluator, and frontend/build gates.
- [ ] Review, merge to public `main`, push, and require CI/demo/Pages success for the exact SHA.
- [ ] Mark Task 4 and Checkpoint A complete privately, merge, push, and verify both remote SHAs.
