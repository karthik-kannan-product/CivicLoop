import { useId } from "react";
import type { PendingOperation } from "../types";

const operationLabels: Record<PendingOperation["operation_kind"], string> = {
  create_eventbrite_draft: "Pending Eventbrite draft",
  create_iterable_email_draft: "Pending Iterable email draft",
  create_iterable_reminder_draft: "Pending Iterable reminder draft",
};

export function PendingOperationsPanel({ operations }: { operations: PendingOperation[] }) {
  const titleId = useId();
  return (
    <section className="review-package" aria-labelledby={titleId}>
      <h3 id={titleId}>Pending draft operations</h3>
      <p>These are reviewable intents. Nothing has been created at Eventbrite or Iterable.</p>
      {operations.length === 0 ? <p>No pending draft operations.</p> : (
        <div className="asset-stack">
          {operations.map((operation) => (
            <article className="asset" key={operation.operation_id}>
              <h4>{operationLabels[operation.operation_kind]}</h4>
              <p>Status: pending</p>
              <p>Digest <code>{operation.action_digest.slice(0, 12)}</code></p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
