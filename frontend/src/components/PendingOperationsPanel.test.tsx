import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { PendingOperationsPanel } from "./PendingOperationsPanel";

afterEach(cleanup);
test("renders each pending intent without execution or approval controls", () => {
  render(<PendingOperationsPanel operations={[
    { operation_id: "event", provider: "eventbrite", operation_kind: "create_eventbrite_draft", status: "pending", action_digest: "a".repeat(64) },
    { operation_id: "email", provider: "iterable", operation_kind: "create_iterable_email_draft", status: "pending", action_digest: "b".repeat(64) },
    { operation_id: "reminder", provider: "iterable", operation_kind: "create_iterable_reminder_draft", status: "pending", action_digest: "c".repeat(64) },
  ]} />);
  expect(screen.getByRole("heading", { name: "Pending Eventbrite draft" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Pending Iterable email draft" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Pending Iterable reminder draft" })).toBeInTheDocument();
  expect(screen.getAllByText("Status: pending")).toHaveLength(3);
  expect(screen.getByText(/Nothing has been created at Eventbrite or Iterable/)).toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
test("explains the empty state", () => {
  render(<PendingOperationsPanel operations={[]} />);
  expect(screen.getByText("No pending draft operations.")).toBeInTheDocument();
});
