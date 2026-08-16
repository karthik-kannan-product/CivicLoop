import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { IntegrationDashboard } from "./IntegrationDashboard";

const connection = {
  provider: "openai",
  state: "healthy",
  capabilities: ["connection_test", "inference"],
  configuration: { region: "us", model: "openai/gpt-oss-20b" },
  version: 3,
  created_at: "2026-08-10T10:00:00Z",
  updated_at: "2026-08-10T11:00:00Z",
  last_successful_test_at: "2026-08-10T11:00:00Z",
  last_failure_category: null,
};

function response(value: object, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => value,
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

test("renders the four provider cards while safely ignoring malformed metadata", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ connections: [connection, { provider: "unsafe", state: "credential=secret" }] })));

  render(<IntegrationDashboard />);

  expect(await screen.findByRole("heading", { name: "Integration connections" })).toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(4);
  expect(screen.getByRole("heading", { name: "OpenAI" })).toBeInTheDocument();
  expect(screen.getByText("Healthy")).toBeInTheDocument();
  expect(screen.queryByText(/credential=secret/)).not.toBeInTheDocument();
});

test("requires fresh password and TOTP before credential entry and sends the credential only once", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({ connections: [connection] }))
    .mockResolvedValueOnce(response({ fresh: true }))
    .mockResolvedValueOnce(response({ ...connection, version: 4, state: "configured" }));
  vi.stubGlobal("fetch", fetchMock);

  render(<IntegrationDashboard />);
  await screen.findByRole("heading", { name: "OpenAI" });
  await user.click(screen.getByRole("button", { name: "Replace credential for OpenAI" }));
  expect(await screen.findByRole("dialog", { name: "Fresh verification required" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Credential for OpenAI")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("Password for fresh verification"), "Synthetic-Current-934!");
  await user.type(screen.getByLabelText("Authenticator code for fresh verification"), "123456");
  await user.click(screen.getByRole("button", { name: "Verify and continue" }));
  await user.type(await screen.findByLabelText("Credential for OpenAI"), "write-only-value");
  await user.click(screen.getByRole("button", { name: "Save credential" }));

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/admin/integrations/openai/credential",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ credential: "write-only-value", expected_version: 3 }) }),
  );
  expect(screen.queryByDisplayValue("write-only-value")).not.toBeInTheDocument();
  expect(window.localStorage).toHaveLength(0);
  expect(window.sessionStorage).toHaveLength(0);
});

test("clears the entered credential after a version conflict and exposes only allowlisted audit actions", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(response({ connections: [connection] }))
    .mockResolvedValueOnce(response({ fresh: true }))
    .mockResolvedValueOnce(response({ code: "version_conflict", message: "Connection changed elsewhere." }, 409))
    .mockResolvedValueOnce(response({ events: [
      { action: "credential_replaced", outcome: "success", correlation_id: "00000000-0000-4000-8000-000000000001", created_at: "2026-08-10T11:00:00Z" },
      { action: "credential_exfiltrated", outcome: "success", correlation_id: "00000000-0000-4000-8000-000000000002", created_at: "2026-08-10T11:00:00Z" },
    ], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  render(<IntegrationDashboard />);
  await screen.findByRole("heading", { name: "OpenAI" });
  await user.click(screen.getByRole("button", { name: "Replace credential for OpenAI" }));
  await user.type(await screen.findByLabelText("Password for fresh verification"), "Synthetic-Current-934!");
  await user.type(screen.getByLabelText("Authenticator code for fresh verification"), "123456");
  await user.click(screen.getByRole("button", { name: "Verify and continue" }));
  await user.type(await screen.findByLabelText("Credential for OpenAI"), "will-be-cleared");
  await user.click(screen.getByRole("button", { name: "Save credential" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection changed elsewhere.");
  expect(screen.queryByDisplayValue("will-be-cleared")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "View OpenAI history" }));
  expect(await screen.findByText("Credential replaced")).toBeInTheDocument();
  expect(screen.queryByText(/exfiltrated/)).not.toBeInTheDocument();
});
