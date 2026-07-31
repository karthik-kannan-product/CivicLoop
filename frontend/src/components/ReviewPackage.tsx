import type { CampaignPackage } from "../types";

export function ReviewPackage({ campaignPackage }: { campaignPackage: CampaignPackage }) {
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
        </aside>
      </div>
    </section>
  );
}

