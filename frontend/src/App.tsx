import { useCallback, useEffect, useState } from "react";

import { requestDemo } from "./api";
import { CompletionPanel } from "./components/CompletionPanel";
import { DecisionPanel } from "./components/DecisionPanel";
import { EventBrief } from "./components/EventBrief";
import { LaneBoard } from "./components/LaneBoard";
import { ReviewPackage } from "./components/ReviewPackage";
import { Timeline } from "./components/Timeline";
import { WorkspaceHeader } from "./components/WorkspaceHeader";
import type { DemoState } from "./types";

export function App() {
  const [state, setState] = useState<DemoState | null>(null);
  const [actor, setActor] = useState("maya");
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
      setState(
        await requestDemo(path, {
          actor,
          body,
          method: "POST",
        }),
      );
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
        <h1>Preparing the LaunchLoop workspace…</h1>
      </main>
    );
  }

  const workflowId = state.workflow.id;
  const activeActor = state.actors.find((item) => item.slug === actor);

  return (
    <div className="app-shell">
      <WorkspaceHeader
        actors={state.actors}
        actor={actor}
        busy={busy}
        deploymentMode={state.deployment_mode}
        onActorChange={setActor}
        onReset={() => void mutate("/api/v1/demo/reset")}
      />
      <main className="workspace">
        {error && (
          <div className="inline-error" role="alert">
            {error}
          </div>
        )}
        <EventBrief
          state={state}
          isOperator={activeActor?.role === "operator"}
          busy={busy}
          onRun={() => void mutate(`/api/v1/workflows/${workflowId}/runs`)}
          onResolve={(answers) =>
            void mutate(`/api/v1/workflows/${workflowId}/answers`, answers)
          }
        />
        <LaneBoard campaignPackage={state.workflow.package} />
        {state.workflow.package && (
          <ReviewPackage campaignPackage={state.workflow.package} />
        )}
        <DecisionPanel
          state={state}
          actor={actor}
          busy={busy}
          onSubmit={() => void mutate(`/api/v1/workflows/${workflowId}/submit`)}
          onApprove={() =>
            void mutate(`/api/v1/approvals/${state.approval?.id}/decision`, {
              decision: "approve",
              package_hash: state.approval?.package_hash ?? "",
            })
          }
        />
        <CompletionPanel state={state} />
        <Timeline items={state.timeline} />
      </main>
    </div>
  );
}
