# CivicLoop public entry routes and GitHub presentation

**Date:** 2026-09-20
**Status:** Approved for implementation planning

## Outcome

CivicLoop will present a public product story at
`https://civicloop.karthikkannan.ca/`, keep the authenticated synthetic
LaunchLoop workspace at `/sandbox`, and provide a clear `/login` gateway to
the sandbox and owner administration. GitHub will point to the self-hosted
site and accurately distinguish deployed capabilities from planned work.

This is one focused public-entry and documentation increment. It does not
implement Hermes, enable provider writes, change credentials, or alter the
existing administrator authentication contract.

## Routes and user journeys

### `/`

The anonymous landing page explains:

- the CivicLoop vision: small, inspectable, human-approved agentic loops for
  nonprofit operations;
- the current LaunchLoop capability and safety boundary;
- what is deployed now, including the synthetic two-role workflow,
  deterministic checks, durable approval/audit state, bounded Eventbrite
  reads, advisory synthetic-only evaluation, and observability;
- the roadmap from the current foundation through self-hosted Hermes-assisted
  drafts and later approval-bound unpublished/unsent provider drafts;
- that publishing, sending, scheduling, ticket-economics changes, segment
  creation, and constituent export remain prohibited;
- links to the sandbox, login gateway, GitHub repository, and relevant public
  documentation.

The primary call to action is **Book a conversation**. A secondary call to
action opens the sandbox, and a tertiary action opens the GitHub repository.
The page does not expose demo
credentials, owner identity details, operational host information, or provider
state.

### Homepage message hierarchy

The page should follow the concise, product-led pattern used by strong modern
agent products: a short outcome-focused promise, a concrete explanation, an
immediate product path, and visible evidence that the work is real and open.
OpenClaw, Hermes Desktop, and Granola are presentation references only; the
page must not copy their language, visual identity, screenshots, or imply an
affiliation.

Use this content structure and approximate copy:

1. **Hero**
   - Eyebrow: `Open-source operations infrastructure for nonprofits`
   - Heading: `Human-approved AI workflows for work that matters.`
   - Supporting copy: `CivicLoop turns fragmented event operations into clear,
     reviewable workflows—grounded in policy, visible in audit trails, and kept
     under human control.`
   - Primary action: `Book a conversation` →
     `https://calendar.proton.me/bookings#KkKDMGq1h4GQZfzYq7jvGNFicfShuijjIRF3Wtg8ods=`
   - Secondary action: `Explore the sandbox` → `/sandbox`
   - Text link: `Follow the development on GitHub` →
     `https://github.com/karthik-kannan-product/CivicLoop`
2. **What works today**
   - Heading: `From event draft to an approval-ready campaign.`
   - Three compact steps: start from an idea or bounded Eventbrite import;
     resolve missing facts and generate grounded campaign assets; independently
     review the exact package with durable evidence.
3. **Why it is different**
   - Three short proof points: `Human approval by design`, `Observable and
     auditable`, and `Open source and self-hostable`.
   - Avoid unverified customer, adoption, performance, or impact claims.
4. **Roadmap**
   - Heading: `A focused loop today. A reusable operating model tomorrow.`
   - Show only three stages: current observable LaunchLoop foundation; next
     self-hosted Hermes-assisted drafts; later reusable membership, sponsor,
     engagement, and reporting loops.
   - Label each stage as `Live`, `In development`, or `Planned`; do not imply
     that Hermes or provider draft writes are deployed.
5. **Closing invitation**
   - Heading: `Building safer agentic operations for mission-driven teams.`
   - Supporting copy: `If you lead nonprofit operations, build responsible AI
     systems, or want to help shape the next loop, let’s talk.`
   - Primary action: `Book a conversation` using the Proton Calendar URL.
   - Secondary action: `View the source` using the GitHub URL.

The booking and GitHub destinations are external links. They should open in a
new tab with `rel="noopener noreferrer"`; analytics, contact forms, email
capture, and third-party embeds are out of scope.

### `/login`

The login gateway keeps the two identity surfaces explicit:

1. **Sandbox access** links to `/sandbox`, where the existing synthetic
   operator/approver sign-in form remains responsible for authentication.
2. **Owner administration** links to `/admin/security`, where the existing
   password, TOTP, recovery, throttling, and session controls remain
   responsible for authentication.

The gateway itself does not accept credentials. This avoids combining demo
and owner identities or creating a new authentication endpoint.

### `/sandbox`

`/sandbox` renders the current authenticated LaunchLoop application. Anonymous
visitors see the existing synthetic-account sign-in form. Authenticated users
see the existing role-bound workspace. Direct navigation and browser refresh
must work at this path.

The old root workspace URL will no longer be canonical. The root becomes the
landing page; no automatic redirect is required because the landing page
provides a prominent sandbox link.

### Existing protected routes

`/admin/security`, `/admin/integrations`, `/internal/django-admin/`, API,
health, static, and asset routes retain their current behavior and precedence.
The broad SPA fallback must not shadow them.

## Frontend architecture

Use the existing React/Vite application and design language. Add small,
route-aware entry components rather than adopting a routing dependency:

- `PublicHome` owns the public vision, current-state, roadmap, safety, and
  calls-to-action presentation.
- `LoginGateway` owns the two authentication destinations and contains no
  credential form.
- the existing authenticated `App` owns `/sandbox`.
- a small pathname dispatcher selects the correct entry component for `/`,
  `/login`, and `/sandbox`.

This keeps the change shallow, avoids a new production dependency, and leaves
the existing sandbox state and API client untouched. Shared visual primitives
and CSS tokens should be reused so the landing and gateway feel related to the
workspace without making the public page look like an internal operations
console.

Unknown non-reserved paths should render a concise not-found view with links
back to the homepage and sandbox rather than silently opening the workspace.

## Backend routing

Django continues to serve the built frontend index for `/`, `/login`,
`/login/`, `/sandbox`, and `/sandbox/`. Named route entries should make this
intent explicit and testable before the constrained fallback route.

The backend does not inspect sandbox authentication state to choose a page;
the frontend performs the existing session check only when `/sandbox` is
rendered. CSRF behavior for login and administrator routes remains unchanged.

## GitHub presentation

Update the public repository README to:

- make `https://civicloop.karthikkannan.ca/` the product homepage;
- make `https://civicloop.karthikkannan.ca/sandbox` the primary live demo;
- retain GitHub Pages as a browser-local, synthetic fallback rather than the
  primary demo;
- summarize the verified deployed state at public revision `e0ed086` without
  implying that a commit remains current after future releases;
- label self-hosted Hermes, live Eventbrite draft writes, and Iterable unsent
  drafts as planned and gated;
- link to the architecture, vision, security, integrations, observability, and
  deployment documentation.

Set the GitHub repository homepage metadata to
`https://civicloop.karthikkannan.ca/`. Keep the existing concise repository
description unless implementation reveals a factual mismatch.

## Accessibility and responsive behavior

- Semantic landmarks and ordered heading levels are required.
- Links and buttons must have descriptive accessible names and visible focus.
- Current/planned status must not rely on color alone.
- The landing page and gateway must remain usable at narrow mobile widths and
  with reduced motion.
- The login gateway must clearly state that sandbox and owner identities are
  separate before users choose a destination.

## Failure and security behavior

- If the sandbox session endpoint is unavailable, retain the existing
  recoverable workspace error behavior.
- Public pages remain useful without API calls.
- No credentials, demo password, owner identifiers, provider connection
  status, production IP address, private roadmap material, or deployment
  secrets appear in public markup or repository text.
- The change does not weaken feature gates, MFA, CSRF, rate limits, role
  checks, provider write switches, or network boundaries.

## Verification and release

Use the repository efficiency ladder once against a frozen candidate:

1. Run formatting/diff checks and a prohibited-content scan.
2. Run focused frontend tests for `/`, `/login`, `/sandbox`, unknown paths,
   direct navigation, and retained sandbox authentication behavior.
3. Run focused Django route tests for public, sandbox, administrator, API, and
   fallback precedence.
4. Run the frontend production build and affected backend checks.
5. Push one coherent feature branch and verify its remote SHA.
6. Merge through GitHub only after review, then let exact-main CI establish the
   full-suite result.
7. Deploy the exact merged SHA through the protected workflow after the normal
   production approval, backup, readiness, and rollback gates.
8. Independently verify `/`, `/login`, `/sandbox`, administrator isolation,
   HTTPS headers, server revision, and recent critical logs.

Report these states independently: implemented locally, focused verification,
GitHub synchronization/merge, and production activation.

## Explicit exclusions

- Task 5 Hermes adapter implementation or activation.
- PostgreSQL, Phoenix, LiteLLM, or image upgrades.
- Demo-password rotation or owner identity changes.
- Provider credential administration or live provider mutations.
- A general-purpose marketing CMS, analytics tracker, newsletter form, or
  contact-data collection.

## Definition of done

- The root presents the public vision and roadmap accurately.
- `/login` presents the two identity destinations without collecting secrets.
- `/sandbox` preserves the authenticated synthetic workflow.
- Existing administrator and API routes behave unchanged.
- GitHub README and homepage metadata point to the self-hosted experience and
  distinguish current from planned capabilities.
- Focused verification, exact GitHub SHA, protected deployment evidence, and
  independent live smoke results are recorded before production completion is
  claimed.
