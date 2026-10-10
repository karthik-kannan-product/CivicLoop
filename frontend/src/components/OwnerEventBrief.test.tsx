import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { CampaignPackage, DemoState } from "../types";
import { EventBrief } from "./EventBrief";
import { ReviewPackage } from "./ReviewPackage";
import { LaneBoard } from "./LaneBoard";

afterEach(cleanup);

const facts = {
  title: "Community Forum", date: "2026-12-12", start_time: "10:00", end_time: "12:00",
  timezone: "Europe/London", city: "London", region: "England", country: "GB",
  venue_name: "Community Hall", venue_address: "12 Example Road",
  access_instructions: "Step-free entrance", signup_url: "https://example.test/forum",
};
const ownerPackage: CampaignPackage = {
  schema_id: "owner_event_draft_v1", status: "ready_for_review", missing_fields: [], questions: [],
  assets: { invitation: { subject: "Forum", body: "Join us" }, reminder: { subject: "Reminder", body: "See you soon" }, social: { body: "Forum" } },
  audience: { id: null, name: "Deferred", member_count: 0, language: "" },
  sponsor: { passed: false, tier: "", expected_discount_percent: null, actual_discount_percent: null },
  lanes: {}, evidence: ["Confirmed public facts"],
};
const state: DemoState = {
  actors: [], event: { id: "forum", title: facts.title, revision: { id: 7, version: 2, facts, source_kind: "eventbrite", author: "owner" } },
  workflow: { id: "workflow", status: "draft", package: null, package_hash: null },
  approval: null, execution: null, evaluation: null, timeline: [],
};

test("imported facts can be corrected through the complete public facts editor", () => {
  const onSaveFacts = vi.fn();
  render(<EventBrief state={state} isOperator busy={false} ownerEvent onRun={vi.fn()} onResolve={vi.fn()} onSaveFacts={onSaveFacts} />);
  expect(screen.getByLabelText("City")).toHaveValue("London");
  fireEvent.change(screen.getByLabelText("City"), { target: { value: "Oxford" } });
  fireEvent.submit(screen.getByRole("button", { name: "Save event facts as revision 3" }).closest("form")!);
  expect(onSaveFacts).toHaveBeenCalledWith({ ...facts, city: "Oxford" });
  expect(screen.queryByText("Active New York members")).not.toBeInTheDocument();
  expect(screen.queryByText(/Gold members receive/)).not.toBeInTheDocument();
});

test("a new server revision resets the editor and saving is unavailable without owner authorization", () => {
  const props = { isOperator: true, busy: false, ownerEvent: true, onRun: vi.fn(), onResolve: vi.fn(), onSaveFacts: vi.fn() };
  const { rerender } = render(<EventBrief state={state} {...props} />);
  fireEvent.change(screen.getByLabelText("City"), { target: { value: "Unsaved city" } });
  rerender(<EventBrief state={{ ...state, event: { ...state.event, revision: { ...state.event.revision, id: 8, version: 3, facts: { ...facts, city: "Bath" } } } }} {...props} />);
  expect(screen.getByLabelText("City")).toHaveValue("Bath");
  rerender(<EventBrief state={state} {...props} onSaveFacts={undefined} />);
  expect(screen.queryByRole("button", { name: /Save event facts/ })).not.toBeInTheDocument();
});

test("owner package explains deferred provider policy without fabricated audience, sponsor or fixture evaluation", () => {
  render(<><ReviewPackage campaignPackage={ownerPackage} canEvaluate onEvaluate={vi.fn()} /><LaneBoard campaignPackage={null} ownerEvent /></>);
  expect(screen.getByText("Deferred to provider review")).toBeInTheDocument();
  expect(screen.getByText("No sponsor policy applied")).toBeInTheDocument();
  expect(screen.queryByText(/people · aggregate/)).not.toBeInTheDocument();
  expect(screen.queryByText(/gold discount/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Run advisory evaluation" })).not.toBeInTheDocument();
  expect(screen.queryByText("Deterministic fake-agent run")).not.toBeInTheDocument();
});
