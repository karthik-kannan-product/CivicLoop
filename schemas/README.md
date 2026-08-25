# JSON Schema contract index

All schemas in this directory use JSON Schema 2020-12. A schema's `$id` is its
stable, versioned identifier: `:v1` denotes the first frozen major contract.
The `schema_version` field identifies the compatible minor revision within that
major version; this initial set is `1.0`.

| Area | Schema | Purpose |
| --- | --- | --- |
| Agents | `agents/fixture-manifest.schema.json` | Synthetic fixture inventory and immutable content hashes |
| Agents | `agents/agent-run.schema.json` | Bounded, redacted lifecycle record for one agent run |
| Agents | `agents/agent-step.schema.json` | Bounded, redacted lifecycle record for one agent step |
| Agents | `agents/model-profile.schema.json` | Versioned routing and inference limits |
| Agents | `agents/budget-record.schema.json` | Token and cost ledger record |
| Agents | `agents/telemetry-metric-record.schema.json` | Bounded metric name and label record |
| Evaluations | `evaluations/example.schema.json` | A labeled, synthetic evaluation input reference |
| Evaluations | `evaluations/result.schema.json` | A bounded evaluation outcome |

## Compatibility

Schemas are closed (`additionalProperties: false`), so any added field is a
breaking change for consumers. Add optional fields only in a new compatible
minor schema revision after clients have been updated to accept them. Change a
required field, remove a value, tighten an existing constraint, or change a
field's meaning only through a new major `$id` (for example, `:v2`) and a
parallel schema file. Never reuse a released `$id` for an incompatible shape.


## Task 1 release status

These :v1 contracts are newly frozen in the unmerged Task 1 branch; they do not revise a previously released external v1 schema. A (manifest_id, revision) maps to exactly one canonical SHA-256 manifest_digest; later persistence work enforces that mapping, while this contract fixes the representation and immutability rule now.
