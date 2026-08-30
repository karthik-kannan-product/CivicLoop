# Task 3 Synthetic Event Ecosystem Design

## Purpose

Expand the LaunchLoop synthetic fixture inventory from six to at least fifteen event records while preserving the six existing deterministic evaluation outcomes. The new data must exercise the complete event-state matrix required by the approved Hermes observability design without adding live provider actions or Task 4's expanded evaluation corpus.

## Scope

Task 3 adds synthetic source data only:

- fifteen or more event records covering the approved scenario matrix;
- at least thirty synthetic members;
- at least five sponsor/domain records;
- approved and deliberately absent audience-segment situations;
- event histories, content templates, review decisions, and simulated provider outcomes;
- manifest, provenance, checksum, referential-integrity, and privacy validation.

The existing six event IDs and six executable evaluation cases remain unchanged. Additional executable cases and the one hundred labeled examples belong to Task 4. Eventbrite and Iterable remain synthetic or connection-test-only, and no send, publish, schedule, pricing, discount, segment, or export action is introduced.

## Scenario Catalog

The event inventory represents these fifteen required scenarios:

1. Complete Toronto event.
2. New York event with missing venue data.
3. Confirmed New York revision.
4. Montreal bilingual-policy event.
5. Chicago sponsor-tier mismatch.
6. Philadelphia event with no approved segment.
7. Ambiguous daylight-saving-time or timezone data.
8. Free event where paid-event discounts do not apply.
9. Online or hybrid event with channel-appropriate access requirements.
10. Rescheduled event that invalidates an earlier package or approval.
11. Accessibility inconsistency requiring resolution.
12. Suppressed or unsubscribed audience members that must be excluded.
13. Prompt-injection text treated as untrusted provider data.
14. Invalid signup or tracking link that blocks readiness.
15. Duplicate delivery or stale revision handled idempotently or rejected safely.

The first six use the current records. Nine additive records cover the remaining scenarios, with histories and provider outcomes used where a scenario requires multiple state transitions. Every event record receives one or more stable `scenario_tags`; adding those tags does not change the fields consumed by the current engine.

## Fixture Model

The existing `events.json`, `audience_segments.json`, policies, and six-case corpus remain authoritative for current behavior. Focused JSON fixtures are added for related data:

- `members.json`: stable member ID, synthetic profile attributes, status, language, region, approved segment reference when present, optional sponsor reference, and suppression state.
- `sponsors.json`: stable sponsor ID, synthetic `example.test` domain, tier, discount rule reference, and active status.
- `event_histories.json`: stable history ID, event reference, revision, transition type, RFC 3339 timestamp, and prior/current state markers.
- `content_templates.json`: stable template ID, channel, locale, version, required placeholders, and synthetic template body.
- `review_decisions.json`: stable decision ID, event reference, decision type (`approved`, `edited`, or `rejected`), revision, timestamp, and synthetic reason.
- `provider_outcomes.json`: stable outcome ID, event reference, provider, operation, result (`success`, `transient_failure`, `permanent_failure`, `timeout`, `duplicate`, or `stale_revision`), attempt, and timestamp.

All identifiers are unique and deterministic. Foreign keys must resolve, except that deliberate absence is modeled explicitly through a null segment reference plus an expected-clarification marker rather than through a broken reference.

## Manifest and Validation

Every new fixture is added to the versioned manifest and fixture metadata with a canonical text checksum, scenario tags, and PRD-risk mappings. Manifest revision increases because the fixture inventory changes.

The synthetic-data validator will enforce:

- at least fifteen events, thirty members, and five sponsors;
- complete representation of the fifteen scenario tags;
- unique stable IDs in every fixture;
- valid event, segment, sponsor, template, and policy references;
- valid review-decision and provider-outcome enums;
- chronological and revision consistency for histories and decisions;
- synthetic provenance, `example.test` contact domains, and absence of credential-like fields or values;
- valid signup links where a scenario expects readiness, with the invalid-link scenario marked explicitly;
- deterministic inventory and checksum validation across LF and CRLF checkouts.

## Compatibility

The current LaunchLoop engine continues to read its existing event and audience shapes. New fields added to event records are optional metadata owned by the synthetic dataset and are not required by the engine. The original six records are semantically unchanged, and `launchloop.py` must continue to report six of six cases passing.

## Testing

Development follows a red-green cycle:

1. Add failing tests for required counts, scenario coverage, associated-fixture presence, and referential integrity.
2. Add the minimal synthetic fixtures and validator support to pass those tests.
3. Add malformed-data tests for duplicate IDs, broken references, missing required scenarios, prohibited contact data, and invalid provider/review states.
4. Run the Task 3 validator, all LaunchLoop tests, Ruff, mypy, API-contract validation, deterministic LaunchLoop execution, and the complete repository test suite.
5. Re-run the complete suite after merging to `main`, push public and private checklist save points, and require successful GitHub CI and demo deployment.

## Completion Evidence

Task 3 is complete only when repository data and validator output prove the required counts, all fifteen scenario categories are represented, the existing six cases remain six of six, local and cloud test gates pass, the private checklist names Task 4 as next, and local and GitHub `main` SHAs match for both repositories.
