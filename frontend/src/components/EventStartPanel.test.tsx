import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import * as api from "../api";
import { EventStartPanel } from "./EventStartPanel";

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
