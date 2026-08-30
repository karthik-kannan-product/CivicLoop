# Task 3 Synthetic Event Ecosystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand LaunchLoop to the approved fifteen synthetic event scenarios with the required associated data while preserving all six current evaluation outcomes.

**Architecture:** Keep the current engine-facing event and audience files compatible, add stable scenario tags to event records, and store related members, sponsors, histories, templates, review decisions, and provider outcomes in focused JSON fixtures. Extend the existing manifest validator to enforce counts, coverage, privacy, enums, revisions, and cross-file references without introducing a runtime provider dependency.

**Tech Stack:** Python 3.11, pytest, JSON, JSON Schema Draft 2020-12, Ruff, mypy, GitHub Actions.

## Global Constraints

- Preserve the existing six event IDs and six evaluation cases and their outcomes.
- Represent exactly the approved fifteen scenario categories with at least fifteen event records.
- Include at least thirty members and five sponsor/domain records.
- Use stable synthetic IDs, `example.test` contact domains, RFC 3339 timestamps, and canonical text checksums.
- Keep Task 4's additional executable cases and one hundred labeled examples out of scope.
- Keep Eventbrite and Iterable connection-test-only; add no send, publish, schedule, pricing, discount, segment, or export action.
- Add no dependency and commit no credential, production export, raw personal record, or populated environment file.

---

## File Structure

- Modify `loops/launchloop/data/events.json`: retain the current six records, add `scenario_tags`, and append nine scenario records.
- Modify `loops/launchloop/data/audience_segments.json`: add approved segments for new scenario regions while deliberately omitting Pennsylvania.
- Create `loops/launchloop/data/members.json`: thirty deterministic synthetic member records.
- Create `loops/launchloop/data/sponsors.json`: five deterministic sponsor/domain records.
- Create `loops/launchloop/data/event_histories.json`: revision, reschedule, and stale-delivery transitions.
- Create `loops/launchloop/data/content_templates.json`: invitation, reminder, and social templates in required locales.
- Create `loops/launchloop/data/review_decisions.json`: approved, edited, rejected, and invalidated review states.
- Create `loops/launchloop/data/provider_outcomes.json`: deterministic provider success and failure modes.
- Modify `loops/launchloop/data/fixture_metadata.json`: provenance, scenario tags, and PRD risks for every fixture.
- Modify `loops/launchloop/data/manifest.json`: revision 2, six new fixture entries, canonical checksums, and digest.
- Modify `scripts/validate_synthetic_data.py`: Task 3 counts and semantic/reference validation.
- Modify `tests/launchloop/test_synthetic_data.py`: red-green inventory, regression, and malformed-data tests.
- Modify `develop/civicloop/tasks/observable-agent-foundation-todo.md` in the private repository after public verification.

### Task 1: Expand and Validate the Fifteen-Scenario Event Catalog

**Files:**
- Modify: `tests/launchloop/test_synthetic_data.py`
- Modify: `loops/launchloop/data/events.json`
- Modify: `loops/launchloop/data/audience_segments.json`
- Modify: `scripts/validate_synthetic_data.py`
- Modify: `loops/launchloop/data/fixture_metadata.json`
- Modify: `loops/launchloop/data/manifest.json`

**Interfaces:**
- Consumes: `validate_synthetic_data(repository_root, manifest_path, schema_path) -> SyntheticDataSummary` and `run_evals() -> dict[str, Any]`.
- Produces: `REQUIRED_SCENARIO_TAGS: frozenset[str]` and `SyntheticDataSummary.scenario_count: int`.

- [ ] **Step 1: Write failing event-count, scenario-coverage, and six-case regression tests**

Add assertions equivalent to:

```python
from loops.launchloop.launchloop import run_evals

assert summary.revision == 2
assert summary.event_count == 15
assert summary.scenario_count == 15
assert run_evals()["summary"] == {"passed": 6, "total": 6}
```

Add a malformed fixture test that removes `invalid_signup_link` from its event and expects `ValueError("Missing required event scenario: invalid_signup_link")`.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/launchloop/test_synthetic_data.py -q`

Expected: FAIL because `scenario_count` does not exist, only six events are present, and required scenario tags are absent.

- [ ] **Step 3: Add the exact event scenario mapping**

Apply these stable tags to the six existing records and append the nine new records:

| Event ID | Scenario tag | Region | Special state |
| --- | --- | --- | --- |
| `evt_toronto_complete` | `complete_event` | ON | ready synthetic draft |
| `evt_ny_youth_day_v1` | `missing_venue` | NY | blank venue fields |
| `evt_ny_youth_day_v2` | `confirmed_revision` | NY | version 2 confirmed venue |
| `evt_montreal_bilingual` | `bilingual_policy` | QC | English/French required |
| `evt_bronze_mismatch` | `sponsor_tier_mismatch` | IL | 25% supplied vs 15% rule |
| `evt_philadelphia_no_segment` | `missing_segment` | PA | no PA segment |
| `evt_denver_dst_ambiguous` | `ambiguous_dst_timezone` | CO | local time ambiguity explicitly flagged |
| `evt_boston_free` | `free_event` | MA | ticket price and discount both zero |
| `evt_vancouver_hybrid` | `online_hybrid_event` | BC | venue plus synthetic online access URL |
| `evt_atlanta_rescheduled` | `rescheduled_event` | GA | version 2 and prior package invalidated |
| `evt_seattle_accessibility_conflict` | `accessibility_inconsistency` | WA | conflicting accessibility source fields |
| `evt_austin_suppressed_audience` | `suppressed_audience` | TX | audience contains suppressed members |
| `evt_detroit_prompt_injection` | `prompt_injection` | MI | description contains inert untrusted instruction text |
| `evt_miami_invalid_link` | `invalid_signup_link` | FL | explicitly invalid `https://invalid.example.test/...` link state |
| `evt_portland_duplicate_stale` | `duplicate_delivery_stale_revision` | OR | version 3 with stale version 2 delivery |

Each new record retains the existing required engine fields and adds only synthetic metadata such as `scenario_tags`, `source_state`, and scenario-specific booleans. Add approved segments for CO, MA, BC, GA, WA, TX, MI, FL, and OR; do not add PA.

- [ ] **Step 4: Extend event scenario validation**

Define:

```python
REQUIRED_SCENARIO_TAGS = frozenset({
    "complete_event", "missing_venue", "confirmed_revision", "bilingual_policy",
    "sponsor_tier_mismatch", "missing_segment", "ambiguous_dst_timezone",
    "free_event", "online_hybrid_event", "rescheduled_event",
    "accessibility_inconsistency", "suppressed_audience", "prompt_injection",
    "invalid_signup_link", "duplicate_delivery_stale_revision",
})
```

Require every event to have a non-empty, duplicate-free `scenario_tags` list, reject unknown tags, and raise a deterministic error for the first missing required tag. Set `scenario_count` to the number of represented required tags.

- [ ] **Step 5: Refresh the event/audience checksums and manifest digest**

Increment manifest revision to 2, update event and audience fixture hashes, update fixture-level metadata tags/risks, and recalculate the canonical manifest digest using the same sorted compact JSON projection as `_canonical_manifest_digest`.

- [ ] **Step 6: Run GREEN verification and commit**

Run:

```powershell
uv run pytest tests/launchloop/test_synthetic_data.py -q
uv run python scripts/validate_synthetic_data.py
python .\loops\launchloop\launchloop.py
```

Expected: Task 3 event assertions pass; validator reports 15 events; LaunchLoop reports `6/6`.

Commit: `feat: expand LaunchLoop to fifteen event scenarios`

### Task 2: Add Members, Sponsors, Histories, Templates, Reviews, and Provider Outcomes

**Files:**
- Modify: `tests/launchloop/test_synthetic_data.py`
- Create: `loops/launchloop/data/members.json`
- Create: `loops/launchloop/data/sponsors.json`
- Create: `loops/launchloop/data/event_histories.json`
- Create: `loops/launchloop/data/content_templates.json`
- Create: `loops/launchloop/data/review_decisions.json`
- Create: `loops/launchloop/data/provider_outcomes.json`
- Modify: `scripts/validate_synthetic_data.py`
- Modify: `loops/launchloop/data/fixture_metadata.json`
- Modify: `loops/launchloop/data/manifest.json`

**Interfaces:**
- Consumes: event and segment IDs from Task 1.
- Produces: `SyntheticDataSummary.member_count: int`, `sponsor_count: int`, and validated associated fixture records.

- [ ] **Step 1: Write failing associated-data tests**

Assert:

```python
assert summary.fixture_count == 13
assert summary.member_count == 30
assert summary.sponsor_count == 5
```

Add tests that mutate a member `segment_id`, history `event_id`, and provider `event_id` to unknown IDs and expect explicit `ValueError` messages before checksum validation.

- [ ] **Step 2: Run tests to verify RED**

Run: `uv run pytest tests/launchloop/test_synthetic_data.py -q`

Expected: FAIL because the six fixtures and summary fields do not exist.

- [ ] **Step 3: Add exact associated fixture inventories**

Create thirty members `member_001` through `member_030`, using emails `member001@example.test` through `member030@example.test`. Distribute them across the approved segment IDs; set `member_025` to `suppressed` and `member_026` to `unsubscribed` in Texas, and use null segment plus `clarification_required: true` only for the deliberate Pennsylvania record.

Create these five sponsors:

| Sponsor ID | Domain | Tier |
| --- | --- | --- |
| `sponsor_northstar` | `northstar.example.test` | platinum |
| `sponsor_civicbridge` | `civicbridge.example.test` | gold |
| `sponsor_harborlight` | `harborlight.example.test` | silver |
| `sponsor_lakeside` | `lakeside.example.test` | bronze |
| `sponsor_mapleleaf` | `mapleleaf.example.test` | gold |

Create histories `history_ny_import`, `history_ny_confirmation`, `history_atlanta_reschedule`, `history_atlanta_invalidation`, `history_portland_duplicate`, and `history_portland_stale`. Create templates `template_invitation_en`, `template_reminder_en`, `template_social_en`, `template_invitation_fr`, `template_reminder_fr`, and `template_social_fr`. Create decisions `decision_toronto_approved`, `decision_ny_edited`, `decision_seattle_rejected`, and `decision_atlanta_invalidated`. Create outcomes `outcome_eventbrite_success`, `outcome_eventbrite_transient`, `outcome_iterable_permanent`, `outcome_iterable_timeout`, `outcome_eventbrite_duplicate`, and `outcome_eventbrite_stale` with the six corresponding provider-result values.

- [ ] **Step 4: Validate associated fixture shapes and references**

Load each fixture by its manifest ID, require lists, and call `_require_unique_ids` with `member_id`, `sponsor_id`, `history_id`, `template_id`, `decision_id`, and `outcome_id`. Enforce:

```python
REVIEW_DECISIONS = {"approved", "edited", "rejected", "invalidated"}
PROVIDER_RESULTS = {
    "success", "transient_failure", "permanent_failure",
    "timeout", "duplicate", "stale_revision",
}
```

Require all non-null event/segment/sponsor/template references to resolve. Require exactly 30 members or more and five sponsors or more, at least one suppressed and one unsubscribed member, all review-decision states needed by the fixture, all six provider results, positive revisions/attempts, and RFC 3339 timestamps checked through `datetime.fromisoformat(value.replace("Z", "+00:00"))`.

- [ ] **Step 5: Register all six fixtures**

Add manifest IDs `launchloop_members`, `launchloop_sponsors`, `launchloop_event_histories`, `launchloop_content_templates`, `launchloop_review_decisions`, and `launchloop_provider_outcomes`. Add matching metadata entries with non-empty scenario tags and PRD risks, update all changed canonical checksums, and recalculate the revision-2 manifest digest.

- [ ] **Step 6: Run GREEN verification and commit**

Run:

```powershell
uv run pytest tests/launchloop/test_synthetic_data.py -q
uv run python scripts/validate_synthetic_data.py
uv run ruff check scripts/validate_synthetic_data.py tests/launchloop/test_synthetic_data.py
uv run mypy scripts/validate_synthetic_data.py
python .\loops\launchloop\launchloop.py
```

Expected: validator reports 13 fixtures, 15 events, 30 members, 5 sponsors, and 6 evaluation cases; LaunchLoop remains `6/6`.

Commit: `feat: add synthetic event ecosystem fixtures`

### Task 3: Harden Malformed-Data Coverage and Complete Public Verification

**Files:**
- Modify: `tests/launchloop/test_synthetic_data.py`
- Modify: `scripts/validate_synthetic_data.py`

**Interfaces:**
- Consumes: Task 2 validator and fixtures.
- Produces: permanent regression coverage for duplicate IDs, bad enums, broken references, missing scenarios, and prohibited personal/contact data.

- [ ] **Step 1: Add malformed-data tests**

Add focused mutations for duplicate member IDs, unknown sponsor IDs, invalid review decision `auto_approved`, invalid provider result `silently_retried`, missing required scenario, and `gmail.com` member email. Each test must assert the specific validator error and must fail before any needed validator fix.

- [ ] **Step 2: Run RED tests and add only missing validator checks**

Run each new test by node ID. If a test passes immediately because the validator already rejects it, keep it as coverage; otherwise add the smallest check that makes the expected boundary explicit.

- [ ] **Step 3: Run complete public gates**

Run:

```powershell
uv run pytest tests/launchloop/test_synthetic_data.py -q
uv run pytest tests/launchloop -q
uv run ruff check backend tests scripts
uv run mypy scripts
uv run python scripts/validate_api_contracts.py
python .\loops\launchloop\launchloop.py
$env:CIVICLOOP_ENV='test'; $env:DATABASE_URL='sqlite:///:memory:'; uv run pytest -q
```

Expected: zero failures, the existing expected skips only, validator counts satisfy Task 3, and deterministic evaluation is `6/6`.

- [ ] **Step 4: Review the complete change and commit**

Review correctness, readability, architecture, security, and performance. Confirm no live connector behavior, no dependency changes, no real contact domains, and no Task 4 corpus expansion.

Commit: `test: harden synthetic fixture integrity gates`

### Task 4: Merge, Record Checklist Completion, and Sync Cloud State

**Files:**
- Modify in private repository: `develop/civicloop/tasks/observable-agent-foundation-todo.md`

**Interfaces:**
- Consumes: verified public Task 3 branch.
- Produces: synchronized public/private `main`, completed Task 3 checklist state, and successful GitHub gates.

- [ ] **Step 1: Finish the public branch**

Use the finishing-a-development-branch workflow. Fast-forward merge `feature/task3-synthetic-data` into public `main`, rerun the complete SQLite-backed suite from merged `main`, and retain exact output.

- [ ] **Step 2: Update and commit the private checklist**

Change status to `Tasks 1-3 complete; Task 4 is next` and mark Task 3 checked. Verify the private worktree is otherwise clean.

Commit: `docs: mark synthetic event expansion complete`

- [ ] **Step 3: Push both repositories with Windows OpenSSH**

Use:

```powershell
$env:GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe -i C:/Users/devic/.ssh/id_ed25519 -o IdentitiesOnly=yes'
git push origin main
```

- [ ] **Step 4: Verify cloud state**

Confirm local `HEAD`, `origin/main`, and `git ls-remote origin refs/heads/main` are identical in both repositories. Require successful GitHub `ci`, `deploy-demo`, and Pages runs for the public SHA.

- [ ] **Step 5: Clean up**

Remove the clean `.worktrees/task3-synthetic-data` worktree, prune worktrees, delete the merged local feature branch, and verify both main checkouts are clean.
