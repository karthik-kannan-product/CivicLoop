# CivicLoop Vision

CivicLoop is a public repository of reusable open-source agentic loops for nonprofit operations.

The intended audience is product and technology volunteers, nonprofit operators, sponsor-facing leadership, and implementation partners who want practical patterns they can fork, inspect, and adapt. The goal is not to publish one large autonomous back-office agent. The goal is to collect focused loops that are safe, testable, human-approved, and useful in real nonprofit workflows.

## Operating Thesis

Volunteer-led nonprofits often coordinate membership, events, sponsor entitlements, email campaigns, renewals, attendance, and reporting across several tools. The work depends on human memory, manual reconciliation, and informal handoffs. Agentic systems can help when they are framed as workflow copilots with clear boundaries, not as unsupervised decision-makers.

CivicLoop should favor:

- deterministic lifecycle rules for repeatable state changes
- human approval for consequential external actions
- role-based access for sensitive data
- audit logs and traceability
- evals that test policy, data, pricing, targeting, and refusal behavior
- open-source-friendly building blocks

## Broader Product Direction

The broader system concept is Hermes: a human-approved operations copilot for nonprofit event, membership, sponsor, and engagement operations.

Potential future loops include:

- membership signup and renewal
- sponsor-domain membership eligibility
- complimentary membership grants
- renewal windows and grace-period handling
- inactive-state enforcement
- Iterable suppression and deliverability cleanup
- Eventbrite setup and campaign launch
- Stripe-based paid membership renewal
- sponsor reporting and CSV exports
- data-quality checks
- agent observability and eval review

## Why LaunchLoop Comes First

LaunchLoop is the first loop because event campaign launch is narrow, visible, and valuable. It demonstrates the key CivicLoop pattern:

1. observe a draft or updated event
2. check required fields and policy rules
3. draft campaign assets
4. validate audience and sponsor logic
5. refuse unsafe requests
6. hand off to a human for approval
7. leave evidence for review

That pattern can later be reused for membership, renewal, reporting, and sponsor workflows.

## Responsible Use

CivicLoop examples are starting points. A real deployment must add security, authentication, authorization, privacy review, data retention rules, real integration tests, monitoring, and rollback plans.

The right question is not "can an agent do all of this?" The right question is: which nonprofit operations tasks are safe, repeatable, and valuable enough to automate or semi-automate first?
