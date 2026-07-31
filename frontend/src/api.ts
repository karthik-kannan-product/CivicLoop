import type { DemoState } from "./types";
import { requestStaticDemo } from "./staticDemo";

type RequestOptions = {
  actor?: string;
  body?: Record<string, string>;
  method?: "GET" | "POST";
};

export async function requestDemo(
  path: string,
  options: RequestOptions = {},
): Promise<DemoState> {
  if (import.meta.env.VITE_STATIC_DEMO === "true") {
    return requestStaticDemo(path, options);
  }
  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      "X-Demo-Actor": options.actor ?? "maya",
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message ?? "CivicLoop could not complete that action.");
  }
  return payload as DemoState;
}
