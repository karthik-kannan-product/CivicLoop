import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

import { ReviewPackage } from "./ReviewPackage";

const campaignPackage = {
  status: "ready_for_review",
  missing_fields: [],
  questions: [],
  assets: {
    invitation: { subject: "Invitation", body: "Synthetic body" },
    reminder: { subject: "Reminder", body: "Synthetic reminder" },
    social: { body: "Synthetic social" },
  },
  audience: { id: "audience", name: "Synthetic audience", member_count: 10, language: "English" },
  sponsor: {
    passed: true,
    tier: "gold",
    expected_discount_percent: 25,
    actual_discount_percent: 25,
    general_ticket_price: 40,
    sponsor_ticket_price: 30,
  },
  lanes: {},
  evidence: ["Synthetic evidence"],
};

test("keeps review available while evaluation is unavailable", () => {
  render(
    <ReviewPackage
      campaignPackage={campaignPackage}
      evaluation={{
        state: "unavailable",
        run_id: "8da86312-f80a-4985-a290-b5076326b546",
        trace_id: "a".repeat(32),
        rubric_id: "launchloop_package_quality",
        rubric_version: 1,
        risk_labels: [],
        summary: "",
        provider: "openai",
        model: "gpt-5-mini-2025-08-07",
        input_tokens: 0,
        output_tokens: 0,
        cost_microusd: 0,
        failure_category: "provider_unavailable",
        advisory_only: true,
      }}
    />,
  );

  expect(screen.getByText("Evaluation unavailable")).toBeInTheDocument();
  expect(screen.getByText("Review only")).toBeInTheDocument();
  expect(screen.getByText(/Approval remains a separate human decision/)).toBeInTheDocument();
});

test("offers the bounded evaluation action only when authorized", async () => {
  const onEvaluate = vi.fn();
  const user = userEvent.setup();
  render(
    <ReviewPackage
      campaignPackage={campaignPackage}
      canEvaluate
      onEvaluate={onEvaluate}
    />,
  );

  await user.click(screen.getByRole("button", { name: "Run advisory evaluation" }));
  expect(onEvaluate).toHaveBeenCalledOnce();
});
