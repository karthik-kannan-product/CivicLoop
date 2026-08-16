export type AuthenticationStage =
  | "anonymous"
  | "password_verified"
  | "recovery_restricted"
  | "authenticated";

export type AuthStatus = { stage: AuthenticationStage };
export type PasswordChallenge = {
  stage: "password_verified";
  expires_at: string;
  next_action: "enroll_totp" | "verify_totp";
};
export type Enrollment = {
  device_id: string;
  otpauth_uri: string;
  manual_secret: string;
};
export type Confirmation = {
  stage: "authenticated";
  recovery_codes: string[];
};
export type AdministratorSession = {
  id: string;
  device_label: string;
  source_ip: string | null;
  created_at: string;
  authenticated_at: string | null;
  last_activity_at: string | null;
  mfa_verified_at: string | null;
  absolute_expires_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  is_current: boolean;
};
export type SecurityEvent = {
  id: string;
  action: string;
  outcome: "success" | "failure" | "denied" | "unavailable";
  target_type: string;
  target_id: string;
  details: Record<string, unknown>;
  source_ip: string | null;
  session_id: string | null;
  created_at: string;
};

export const INTEGRATION_PROVIDERS = ["eventbrite", "groq", "iterable", "openai"] as const;
export type IntegrationProvider = (typeof INTEGRATION_PROVIDERS)[number];
export type IntegrationState = "not_configured" | "configured" | "healthy" | "degraded" | "disabled";
export type IntegrationCapability = "connection_test" | "draft_create" | "evaluation_judge" | "inference" | "metadata_read";
export type SafeConfiguration = { region?: "us" | "eu"; model?: "openai/gpt-oss-20b" };
export type IntegrationConnection = {
  provider: IntegrationProvider;
  state: IntegrationState;
  capabilities: IntegrationCapability[];
  configuration: SafeConfiguration;
  version: number;
  created_at: string;
  updated_at: string;
  credential_rotated_at: string | null;
  responsible_actor_id: string | null;
  last_successful_test_at: string | null;
  last_failure_category: "authentication" | "authorization" | "rate_limit" | "timeout" | "network" | "invalid_response" | "provider_unavailable" | null;
};
export type ConnectionTest = { provider: IntegrationProvider; outcome: "healthy" | "degraded"; error_category: IntegrationConnection["last_failure_category"]; duration_ms: number; correlation_id: string; tested_at: string };
export type IntegrationAuditFailureCategory = NonNullable<IntegrationConnection["last_failure_category"]> | "freshness" | "recovery_restricted" | "rate_limit_unavailable" | "version_conflict" | "key_unavailable" | "provider_not_found" | "invalid_request" | "integration_unavailable";
export type IntegrationAuditEvent = { action: "credential_replaced" | "configuration_changed" | "connection_tested" | "connection_disabled" | "audit_listed"; outcome: "success" | "failure" | "denied" | "unavailable"; actor_id: string | null; version: number | null; failure_category: IntegrationAuditFailureCategory | null; correlation_id: string; created_at: string };
export type CredentialReplacement = { credential: string; expected_version: number };
export type ConfigurationPatch = { configuration: SafeConfiguration; expected_version: number };
export type VersionedAction = { expected_version: number };

type Problem = {
  code?: string;
  message?: string;
};

export class AdminAPIError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryAfter: number | null,
  ) {
    super(message);
  }
}

function csrfToken(): string {
  const value = document.cookie
    .split("; ")
    .find((item) => item.startsWith("csrftoken="))
    ?.slice("csrftoken=".length);
  return value ? decodeURIComponent(value) : "";
}

export async function adminRequest<T>(
  path: string,
  options: { method?: "GET" | "POST" | "PUT" | "PATCH"; body?: Record<string, unknown> } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const csrf = method === "GET" ? "" : csrfToken();
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json, application/problem+json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(csrf ? { "X-CSRFToken": csrf } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let payload: T | Problem;
  try {
    payload = (await response.json()) as T | Problem;
  } catch {
    throw new AdminAPIError(
      "Administrator security is temporarily unavailable.",
      response.status,
      "invalid_response",
      null,
    );
  }
  if (!response.ok) {
    const problem = payload as Problem;
    const retryValue = Number(response.headers.get("Retry-After"));
    throw new AdminAPIError(
      problem.message ?? "CivicLoop could not complete that security action.",
      response.status,
      problem.code ?? "unknown_error",
      Number.isFinite(retryValue) && retryValue > 0 ? retryValue : null,
    );
  }
  return payload as T;
}

export const adminAPI = {
  status: () => adminRequest<AuthStatus>("/api/v1/admin/security/status"),
  password: (username: string, password: string) =>
    adminRequest<PasswordChallenge>("/api/v1/admin/auth/password", {
      method: "POST",
      body: { username, password },
    }),
  totp: (token: string) =>
    adminRequest<AuthStatus>("/api/v1/admin/auth/totp", {
      method: "POST",
      body: { token },
    }),
  recovery: (recoveryCode: string) =>
    adminRequest<{ stage: "recovery_restricted"; next_action: "replace_totp" }>(
      "/api/v1/admin/auth/recovery",
      { method: "POST", body: { recovery_code: recoveryCode } },
    ),
  beginEnrollment: (label: string) =>
    adminRequest<Enrollment>("/api/v1/admin/security/totp/enrollment", {
      method: "POST",
      body: { label },
    }),
  confirmEnrollment: (deviceId: string, token: string) =>
    adminRequest<Confirmation>("/api/v1/admin/security/totp/confirmation", {
      method: "POST",
      body: { device_id: deviceId, token },
    }),
  logout: () =>
    adminRequest<AuthStatus>("/api/v1/admin/auth/logout", { method: "POST" }),
  reauthenticate: (password: string, token: string) =>
    adminRequest<{ fresh: true }>("/api/v1/admin/security/reauthentication", {
      method: "POST",
      body: { password, token },
    }),
  changePassword: (currentPassword: string, newPassword: string) =>
    adminRequest<{ changed: true; revoked_session_count: number }>(
      "/api/v1/admin/security/password",
      { method: "PUT", body: { current_password: currentPassword, new_password: newPassword } },
    ),
  regenerateRecoveryCodes: () =>
    adminRequest<{ recovery_codes: string[]; revoked_session_count: number }>(
      "/api/v1/admin/security/recovery-codes/regeneration",
      { method: "POST" },
    ),
  sessions: () =>
    adminRequest<{ sessions: AdministratorSession[] }>("/api/v1/admin/security/sessions"),
  revokeSession: (sessionId: string) =>
    adminRequest<{ revoked: true; logged_out: boolean }>(
      `/api/v1/admin/security/sessions/${sessionId}/revocation`,
      { method: "POST" },
    ),
  revokeOthers: () =>
    adminRequest<{ revoked_count: number }>(
      "/api/v1/admin/security/sessions/revoke-others",
      { method: "POST" },
    ),
  events: (cursor?: string) =>
    adminRequest<{ events: SecurityEvent[]; next_cursor: string | null }>(
      `/api/v1/admin/security/events?${cursor ? `cursor=${encodeURIComponent(cursor)}&` : ""}limit=25`,
    ),
  integrations: () => adminRequest<unknown>("/api/v1/admin/integrations"),
  replaceIntegrationCredential: (provider: IntegrationProvider, body: CredentialReplacement) =>
    adminRequest<unknown>(`/api/v1/admin/integrations/${provider}/credential`, { method: "PUT", body }),
  updateIntegrationConfiguration: (provider: IntegrationProvider, body: ConfigurationPatch) =>
    adminRequest<unknown>(`/api/v1/admin/integrations/${provider}/configuration`, { method: "PATCH", body }),
  testIntegration: (provider: IntegrationProvider, body: VersionedAction) =>
    adminRequest<unknown>(`/api/v1/admin/integrations/${provider}/test`, { method: "POST", body }),
  disableIntegration: (provider: IntegrationProvider, body: VersionedAction) =>
    adminRequest<unknown>(`/api/v1/admin/integrations/${provider}/disable`, { method: "POST", body }),
  integrationAudit: (provider: IntegrationProvider, cursor?: string) =>
    adminRequest<unknown>(
      `/api/v1/admin/integrations/${provider}/audit?${cursor ? `cursor=${encodeURIComponent(cursor)}&` : ""}limit=50`,
    ),
};
