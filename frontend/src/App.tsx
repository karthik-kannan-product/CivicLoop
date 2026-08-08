import { useCallback, useEffect, useState } from "react";

import { loginDemo, logoutDemo, requestDemo, requestSession, type SessionUser } from "./api";
import { CompletionPanel } from "./components/CompletionPanel";
import { DecisionPanel } from "./components/DecisionPanel";
import { EventBrief } from "./components/EventBrief";
import { LaneBoard } from "./components/LaneBoard";
import { ReviewPackage } from "./components/ReviewPackage";
import { Timeline } from "./components/Timeline";
import { WorkspaceHeader } from "./components/WorkspaceHeader";
import type { DemoState } from "./types";

function Workspace({ sessionUser, onLogout }: { sessionUser?: SessionUser; onLogout?: () => void }) {
  const [state, setState] = useState<DemoState | null>(null);
  const [actor, setActor] = useState(sessionUser?.role === "approver" ? "jordan" : "maya");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setState(await requestDemo("/api/v1/demo"));
    } catch {
      setError("CivicLoop could not load the demo workspace.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function mutate(path: string, body?: Record<string, string>) {
    setBusy(true);
    setError(null);
    try {
      setState(await requestDemo(path, { actor, body, method: "POST" }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "CivicLoop could not continue.");
    } finally {
      setBusy(false);
    }
  }

  if (error && !state) {
    return (
      <main className="load-state" role="alert">
        <p className="eyebrow">Workspace unavailable</p>
        <h1>CivicLoop could not load the demo workspace.</h1>
        <p>Check the application connection, then try again.</p>
        <button className="button button--primary" onClick={() => void load()}>
          Try again
        </button>
      </main>
    );
  }

  if (!state) {
    return (
      <main className="load-state" aria-busy="true">
        <p className="eyebrow">Loading CivicLoop</p>
        <h1>Preparing the LaunchLoop workspace...</h1>
      </main>
    );
  }

  const workflowId = state.workflow.id;
  const activeActor = state.actors.find((item) => item.slug === actor);
  const isOperator = sessionUser ? sessionUser.role === "operator" : activeActor?.role === "operator";

  return (
    <div className="app-shell">
      <WorkspaceHeader
        actors={state.actors}
        actor={actor}
        busy={busy}
        deploymentMode={state.deployment_mode}
        onActorChange={setActor}
        onLogout={onLogout}
        onReset={() => void mutate("/api/v1/demo/reset")}
        sessionUser={sessionUser}
      />
      <main className="workspace">
        {error && <div className="inline-error" role="alert">{error}</div>}
        {sessionUser?.role === "approver" && (
          <section className="monitoring-summary" aria-labelledby="monitoring-title">
            <p className="eyebrow">Approval and monitoring</p>
            <h1 id="monitoring-title">Approver dashboard</h1>
            <p>Review the locked package, agent evidence, and durable audit trail before deciding.</p>
          </section>
        )}
        <EventBrief
          state={state}
          isOperator={Boolean(isOperator)}
          busy={busy}
          onRun={() => void mutate(`/api/v1/workflows/${workflowId}/runs`)}
          onResolve={(answers) => void mutate(`/api/v1/workflows/${workflowId}/answers`, answers)}
        />
        <LaneBoard campaignPackage={state.workflow.package} />
        {state.workflow.package && <ReviewPackage campaignPackage={state.workflow.package} />}
        <DecisionPanel
          state={state}
          actor={actor}
          busy={busy}
          onSubmit={() => void mutate(`/api/v1/workflows/${workflowId}/submit`)}
          onApprove={() => void mutate(`/api/v1/approvals/${state.approval?.id}/decision`, {
            decision: "approve",
            package_hash: state.approval?.package_hash ?? "",
          })}
          onReject={(reason) => void mutate(`/api/v1/approvals/${state.approval?.id}/decision`, {
            decision: "reject",
            package_hash: state.approval?.package_hash ?? "",
            reason,
          })}
        />
        <CompletionPanel state={state} />
        <Timeline items={state.timeline} />
      </main>
    </div>
  );
}

function LoginScreen({ onLogin }: { onLogin: (username: string, password: string) => Promise<void> }) {
  const [username, setUsername] = useState("maya.operator");
  const [password, setPassword] = useState("civicloop-demo");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onLogin(username, password);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "CivicLoop could not sign you in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <p className="eyebrow">Authenticated sandbox</p>
        <h1>Enter the LaunchLoop workspace</h1>
        <p>Use synthetic accounts only. No real nonprofit or constituent data is stored here.</p>
        {error && <div className="inline-error" role="alert">{error}</div>}
        <label>
          <span>Username</span>
          <select value={username} onChange={(event) => setUsername(event.target.value)}>
            <option value="maya.operator">Maya Chen  -  operator</option>
            <option value="jordan.approver">Jordan Brooks  -  approver</option>
          </select>
        </label>
        <label>
          <span>Demo password</span>
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required />
        </label>
        <button className="button button--primary" disabled={busy} type="submit">
          {busy ? "Signing in..." : "Sign in"}
        </button>
        <p className="login-card__hint">Temporary demo password: <code>civicloop-demo</code></p>
      </form>
    </main>
  );
}

function AuthenticatedApp() {
  const [sessionUser, setSessionUser] = useState<SessionUser | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    void requestSession().then(setSessionUser).catch(() => setSessionUser(null)).finally(() => setChecking(false));
  }, []);

  if (checking) {
    return <main className="load-state" aria-busy="true"><p className="eyebrow">Loading CivicLoop</p><h1>Checking your demo session...</h1></main>;
  }
  if (!sessionUser) {
    return <LoginScreen onLogin={async (username, password) => setSessionUser(await loginDemo(username, password))} />;
  }
  return <Workspace sessionUser={sessionUser} onLogout={() => void logoutDemo().then(() => setSessionUser(null))} />;
}

export function App() {
  if (import.meta.env.VITE_STATIC_DEMO === "true" || import.meta.env.VITEST) {
    return <Workspace />;
  }
  return <AuthenticatedApp />;
}
