import type { DemoState } from "./types";
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
};

function csrfToken(): string {
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith("csrftoken="))
    ?.split("=")[1] ?? "";
}

export async function requestJson<T>(
  path: string,
  options: { body?: Record<string, string>; method?: "GET" | "POST" } = {},
): Promise<T> {
  const response = await fetch(path, {
    method: options.method ?? "GET",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.method === "POST" && csrfToken() ? { "X-CSRFToken": csrfToken() } : {}),
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

export async function refreshEventbriteEvents(): Promise<EventbriteEvent[]> {
  return (await requestJson<{ events: EventbriteEvent[] }>("/api/v1/eventbrite/events/refresh", {
    method: "POST",
  })).events;
}

export async function selectEventbriteEvent(id: string): Promise<DemoState> {
  return requestJson<DemoState>(`/api/v1/eventbrite/events/${id}/select`, { method: "POST" });
}

export async function startManualEvent(body: Record<string, string>): Promise<DemoState> {
  return requestJson<DemoState>("/api/v1/events/manual", { method: "POST", body });
}
