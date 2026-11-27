import type { ReactNode } from "react";

const BOOKING_URL =
  "https://calendar.proton.me/bookings#KkKDMGq1h4GQZfzYq7jvGNFicfShuijjIRF3Wtg8ods=";
const GITHUB_URL = "https://github.com/karthik-kannan-product/CivicLoop";
const BROWSER_DEMO_URL = "https://karthik-kannan-product.github.io/CivicLoop/";
const ARCHITECTURE_URL = `${GITHUB_URL}/blob/main/docs/2026-07-30-civicloop-v1-architecture-design.md`;

const roadmap = [
  {
    status: "Live",
    title: "Observable LaunchLoop",
    body: "Start from an idea or bounded Eventbrite import, resolve missing facts, prepare campaign assets, and require an independent decision on the exact package.",
  },
  {
    status: "Owner pilot",
    title: "Self-hosted Hermes-assisted drafts",
    body: "Hermes prepares a proposal for owner review. The live pilot verified an unpublished Eventbrite draft and unscheduled Iterable email campaigns after explicit approval.",
  },
  {
    status: "Planned",
    title: "Reusable nonprofit operations loops",
    body: "Extend the same human-approved pattern to membership, sponsor, engagement, and reporting workflows.",
  },
];

const proofPoints = [
  [
    "Human approval by design",
    "Consequential actions stop at a clear decision boundary.",
  ],
  [
    "Observable and auditable",
    "Revisions, evaluations, decisions, and outcomes leave durable evidence.",
  ],
  [
    "Open source and self-hostable",
    "Inspect the implementation, adapt the loop, and keep control of deployment.",
  ],
];

function ExternalLink({
  href,
  children,
  className = "public-link",
}: {
  href: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <a
      className={className}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
    </a>
  );
}

export function PublicHome() {
  return (
    <div className="public-page">
      <header className="public-nav">
        <a className="public-brand" href="/">
          CivicLoop
        </a>
        <nav aria-label="Primary navigation">
          <a className="public-link" href="/login">
            Log in
          </a>
          <ExternalLink href={ARCHITECTURE_URL}>Read the architecture</ExternalLink>
          <ExternalLink href={GITHUB_URL}>GitHub</ExternalLink>
        </nav>
      </header>

      <main>
        <section className="public-hero" aria-labelledby="public-title">
          <p className="eyebrow">Open-source operations infrastructure for nonprofits</p>
          <h1 id="public-title">Human-approved AI workflows for work that matters.</h1>
          <p className="public-hero__lede">
            Prepare nonprofit event campaigns with LaunchLoop. Review the exact
            package, approve drafts, and see what happened in the audit trail.
          </p>
          <div className="public-actions">
            <ExternalLink
              className="button button--primary public-cta"
              href={BOOKING_URL}
            >
              Book a conversation
            </ExternalLink>
            <a
              className="button public-cta public-cta--secondary"
              href={BROWSER_DEMO_URL}
            >
              Try the browser demo
            </a>
          </div>
          <ExternalLink href={GITHUB_URL}>Follow the development on GitHub</ExternalLink>
        </section>

        <section className="public-section" aria-labelledby="today-title">
          <p className="eyebrow">What works today</p>
          <h2 id="today-title">From event draft to an approval-ready campaign.</h2>
          <ol className="public-grid public-steps">
            <li>
              <strong>Start with grounded context.</strong>
              <span>Try a sample event in the sandbox or import an Eventbrite draft in the owner pilot.</span>
            </li>
            <li>
              <strong>Resolve and prepare.</strong>
              <span>Confirm missing facts and generate reviewable campaign assets.</span>
            </li>
            <li>
              <strong>Approve with evidence.</strong>
              <span>
                Review the exact package and its audit trail before approving a draft.
              </span>
            </li>
          </ol>
          <p className="public-safety-note">
            Sandbox evaluation uses synthetic data. The owner pilot stops at draft
            creation. Publishing, sending, scheduling, and recipient imports are outside the pilot.
          </p>
        </section>

        <section className="public-section" aria-labelledby="difference-title">
          <p className="eyebrow">Why it is different</p>
          <h2 id="difference-title">Useful automation without surrendering control.</h2>
          <div className="public-grid">
            {proofPoints.map(([title, body]) => (
              <article className="public-card" key={title}>
                <h3>{title}</h3>
                <p>{body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="public-section" aria-labelledby="roadmap-title">
          <p className="eyebrow">Roadmap</p>
          <h2 id="roadmap-title">
            A focused loop today. A reusable operating model tomorrow.
          </h2>
          <div className="public-grid">
            {roadmap.map((item) => (
              <article className="public-card" key={item.status}>
                <span className="public-status">{item.status}</span>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section
          className="public-section public-invitation"
          aria-labelledby="invitation-title"
        >
          <h2 id="invitation-title">
            Building safer agentic operations for mission-driven teams.
          </h2>
          <p>
            If you lead nonprofit operations, build responsible AI systems, or want
            to help shape the next loop, let&apos;s talk.
          </p>
          <div className="public-actions">
            <ExternalLink
              className="button button--primary public-cta"
              href={BOOKING_URL}
            >
              Book a conversation
            </ExternalLink>
            <ExternalLink href={GITHUB_URL}>View the source</ExternalLink>
          </div>
        </section>
      </main>

      <footer className="public-footer">
        <span>CivicLoop</span>
        <span>Open source · Human approved · Built in public</span>
      </footer>
    </div>
  );
}
