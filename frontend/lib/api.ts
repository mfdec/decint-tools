// Same-origin API client. Next rewrites /api/v1/* to the FastAPI backend.
import type {
  AdminStats,
  AdminUser,
  AnalyticsConfig,
  AnalyticsSummary,
  AuditEntry,
  LoginResponse,
  MfaStatus,
  DarkwebJob,
  DarkwebMode,
  DiscordLookupResponse,
  HealthResponse,
  LeakKind,
  LeakSearchResponse,
  SessionResponse,
  VisitRow,
  BillingConfig,
  BillingPeriod,
  BillingProvider,
  BillingSummary,
  CheckoutResponse,
  FleetRun,
  FleetState,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    credentials: "same-origin",
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail || body.message || detail;
    } catch {
      /* non-json error */
    }
    throw new ApiError(detail, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<HealthResponse>("/health"),

  session: () => req<SessionResponse>("/auth/session"),
  bootstrap: () =>
    req<{ has_users: boolean; email_2fa_available: boolean; sms_2fa_available: boolean; password_reset_enabled: boolean }>(
      "/auth/bootstrap"
    ),
  signupInfo: () =>
    req<{
      signup_enabled: boolean;
      captcha_site_key: string;
      captcha_on_signup: boolean;
      captcha_on_login: boolean;
      oauth_providers: string[];
      login_token_enabled: boolean;
      password_reset_enabled: boolean;
      /** New accounts activate themselves from an emailed link — no operator. */
      email_activation: boolean;
      activation_ttl_hours: number;
    }>("/auth/signup-info"),
  signup: (email: string, username: string, password: string, captcha = "", next = "") =>
    req<{ created: boolean; note: string }>("/auth/signup", {
      method: "POST",
      body: JSON.stringify({ email, username, password, captcha, next }),
    }),
  // ── activation (no session: the person cannot sign in yet) ──
  activateCheck: (token: string) =>
    req<{ valid: boolean; next: string }>(`/auth/activate-check?token=${encodeURIComponent(token)}`),
  activate: (token: string) =>
    req<{ activated: boolean; already: boolean; next: string; note: string }>("/auth/activate", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  activationResend: (email: string, captcha = "") =>
    req<{ sent: boolean; note: string }>("/auth/activate/resend", {
      method: "POST",
      body: JSON.stringify({ email, captcha }),
    }),
  login: (email: string, password: string, token = "", captcha = "") =>
    req<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, token, captcha }),
    }),
  loginToken: (token: string) =>
    req<LoginResponse>("/auth/login-token", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  mfaVerify: (challenge: string, method: string, code: string) =>
    req<LoginResponse>("/auth/mfa/verify", {
      method: "POST",
      body: JSON.stringify({ challenge, method, code }),
    }),
  mfaSend: (challenge: string, method: string) =>
    req<{ sent: boolean; method: string }>("/auth/mfa/send", {
      method: "POST",
      body: JSON.stringify({ challenge, method, code: "" }),
    }),
  logout: () => req<SessionResponse>("/auth/logout", { method: "POST" }),

  mfaStatus: () => req<MfaStatus>("/auth/mfa/status"),
  totpBegin: () =>
    req<{ secret: string; otpauth_uri: string; qr_svg: string }>("/auth/mfa/totp/begin", {
      method: "POST",
    }),
  mfaEnable: (method: string, code = "", phone = "") =>
    req<{ enabled: boolean; recovery_codes?: string[] }>("/auth/mfa/enable", {
      method: "POST",
      body: JSON.stringify({ method, code, phone }),
    }),
  mfaDisable: (method: string) =>
    req<{ disabled: boolean }>("/auth/mfa/disable", {
      method: "POST",
      body: JSON.stringify({ method, code: "" }),
    }),
  regenerateRecovery: () =>
    req<{ recovery_codes: string[] }>("/auth/mfa/recovery-codes", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    req<{ changed: boolean; note: string }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  adminUsers: (params: Record<string, string> = {}) =>
    req<{ total: number; users: AdminUser[] }>(
      `/admin/users?${new URLSearchParams(params).toString()}`
    ),
  adminStats: () => req<AdminStats>("/admin/stats"),
  adminUser: (id: number) =>
    req<AdminUser & { sessions: any[]; recovery_codes_left: number }>(`/admin/users/${id}`),
  adminCreateUser: (body: Record<string, unknown>) =>
    req<AdminUser>("/admin/users", { method: "POST", body: JSON.stringify(body) }),
  adminUpdateUser: (id: number, body: Record<string, unknown>) =>
    req<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  adminDeleteUser: (id: number) =>
    req<{ ok: boolean }>(`/admin/users/${id}`, { method: "DELETE" }),
  adminSetPassword: (id: number, password: string) =>
    req<{ ok: boolean; note: string }>(`/admin/users/${id}/password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  adminResetMfa: (id: number) =>
    req<{ ok: boolean; note: string }>(`/admin/users/${id}/reset-mfa`, { method: "POST" }),
  adminRevokeSessions: (id: number) =>
    req<{ revoked: number }>(`/admin/users/${id}/revoke-sessions`, { method: "POST" }),
  adminAudit: (limit = 200) =>
    req<{ entries: AuditEntry[] }>(`/admin/audit?limit=${limit}`),

  searchLeaks: (query: string, kind: LeakKind = "auto", reveal = false) =>
    req<LeakSearchResponse>(
      `/leaks/search?query=${encodeURIComponent(query)}&kind=${kind}&reveal=${reveal}`
    ),

  startDarkweb: (query: string, mode: DarkwebMode = "ahmia", limit = 25) =>
    req<{ job_id: string; status: string }>("/darkweb/search", {
      method: "POST",
      body: JSON.stringify({ query, mode, limit }),
    }),
  darkwebJob: (jobId: string) => req<DarkwebJob>(`/darkweb/jobs/${jobId}`),

  discordSnowflake: (id: string) =>
    req<DiscordLookupResponse>(`/discord/snowflake/${id}`),
  discordUser: (id: string) => req<DiscordLookupResponse>(`/discord/user/${id}`),
  discordInvite: (code: string) =>
    req<DiscordLookupResponse>(`/discord/invite/${encodeURIComponent(code)}`),
  discordGuildWidget: (id: string) =>
    req<DiscordLookupResponse>(`/discord/guild/${id}/widget`),

  packetInterfaces: () =>
    req<{ interfaces: string[]; default: string | null }>("/packets/interfaces"),

  analyticsSummary: (days = 7, includeBots = false) =>
    req<AnalyticsSummary>(`/analytics/summary?days=${days}&include_bots=${includeBots}`),
  analyticsRecent: (limit = 200, includeBots = false) =>
    req<{ rows: VisitRow[] }>(`/analytics/recent?limit=${limit}&include_bots=${includeBots}`),
  analyticsVisitor: (id: string) =>
    req<{ visitor_id: string; events: number; timeline: VisitRow[] }>(
      `/analytics/visitor/${id}`
    ),
  analyticsConfig: () => req<AnalyticsConfig>("/analytics/config"),
  // ── password reset ──
  // Forgot always answers 200 with a note, whether or not the address exists;
  // a 503 means the server has no mail relay configured, which is a property
  // of the deployment rather than of any account.
  passwordForgot: (email: string, captcha = "") =>
    req<{ note: string }>("/auth/password/forgot", {
      method: "POST",
      body: JSON.stringify({ email, captcha }),
    }),
  passwordResetCheck: (token: string) =>
    req<{ valid: boolean }>(`/auth/password/reset-check?token=${encodeURIComponent(token)}`),
  passwordReset: (token: string, password: string) =>
    req<{ reset: boolean }>("/auth/password/reset", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    }),

  // ── billing ──
  // config is public on purpose: the pricing page renders from the same
  // catalogue the processor charges against, so the two cannot drift.
  billingConfig: () => req<BillingConfig>("/billing/config"),
  cryptoCurrencies: () => req<{ currencies: string[] }>("/billing/crypto/currencies"),
  billingMe: () => req<BillingSummary>("/billing/me"),
  checkout: (
    plan: string,
    period: BillingPeriod = "monthly",
    provider: BillingProvider = "stripe",
    payCurrency = ""
  ) =>
    req<CheckoutResponse>("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ plan, period, provider, pay_currency: payCurrency }),
    }),
  billingPortal: () => req<{ url: string }>("/billing/portal", { method: "POST" }),

  // ── fleet (admin) ──
  // state answers {configured:false} rather than erroring when FLEET_TOKEN is
  // unset, so the tab can explain itself instead of showing a failure.
  fleetState: () => req<FleetState>("/fleet/state"),
  fleetRun: (body: {
    kind: string;
    script: string | null;
    command: string | null;
    args: string;
    ids: string[];
    tags?: string[];
    dryRun: boolean;
    sudo?: boolean;
  }) => req<FleetRun>("/fleet/runs", { method: "POST", body: JSON.stringify(body) }),
  fleetCancel: (runId: string) =>
    req<FleetRun>(`/fleet/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" }),
  fleetRecheck: (ids?: string[]) =>
    req<FleetState>("/fleet/health", {
      method: "POST",
      body: JSON.stringify(ids ? { ids } : {}),
    }),
};

/**
 * Server-sent-events URL for the fleet hub relay.
 *
 * Same origin — nginx proxies /api to FastAPI, which streams the hub's events
 * straight through, so output arrives per host as it happens. Unlike the
 * packet stream this is EventSource over plain HTTP, not a WebSocket, so no
 * protocol swap is needed.
 */
export function fleetStreamUrl(): string {
  return `${BASE}/fleet/stream`;
}

/** Poll a dark-web job until it settles. */
export async function pollDarkweb(
  jobId: string,
  onTick?: (job: DarkwebJob) => void,
  intervalMs = 1000,
  timeoutMs = 180000
): Promise<DarkwebJob> {
  const start = Date.now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const job = await api.darkwebJob(jobId);
    onTick?.(job);
    if (job.status === "done" || job.status === "error") return job;
    if (Date.now() - start > timeoutMs) return { ...job, status: "error", error: "timed out" };
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** WebSocket URL for the packet stream (same origin; Next proxies upgrade). */
export function packetsWsUrl(iface?: string, bpf?: string): string {
  const proto = typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
  const host = typeof window !== "undefined" ? window.location.host : "localhost:3000";
  const params = new URLSearchParams();
  if (iface) params.set("iface", iface);
  if (bpf) params.set("bpf", bpf);
  const qs = params.toString();
  return `${proto}://${host}${BASE}/packets/stream${qs ? `?${qs}` : ""}`;
}
