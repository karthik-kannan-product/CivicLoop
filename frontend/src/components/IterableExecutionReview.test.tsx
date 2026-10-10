import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { approveIterable, executeIterable, prepareIterable, reconcileIterable, submitCampaign, submitTemplate } from "../iterableDraftApi";
import type { CampaignExecution, IterableExecution, IterablePreparation, TemplateExecution } from "../iterableDraftApi";
import { IterableExecutionReview } from "./IterableExecutionReview";

vi.mock("../iterableDraftApi", () => ({ approveIterable: vi.fn(), executeIterable: vi.fn(), prepareIterable: vi.fn(), reconcileIterable: vi.fn(), submitCampaign: vi.fn(), submitTemplate: vi.fn() }));
const template: TemplateExecution = { operation_id: "template", intent_id: "intent", run_id: "run", revision_id: 7, review_digest: "a".repeat(64), request_digest: "b".repeat(64), status: "pending", provider_id: null, receipt: null, step: "template", provider_configuration: { region: "eu" },
  payload: { fromEmail: "sender@example.test", fromName: "Owner sender", replyToEmail: "reply@example.test", messageTypeId: 7, clientTemplateId: "civicloop-intent", name: "Hermes invitation", subject: "Join us", plainText: "<script>unsafe</script> invitation", html: "escaped" } };
const campaign: CampaignExecution = { ...template, operation_id: "campaign", step: "campaign", provider_configuration: { region: "eu", expected_template_digest: "c".repeat(64) }, payload: { name: "Invitation", templateId: 44, listIds: [9], suppressionListIds: [10], scheduleSend: false } };
const review: IterablePreparation = { revision_id: 7, kind: "invitation", subject: "Join us", body: template.payload.plainText, template_execution: template, campaign_execution: null };
async function flush() { await act(async () => { await Promise.resolve(); }); }
beforeEach(() => { vi.resetAllMocks(); vi.mocked(prepareIterable).mockResolvedValue(review); });
afterEach(cleanup);

test("separate exact approval, template creation and campaign request using chosen lists", async () => {
  vi.mocked(approveIterable).mockResolvedValue({ ...template, status: "approved" });
  vi.mocked(executeIterable).mockResolvedValue({ ...template, status: "succeeded", provider_id: "44", receipt: { outcome: "CONFIRMED", provider_status: "template", readback_digest: "c".repeat(64) } });
  vi.mocked(submitCampaign).mockResolvedValue(campaign);
  render(<IterableExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  expect(screen.getByText(template.payload.plainText)).toBeInTheDocument();
  expect(document.querySelector("script")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact template request" })); await flush();
  expect(vi.mocked(approveIterable).mock.calls[0][0].review_digest).toBe(template.review_digest);
  expect(executeIterable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Create reviewed Iterable template" })); await flush();
  expect(screen.getByText(/Campaign creation is a separate reviewed step/)).toBeInTheDocument();
  expect(screen.queryByText(/Actual Iterable campaign confirmed/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Campaign name"), { target: { value: "Invitation" } });
  fireEvent.change(screen.getByLabelText("Audience list IDs (comma separated)"), { target: { value: "9" } });
  fireEvent.change(screen.getByLabelText("Suppression list IDs (comma separated)"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare exact unscheduled campaign request" })); await flush();
  expect(vi.mocked(submitCampaign).mock.calls[0].slice(0, 3)).toEqual(["run", "intent", { name: "Invitation", listIds: [9], suppressionListIds: [10] }]);
  expect(screen.getByText("false — unscheduled and unactivated")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Approve this exact campaign request" })).toBeInTheDocument();
  expect(executeIterable).toHaveBeenCalledTimes(1);
});

test("actual campaign needs its own approval and unknown creation cannot repeat", async () => {
  vi.mocked(prepareIterable).mockResolvedValue({ ...review, template_execution: { ...template, status: "succeeded" }, campaign_execution: campaign });
  vi.mocked(approveIterable).mockResolvedValue({ ...campaign, status: "approved" });
  vi.mocked(executeIterable).mockResolvedValue({ ...campaign, status: "unknown", provider_id: "55" });
  render(<IterableExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact campaign request" })); await flush();
  expect(executeIterable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Create unscheduled Iterable campaign" })); await flush();
  expect(executeIterable).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Create unscheduled Iterable campaign" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Check recorded campaign with a read only lookup" })).toBeInTheDocument();
  expect(reconcileIterable).not.toHaveBeenCalled();
});

test("uncertain approval is locked until recorded status refresh", async () => {
  vi.mocked(approveIterable).mockRejectedValue(new Error("private provider response"));
  render(<IterableExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact template request" })); await flush();
  expect(screen.getByRole("button", { name: "Approve this exact template request" })).toBeDisabled();
  expect(screen.queryByText(/private provider/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh recorded Iterable status" })); await flush();
  expect(screen.getByRole("button", { name: "Approve this exact template request" })).toBeEnabled();
});

test("late approval cannot alter a different run and revision", async () => {
  let resolve!: (value: IterableExecution) => void;
  vi.mocked(approveIterable).mockReturnValue(new Promise((done) => { resolve = done; }));
  const view = render(<IterableExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  fireEvent.click(screen.getByRole("button", { name: "Approve this exact template request" }));
  vi.mocked(prepareIterable).mockResolvedValue({ ...review, revision_id: 8, template_execution: { ...template, run_id: "new-run", revision_id: 8, intent_id: "new-intent" } });
  view.rerender(<IterableExecutionReview runId="new-run" intentId="new-intent" revisionId={8} />); await flush();
  await act(async () => { resolve({ ...template, status: "approved" }); });
  expect(screen.getByRole("button", { name: "Approve this exact template request" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Create reviewed Iterable template" })).not.toBeInTheDocument();
});

test("owner specified sender and region are the only submitted content configuration", async () => {
  vi.mocked(prepareIterable).mockResolvedValue({ ...review, template_execution: null });
  vi.mocked(submitTemplate).mockResolvedValue(template);
  render(<IterableExecutionReview runId="run" intentId="intent" revisionId={7} />); await flush();
  for (const [label, value] of [["Sender display name", "Owner sender"], ["Sender email", "sender@example.test"], ["Reply to email", "reply@example.test"], ["Account message type ID", "7"], ["Account region", "eu"]]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  fireEvent.click(screen.getByRole("button", { name: "Prepare exact template request" })); await flush();
  expect(vi.mocked(submitTemplate).mock.calls[0].slice(0, 4)).toEqual(["run", "intent", { fromEmail: "sender@example.test", fromName: "Owner sender", replyToEmail: "reply@example.test", messageTypeId: 7 }, "eu"]);
});
