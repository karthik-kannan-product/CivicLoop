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
