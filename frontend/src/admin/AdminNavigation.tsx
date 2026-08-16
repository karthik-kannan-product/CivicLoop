type AdminPath = "security" | "integrations";

export function adminPathname(pathname: string): AdminPath {
  return pathname === "/admin/integrations" || pathname === "/admin/integrations/" ? "integrations" : "security";
}

export function AdminNavigation({ current }: { current: AdminPath }) {
  return <nav aria-label="Administrator sections" className="admin-navigation">
    <a aria-current={current === "security" ? "page" : undefined} href="/admin/security">Security</a>
    <a aria-current={current === "integrations" ? "page" : undefined} href="/admin/integrations">Integrations</a>
  </nav>;
}
