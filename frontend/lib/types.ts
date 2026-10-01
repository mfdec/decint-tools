// Mirrors backend/app/models.py (snake_case kept 1:1).

export interface HealthResponse {
  status: "ok";
  version: string;
  tor: boolean;
  tor_detail: string;
  sniffer_enabled: boolean;
  discord_enabled: boolean;
  leak_providers: string[];
  auth_enabled: boolean;
}

export interface CurrentUser {
  id: number;
  email: string;
  username?: string | null;
  role: "admin" | "operator" | "user";
  tier: "starter" | "professional" | "custom";
  status: "active" | "suspended" | "pending";
  mfa: string[];
  break_glass?: boolean;
}

export interface SessionResponse {
  authenticated: boolean;
  user?: CurrentUser;
}

export interface LoginResponse {
  authenticated: boolean;
  user?: CurrentUser;
  mfa_required?: boolean;
  methods?: string[];
  sent?: string | null;
  challenge?: string;
  warning?: string;
}

export interface AdminUser {
  id: number;
  email: string;
  username: string | null;
  role: string;
  tier: string;
  status: string;
  email_verified: number;
  phone: string | null;
  totp_enabled: number;
  email_otp_enabled: number;
  sms_otp_enabled: number;
  created_at: string;
  last_login_at: string | null;
  last_login_ip: string | null;
  locked_until: string | null;
  notes: string | null;
}

export interface AdminStats {
  total: number;
  by_role: Record<string, number>;
  by_tier: Record<string, number>;
  by_status: Record<string, number>;
  with_mfa: number;
  active_sessions: number;
  roles: string[];
  tiers: string[];
  statuses: string[];
  tier_quota: Record<string, number | null>;
  tier_quota_window?: Record<string, "monthly" | "lifetime">;
  billing?: {
    customers: number;
    paying: number;
    free: number;
    paying_by_tier: Record<string, number>;
    paid_pct: number;
  };
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string | null;
  action: string;
  target: string | null;
  detail: string | null;
  ip: string | null;
}

export interface MfaStatus {
  methods: string[];
  totp_enabled: boolean;
  email_enabled: boolean;
  sms_enabled: boolean;
  email_available: boolean;
  sms_available: boolean;
  phone: string | null;
  recovery_codes_left: number;
}

export type LeakKind = "email" | "username" | "domain" | "auto";

export interface LeakHit {
  source: string;
  source_label: string;
  breach?: string | null;
  email?: string | null;
  username?: string | null;
  password?: string | null;
  line?: string | null;
  date?: string | null;
  detail?: string | null;
  fields: string[];
  url?: string | null;
}

export interface LeakSource {
  key: string;
  label: string;
  ok: boolean;
  count: number;
  status: string;
}

export interface LeakSearchResponse {
  query: string;
  kind: LeakKind;
  total: number;
  masked: boolean;
  sources: LeakSource[];
  hits: LeakHit[];
  note: string;
}

export type DarkwebMode = "ahmia" | "tor";
/** How the Discord app reads its target; `auto` decides by shape. */
export type DiscordMode = "auto" | "user" | "invite" | "guild";

export interface DarkwebResult {
  title: string;
  url: string;
  snippet: string;
  score: number;
  coverage?: number | null;
  corroboration?: number | null;
  sources: string[];
  entities: Record<string, unknown>;
  live?: boolean | null;
}

export interface DarkwebJob {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  mode: DarkwebMode;
  query: string;
  progress: number;
  message: string;
  results: DarkwebResult[];
  manifest: Record<string, unknown>;
  error?: string | null;
}

export interface SnowflakeInfo {
  id: string;
  created_at: string;
  unix_ms: number;
  worker_id: number;
  process_id: number;
  increment: number;
}

export interface DiscordUser {
  id: string;
  username?: string | null;
  global_name?: string | null;
  discriminator?: string | null;
  avatar_url?: string | null;
  accent_color?: number | null;
  public_flags?: number | null;
  flags_decoded: string[];
  bot?: boolean | null;
  created_at?: string | null;
}

export interface DiscordInvite {
  code: string;
  guild_id?: string | null;
  guild_name?: string | null;
  channel_name?: string | null;
  approximate_member_count?: number | null;
  approximate_presence_count?: number | null;
  inviter?: string | null;
  expires_at?: string | null;
  created_at?: string | null;
}

export interface VisitRow {
  id: number;
  ts: string;
  event: string;
  visitor_id: string | null;
  session_id: string | null;
  ip: string | null;
  country: string | null;
  country_code: string | null;
  region: string | null;
  city: string | null;
  asn: string | null;
  org: string | null;
  browser: string | null;
  browser_version: string | null;
  os: string | null;
  os_version: string | null;
  device_type: string | null;
  device_model: string | null;
  is_bot: number;
  path: string | null;
  referrer: string | null;
  screen_w: number | null;
  screen_h: number | null;
  pixel_ratio: number | null;
  cpu_cores: number | null;
  device_memory: number | null;
  gpu: string | null;
  tz_client: string | null;
  language: string | null;
  connection: string | null;
  meta: string | null;
}

export interface TopRow {
  label: string;
  n: number;
  visitors: number;
}

export interface AnalyticsSummary {
  days: number;
  events: number;
  visitors: number;
  sessions: number;
  bots_filtered: number;
  geo_enabled: boolean;
  by_day: { day: string; n: number; visitors: number }[];
  top_pages: TopRow[];
  top_countries: TopRow[];
  top_referrers: TopRow[];
  top_browsers: TopRow[];
  top_os: TopRow[];
  top_devices: TopRow[];
  top_orgs: TopRow[];
}

export interface AnalyticsConfig {
  enabled: boolean;
  ip_mode: string;
  id_mode: string;
  track_bots: boolean;
  retention_days: number;
}

export interface DiscordLookupResponse {
  kind: "snowflake" | "user" | "invite" | "guild_widget";
  token_present: boolean;
  snowflake?: SnowflakeInfo | null;
  user?: DiscordUser | null;
  invite?: DiscordInvite | null;
  widget?: Record<string, unknown> | null;
  note: string;
}

/* ────────────────────────── billing ──────────────────────────
 * Shapes mirror backend/app/routers/billing.py and
 * backend/app/services/billing/{plans,store}.py.
 */

export type BillingProvider = "stripe" | "nowpayments";
export type BillingPeriod = "monthly" | "yearly";

/** One catalogue entry — `plans.as_dict()`. */
export interface BillingPlan {
  key: string;
  name: string;
  blurb: string;
  monthly_cents: number;
  yearly_cents: number;
  /** Searches per window; null means unmetered. */
  quota: number | null;
  /** "monthly" resets on the 1st; "lifetime" is the free tier's fixed trial. */
  quota_window: "monthly" | "lifetime";
  features: string[];
  featured: boolean;
  /** Sold by conversation — the button emails the operator. */
  contact: boolean;
  is_free: boolean;
  purchasable: boolean;
}

/** `GET /billing/config` — what the pricing page renders itself from. */
export interface BillingConfig {
  enabled: boolean;
  providers: string[];
  card_enabled: boolean;
  crypto_enabled: boolean;
  currency: string;
  periods: BillingPeriod[];
  plans: BillingPlan[];
  free_tier: string;
}

/** Lifecycle of a billing_orders row. */
export type OrderStatus = "paid" | "pending" | "failed" | "expired" | "refunded";

export interface BillingOrder {
  id: number;
  provider: BillingProvider;
  plan: string;
  period: BillingPeriod;
  months: number;
  amount_cents: number;
  currency: string;
  status: OrderStatus;
  pay_currency: string | null;
  created_at: string;
  paid_at: string | null;
}

export interface BillingSubscription {
  provider: BillingProvider;
  /** Stripe's own lifecycle: "active", "trialing", "past_due", … */
  status: string;
  plan: string;
  period: BillingPeriod;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

/** How many searches an account has used in its window. */
export interface UsageState {
  used: number;
  /** null = unmetered (staff, or a tier without a quota). */
  limit: number | null;
  remaining: number | null;
  window: "monthly" | "lifetime" | null;
  resets_at: string | null;
  tier: string;
  is_free: boolean;
}

/** `GET /billing/me` — `store.summary()`. */
export interface BillingSummary {
  tier: string;
  plan_name: string;
  quota: number | null;
  quota_window: "monthly" | "lifetime";
  usage: UsageState;
  /** Where the entitlement came from: "stripe", "nowpayments", "manual". */
  source: string;
  expires_at: string | null;
  days_left: number | null;
  renews: boolean;
  /** A live card subscription: another plan is applied in place, against the
   *  card on file, rather than through a fresh checkout. */
  can_change_plan: boolean;
  subscription: BillingSubscription | null;
  orders: BillingOrder[];
}

/**
 * `POST /billing/checkout`. Two shapes, one endpoint:
 *  - a new customer gets `url` and is sent to the processor;
 *  - a card subscriber gets `changed: true` and `url: null` — the plan was
 *    switched in place and the difference charged to their card already.
 */
export interface CheckoutResponse {
  url: string | null;
  changed: boolean;
  /** Only with `changed`: "changed" (price moved) or "resumed" (a cancelling
   *  subscription kept, nothing charged). */
  action?: "changed" | "resumed";
  order_id: number;
  provider: BillingProvider;
  plan: string;
  period: BillingPeriod;
  amount_cents: number;
  currency: string;
  recurring: boolean;
}

/* ────────────────────────── fleet ──────────────────────────
 * Shapes mirror backend/app/routers/fleet.py, which passes the hub's own
 * JSON through. Field names are the hub's camelCase, not the API's snake_case.
 */

/** Result of the hub's reachability probe for one server. */
export interface FleetHealth {
  /** "up" | "down" | "unknown" — the console maps this to a dot colour. */
  status: string;
  checkedAt?: string;
  error?: string | null;
}

export interface FleetServer {
  id: string;
  label: string;
  host: string;
  tags: string[];
  /** The hub always reports a probe result, even if only {status:"unknown"}. */
  health: FleetHealth;
}

export interface FleetScript {
  name: string;
  title: string;
  description?: string;
  /** Argument hint shown under the picker. */
  args?: string;
  /** Marked dangerous — the console confirms before running it. */
  danger?: boolean;
}

export interface FleetTask {
  serverId: string;
  host: string;
  label: string;
  status: string;
  exitCode: number | null;
  output: string;
  error: string | null;
  durationMs: number | null;
}

export interface FleetRun {
  id: string;
  title: string;
  status: string;
  dryRun: boolean;
  /** Keyed by server id. */
  tasks: Record<string, FleetTask>;
}

/** A tag and how many servers carry it. */
export interface FleetTag {
  tag: string;
  count: number;
}

/** `GET /fleet/state`. `configured: false` when FLEET_TOKEN is unset. */
export interface FleetState {
  configured: boolean;
  hubUrl?: string;
  servers: FleetServer[];
  tags: FleetTag[];
  scripts: FleetScript[];
  runs: FleetRun[];
  history?: FleetRun[];
  inventoryError?: string | null;
}
