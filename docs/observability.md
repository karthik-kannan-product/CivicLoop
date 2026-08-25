# Observable agent telemetry policy

This policy applies to persisted agent-run records, agent-step records, logs,
OpenTelemetry/OpenInference attributes, evaluation artifacts, and exported
diagnostic views. CivicLoop's public foundation uses synthetic data only.

## Permitted telemetry attributes

Record only the bounded identifiers and operational measurements required to
understand synthetic workflow behavior: opaque run, step, workflow, fixture,
model-profile, trace, and span identifiers; schema and policy revisions;
enumerated lifecycle states and failure categories; timestamps; queue and step
durations; retry counts; token counts; budget ledger amounts; model provider and
model identifiers; allowed tool names; synthetic fixture references and hashes;
and short, redacted summaries that contain no prompt or response body.

All persisted text must be bounded by its schema. Telemetry attributes not in a
checked-in schema or an approved instrumentation allowlist must be dropped.

## Forbidden telemetry attributes

Never record credentials, provider credentials, authentication material,
authentication headers, secrets, API keys, session cookies, bearer tokens,
passwords, TOTP values, recovery codes or other recovery data, encryption
material, or credential fingerprints. Never record raw personal records or
direct identifiers for real people, members, donors, sponsors, employees,
volunteers, customers, or payment subjects. Never record non-synthetic
prompt/response bodies, provider request or response bodies, tool arguments or
tool results that contain unreviewed text, request headers, IP addresses, or
free-form provider errors.

If an attribute cannot be classified as permitted, it is forbidden until the
policy and the corresponding versioned schema are reviewed and updated.

## Redaction and retention

Redact before telemetry crosses a process, persistence, or export boundary.
Use opaque IDs and hashes in place of content. Do not rely on downstream trace
processors to remove prohibited values. Delete or quarantine a record that is
found to violate this policy and investigate its originating instrumentation.

## Privacy modes

`synthetic_full` exports full synthetic prompts and responses plus sanitized
tool payloads only when the export record names the exact `run_id` and fixture
manifest ID, revision, and digest, and `synthetic_manifest_verified` is true.
The checked-in contract validator requires the export `run_id`, `privacy_mode`,
and manifest ID, revision, and digest to equal its anchored run. Later
persistence and export code must enforce the same relationship, resolve that
immutable coordinate, prove the manifest has `synthetic: true`, and reject an
ID/revision mapped to another digest. The assertion alone is not authorization. Non-synthetic runs must never
use this mode.

`pilot_minimized` exports only redacted summaries and hides prompt,
message, and tool content. `disabled` disables telemetry export while keeping
the workflow enabled. These modes are frozen by
`schemas/agents/telemetry-export.schema.json`.

## Frozen log and metric dimensions

The log-attribute allowlist is: `workflow_id`, `revision_id`, `package_id`,
`schema_version`, `policy_version`, `capability_profile`, `provider`, `model`,
`input_tokens`, `output_tokens`, `cost_microusd`, `fallback_category`,
`approval_state`, `connector_category`, `evaluation_labels`, `trace_id`, and
bounded durations. No other log attributes may be persisted.

Metric records are discriminated by name. Duration is a bounded numeric
measurement; token and cost counts are non-negative integers; and evaluation
outcomes carry an explicit `passed`, `failed`, or `inconclusive` outcome. Each variant permits only its relevant bounded labels. Duration, token, and
cost metrics require a `model_profile_id` plus `model_profile_revision`; the
profile is the sole provider/model truth and is resolved later. Those variants
also use only the applicable capability, fallback, or token-direction labels.
Evaluation outcomes explicitly have no model-profile coordinate and use one
bounded evaluation label. Never use user IDs, event IDs, request IDs, raw URLs, or
error text as metric labels.

## Run and step lifecycle timestamps

Every terminal run or step records `finished_at`; every non-terminal record has
`finished_at: null`. A queued run and a pending step have not started, so both
timestamps are null. A running run or step has `started_at` set and
`finished_at: null`. Succeeded and failed records set both timestamps.
Succeeded records have no failure category. Failed and cancelled terminal
states carry their respective enumerated categories.

A run can be cancelled before or after it starts. A cancelled run therefore
sets `finished_at` and `failure_category: cancelled`, while `started_at` is null
for pre-start cancellation and an RFC 3339 timestamp for in-progress
cancellation. A skipped step never starts: it has `started_at: null`, a
`finished_at` timestamp for the transition to the terminal skipped state, and
`failure_category: null`.

## Evaluation advisory boundary

Evaluation is advisory-only. It cannot authorize, approve, publish, send,
schedule, price, discount, select audiences, or modify event facts. Only the
deterministic workflow and its existing human-approval controls may perform
those state transitions or consequential actions.
## Immutable provenance and opaque references

Runs retain immutable fixture-manifest and model-profile coordinates as logical
ID plus revision, with the canonical manifest digest where content provenance
is required. Budget and LLM evaluation records retain the referenced profile
revision. Evaluation input and prompt references are bounded opaque IDs; they
must not contain filesystem traversal, paths, or raw content. Later persistence
work verifies coordinate resolution and manifest membership.
