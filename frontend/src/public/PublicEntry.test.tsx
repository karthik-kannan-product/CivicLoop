import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { PublicEntry, normalizePublicPath } from "./PublicEntry";

afterEach(cleanup);

function renderPublicEntry(pathname: string) {
  return render(
    <PublicEntry
      pathname={pathname}
      sandbox={<p>Checking your demo session...</p>}
    />,
  );
}

test("normalizes only trailing slashes", () => {
  expect(normalizePublicPath("/")).toBe("/");
  expect(normalizePublicPath("/login/")).toBe("/login");
  expect(normalizePublicPath("/sandbox/")).toBe("/sandbox");
  expect(normalizePublicPath("/projects/civicloop/")).toBe("/projects/civicloop");
});

test("dispatches the homepage", () => {
  renderPublicEntry("/");
  expect(
    screen.getByRole("heading", {
      name: "Human-approved AI workflows for work that matters.",
    }),
  ).toBeInTheDocument();
});

test("dispatches the login gateway", () => {
  renderPublicEntry("/login/");
  expect(
    screen.getByRole("heading", {
      name: "Choose how you want to enter CivicLoop.",
    }),
  ).toBeInTheDocument();
});

test("dispatches the authenticated sandbox entry", () => {
  renderPublicEntry("/sandbox");
  expect(screen.getByText("Checking your demo session...")).toBeInTheDocument();
});

test("renders a useful not-found page for unknown public paths", () => {
  renderPublicEntry("/projects/civicloop");
  expect(
    screen.getByRole("heading", { name: "That page is not part of CivicLoop." }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Return home" })).toHaveAttribute(
    "href",
    "/",
  );
});

test("homepage offers contact, sandbox, and transparent development paths", () => {
  renderPublicEntry("/");

  for (const link of screen.getAllByRole("link", { name: "Book a conversation" })) {
    expect(link).toHaveAttribute(
      "href",
      "https://calendar.proton.me/bookings#KkKDMGq1h4GQZfzYq7jvGNFicfShuijjIRF3Wtg8ods=",
    );
  }
  expect(screen.getByRole("link", { name: "Explore the sandbox" })).toHaveAttribute(
    "href",
    "/sandbox",
  );
  expect(
    screen.getByRole("link", { name: "Follow the development on GitHub" }),
  ).toHaveAttribute(
    "href",
    "https://github.com/karthik-kannan-product/CivicLoop",
  );
  expect(screen.getByRole("link", { name: "Read the architecture" })).toHaveAttribute(
    "href",
    "https://github.com/karthik-kannan-product/CivicLoop/blob/main/docs/2026-07-30-civicloop-v1-architecture-design.md",
  );
  expect(screen.getByText("Live")).toBeInTheDocument();
  expect(screen.getByText("In development")).toBeInTheDocument();
  expect(screen.getByText("Planned")).toBeInTheDocument();
  expect(
    screen.getByText(/Evaluation stays synthetic-only/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Publishing, sending, scheduling, ticket-economics changes/),
  ).toBeInTheDocument();
});

test("login gateway separates sandbox and owner identities", () => {
  renderPublicEntry("/login");

  expect(
    screen.getByText(/Sandbox and owner identities are separate/),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Enter the sandbox" })).toHaveAttribute(
    "href",
    "/sandbox",
  );
  expect(
    screen.getByRole("link", { name: "Open owner administration" }),
  ).toHaveAttribute("href", "/admin/security");
  expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
});

test("external homepage actions use safe new-tab attributes", () => {
  renderPublicEntry("/");
  for (const name of [
    "Book a conversation",
    "Follow the development on GitHub",
    "Read the architecture",
    "View the source",
  ]) {
    for (const link of screen.getAllByRole("link", { name })) {
      expect(link).toHaveAttribute("target", "_blank");
      expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  }
});
