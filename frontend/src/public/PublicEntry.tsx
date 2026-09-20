import type { ReactNode } from "react";

import { LoginGateway } from "./LoginGateway";
import { PublicHome } from "./PublicHome";
import { PublicNotFound } from "./PublicNotFound";

export function normalizePublicPath(pathname: string) {
  if (pathname === "/") return pathname;
  return pathname.replace(/\/+$/, "") || "/";
}

export function PublicEntry({
  pathname,
  sandbox,
}: {
  pathname: string;
  sandbox: ReactNode;
}) {
  switch (normalizePublicPath(pathname)) {
    case "/":
      return <PublicHome />;
    case "/login":
      return <LoginGateway />;
    case "/sandbox":
      return sandbox;
    default:
      return <PublicNotFound />;
  }
}
