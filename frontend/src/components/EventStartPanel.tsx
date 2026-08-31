import { useEffect, useState } from "react";

import {
  listEventbriteEvents,
  refreshEventbriteEvents,
  selectEventbriteEvent,
  startManualEvent,
  type EventbriteEvent,
} from "../api";
import type { DemoState } from "../types";

export function EventStartPanel({ onStarted }: { onStarted: (state: DemoState) => void }) {
  const [events, setEvents] = useState<EventbriteEvent[] | null>(null);
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

  return (
    <section className="event-start" aria-labelledby="event-start-title">
      <div>
        <p className="eyebrow">Start or switch work</p>
        <h2 id="event-start-title">Choose the event CivicLoop should coordinate</h2>
        <p>Create a local brief, or safely refresh Eventbrite metadata. Nothing is changed in Eventbrite.</p>
      </div>
      {message && <div className="inline-error" role="alert">{message}</div>}
      <form className="event-start__manual" onSubmit={(formEvent) => {
        formEvent.preventDefault();
        const data = new FormData(formEvent.currentTarget);
        void act(async () => onStarted(await startManualEvent({
          title: String(data.get("title") ?? ""),
          date: String(data.get("date") ?? ""),
          timezone: String(data.get("timezone") ?? ""),
        })));
      }}>
        <input aria-label="Event title" name="title" placeholder="Event title" maxLength={240} required />
        <input aria-label="Event date" name="date" type="date" required />
        <input aria-label="Event timezone" name="timezone" defaultValue="America/Toronto" required />
        <button className="button button--secondary" disabled={busy} type="submit">Start manual brief</button>
      </form>
      {events !== null && (
        <div className="event-start__provider">
          <div className="event-start__provider-heading">
            <h3>Eventbrite</h3>
            <button className="button button--secondary" disabled={busy} onClick={() => void act(async () => setEvents(await refreshEventbriteEvents()))} type="button">Refresh events</button>
          </div>
          {events.length === 0 ? <p>No Eventbrite events are available. You can still start a manual brief.</p> : (
            <ul className="event-start__events">
              {events.map((event) => (
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
