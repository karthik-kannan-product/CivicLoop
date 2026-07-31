import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("shows the CivicLoop foundation and three future agent lanes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok" }),
    }),
  );

  render(<App />);

  expect(screen.getByRole("heading", { name: "CivicLoop" })).toBeInTheDocument();
  expect(screen.getByText("Event Readiness")).toBeInTheDocument();
  expect(screen.getByText("Campaign Composer")).toBeInTheDocument();
  expect(screen.getByText("Audience and Policy")).toBeInTheDocument();
  expect(await screen.findByText("Application healthy")).toBeInTheDocument();
});

test("announces an unavailable application when the health request fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Network unavailable")));

  render(<App />);

  expect(screen.getByRole("status")).toHaveTextContent("Checking application");
  expect(await screen.findByText("Application unavailable")).toBeInTheDocument();
});

test("keeps the later LaunchLoop workflow disabled", () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) }),
  );

  render(<App />);

  expect(screen.getByRole("button", { name: "Start LaunchLoop" })).toBeDisabled();
});
