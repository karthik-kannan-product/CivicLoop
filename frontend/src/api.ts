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
};

function csrfToken(): string {
  return document.cookie
    .split("; ")
    .find((item) => item.startsWith("csrftoken="))
    ?.split("=")[1] ?? "";
}

async function requestJson<T>(
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