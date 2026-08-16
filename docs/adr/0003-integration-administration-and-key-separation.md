# ADR-0003: Separate integration credential administration and encryption keys

## Status

Accepted

## Date

2026-08-15

## Context

Integration credentials can authorize consequential external operations. The
owner identity key protects authentication factors, while an integration key
protects stored provider credentials. Sharing either key, or granting both to
every process, would turn a compromise of one service into a broader compromise
than its job requires.

## Decision

Integration administration is enabled only when both
`CIVICLOOP_ADMIN_IDENTITY_ENABLED` and `CIVICLOOP_INTEGRATIONS_ENABLED` are
true. Its browser route and API return 404 whenever either flag is disabled.
`/admin/security` remains controlled only by the identity flag.

Use a distinct externally managed AES-256-GCM key ring for integration
credentials. Compose mounts it read-only into `web` and `worker` only. The
worker explicitly has owner identity disabled and no identity-key mount;
`scheduler`, `migrate`, `db`, and `valkey` receive neither integration key
material nor a mount.

An unauthenticated, feature-gated integration readiness endpoint validates the
mounted key ring without returning key material. Deployment automation may
require that endpoint in addition to core health. A missing or invalid key
therefore fails the optional integration gate closed while `/api/v1/health/live`
continues to report core process liveness.

## Consequences

- Operators manage and restore-test a second host-only key ring.
- The integration flag must not be enabled until the read-only mount exists for
  both `web` and `worker`.
- CI uses generated synthetic integration keys and explicitly runs integration
  backend gates; no populated credential is stored in the repository.
- Key rotation retains old keys until every affected encrypted credential is
  re-encrypted or replaced.
