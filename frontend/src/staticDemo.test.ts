import { beforeEach, expect, test, vi } from "vitest";

import { requestStaticDemo } from "./staticDemo";

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    removeItem: (key: string) => values.delete(key),
    setItem: (key: string, value: string) => values.set(key, value),
  });
});

test("persists the complete static demo journey across reads", async () => {
  const initial = await requestStaticDemo("/api/v1/demo/reset", {
    actor: "maya",
    method: "POST",
  });
  const workflowId = initial.workflow.id;

  const blocked = await requestStaticDemo(`/api/v1/workflows/${workflowId}/runs`, {
    actor: "maya",
    method: "POST",
  });
  expect(blocked.workflow.status).toBe("needs_input");

  const revised = await requestStaticDemo(`/api/v1/workflows/${workflowId}/answers`, {
    actor: "maya",
    method: "POST",
    body: {
      venue_name: "Hudson Civic Center",
      venue_address: "455 West 34th Street, New York, NY 10001",
      access_instructions: "Use the 10th Avenue entrance.",
    },
  });
  expect(revised.event.revision.version).toBe(2);

  const ready = await requestStaticDemo(`/api/v1/workflows/${workflowId}/runs`, {
    actor: "maya",
    method: "POST",
  });
  const submitted = await requestStaticDemo(
    `/api/v1/workflows/${workflowId}/submit`,
    { actor: "maya", method: "POST" },
  );

  await expect(
    requestStaticDemo(`/api/v1/approvals/${submitted.approval?.id}/decision`, {
      actor: "maya",
      method: "POST",
      body: {
        decision: "approve",
        package_hash: ready.workflow.package_hash ?? "",
      },
    }),
  ).rejects.toThrow("submitter cannot approve");

  const completed = await requestStaticDemo(
    `/api/v1/approvals/${submitted.approval?.id}/decision`,
    {
      actor: "jordan",
      method: "POST",
      body: {
        decision: "approve",
        package_hash: ready.workflow.package_hash ?? "",
      },
    },
  );
  expect(completed.workflow.status).toBe("completed");
  expect(completed.execution?.receipt.audience_count).toBe(418);

  const reloaded = await requestStaticDemo("/api/v1/demo");
  expect(reloaded.execution?.id).toBe(completed.execution?.id);
});
