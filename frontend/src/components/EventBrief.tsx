import { useEffect, useState, type FormEvent } from "react";

import type { DemoState } from "../types";
import { EventFactsFields, publicEventFacts } from "./EventFactsFields";

type Props = {
  state: DemoState;
  isOperator: boolean;
  busy: boolean;
  onRun: () => void;
  onResolve: (answers: Record<string, string>) => void;
  ownerEvent?: boolean;
  onSaveFacts?: (facts: Record<string, string>) => void;
};

export function EventBrief({ state, isOperator, busy, onRun, onResolve, ownerEvent = false, onSaveFacts }: Props) {
  const [answers, setAnswers] = useState({
    venue_name: "",
    venue_address: "",
    access_instructions: "",
  });
  const { event, workflow } = state;
  const facts = event.revision.facts;

  useEffect(() => {
    if (workflow.status === "needs_input") {
      setAnswers({
        venue_name: String(facts.venue_name ?? ""),
        venue_address: String(facts.venue_address ?? ""),
        access_instructions: String(facts.access_instructions ?? ""),
      });
    }
  }, [facts.access_instructions, facts.venue_address, facts.venue_name, workflow.status]);

  function submitAnswers(event: FormEvent) {
    event.preventDefault();
    onResolve(answers);
  }

  return (
    <section className="event-brief" aria-labelledby="event-title">
      <div className="event-brief__heading">
        <div>
          <p className="eyebrow">{ownerEvent ? "Owner event brief" : "Event campaign"} · {String(facts.city || "Location not confirmed")}</p>
          <h1 id="event-title">{event.title}</h1>
          <p className="event-meta">
            <span>Revision {event.revision.version}</span> · {String(facts.date || "Date not confirmed")} ·{" "}
            {String(facts.start_time || "Start not set")}–{String(facts.end_time || "End not set")} {String(facts.timezone || "Timezone not confirmed")}
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
          <dd>{ownerEvent ? "Select audience and suppressions during provider request review" : "Active New York members"}</dd>
        </div>
        <div>
          <dt>{ownerEvent ? "Signup" : "Ticket and sponsor rule"}</dt>
          <dd>{ownerEvent ? String(facts.signup_url || "Not confirmed") : `$${String(facts.general_ticket_price)} · Gold members receive 25% off`}</dd>
        </div>
      </dl>

      {workflow.status === "draft" && isOperator && (
        <div className="action-strip">
          <div>
            <strong>{ownerEvent ? "Prepare event copy for review" : "Ready for a grounded review"}</strong>
            <span>{ownerEvent ? "Confirmed facts prepare drafts. Audience and provider requests need separate review." : "Three deterministic specialists will prepare one package."}</span>
          </div>
          <button className="button button--primary" disabled={busy} onClick={onRun}>
            {busy ? "Running…" : ownerEvent ? "Prepare event drafts" : "Run LaunchLoop"}
          </button>
        </div>
      )}

      {ownerEvent && isOperator && onSaveFacts && (
        <form className="owner-facts-editor" key={event.revision.id} onSubmit={(formEvent) => {
          formEvent.preventDefault();
          onSaveFacts(publicEventFacts(formEvent.currentTarget));
        }}>
          <h2>Confirm or correct the event facts</h2>
          <p>Save a new revision before preparing drafts. Event facts do not select recipients or approve provider actions.</p>
          {workflow.package?.missing_fields.length ? (
            <p role="status">Still needed: {workflow.package.questions.map((question) => question.prompt).join(" ")}</p>
          ) : null}
          <EventFactsFields facts={facts} disabled={busy} />
          <button className="button button--primary" disabled={busy} type="submit">
            Save event facts as revision {event.revision.version + 1}
          </button>
        </form>
      )}

      {!ownerEvent && workflow.status === "needs_input" && isOperator && (
        <form className="remediation" onSubmit={submitAnswers}>
          <div className="remediation__intro">
            <p className="eyebrow">Human input required</p>
            <h2>Confirm the missing event facts</h2>
            <p>Save confirmed details now, then come back for the remaining items.</p>
          </div>
          <label>
            <span>Confirmed venue name</span>
            <input
              value={answers.venue_name}
              onChange={(event) =>
                setAnswers({ ...answers, venue_name: event.target.value })
              }
            />
          </label>
          <label>
            <span>Complete venue address</span>
            <input
              value={answers.venue_address}
              onChange={(event) =>
                setAnswers({ ...answers, venue_address: event.target.value })
              }
            />
          </label>
          <label className="remediation__wide">
            <span>Arrival and accessibility instructions</span>
            <textarea
              rows={3}
              value={answers.access_instructions}
              onChange={(event) =>
                setAnswers({ ...answers, access_instructions: event.target.value })
              }
            />
          </label>
          <button className="button button--primary" disabled={busy} type="submit">
            Save progress as revision {event.revision.version + 1}
          </button>
        </form>
      )}
    </section>
  );
}
