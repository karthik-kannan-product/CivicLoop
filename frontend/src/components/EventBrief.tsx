import { useState, type FormEvent } from "react";

import type { DemoState } from "../types";

type Props = {
  state: DemoState;
  isOperator: boolean;
  busy: boolean;
  onRun: () => void;
  onResolve: (answers: Record<string, string>) => void;
};

export function EventBrief({ state, isOperator, busy, onRun, onResolve }: Props) {
  const [answers, setAnswers] = useState({
    venue_name: "",
    venue_address: "",
    access_instructions: "",
  });
  const { event, workflow } = state;
  const facts = event.revision.facts;

  function submitAnswers(event: FormEvent) {
    event.preventDefault();
    onResolve(answers);
  }

  return (
    <section className="event-brief" aria-labelledby="event-title">
      <div className="event-brief__heading">
        <div>
          <p className="eyebrow">Event campaign · New York</p>
          <h1 id="event-title">{event.title}</h1>
          <p className="event-meta">
            <span>Revision {event.revision.version}</span> · {String(facts.date)} ·{" "}
            {String(facts.start_time)}–{String(facts.end_time)} {String(facts.timezone)}
          </p>
        </div>
        <span className={`status status--${workflow.status}`}>
          {workflow.status.replaceAll("_", " ")}
        </span>
      </div>

      <dl className="facts">
        <div>
          <dt>Venue</dt>
          <dd>{String(facts.venue_name || "Not confirmed")}</dd>
        </div>
        <div>
          <dt>Audience</dt>
          <dd>Active New York members</dd>
        </div>
        <div>
          <dt>Ticket and sponsor rule</dt>
          <dd>${String(facts.general_ticket_price)} · Gold members receive 25% off</dd>
        </div>
      </dl>

      {workflow.status === "draft" && isOperator && (
        <div className="action-strip">
          <div>
            <strong>Ready for a grounded review</strong>
            <span>Three deterministic specialists will prepare one package.</span>
          </div>
          <button className="button button--primary" disabled={busy} onClick={onRun}>
            {busy ? "Running…" : "Run LaunchLoop"}
          </button>
        </div>
      )}

      {workflow.status === "needs_input" && isOperator && (
        <form className="remediation" onSubmit={submitAnswers}>
          <div className="remediation__intro">
            <p className="eyebrow">Human input required</p>
            <h2>Confirm the missing event facts</h2>
            <p>LaunchLoop preserved placeholders instead of inventing venue details.</p>
          </div>
          <label>
            <span>Confirmed venue name</span>
            <input
              required
              value={answers.venue_name}
              onChange={(event) =>
                setAnswers({ ...answers, venue_name: event.target.value })
              }
            />
          </label>
          <label>
            <span>Complete venue address</span>
            <input
              required
              value={answers.venue_address}
              onChange={(event) =>
                setAnswers({ ...answers, venue_address: event.target.value })
              }
            />
          </label>
          <label className="remediation__wide">
            <span>Arrival and accessibility instructions</span>
            <textarea
              required
              rows={3}
              value={answers.access_instructions}
              onChange={(event) =>
                setAnswers({ ...answers, access_instructions: event.target.value })
              }
            />
          </label>
          <button className="button button--primary" disabled={busy} type="submit">
            Save facts as revision {event.revision.version + 1}
          </button>
        </form>
      )}
    </section>
  );
}
