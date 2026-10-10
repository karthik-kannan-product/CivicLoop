import { useId } from "react";
import type { PendingOperation } from "../types";
import { DraftExecutionReview } from "./DraftExecutionReview";
import { IterableExecutionReview } from "./IterableExecutionReview";

const operationLabels: Record<PendingOperation["operation_kind"], string> = {
  create_eventbrite_draft: "Pending Eventbrite draft",
  create_iterable_email_draft: "Pending Iterable email draft",
  create_iterable_reminder_draft: "Pending Iterable reminder draft",
};

export function PendingOperationsPanel({ operations, runId, revisionId, ownerReview = false }: { operations: PendingOperation[]; runId?: string; revisionId?: number; ownerReview?: boolean }) {
  const titleId = useId();
  return (
    <section className="review-package" aria-labelledby={titleId}>
      <h3 id={titleId}>Pending draft operations</h3>
      <p>These are reviewable intents. Provider execution and receipts are shown separately.</p>
      {operations.length === 0 ? <p>No pending draft operations.</p> : (
        <div className="asset-stack">
          {operations.map((operation) => (
            <article className="asset" key={operation.operation_id}>
              <h4>{operationLabels[operation.operation_kind]}</h4>
              <p>Status: pending</p>
              <p>Digest <code>{operation.action_digest.slice(0, 12)}</code></p>
              {ownerReview && runId && revisionId && operation.operation_kind === "create_eventbrite_draft" && <DraftExecutionReview key={`${runId}:${revisionId}:${operation.operation_id}`} runId={runId} revisionId={revisionId} intentId={operation.operation_id} />}
              {ownerReview && runId && revisionId && operation.provider === "iterable" && <IterableExecutionReview key={`${runId}:${revisionId}:${operation.operation_id}`} runId={runId} revisionId={revisionId} intentId={operation.operation_id} />}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
