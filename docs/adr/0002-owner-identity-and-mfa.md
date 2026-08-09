# ADR-0002: Use a separate owner identity with TOTP and restricted recovery

## Status

Accepted

## Date

2026-08-09

## Context

CivicLoop needs an administrator who can configure integrations, inspect agent
operations, and approve security-sensitive changes. The existing LaunchLoop
accounts are synthetic demo personas. Treating either persona as a production
administrator would merge two trust domains, make demonstrations capable of
reaching real credentials, and weaken the four-eyes workflow.

The initial self-hosted deployment is a single low-cost Vultr server with
PostgreSQL and Valkey. It needs strong owner authentication without adding a
large external identity platform before CivicLoop has multiple administrators.
The design must also support unattended container restarts without putting
factor seeds or recovery codes in Git, environment variables, logs, traces, or
browser storage.

## Decision

Use a CivicLoop-specific owner identity that is structurally separate from
LaunchLoop demo actors and Django's built-in administration surface.

- Password verification creates only a five-minute pre-authentication state.
- Full administrator access requires a confirmed six-digit, 30-second TOTP.
- Accepted TOTP counters are persisted so a code cannot be replayed.
- Recovery codes contain 128 random bits, are stored with password hashes, and
  can be consumed once under a database row lock.
- Recovery establishes a restricted session that can only replace TOTP and
  recovery codes; it cannot access normal administrator or LaunchLoop actions.
- Administrator sessions have a 30-minute idle limit and an immutable 12-hour
  absolute limit, enforced from database metadata.
- Password, factor, recovery-code, and bulk-session changes require or clear a
  ten-minute fresh-verification timestamp as appropriate.
- TOTP seeds use AES-256-GCM with owner/device-bound additional authenticated
  data. A versioned key ring is mounted read-only from outside the checkout.
- Only the web and migration/management-command contexts receive that mount.
  Worker and scheduler processes run with administrator identity disabled.
- Password and recovery throttles use HMAC-scoped Valkey keys and fail closed
  when the cache is unavailable.
- Security events are redacted and append-only; PostgreSQL triggers reject
  updates and deletes.
- The public contract is OpenAPI 3.1 plus JSON Schema 2020-12. Errors use RFC
  9457 Problem Details with stable CivicLoop extensions.

The feature remains controlled by `CIVICLOOP_ADMIN_IDENTITY_ENABLED`. Shipping
the code and enabling production are separate decisions.

## Alternatives considered

### Reuse LaunchLoop demo users

Rejected because demo identities are intentionally synthetic and exercise role
workflows. Giving them credential-management authority would cross the
repository's safety boundary and make a demo password consequential.

### Use Django admin as the owner interface

Rejected as the primary interface because Django admin does not model the
password-only, recovery-restricted, and fresh-verification states required for
integration secrets. Django admin remains available at an internal route for
framework operations but does not grant CivicLoop owner authority.

### Deploy an external identity provider immediately

Deferred. An external provider is valuable for multiple administrators, SSO,
central offboarding, and hardware-backed factors, but adds cost and operational
surface to the first single-owner deployment. The separate owner boundary makes
that later migration possible without elevating demo accounts.

### Store the TOTP seed in an environment variable or database plaintext

Rejected. Environment variables are commonly exposed through process and
orchestration diagnostics, while database plaintext makes a database backup
sufficient to reconstruct the factor. A separately backed-up mounted key ring
keeps the two compromise domains distinct.

### Allow recovery codes to create a normal session

Rejected. Recovery is a weaker, offline fallback. Restricting it to factor
replacement prevents a stolen recovery code from reaching integration keys or
normal administrator actions.

## Consequences

- Operators must create, permission, back up, restore-test, and rotate one host
  key-ring file separately from PostgreSQL.
- Losing both the key-ring backup and working TOTP device requires the SSH-only
  reset procedure and re-enrollment.
- Valkey becomes part of the administrator authentication availability path;
  an outage denies attempts rather than bypassing throttling.
- PostgreSQL, not SQLite, is required to prove append-only enforcement and
  concurrent single-use factor behavior before release.
- The extra administrator frontend remains a small, separate Vite entry and
  does not expose secrets to third-party QR services or browser storage.
- When CivicLoop gains multiple human administrators or enterprise SSO, a new
  ADR should supersede the singleton-owner constraint while retaining the
  separate trust domain and auditable state transitions.
