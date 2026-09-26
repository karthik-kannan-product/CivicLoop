# Internal workflow MCP broker

Task 7 adds a private, stateless JSON-RPC MCP endpoint at
`http://mcp:8000/internal/v1/mcp`. Only `agents.mcp_settings` installs its URLconf;
the public Django application does not route this endpoint. Compose exposes no
host port. The service joins `default` for the application database and
`hermes-runtime` for Hermes, with no provider-egress or agent-control membership.

The trusted CivicLoop caller issues authority with `issue_workflow_capability`.
It binds the existing Workflow UUID, EventRevision integer PK, DemoActor slug,
the revision snapshot digest, exact tool allowlist, audience, and a 1–300 second
lifetime. The bearer is a signed random opaque value; only its SHA256 digest is
stored. Issuance requires an active operator account. Revocation, expiry, actor
deactivation, a changed current revision, or changed revision contents deny
further use, including retries. The signing key is Django's application secret;
it is never provided to Hermes.

The HTTP caller supplies the distinct service identity in `Authorization: Bearer`
and the workflow capability in `X-CivicLoop-Capability`. Tool arguments contain
workflow/revision/actor identifiers, a correlation UUID, a request UUID and an
idempotency key. All must match the stored authority. The first accepted request
pins the correlation UUID; changed correlation or changed replay content fails.
Identical retries return the saved result and append a content-free replay audit.
Pending provider operations also deduplicate across newly issued capabilities
using actor, workflow, revision, provider, kind, and the exact action digest.
Proposal and operation-status references must also match the originating
capability's revision-content digest. A newly issued capability cannot authorize
old proposal content after an in-place mutation under the same revision PK.

`tools/list` publishes eight exact input schemas. Reads expose bounded event
facts and policy metadata. Clarifications and campaign proposals are untrusted
pending submissions. `validate_proposal` checks structure and explicitly returns
`execution_authorized: false`; deterministic policy checks and human review are
still required. Eventbrite/Iterable request tools only persist pending
`DraftOperation` records. A database constraint forbids approval, execution, or
receipts in this initial broker model. Task 11/12 execution work must replace that
constraint only when exact revision/action four-eyes approval, typed receipts,
and reconciliation are implemented. There is no adapter/provider invocation.

Payloads are capped at 64 KiB, nesting depth 8, 2,048 nodes, and 100 members per
collection; each tool schema applies tighter content limits and rejects unknown
fields. Audit records contain validated identifiers and outcomes, never content,
tokens or headers. Tool spans use a fixed name, allowlisted stage/outcome, and
disable automatic exception recording. Proposal bodies live in the application
database only, not audit logs or production traces.

The Hermes startup loader reads the separate identity file mounted as
`/run/secrets/civicloop-mcp-token`; its host path uses the existing
`CIVICLOOP_HERMES_MCP_TOKEN_FILE` setting. The value must be distinct from the
adapter, upstream-Hermes and gateway identities. The human provisions the file;
no credential values belong in Git or reports. Hermes waits for the authenticated
MCP liveness probe before starting.

Compatibility: pre-activation v1 contracts now use actual domain revision and
actor types instead of the earlier UUID placeholders. Pending operation approval
is null; non-pending operation schemas still require full approval metadata.
No deployed consumer is migrated by this change. Trusted per-run forwarding of
the capability HTTP header must be connected by the application runner before
live Hermes tools work; it must never be inserted into model-visible content.
Task 8's per-inference budget assertion bridge is not implemented here.

Public/development Compose intentionally uses `civicloop:local` for both MCP and
the application; merged-Compose tests mechanically assert that equality. The
immutable-digest requirement applies to private production, not local builds.
Production remains disabled. The private Compose contract requires the same
repository plus SHA256 digest for MCP and all application runtime services.
Artifact publication, exact-SHA image-label verification, and migration of the
existing local-build deploy script are prerequisites for activation, followed
by backup/restore, readiness, and rollback gates. Topology tests render Compose;
they do not constitute a deployed network or full end-to-end smoke.
