import type { CampaignPackage, DemoState } from "./types";

const STORAGE_KEY = "civicloop_launchloop_demo_v1";
const WORKFLOW_ID = "00000000-0000-4000-8000-000000000001";
const APPROVAL_ID = "00000000-0000-4000-8000-000000000002";
const PACKAGE_HASH = "d7dae933f7448ca1f50b0f4f8f83b9b18c0dfe023a32a715f8029fa138ce1587";

type StaticOptions = {
  actor?: string;
  body?: Record<string, string>;
  method?: "GET" | "POST";
};

const initialFacts = {
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

function timelineItem(
  id: number,
  actor: string,
  action: string,
  fromStatus: string,
  toStatus: string,
) {
  return {
    id,
    actor,
    action,
    from_status: fromStatus,
    to_status: toStatus,
    details: {},
    created_at: new Date().toISOString(),
  };
}

function initialState(): DemoState {
  return {
    deployment_mode: "browser_local",
    actors: [
      { slug: "maya", display_name: "Maya Chen", role: "operator" },
      { slug: "jordan", display_name: "Jordan Brooks", role: "approver" },
    ],
    event: {
      id: "ny-youth-day",
      title: initialFacts.title,
      revision: { id: 1, version: 1, facts: initialFacts, author: "maya" },
    },
    workflow: {
      id: WORKFLOW_ID,
      status: "draft",
      package: null,
      package_hash: null,
    },
    approval: null,
    execution: null,
    timeline: [timelineItem(1, "Maya Chen", "demo_reset", "", "draft")],
  };
}

function campaignPackage(state: DemoState): CampaignPackage {
  const facts = state.event.revision.facts;
  const complete = state.event.revision.version === 2;
  const venue = complete ? String(facts.venue_name) : "[Venue TBD]";
  const address = complete ? String(facts.venue_address) : "[Venue Address TBD]";
  const access = complete ? String(facts.access_instructions) : "[Access Instructions TBD]";
  const accessSentence = /[.!?]$/.test(access) ? access : `${access}.`;
  const body = `${facts.title} is scheduled for ${facts.date} from ${facts.start_time} to ${facts.end_time} ${facts.timezone} at ${venue}. Address: ${address}. Access: ${accessSentence} Register: ${facts.signup_url}`;
  const missing = complete
    ? []
    : ["venue_name", "venue_address", "access_instructions"];
  return {
    status: complete ? "ready_for_review" : "needs_input",
    missing_fields: missing,
    questions: complete
      ? []
      : [
          { field: "venue_name", prompt: "What is the confirmed venue name?" },
          { field: "venue_address", prompt: "What is the complete venue address?" },
          {
            field: "access_instructions",
            prompt: "What arrival or accessibility instructions should guests receive?",
          },
        ],
    assets: {
      invitation: { subject: `Invitation: ${facts.title}`, body },
      reminder: { subject: `Reminder: ${facts.title}`, body },
      social: {
        body: `Join us for ${facts.title} in New York on ${facts.date}. Venue: ${venue}. Register: ${facts.signup_url}`,
      },
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
        status: complete ? "complete" : "needs_input",
        summary: complete
          ? "All required event facts are grounded."
          : "3 event facts need an operator response.",
      },
      campaign_composer: {
        label: "Campaign Composer",
        status: complete ? "complete" : "needs_input",
        summary: "Prepared invitation, reminder, and social drafts.",
      },
      audience_policy: {
        label: "Audience and Policy",
        status: "complete",
        summary: "Matched the approved New York audience and validated sponsor pricing.",
      },
    },
    evidence: [
      "Read one immutable event revision.",
      "Matched only an approved geography-specific audience.",
      "Recomputed sponsor discount math deterministically.",
      "Prepared review-only drafts; no external action was taken.",
    ],
  };
}

function readState(): DemoState {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) {
    const state = initialState();
    writeState(state);
    return state;
  }
  return JSON.parse(stored) as DemoState;
}

function writeState(state: DemoState): DemoState {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  return state;
}

function addTimeline(
  state: DemoState,
  actor: string,
  action: string,
  fromStatus: string,
  toStatus: string,
) {
  const displayName = actor === "jordan" ? "Jordan Brooks" : "Maya Chen";
  state.timeline.push(
    timelineItem(state.timeline.length + 1, displayName, action, fromStatus, toStatus),
  );
}

export async function requestStaticDemo(
  path: string,
  options: StaticOptions = {},
): Promise<DemoState> {
  const actor = options.actor ?? "maya";
  if (path === "/api/v1/demo" && (options.method ?? "GET") === "GET") {
    return readState();
  }
  if (path === "/api/v1/demo/reset") {
    localStorage.removeItem(STORAGE_KEY);
    return writeState(initialState());
  }

  const state = readState();
  if (path.endsWith("/runs")) {
    if (actor !== "maya" || state.workflow.status !== "draft") {
      throw new Error("Only Maya can run a draft workflow.");
    }
    const nextStatus =
      state.event.revision.version === 1 ? "needs_input" : "ready_for_review";
    state.workflow.package = campaignPackage(state);
    state.workflow.package_hash = PACKAGE_HASH;
    addTimeline(state, actor, "launchloop_ran", "draft", nextStatus);
    state.workflow.status = nextStatus;
    return writeState(state);
  }
  if (path.endsWith("/answers")) {
    if (actor !== "maya" || state.workflow.status !== "needs_input") {
      throw new Error("The workflow is not waiting for operator input.");
    }
    const body = options.body ?? {};
    const required = ["venue_name", "venue_address", "access_instructions"];
    if (required.some((field) => !body[field]?.trim())) {
      throw new Error("Complete every missing event fact.");
    }
    state.event.revision = {
      id: 2,
      version: 2,
      author: "maya",
      facts: { ...state.event.revision.facts, ...body },
    };
    state.workflow.package = null;
    state.workflow.package_hash = null;
    addTimeline(state, actor, "event_facts_resolved", "needs_input", "draft");
    state.workflow.status = "draft";
    return writeState(state);
  }
  if (path.endsWith("/submit")) {
    if (actor !== "maya" || state.workflow.status !== "ready_for_review") {
      throw new Error("Only a review-ready package can be submitted.");
    }
    state.approval = {
      id: APPROVAL_ID,
      status: "pending",
      package_hash: PACKAGE_HASH,
      submitter: "maya",
      approver: null,
      reason: "",
    };
    addTimeline(state, actor, "package_submitted", "ready_for_review", "in_review");
    state.workflow.status = "in_review";
    return writeState(state);
  }
  if (path.includes("/approvals/") && path.endsWith("/decision")) {
    const approval = state.approval;
    if (!approval) {
      throw new Error("The approval request does not exist.");
    }
    if (actor === approval.submitter) {
      throw new Error("The package submitter cannot approve their own work.");
    }
    if (actor !== "jordan" || state.workflow.status !== "in_review") {
      throw new Error("A separate approver must decide this package.");
    }
    if (options.body?.package_hash !== approval.package_hash) {
      throw new Error("The package changed after review.");
    }
    approval.status = "approved";
    approval.approver = "jordan";
    addTimeline(state, actor, "package_approved", "in_review", "approved");
    addTimeline(state, actor, "sandbox_receipt_recorded", "approved", "completed");
    state.workflow.status = "completed";
    state.execution = {
      id: "demo-receipt-0001",
      status: "delivered",
      receipt: {
        connector: "sandbox_iterable",
        audience_count: 418,
        mode: "simulation",
        external_actions: 0,
        message: "Sandbox delivery recorded. No email or social post was sent.",
      },
    };
    return writeState(state);
  }
  throw new Error("This demo action is unavailable.");
}
