import { useEffect, useId, useRef, useState } from "react";
import { cancelHermesRun, getHermesRun, getPendingOperations, startHermesRun } from "../api";
import type { HermesRunStatus, PendingOperation } from "../types";
import { PendingOperationsPanel } from "./PendingOperationsPanel";

type Props = {
  workflowId: string;
  revisionId: number;
  authorized: boolean;
  enabled: boolean;
  ready: boolean;
};

const fallback = "Hermes is unavailable. You can continue with the deterministic workflow.";
const failureMessages: Record<string, string> = {
  budget_exhausted: "Hermes reached its run budget.",
  cancelled: "The Hermes run was cancelled.",
  dependency_unavailable: fallback,
  invalid_output: "Hermes could not produce a valid proposal.",
  provider_unavailable: fallback,
  timeout: "The Hermes run reached its time limit.",
};
function terminal(status?: string) {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

export function HermesRunPanel({ workflowId, revisionId, authorized, enabled, ready }: Props) {
  const titleId = useId();
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<HermesRunStatus | null>(null);
  const [operations, setOperations] = useState<PendingOperation[]>([]);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [message, setMessage] = useState("");
  const intentKey = useRef<string | null>(null);
  const latestStatus = useRef<HermesRunStatus | null>(null);
  const generation = useRef(0);
  const actions = useRef(new Set<AbortController>());
  const available = authorized && enabled && ready;
  const finished = terminal(status?.status);

  useEffect(() => {
    generation.current += 1;
    intentKey.current = null;
    latestStatus.current = null;
    setRunId(null);
    setStatus(null);
    setOperations([]);
    setStarting(false);
    setCancelling(false);
    setMessage("");
    return () => {
      generation.current += 1;
      for (const controller of actions.current) controller.abort();
      actions.current.clear();
    };
  }, [workflowId, revisionId, authorized, enabled, ready]);

  useEffect(() => {
    if (!runId || !available || finished) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    const deadline = setTimeout(() => {
      controller.abort();
      if (timer) clearTimeout(timer);
      setMessage("Status polling paused after three minutes. The run may still be active.");
    }, 180_000);
    async function poll() {
      attempts += 1;
      try {
        const next = await getHermesRun(runId!, controller.signal);
        if (controller.signal.aborted) return;
        if (terminal(latestStatus.current?.status)) return;
        if (next.pending_operation_count > 0) {
          const pending = await getPendingOperations(runId!, controller.signal);
          if (controller.signal.aborted) return;
          setOperations(pending);
        } else {
          setOperations([]);
        }
        // Publish a terminal status only after loading its pending intents: the
        // status change cleans up this polling effect and aborts its requests.
        if (terminal(latestStatus.current?.status)) return;
        latestStatus.current = next;
        setStatus(next);
        setMessage("");
        if (terminal(next.status)) {
          intentKey.current = null;
          clearTimeout(deadline);
          return;
        }
      } catch {
        if (controller.signal.aborted) return;
        setMessage(fallback);
      }
      if (!controller.signal.aborted && attempts < 90) {
        timer = setTimeout(() => { void poll(); }, 2_000);
      } else if (!controller.signal.aborted) {
        clearTimeout(deadline);
        setMessage("Status polling paused. The run may still be active.");
      }
    }
    void poll();
    return () => {
      controller.abort();
      clearTimeout(deadline);
      if (timer) clearTimeout(timer);
    };
  }, [runId, available, finished]);

  async function start() {
    if (!available || starting || (runId && !finished)) return;
    // A new start supersedes any actions belonging to the previous run. Keep
    // the intent key for uncertain-start retries, but invalidate old receipts.
    const current = ++generation.current;
    for (const previous of actions.current) previous.abort();
    actions.current.clear();
    const controller = new AbortController();
    actions.current.add(controller);
    setStarting(true);
    setCancelling(false);
    setMessage("");
    try {
      intentKey.current ??= crypto.randomUUID();
      const receipt = await startHermesRun(workflowId, revisionId, intentKey.current, controller.signal);
      if (controller.signal.aborted || current !== generation.current) return;
      setStatus(null);
      latestStatus.current = null;
      setOperations([]);
      setRunId(receipt.run_id);
    } catch {
      if (!controller.signal.aborted && current === generation.current) setMessage(fallback);
    } finally {
      actions.current.delete(controller);
      if (!controller.signal.aborted && current === generation.current) setStarting(false);
    }
  }

  async function cancel() {
    if (!runId || finished || cancelling || !available) return;
    const controller = new AbortController();
    actions.current.add(controller);
    const current = generation.current;
    setCancelling(true);
    try {
      const next = await cancelHermesRun(runId, controller.signal);
      if (controller.signal.aborted || current !== generation.current) return;
      if (terminal(next.status) && next.pending_operation_count > 0) {
        const pending = await getPendingOperations(runId, controller.signal);
        if (controller.signal.aborted || current !== generation.current) return;
        setOperations(pending);
      }
      const effective = terminal(latestStatus.current?.status) ? latestStatus.current! : next;
      latestStatus.current = effective;
      setStatus(effective);
      setMessage(terminal(effective.status) ? "" : "Cancellation requested. Waiting for the run to finish.");
      if (terminal(effective.status)) intentKey.current = null;
    } catch {
      if (!controller.signal.aborted && current === generation.current) setMessage("Cancellation could not be confirmed. Status polling continues.");
    } finally {
      actions.current.delete(controller);
      if (!controller.signal.aborted && current === generation.current) setCancelling(false);
    }
  }

  const unavailableReason = !authorized ? "Hermes is available to the owner operator only."
    : !enabled ? "Hermes is currently disabled."
      : !ready ? "Complete the current event revision before starting Hermes." : "";
  const failure = status?.failure_category;
  const failureMessage = failure ? (Object.hasOwn(failureMessages, failure) ? failureMessages[failure] : "The Hermes run could not finish.") : "";
  if (!authorized) return null;
  return (
    <section className="review-package" aria-labelledby={titleId}>
      <div className="section-heading">
        <div><p className="eyebrow">Optional generation</p><h2 id={titleId}>Generate with Hermes</h2></div>
        <button type="button" onClick={() => { void start(); }} disabled={!available || starting || Boolean(runId && !finished)}>
          {starting ? "Starting Hermes…" : "Generate with Hermes"}
        </button>
      </div>
      <p>Hermes prepares proposals and pending draft intents for review.</p>
      {unavailableReason && <p>{unavailableReason}</p>}
      <div role="status" aria-live="polite">
        {runId && <p>Run reference <code>{runId}</code></p>}
        {runId && <p>Hermes status: {status?.status ?? "queued"}</p>}
        {status && <p>{status.proposal_count} proposals · {status.pending_operation_count} pending operations</p>}
        {failureMessage && <p>{failureMessage}</p>}
        {message && <p>{message}</p>}
      </div>
      {runId && !finished && available && <button type="button" onClick={() => { void cancel(); }} disabled={cancelling || status?.cancel_requested}>
        {cancelling || status?.cancel_requested ? "Cancellation requested" : "Cancel Hermes run"}
      </button>}
      {runId && <PendingOperationsPanel operations={operations} runId={runId} revisionId={revisionId} ownerReview={available && status?.status === "succeeded"} />}
    </section>
  );
}
