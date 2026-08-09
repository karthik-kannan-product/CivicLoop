import { useState, type FormEvent } from "react";
import { QRCodeSVG } from "qrcode.react";

import type { Enrollment } from "./api";

type Submit = (value: string) => Promise<void>;

export function PasswordScreen({ busy, onSubmit }: { busy: boolean; onSubmit: (u: string, p: string) => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSubmit(username, password);
    setPassword("");
  }
  return (
    <form className="admin-card admin-form" onSubmit={(event) => void submit(event)}>
      <p className="admin-eyebrow">Restricted access</p>
      <h1>Administrator security</h1>
      <p>Sign in with the owner account. A password alone never grants administrative access.</p>
      <label htmlFor="admin-username">Administrator username</label>
      <input id="admin-username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required />
      <label htmlFor="admin-password">Password</label>
      <input id="admin-password" autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
      <button className="admin-button admin-button--primary" disabled={busy} type="submit">{busy ? "Checking…" : "Continue"}</button>
    </form>
  );
}

export function TotpScreen({ busy, onSubmit, onRecovery }: { busy: boolean; onSubmit: Submit; onRecovery: () => void }) {
  const [token, setToken] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSubmit(token);
    setToken("");
  }
  return (
    <form className="admin-card admin-form" onSubmit={(event) => void submit(event)}>
      <p className="admin-eyebrow">Second factor</p>
      <h1>Enter your authenticator code</h1>
      <p>Use the current six-digit code. Each code can be accepted only once.</p>
      <label htmlFor="admin-totp">6-digit authenticator code</label>
      <input id="admin-totp" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={token} onChange={(event) => setToken(event.target.value)} required />
      <button className="admin-button admin-button--primary" disabled={busy} type="submit">{busy ? "Verifying…" : "Verify and sign in"}</button>
      <button className="admin-button admin-button--text" disabled={busy} onClick={onRecovery} type="button">Use a recovery code</button>
    </form>
  );
}

export function RecoveryScreen({ busy, onSubmit, onBack }: { busy: boolean; onSubmit: Submit; onBack: () => void }) {
  const [code, setCode] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSubmit(code);
    setCode("");
  }
  return (
    <form className="admin-card admin-form" onSubmit={(event) => void submit(event)}>
      <p className="admin-eyebrow">Account recovery</p>
      <h1>Use one recovery code</h1>
      <p>Recovery grants access only to replace your authenticator and recovery codes.</p>
      <label htmlFor="admin-recovery">Recovery code</label>
      <input id="admin-recovery" autoComplete="off" value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} required />
      <button className="admin-button admin-button--primary" disabled={busy} type="submit">{busy ? "Checking…" : "Continue recovery"}</button>
      <button className="admin-button admin-button--text" disabled={busy} onClick={onBack} type="button">Back to authenticator code</button>
    </form>
  );
}

export function EnrollmentStart({ busy, recovery, onSubmit }: { busy: boolean; recovery: boolean; onSubmit: Submit }) {
  return (
    <section className="admin-card">
      <p className="admin-eyebrow">{recovery ? "Recovery required" : "First sign-in"}</p>
      <h1>{recovery ? "Replace your authenticator" : "Set up your authenticator"}</h1>
      <p>CivicLoop generates the setup secret locally and encrypts it before storage. Keep your authenticator nearby.</p>
      <button className="admin-button admin-button--primary" disabled={busy} onClick={() => void onSubmit("Primary authenticator")} type="button">{busy ? "Preparing…" : "Set up authenticator"}</button>
    </section>
  );
}

export function EnrollmentConfirm({ busy, enrollment, onSubmit }: { busy: boolean; enrollment: Enrollment; onSubmit: Submit }) {
  const [token, setToken] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSubmit(token);
    setToken("");
  }
  return (
    <section className="admin-card admin-enrollment">
      <div>
        <p className="admin-eyebrow">Authenticator setup</p>
        <h1>Scan, then verify</h1>
        <p>Scan this code with your authenticator. The setup material disappears after confirmation.</p>
        <div className="admin-qr"><QRCodeSVG value={enrollment.otpauth_uri} size={184} title="Authenticator setup QR code" /></div>
        <p className="admin-secret-label">Manual setup code</p>
        <code className="admin-secret">{enrollment.manual_secret}</code>
      </div>
      <form className="admin-form" onSubmit={(event) => void submit(event)}>
        <label htmlFor="admin-confirm-totp">6-digit authenticator code</label>
        <input id="admin-confirm-totp" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={token} onChange={(event) => setToken(event.target.value)} required />
        <button className="admin-button admin-button--primary" disabled={busy} type="submit">{busy ? "Confirming…" : "Confirm authenticator"}</button>
      </form>
    </section>
  );
}
