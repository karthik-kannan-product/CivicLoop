import type { CampaignPackage, DemoState } from "../types";

type ReviewPackageProps = {
  campaignPackage: CampaignPackage;
  evaluation?: DemoState["evaluation"];
  canEvaluate?: boolean;
  busy?: boolean;
  onEvaluate?: () => void;
};

const stateLabels = {
  pending: "Evaluation pending",
  passed: "Evaluation passed",
  failed: "Evaluation failed",
  unavailable: "Evaluation unavailable",
  denied: "Evaluation denied by budget policy",
};

export function ReviewPackage({
  campaignPackage,
  evaluation,
  canEvaluate = false,
  busy = false,
  onEvaluate,
}: ReviewPackageProps) {
  return (
    <section className="review-package" aria-labelledby="package-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Generated package</p>
          <h2 id="package-title">Campaign assets and evidence</h2>
        </div>
        <span className="evidence-count">{campaignPackage.evidence.length} evidence records</span>
      </div>
      <div className="review-grid">
        <div className="asset-stack">
          {Object.entries(campaignPackage.assets).map(([kind, asset]) => (
            <article className="asset" key={kind}>
              <p className="asset__kind">{kind}</p>
              {"subject" in asset && <h3>{asset.subject}</h3>}
              <p>{asset.body}</p>
            </article>
          ))}
        </div>
        <aside className="validation" aria-label="Validation evidence">
          <div>
            <p className="validation__label">Approved audience</p>
            <strong>{campaignPackage.audience.name}</strong>
            <span>{campaignPackage.audience.member_count} people · aggregate only</span>
          </div>
          <div>
            <p className="validation__label">Sponsor validation</p>
            <strong>{campaignPackage.sponsor.expected_discount_percent}% gold discount</strong>
            <span>
              ${campaignPackage.sponsor.general_ticket_price} general · $
              {campaignPackage.sponsor.sponsor_ticket_price} sponsor
            </span>
          </div>
          <div>
            <p className="validation__label">Action boundary</p>
            <strong>Review only</strong>
            <span>Nothing has been sent, scheduled, or published.</span>
          </div>
          <div className="evaluation-status" aria-live="polite" aria-busy={evaluation?.state === "pending"}>
            <p className="validation__label">Advisory evaluation</p>
            {evaluation ? (
              <>
                <strong>{stateLabels[evaluation.state]}</strong>
                <span>
                  Rubric {evaluation.rubric_id} v{evaluation.rubric_version} · {evaluation.provider} / {evaluation.model}
                </span>
                {evaluation.summary && <span>{evaluation.summary}</span>}
                {evaluation.risk_labels.length > 0 && (
                  <span>Labels: {evaluation.risk_labels.join(", ")}</span>
                )}
                <span>
                  Trace {evaluation.trace_id} · {evaluation.input_tokens + evaluation.output_tokens} tokens · ${(evaluation.cost_microusd / 1_000_000).toFixed(6)}
                </span>
                <span>Advisory only. Approval remains a separate human decision.</span>
              </>
            ) : (
              <>
                <strong>Evaluation not requested</strong>
                <span>The package and human approval remain available.</span>
              </>
            )}
            {canEvaluate && !evaluation && (
              <button className="button" disabled={busy} onClick={onEvaluate} type="button">
                {busy ? "Evaluating..." : "Run advisory evaluation"}
              </button>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}

