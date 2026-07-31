# CivicLoop

Open-source agentic workflow loops for nonprofit operations.

CivicLoop is a public library of small, forkable agentic loops that nonprofit teams, technology volunteers, and implementation partners can adapt inside their own organizations. The project starts with **LaunchLoop**, a human-approved event campaign launch loop for small nonprofits.

The long-term CivicLoop vision is broader: reusable loops for membership lifecycle, sponsor entitlements, event operations, campaign communications, reporting, and agent observability. The repository is intentionally starting narrow so each loop can be inspected, tested, and adapted responsibly.

## Live Demo

LaunchLoop is published with GitHub Pages:

https://karthik-kannan-product.github.io/CivicLoop/

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
│       ├── eval_cases.json     # Six synthetic eval cases
│       ├── data/               # Synthetic event and audience data
│       └── policies/           # Synthetic approval, language, and discount rules
└── docs/
    ├── civicloop-vision.md
    └── launchloop-implementation-guide.md
```

## Application Foundation

CivicLoop now includes the container foundation for the self-hosted application.
The current increment provides the web shell, health contracts, PostgreSQL,
Valkey, and Celery process modes. Authentication and live LaunchLoop agents are
delivered in later reviewed increments.

### Prerequisites

- Docker 29 or newer
- Docker Compose 5 or newer

No host Python, Node.js, PostgreSQL client, or Valkey installation is required.
Docker Desktop (or another Docker Engine) must be running before any `docker compose`
command; the foundation cannot start when the Docker daemon is unavailable.

### Start

```powershell
Copy-Item .env.example .env
# Replace the example passwords and secret in the untracked .env file.
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

Expected LaunchLoop result: `6 / 6` eval cases pass.

### Stop

```powershell
docker compose down
```

Use `docker compose down -v` only when you intentionally want to delete the
local PostgreSQL volume and all of its data. Stopping the stack normally keeps
that local data. This foundation’s architecture is described in the
[foundation implementation plan](docs/superpowers/plans/2026-07-30-civicloop-foundation-implementation-plan.md)
and the broader [CivicLoop vision](docs/civicloop-vision.md).

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

Expected result: `6 / 6` eval cases pass.

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
