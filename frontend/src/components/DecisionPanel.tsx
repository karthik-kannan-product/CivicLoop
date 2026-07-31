import type { DemoState } from "../types";

type Props = {
  state: DemoState;
  actor: string;
  busy: boolean;
  onSubmit: () => void;
  onApprove: () => void;
};

export function DecisionPanel({ state, actor, busy, onSubmit, onApprove }: Props) {
  const { workflow, approval, actors } = state;
  const activeActor = actors.find((item) => item.slug === actor);

  if (workflow.status === "ready_for_review" && activeActor?.role === "operator") {
    return (
      <section className="decision decision--ready" aria-labelledby="decision-title">
        <div>
          <p className="eyebrow">Operator handoff</p>
          <h2 id="decision-title">Ready for review</h2>
          <p>The exact package will be hashed and locked for a separate approver.</p>
        </div>
        <button className="button button--primary" disabled={busy} onClick={onSubmit}>
          Submit for approval
        </button>
      </section>
    );
  }

  if (workflow.status === "in_review") {
    const isApprover = activeActor?.role === "approver";
    return (
      <section className="decision" aria-labelledby="decision-title">
        <div>
          <p className="eyebrow">Four-eyes approval</p>
          <h2 id="decision-title">
            {isApprover ? "Review the locked package" : "Waiting for Jordan Brooks"}
          </h2>
          <p className="hash">
            Package <code>{approval?.package_hash.slice(0, 12)}…</code>
          </p>
        </div>
        {isApprover && (
          <button className="button button--approve" disabled={busy} onClick={onApprove}>
            Approve exact package
          </button>
        )}
      </section>
    );
  }
  return null;
}

