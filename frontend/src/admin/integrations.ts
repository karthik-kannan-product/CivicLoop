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
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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

export function parseConnection(value: unknown): IntegrationConnection | null {
  const raw = object(value);
  if (!raw || !INTEGRATION_PROVIDERS.includes(raw.provider as IntegrationProvider) || !states.has(raw.state as IntegrationState)) return null;
  const config = configuration(raw.configuration);
  if (!config) return null;
  const createdAt = date(raw.created_at);
  const updatedAt = date(raw.updated_at);
  const successfulAt = raw.last_successful_test_at === null ? null : date(raw.last_successful_test_at);
  const rotatedAt = raw.credential_rotated_at === null ? null : date(raw.credential_rotated_at);
  const provider = raw.provider as IntegrationProvider;
  const correctConfig = provider === "eventbrite" ? Object.keys(config).length === 0 : provider === "iterable" ? Object.keys(config).length === 1 && !!config.region : Object.keys(config).length === 1 && !!config.model;
  if (!correctConfig || !createdAt || !updatedAt || rotatedAt === null && raw.credential_rotated_at !== null || successfulAt === null && raw.last_successful_test_at !== null || !(raw.responsible_actor_id === null || typeof raw.responsible_actor_id === "string") || !Array.isArray(raw.capabilities) || !raw.capabilities.every((item) => typeof item === "string" && capabilities.has(item as IntegrationCapability)) || !Number.isInteger(raw.version) || (raw.version as number) < 1 || !(raw.last_failure_category === null || failureCategories.has(raw.last_failure_category as NonNullable<IntegrationConnection["last_failure_category"]>))) return null;
  return { provider, state: raw.state as IntegrationState, capabilities: raw.capabilities as IntegrationCapability[], configuration: config, version: raw.version as number, created_at: createdAt, updated_at: updatedAt, credential_rotated_at: rotatedAt, responsible_actor_id: raw.responsible_actor_id as string | null, last_successful_test_at: successfulAt, last_failure_category: raw.last_failure_category as IntegrationConnection["last_failure_category"] };
}

export function parseConnections(value: unknown): IntegrationConnection[] {
  const raw = object(value);
  if (!raw || !Array.isArray(raw.connections)) return [];
  return raw.connections.map(parseConnection).filter((item): item is IntegrationConnection => item !== null);
}

export function parseHealthCheck(value: unknown): { provider: IntegrationProvider; outcome: "healthy" | "degraded"; error_category: IntegrationConnection["last_failure_category"]; tested_at: string } | null {
  const raw = object(value);
  const testedAt = raw && date(raw.tested_at);
  if (!raw || !INTEGRATION_PROVIDERS.includes(raw.provider as IntegrationProvider) || (raw.outcome !== "healthy" && raw.outcome !== "degraded") || !testedAt || !Number.isInteger(raw.duration_ms) || (raw.duration_ms as number) < 0 || (raw.duration_ms as number) > 30000 || typeof raw.correlation_id !== "string" || !UUID.test(raw.correlation_id) || !(raw.error_category === null || failureCategories.has(raw.error_category as NonNullable<IntegrationConnection["last_failure_category"]>))) return null;
  return { provider: raw.provider as IntegrationProvider, outcome: raw.outcome, error_category: raw.error_category as IntegrationConnection["last_failure_category"], tested_at: testedAt };
}

export function parseAuditPage(value: unknown): { events: IntegrationAuditEvent[]; next_cursor: string | null } | null {
  const raw = object(value);
  if (!raw || !Array.isArray(raw.events) || !(raw.next_cursor === null || typeof raw.next_cursor === "string")) return null;
  return { events: parseAuditEvents(raw.events), next_cursor: raw.next_cursor };
}

export function parseAuditEvents(value: unknown): IntegrationAuditEvent[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const raw = object(item);
    if (!raw || !auditActions.has(raw.action as IntegrationAuditEvent["action"]) || !auditOutcomes.has(raw.outcome as IntegrationAuditEvent["outcome"]) || !(raw.actor_id === null || typeof raw.actor_id === "string" && UUID.test(raw.actor_id)) || !Number.isInteger(raw.version) || (raw.version as number) < 1 || !(raw.failure_category === null || failureCategories.has(raw.failure_category as NonNullable<IntegrationConnection["last_failure_category"]>)) || typeof raw.correlation_id !== "string" || !UUID.test(raw.correlation_id) || !date(raw.created_at)) return [];
    return [{ action: raw.action as IntegrationAuditEvent["action"], outcome: raw.outcome as IntegrationAuditEvent["outcome"], actor_id: raw.actor_id as string | null, version: raw.version as number, failure_category: raw.failure_category as IntegrationConnection["last_failure_category"], correlation_id: raw.correlation_id, created_at: raw.created_at as string }];
  });
}

export const providerLabel: Record<IntegrationProvider, string> = { eventbrite: "Eventbrite", groq: "Groq", iterable: "Iterable", openai: "OpenAI" };
