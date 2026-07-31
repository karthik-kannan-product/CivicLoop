import type { DemoState } from "../types";

export function CompletionPanel({ state }: { state: DemoState }) {
  if (!state.execution) return null;
  const receipt = state.execution.receipt;
  return (
    <section className="completion" aria-labelledby="completion-title">
      <div className="completion__icon" aria-hidden="true">
        ✓
      </div>
      <div>
        <p className="eyebrow">Approved sandbox receipt</p>
        <h2 id="completion-title">Sandbox delivery recorded</h2>
        <p>{receipt.message}</p>
        <ul className="receipt-facts">
          <li>{receipt.audience_count} people in approved audience</li>
          <li>No external messages sent</li>
          <li>Receipt {state.execution.id.slice(0, 8)}</li>
        </ul>
      </div>
    </section>
  );
}

