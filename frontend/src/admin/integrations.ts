import {
  INTEGRATION_PROVIDERS,
  type IntegrationAuditEvent,
  type IntegrationCapability,
  type IntegrationConnection,
  type IntegrationProvider,
  type IntegrationState,
  type SafeConfiguration,
} from "./api";

const states = new Set<IntegrationState>(["not_configured", "configured", "healthy", "degraded", "disabled"]);
const capabilities = new Set<IntegrationCapability>(["connection_test", "draft_create", "evaluation_judge", "inference", "metadata_read"]);
const failureCategories = new Set<NonNullable<IntegrationConnection["last_failure_category"]>>(["authentication", "authorization", "rate_limit", "timeout", "network", "invalid_response", "provider_unavailable"]);
const auditActions = new Set<IntegrationAuditEvent["action"]>(["credential_replaced", "configuration_changed", "connection_tested", "connection_disabled"]);
const auditOutcomes = new Set<IntegrationAuditEvent["outcome"]>(["success", "failure", "denied", "unavailable"]);

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function date(value: unknown): string | null {
  return typeof value === "string" && !Number.isNaN(Date.parse(value)) ? value : null;
}

function configuration(value: unknown): SafeConfiguration | null {
  const raw = object(value);
  if (!raw) return null;
  const region = raw.region === "us" || raw.region === "eu" ? raw.region : undefined;
  const model = raw.model === "openai/gpt-oss-20b" ? raw.model : undefined;
  return Object.keys(raw).every((key) => key === "region" || key === "model") ? { ...(region ? { region } : {}), ...(model ? { model } : {}) } : null;
}

function connection(value: unknown): IntegrationConnection | null {
  const raw = object(value);
  if (!raw || !INTEGRATION_PROVIDERS.includes(raw.provider as IntegrationProvider) || !states.has(raw.state as IntegrationState)) return null;
  const config = configuration(raw.configuration);
  const createdAt = date(raw.created_at);
  const updatedAt = date(raw.updated_at);
  const successfulAt = raw.last_successful_test_at === null ? null : date(raw.last_successful_test_at);
  if (!config || !createdAt || !updatedAt || successfulAt === null && raw.last_successful_test_at !== null || !Array.isArray(raw.capabilities) || !raw.capabilities.every((item) => typeof item === "string" && capabilities.has(item as IntegrationCapability)) || !Number.isInteger(raw.version) || (raw.version as number) < 1 || !(raw.last_failure_category === null || failureCategories.has(raw.last_failure_category as NonNullable<IntegrationConnection["last_failure_category"]>))) return null;
  return { provider: raw.provider as IntegrationProvider, state: raw.state as IntegrationState, capabilities: raw.capabilities as IntegrationCapability[], configuration: config, version: raw.version as number, created_at: createdAt, updated_at: updatedAt, last_successful_test_at: successfulAt, last_failure_category: raw.last_failure_category as IntegrationConnection["last_failure_category"] };
}

export function parseConnections(value: unknown): IntegrationConnection[] {
  const raw = object(value);
  if (!raw || !Array.isArray(raw.connections)) return [];
  return raw.connections.map(connection).filter((item): item is IntegrationConnection => item !== null);
}

export function parseAuditEvents(value: unknown): IntegrationAuditEvent[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const raw = object(item);
    if (!raw || !auditActions.has(raw.action as IntegrationAuditEvent["action"]) || !auditOutcomes.has(raw.outcome as IntegrationAuditEvent["outcome"]) || typeof raw.correlation_id !== "string" || !date(raw.created_at)) return [];
    return [{ action: raw.action as IntegrationAuditEvent["action"], outcome: raw.outcome as IntegrationAuditEvent["outcome"], correlation_id: raw.correlation_id, created_at: raw.created_at as string }];
  });
}

export const providerLabel: Record<IntegrationProvider, string> = { eventbrite: "Eventbrite", groq: "Groq", iterable: "Iterable", openai: "OpenAI" };
