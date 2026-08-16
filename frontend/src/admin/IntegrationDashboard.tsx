import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { AdminAPIError, adminAPI, INTEGRATION_PROVIDERS, type IntegrationAuditEvent, type IntegrationConnection, type IntegrationProvider, type SafeConfiguration } from "./api";
import { FreshVerificationDialog } from "./FreshVerificationDialog";
import { IntegrationDialog } from "./IntegrationDialog";
import { parseAuditPage, parseConnection, parseConnections, parseHealthCheck, providerLabel } from "./integrations";

const EMPTY_CONNECTION = (provider: IntegrationProvider): IntegrationConnection => ({ provider, state: "not_configured", capabilities: [], configuration: {}, version: 1, created_at: "", updated_at: "", credential_rotated_at: null, responsible_actor_id: null, last_successful_test_at: null, last_failure_category: null });
const stateLabel: Record<IntegrationConnection["state"], string> = { not_configured: "Not configured", configured: "Configured", healthy: "Healthy", degraded: "Degraded", disabled: "Disabled" };
const auditLabel = { credential_replaced: "Credential replaced", configuration_changed: "Configuration changed", connection_tested: "Connection tested", connection_disabled: "Connection disabled" };

function errorMessage(error: unknown): string {
  if (!(error instanceof AdminAPIError)) return "Integration service is temporarily unavailable. Try again shortly.";
  if (error.status === 401) return "Your administrator session expired. Sign in again before changing integrations.";
  if (error.status === 403 || error.code === "permission_denied") return "You do not have permission to manage this integration.";
  if (error.status === 409 || error.code === "version_conflict") return "This integration changed elsewhere. Metadata was refreshed; review it before trying again.";
  if (error.status >= 500 || error.code === "integration_unavailable") return "Integration service is temporarily unavailable. Try again shortly.";
  return "CivicLoop could not complete that integration action.";
}

function date(value: string | null): string { return value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "Not tested"; }

export function IntegrationDashboard() {
  const [connections, setConnections] = useState<IntegrationConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [freshAction, setFreshAction] = useState<{ provider: IntegrationProvider; kind: "credential" | "configuration" | "disable" } | null>(null);
  const [credentialProvider, setCredentialProvider] = useState<IntegrationProvider | null>(null);
  const [configurationProvider, setConfigurationProvider] = useState<IntegrationProvider | null>(null);
  const [disableProvider, setDisableProvider] = useState<IntegrationProvider | null>(null);
  const [historyProvider, setHistoryProvider] = useState<IntegrationProvider | null>(null);
  const [history, setHistory] = useState<IntegrationAuditEvent[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const credentialInput = useRef<HTMLInputElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const freshRequest = useRef(0);
  const historyInFlight = useRef(false);

  const load = useCallback(async (): Promise<boolean> => {
    setLoading(true); setError(null);
    try { setConnections(parseConnections(await adminAPI.integrations())); return true; }
    catch { setError("CivicLoop could not load integration connections."); return false; }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (credentialProvider) credentialInput.current?.focus(); }, [credentialProvider]);
  const getConnection = (provider: IntegrationProvider) => connections.find((item) => item.provider === provider) ?? EMPTY_CONNECTION(provider);
  const update = (value: IntegrationConnection) => setConnections((items) => [...items.filter((item) => item.provider !== value.provider), value]);

  async function run(operation: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await operation(); } catch (caught) {
      if (caught instanceof AdminAPIError && caught.status === 409) {
        const refreshed = await load();
        setError(refreshed ? errorMessage(caught) : "Integration metadata could not be refreshed. Try again shortly.");
      } else setError(errorMessage(caught));
    } finally { setBusy(false); }
  }
  function begin(provider: IntegrationProvider, kind: "credential" | "configuration" | "disable", trigger: HTMLElement) {
    freshRequest.current += 1; returnFocus.current = trigger; setFreshAction({ provider, kind });
  }
  function closeDialog() {
    setCredentialProvider(null); setConfigurationProvider(null); setDisableProvider(null);
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  }
  async function verifyFresh(password: string, token: string) {
    const request = freshRequest.current;
    await run(async () => {
      await adminAPI.reauthenticate(password, token);
      if (request !== freshRequest.current) return;
      const action = freshAction;
      setFreshAction(null);
      if (!action) return;
      if (action.kind === "credential") { setCredentialProvider(action.provider); return; }
      if (action.kind === "configuration") { setConfigurationProvider(action.provider); return; }
      setDisableProvider(action.provider); return;
      window.setTimeout(() => returnFocus.current?.focus(), 0);
    });
  }
  async function saveCredential(event: FormEvent) {
    event.preventDefault();
    const provider = credentialProvider;
    const input = credentialInput.current;
    if (!provider || !input) return;
    const credential = input.value;
    input.value = "";
    setCredentialProvider(null);
    await run(async () => {
      const value = parseConnection(await adminAPI.replaceIntegrationCredential(provider, { credential, expected_version: getConnection(provider).version }));
      if (!value) throw new Error("invalid response");
      update(value);
      setNotice(`${providerLabel[provider]} credential replaced.`);
    });
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  }
  async function saveConfiguration(event: FormEvent) {
    event.preventDefault(); const provider = configurationProvider; if (!provider) return;
    const form = event.currentTarget as HTMLFormElement; const current = getConnection(provider);
    const configuration: SafeConfiguration = provider === "eventbrite" ? {} : provider === "iterable" ? { region: new FormData(form).get("region") === "eu" ? "eu" : "us" } : { model: "openai/gpt-oss-20b" };
    setConfigurationProvider(null);
    await run(async () => { const value = parseConnection(await adminAPI.updateIntegrationConfiguration(provider, { configuration, expected_version: current.version })); if (!value) throw new Error("invalid response"); update(value); setNotice(`${providerLabel[provider]} configuration updated.`); });
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  }
  async function confirmDisable() {
    const provider = disableProvider; if (!provider) return; setDisableProvider(null);
    await run(async () => { const value = parseConnection(await adminAPI.disableIntegration(provider, { expected_version: getConnection(provider).version })); if (!value) throw new Error("invalid response"); update(value); setNotice(`${providerLabel[provider]} disabled.`); });
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  }
  async function testConnection(provider: IntegrationProvider) {
    await run(async () => {
      const result = parseHealthCheck(await adminAPI.testIntegration(provider, { expected_version: getConnection(provider).version }));
      if (!result || result.provider !== provider) throw new Error("invalid response");
      setConnections((items) => items.map((item) => item.provider === provider ? { ...item, state: result.outcome, last_successful_test_at: result.outcome === "healthy" ? result.tested_at : item.last_successful_test_at, last_failure_category: result.error_category } : item));
      setNotice(`${providerLabel[provider]} connection test ${result.outcome}.`);
    });
  }
  async function loadHistory(provider: IntegrationProvider) {
    setHistoryProvider(provider); setHistory([]); setHistoryCursor(null); setBusy(true); setError(null);
    try { const page = parseAuditPage(await adminAPI.integrationAudit(provider)); if (!page) throw new Error("invalid response"); setHistory(page.events); setHistoryCursor(page.next_cursor); }
    catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }
  async function loadOlderHistory() {
    if (!historyProvider || !historyCursor || historyInFlight.current) return;
    historyInFlight.current = true;
    setBusy(true); setError(null);
    try { const page = parseAuditPage(await adminAPI.integrationAudit(historyProvider, historyCursor)); if (!page) throw new Error("invalid response"); setHistory((items) => [...items, ...page.events.filter((event) => !items.some((item) => item.correlation_id === event.correlation_id))]); setHistoryCursor(page.next_cursor); }
    catch (caught) { setError(errorMessage(caught)); } finally { historyInFlight.current = false; setBusy(false); }
  }

  return <div className="integrations-dashboard">
    <header className="integrations-heading"><div><p className="admin-eyebrow">Connected services</p><h1>Integration connections</h1><p>Manage redacted connection metadata. Credentials are entered only after fresh verification and are never shown again.</p></div><button className="admin-button admin-button--secondary" disabled={loading || busy} onClick={() => void load()} type="button">Refresh</button></header>
    {error && <div className="admin-error" role="alert">{error}</div>}{notice && <div className="security-notice" role="status">{notice}</div>}
    {loading ? <section className="security-panel" aria-busy="true"><h2>Loading integration connections…</h2></section> : <div className="integration-grid">{INTEGRATION_PROVIDERS.map((provider) => {
      const item = getConnection(provider);
      return <article className="integration-card" key={provider}><div className="integration-card__heading"><div><h2>{providerLabel[provider]}</h2><p>{provider === "eventbrite" ? "Event publishing" : provider === "iterable" ? "Audience messaging" : "AI inference and evaluation"} · Last test: {date(item.last_successful_test_at)}</p></div><span className={`integration-state integration-state--${item.state}`}><span aria-hidden="true">{item.state === "healthy" ? "●" : item.state === "degraded" ? "!" : "○"}</span> {stateLabel[item.state]}</span></div><dl className="integration-metadata"><div><dt>Version</dt><dd>{item.version}</dd></div><div><dt>Credential rotation</dt><dd>{date(item.credential_rotated_at)}</dd></div><div><dt>Capabilities</dt><dd>{item.capabilities.length ? item.capabilities.map((capability) => capability.replaceAll("_", " ")).join(", ") : "None configured"}</dd></div><div><dt>Configuration</dt><dd>{item.configuration.region ?? "No region"}{item.configuration.model ? ` · ${item.configuration.model}` : ""}</dd></div>{item.last_failure_category && <div><dt>Last failure</dt><dd>{item.last_failure_category.replaceAll("_", " ")}</dd></div>}</dl><div className="integration-actions"><button className="admin-button admin-button--primary" disabled={busy} onClick={(event) => begin(provider, "credential", event.currentTarget)} type="button">Replace credential for {providerLabel[provider]}</button><button className="admin-button admin-button--secondary" disabled={busy} onClick={(event) => begin(provider, "configuration", event.currentTarget)} type="button">Update configuration</button><button className="admin-button admin-button--secondary" disabled={busy} onClick={() => void testConnection(provider)} type="button">Test connection (no external changes)</button><button className="admin-button admin-button--text" disabled={busy} onClick={() => void loadHistory(provider)} type="button">View {providerLabel[provider]} history</button>{item.state !== "disabled" && <button className="admin-button admin-button--danger" disabled={busy} onClick={(event) => begin(provider, "disable", event.currentTarget)} type="button">Disable connection</button>}</div></article>;
    })}</div>}
    {credentialProvider && <IntegrationDialog busy={busy} description="This write-only value is submitted once and is never displayed again." onClose={() => { credentialInput.current && (credentialInput.current.value = ""); closeDialog(); }} title={`Replace ${providerLabel[credentialProvider]} credential`}><form className="admin-form" onSubmit={(event) => void saveCredential(event)}><label htmlFor="integration-credential">Credential for {providerLabel[credentialProvider]}</label><input ref={credentialInput} id="integration-credential" autoComplete="off" type="password" required /><div className="admin-actions"><button className="admin-button admin-button--secondary" disabled={busy} onClick={() => { credentialInput.current && (credentialInput.current.value = ""); closeDialog(); }} type="button">Cancel</button><button className="admin-button admin-button--primary" disabled={busy} type="submit">Save credential</button></div></form></IntegrationDialog>}
    {configurationProvider && <IntegrationDialog busy={busy} description="Only the bounded, non-secret settings for this provider can be changed." onClose={closeDialog} title={"Configure " + providerLabel[configurationProvider]}><form className="admin-form" onSubmit={(event) => void saveConfiguration(event)}>{configurationProvider === "eventbrite" ? <p>Eventbrite has no configurable non-secret settings.</p> : configurationProvider === "iterable" ? <><label htmlFor="integration-region">Region</label><select defaultValue={getConnection(configurationProvider).configuration.region ?? "us"} id="integration-region" name="region"><option value="us">US</option><option value="eu">EU</option></select></> : <><label htmlFor="integration-model">Model</label><select id="integration-model" name="model"><option value="openai/gpt-oss-20b">openai/gpt-oss-20b</option></select></>}<div className="admin-actions"><button className="admin-button admin-button--secondary" disabled={busy} onClick={closeDialog} type="button">Cancel</button><button className="admin-button admin-button--primary" disabled={busy} type="submit">Save configuration</button></div></form></IntegrationDialog>}
    {disableProvider && <IntegrationDialog busy={busy} description="Disabling stops this connection but preserves its redacted audit history." onClose={closeDialog} title={`Disable ${providerLabel[disableProvider]} connection`}><div className="admin-actions"><button className="admin-button admin-button--secondary" disabled={busy} onClick={closeDialog} type="button">Cancel</button><button className="admin-button admin-button--danger" disabled={busy} onClick={() => void confirmDisable()} type="button">Confirm disable</button></div></IntegrationDialog>}
    {historyProvider && <section className="security-panel integration-history" aria-labelledby="history-title"><div className="security-section-heading"><div><p className="admin-eyebrow">Redacted audit history</p><h2 id="history-title">{providerLabel[historyProvider]} history</h2></div><button className="admin-button admin-button--text" onClick={() => setHistoryProvider(null)} type="button">Close history</button></div>{busy ? <p aria-busy="true">Loading history…</p> : history.length ? <><ol className="security-event-list">{history.map((item) => <li key={item.correlation_id}><strong>{auditLabel[item.action]}</strong><span>{item.outcome} · version {item.version} · actor {item.actor_id ?? "unavailable"} · failure {item.failure_category ?? "none"} · correlation {item.correlation_id} · {date(item.created_at)}</span></li>)}</ol>{historyCursor && <button className="admin-button admin-button--secondary" disabled={busy} onClick={() => void loadOlderHistory()} type="button">Load older history</button>}</> : <p className="security-empty">No permitted audit events are available.</p>}</section>}
    {freshAction && <FreshVerificationDialog busy={busy} onCancel={() => { freshRequest.current += 1; setFreshAction(null); window.setTimeout(() => returnFocus.current?.focus(), 0); }} onSubmit={verifyFresh} />}
  </div>;
}
