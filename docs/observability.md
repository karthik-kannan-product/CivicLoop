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

Never record credentials, authentication material, secrets, API keys, session
cookies, bearer tokens, passwords, TOTP values, recovery codes, encryption
material, or credential fingerprints. Never record raw personal records or
direct identifiers for real people, members, donors, sponsors, employees,
volunteers, customers, or payment subjects. Never record non-synthetic prompt/response bodies, provider request or response bodies, tool arguments or
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

`synthetic_full` exports full synthetic prompts and responses plus sanitized tool
payloads. `pilot_minimized` exports only redacted summaries and hides prompt,
message, and tool content. `disabled` disables telemetry export while keeping
the workflow enabled. These modes are frozen by
`schemas/agents/telemetry-export.schema.json`.

## Frozen log and metric dimensions

The log-attribute allowlist is: `workflow_id`, `revision_id`, `package_id`,
`schema_version`, `policy_version`, `capability_profile`, `provider`, `model`,
`input_tokens`, `output_tokens`, `cost_microusd`, `fallback_category`,
`approval_state`, `connector_category`, `evaluation_labels`, `trace_id`, and
bounded durations. No other log attributes may be persisted.

Metric label keys are limited to the bounded categorical dimensions
`capability_profile`, `provider`, `model`, `fallback_category`, `approval_state`,
`connector_category`, and `evaluation_labels`; values must be schema-enumerated
or bounded identifiers. Never use user IDs, event IDs, request IDs, raw URLs, or
error text as metric labels.

## Evaluation advisory boundary

Evaluation is advisory-only. It cannot authorize, approve, publish, send,
schedule, price, discount, select audiences, or modify event facts. Only the
deterministic workflow and its existing human-approval controls may perform
those state transitions or consequential actions.