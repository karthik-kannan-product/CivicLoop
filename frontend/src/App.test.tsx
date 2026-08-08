import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

const facts = {
  title: "New York International Youth Day Networking Breakfast",
  city: "New York",
  region: "NY",
  country: "US",
  date: "2026-08-12",
  start_time: "09:00",
  end_time: "12:00",
  timezone: "America/New_York",
  venue_name: "",
  venue_address: "",
  access_instructions: "",
  general_ticket_price: 40,
  signup_url: "https://example.test/eventbrite/ny-youth-day",
  sponsor_tier: "gold",
  sponsor_discount_percent: 25,
};

const baseState = {
  actors: [
    { slug: "maya", display_name: "Maya Chen", role: "operator" },
    { slug: "jordan", display_name: "Jordan Brooks", role: "approver" },
  ],
  event: {
    id: "ny-youth-day",
    title: facts.title,
    revision: { id: 1, version: 1, facts, author: "maya" },
  },
  workflow: {
    id: "0b636977-c214-40b5-ac32-9cb975d8dbbb",
    status: "draft",
    package: null,
    package_hash: null,
  },
  approval: null,
  execution: null,
  timeline: [
    {
      id: 1,
      actor: "Maya Chen",
      action: "demo_reset",
      from_status: "",
      to_status: "draft",
      details: { revision: 1 },
      created_at: "2026-07-31T12:00:00Z",
    },
  ],
};

const blockedPackage = {
  status: "needs_input",
  missing_fields: ["venue_name", "venue_address", "access_instructions"],
  questions: [
    { field: "venue_name", prompt: "What is the confirmed venue name?" },
    { field: "venue_address", prompt: "What is the complete venue address?" },
    {
      field: "access_instructions",
      prompt: "What arrival or accessibility instructions should guests receive?",
    },
  ],
  assets: {
    invitation: { subject: `Invitation: ${facts.title}`, body: "Meet at [Venue TBD]." },
    reminder: { subject: `Reminder: ${facts.title}`, body: "Meet at [Venue TBD]." },
    social: { body: "Venue: [Venue TBD]." },
  },
  audience: {
    id: "new_york_active_members",
    name: "New York Active Members",
    member_count: 418,
    language: "English",
  },
  sponsor: {
    passed: true,
    tier: "gold",
    expected_discount_percent: 25,
    actual_discount_percent: 25,
    general_ticket_price: 40,
    sponsor_ticket_price: 30,
  },
  lanes: {
    event_readiness: {
      label: "Event Readiness",
      status: "needs_input",
      summary: "3 event facts need an operator response.",
    },
    campaign_composer: {
      label: "Campaign Composer",
      status: "needs_input",
      summary: "Prepared invitation, reminder, and social drafts.",
    },
    audience_policy: {
      label: "Audience and Policy",
      status: "complete",
      summary: "Matched the approved New York audience and validated sponsor pricing.",
    },
  },
  evidence: ["Prepared review-only drafts; no external action was taken."],
};

function jsonResponse(value: object) {
  return Promise.resolve({ ok: true, json: async () => value });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

test("logs out of the authenticated demo workspace", async () => {
  vi.stubEnv("VITEST", "");
  vi.stubEnv("VITE_STATIC_DEMO", "false");
  const user = userEvent.setup();
  const authenticatedState = { ...baseState, deployment_mode: "server" };
  const responses = [
    {
      user: {
        username: "maya.operator",
        display_name: "Maya Chen",
        role: "operator",
      },
    },
    authenticatedState,
    { logged_out: true },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => jsonResponse(responses.shift() ?? { logged_out: true })),
  );

  render(<App />);

  expect(await screen.findByRole("heading", { name: facts.title })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Log out" }));

  expect(
    await screen.findByRole("heading", { name: "Enter the LaunchLoop workspace" }),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Demo password")).toHaveValue("");
  expect(screen.queryByText(/Temporary demo password/i)).not.toBeInTheDocument();
});

test("completes the operator-to-approver LaunchLoop journey", async () => {
  const user = userEvent.setup();
  const blocked = {
    ...baseState,
    workflow: { ...baseState.workflow, status: "needs_input", package: blockedPackage },
  };
  const revisionTwo = {
    ...baseState,
    event: {
      ...baseState.event,
      revision: {
        id: 2,
        version: 2,
        author: "maya",
        facts: {
          ...facts,
          venue_name: "Hudson Civic Center",
          venue_address: "455 West 34th Street, New York, NY 10001",
          access_instructions: "Use the 10th Avenue entrance.",
        },
      },
    },
  };
  const readyPackage = {
    ...blockedPackage,
    status: "ready_for_review",
    missing_fields: [],
    questions: [],
    lanes: Object.fromEntries(
      Object.entries(blockedPackage.lanes).map(([key, lane]) => [
        key,
        { ...lane, status: "complete" },
      ]),
    ),
  };
  const ready = {
    ...revisionTwo,
    workflow: {
      ...baseState.workflow,
      status: "ready_for_review",
      package: readyPackage,
      package_hash: "a".repeat(64),
    },
  };
  const submitted = {
    ...ready,
    workflow: { ...ready.workflow, status: "in_review" },
    approval: {
      id: "8da86312-f80a-4985-a290-b5076326b546",
      status: "pending",
      package_hash: "a".repeat(64),
      submitter: "maya",
      approver: null,
      reason: "",
    },
  };
  const completed = {
    ...submitted,
    workflow: { ...submitted.workflow, status: "completed" },
    approval: { ...submitted.approval, status: "approved", approver: "jordan" },
    execution: {
      id: "receipt-1",
      status: "delivered",
      receipt: {
        connector: "sandbox_iterable",
        audience_count: 418,
        mode: "simulation",
        external_actions: 0,
        message: "Sandbox delivery recorded. No email or social post was sent.",
      },
    },
  };

  const responses = [baseState, blocked, revisionTwo, ready, submitted, completed];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => jsonResponse(responses.shift() ?? completed)),
  );

  render(<App />);

  expect(await screen.findByRole("heading", { name: facts.title })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Run LaunchLoop" }));
  expect(await screen.findByText("3 event facts need an operator response.")).toBeInTheDocument();

  await user.type(screen.getByLabelText("Confirmed venue name"), "Hudson Civic Center");
  await user.type(
    screen.getByLabelText("Complete venue address"),
    "455 West 34th Street, New York, NY 10001",
  );
  await user.type(
    screen.getByLabelText("Arrival and accessibility instructions"),
    "Use the 10th Avenue entrance.",
  );
  await user.click(screen.getByRole("button", { name: "Save progress as revision 2" }));
  expect(await screen.findByText("Revision 2")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "Run LaunchLoop" }));
  expect(await screen.findByText("Ready for review")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Submit for approval" }));

  await user.selectOptions(screen.getByLabelText("Demo persona"), "jordan");
  await user.click(screen.getByRole("button", { name: "Approve exact package" }));

  expect(await screen.findByText("Sandbox delivery recorded")).toBeInTheDocument();
  expect(screen.getByText("418 people in approved audience")).toBeInTheDocument();
  expect(screen.getByText("No external messages sent")).toBeInTheDocument();
});

test("lets the approver reject and send the workflow back for operator updates", async () => {
  const user = userEvent.setup();
  const readyPackage = {
    ...blockedPackage,
    status: "ready_for_review",
    missing_fields: [],
    questions: [],
    lanes: Object.fromEntries(
      Object.entries(blockedPackage.lanes).map(([key, lane]) => [
        key,
        { ...lane, status: "complete" },
      ]),
    ),
  };
  const ready = {
    ...baseState,
    event: {
      ...baseState.event,
      revision: {
        id: 2,
        version: 2,
        author: "maya",
        facts: {
          ...facts,
          venue_name: "Hudson Civic Center",
          venue_address: "455 West 34th Street, New York, NY 10001",
          access_instructions: "Use the 10th Avenue entrance.",
        },
      },
    },
    workflow: {
      ...baseState.workflow,
      status: "ready_for_review",
      package: readyPackage,
      package_hash: "b".repeat(64),
    },
  };
  const submitted = {
    ...ready,
    workflow: { ...ready.workflow, status: "in_review" },
    approval: {
      id: "8da86312-f80a-4985-a290-b5076326b546",
      status: "pending",
      package_hash: "b".repeat(64),
      submitter: "maya",
      approver: null,
      reason: "",
    },
  };
  const rejected = {
    ...submitted,
    workflow: { ...submitted.workflow, status: "needs_input", package: null, package_hash: null },
    approval: {
      ...submitted.approval,
      status: "rejected",
      approver: "jordan",
      reason: "Add wheelchair-accessible entrance instructions.",
    },
  };

  const responses = [ready, submitted, rejected];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => jsonResponse(responses.shift() ?? rejected)),
  );

  render(<App />);

  expect(await screen.findByRole("heading", { name: facts.title })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Submit for approval" }));
  await user.selectOptions(screen.getByLabelText("Demo persona"), "jordan");
  await user.type(
    screen.getByLabelText("Requested change"),
    "Add wheelchair-accessible entrance instructions.",
  );
  await user.click(screen.getByRole("button", { name: "Reject and request changes" }));
  await user.selectOptions(screen.getByLabelText("Demo persona"), "maya");

  expect(await screen.findByText("Save confirmed details now, then come back for the remaining items.")).toBeInTheDocument();
  expect(screen.getByLabelText("Arrival and accessibility instructions")).toHaveValue(
    "Use the 10th Avenue entrance.",
  );
});

test("shows a recoverable error when the demo state cannot load", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "CivicLoop could not load the demo workspace.",
  );
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});
