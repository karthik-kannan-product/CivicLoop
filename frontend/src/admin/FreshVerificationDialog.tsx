import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

export function FreshVerificationDialog({
  busy,
  onCancel,
  onSubmit,
}: {
  busy: boolean;
  onCancel: () => void;
  onSubmit: (password: string, token: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const passwordRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => passwordRef.current?.focus(), []);

  function keepFocusInside(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape" && !busy) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    const controls = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled)") ?? [],
    );
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await onSubmit(password, token);
    setPassword("");
    setToken("");
  }

  return (
    <div className="security-dialog-backdrop">
      <div
        ref={dialogRef}
        aria-describedby="fresh-verification-description"
        aria-labelledby="fresh-verification-title"
        aria-modal="true"
        className="security-dialog"
        onKeyDown={keepFocusInside}
        role="dialog"
      >
        <p className="admin-eyebrow">Sensitive action</p>
        <h2 id="fresh-verification-title">Fresh verification required</h2>
        <p id="fresh-verification-description">
          Re-enter your password and a new authenticator code. Recovery codes cannot approve this action.
        </p>
        <form className="admin-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="fresh-password">Password for fresh verification</label>
          <input ref={passwordRef} id="fresh-password" autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required />
          <label htmlFor="fresh-token">Authenticator code for fresh verification</label>
          <input id="fresh-token" autoComplete="one-time-code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} value={token} onChange={(event) => setToken(event.target.value)} required />
          <div className="admin-actions">
            <button className="admin-button admin-button--secondary" disabled={busy} onClick={onCancel} type="button">Cancel</button>
            <button className="admin-button admin-button--primary" disabled={busy} type="submit">{busy ? "Verifying…" : "Verify and continue"}</button>
          </div>
        </form>
      </div>
    </div>
  );
}
