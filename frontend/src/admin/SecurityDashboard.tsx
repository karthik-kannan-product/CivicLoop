import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { AdminAPIError, adminAPI, type AdministratorSession, type SecurityEvent } from "./api";
import { FreshVerificationDialog } from "./FreshVerificationDialog";
import { RecoveryCodes } from "./RecoveryCodes";

const ACTION_LABELS: Record<string, string> = {
  owner_bootstrapped: "Owner account created",
  owner_fresh_verification: "Fresh verification completed",
  owner_logout: "Administrator signed out",
  owner_password_changed: "Password changed",
  owner_password_verified: "Password verified",
  owner_recovery_codes_regenerated: "Recovery codes regenerated",
  owner_recovery_verified: "Recovery code verified",
  owner_session_revoked: "Session revoked",
  owner_totp_enrollment_confirmed: "Authenticator confirmed",
  owner_totp_enrollment_started: "Authenticator setup started",
  owner_totp_verified: "Authenticator verified",
};

function displayTime(value: string | null): string {
  if (!value) return "Not available";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

export function SecurityDashboard({ onLoggedOut }: { onLoggedOut: () => void }) {
  const [sessions, setSessions] = useState<AdministratorSession[]>([]);
  const [events, setEvents] = useState<SecurityEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [freshDialog, setFreshDialog] = useState(false);
  const pendingAction = useRef<(() => Promise<void>) | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sessionPage, eventPage] = await Promise.all([adminAPI.sessions(), adminAPI.events()]);
      setSessions(sessionPage.sessions);
      setEvents(eventPage.events);
      setNextCursor(eventPage.next_cursor);
    } catch {
      setError("CivicLoop could not load administrator security data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function run(action: () => Promise<void>, sensitive = false) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (caught) {
      if (sensitive && caught instanceof AdminAPIError && caught.code === "fresh_verification_required") {
        pendingAction.current = action;
        returnFocus.current = document.activeElement as HTMLElement | null;
        setFreshDialog(true);
      } else {
        setError(caught instanceof AdminAPIError ? caught.message : "CivicLoop could not complete that security action.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function verifyFresh(password: string, token: string) {
    await run(async () => {
      await adminAPI.reauthenticate(password, token);
      setFreshDialog(false);
      const intended = pendingAction.current;
      pendingAction.current = null;
      if (intended) await intended();
      window.setTimeout(() => returnFocus.current?.focus(), 0);
    });
  }

  async function changePassword(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await adminAPI.changePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setSessions((items) => items.filter((item) => item.is_current));
      setNotice("Password changed. Other administrator sessions were revoked.");
    }, true);
  }

  async function regenerateCodes() {
    await run(async () => {
      const result = await adminAPI.regenerateRecoveryCodes();
      setCodes(result.recovery_codes);
      setSessions((items) => items.filter((item) => item.is_current));
    }, true);
  }

  async function revoke(session: AdministratorSession) {
    await run(async () => {
      const result = await adminAPI.revokeSession(session.id);
      if (result.logged_out) onLoggedOut();
      else setSessions((items) => items.filter((item) => item.id !== session.id));
    });
  }

  async function revokeOthers() {
    await run(async () => {
      const result = await adminAPI.revokeOthers();
      setSessions((items) => items.filter((item) => item.is_current));
      setNotice(`${result.revoked_count} other session${result.revoked_count === 1 ? "" : "s"} revoked.`);
    }, true);
  }

  async function loadOlderEvents() {
    if (!nextCursor) return;
    await run(async () => {
      const page = await adminAPI.events(nextCursor);
      setEvents((items) => [...items, ...page.events]);
      setNextCursor(page.next_cursor);
    });
  }

  if (codes.length) return <RecoveryCodes codes={codes} onContinue={() => setCodes([])} />;

  return (
    <div className="security-dashboard">
      <header className="security-heading"><div><p className="admin-eyebrow">Owner account</p><h1>Security overview</h1><p>Manage the credentials and sessions that can control CivicLoop integrations.</p></div><span className="security-mfa">MFA active</span></header>
      {error && <div className="admin-error" role="alert">{error}</div>}
      {notice && <div className="security-notice" role="status">{notice}</div>}
      {loading ? <section className="security-panel" aria-busy="true"><h2>Loading security details…</h2></section> : <>
        <section className="security-panel" aria-labelledby="password-title"><h2 id="password-title">Change password</h2><p>Changing the password signs out every other administrator session.</p><form className="admin-form security-password-form" onSubmit={(event) => void changePassword(event)}><label htmlFor="security-current-password">Current password</label><input id="security-current-password" type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /><label htmlFor="security-new-password">New password</label><input id="security-new-password" type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /><button className="admin-button admin-button--primary" disabled={busy} type="submit">Change password</button></form></section>
        <section className="security-panel" aria-labelledby="recovery-title"><h2 id="recovery-title">Recovery codes</h2><p>Generating a new set invalidates every unused old code and signs out other sessions.</p><button className="admin-button admin-button--secondary" disabled={busy} onClick={() => void regenerateCodes()} type="button">Generate new recovery codes</button></section>
        <section className="security-panel" aria-labelledby="sessions-title"><div className="security-section-heading"><div><h2 id="sessions-title">Active sessions</h2><p>Review browser labels, recent activity, and source addresses.</p></div><button className="admin-button admin-button--secondary" disabled={busy || sessions.length < 2} onClick={() => void revokeOthers()} type="button">Revoke other sessions</button></div>{sessions.length === 0 ? <p className="security-empty">No active sessions found.</p> : <ul className="security-list">{sessions.map((session) => <li key={session.id}><div><h3>{session.device_label}</h3><p>{session.source_ip ?? "Address unavailable"} · Last active {displayTime(session.last_activity_at)}</p><p>Expires {displayTime(session.expires_at)}</p></div><div>{session.is_current && <span className="security-current">Current session</span>}<button className="admin-button admin-button--text" disabled={busy} onClick={() => void revoke(session)} type="button">{session.is_current ? "Sign out this session" : "Revoke session"}</button></div></li>)}</ul>}</section>
        <section className="security-panel" aria-labelledby="events-title"><h2 id="events-title">Security events</h2><p>Append-only history of administrator security transitions.</p>{events.length === 0 ? <p className="security-empty">No security events yet.</p> : <ol className="security-event-list">{events.map((item) => <li key={item.id}><strong>{ACTION_LABELS[item.action] ?? item.action.replaceAll("_", " ")}</strong><span>{item.outcome} · {displayTime(item.created_at)}{item.source_ip ? ` · ${item.source_ip}` : ""}</span></li>)}</ol>}{nextCursor && <button className="admin-button admin-button--secondary" disabled={busy} onClick={() => void loadOlderEvents()} type="button">Load older events</button>}</section>
      </>}
      {freshDialog && <FreshVerificationDialog busy={busy} onCancel={() => { setFreshDialog(false); pendingAction.current = null; window.setTimeout(() => returnFocus.current?.focus(), 0); }} onSubmit={verifyFresh} />}
    </div>
  );
}
