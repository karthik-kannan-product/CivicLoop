export type Actor = {
  slug: string;
  display_name: string;
  role: "operator" | "approver";
};

export type HermesStartReceipt = {
  schema_version: "1.0";
  run_id: string;
  status: "queued";
};

export type HermesRunStatus = {
  schema_version: "1.0";
  run_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  failure_category: "budget_exhausted" | "cancelled" | "dependency_unavailable" |
    "invalid_output" | "provider_unavailable" | "timeout" | null;
  cancel_requested: boolean;
  proposal_count: number;
  pending_operation_count: number;
};

export type PendingOperation = {
  operation_id: string;
  provider: "eventbrite" | "iterable";
  operation_kind: "create_eventbrite_draft" | "create_iterable_email_draft" |
    "create_iterable_reminder_draft";
  status: "pending";
  action_digest: string;
};

export type Lane = {
  label: string;
  status: "complete" | "needs_input" | "blocked";
  summary: string;
};

type PackageContents = {
  status: string;
  missing_fields: string[];
  questions: Array<{ field: string; prompt: string }>;
  assets: {
    invitation: { subject: string; body: string };
    reminder: { subject: string; body: string };
    social: { body: string };
  };
  audience: {
    id: string | null;
    name: string;
    member_count: number;
    language: string;
  };
  lanes: Record<string, Lane>;
  evidence: string[];
};

export type CampaignPackage = PackageContents & ({
  schema_id: "owner_event_draft_v1";
  sponsor: {
    passed: false;
    tier: string;
    expected_discount_percent: null;
    actual_discount_percent: null;
  };
} | {
  schema_id?: undefined;
  sponsor: {
    passed: boolean;
    tier: string;
    expected_discount_percent: number;
    actual_discount_percent: number;
    general_ticket_price: number;
    sponsor_ticket_price: number;
  };
});

export type DemoState = {
  deployment_mode?: "server" | "browser_local";
  actors: Actor[];
  event: {
    id: string;
    title: string;
    revision: {
      id: number;
      version: number;
      facts: Record<string, string | number | boolean>;
      source_kind?: "manual" | "eventbrite" | "synthetic" | "unsupported";
      author: string;
    };
  };
  workflow: {
    id: string;
    status: string;
    package: CampaignPackage | null;
    package_hash: string | null;
  };
  approval: {
    id: string;
    status: string;
    package_hash: string;
    submitter: string;
    approver: string | null;
    reason: string;
  } | null;
  execution: {
    id: string;
    status: string;
    receipt: {
      connector: string;
      audience_count: number;
      mode: string;
      external_actions: number;
      message: string;
    };
  } | null;
  evaluation: {
    state: "pending" | "passed" | "failed" | "unavailable" | "denied";
    run_id: string;
    trace_id: string;
    rubric_id: string;
    rubric_version: number;
    risk_labels: string[];
    summary: string;
    provider: "openai";
    model: string;
    input_tokens: number;
    output_tokens: number;
    cost_microusd: number;
    failure_category: string | null;
    advisory_only: true;
  } | null;
  timeline: Array<{
    id: number;
    actor: string;
    action: string;
    from_status: string;
    to_status: string;
    details: Record<string, unknown>;
    created_at: string;
  }>;
};
