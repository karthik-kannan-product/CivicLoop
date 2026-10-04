import { afterEach, expect, test, vi } from "vitest";
import { cancelHermesRun, getHermesRun, getPendingOperations, startHermesRun } from "./api";

const runId = "11111111-1111-4111-8111-111111111111";
const receipt = { schema_version: "1.0", run_id: runId, status: "queued" };
const status = { ...receipt, failure_category: null, cancel_requested: false,
  proposal_count: 0, pending_operation_count: 0 };
const operation = { operation_id: runId, provider: "eventbrite",
  operation_kind: "create_eventbrite_draft", status: "pending", action_digest: "a".repeat(64) };
function response(value: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => value });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
afterEach(() => vi.unstubAllGlobals());

test("start binds the revision and idempotency key, passes CSRF and supports cancellation", async () => {
  document.cookie = "csrftoken=synthetic-csrf";
  const fetchMock = response(receipt);
  const controller = new AbortController();
  expect(await startHermesRun(runId, 3, runId, controller.signal)).toEqual(receipt);
  expect(fetchMock).toHaveBeenCalledWith(`/api/v1/workflows/${runId}/hermes-runs`, expect.objectContaining({
    method: "POST", credentials: "same-origin", signal: controller.signal,
    body: JSON.stringify({ revision_id: 3 }),
    headers: expect.objectContaining({ "Idempotency-Key": runId, "X-CSRFToken": "synthetic-csrf" }),
  }));
});

test("status and cancel reject responses belonging to another run", async () => {
  response({ ...status, run_id: "22222222-2222-4222-8222-222222222222" });
  await expect(getHermesRun(runId)).rejects.toThrow("could not read");
  await expect(cancelHermesRun(runId)).rejects.toThrow("could not read");
});

test.each([
  { ...status, prompt: "private" }, { ...status, proposal_count: 21 },
  { ...status, status: "executed" }, { ...status, failure_category: "raw-error" },
  { ...status, status: ["succeeded"] },
])("status rejects fields outside the bounded contract", async (value) => {
  response(value);
  await expect(getHermesRun(runId)).rejects.toThrow("could not read");
});

test.each([
  [{ ...operation, receipt: {} }], [{ ...operation, status: "executed" }],
  [{ ...operation, provider: "iterable" }], [operation, operation],
  [{ ...operation, provider: "iterable", operation_kind: ["create_iterable_email_draft"] }],
  Array.from({ length: 21 }, () => operation),
].map((results) => ({ results })))("pending operations reject execution data, inconsistent provider kinds and duplicate IDs", async ({ results }) => {
  response({ schema_version: "1.0", results });
  await expect(getPendingOperations(runId)).rejects.toThrow("could not read");
});

test("accepts a closed inert pending page", async () => {
  response({ schema_version: "1.0", results: [operation] });
  expect(await getPendingOperations(runId)).toEqual([operation]);
});
