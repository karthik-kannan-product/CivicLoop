# CivicLoop API contracts

The canonical HTTP contract is `openapi/civicloop-v1.yaml`. Reusable payload
schemas live below `schemas/` and use JSON Schema 2020-12.

## View the documentation

Start CivicLoop and open `/api/docs`. The page bundles Swagger UI locally and
loads `/api/v1/contracts/openapi/civicloop-v1.yaml` from the same deployment.

The current API uses server-side session cookies. State-changing calls require
the `X-CSRFToken` header. Swagger UI reads the same-origin `csrftoken` cookie and
adds the header to non-GET requests; it never persists credentials.

## Administrator identity contract

The administrator surface is documented under two OpenAPI tags and fourteen
versioned routes. Its session is separate from the synthetic LaunchLoop demo
session and moves through four explicit stages:

1. `anonymous`
2. `password_verified` (five-minute pre-authentication only)
3. `recovery_restricted` (security recovery routes only)
4. `authenticated` (password plus confirmed TOTP)

Fresh verification is a server-side timestamp valid for ten minutes; it is not
a client-provided claim. Password changes, recovery-code regeneration, and
bulk session revocation require fresh verification. Recovery codes and TOTP
provisioning material are returned once and every such response declares
`Cache-Control: no-store`.

The schemas in `schemas/admin/` use closed object shapes. Credential inputs are
marked `writeOnly`; one-time provisioning outputs are marked `readOnly`.
Problem responses use RFC 9457 with stable `code` and `message` compatibility
extensions. Authentication throttles also expose the standard `Retry-After`
response header.

## Validate changes

```powershell
uv run python scripts/validate_api_contracts.py
uv run pytest tests/api_contracts -v
```

Any endpoint change must update its OpenAPI operation, affected JSON Schemas,
contract tests, and examples in the same commit. Never include real credentials,
member data, provider responses, or production identifiers in examples.

## Compatibility

Breaking request or response changes require a new versioned path or a reviewed
deprecation plan. Additive optional fields remain backward compatible only when
schemas and clients permit them.

## Schema compatibility

The JSON Schema index at `schemas/README.md` is scoped to the nine frozen
observable-agent contracts. Each is a JSON Schema 2020-12 document with an
immutable major.minor `$id` suffix (for example, `:v1.0`) and a matching
`schema_version` field. Closed shapes intentionally reject unknown fields: add,
remove, or reinterpret a field only through the compatibility process in that
index. OpenAPI remains the versioned index for HTTP contracts; these persistence
schemas do not imply an HTTP endpoint until one is explicitly added.
The JSON field names remain `snake_case` for compatibility with the existing
CivicLoop v1 implementation. New v1 fields must follow that convention; a
different naming convention requires a new versioned contract rather than a
silent observable change.

## Evaluation result representation

In `evaluations/result.schema.json` v1.0, the nested `example.example_id` is the
sole example identity. `evaluator_profile_id` plus
`evaluator_profile_revision` is the sole immutable model-profile coordinate. It
is populated for an `llm_judge` result and both fields are null for
deterministic or human evaluation. The nested `judge` object does not repeat that reference or carry a second
discriminator. Top-level `evaluator` is the sole discriminator for one
top-level `oneOf`: deterministic evaluation requires a closed empty `judge`; an
LLM judge requires only `config_id`; and human review requires `reviewer_id`
plus `review_policy_id`. Any `judge.kind` or cross-mode field is invalid.

This record remains advisory-only and cannot authorize a consequential action.

## Fixture manifest hashing

`manifest_digest` is the SHA-256 digest of a deterministic, non-recursive
projection of the fixture manifest. To compute it:

1. Start with the complete manifest object and omit only `manifest_digest`.
2. Serialize that object with the RFC 8785 JSON Canonicalization Scheme and
   encode the result as UTF-8 without a byte-order mark.
3. Compute SHA-256 over those canonical bytes and encode the 32-byte result as
   64 lowercase hexadecimal characters.

The schema restricts manifest keys and values to the RFC 8785 interoperable
domain; producers must reject non-finite numbers rather than serializing them.
The digest covers `schema_version`, identity and revision metadata,
`created_at`, the `synthetic` assertion, fixture IDs, kinds, paths, and member
hashes. Therefore a change to any covered value produces a different manifest
digest. A fixture member's `sha256` is separately computed over the exact fixture-file
bytes as stored, before any JSON parsing, normalization, newline
conversion, or transcoding. A `(manifest_id, revision)` may identify only one
such digest.

## Validation profile and immutable references

The checked-in validator applies the JSON Schema 2020-12 format-asserting
profile by constructing each observable fixture validator with jsonschema's
`FormatChecker`. Consequently, `format: date-time` is an asserted RFC 3339
constraint; date-only and malformed timestamps are rejected. Contract JSON is
parsed with duplicate-key rejection and rejects the non-finite `NaN`,
`Infinity`, and `-Infinity` extensions. OpenAPI YAML is likewise parsed with a
duplicate-key-rejecting safe loader before validation.

Run records freeze both fixture-manifest and model-profile coordinates as
logical ID plus revision, and include the canonical manifest digest. Budget and
LLM evaluation records likewise retain the referenced model-profile revision.
A `synthetic_full` export names the exact run and manifest ID, revision, and
digest and requires a successful synthetic-manifest verification assertion.
Task 2 persistence and export code must resolve those coordinates, prove the
manifest is synthetic, and reject an ID/revision that maps to a different
digest; the Task 1 schemas freeze the representation but do not implement that
lookup.

Evaluation `input_reference` and prompt `reference` values are bounded opaque
manifest-member/reference IDs, never paths or content. Later persistence work
must verify that each opaque ID belongs to the referenced immutable manifest.

## Cross-record fixture validation

The public validator requires an explicit non-null `fixture_root`; omission or
`None` cannot select a schema-only path. It also fails closed when the observable fixture directory is
missing, any mapped positive/negative pair is missing, or only one member of a
pair exists. A partial fixture root is accepted only through the explicit
`allow_partial_observable_fixtures` input used by isolated validator tests; the
CLI never enables it.

At this checked-in boundary, telemetry exports are validated against their
anchored agent run. `run_id`, `privacy_mode`, and fixture-manifest ID, revision,
and digest must match exactly. Later persistence and export implementations
must enforce the same invariant when resolving stored records.

## Safe agent read APIs

The durable control plane exposes four GET-only, session-authenticated routes:
`/api/v1/agent-runs/{runId}`, `/steps`, `/evaluations`, and `/usage`. Access is
limited to the owner administrator or an authenticated LaunchLoop approver.
Every response is `Cache-Control: no-store`; denials and missing records use the
RFC 9457 problem contract. The response schemas allowlist opaque identifiers,
bounded sanitized summaries, lifecycle state, trace ID, and numeric usage/cost.
They provide no arbitrary prompt, provider response, raw trace, or Phoenix proxy
surface.

Duration, token, and cost metric variants identify provider/model configuration
only through `model_profile_id` plus `model_profile_revision`; crossed or stale
provider/model label pairs cannot be represented. Evaluation-outcome metrics
explicitly carry no model-profile coordinate. Later runtime code resolves each
metric coordinate through the immutable model-profile contract.
