import { useEffect, useRef, useState } from "react";
import { approveDraft, executeDraft, prepareDraft, readDraft, reconcileDraft, submitDraft } from "../draftApi";
import type { DraftExecution, DraftFields } from "../draftApi";

const empty: DraftFields = { title: "", summary: "", organization_id: "", start_local: "", end_local: "", timezone: "", currency: "" };
function eventDate(value: { utc: string; timezone: string }) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: value.timezone }).format(new Date(value.utc));
}
function eventName(value: string) {
  return new DOMParser().parseFromString(value, "text/html").body.textContent ?? "";
}
export function DraftExecutionReview({ runId, intentId, revisionId }: { runId: string; intentId: string; revisionId: number }) {
  const [fields, setFields] = useState<DraftFields>(empty);
  const [draft, setDraft] = useState<DraftExecution | null>(null);
  const [copy, setCopy] = useState("");
  const [busy, setBusy] = useState(true);
  const [uncertain, setUncertain] = useState(false);
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController();
    controllers.current.add(controller);
    setFields(empty); setDraft(null); setCopy(""); setBusy(true); setUncertain(false); setMessage("");
    void Promise.resolve().then(() => prepareDraft(runId, intentId, controller.signal)).then((value) => {
      if (controller.signal.aborted || current !== generation.current) return;
      if (value.execution && (value.execution.run_id !== runId || value.execution.intent_id !== intentId || value.execution.revision_id !== revisionId)) throw new Error();
      if (!value.execution && (value.intent_id !== intentId || value.revision_id !== revisionId)) throw new Error();
      setDraft(value.execution);
      setCopy(value.proposal_copy ?? "");
      setFields({ ...empty, title: value.title ?? "", timezone: value.timezone ?? "", summary: value.summary ?? "",
        start_local: value.date && value.start_time ? `${value.date}T${value.start_time}` : "",
        end_local: value.date && value.end_time ? `${value.date}T${value.end_time}` : "" });
    }).catch(() => {
      if (!controller.signal.aborted && current === generation.current) setMessage("The draft review could not be loaded.");
    }).finally(() => {
      controllers.current.delete(controller);
      if (!controller.signal.aborted && current === generation.current) setBusy(false);
    });
    return () => {
      ++generation.current;
      for (const action of controllers.current) action.abort();
      controllers.current.clear();
    };
  }, [runId, intentId, revisionId]);

  async function action(kind: "submit" | "approve" | "execute" | "refresh" | "reconcile") {
    if (busy || (uncertain && kind !== "refresh")) return;
    const controller = new AbortController();
    controllers.current.add(controller);
    const current = generation.current;
    setBusy(true); setMessage("");
    try {
      let result: DraftExecution | null;
      if (kind === "refresh" && !draft) {
        const preparation = await prepareDraft(runId, intentId, controller.signal);
        if (!preparation.execution) {
          if (preparation.intent_id !== intentId || preparation.revision_id !== revisionId) throw new Error();
          if (controller.signal.aborted || current !== generation.current) return;
          setUncertain(false);
          setMessage("");
          return;
        }
        result = preparation.execution;
      } else {
        result = kind === "submit" ? await submitDraft(runId, intentId, fields, controller.signal)
          : kind === "approve" && draft ? await approveDraft(draft, controller.signal)
            : kind === "execute" && draft ? await executeDraft(draft.operation_id, controller.signal)
              : kind === "reconcile" && draft ? await reconcileDraft(draft.operation_id, controller.signal)
                : draft ? await readDraft(draft.operation_id, controller.signal) : null;
      }
      if (controller.signal.aborted || current !== generation.current) return;
      if (!result || result.run_id !== runId || result.intent_id !== intentId || result.revision_id !== revisionId
        || (kind !== "submit" && draft && result.operation_id !== draft.operation_id)) throw new Error();
      setDraft(result); setUncertain(false);
    } catch {
      if (!controller.signal.aborted && current === generation.current) {
        if (kind !== "refresh") setUncertain(true);
        setMessage("The result could not be confirmed. Refresh the recorded status before taking another action.");
      }
    } finally {
      controllers.current.delete(controller);
      if (!controller.signal.aborted && current === generation.current) setBusy(false);
    }
  }
  const locked = busy || uncertain;
  return <section aria-label="Eventbrite owner draft review">
    <h4>Review an Eventbrite draft</h4>
    <p>Choose the organization and event details, then review the exact request. Approval and creating the draft are separate actions. This creates an unpublished draft with listing and sharing disabled.</p>
    {copy && <div><h5>Hermes event proposal</h5><p style={{ whiteSpace: "pre-wrap" }}>{copy}</p></div>}
    {!draft && <form onSubmit={(event) => { event.preventDefault(); void action("submit"); }}>
      <fieldset disabled={locked}>
        <legend>Event details</legend>
        {([ ["title", "Event name"], ["summary", "Summary (up to 140 characters)"], ["organization_id", "Eventbrite organization ID"],
          ["start_local", "Start date and time in the event timezone"], ["end_local", "End date and time in the event timezone"],
          ["timezone", "Event timezone (for example America/New_York)"], ["currency", "Currency (three uppercase letters)"] ] as const).map(([key, label]) =>
          <label key={key}>{label}<input required type={key === "start_local" || key === "end_local" ? "datetime-local" : "text"} name={key} value={fields[key]} maxLength={key === "summary" ? 140 : key === "currency" ? 3 : 200}
            onChange={(event) => setFields((previous) => ({ ...previous, [key]: event.target.value }))} /></label>)}
        <button type="submit">Prepare exact draft request</button>
      </fieldset>
    </form>}
    {draft && <>
      <dl>
        <dt>Organization</dt><dd>{draft.organization_id}</dd>
        <dt>Name</dt><dd>{eventName(draft.payload.event.name.html)}</dd>
        <dt>Summary</dt><dd>{draft.payload.event.summary}</dd>
        <dt>Start</dt><dd>{eventDate(draft.payload.event.start)} · {draft.payload.event.start.timezone}</dd>
        <dt>End</dt><dd>{eventDate(draft.payload.event.end)} · {draft.payload.event.end.timezone}</dd>
        <dt>Currency</dt><dd>{draft.payload.event.currency}</dd>
        <dt>Visibility</dt><dd>Unpublished draft; listing off; sharing off</dd>
        <dt>Review reference</dt><dd><code>{draft.review_digest}</code></dd>
      </dl>
      <p>Provider execution: {draft.status}</p>
      {draft.status === "pending" && <button disabled={locked} onClick={() => { void action("approve"); }}>Approve this exact draft request</button>}
      {draft.status === "approved" && <button disabled={locked} onClick={() => { void action("execute"); }}>Create unpublished Eventbrite draft</button>}
      {(draft.status === "unknown" || draft.status === "executing") && <p>The provider result is unconfirmed. Do not create another draft or repeat this request.</p>}
      {draft.provider_id && <p>Eventbrite event ID: <code>{draft.provider_id}</code></p>}
      {draft.status === "succeeded" && <p>Eventbrite confirmed the unpublished draft. Provider status: {draft.receipt?.provider_status}. Receipt reference: <code>{draft.receipt?.readback_digest}</code></p>}
      {draft.status === "failed" && <p>The draft creation failed. No automatic retry will be attempted.</p>}
      {(draft.status === "unknown" || draft.status === "executing") && draft.provider_id && <button disabled={locked} onClick={() => { void action("reconcile"); }}>Check the recorded Eventbrite event</button>}
    </>}
    {(draft || uncertain) && <button disabled={busy} onClick={() => { void action("refresh"); }}>Refresh recorded status</button>}
    <p role="status">{message}</p>
  </section>;
}
