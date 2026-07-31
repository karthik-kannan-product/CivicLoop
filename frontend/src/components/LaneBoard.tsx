import type { CampaignPackage } from "../types";

const waitingLanes = [
  {
    label: "Event Readiness",
    status: "waiting",
    summary: "Checks completeness and asks grounded questions.",
  },
  {
    label: "Campaign Composer",
    status: "waiting",
    summary: "Prepares invitation, reminder, and social drafts.",
  },
  {
    label: "Audience and Policy",
    status: "waiting",
    summary: "Validates targeting, language, and sponsor pricing.",
  },
];

export function LaneBoard({ campaignPackage }: { campaignPackage: CampaignPackage | null }) {
  const lanes = campaignPackage ? Object.values(campaignPackage.lanes) : waitingLanes;
  return (
    <section className="lanes-section" aria-labelledby="lanes-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Deterministic fake-agent run</p>
          <h2 id="lanes-title">Specialist lanes</h2>
        </div>
        <span className="safety-note">No external actions</span>
      </div>
      <div className="lanes">
        {lanes.map((lane) => (
          <article className="lane" key={lane.label}>
            <p className={`lane__state lane__state--${lane.status}`}>
              <span aria-hidden="true" className="lane__dot" />
              {lane.status.replaceAll("_", " ")}
            </p>
            <h3>{lane.label}</h3>
            <p>{lane.summary}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

