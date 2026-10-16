import { useEffect, useState } from "react";

import {
  listEventbriteEvents,
  refreshEventbriteEvents,
  selectEventbriteEvent,
  startManualEvent,
  type EventbriteEvent,
} from "../api";
import type { DemoState } from "../types";
import { EventFactsFields, publicEventFacts } from "./EventFactsFields";

export function EventStartPanel({ onStarted }: { onStarted: (state: DemoState) => void }) {
  const [events, setEvents] = useState<EventbriteEvent[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void listEventbriteEvents().then(setEvents).catch(() => setEvents(null));
  }, []);

  async function act(operation: () => Promise<void>) {
    setBusy(true);
    setMessage(null);
    try {
      await operation();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "CivicLoop could not continue.");
    } finally {
      setBusy(false);
    }
  }

  async function refresh(loadMore = false) {
    const page = await refreshEventbriteEvents(loadMore && cursor ? cursor : undefined);
    setEvents((previous) => Array.from(new Map([
      ...(loadMore ? previous ?? [] : []), ...page.events,
    ].filter((event) => event.status === "draft").map((event) => [event.id, event])).values()));
    setCursor(page.next_cursor);
    setComplete(page.complete);
  }

  const drafts = events?.filter((event) => event.status === "draft") ?? [];

  return (
    <section className="event-start" aria-labelledby="event-start-title">
      <div>
        <p className="eyebrow">Start or switch work</p>
        <h2 id="event-start-title">Choose the event CivicLoop should coordinate</h2>
        <p>Enter confirmed public event facts, or import an Eventbrite draft and complete its brief. Nothing is changed in Eventbrite.</p>
      </div>
      {message && <div className="inline-error" role="alert">{message}</div>}
      <form className="event-start__manual" onSubmit={(formEvent) => {
        formEvent.preventDefault();
        const facts = publicEventFacts(formEvent.currentTarget);
        void act(async () => onStarted(await startManualEvent(facts)));
      }}>
        <EventFactsFields disabled={busy} />
        <button className="button button--secondary" disabled={busy} type="submit">Start manual brief</button>
      </form>
      {events !== null && (
        <div className="event-start__provider">
          <div className="event-start__provider-heading">
            <h3>Eventbrite</h3>
            <button className="button button--secondary" disabled={busy} onClick={() => void act(async () => refresh())} type="button">Refresh events</button>
          </div>
          <p role="status">{complete ? "All accessible drafts have been loaded." : cursor ? "More drafts may be available." : "Refresh to browse accessible drafts."}</p>
          {cursor && <button className="button button--secondary" disabled={busy} onClick={() => void act(() => refresh(true))} type="button">Load more drafts</button>}
          {drafts.length === 0 ? <p>No Eventbrite events are available. You can still start a manual brief.</p> : (
            <ul className="event-start__events">
              {drafts.map((event) => (
                <li key={event.id}>
                  <div><strong>{event.title}</strong><span>{event.status} · {event.start_at ? new Date(event.start_at).toLocaleDateString() : "Date not set"}</span></div>
                  <button className="button button--secondary" disabled={busy || !event.selectable} onClick={() => void act(async () => onStarted(await selectEventbriteEvent(event.id)))} type="button">{event.selectable ? "Use event" : "Unavailable"}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
