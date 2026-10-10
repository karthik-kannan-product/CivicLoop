import { requestJson } from "./api";

export type DraftExecution = {
  operation_id: string; intent_id: string; run_id: string; revision_id: number; review_digest: string;
  request_digest: string; status: "pending" | "approved" | "executing" | "unknown" | "succeeded" | "failed";
  organization_id: string; provider_id: string | null;
  payload: { event: { name: { html: string }; summary: string; start: { utc: string; timezone: string };
    end: { utc: string; timezone: string }; currency: string; listed: false; shareable: false } };
  receipt: { provider_status: string | null; readback_digest?: string | null } | null;
};
export type DraftPreparation = { execution: DraftExecution | null; intent_id?: string; revision_id?: number; title?: string;
  timezone?: string; date?: string; start_time?: string; end_time?: string; proposal_copy?: string; summary?: string };
export type DraftFields = { title: string; summary: string; organization_id: string;
  start_local: string; end_local: string; timezone: string; currency: string };
const intentPath = (run: string, intent: string) => `/api/v1/agent-runs/${encodeURIComponent(run)}/pending-operations/${encodeURIComponent(intent)}/draft-review`;
const executionPath = (id: string) => `/api/v1/draft-executions/${encodeURIComponent(id)}`;
export const prepareDraft = (run: string, intent: string, signal?: AbortSignal) => requestJson<DraftPreparation>(intentPath(run, intent), { signal });
export const submitDraft = (run: string, intent: string, body: DraftFields, signal?: AbortSignal) => requestJson<DraftExecution>(intentPath(run, intent), { method: "POST", body, signal });
export const readDraft = (id: string, signal?: AbortSignal) => requestJson<DraftExecution>(executionPath(id), { signal });
export const approveDraft = (draft: DraftExecution, signal?: AbortSignal) => requestJson<DraftExecution>(`${executionPath(draft.operation_id)}/approve`, {
  method: "POST", body: { review_digest: draft.review_digest, revision_id: draft.revision_id }, signal,
});
export const executeDraft = (id: string, signal?: AbortSignal) => requestJson<DraftExecution>(`${executionPath(id)}/execute`, { method: "POST", body: {}, signal });
export const reconcileDraft = (id: string, signal?: AbortSignal) => requestJson<DraftExecution>(`${executionPath(id)}/reconcile`, { method: "POST", body: {}, signal });
