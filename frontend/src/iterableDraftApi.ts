import { requestJson } from "./api";

type Status = "pending" | "approved" | "executing" | "unknown" | "succeeded" | "failed";
type Base = { operation_id: string; intent_id: string; run_id: string; revision_id: number; review_digest: string; request_digest: string; status: Status;
  provider_id: string | null; receipt: { outcome: "CONFIRMED" | "UNKNOWN"; provider_status: string | null; readback_digest: string | null } | null };
export type Sender = { fromEmail: string; fromName: string; replyToEmail: string; messageTypeId: number };
export type TemplateExecution = Base & { step: "template"; provider_configuration: { region: "us" | "eu" }; payload: Sender & { clientTemplateId: string; name: string; subject: string; plainText: string; html: string } };
export type CampaignExecution = Base & { step: "campaign"; provider_configuration: { region: "us" | "eu"; expected_template_digest: string }; payload: { name: string; templateId: number; listIds: number[]; suppressionListIds: number[]; scheduleSend: false } };
export type IterableExecution = TemplateExecution | CampaignExecution;
export type IterablePreparation = { revision_id: number; kind: "invitation" | "reminder"; subject: string; body: string; template_execution: TemplateExecution | null; campaign_execution: CampaignExecution | null };
const intentPath = (run: string, intent: string) => `/api/v1/agent-runs/${encodeURIComponent(run)}/pending-operations/${encodeURIComponent(intent)}`;
const path = (value: IterableExecution) => `/api/v1/iterable-${value.step}-executions/${encodeURIComponent(value.operation_id)}`;
export const prepareIterable = (run: string, intent: string, signal?: AbortSignal) => requestJson<IterablePreparation>(`${intentPath(run, intent)}/iterable-review`, { signal });
export const submitTemplate = (run: string, intent: string, sender: Sender, region: "us" | "eu", signal?: AbortSignal) => requestJson<TemplateExecution>(`${intentPath(run, intent)}/iterable-review`, { method: "POST", body: { sender, region }, signal });
export const submitCampaign = (run: string, intent: string, body: { name: string; listIds: number[]; suppressionListIds: number[] }, signal?: AbortSignal) => requestJson<CampaignExecution>(`${intentPath(run, intent)}/iterable-campaign-review`, { method: "POST", body, signal });
export const approveIterable = (value: IterableExecution, signal?: AbortSignal) => requestJson<IterableExecution>(`${path(value)}/approve`, { method: "POST", body: { review_digest: value.review_digest, revision_id: value.revision_id }, signal });
export const executeIterable = (value: IterableExecution, signal?: AbortSignal) => requestJson<IterableExecution>(`${path(value)}/execute`, { method: "POST", body: {}, signal });
export const reconcileIterable = (value: IterableExecution, signal?: AbortSignal) => requestJson<IterableExecution>(`${path(value)}/reconcile`, { method: "POST", body: {}, signal });
