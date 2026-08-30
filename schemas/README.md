# Observable-agent JSON Schema contract index

This index covers the nine agent-observability and evaluation contracts frozen
by Task 1. Other schemas below this directory retain their own existing version
policy and identifiers. Every contract listed here uses JSON Schema 2020-12, a
closed object shape, and an immutable major.minor `$id`; the payload's
`schema_version` matches that published contract version.

| Area | Schema | Immutable `$id` | Purpose |
| --- | --- | --- | --- |
| Agents | `agents/fixture-manifest.schema.json` | `urn:civicloop:schema:agents:fixture-manifest:v1.0` | Synthetic fixture inventory and immutable content hashes |
| Agents | `agents/agent-run.schema.json` | `urn:civicloop:schema:agents:agent-run:v1.0` | Bounded, redacted lifecycle record for one agent run |
| Agents | `agents/agent-step.schema.json` | `urn:civicloop:schema:agents:agent-step:v1.0` | Bounded, redacted lifecycle record for one agent step |
| Agents | `agents/model-profile.schema.json` | `urn:civicloop:schema:agents:model-profile:v1.0` | Versioned routing and inference limits |
| Agents | `agents/budget-record.schema.json` | `urn:civicloop:schema:agents:budget-record:v1.0` | Token and cost ledger record |
| Agents | `agents/telemetry-export.schema.json` | `urn:civicloop:schema:agents:telemetry-export:v1.0` | Privacy mode and synthetic-content eligibility |
| Agents | `agents/telemetry-metric-record.schema.json` | `urn:civicloop:schema:agents:telemetry-metric-record:v1.0` | Discriminated metric values and bounded dimensions |
| Evaluations | `evaluations/example.schema.json` | `urn:civicloop:schema:evaluations:example:v1.0` | A labeled, synthetic evaluation input reference |
| Evaluations | `evaluations/result.schema.json` | `urn:civicloop:schema:evaluations:result:v1.0` | A bounded, advisory evaluation outcome |

The following closed response schemas are the OpenAPI-indexed safe-read layer;
they do not change the nine frozen persistence/interchange contracts above:

| Area | Schema | Immutable `$id` | Purpose |
| --- | --- | --- | --- |
| Agents | `agents/run-read.schema.json` | `urn:civicloop:schema:agents:run-read:v1.0` | Owner/reviewer run, trace, and aggregate usage status |
| Agents | `agents/step-page.schema.json` | `urn:civicloop:schema:agents:step-page:v1.0` | Bounded sanitized step summaries |
| Agents | `agents/usage-read.schema.json` | `urn:civicloop:schema:agents:usage-read:v1.0` | Reservation and append-only ledger status |
| Evaluations | `evaluations/result-page.schema.json` | `urn:civicloop:schema:evaluations:result-page:v1.0` | Bounded advisory evaluation summaries |

## Compatibility

A published `$id` is immutable. Any compatible minor revision is published as a
parallel schema with a new major.minor identifier (for example, `:v1.1`) and a
matching `schema_version`; producers and consumers opt into it explicitly.
Removing a field or enum value, making an optional field required, tightening a
constraint, or changing meaning requires a new major contract (for example,
`:v2.0`). Never edit the shape associated with an already published `$id`.

These `:v1.0` contracts are newly frozen in the unmerged Task 1 branch, so this
initial publication does not revise an external v1 contract. A
`(manifest_id, revision)` maps to exactly one canonical SHA-256
`manifest_digest`; later persistence work enforces that mapping.
