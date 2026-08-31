# CivicLoop

Open-source agentic workflow loops for nonprofit operations.

CivicLoop is a public library of small, forkable agentic loops that nonprofit teams, technology volunteers, and implementation partners can adapt inside their own organizations. The project starts with **LaunchLoop**, a human-approved event campaign launch loop for small nonprofits.

The long-term CivicLoop vision is broader: reusable loops for membership lifecycle, sponsor entitlements, event operations, campaign communications, reporting, and agent observability. The repository is intentionally starting narrow so each loop can be inspected, tested, and adapted responsibly.

## Live Demo

LaunchLoop is published with GitHub Pages:

https://karthik-kannan-product.github.io/CivicLoop/

The public demo uses browser-local synthetic state so it can run safely on
GitHub Pages. Refreshes preserve the journey in that browser, and **Reset demo**
restores the seeded incomplete New York event. The Compose application uses
Django and PostgreSQL for durable workflow, revision, approval, audit, and
sandbox-receipt records.

## First Loop: LaunchLoop

LaunchLoop turns synthetic Eventbrite-style draft event data into a human-review campaign package:

- checks required event fields
- drafts invitation and reminder email copy
- drafts a LinkedIn/social post
- recommends an approved audience segment or asks for clarification
- validates sponsor discount rules
- preserves placeholders when details are missing
- blocks risky or incomplete packages
- refuses send, publish, pricing, discount, or segment changes without human approval
- records trace-style evidence for review

The loop is intentionally scoped to event campaign launch. It is not a full nonprofit operating system, production integration, or autonomous marketing tool.

## Repository Structure

```text
.
├── index.html                  # GitHub Pages entry point for LaunchLoop
├── loops/
│   └── launchloop/
│       ├── index.html          # Standalone browser demo
│       ├── launchloop.py       # Deterministic evaluator
│       ├── eval_cases.json     # 16 deterministic executable cases
│       ├── evaluations/        # 100 versioned labeled examples
│       ├── data/               # Synthetic event and audience data
│       └── policies/           # Synthetic approval, language, and discount rules
└── docs/
    ├── civicloop-vision.md
    └── launchloop-implementation-guide.md
```

## Application Foundation

CivicLoop now includes the container foundation for the self-hosted application.
The current increment provides the web shell, health contracts, PostgreSQL,
Valkey, Celery process modes, a synthetic authenticated demo, and a separately
feature-gated single-owner administrator security surface.

### Administrator security

The owner identity is separate from LaunchLoop's synthetic users. It uses a
password plus TOTP, single-use recovery codes, database-enforced session limits,
fresh verification for sensitive changes, fail-closed Valkey throttling, and
append-only PostgreSQL security events. The administrator API is specified in
OpenAPI 3.1 with JSON Schema 2020-12 payload contracts.

Before the first Compose start, create the external identity key-ring file and
set its absolute host path even while the feature flag remains disabled. Follow
[the administrator security runbook](docs/admin-security.md); the architecture
decision is recorded in
[ADR-0002](docs/adr/0002-owner-identity-and-mfa.md).

### Administrator integrations

Integration administration is separately feature-gated and requires the owner
identity feature. It is disabled by default. Operators who choose to enable it
must provision a distinct host-only key ring for integration credentials and
follow [the integration administration runbook](docs/integrations-administration.md).
The decision and trust-boundary rationale are recorded in
[ADR-0003](docs/adr/0003-integration-administration-and-key-separation.md).

For the complete, resumable local-to-GitHub-to-server delivery process, use the
[development-to-server runbook](docs/development-to-vultr-runbook.md).
### Authenticated demo journey

The self-hosted application now provides a temporary, synthetic two-role demo:

- `maya.operator` - operational staff; can reset the sandbox, resolve event facts, run LaunchLoop, and submit a package.
- `jordan.approver` - independent approver; can inspect the generated package, evidence, and audit trail, then approve the exact locked package.

For an authenticated server deployment, set a unique `CIVICLOOP_DEMO_PASSWORD` in the
host-only `.env` file and share it out of band. The application refuses to start in
production with the development or documented placeholder password.

The public GitHub Pages site remains browser-local and does not expose these accounts. The server application uses Django sessions and enforces the roles on every workflow action. Both accounts are synthetic and must be replaced before any real deployment.

### Prerequisites

- Docker 29 or newer
- Docker Compose 5 or newer

No host Python, Node.js, PostgreSQL client, or Valkey installation is required.
Docker Desktop (or another Docker Engine) must be running before any `docker compose`
command; the foundation cannot start when the Docker daemon is unavailable.

### Start

```powershell
Copy-Item .env.example .env
# Follow docs/admin-security.md to create the external key ring.
# Replace the example passwords, host path, and secret in the untracked .env file.
docker compose up -d --build
for ($attempt = 1; $attempt -le 30; $attempt++) {
  docker compose exec -T web python scripts/readiness.py --base-url http://localhost:8000
  if ($LASTEXITCODE -eq 0) { break }
  Start-Sleep -Seconds 2
}
if ($LASTEXITCODE -ne 0) {
  docker compose ps -a
  docker compose logs
  throw "CivicLoop did not become ready."
}
```

Open http://localhost:8000.

When explicitly enabled, the owner interface is at `/admin/security`; with both
administrator feature flags enabled, integration administration is at
`/admin/integrations`. Django's framework administration route is
`/internal/django-admin/`; it does not grant CivicLoop owner authorization.

### Verify

```powershell
docker compose exec web python backend/manage.py check
docker compose exec web python scripts/readiness.py --base-url http://localhost:8000
docker run --rm `
  -e CIVICLOOP_ENV=test `
  -e DATABASE_URL=sqlite:///:memory: `
  -v "${PWD}:/app" `
  -w /app `
  ghcr.io/astral-sh/uv:0.11.32 `
  uv run --python 3.11 pytest tests -v
python .\loops\launchloop\launchloop.py
```

Expected LaunchLoop result: `16 / 16` eval cases pass, with 100 labeled
examples validated by the synthetic fixture manifest.

### Stop

```powershell
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete the
local PostgreSQL volume and all of its data. Stopping the stack normally keeps
that local data. This foundation’s architecture is described in the
[CivicLoop v1 architecture design](docs/2026-07-30-civicloop-v1-architecture-design.md),
the [broader CivicLoop vision](docs/civicloop-vision.md),
and the [repository boundary](docs/repository-boundary.md).

## API documentation

With CivicLoop running, open `/api/docs` for the self-hosted Swagger UI.
The canonical OpenAPI 3.1 document is `openapi/civicloop-v1.yaml`, and reusable
JSON Schema 2020-12 contracts live in `schemas/`.

Validate the contracts with:

```powershell
uv run python scripts/validate_api_contracts.py
```

See `docs/api-contracts.md` for contributor rules and authentication details.

## Next PRD Milestone

The synthetic, durable-control-plane, and observable-deterministic gates are
implemented: 15 event scenarios, 16 deterministic executable cases, 100
labeled examples, durable run/budget/evaluation records, redacted
OpenTelemetry/OpenInference tracing, and an optional authenticated Phoenix
profile with 14-day retention. Phoenix is not required for CivicLoop readiness
and its production activation remains a protected operations step.

The live-read pilot is implemented: an authorized human can initiate manually
or select safely imported Eventbrite state while all external mutation remains
disabled. Imports retain sanitized, revision-aware provenance and explicitly
handle zero, one, many, stale, unavailable, draft, and live event states. The
following evaluation increment adds a
fixed budgeted judge and safe trace/evaluation/usage status in review. Hermes
remains synthetic and internal-only until its later gate, and Iterable draft
operations remain disabled. See the
[architecture delivery sequence](docs/2026-07-30-civicloop-v1-architecture-design.md#20-scope-and-delivery-sequence).

## Run LaunchLoop Locally

Open the browser demo:

```text
index.html
```

Or run the deterministic evaluator:

```powershell
cd loops\launchloop
python .\launchloop.py
```

Expected result: `16 / 16` eval cases pass and the summary reports 100 labeled
examples under schema version `1.0`.

## Data and Safety

This repository uses synthetic demo data only. It does not include real member, donor, sponsor, employee, volunteer, customer, payment, or credential data.

LaunchLoop is built around a human-in-the-loop operating model. The demo can draft, validate, recommend, and refuse risky actions, but it cannot send emails, publish posts, publish Eventbrite pages, change prices, create discounts, create audience segments, or export private data.

## Adapting This Loop

Before using this with a real nonprofit:

1. Replace synthetic data with a secure canonical data source.
2. Add authentication, authorization, role mapping, and audit logs.
3. Use least-privilege API credentials for Eventbrite, Iterable, Stripe, or other tools.
4. Keep consequential actions approval-gated.
5. Add privacy review for member/contact/sponsor/attendance data.
6. Run evals against your own policies before piloting.

## License

MIT License. See [LICENSE](LICENSE).
