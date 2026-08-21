import type { AgentActivity, AgentRun, CampaignPackage } from "../types";

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

type Props = {
  agentCapacity?: { active: number; limit: number };
  agentRuns?: AgentRun[];
  campaignPackage?: CampaignPackage | null;
};

type LaneView = {
  label: string;
  status: string;
  summary: string;
  activity?: AgentActivity[];
};

function specialistLabel(specialist: AgentRun["specialist"]): string {
  return {
    event_readiness: "Event Readiness",
    campaign_composer: "Campaign Composer",
    audience_policy: "Audience and Policy",
  }[specialist];
}

export function LaneBoard({ agentCapacity, agentRuns = [], campaignPackage = null }: Props) {
  const lanes: LaneView[] = agentRuns.length
    ? agentRuns.map((run) => ({
        label: specialistLabel(run.specialist),
        status: run.status,
        summary: run.summary,
        activity: run.activity,
      }))
    : campaignPackage
      ? Object.values(campaignPackage.lanes)
      : waitingLanes;
  const capacity = agentCapacity ?? { active: 0, limit: 3 };
  return (
    <section className="lanes-section" aria-labelledby="lanes-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Hermes-compatible specialist activity</p>
          <h2 id="lanes-title">Specialist lanes</h2>
        </div>
        <div className="lane-board__meta">
          <span className="capacity-note">{capacity.active} active of {capacity.limit} allowed</span>
          <span className="safety-note">No external actions</span>
        </div>
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
            {lane.activity && lane.activity.length > 0 && (
              <ol className="lane__activity" aria-label={`${lane.label} activity`}>
                {lane.activity.map((activity) => (
                  <li key={`${activity.kind}-${activity.created_at}`}>
                    <span>{activity.kind.replaceAll("_", " ")}</span>
                    {activity.message}
                  </li>
                ))}
              </ol>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
