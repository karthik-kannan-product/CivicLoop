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

The JSON field names remain `snake_case` for compatibility with the existing
CivicLoop v1 implementation. New v1 fields must follow that convention; a
different naming convention requires a new versioned contract rather than a
silent observable change.
