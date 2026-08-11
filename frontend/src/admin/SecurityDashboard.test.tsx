import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { SecurityDashboard } from "./SecurityDashboard";

const currentSession = {
  id: "00000000-0000-4000-8000-000000000001",
  device_label: "Firefox on Windows",
  source_ip: "192.0.2.44",
  created_at: "2026-08-09T10:00:00Z",
  authenticated_at: "2026-08-09T10:00:00Z",
  last_activity_at: "2026-08-09T10:05:00Z",
  mfa_verified_at: "2026-08-09T10:00:00Z",
  absolute_expires_at: "2026-08-09T22:00:00Z",
  expires_at: "2026-08-09T10:35:00Z",
  revoked_at: null,
  is_current: true,
};

const event = {
  id: "00000000-0000-4000-8000-000000000002",
  action: "owner_totp_verified",
  outcome: "success",
  target_type: "",
  target_id: "",
  details: {},
  source_ip: "192.0.2.44",
  session_id: currentSession.id,
  created_at: "2026-08-09T10:00:00Z",
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
});

test("renders bounded sessions and paginated security events", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => response({ sessions: [currentSession] }))
    .mockImplementationOnce(() => response({ events: [event], next_cursor: "signed-cursor" }))
    .mockImplementationOnce(() => response({ events: [{ ...event, id: "00000000-0000-4000-8000-000000000003" }], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  render(<SecurityDashboard onLoggedOut={vi.fn()} />);

  expect(await screen.findByText("Firefox on Windows")).toBeInTheDocument();
  expect(screen.getByText("Current session")).toBeInTheDocument();
  expect(await screen.findByText("Authenticator verified")).toBeInTheDocument();
  expect(screen.queryByText(/user agent/i)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Load older events" }));
  await waitFor(() => expect(screen.getAllByText("Authenticator verified")).toHaveLength(2));
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/v1/admin/security/events?cursor=signed-cursor&limit=25",
    expect.anything(),
  );
});

test("freshly verifies and retries the intended password change", async () => {
  const user = userEvent.setup();
  const freshnessProblem = {
    code: "fresh_verification_required",
    message: "Complete fresh verification.",
  };
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => response({ sessions: [currentSession] }))
    .mockImplementationOnce(() => response({ events: [], next_cursor: null }))
    .mockImplementationOnce(() => response(freshnessProblem, 403))
    .mockImplementationOnce(() => response({ fresh: true }))
    .mockImplementationOnce(() => response({ changed: true, revoked_session_count: 0 }));
  vi.stubGlobal("fetch", fetchMock);

  render(<SecurityDashboard onLoggedOut={vi.fn()} />);
  await screen.findByText("Firefox on Windows");
  await user.type(screen.getByLabelText("Current password"), "Synthetic-Current-934!");
  await user.type(screen.getByLabelText("New password"), "Synthetic-New-935!");
  const submit = screen.getByRole("button", { name: "Change password" });
  await user.click(submit);

  expect(await screen.findByRole("dialog", { name: "Fresh verification required" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("Password for fresh verification"), "Synthetic-Current-934!");
  await user.type(screen.getByLabelText("Authenticator code for fresh verification"), "123456");
  await user.click(screen.getByRole("button", { name: "Verify and continue" }));

  expect(await screen.findByRole("status")).toHaveTextContent("Password changed");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(submit).toHaveFocus());
  expect(fetchMock.mock.calls.filter(([path]) => path === "/api/v1/admin/security/password")).toHaveLength(2);
});

test("shows regenerated recovery codes once and clears them", async () => {
  const user = userEvent.setup();
  const codes = Array.from({ length: 10 }, (_, index) => `ABCDEFG${index}-ABCDEFGHIJKLMNOPQRSTUVWXYZ`);
  vi.stubGlobal(
    "fetch",
    vi.fn()
      .mockImplementationOnce(() => response({ sessions: [] }))
      .mockImplementationOnce(() => response({ events: [], next_cursor: null }))
      .mockImplementationOnce(() => response({ recovery_codes: codes, revoked_session_count: 1 })),
  );

  render(<SecurityDashboard onLoggedOut={vi.fn()} />);
  await screen.findByText("No active sessions found.");
  await user.click(screen.getByRole("button", { name: "Generate new recovery codes" }));

  expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "I saved these codes" }));
  expect(screen.queryByText(codes[0])).not.toBeInTheDocument();
});

test("self revocation signs out and empty events remain meaningful", async () => {
  const user = userEvent.setup();
  const onLoggedOut = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn()
      .mockImplementationOnce(() => response({ sessions: [currentSession] }))
      .mockImplementationOnce(() => response({ events: [], next_cursor: null }))
      .mockImplementationOnce(() => response({ revoked: true, logged_out: true })),
  );

  render(<SecurityDashboard onLoggedOut={onLoggedOut} />);
  expect(await screen.findByText("No security events yet.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Sign out this session" }));

  await waitFor(() => expect(onLoggedOut).toHaveBeenCalledOnce());
});
