import { useEffect, useRef, useState } from "react";
import { approveIterable, executeIterable, prepareIterable, reconcileIterable, submitCampaign, submitTemplate } from "../iterableDraftApi";
import type { IterableExecution, IterablePreparation, Sender } from "../iterableDraftApi";

function ids(value: string, required: boolean) {
  if (!value.trim() && !required) return [];
  const parts = value.split(",").map((part) => part.trim());
  if (!parts.length || parts.some((part) => !/^[1-9][0-9]*$/.test(part))) throw new Error();
  const values = parts.map(Number);
  if (values.some((value) => !Number.isSafeInteger(value)) || new Set(values).size !== values.length) throw new Error();
  return values;
}
export function IterableExecutionReview({ runId, intentId, revisionId }: { runId: string; intentId: string; revisionId: number }) {
  const [review, setReview] = useState<IterablePreparation | null>(null);
  const [sender, setSender] = useState<Sender>({ fromEmail: "", fromName: "", replyToEmail: "", messageTypeId: 0 });
  const [region, setRegion] = useState<"us" | "eu">("us");
  const [name, setName] = useState("");
  const [audience, setAudience] = useState("");
  const [suppression, setSuppression] = useState("");
  const [busy, setBusy] = useState(true);
  const [uncertain, setUncertain] = useState(false);
  const [message, setMessage] = useState("");
  const generation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  function validate(value: IterablePreparation) {
    if (value.revision_id !== revisionId) throw new Error();
    for (const item of [value.template_execution, value.campaign_execution]) {
      if (item && (item.run_id !== runId || item.intent_id !== intentId || item.revision_id !== revisionId)) throw new Error();
    }
    if (value.template_execution && value.template_execution.step !== "template") throw new Error();
    if (value.campaign_execution && (value.campaign_execution.step !== "campaign" || value.campaign_execution.payload.scheduleSend !== false)) throw new Error();
    return value;
  }
  useEffect(() => {
    const current = ++generation.current;
    const controller = new AbortController(); controllers.current.add(controller);
    setReview(null); setBusy(true); setUncertain(false); setMessage(""); setName(""); setAudience(""); setSuppression("");
    setSender({ fromEmail: "", fromName: "", replyToEmail: "", messageTypeId: 0 }); setRegion("us");
    void prepareIterable(runId, intentId, controller.signal).then((value) => {
      if (!controller.signal.aborted && current === generation.current) setReview(validate(value));
    }).catch(() => { if (!controller.signal.aborted && current === generation.current) setMessage("The Iterable review could not be loaded."); })
      .finally(() => { controllers.current.delete(controller); if (!controller.signal.aborted && current === generation.current) setBusy(false); });
    return () => { ++generation.current; for (const action of controllers.current) action.abort(); controllers.current.clear(); };
  }, [runId, intentId, revisionId]);

  async function action(kind: "template" | "campaign" | "approve" | "execute" | "reconcile" | "refresh", operation?: IterableExecution) {
    if (busy || (uncertain && kind !== "refresh")) return;
    let lists: number[] = [], suppressions: number[] = [];
    if (kind === "campaign") {
      try { lists = ids(audience, true); suppressions = ids(suppression, false); }
      catch { setMessage("Enter positive list IDs separated by commas, with no duplicates."); return; }
    }
    const current = generation.current;
    const controller = new AbortController(); controllers.current.add(controller);
    setBusy(true); setMessage("");
    try {
      if (kind === "refresh") {
        const value = await prepareIterable(runId, intentId, controller.signal);
        if (controller.signal.aborted || current !== generation.current) return;
        setReview(validate(value)); setUncertain(false); return;
      }
      const result = kind === "template" ? await submitTemplate(runId, intentId, sender, region, controller.signal)
        : kind === "campaign" ? await submitCampaign(runId, intentId, { name, listIds: lists, suppressionListIds: suppressions }, controller.signal)
          : operation && kind === "approve" ? await approveIterable(operation, controller.signal)
            : operation && kind === "execute" ? await executeIterable(operation, controller.signal)
              : operation && kind === "reconcile" ? await reconcileIterable(operation, controller.signal) : null;
      if (controller.signal.aborted || current !== generation.current) return;
      if (!result || !review || (operation && (result.operation_id !== operation.operation_id || result.step !== operation.step))) throw new Error();
      const value = { ...review, [result.step === "template" ? "template_execution" : "campaign_execution"]: result };
      setReview(validate(value)); setUncertain(false);
    } catch {
      if (!controller.signal.aborted && current === generation.current) { setUncertain(true); setMessage("The result could not be confirmed. Refresh the recorded status before another action."); }
    } finally {
      controllers.current.delete(controller); if (!controller.signal.aborted && current === generation.current) setBusy(false);
    }
  }
  const locked = busy || uncertain;
  function execution(operation: IterableExecution) {
    const template = operation.step === "template";
    return <div key={operation.operation_id}>
      <h5>{template ? "Exact template request" : "Exact campaign request"}</h5>
      {template ? <dl><dt>Sender</dt><dd>{operation.payload.fromName} &lt;{operation.payload.fromEmail}&gt;</dd><dt>Reply to</dt><dd>{operation.payload.replyToEmail}</dd><dt>Message type ID</dt><dd>{operation.payload.messageTypeId}</dd><dt>Subject</dt><dd>{operation.payload.subject}</dd><dt>Message</dt><dd style={{ whiteSpace: "pre-wrap" }}>{operation.payload.plainText}</dd><dt>Client template ID</dt><dd>{operation.payload.clientTemplateId}</dd></dl>
        : <dl><dt>Campaign name</dt><dd>{operation.payload.name}</dd><dt>Created template ID</dt><dd>{operation.payload.templateId}</dd><dt>Template content hash</dt><dd><code>{operation.provider_configuration.expected_template_digest}</code></dd><dt>Audience list IDs</dt><dd>{operation.payload.listIds.join(", ")}</dd><dt>Suppression list IDs</dt><dd>{operation.payload.suppressionListIds.join(", ") || "None selected"}</dd><dt>Schedule send</dt><dd>false — unscheduled and unactivated</dd></dl>}
      <p>Region: {operation.provider_configuration.region.toUpperCase()}. Durable {operation.step} phase: {operation.status}</p>
      <p>Review reference: <code>{operation.review_digest}</code></p>
      {operation.status === "pending" && <button disabled={locked} onClick={() => { void action("approve", operation); }}>Approve this exact {operation.step} request</button>}
      {operation.status === "approved" && <button disabled={locked} onClick={() => { void action("execute", operation); }}>{template ? "Create reviewed Iterable template" : "Create unscheduled Iterable campaign"}</button>}
      {(operation.status === "unknown" || operation.status === "executing") && <><p>The provider result is unconfirmed. Do not repeat the creation request.</p>{(template || operation.provider_id) && <button disabled={locked} onClick={() => { void action("reconcile", operation); }}>Check recorded {operation.step} with a read only lookup</button>}</>}
      {operation.provider_id && <p>Iterable {operation.step} ID: <code>{operation.provider_id}</code></p>}
      {operation.status === "succeeded" && <p>{template ? "Template content confirmed. Campaign creation is a separate reviewed step." : "Actual Iterable campaign confirmed unscheduled and unactivated."} Provider status: {operation.receipt?.provider_status}. Receipt: <code>{operation.receipt?.readback_digest}</code></p>}
      {operation.status === "failed" && <p>The {operation.step} creation failed. No automatic retry will be attempted.</p>}
    </div>;
  }
  return <section aria-label="Iterable owner draft review">
    <h4>Review an Iterable campaign</h4>
    <p>First approve the Hermes message and your account sender details. After the template is confirmed, separately approve the campaign and existing audience and suppression lists. Campaign creation always uses scheduleSend=false.</p>
    {review && <>
      {!review.template_execution && <><h5>Hermes {review.kind}</h5><p>{review.subject}</p><p style={{ whiteSpace: "pre-wrap" }}>{review.body}</p>
        <form onSubmit={(event) => { event.preventDefault(); void action("template"); }}><fieldset disabled={locked}><legend>Account sender details</legend>
          {([ ["fromName", "Sender display name"], ["fromEmail", "Sender email"], ["replyToEmail", "Reply to email"] ] as const).map(([key, label]) => <label key={key}>{label}<input required type={key === "fromName" ? "text" : "email"} maxLength={key === "fromName" ? 240 : 254} value={sender[key]} onChange={(event) => setSender({ ...sender, [key]: event.target.value })} /></label>)}
          <label>Account message type ID<input required type="number" min="1" max="2147483647" step="1" value={sender.messageTypeId || ""} onChange={(event) => setSender({ ...sender, messageTypeId: Number(event.target.value) })} /></label>
          <label>Account region<select value={region} onChange={(event) => setRegion(event.target.value as "us" | "eu")}><option value="us">US</option><option value="eu">EU</option></select></label>
          <button type="submit">Prepare exact template request</button></fieldset></form></>}
      {review.template_execution && execution(review.template_execution)}
      {review.template_execution?.status === "succeeded" && !review.campaign_execution && <form onSubmit={(event) => { event.preventDefault(); void action("campaign"); }}><fieldset disabled={locked}><legend>Existing account audience</legend>
        <label>Campaign name<input required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Audience list IDs (comma separated)<input required value={audience} onChange={(event) => setAudience(event.target.value)} /></label>
        <label>Suppression list IDs (comma separated)<input value={suppression} onChange={(event) => setSuppression(event.target.value)} /></label>
        <p>Use existing list IDs approved for this message. This flow does not create lists or export recipients.</p><button type="submit">Prepare exact unscheduled campaign request</button></fieldset></form>}
      {review.campaign_execution && execution(review.campaign_execution)}
    </>}
    <button disabled={busy} onClick={() => { void action("refresh"); }}>Refresh recorded Iterable status</button>
    <p role="status">{message}</p>
  </section>;
}
