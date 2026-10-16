import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import { EventStartPanel } from "./EventStartPanel";
import { eventFactFields } from "./EventFactsFields";
import type { DemoState } from "../types";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    listEventbriteEvents: vi.fn(),
    refreshEventbriteEvents: vi.fn(),
    selectEventbriteEvent: vi.fn(),
    startManualEvent: vi.fn(),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

test("shows the safe zero-event state without blocking manual work", async () => {
  vi.mocked(api.listEventbriteEvents).mockResolvedValue([]);

  render(<EventStartPanel onStarted={vi.fn()} />);

  expect(await screen.findByText(/No Eventbrite events are available/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start manual brief" })).toBeEnabled();
});

test("shows many events and prevents selection of an unavailable event", async () => {
  vi.mocked(api.listEventbriteEvents).mockResolvedValue([
    { id: "1", provider_event_id: "1", title: "Draft Forum", status: "draft", start_at: null, timezone: "America/Toronto", available: true, selectable: true },
    { id: "2", provider_event_id: "2", title: "Deleted Forum", status: "draft", start_at: null, timezone: "America/Toronto", available: false, selectable: false },
  ]);

  render(<EventStartPanel onStarted={vi.fn()} />);

  expect(await screen.findByText("Draft Forum")).toBeInTheDocument();
  expect(screen.getByText("Deleted Forum")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Unavailable" })).toBeDisabled();
});

test("loads more draft pages and shows completion", async () => {
  vi.mocked(api.listEventbriteEvents).mockResolvedValue([]);
  const draft = { id: "1", provider_event_id: "1", title: "Draft Forum", status: "draft", start_at: null, timezone: "UTC", available: true, selectable: true };
  vi.mocked(api.refreshEventbriteEvents)
    .mockResolvedValueOnce({ events: [draft], next_cursor: "opaque-next", has_more: true, complete: false })
    .mockResolvedValueOnce({ events: [draft, { ...draft, id: "2", title: "Second Forum" }], next_cursor: null, has_more: false, complete: true });
  render(<EventStartPanel onStarted={vi.fn()} />);
  const { fireEvent } = await import("@testing-library/react");
  fireEvent.click(await screen.findByRole("button", { name: "Refresh events" }));
  fireEvent.click(await screen.findByRole("button", { name: "Load more drafts" }));
  expect(await screen.findByText("Second Forum")).toBeInTheDocument();
  expect(screen.getAllByText("Draft Forum")).toHaveLength(1);
  expect(api.refreshEventbriteEvents).toHaveBeenLastCalledWith("opaque-next");
  expect(screen.getByText("All accessible drafts have been loaded.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Load more drafts" })).not.toBeInTheDocument();
});

test("manual creation sends every confirmed public fact without provenance markers", async () => {
  vi.mocked(api.listEventbriteEvents).mockResolvedValue([]);
  const created = { event: { id: "new-event" } } as DemoState;
  vi.mocked(api.startManualEvent).mockResolvedValue(created);
  const onStarted = vi.fn();
  render(<EventStartPanel onStarted={onStarted} />);
  const facts = {
    title: "Community Forum", date: "2026-12-12", start_time: "10:00", end_time: "12:00",
    timezone: "Europe/London", city: "London", region: "England", country: "GB",
    venue_name: "Community Hall", venue_address: "12 Example Road",
    access_instructions: "Step-free entrance on Example Road", signup_url: "https://example.test/forum",
  };
  const form = screen.getByRole("button", { name: "Start manual brief" }).closest("form")!;
  expect(form.checkValidity()).toBe(false);
  for (const field of eventFactFields) {
    fireEvent.change(screen.getByLabelText(field.label), { target: { value: facts[field.name] } });
  }
  expect(form.checkValidity()).toBe(true);
  fireEvent.submit(form);
  expect(await screen.findByText(/No Eventbrite events are available/)).toBeInTheDocument();
  expect(api.startManualEvent).toHaveBeenCalledWith(facts);
  expect(onStarted).toHaveBeenCalledWith(created);
});
