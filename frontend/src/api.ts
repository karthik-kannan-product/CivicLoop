import type { DemoState, HermesStartReceipt, HermesRunStatus, PendingOperation } from "./types";
import { requestStaticDemo } from "./staticDemo";

type RequestOptions = {
  actor?: string;
  body?: Record<string, string>;
  method?: "GET" | "POST";
};

export type SessionUser = {
  username: string;
  display_name: string;
  role: "operator" | "approver";
  administrator?: boolean;
  hermes_enabled?: boolean;
};

function csrfToken(): string {
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith("csrftoken="))
    ?.split("=")[1] ?? "";
}

export async function requestJson<T>(
  path: string,
  options: {
    body?: Record<string, unknown>;
    method?: "GET" | "POST";
    headers?: Record<string, string>;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    credentials: "same-origin",
    signal: options.signal,
    headers: {
      "Content-Type": "application/json",
      ...(options.method === "POST" && csrfToken() ? { "X-CSRFToken": csrfToken() } : {}),
      ...options.headers,
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = (await response.json()) as T & { message?: string };
  if (!response.ok) {
    throw Object.assign(new Error(payload.message ?? "CivicLoop could not complete that action."), {
      status: response.status,
    });
  }
  return payload;
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const failures = new Set([null, "budget_exhausted", "cancelled", "dependency_unavailable",
  "invalid_output", "provider_unavailable", "timeout"]);

function objectWithKeys(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function invalidHermesResponse(): never {
  throw new Error("CivicLoop could not read the Hermes response.");
}

function runStatus(value: unknown, runId: string): HermesRunStatus {
  if (!objectWithKeys(value, ["schema_version", "run_id", "status", "failure_category",
    "cancel_requested", "proposal_count", "pending_operation_count"])
    || value.schema_version !== "1.0" || value.run_id !== runId
    || typeof value.status !== "string"
    || !["queued", "running", "succeeded", "failed", "cancelled"].includes(value.status)
    || !failures.has(value.failure_category as string | null)
    || typeof value.cancel_requested !== "boolean"
    || ![value.proposal_count, value.pending_operation_count].every((count) =>
      typeof count === "number" && Number.isInteger(count) && count >= 0 && count <= 20)) {
    invalidHermesResponse();
  }
  return value as unknown as HermesRunStatus;
}

export async function startHermesRun(
  workflowId: string, revisionId: number, idempotencyKey: string, signal?: AbortSignal,
): Promise<HermesStartReceipt> {
  const value = await requestJson<unknown>(`/api/v1/workflows/${encodeURIComponent(workflowId)}/hermes-runs`, {
    method: "POST", body: { revision_id: revisionId }, headers: { "Idempotency-Key": idempotencyKey }, signal,
  });
  if (!objectWithKeys(value, ["schema_version", "run_id", "status"])
    || value.schema_version !== "1.0" || value.status !== "queued"
    || typeof value.run_id !== "string" || !uuidPattern.test(value.run_id)) invalidHermesResponse();
  return value as unknown as HermesStartReceipt;
}

export async function getHermesRun(runId: string, signal?: AbortSignal): Promise<HermesRunStatus> {
  return runStatus(await requestJson<unknown>(`/api/v1/agent-runs/${encodeURIComponent(runId)}`, { signal }), runId);
}

export async function cancelHermesRun(runId: string, signal?: AbortSignal): Promise<HermesRunStatus> {
  return runStatus(await requestJson<unknown>(`/api/v1/agent-runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST", signal,
  }), runId);
}

export async function getPendingOperations(runId: string, signal?: AbortSignal): Promise<PendingOperation[]> {
  const value = await requestJson<unknown>(`/api/v1/agent-runs/${encodeURIComponent(runId)}/pending-operations`, { signal });
  if (!objectWithKeys(value, ["schema_version", "results"]) || value.schema_version !== "1.0"
    || !Array.isArray(value.results) || value.results.length > 20) invalidHermesResponse();
  const ids = new Set<string>();
  for (const item of value.results) {
    if (!objectWithKeys(item, ["operation_id", "provider", "operation_kind", "status", "action_digest"])
      || typeof item.operation_id !== "string" || !uuidPattern.test(item.operation_id)
      || ids.has(item.operation_id) || item.status !== "pending"
      || typeof item.action_digest !== "string" || !/^[a-f0-9]{64}$/.test(item.action_digest)
      || typeof item.operation_kind !== "string"
      || !((item.provider === "eventbrite" && item.operation_kind === "create_eventbrite_draft")
        || (item.provider === "iterable" && ["create_iterable_email_draft", "create_iterable_reminder_draft"].includes(item.operation_kind)))) invalidHermesResponse();
    ids.add(item.operation_id);
  }
  return value.results as PendingOperation[];
}

export async function requestDemo(path: string, options: RequestOptions = {}): Promise<DemoState> {
  if (import.meta.env.VITE_STATIC_DEMO === "true") {
    return requestStaticDemo(path, options);
  }
  return requestJson<DemoState>(path, options);
}

export async function requestSession(): Promise<SessionUser> {
  const payload = await requestJson<{ user: SessionUser }>("/api/v1/auth/session");
  return payload.user;
}

export async function loginDemo(username: string, password: string): Promise<SessionUser> {
  const payload = await requestJson<{ user: SessionUser }>("/api/v1/auth/login", {
    method: "POST",
    body: { username, password },
  });
  return payload.user;
}

export async function logoutDemo(): Promise<void> {
  await requestJson<{ logged_out: boolean }>("/api/v1/auth/logout", { method: "POST" });
}

export type EventbriteEvent = {
  id: string;
  provider_event_id: string;
  title: string;
  status: string;
  start_at: string | null;
  timezone: string;
  available: boolean;
  selectable: boolean;
};

export async function listEventbriteEvents(): Promise<EventbriteEvent[]> {
  return (await requestJson<{ events: EventbriteEvent[] }>("/api/v1/eventbrite/events")).events;
}

export type EventbritePage = {
  events: EventbriteEvent[];
  next_cursor: string | null;
  has_more: boolean;
  complete: boolean;
};

export async function refreshEventbriteEvents(cursor?: string): Promise<EventbritePage> {
  const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return requestJson<EventbritePage>(`/api/v1/eventbrite/events/refresh${query}`, {
    method: "POST",
  });
}

export async function selectEventbriteEvent(id: string): Promise<DemoState> {
  return requestJson<DemoState>(`/api/v1/eventbrite/events/${id}/select`, { method: "POST" });
}

export async function startManualEvent(body: Record<string, string>): Promise<DemoState> {
  return requestJson<DemoState>("/api/v1/events/manual", { method: "POST", body });
}
