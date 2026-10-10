import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { approveDraft, executeDraft, prepareDraft, readDraft, reconcileDraft, submitDraft } from "../draftApi";
import type { DraftExecution } from "../draftApi";
import { DraftExecutionReview } from "./DraftExecutionReview";

vi.mock("../draftApi", () => ({ approveDraft: vi.fn(), executeDraft: vi.fn(), prepareDraft: vi.fn(), readDraft: vi.fn(), reconcileDraft: vi.fn(), submitDraft: vi.fn() }));
const draft: DraftExecution = { operation_id: "operation", intent_id: "intent", run_id: "run", revision_id: 7, review_digest: "a".repeat(64), request_digest: "b".repeat(64), status: "pending", organization_id: "123", provider_id: null, receipt: null,
  payload: { event: { name: { html: "Reviewed event" }, summary: "Reviewed summary", start: { utc: "2027-01-01T12:00:00Z", timezone: "UTC" }, end: { utc: "2027-01-01T13:00:00Z", timezone: "UTC" }, currency: "USD", listed: false, shareable: false } } };
async function flush() { await act(async () => { await Promise.resolve(); }); }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(prepareDraft).mockResolvedValue({ execution: draft }); });
afterEach(cleanup);

test("pins exact review and requires separate create action", async () => {
  vi.mocked(approveDraft).mockResolvedValue({ ...draft, status: "approved" });
  vi.mocked(executeDraft).mockResolvedValue({ ...draft, status: "unknown" });
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  expect(executeDraft).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact draft request" })); await flush();
  expect(vi.mocked(approveDraft).mock.calls[0][0].review_digest).toBe(draft.review_digest);
  expect(executeDraft).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Create unpublished Eventbrite draft" })); await flush();
  expect(executeDraft).toHaveBeenCalledTimes(1);
  expect(screen.getByText(/Do not create another draft/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create unpublished Eventbrite draft" })).not.toBeInTheDocument();
});

test("uncertain action locks approval and only status refresh unlocks", async () => {
  vi.mocked(approveDraft).mockRejectedValue(new Error("private response"));
  vi.mocked(readDraft).mockResolvedValue({ ...draft, status: "approved" });
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact draft request" })); await flush();
  expect(screen.getByRole("button", { name: "Approve this exact draft request" })).toBeDisabled();
  expect(screen.queryByText(/private response/)).not.toBeInTheDocument();
  expect(executeDraft).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh recorded status" })); await flush();
  expect(screen.getByRole("button", { name: "Create unpublished Eventbrite draft" })).toBeEnabled();
});

test("late approval cannot change a new run review", async () => {
  let resolve!: (value: DraftExecution) => void;
  vi.mocked(approveDraft).mockReturnValue(new Promise((done) => { resolve = done; }));
  const view = render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact draft request" }));
  vi.mocked(prepareDraft).mockResolvedValue({ execution: { ...draft, run_id: "new-run", intent_id: "new-intent", revision_id: 8 } });
  view.rerender(<DraftExecutionReview runId="new-run" intentId="new-intent" revisionId={8} />); await flush();
  await act(async () => { resolve({ ...draft, status: "approved" }); });
  expect(screen.getByRole("button", { name: "Approve this exact draft request" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Create unpublished Eventbrite draft" })).not.toBeInTheDocument();
});

test("lost execution and failed refresh keep create locked until a validated read succeeds", async () => {
  vi.mocked(prepareDraft).mockResolvedValue({ execution: { ...draft, status: "approved" } });
  vi.mocked(executeDraft).mockRejectedValue(new Error("lost response"));
  vi.mocked(readDraft).mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce({ ...draft, status: "approved", intent_id: "another-intent" })
    .mockResolvedValueOnce({ ...draft, status: "approved", operation_id: "another-operation" })
    .mockResolvedValueOnce({ ...draft, status: "unknown" });
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.click(screen.getByRole("button", { name: "Create unpublished Eventbrite draft" })); await flush();
  for (let i = 0; i < 3; i++) {
    fireEvent.click(screen.getByRole("button", { name: "Refresh recorded status" })); await flush();
    expect(screen.getByRole("button", { name: "Create unpublished Eventbrite draft" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Create unpublished Eventbrite draft" }));
    expect(executeDraft).toHaveBeenCalledTimes(1);
  }
  fireEvent.click(screen.getByRole("button", { name: "Refresh recorded status" })); await flush();
  expect(screen.getByText(/Provider execution: unknown/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Create unpublished Eventbrite draft" })).not.toBeInTheDocument();
});

test.each(["existing", "empty"])("rejects %s preparation for another intent on the same run and revision", async (kind) => {
  vi.mocked(prepareDraft).mockResolvedValue(kind === "existing"
    ? { execution: { ...draft, intent_id: "another-intent" } }
    : { execution: null, intent_id: "another-intent", revision_id: 7 });
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  expect(screen.getByText("The draft review could not be loaded.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Approve this exact draft request" })).not.toBeInTheDocument();
});

test.each(["intent_id", "operation_id"] as const)("rejects mismatched %s from every existing-operation action", async (key) => {
  const actions = [
    { status: "pending", label: "Approve this exact draft request", mock: approveDraft },
    { status: "approved", label: "Create unpublished Eventbrite draft", mock: executeDraft },
    { status: "unknown", label: "Check the recorded Eventbrite event", mock: reconcileDraft },
    { status: "approved", label: "Refresh recorded status", mock: readDraft },
  ] as const;
  for (const action of actions) {
    const original = { ...draft, status: action.status, provider_id: "456" };
    vi.mocked(prepareDraft).mockResolvedValue({ execution: original });
    vi.mocked(action.mock).mockResolvedValue({ ...original, [key]: "another", status: "succeeded" });
    const view = render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
    fireEvent.click(screen.getByRole("button", { name: action.label })); await flush();
    expect(screen.getByText(/The result could not be confirmed/)).toBeInTheDocument();
    expect(screen.getByText(`Provider execution: ${original.status}`)).toBeInTheDocument();
    expect(screen.queryByText(/Eventbrite confirmed the unpublished draft/)).not.toBeInTheDocument();
    view.unmount();
  }
});

test.each([true, false])("submission may establish an operation only for the requested intent: %s", async (matches) => {
  vi.mocked(prepareDraft).mockResolvedValue({ execution: null, intent_id: "intent", revision_id: 7 });
  vi.mocked(submitDraft).mockResolvedValue({ ...draft, operation_id: "new-operation", intent_id: matches ? "intent" : "another-intent" });
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.submit(screen.getByRole("button", { name: "Prepare exact draft request" }).closest("form")!); await flush();
  if (matches) expect(screen.getByRole("button", { name: "Approve this exact draft request" })).toBeEnabled();
  else {
    expect(screen.getByText(/The result could not be confirmed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare exact draft request" })).toBeDisabled();
  }
});

test("confirmed empty refresh unlocks a rejected submission while preserving editable fields", async () => {
  vi.mocked(prepareDraft).mockResolvedValue({ execution: null, intent_id: "intent", revision_id: 7 });
  vi.mocked(submitDraft).mockRejectedValueOnce(new Error("validation response"))
    .mockResolvedValueOnce({ ...draft, operation_id: "corrected-operation" });
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  const values: Record<string, string> = {
    "Event name": "Community event",
    "Summary (up to 140 characters)": "A useful community gathering",
    "Eventbrite organization ID": "123",
    "Start date and time in the event timezone": "2027-01-01T12:00",
    "End date and time in the event timezone": "2027-01-01T13:00",
    "Event timezone (for example America/New_York)": "UTC",
    "Currency (three uppercase letters)": "usd",
  };
  for (const [label, value] of Object.entries(values)) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  fireEvent.submit(screen.getByRole("button", { name: "Prepare exact draft request" }).closest("form")!); await flush();
  expect(screen.getByRole("button", { name: "Prepare exact draft request" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Refresh recorded status" })); await flush();
  expect(screen.getByRole("button", { name: "Prepare exact draft request" })).toBeEnabled();
  expect(screen.getByLabelText("Currency (three uppercase letters)")).toHaveValue("usd");
  fireEvent.change(screen.getByLabelText("Currency (three uppercase letters)"), { target: { value: "USD" } });
  fireEvent.submit(screen.getByRole("button", { name: "Prepare exact draft request" }).closest("form")!); await flush();
  expect(screen.getByRole("button", { name: "Approve this exact draft request" })).toBeEnabled();
  expect(submitDraft).toHaveBeenCalledTimes(2);
});

test("lost submission response recovers an existing execution without preparation-level identity fields", async () => {
  vi.mocked(prepareDraft).mockResolvedValue({ execution: null, intent_id: "intent", revision_id: 7 });
  vi.mocked(submitDraft).mockRejectedValue(new Error("lost response"));
  render(<DraftExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.submit(screen.getByRole("button", { name: "Prepare exact draft request" }).closest("form")!); await flush();
  expect(screen.getByRole("button", { name: "Prepare exact draft request" })).toBeDisabled();

  vi.mocked(prepareDraft).mockResolvedValue({ execution: { ...draft, status: "pending" } });
  fireEvent.click(screen.getByRole("button", { name: "Refresh recorded status" })); await flush();

  expect(screen.getByText("Provider execution: pending")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve this exact draft request" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Prepare exact draft request" })).not.toBeInTheDocument();
});
