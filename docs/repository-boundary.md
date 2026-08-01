# Repository Boundary

CivicLoop is the public, distributable application repository. It contains the
source code and everything a contributor or nonprofit needs to inspect, test,
run, and safely evaluate the synthetic demo.

## Kept in this public repository

- application source, migrations, and synthetic demo data
- deterministic evals and automated tests
- reusable Dockerfiles and generic local/self-hosted Compose examples
- public continuous-integration and GitHub Pages workflows
- contributor-facing architecture, safety, and implementation guidance

These assets are intentionally public because they make the project auditable
and reproducible. They must not depend on a specific CivicLoop deployment.

## Kept in the production operations repository

- internal development plans and working specifications
- environment-specific Compose overrides and infrastructure definitions
- hostnames, DNS and TLS procedures
- secret references and credential-rotation procedures (never secret values)
- backup, restore, monitoring, incident, and deployment runbooks
- production release promotion and rollback automation

The operations repository deploys a pinned CivicLoop commit or immutable image.
Production configuration must not be copied back into this public repository.

## Demo environments

The GitHub Pages demo is a browser-local, synthetic public sandbox. It does not
run Django or expose seeded login accounts. The self-hosted application includes
temporary synthetic operator and approver accounts for the authenticated demo
journey. Those accounts are demo fixtures, not production credentials.
