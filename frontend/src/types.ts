export type Actor = {
  slug: string;
  display_name: string;
  role: "operator" | "approver";
};

export type Lane = {
  label: string;
  status: "complete" | "needs_input" | "blocked";
  summary: string;
};

export type CampaignPackage = {
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
  sponsor: {
    passed: boolean;
    tier: string;
    expected_discount_percent: number;
    actual_discount_percent: number;
    general_ticket_price: number;
    sponsor_ticket_price: number;
  };
  lanes: Record<string, Lane>;
  evidence: string[];
};

export type AgentActivity = {
  kind: "queued" | "analyzing" | "completed";
  message: string;
  created_at: string;
};

export type AgentRun = {
  specialist: "event_readiness" | "campaign_composer" | "audience_policy";
  provider: "deterministic_hermes";
  status: "queued" | "running" | "completed" | "failed";
  summary: string;
  revision: number;
  activity: AgentActivity[];
};

export type DemoState = {
  deployment_mode?: "server" | "browser_local";
  actors: Actor[];
  event: {
    id: string;
    title: string;
    revision: {
      id: number;
      version: number;
      facts: Record<string, string | number>;
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
  agent_capacity?: {
    active: number;
    limit: number;
  };
  agent_runs?: AgentRun[];
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
