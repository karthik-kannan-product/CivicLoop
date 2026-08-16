import { useCallback, useEffect, useRef, useState } from "react";

import { AdminAPIError, adminAPI, type AuthenticationStage, type Enrollment } from "./api";
import { EnrollmentConfirm, EnrollmentStart, PasswordScreen, RecoveryScreen, TotpScreen } from "./AuthScreens";
import { AdminNavigation, adminPathname } from "./AdminNavigation";
import { IntegrationDashboard } from "./IntegrationDashboard";
import { RecoveryCodes } from "./RecoveryCodes";
import { SecurityDashboard } from "./SecurityDashboard";

type Screen = AuthenticationStage | "loading" | "recovery_code" | "enrollment_confirm" | "recovery_codes" | "unavailable";

function friendlyError(error: unknown): string {
  if (error instanceof AdminAPIError) {
    if (error.code === "rate_limited" && error.retryAfter) return `Too many attempts. Try again in ${error.retryAfter} seconds.`;
    if (error.code === "identity_unavailable") return "Administrator security is temporarily unavailable. Try again shortly.";
    if (error.code === "preauthentication_required") return "Your password verification expired. Start again.";
    return error.message;
  }
  return "Administrator security is temporarily unavailable. Check the connection and try again.";
}

export function AdminApp() {
  const [screen, setScreen] = useState<Screen>("loading");
  const [nextAction, setNextAction] = useState<"enroll_totp" | "verify_totp">("verify_totp");
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const headingRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    setError(null);
    setScreen("loading");
    try {
      const status = await adminAPI.status();
      setScreen(status.stage);
    } catch {
      setScreen("unavailable");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { headingRef.current?.focus(); }, [screen]);

  async function perform(operation: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await operation();
    } catch (caught) {
      setError(friendlyError(caught));
      if (caught instanceof AdminAPIError && caught.code === "preauthentication_required") {
        setEnrollment(null);
        setScreen("anonymous");
      }
    } finally {
      setBusy(false);
    }
  }

  let content;
  if (screen === "loading") {
    content = <section className="admin-card" aria-busy="true"><p className="admin-eyebrow">CivicLoop security</p><h1>Checking administrator session…</h1></section>;
  } else if (screen === "unavailable") {
    content = <section className="admin-card" role="alert"><p className="admin-eyebrow">Service unavailable</p><h1>Administrator security is temporarily unavailable</h1><p>Check the application connection, then try again.</p><button className="admin-button admin-button--primary" onClick={() => void load()} type="button">Try again</button></section>;
  } else if (screen === "anonymous") {
    content = <PasswordScreen busy={busy} onSubmit={async (username, password) => perform(async () => { const result = await adminAPI.password(username, password); setNextAction(result.next_action); setScreen("password_verified"); })} />;
  } else if (screen === "password_verified" && nextAction === "enroll_totp") {
    content = <EnrollmentStart busy={busy} recovery={false} onSubmit={async (label) => perform(async () => { setEnrollment(await adminAPI.beginEnrollment(label)); setScreen("enrollment_confirm"); })} />;
  } else if (screen === "password_verified") {
    content = <TotpScreen busy={busy} onRecovery={() => setScreen("recovery_code")} onSubmit={async (token) => perform(async () => { await adminAPI.totp(token); setScreen("authenticated"); })} />;
  } else if (screen === "recovery_code") {
    content = <RecoveryScreen busy={busy} onBack={() => setScreen("password_verified")} onSubmit={async (code) => perform(async () => { await adminAPI.recovery(code); setScreen("recovery_restricted"); })} />;
  } else if (screen === "recovery_restricted") {
    content = <EnrollmentStart busy={busy} recovery onSubmit={async (label) => perform(async () => { setEnrollment(await adminAPI.beginEnrollment(label)); setScreen("enrollment_confirm"); })} />;
  } else if (screen === "enrollment_confirm" && enrollment) {
    content = <EnrollmentConfirm busy={busy} enrollment={enrollment} onSubmit={async (token) => perform(async () => { const result = await adminAPI.confirmEnrollment(enrollment.device_id, token); setEnrollment(null); setRecoveryCodes(result.recovery_codes); setScreen("recovery_codes"); })} />;
  } else if (screen === "recovery_codes") {
    content = <RecoveryCodes codes={recoveryCodes} onContinue={() => { setRecoveryCodes([]); setScreen("authenticated"); }} />;
  } else {
    const current = adminPathname(window.location.pathname);
    content = <><AdminNavigation current={current} />{current === "integrations" ? <IntegrationDashboard /> : <SecurityDashboard onLoggedOut={() => setScreen("anonymous")} />}</>;
  }

  return (
    <div className="admin-shell">
      <header className="admin-header"><a className="admin-brand" href="/" aria-label="CivicLoop home"><span aria-hidden="true">CL</span><strong>CivicLoop</strong></a><p>Administrator console</p></header>
      <main className="admin-main admin-focus-target" ref={headingRef} tabIndex={-1}>
        {error && <div className="admin-error" role="alert">{error}</div>}
        {content}
      </main>
      <footer className="admin-footer">Integration credentials are submitted once after fresh verification and are never written to browser storage.</footer>
    </div>
  );
}
