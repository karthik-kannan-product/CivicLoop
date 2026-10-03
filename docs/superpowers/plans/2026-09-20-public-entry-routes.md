# CivicLoop Public Entry Routes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a concise CivicLoop product homepage at `/`, a two-destination login gateway at `/login`, and the existing authenticated LaunchLoop workspace at `/sandbox`, then align GitHub and deploy the exact reviewed revision to Vultr.

**Architecture:** Keep the existing React/Vite bundle and add a small pathname dispatcher with focused public components; no routing dependency is introduced. Django names the three public entry routes explicitly while retaining the constrained SPA fallback and all protected-route precedence. GitHub and production promotion remain separate evidence gates after the source candidate is frozen.

**Tech Stack:** React 19, TypeScript 7, Vite 8, Vitest/Testing Library, Django 5, pytest, GitHub CLI/Actions, Docker Compose, Vultr.

## Global Constraints

- Keep the homepage concise, outcome-led, and original; OpenClaw, Hermes Desktop, and Granola are inspiration only.
- `Book a conversation` points exactly to `https://calendar.proton.me/bookings#KkKDMGq1h4GQZfzYq7jvGNFicfShuijjIRF3Wtg8ods=`.
- GitHub links point exactly to `https://github.com/karthik-kannan-product/CivicLoop`.
- `/login` never accepts credentials; it links to `/sandbox` and `/admin/security`.
- GitHub Pages continues to render the browser-local synthetic workspace when `VITE_STATIC_DEMO=true`.
- No new runtime dependency, analytics, third-party embed, contact form, or credential handling.
- Do not change administrator MFA, session, CSRF, throttling, integration, API, or provider-write behavior.
- Label capabilities as `Live`, `In development`, or `Planned`; never present Hermes or provider draft writes as deployed.
- Report implemented, verified, GitHub-synchronized, and production-activated states independently.
- Deploy only an exact merged `main` SHA through the protected workflow with its existing backup, readiness, and rollback gates.
- Task 5 Hermes implementation remains a separate follow-on task after this release.

---

## File structure

- Create `frontend/src/public/PublicHome.tsx`: public homepage copy and external calls to action.
- Create `frontend/src/public/LoginGateway.tsx`: identity-boundary explanation and destination links.
- Create `frontend/src/public/PublicNotFound.tsx`: safe non-reserved-path fallback.
- Create `frontend/src/public/PublicEntry.tsx`: pure pathname normalization and component dispatch.
- Create `frontend/src/public/PublicEntry.test.tsx`: route, copy, links, status labels, and static-boundary tests.
- Modify `frontend/src/App.tsx`: preserve Pages/test behavior and delegate production paths.
- Modify `frontend/src/index.css`: public-page visual system, responsive behavior, focus, and reduced-motion compatibility.
- Modify `backend/civicloop/urls.py`: name `/`, `/login`, and `/sandbox` routes explicitly before the fallback.
- Modify `tests/test_spa.py`: direct-route, trailing-slash, reserved-route, and unknown-path contracts.
- Modify `README.md`: make Vultr canonical, retain Pages as fallback, and reconcile live versus planned state.
- Modify private `develop/civicloop/handoffs/current-vultr-deployment.md`: record only non-secret exact-SHA release evidence after deployment.

---

### Task 1: Add the public route dispatcher and failing component contracts

**Files:**
- Create: `frontend/src/public/PublicEntry.test.tsx`
- Create: `frontend/src/public/PublicEntry.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `Workspace`, `AuthenticatedApp`, `import.meta.env.VITE_STATIC_DEMO`, and `window.location.pathname`.
- Produces: `normalizePublicPath(pathname: string): string` and `PublicEntry({ pathname, sandbox }: { pathname: string; sandbox: ReactNode }): JSX.Element`.

- [ ] **Step 1: Write the failing route tests**

Create `frontend/src/public/PublicEntry.test.tsx`:

```tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { PublicEntry, normalizePublicPath } from "./PublicEntry";

afterEach(cleanup);

function renderPublicEntry(pathname: string) {
  return render(<PublicEntry pathname={pathname} sandbox={<p>Checking your demo session...</p>} />);
}

test("normalizes only trailing slashes", () => {
  expect(normalizePublicPath("/")).toBe("/");
  expect(normalizePublicPath("/login/")).toBe("/login");
  expect(normalizePublicPath("/sandbox/")).toBe("/sandbox");
  expect(normalizePublicPath("/projects/civicloop/")).toBe("/projects/civicloop");
});

test("dispatches the homepage", () => {
  renderPublicEntry("/");
  expect(screen.getByRole("heading", { name: "Human-approved AI workflows for work that matters." })).toBeInTheDocument();
});

test("dispatches the login gateway", () => {
  renderPublicEntry("/login/");
  expect(screen.getByRole("heading", { name: "Choose how you want to enter CivicLoop." })).toBeInTheDocument();
});

test("dispatches the authenticated sandbox entry", () => {
  renderPublicEntry("/sandbox");
  expect(screen.getByText("Checking your demo session...")).toBeInTheDocument();
});

test("renders a useful not-found page for unknown public paths", () => {
  renderPublicEntry("/projects/civicloop");
  expect(screen.getByRole("heading", { name: "That page is not part of CivicLoop." })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Return home" })).toHaveAttribute("href", "/");
});
```

- [ ] **Step 2: Run the route test to verify it fails**

Run:

```powershell
Set-Location frontend
npm test -- --run src/public/PublicEntry.test.tsx
```

Expected: FAIL because `./PublicEntry` does not exist.

- [ ] **Step 3: Add the dispatcher and preserve static-demo behavior**

Create `frontend/src/public/PublicEntry.tsx`:

```tsx
import type { ReactNode } from "react";

import { LoginGateway } from "./LoginGateway";
import { PublicHome } from "./PublicHome";
import { PublicNotFound } from "./PublicNotFound";

export function normalizePublicPath(pathname: string) {
  if (pathname === "/") return pathname;
  return pathname.replace(/\/+$/, "") || "/";
}

export function PublicEntry({ pathname, sandbox }: { pathname: string; sandbox: ReactNode }) {
  switch (normalizePublicPath(pathname)) {
    case "/":
      return <PublicHome />;
    case "/login":
      return <LoginGateway />;
    case "/sandbox":
      return sandbox;
    default:
      return <PublicNotFound />;
  }
}
```

In `frontend/src/App.tsx`, export `AuthenticatedApp` and replace `App` with:

```tsx
export function AuthenticatedApp() {
  const [sessionUser, setSessionUser] = useState<SessionUser | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    void requestSession().then(setSessionUser).catch(() => setSessionUser(null)).finally(() => setChecking(false));
  }, []);

  if (checking) {
    return <main className="load-state" aria-busy="true"><p className="eyebrow">Loading CivicLoop</p><h1>Checking your demo session...</h1></main>;
  }
  if (!sessionUser) {
    return <LoginScreen onLogin={async (username, password) => setSessionUser(await loginDemo(username, password))} />;
  }
  return <Workspace sessionUser={sessionUser} onLogout={() => void logoutDemo().then(() => setSessionUser(null))} />;
}

export function App() {
  if (import.meta.env.VITE_STATIC_DEMO === "true" || import.meta.env.VITEST) {
    return <Workspace />;
  }
  return <PublicEntry pathname={window.location.pathname} sandbox={<AuthenticatedApp />} />;
}
```

Add this import near the existing imports in `frontend/src/App.tsx`:

```tsx
import { PublicEntry } from "./public/PublicEntry";
```

- [ ] **Step 4: Run the route test and observe only the missing-component failures**

Run:

```powershell
npm test -- --run src/public/PublicEntry.test.tsx
```

Expected: FAIL because `PublicHome`, `LoginGateway`, and `PublicNotFound` do not exist; `normalizePublicPath` tests pass.

---

### Task 2: Build concise public pages and responsive styling

**Files:**
- Create: `frontend/src/public/PublicHome.tsx`
- Create: `frontend/src/public/LoginGateway.tsx`
- Create: `frontend/src/public/PublicNotFound.tsx`
- Modify: `frontend/src/public/PublicEntry.test.tsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: ordinary anchor navigation; no API, session, or browser storage.
- Produces: accessible public pages selected by `PublicEntry`.

- [ ] **Step 1: Extend tests for exact copy, destinations, and status labels**

Append to `frontend/src/public/PublicEntry.test.tsx`:

```tsx
test("homepage offers contact, sandbox, and transparent development paths", () => {
  renderPublicEntry("/");

  for (const link of screen.getAllByRole("link", { name: "Book a conversation" })) {
    expect(link).toHaveAttribute(
      "href",
      "https://calendar.proton.me/bookings#KkKDMGq1h4GQZfzYq7jvGNFicfShuijjIRF3Wtg8ods=",
    );
  }
  expect(screen.getByRole("link", { name: "Explore the sandbox" })).toHaveAttribute("href", "/sandbox");
  expect(screen.getByRole("link", { name: "Follow the development on GitHub" })).toHaveAttribute(
    "href",
    "https://github.com/karthik-kannan-product/CivicLoop",
  );
  expect(screen.getByRole("link", { name: "Read the architecture" })).toHaveAttribute(
    "href",
    "https://github.com/karthik-kannan-product/CivicLoop/blob/main/docs/2026-07-30-civicloop-v1-architecture-design.md",
  );
  expect(screen.getByText("Live")).toBeInTheDocument();
  expect(screen.getByText("In development")).toBeInTheDocument();
  expect(screen.getByText("Planned")).toBeInTheDocument();
});

test("login gateway separates sandbox and owner identities", () => {
  renderPublicEntry("/login");

  expect(screen.getByText(/Sandbox and owner identities are separate/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Enter the sandbox" })).toHaveAttribute("href", "/sandbox");
  expect(screen.getByRole("link", { name: "Open owner administration" })).toHaveAttribute(
    "href",
    "/admin/security",
  );
  expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
});

test("external homepage actions use safe new-tab attributes", () => {
  renderPublicEntry("/");
  for (const name of ["Book a conversation", "Follow the development on GitHub", "Read the architecture", "View the source"]) {
    for (const link of screen.getAllByRole("link", { name })) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  }
});
```

- [ ] **Step 2: Run the focused tests to confirm the public components are missing**

Run:

```powershell
npm test -- --run src/public/PublicEntry.test.tsx
```

Expected: FAIL on missing public component modules.

- [ ] **Step 3: Implement the homepage**

Create `frontend/src/public/PublicHome.tsx`:

```tsx
import type { ReactNode } from "react";

const BOOKING_URL = "https://calendar.proton.me/bookings#KkKDMGq1h4GQZfzYq7jvGNFicfShuijjIRF3Wtg8ods=";
const GITHUB_URL = "https://github.com/karthik-kannan-product/CivicLoop";
const ARCHITECTURE_URL = `${GITHUB_URL}/blob/main/docs/2026-07-30-civicloop-v1-architecture-design.md`;

const roadmap = [
  { status: "Live", title: "Observable LaunchLoop", body: "Start from an idea or bounded Eventbrite import, resolve missing facts, prepare campaign assets, and require an independent decision on the exact package." },
  { status: "In development", title: "Self-hosted Hermes-assisted drafts", body: "Add bounded, private agent assistance without giving the model provider credentials or authority to publish or send." },
  { status: "Planned", title: "Reusable nonprofit operations loops", body: "Extend the same human-approved pattern to membership, sponsor, engagement, and reporting workflows." },
];

const proofPoints = [
  ["Human approval by design", "Consequential actions stop at a clear decision boundary."],
  ["Observable and auditable", "Revisions, evaluations, decisions, and outcomes leave durable evidence."],
  ["Open source and self-hostable", "Inspect the implementation, adapt the loop, and keep control of deployment."],
];

function ExternalLink({ href, children, className = "public-link" }: { href: string; children: ReactNode; className?: string }) {
  return <a className={className} href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
}

export function PublicHome() {
  return (
    <div className="public-page">
      <header className="public-nav" aria-label="CivicLoop">
        <a className="public-brand" href="/">CivicLoop</a>
        <nav aria-label="Primary navigation">
          <a className="public-link" href="/login">Log in</a>
          <ExternalLink href={ARCHITECTURE_URL}>Read the architecture</ExternalLink>
          <ExternalLink href={GITHUB_URL}>GitHub</ExternalLink>
        </nav>
      </header>
      <main>
        <section className="public-hero" aria-labelledby="public-title">
          <p className="eyebrow">Open-source operations infrastructure for nonprofits</p>
          <h1 id="public-title">Human-approved AI workflows for work that matters.</h1>
          <p className="public-hero__lede">CivicLoop turns fragmented event operations into clear, reviewable workflows—grounded in policy, visible in audit trails, and kept under human control.</p>
          <div className="public-actions">
            <ExternalLink className="button button--primary public-cta" href={BOOKING_URL}>Book a conversation</ExternalLink>
            <a className="button public-cta public-cta--secondary" href="/sandbox">Explore the sandbox</a>
          </div>
          <ExternalLink href={GITHUB_URL}>Follow the development on GitHub</ExternalLink>
        </section>

        <section className="public-section" aria-labelledby="today-title">
          <p className="eyebrow">What works today</p>
          <h2 id="today-title">From event draft to an approval-ready campaign.</h2>
          <ol className="public-grid public-steps">
            <li><strong>Start with grounded context.</strong><span>Begin from an idea or a bounded Eventbrite import.</span></li>
            <li><strong>Resolve and prepare.</strong><span>Confirm missing facts and generate reviewable campaign assets.</span></li>
            <li><strong>Approve with evidence.</strong><span>Let a distinct human review the exact package and durable audit trail.</span></li>
          </ol>
        </section>

        <section className="public-section" aria-labelledby="difference-title">
          <p className="eyebrow">Why it is different</p>
          <h2 id="difference-title">Useful automation without surrendering control.</h2>
          <div className="public-grid">
            {proofPoints.map(([title, body]) => <article className="public-card" key={title}><h3>{title}</h3><p>{body}</p></article>)}
          </div>
        </section>

        <section className="public-section" aria-labelledby="roadmap-title">
          <p className="eyebrow">Roadmap</p>
          <h2 id="roadmap-title">A focused loop today. A reusable operating model tomorrow.</h2>
          <div className="public-grid">
            {roadmap.map((item) => <article className="public-card" key={item.status}><span className="public-status">{item.status}</span><h3>{item.title}</h3><p>{item.body}</p></article>)}
          </div>
        </section>

        <section className="public-section public-invitation" aria-labelledby="invitation-title">
          <h2 id="invitation-title">Building safer agentic operations for mission-driven teams.</h2>
          <p>If you lead nonprofit operations, build responsible AI systems, or want to help shape the next loop, let’s talk.</p>
          <div className="public-actions">
            <ExternalLink className="button button--primary public-cta" href={BOOKING_URL}>Book a conversation</ExternalLink>
            <ExternalLink href={GITHUB_URL}>View the source</ExternalLink>
          </div>
        </section>
      </main>
      <footer className="public-footer"><span>CivicLoop</span><span>Open source · Human approved · Built in public</span></footer>
    </div>
  );
}
```

- [ ] **Step 4: Implement the login gateway and not-found page**

Create `frontend/src/public/LoginGateway.tsx`:

```tsx
export function LoginGateway() {
  return (
    <main className="gateway-page">
      <section className="gateway-panel" aria-labelledby="gateway-title">
        <a className="public-brand" href="/">CivicLoop</a>
        <p className="eyebrow">Secure entry</p>
        <h1 id="gateway-title">Choose how you want to enter CivicLoop.</h1>
        <p>Sandbox and owner identities are separate so demonstration access never grants administrative access.</p>
        <div className="gateway-grid">
          <article className="gateway-card"><h2>Synthetic sandbox</h2><p>Explore the two-role LaunchLoop journey using synthetic data.</p><a className="button button--primary public-cta" href="/sandbox">Enter the sandbox</a></article>
          <article className="gateway-card"><h2>Owner administration</h2><p>Manage security and configured integrations through the MFA-protected owner surface.</p><a className="button public-cta public-cta--secondary" href="/admin/security">Open owner administration</a></article>
        </div>
        <a className="public-link" href="/">Return to CivicLoop</a>
      </section>
    </main>
  );
}
```

Create `frontend/src/public/PublicNotFound.tsx`:

```tsx
export function PublicNotFound() {
  return (
    <main className="not-found-page">
      <section className="gateway-panel">
        <p className="eyebrow">Page not found</p>
        <h1>That page is not part of CivicLoop.</h1>
        <p>Use the public homepage or enter the synthetic sandbox.</p>
        <div className="public-actions">
          <a className="button button--primary public-cta" href="/">Return home</a>
          <a className="public-link" href="/sandbox">Explore the sandbox</a>
        </div>
      </section>
    </main>
  );
}
```

- [ ] **Step 5: Add the complete public-page style layer**

Append this complete style layer to `frontend/src/index.css`:

```css
.public-page { min-height: 100vh; }
.public-nav, .public-footer { align-items: center; display: flex; justify-content: space-between; margin: 0 auto; max-width: 72rem; padding: 1.25rem clamp(1rem, 4vw, 3rem); }
.public-nav nav { align-items: center; display: flex; gap: 1rem; }
.public-brand { color: var(--brand); font-size: 1.1rem; font-weight: 850; text-decoration: none; }
.public-link { color: var(--brand-bright); font-weight: 750; }
.public-hero, .public-section { margin: 0 auto; max-width: 72rem; padding: clamp(3rem, 8vw, 7rem) clamp(1rem, 4vw, 3rem); }
.public-hero h1 { font-size: clamp(2.75rem, 8vw, 6.25rem); max-width: 14ch; }
.public-hero__lede { color: var(--muted); font-size: clamp(1.05rem, 2vw, 1.35rem); max-width: 48rem; }
.public-actions { align-items: center; display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1.5rem 0; }
.public-cta { display: inline-flex; justify-content: center; text-decoration: none; }
.public-cta--secondary { border-color: var(--brand-bright); color: var(--brand); }
.public-section { border-top: 1px solid var(--border); }
.public-section > h2 { font-size: clamp(1.8rem, 4vw, 3rem); max-width: 24ch; }
.public-grid { display: grid; gap: 1rem; grid-template-columns: repeat(3, minmax(0, 1fr)); list-style: none; padding: 0; }
.public-card, .public-steps li { background: var(--surface); border: 1px solid var(--border); padding: 1.25rem; }
.public-card p, .public-steps span { color: var(--muted); display: block; margin: 0.5rem 0 0; }
.public-status { color: var(--brand-bright); font-size: 0.72rem; font-weight: 850; letter-spacing: 0.08em; text-transform: uppercase; }
.public-invitation { background: var(--brand); color: white; max-width: none; padding-left: max(1rem, calc((100vw - 66rem) / 2)); padding-right: max(1rem, calc((100vw - 66rem) / 2)); }
.public-invitation p { color: #c5d9d3; max-width: 44rem; }
.public-invitation .public-link { color: white; }
.public-footer { color: var(--muted); font-size: 0.82rem; }
.gateway-page, .not-found-page { align-items: center; display: flex; justify-content: center; min-height: 100vh; padding: 1rem; }
.gateway-panel { background: var(--surface); border: 1px solid var(--border); max-width: 64rem; padding: clamp(1.5rem, 5vw, 3.5rem); width: 100%; }
.gateway-grid { display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 2rem 0; }
.gateway-card { background: var(--canvas); border: 1px solid var(--border); padding: 1.25rem; }
.public-link:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
@media (max-width: 48rem) {
  .public-grid, .gateway-grid { grid-template-columns: 1fr; }
  .public-actions { align-items: stretch; flex-direction: column; }
  .public-actions .button { text-align: center; width: 100%; }
  .public-footer { align-items: flex-start; flex-direction: column; gap: 0.5rem; }
}
@media (prefers-reduced-motion: reduce) {
  .public-page *, .gateway-page * { scroll-behavior: auto !important; transition: none !important; }
}
```

Do not use remote fonts, tracking scripts, background video, animation libraries, or third-party assets.

- [ ] **Step 6: Run focused frontend tests**

Run:

```powershell
npm test -- --run src/public/PublicEntry.test.tsx src/App.test.tsx
```

Expected: both test files pass with zero failed tests.

- [ ] **Step 7: Commit the frontend slice**

Run:

```powershell
Set-Location ..
git add frontend/src/App.tsx frontend/src/index.css frontend/src/public
git commit -m "feat: add public CivicLoop entry experience"
```

---

### Task 3: Make Django entry routes explicit and preserve boundaries

**Files:**
- Modify: `tests/test_spa.py`
- Modify: `backend/civicloop/urls.py`

**Interfaces:**
- Consumes: existing `spa_index(request: HttpRequest) -> FileResponse`.
- Produces: named `public-home`, `public-login`, `public-login-slash`, `sandbox`, and `sandbox-slash` URL patterns.

- [ ] **Step 1: Replace generic entry tests with explicit route contracts**

Add this parametrized test to `tests/test_spa.py`:

```python
@pytest.mark.parametrize("path", ["/", "/login", "/login/", "/sandbox", "/sandbox/"])
def test_named_public_entry_routes_serve_compiled_index(tmp_path: Path, path: str) -> None:
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><title>CivicLoop</title>", encoding="utf-8")

    with override_settings(FRONTEND_INDEX=index):
        response = Client().get(path)

    assert response.status_code == 200
    assert b"<title>CivicLoop</title>" in b"".join(response.streaming_content)
```

Keep `test_frontend_deep_link_serves_compiled_index` to prove the frontend not-found view remains reachable through the constrained fallback. Keep every reserved-namespace test unchanged.

- [ ] **Step 2: Run the focused backend test and verify the named-route assertion fails**

First add `from django.urls import reverse` and these assertions inside the new test, keyed by path:

```python
route_names = {
    "/": "public-home",
    "/login": "public-login",
    "/login/": "public-login-slash",
    "/sandbox": "sandbox",
    "/sandbox/": "sandbox-slash",
}
assert reverse(route_names[path]) == path
```

Run:

```powershell
$env:CIVICLOOP_ENV='test'
$env:DATABASE_URL='sqlite:///:memory:'
uv run pytest tests/test_spa.py -q
```

Expected: FAIL with `NoReverseMatch` for the new public route names.

- [ ] **Step 3: Add explicit Django routes before protected and fallback routes**

Add these entries at the beginning of `urlpatterns` in `backend/civicloop/urls.py`:

```python
path("", spa_index, name="public-home"),
path("login", spa_index, name="public-login"),
path("login/", spa_index, name="public-login-slash"),
path("sandbox", spa_index, name="sandbox"),
path("sandbox/", spa_index, name="sandbox-slash"),
```

Do not move or weaken any administrator, internal, API, health, asset, static, or negative-lookahead rule.

- [ ] **Step 4: Run focused backend route tests**

Run:

```powershell
uv run pytest tests/test_spa.py -q
```

Expected: all `tests/test_spa.py` tests pass.

- [ ] **Step 5: Commit the route contract**

Run:

```powershell
git add backend/civicloop/urls.py tests/test_spa.py
git commit -m "feat: name public CivicLoop entry routes"
```

---

### Task 4: Reconcile GitHub-facing documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: the deployed feature inventory already documented in the repository and approved design.
- Produces: canonical public links and explicit live/in-development/planned labels.

- [ ] **Step 1: Update the README opening and live-demo section**

Replace the existing `Live Demo` section with:

```markdown
## Live CivicLoop

- **Product and roadmap:** https://civicloop.karthikkannan.ca/
- **Authenticated synthetic sandbox:** https://civicloop.karthikkannan.ca/sandbox
- **Login gateway:** https://civicloop.karthikkannan.ca/login
- **Browser-local fallback:** https://karthik-kannan-product.github.io/CivicLoop/

The self-hosted sandbox is the canonical demonstration. It uses synthetic
operator and approver identities with durable PostgreSQL workflow, revision,
approval, audit, evaluation, and sandbox-receipt records. GitHub Pages remains
a safe browser-local fallback and does not expose server accounts.
```

Replace `Next PRD Milestone` with `Current state and roadmap`, preserving the existing verified details but grouping them under `Live`, `In development`, and `Planned`. State that the observable LaunchLoop foundation and bounded Eventbrite reads are live; self-hosted Hermes-assisted drafts are in development; Eventbrite unpublished-draft and Iterable unsent-draft operations remain planned, disabled, and approval-gated.

- [ ] **Step 2: Run documentation checks**

Run:

```powershell
rg -n "karthik-kannan-product.github.io/CivicLoop|civicloop.karthikkannan.ca|Hermes|Iterable|Eventbrite" README.md
git diff --check
```

Expected: the Vultr homepage and sandbox are present; GitHub Pages appears only as a fallback; Hermes/provider writes are not described as live; `git diff --check` exits 0.

- [ ] **Step 3: Commit the README**

Run:

```powershell
git add README.md
git commit -m "docs: align GitHub with CivicLoop vision and status"
```

---

### Task 5: Freeze and verify the source candidate once

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes: Tasks 1-4 frozen commits.
- Produces: one immutable release-candidate SHA and scoped verification evidence.

- [ ] **Step 1: Run static and focused gates**

Run from the repository root:

```powershell
$env:CIVICLOOP_ENV='test'
$env:DATABASE_URL='sqlite:///:memory:'
uv run ruff check backend tests scripts
uv run python backend/manage.py check
uv run pytest tests/test_spa.py -q
Set-Location frontend
npm test -- --run src/public/PublicEntry.test.tsx src/App.test.tsx
npm run build
npm run build:pages
npm audit --audit-level=high
Set-Location ..
git diff --check
```

Expected: every command exits 0; focused pytest and Vitest have zero failures; both builds complete; npm reports zero high-severity vulnerabilities.

- [ ] **Step 2: Run leakage and scope review**

Run:

```powershell
git status --short
git diff main...HEAD --stat
git diff main...HEAD | Select-String -Pattern 'password|secret|api[_-]?key|token|BEGIN .*PRIVATE KEY' -CaseSensitive:$false
git log --oneline main..HEAD
```

Expected: only planned files changed; matches are limited to legitimate safety/authentication documentation or existing UI labels; no secret value, private key, populated environment data, or generated build output is tracked.

- [ ] **Step 3: Record the candidate SHA**

Run:

```powershell
$candidateSha = (git rev-parse HEAD).Trim()
if ($candidateSha -notmatch '^[0-9a-f]{40}$') { throw 'Candidate SHA is not exact.' }
$candidateSha
```

Expected: one exact 40-character SHA.

---

### Task 6: Synchronize GitHub, merge, and update repository metadata

**Files:**
- GitHub branch, pull request, `main`, and repository homepage metadata.

**Interfaces:**
- Consumes: verified candidate SHA from Task 5.
- Produces: reviewed merged `main` SHA and homepage metadata set to the Vultr root.

- [ ] **Step 1: Push the feature branch and verify the remote SHA**

Run:

```powershell
$env:GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe'
git push -u origin codex/public-entry-routes
$localSha = (git rev-parse HEAD).Trim()
$remoteSha = (& 'C:\Program Files\GitHub CLI\gh.exe' api repos/karthik-kannan-product/CivicLoop/git/ref/heads/codex/public-entry-routes --jq .object.sha).Trim()
if ($localSha -ne $remoteSha) { throw 'Local and GitHub feature SHAs differ.' }
```

Expected: push succeeds and the SHA comparison does not throw.

- [ ] **Step 2: Create the pull request and wait for CI**

Run:

```powershell
$prUrl = (& 'C:\Program Files\GitHub CLI\gh.exe' pr create --repo karthik-kannan-product/CivicLoop --base main --head codex/public-entry-routes --title 'feat: add public CivicLoop entry experience' --body 'Adds the concise public homepage, login gateway, canonical sandbox route, explicit Django route contracts, and GitHub status reconciliation. Hermes and provider writes remain gated and out of scope.').Trim()
& 'C:\Program Files\GitHub CLI\gh.exe' pr checks $prUrl --watch
```

Expected: a PR URL is returned and every required check passes.

- [ ] **Step 3: Merge the reviewed PR and verify local/GitHub `main`**

Run:

```powershell
& 'C:\Program Files\GitHub CLI\gh.exe' pr merge $prUrl --merge --delete-branch
git checkout main
git pull --ff-only origin main
$mergedSha = (git rev-parse HEAD).Trim()
$githubMainSha = (& 'C:\Program Files\GitHub CLI\gh.exe' api repos/karthik-kannan-product/CivicLoop/commits/main --jq .sha).Trim()
if ($mergedSha -ne $githubMainSha) { throw 'Local main and GitHub main differ.' }
```

Expected: merge succeeds and local `main` equals GitHub `main`.

- [ ] **Step 4: Wait for exact-main CI**

Run:

```powershell
$mainRunId = $null
for ($attempt = 1; $attempt -le 12; $attempt++) {
  $mainRunId = (& 'C:\Program Files\GitHub CLI\gh.exe' api "repos/karthik-kannan-product/CivicLoop/actions/runs?head_sha=$mergedSha&event=push&per_page=10" --jq '.workflow_runs[] | select(.name == "ci") | .id' | Select-Object -First 1)
  if ($mainRunId) { break }
  Start-Sleep -Seconds 10
}
if (-not $mainRunId) { throw 'Exact-main CI run was not found.' }
& 'C:\Program Files\GitHub CLI\gh.exe' run watch $mainRunId --repo karthik-kannan-product/CivicLoop --exit-status
```

Expected: the CI run whose `head_sha` is exactly `$mergedSha` exits successfully.

- [ ] **Step 5: Set and verify GitHub homepage metadata**

Run:

```powershell
& 'C:\Program Files\GitHub CLI\gh.exe' repo edit karthik-kannan-product/CivicLoop --homepage 'https://civicloop.karthikkannan.ca/'
$homepage = (& 'C:\Program Files\GitHub CLI\gh.exe' repo view karthik-kannan-product/CivicLoop --json homepageUrl --jq .homepageUrl).Trim()
if ($homepage -ne 'https://civicloop.karthikkannan.ca/') { throw 'GitHub homepage metadata is incorrect.' }
```

Expected: metadata verification returns the exact Vultr homepage URL.

---

### Task 7: Deploy the exact merged SHA and verify Vultr

**Files:**
- Protected GitHub Actions deployment state.
- Modify after success: private `develop/civicloop/handoffs/current-vultr-deployment.md`.

**Interfaces:**
- Consumes: exact merged `main` SHA from Task 6 and existing protected production workflow.
- Produces: deployed revision equality, live route evidence, and a non-secret handoff entry.

- [ ] **Step 1: Dispatch the protected deployment**

Run:

```powershell
$releaseSha = (& 'C:\Program Files\GitHub CLI\gh.exe' api repos/karthik-kannan-product/CivicLoop/commits/main --jq .sha).Trim()
& 'C:\Program Files\GitHub CLI\gh.exe' workflow run deploy-production.yml --repo karthik-kannan-product/CivicLoop -f commit_sha=$releaseSha
$runId = (& 'C:\Program Files\GitHub CLI\gh.exe' run list --repo karthik-kannan-product/CivicLoop --workflow deploy-production.yml --limit 1 --json databaseId --jq '.[0].databaseId').Trim()
& 'C:\Program Files\GitHub CLI\gh.exe' run watch $runId --repo karthik-kannan-product/CivicLoop --exit-status
```

Expected: the protected environment requests/receives its normal human approval, backup and deployment jobs pass, and the run exits 0. Do not bypass or weaken the approval gate.

- [ ] **Step 2: Independently verify server revision and health without reading secrets**

Run:

```powershell
$env:GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe'
$serverState = & 'C:\Windows\System32\OpenSSH\ssh.exe' -o BatchMode=yes linuxuser@civicloop.karthikkannan.ca 'set -eu; cat /var/lib/civicloop/current-revision; cd /opt/civicloop/app; docker compose ps --format json; docker compose exec -T web python scripts/readiness.py --base-url http://localhost:8000 --require-admin-identity --require-admin-integrations'
$serverState
if ($serverState -notmatch [regex]::Escape($releaseSha)) { throw 'Server revision does not match release SHA.' }
```

Expected: output contains the exact release SHA, healthy/running service state, and `CivicLoop is ready.` No environment or keyring contents are printed.

- [ ] **Step 3: Verify public routes in a fresh browser**

Open and inspect:

- `https://civicloop.karthikkannan.ca/` — hero, booking CTA, sandbox CTA, GitHub link, three status labels.
- `https://civicloop.karthikkannan.ca/login` — identity separation and two destinations, no credential form.
- `https://civicloop.karthikkannan.ca/sandbox` — existing synthetic login form.
- `https://civicloop.karthikkannan.ca/admin/security` — existing owner authentication surface, not the demo form.

Check at 320px, 768px, and 1440px for keyboard focus, horizontal overflow, readable order, and console errors. Do not enter, capture, or expose passwords or TOTP material.

- [ ] **Step 4: Record non-secret release evidence in the private handoff**

Append a dated section to `D:\git files\.worktrees\agentic-playbox-hermes-live-draft\develop\civicloop\handoffs\current-vultr-deployment.md` containing: public merged/deployed SHA, PR and CI/deployment run URLs, backup identifier without contents, readiness result, verified public routes, GitHub homepage value, previous rollback SHA, critical-log count, and the statement that no credentials or provider mutations occurred.

Commit and push only that handoff change on the existing private branch:

```powershell
Set-Location 'D:\git files\.worktrees\agentic-playbox-hermes-live-draft'
git add develop/civicloop/handoffs/current-vultr-deployment.md
git commit -m "docs: record CivicLoop public entry release"
$env:GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe'
git push origin codex/hermes-live-draft-ops
git status --short --branch
```

Expected: the private branch pushes successfully and remains clean; its remote SHA matches local HEAD.

- [ ] **Step 5: Report the four completion states and next checklist item**

Report separately:

1. Implemented locally: exact source commit range.
2. Verified: exact focused commands and results; do not call them a full suite unless exact-main CI passed.
3. GitHub synchronized: PR URL, merged `main` SHA, GitHub homepage value, private handoff branch SHA.
4. Production activated: deployment run, deployed SHA equality, backup/rollback evidence, route smoke, readiness, and log result.

State that Task 5—the disabled, private-network Hermes adapter source implementation—is the next independent checklist unit. Do not begin it inside this release task.
