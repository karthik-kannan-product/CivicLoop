import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { AdminAPIError, adminAPI, INTEGRATION_PROVIDERS, type IntegrationConnection, type IntegrationProvider, type SafeConfiguration } from "./api";
import { FreshVerificationDialog } from "./FreshVerificationDialog";
import { parseAuditEvents, parseConnections, providerLabel } from "./integrations";

const EMPTY_CONNECTION = (provider: IntegrationProvider): IntegrationConnection => ({ provider, state: "not_configured", capabilities: [], configuration: {}, version: 1, created_at: "", updated_at: "", last_successful_test_at: null, last_failure_category: null });
const stateLabel: Record<IntegrationConnection["state"], string> = { not_configured: "Not configured", configured: "Configured", healthy: "Healthy", degraded: "Degraded", disabled: "Disabled" };
const auditLabel = { credential_replaced: "Credential replaced", configuration_changed: "Configuration changed", connection_tested: "Connection tested", connection_disabled: "Connection disabled" };

function errorMessage(error: unknown): string {
  if (error instanceof AdminAPIError) return error.status === 401 ? "Your session expired. Sign in again before changing integrations." : error.message;
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
  const [historyProvider, setHistoryProvider] = useState<IntegrationProvider | null>(null);
  const [history, setHistory] = useState<ReturnType<typeof parseAuditEvents>>([]);
  const credentialInput = useRef<HTMLInputElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setConnections(parseConnections(await adminAPI.integrations())); }
    catch { setError("CivicLoop could not load integration connections."); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (credentialProvider) credentialInput.current?.focus(); }, [credentialProvider]);
  const getConnection = (provider: IntegrationProvider) => connections.find((item) => item.provider === provider) ?? EMPTY_CONNECTION(provider);
  const update = (value: IntegrationConnection) => setConnections((items) => [...items.filter((item) => item.provider !== value.provider), value]);

  async function run(operation: () => Promise<void>) {
    setBusy(true); setError(null); setNotice(null);
    try { await operation(); } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }
  function begin(provider: IntegrationProvider, kind: "credential" | "configuration" | "disable", trigger: HTMLElement) {
    returnFocus.current = trigger; setFreshAction({ provider, kind });
  }
  async function verifyFresh(password: string, token: string) {
    await run(async () => {
      await adminAPI.reauthenticate(password, token);
      const action = freshAction;
      setFreshAction(null);
      if (!action) return;
      if (action.kind === "credential") { setCredentialProvider(action.provider); return; }
      const current = getConnection(action.provider);
      if (action.kind === "configuration") {
        const configuration: SafeConfiguration = current.configuration.region ? { region: current.configuration.region === "us" ? "eu" : "us" } : { region: "us" };
        update(await adminAPI.updateIntegrationConfiguration(action.provider, { configuration, expected_version: current.version }));
        setNotice(`${providerLabel[action.provider]} configuration updated.`);
      } else {
        update(await adminAPI.disableIntegration(action.provider, { expected_version: current.version }));
        setNotice(`${providerLabel[action.provider]} disabled.`);
      }
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
      update(await adminAPI.replaceIntegrationCredential(provider, { credential, expected_version: getConnection(provider).version }));
      setNotice(`${providerLabel[provider]} credential replaced.`);
    });
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  }
  async function testConnection(provider: IntegrationProvider) {
    await run(async () => {
      const result = await adminAPI.testIntegration(provider, { expected_version: getConnection(provider).version });
      setConnections((items) => items.map((item) => item.provider === provider ? { ...item, state: result.outcome, last_successful_test_at: result.outcome === "healthy" ? result.tested_at : item.last_successful_test_at, last_failure_category: result.error_category } : item));
      setNotice(`${providerLabel[provider]} connection test ${result.outcome}.`);
    });
  }
  async function loadHistory(provider: IntegrationProvider) {
    setHistoryProvider(provider); setHistory([]); setBusy(true); setError(null);
    try { const page = await adminAPI.integrationAudit(provider); setHistory(parseAuditEvents(page.events)); }
    catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }

  return <div className="integrations-dashboard">
    <header className="integrations-heading"><div><p className="admin-eyebrow">Connected services</p><h1>Integration connections</h1><p>Manage redacted connection metadata. Credentials are entered only after fresh verification and are never shown again.</p></div><button className="admin-button admin-button--secondary" disabled={loading || busy} onClick={() => void load()} type="button">Refresh</button></header>
    {error && <div className="admin-error" role="alert">{error}</div>}{notice && <div className="security-notice" role="status">{notice}</div>}
    {loading ? <section className="security-panel" aria-busy="true"><h2>Loading integration connections…</h2></section> : <div className="integration-grid">{INTEGRATION_PROVIDERS.map((provider) => {
      const item = getConnection(provider);
      return <article className="integration-card" key={provider}><div className="integration-card__heading"><div><h2>{providerLabel[provider]}</h2><p>Last test: {date(item.last_successful_test_at)}</p></div><span className={`integration-state integration-state--${item.state}`}>{stateLabel[item.state]}</span></div><dl className="integration-metadata"><div><dt>Capabilities</dt><dd>{item.capabilities.length ? item.capabilities.map((capability) => capability.replaceAll("_", " ")).join(", ") : "None configured"}</dd></div><div><dt>Configuration</dt><dd>{item.configuration.region ?? "No region"}{item.configuration.model ? ` · ${item.configuration.model}` : ""}</dd></div>{item.last_failure_category && <div><dt>Last failure</dt><dd>{item.last_failure_category.replaceAll("_", " ")}</dd></div>}</dl><div className="integration-actions"><button className="admin-button admin-button--primary" disabled={busy} onClick={(event) => begin(provider, "credential", event.currentTarget)} type="button">Replace credential for {providerLabel[provider]}</button><button className="admin-button admin-button--secondary" disabled={busy} onClick={(event) => begin(provider, "configuration", event.currentTarget)} type="button">Update configuration</button><button className="admin-button admin-button--secondary" disabled={busy} onClick={() => void testConnection(provider)} type="button">Test connection</button><button className="admin-button admin-button--text" disabled={busy} onClick={() => void loadHistory(provider)} type="button">View {providerLabel[provider]} history</button>{item.state !== "disabled" && <button className="admin-button admin-button--danger" disabled={busy} onClick={(event) => begin(provider, "disable", event.currentTarget)} type="button">Disable connection</button>}</div></article>;
    })}</div>}
    {credentialProvider && <section className="security-panel integration-credential" aria-labelledby="credential-title"><p className="admin-eyebrow">Write-only credential</p><h2 id="credential-title">Replace {providerLabel[credentialProvider]} credential</h2><p>This value is submitted once and is not stored, replayed, or displayed.</p><form className="admin-form" onSubmit={(event) => void saveCredential(event)}><label htmlFor="integration-credential">Credential for {providerLabel[credentialProvider]}</label><input ref={credentialInput} id="integration-credential" autoComplete="off" type="password" required /><div className="admin-actions"><button className="admin-button admin-button--secondary" onClick={() => { credentialInput.current && (credentialInput.current.value = ""); setCredentialProvider(null); window.setTimeout(() => returnFocus.current?.focus(), 0); }} type="button">Cancel</button><button className="admin-button admin-button--primary" disabled={busy} type="submit">Save credential</button></div></form></section>}
    {historyProvider && <section className="security-panel integration-history" aria-labelledby="history-title"><div className="security-section-heading"><div><p className="admin-eyebrow">Redacted audit history</p><h2 id="history-title">{providerLabel[historyProvider]} history</h2></div><button className="admin-button admin-button--text" onClick={() => setHistoryProvider(null)} type="button">Close history</button></div>{busy ? <p aria-busy="true">Loading history…</p> : history.length ? <ol className="security-event-list">{history.map((item) => <li key={item.correlation_id}><strong>{auditLabel[item.action]}</strong><span>{item.outcome} · {date(item.created_at)}</span></li>)}</ol> : <p className="security-empty">No permitted audit events are available.</p>}</section>}
    {freshAction && <FreshVerificationDialog busy={busy} onCancel={() => { setFreshAction(null); window.setTimeout(() => returnFocus.current?.focus(), 0); }} onSubmit={verifyFresh} />}
  </div>;
}
