# ADR-0001: Use contract-first OpenAPI and JSON Schema

## Status

Accepted

## Date

2026-08-08

## Context

CivicLoop has a Django JSON API, a React client, worker-driven workflows, and
future agent and connector interfaces. These consumers need one portable source
for paths, authentication, request/response shapes, errors, examples, and schema
versions. Framework-generated documentation would couple the contract to one
implementation and the current API does not use Django REST Framework.

## Decision

Maintain a checked-in OpenAPI 3.1 document and reusable JSON Schema 2020-12
files. Validate them in CI, exercise representative live payloads against the
same schemas, and render the OpenAPI document through locally bundled Swagger
UI assets. API errors use RFC 9457 Problem Details with stable CivicLoop
extensions.

## Alternatives considered

### Generate documentation from Django views

Rejected for this increment because the current views are plain Django
functions and adding a REST framework solely for documentation would expand the
runtime and migration scope.

### Maintain prose-only API documentation

Rejected because prose cannot drive schema validation, client generation, or
contract tests.

### Load Swagger UI from a CDN

Rejected because CivicLoop is self-hosted and its operator documentation should
remain available without a third-party runtime dependency.

## Consequences

- Contract changes begin in OpenAPI and JSON Schema and ship with tests.
- Reviewers must check implementation/contract parity.
- Swagger assets increase the frontend bundle size but do not affect the main
  CivicLoop application entry page.
- A future framework may generate code from the contract, but the checked-in
  standard remains authoritative.
