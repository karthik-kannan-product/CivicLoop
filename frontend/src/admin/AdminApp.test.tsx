import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";

import { AdminApp } from "./AdminApp";

function jsonResponse(value: object, status = 200, headers: Record<string, string> = {}) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name: string) => headers[name] ?? null },
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

test("signs in with password and TOTP without persisting credentials", async () => {
  const user = userEvent.setup();
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => jsonResponse({ stage: "anonymous" }))
    .mockImplementationOnce(() => jsonResponse({
      stage: "password_verified",
      expires_at: "2026-08-09T12:05:00Z",
      next_action: "verify_totp",
    }))
    .mockImplementationOnce(() => jsonResponse({ stage: "authenticated" }))
    .mockImplementationOnce(() => jsonResponse({ sessions: [] }))
    .mockImplementationOnce(() => jsonResponse({ events: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  render(<AdminApp />);

  await user.type(await screen.findByLabelText("Administrator username"), "civicloop.owner");
  await user.type(screen.getByLabelText("Password"), "Synthetic-Passphrase-934!");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.type(await screen.findByLabelText("6-digit authenticator code"), "123456");
  await user.click(screen.getByRole("button", { name: "Verify and sign in" }));

  expect(await screen.findByRole("heading", { name: "Security overview" })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/admin/auth/totp",
    expect.objectContaining({ credentials: "same-origin", body: JSON.stringify({ token: "123456" }) }),
  );
  expect(window.localStorage).toHaveLength(0);
  expect(window.sessionStorage).toHaveLength(0);
});

test("enrolls an authenticator and displays recovery codes exactly once", async () => {
  const user = userEvent.setup();
  const recoveryCodes = Array.from(
    { length: 10 },
    (_, index) => `ABCDEFG${index}-ABCDEFGHIJKLMNOPQRSTUVWXYZ`,
  );
  vi.stubGlobal(
    "fetch",
    vi.fn()
      .mockImplementationOnce(() => jsonResponse({ stage: "anonymous" }))
      .mockImplementationOnce(() => jsonResponse({
        stage: "password_verified",
        expires_at: "2026-08-09T12:05:00Z",
        next_action: "enroll_totp",
      }))
      .mockImplementationOnce(() => jsonResponse({
        device_id: "00000000-0000-4000-8000-000000000001",
        otpauth_uri: "otpauth://totp/CivicLoop%3Aowner?secret=ABCDEFGHIJKLMNOP&issuer=CivicLoop",
        manual_secret: "ABCDEFGHIJKLMNOP",
      }))
      .mockImplementationOnce(() => jsonResponse({
        stage: "authenticated",
        recovery_codes: recoveryCodes,
      }))
      .mockImplementationOnce(() => jsonResponse({ sessions: [] }))
      .mockImplementationOnce(() => jsonResponse({ events: [], next_cursor: null })),
  );

  render(<AdminApp />);
  await user.type(await screen.findByLabelText("Administrator username"), "owner");
  await user.type(screen.getByLabelText("Password"), "Synthetic-Passphrase-934!");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(await screen.findByRole("button", { name: "Set up authenticator" }));

  expect(await screen.findByText("ABCDEFGHIJKLMNOP")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: "Authenticator setup QR code" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("6-digit authenticator code"), "654321");
  await user.click(screen.getByRole("button", { name: "Confirm authenticator" }));

  expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(10);
  await user.click(screen.getByRole("button", { name: "I saved these codes" }));
  expect(await screen.findByRole("heading", { name: "Security overview" })).toBeInTheDocument();
  expect(screen.queryByText(recoveryCodes[0])).not.toBeInTheDocument();
});

test("offers recovery login and announces retryable failures", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn()
      .mockImplementationOnce(() => jsonResponse({ stage: "anonymous" }))
      .mockImplementationOnce(() => jsonResponse({
        stage: "password_verified",
        expires_at: "2026-08-09T12:05:00Z",
        next_action: "verify_totp",
      }))
      .mockImplementationOnce(() => jsonResponse({
        type: "/problems/rate_limited",
        title: "Too many attempts",
        status: 429,
        detail: "Try again later.",
        instance: "/api/v1/admin/auth/recovery",
        code: "rate_limited",
        message: "Too many authentication attempts. Try again later.",
      }, 429, { "Retry-After": "300" })),
  );

  render(<AdminApp />);
  await user.type(await screen.findByLabelText("Administrator username"), "owner");
  await user.type(screen.getByLabelText("Password"), "Synthetic-Passphrase-934!");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await user.click(await screen.findByRole("button", { name: "Use a recovery code" }));
  await user.type(screen.getByLabelText("Recovery code"), "AAAAAAAA-AAAAAAAAAAAAAAAAAAAAAAAAAA");
  await user.click(screen.getByRole("button", { name: "Continue recovery" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 300 seconds");
});

test("renders a retry state when status is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("synthetic network detail")));

  render(<AdminApp />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Administrator security is temporarily unavailable",
  );
  expect(screen.queryByText("synthetic network detail")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
});
