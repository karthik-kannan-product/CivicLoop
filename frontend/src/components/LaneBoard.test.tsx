import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { LaneBoard } from "./LaneBoard";

test("shows durable specialist activity and the three-agent capacity boundary", () => {
  render(
    <LaneBoard
      agentCapacity={{ active: 0, limit: 3 }}
      agentRuns={[
        {
          specialist: "event_readiness",
          provider: "deterministic_hermes",
          status: "completed",
          summary: "3 event facts need an operator response.",
          revision: 1,
          activity: [
            {
              kind: "completed",
              message: "3 event facts need an operator response.",
              created_at: "2026-08-21T12:00:00Z",
            },
          ],
        },
        {
          specialist: "campaign_composer",
          provider: "deterministic_hermes",
          status: "completed",
          summary: "Prepared invitation, reminder, and social drafts for review.",
          revision: 1,
          activity: [],
        },
        {
          specialist: "audience_policy",
          provider: "deterministic_hermes",
          status: "completed",
          summary: "Matched the approved audience and validated sponsor pricing.",
          revision: 1,
          activity: [],
        },
      ]}
    />,
  );

  expect(screen.getByText("Hermes-compatible specialist activity")).toBeInTheDocument();
  expect(screen.getByText("0 active of 3 allowed")).toBeInTheDocument();
  expect(screen.getByLabelText("Event Readiness activity")).toHaveTextContent(
    "3 event facts need an operator response.",
  );
  expect(screen.getByText("No external actions")).toBeInTheDocument();
});
