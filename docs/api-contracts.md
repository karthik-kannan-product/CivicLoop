# CivicLoop API contracts

The canonical HTTP contract is `openapi/civicloop-v1.yaml`. Reusable payload
schemas live below `schemas/` and use JSON Schema 2020-12.

## View the documentation

Start CivicLoop and open `/api/docs`. The page bundles Swagger UI locally and
loads `/api/v1/contracts/openapi/civicloop-v1.yaml` from the same deployment.

The current API uses server-side session cookies. State-changing calls require
the `X-CSRFToken` header. Swagger UI reads the same-origin `csrftoken` cookie and
adds the header to non-GET requests; it never persists credentials.

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
