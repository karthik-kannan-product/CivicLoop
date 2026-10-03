import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cancelHermesRun, getHermesRun, getPendingOperations, startHermesRun } from "../api";
import { HermesRunPanel } from "./HermesRunPanel";

vi.mock("../api", () => ({
  startHermesRun: vi.fn(), getHermesRun: vi.fn(),
  cancelHermesRun: vi.fn(), getPendingOperations: vi.fn(),
}));

const props = { workflowId: "workflow-a", revisionId: 7, authorized: true, enabled: true, ready: true };
const runId = "68f24806-9e6d-486d-ac19-8a2d7e00463e";
const status = { schema_version: "1.0" as const, run_id: runId, status: "running" as const,
  failure_category: null, cancel_requested: false, proposal_count: 0, pending_operation_count: 0 };
const operation = { operation_id: "4d26a5f8-9532-469f-8b9c-dd003bdfb94b",
  provider: "eventbrite" as const, operation_kind: "create_eventbrite_draft" as const,
  status: "pending" as const, action_digest: "a".repeat(64) };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
async function flush() { await act(async () => { await Promise.resolve(); }); }
async function start() {
  fireEvent.click(screen.getByRole("button", { name: "Generate with Hermes" }));
  await flush();
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  vi.mocked(startHermesRun).mockResolvedValue({ schema_version: "1.0", run_id: runId, status: "queued" });
  vi.mocked(getHermesRun).mockResolvedValue(status);
  vi.mocked(getPendingOperations).mockResolvedValue([]);
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

test("shows controls only to the owner and disables unavailable or incomplete generation", () => {
  const view = render(<HermesRunPanel {...props} authorized={false} />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  view.rerender(<HermesRunPanel {...props} enabled={false} />);
  expect(screen.getByRole("button", { name: "Generate with Hermes" })).toBeDisabled();
  expect(screen.getByText("Hermes is currently disabled.")).toBeInTheDocument();
  view.rerender(<HermesRunPanel {...props} ready={false} />);
  expect(screen.getByRole("button", { name: "Generate with Hermes" })).toBeDisabled();
  expect(startHermesRun).not.toHaveBeenCalled();
});

test("loads final pending intents before terminal status aborts the poll", async () => {
  vi.mocked(getHermesRun).mockResolvedValue({ ...status, status: "succeeded", proposal_count: 1, pending_operation_count: 1 });
  const pending = deferred<typeof operation[]>();
  vi.mocked(getPendingOperations).mockReturnValue(pending.promise);
  render(<HermesRunPanel {...props} />);
  await start();
  const signal = vi.mocked(getPendingOperations).mock.calls[0][1];
  expect(signal?.aborted).toBe(false);
  await act(async () => { pending.resolve([operation]); });
  expect(screen.getByRole("heading", { name: "Pending Eventbrite draft" })).toBeInTheDocument();
  expect(screen.getByText("Hermes status: succeeded")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel Hermes run" })).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(getHermesRun).toHaveBeenCalledTimes(1);
});

test("retries an uncertain start with the same idempotency key", async () => {
  vi.mocked(startHermesRun).mockRejectedValueOnce(new Error("secret transport detail"));
  render(<HermesRunPanel {...props} />);
  await start();
  expect(screen.queryByText(/secret transport detail/)).not.toBeInTheDocument();
  await start();
  expect(vi.mocked(startHermesRun).mock.calls[0][2]).toBe(vi.mocked(startHermesRun).mock.calls[1][2]);
  expect(startHermesRun).toHaveBeenCalledTimes(2);
});

test("aborts and ignores an old start receipt when the revision changes", async () => {
  const receipt = deferred<{ schema_version: "1.0"; run_id: string; status: "queued" }>();
  vi.mocked(startHermesRun).mockReturnValueOnce(receipt.promise);
  const view = render(<HermesRunPanel {...props} />);
  await start();
  const oldSignal = vi.mocked(startHermesRun).mock.calls[0][3];
  view.rerender(<HermesRunPanel {...props} revisionId={8} />);
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => { receipt.resolve({ schema_version: "1.0", run_id: runId, status: "queued" }); });
  expect(screen.queryByText(runId)).not.toBeInTheDocument();
  expect(getHermesRun).not.toHaveBeenCalled();
  await start();
  expect(vi.mocked(startHermesRun).mock.calls[1][1]).toBe(8);
  expect(vi.mocked(startHermesRun).mock.calls[1][2]).not.toBe(vi.mocked(startHermesRun).mock.calls[0][2]);
});

test("continues polling after a cancellation request", async () => {
  vi.mocked(cancelHermesRun).mockResolvedValue({ ...status, cancel_requested: true });
  render(<HermesRunPanel {...props} />);
  await start();
  fireEvent.click(screen.getByRole("button", { name: "Cancel Hermes run" }));
  await flush();
  expect(screen.getByRole("button", { name: "Cancellation requested" })).toBeDisabled();
  vi.mocked(getHermesRun).mockResolvedValue({ ...status, status: "cancelled", failure_category: "cancelled", cancel_requested: true });
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(screen.getByText("Hermes status: cancelled")).toBeInTheDocument();
  expect(screen.queryByText(/Waiting for the run/)).not.toBeInTheDocument();
});

test("does not show a waiting message when cancellation is already terminal", async () => {
  vi.mocked(cancelHermesRun).mockResolvedValue({ ...status, status: "cancelled", failure_category: "cancelled", cancel_requested: true });
  render(<HermesRunPanel {...props} />);
  await start();
  fireEvent.click(screen.getByRole("button", { name: "Cancel Hermes run" }));
  await flush();
  expect(screen.getByText("Hermes status: cancelled")).toBeInTheDocument();
  expect(screen.queryByText(/Waiting for the run/)).not.toBeInTheDocument();
});

test("a delayed cancellation receipt cannot replace a completed run with running status", async () => {
  const cancellation = deferred<typeof status>();
  vi.mocked(cancelHermesRun).mockReturnValue(cancellation.promise);
  render(<HermesRunPanel {...props} />);
  await start();
  fireEvent.click(screen.getByRole("button", { name: "Cancel Hermes run" }));
  await flush();
  vi.mocked(getHermesRun).mockResolvedValue({ ...status, status: "succeeded" });
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  await act(async () => { cancellation.resolve({ ...status, cancel_requested: true }); });
  expect(screen.getByText("Hermes status: succeeded")).toBeInTheDocument();
  expect(screen.queryByText(/Waiting for the run/)).not.toBeInTheDocument();
});

test("a cancellation failure keeps status polling active and hides transport details", async () => {
  vi.mocked(cancelHermesRun).mockRejectedValue(new Error("private adapter response"));
  render(<HermesRunPanel {...props} />);
  await start();
  fireEvent.click(screen.getByRole("button", { name: "Cancel Hermes run" }));
  await flush();
  expect(screen.getByText("Cancellation could not be confirmed. Status polling continues.")).toBeInTheDocument();
  expect(screen.queryByText(/private adapter response/)).not.toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  expect(getHermesRun).toHaveBeenCalledTimes(2);
});

test("a cancellation from run A cannot overwrite run B after starting again", async () => {
  const cancellation = deferred<Awaited<ReturnType<typeof cancelHermesRun>>>();
  vi.mocked(cancelHermesRun).mockReturnValue(cancellation.promise);
  render(<HermesRunPanel {...props} />);
  await start();
  fireEvent.click(screen.getByRole("button", { name: "Cancel Hermes run" }));
  await flush();
  const oldSignal = vi.mocked(cancelHermesRun).mock.calls[0][1];
  vi.mocked(getHermesRun).mockResolvedValue({ ...status, status: "succeeded" });
  await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
  const nextRunId = "cb9ca950-ce9e-4b5f-b84a-f41e355a414c";
  vi.mocked(startHermesRun).mockResolvedValue({ schema_version: "1.0", run_id: nextRunId, status: "queued" });
  vi.mocked(getHermesRun).mockResolvedValue({ ...status, run_id: nextRunId });
  await start();
  await act(async () => { cancellation.resolve({ ...status, status: "cancelled", failure_category: "cancelled", pending_operation_count: 1 }); });
  expect(oldSignal?.aborted).toBe(true);
  expect(screen.getByText(nextRunId)).toBeInTheDocument();
  expect(screen.getByText("Hermes status: running")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel Hermes run" })).toBeEnabled();
  expect(getPendingOperations).not.toHaveBeenCalled();
  expect(screen.queryByText("The Hermes run was cancelled.")).not.toBeInTheDocument();
  expect(vi.mocked(startHermesRun).mock.calls[1][2]).not.toBe(vi.mocked(startHermesRun).mock.calls[0][2]);
});

test("aborts a pending-intent request and ignores its result when changing workflows", async () => {
  vi.mocked(getHermesRun).mockResolvedValue({ ...status, status: "succeeded", pending_operation_count: 1 });
  const pending = deferred<typeof operation[]>();
  vi.mocked(getPendingOperations).mockReturnValue(pending.promise);
  const view = render(<HermesRunPanel {...props} />);
  await start();
  const signal = vi.mocked(getPendingOperations).mock.calls[0][1];
  view.rerender(<HermesRunPanel {...props} workflowId="workflow-b" />);
  expect(signal?.aborted).toBe(true);
  await act(async () => { pending.resolve([operation]); });
  expect(screen.queryByRole("heading", { name: "Pending Eventbrite draft" })).not.toBeInTheDocument();
  expect(screen.queryByText(runId)).not.toBeInTheDocument();
});

test("bounds polling and aborts outstanding requests on unmount", async () => {
  const view = render(<HermesRunPanel {...props} />);
  await start();
  await act(async () => { await vi.advanceTimersByTimeAsync(200_000); });
  expect(vi.mocked(getHermesRun).mock.calls.length).toBeLessThanOrEqual(90);
  expect(screen.getByText(/Status polling paused/)).toBeInTheDocument();
  const count = vi.mocked(getHermesRun).mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
  expect(getHermesRun).toHaveBeenCalledTimes(count);
  view.unmount();
  expect(vi.mocked(getHermesRun).mock.calls[0][1]?.aborted).toBe(true);
});
