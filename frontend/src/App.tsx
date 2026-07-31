import { useEffect, useState } from "react";

const lanes = ["Event Readiness", "Campaign Composer", "Audience and Policy"];

type HealthState = "checking" | "healthy" | "unavailable";

const healthMessage: Record<HealthState, string> = {
  checking: "Checking application",
  healthy: "Application healthy",
  unavailable: "Application unavailable",
};

export function App() {
  const [health, setHealth] = useState<HealthState>("checking");

  useEffect(() => {
    const controller = new AbortController();

    fetch("/api/v1/health/live", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health request failed");
        return response.json();
      })
      .then(() => setHealth("healthy"))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setHealth("unavailable");
      });

    return () => controller.abort();
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Open-source nonprofit operations</p>
          <h1>CivicLoop</h1>
        </div>
        <span
          aria-atomic="true"
          aria-live="polite"
          className={`health health--${health}`}
          role="status"
        >
          {healthMessage[health]}
        </span>
      </header>
      <main>
        <section className="intro" aria-labelledby="foundation-title">
          <div>
            <p className="eyebrow">Foundation increment</p>
            <h2 id="foundation-title">Human-approved agent workflows</h2>
            <p>
              The platform shell is running. Identity, event input, live agents,
              and approvals arrive in independently reviewed increments.
            </p>
          </div>
          <button type="button" disabled title="Available in a later increment">
            Start LaunchLoop
          </button>
        </section>
        <section className="workspace" aria-labelledby="agents-title">
          <h2 id="agents-title">Agent workspace</h2>
          <div className="lanes">
            {lanes.map((lane) => (
              <article className="lane" key={lane}>
                <p className="lane__state">
                  <span aria-hidden="true" className="lane__status" />
                  Not configured
                </p>
                <h3>{lane}</h3>
                <p>Reserved for a future, human-approved agent lane.</p>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
