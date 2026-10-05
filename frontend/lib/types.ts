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
  tier: string;
  /** Paid plans (and staff) may see leak-search passwords unmasked. */
  can_reveal_secrets?: boolean;
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

export type LeakKind = "email" | "username" | "domain" | "name" | "auto";

export interface LeakHit {
  source: string;
  source_label: string;
  breach?: string | null;
  email?: string | null;
  username?: string | null;
  first_name?: string | null;
  last_name?: string | null;
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

/** gateway: engines with a clearnet gateway, no Tor, seconds. tor: every onion engine over Tor. */
export type DarkwebMode = "gateway" | "tor";
/** How the Discord app reads its target; `auto` decides by shape. */
export type DiscordMode = "auto" | "user" | "invite" | "guild";

export interface DarkwebScore {
  relevance: number;
  fusion: number;
  consensus: number;
  quality: number;
  freshness: number;
  final: number;
}

/** A collapsed entry under a result: a mirror/possible clone, or another page of the same site. */
export interface DarkwebRelated {
  url: string;
  title: string;
  engines: string[];
}

export interface DarkwebResult {
  url: string;
  host: string;
  title: string;
  snippet: string;
  /** 0..1, higher is better. Quality gates it, so spam cannot ride on keyword matches. */
  score: number;
  breakdown: DarkwebScore;
  engines: string[];
  engine_ranks: Record<string, number>;
  /** Distinct backend indexes that returned it; two front-ends of one index count once. */
  corroboration: number;
  quality: number;
  /** scam-signals, emoji-spam, advertiser, verified, warning, risky, has-mirrors … */
  flags: string[];
  /** The engine's own label (VormWeb: Verified / Warning / Risky). */
  badge: string | null;
  last_seen: string | null;
  merged: number;
  mirrors: DarkwebRelated[];
  more_from_site: DarkwebRelated[];
  /** Tor mode: onion addresses, emails, wallets, PGP markers. */
  entities: Record<string, string[] | boolean>;
}

export type DarkwebEngineStatus = "ok" | "empty" | "timeout" | "error" | "blocked" | "benched" | "skipped";

export interface DarkwebEngine {
  engine: string;
  label: string;
  status: DarkwebEngineStatus;
  results: number;
  pages: number;
  latency_ms: number | null;
  endpoint: string | null;
  error: string | null;
}

export type DarkwebPruneReason =
  | "non_onion" | "dead_v2" | "invalid_address" | "sponsored" | "self_link" | "empty"
  | "near_duplicate" | "low_relevance" | "spam" | "excluded" | "phrase_missing";

export interface DarkwebPruned {
  url: string;
  title: string;
  engines: string[];
  reason: DarkwebPruneReason;
  detail: string;
}

export interface DarkwebStats {
  raw_results: number;
  unique_urls: number;
  merged_duplicates: number;
  mirror_clusters: number;
  shown: number;
  collapsed: number;
  safety_blocked: number;
  pruned_by_reason: Record<string, number>;
  engines_queried: number;
  engines_with_results: number;
}

export interface DarkwebManifest {
  keyword?: string;
  mode?: DarkwebMode;
  transport?: string;
  started_utc?: string;
  completed_utc?: string;
  elapsed_seconds?: number;
  result_count?: number;
  engines_queried?: number;
  engines_responded?: number;
  /** Tor mode only: SHA-256 over the result set, so a report can be shown to be unaltered. */
  sha256?: string;
  [k: string]: unknown;
}

export interface DarkwebJob {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  mode: DarkwebMode;
  query: string;
  progress: number;
  message: string;
  transport: string;
  engines_planned: number;
  /** One entry per engine, appended as each answers. */
  engines: DarkwebEngine[];
  results: DarkwebResult[];
  pruned: DarkwebPruned[];
  stats: DarkwebStats | null;
  /** "phrases" and -excluded words in force. */
  operators: { phrases?: string[]; excluded?: string[]; excluded_phrases?: string[] };
  manifest: DarkwebManifest;
  error?: string | null;
}

export interface DarkwebRosterEntry {
  name: string;
  label: string;
  tier: string;
  notes: string;
  benched: boolean;
  success_rate: number | null;
  latency_s: number | null;
}

export interface DarkwebSearchOptions {
  mode?: DarkwebMode;
  limit?: number;
  pages?: number;
  /** Tor mode only: also query the unvetted engines. */
  experimental?: boolean;
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

/* ────────────────────────── password checker ──────────────────────────
 * Mirrors PasswordCheckResponse in backend/app/models.py.
 */

/** Which source answered: leakedpassword.com, or Pwned Passwords directly when it failed. */
export type PasswordSource = "leakedpassword" | "hibp_range";

export interface PasswordResult {
  hash: string;
  /** False when neither source could answer — never read that as "not leaked". */
  ok: boolean;
  leaked: boolean;
  seen: number;
  source: PasswordSource | null;
  error: string | null;
}

export interface PasswordCheckResponse {
  total: number;
  leaked: number;
  failed: number;
  results: PasswordResult[];
  attribution: string;
}

/* ────────────────────────── spider (correlation / pivoting) ──────────────────────────
 * Shapes mirror the SpiderScanRequest/SpiderJob models in backend/app/models.py.
 */

export type SpiderSeedKind = "email" | "username" | "domain" | "name" | "auto";

/** The entity kinds a node can be. Drives per-type colour/icon in the graph. */
export type SpiderNodeType =
  | "email" | "username" | "domain" | "name"
  | "password" | "hash" | "breach" | "account" | "onion" | "wallet";

/** One earlier scan by this account that held the same identifier. */
export interface SpiderSeen {
  scan_id: number;
  at: string;
  seed: string;
}

export interface SpiderNode {
  type: SpiderNodeType;
  value: string;
  label: string;
  depth: number;
  sources: string[];
  detail: string | null;
  url: string | null;
  masked: boolean;
  /** Earlier scans this value showed up in — the "featured before" signal. */
  seen_before: SpiderSeen[];
}

export interface SpiderEdge {
  src: [SpiderNodeType, string] | string[];
  dst: [SpiderNodeType, string] | string[];
  source: string;
  label: string;
}

export interface SpiderGraph {
  nodes: SpiderNode[];
  edges: SpiderEdge[];
}

export interface SpiderStats {
  nodes: number;
  edges: number;
  lookups: number;
  truncated: boolean;
}

export interface SpiderJob {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  seed: string;
  kind: SpiderSeedKind;
  progress: number;
  message: string;
  modules: { key: string; name: string }[];
  graph: SpiderGraph;
  stats: SpiderStats | null;
  scan_id: number | null;
  error?: string | null;
}

export interface SpiderScanSummary {
  id: number;
  seed: string;
  seed_kind: string;
  title: string | null;
  modules: string[];
  node_count: number;
  edge_count: number;
  created_at: string;
}

export interface SpiderScanDetail extends SpiderScanSummary {
  graph: SpiderGraph;
}

/* ────────────────────────── billing ──────────────────────────
 * Shapes mirror backend/app/routers/billing.py and
 * backend/app/services/billing/{plans,store}.py.
 */

/** "google_play" only ever appears on accounts: the website itself sells through the other two. */
export type BillingProvider = "stripe" | "nowpayments" | "google_play";
export type BillingPeriod = "monthly" | "semiannual" | "yearly";

/** One catalogue entry — `plans.as_dict()`. */
export interface BillingPlan {
  key: string;
  name: string;
  blurb: string;
  monthly_cents: number;
  yearly_cents: number;
  /** 6 months, priced at 15% off monthly*6. 0 on plans with no self-serve price. */
  semiannual_cents: number;
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
  /** In-app subscriptions (Android app only). */
  play_enabled?: boolean;
}

/** `GET /billing/play/account` — what the app needs before opening Google's purchase sheet. */
export interface PlayAccount {
  /** Stamped on the purchase; the server only honours purchases carrying it. */
  account_ref: string;
  package_name: string;
  current: { product_id: string; base_plan_id: BillingPeriod; purchase_token: string } | null;
  /** A plan paid by card, crypto or an operator: nothing to sell in the app. */
  billed_elsewhere: boolean;
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

// ─────────────────────────── support tickets ───────────────────────────

export type TicketReason = "billing" | "technical" | "other";
export type TicketStatus = "open" | "resolved" | "closed";

export interface TicketMessage {
  id: number;
  author_label: string;
  /** Raw sqlite flag, not a real boolean — compare with `=== 1` or use it as truthy. */
  is_staff: number;
  body: string;
  created_at: string;
}

export interface Ticket {
  id: number;
  user_id: number;
  reason: TicketReason;
  subject: string;
  status: TicketStatus;
  created_at: string;
  updated_at: string;
  message_count: number;
  // Present for staff viewing someone else's ticket; present but redundant
  // for an account looking at its own.
  user_email: string | null;
  user_username: string | null;
}

export interface TicketDetail extends Ticket {
  messages: TicketMessage[];
}

// ── admin ▸ data ──

export type DataFmt = "int" | "float" | "money" | "pct" | "hours" | "days" | "bytes" | "text";

export interface DataKpi {
  label: string;
  value: number | string | null;
  fmt: DataFmt;
  sub?: string;
  tone?: "ok" | "warn" | "bad";
}

export interface DataPoint { x: string; y: number | null }
export interface DataBar { label: string; value: number; share: number; muted?: boolean }

export type DataChart =
  | { title: string; fmt: DataFmt; note?: string; kind: "bars" | "line"; points: DataPoint[] }
  | { title: string; fmt: DataFmt; note?: string; kind: "hbars"; points: DataBar[] };

export type DataColumnKind =
  | "text" | "int" | "money" | "pct" | "hours" | "ts" | "date" | "mono" | "badge" | "bytes" | "days";

export interface DataColumn { key: string; label: string; kind: DataColumnKind }

export interface DataTable {
  key: string;
  title: string;
  columns: DataColumn[];
  rows: Record<string, string | number | null>[];
  note?: string;
}

export interface DataDataset {
  id: string;
  title: string;
  days: number;
  generated_at: string;
  kpis: DataKpi[];
  charts: DataChart[];
  tables: DataTable[];
  notes: string[];
}

export interface DataCatalogItem {
  id: string;
  title: string;
  group: string;
  blurb: string;
  filters: ("days" | "bots" | "q" | "limit")[];
  records?: number;
  newest?: string | null;
}

export interface DataQuery { days?: number; q?: string; bots?: boolean; limit?: number }

export type LeakDatasetStatus = "uploading" | "processing" | "ready" | "failed" | "removing";

export interface LeakDataset {
  id: number;
  name: string;
  description: string | null;
  filename: string;
  format: "txt" | "csv" | "json";
  size_bytes: number;
  received_bytes: number;
  sha256: string | null;
  status: LeakDatasetStatus;
  error: string | null;
  records: number;
  skipped: number;
  fields: string[];
  enabled: boolean;
  uploaded_by: string | null;
  created_at: string;
  ready_at: string | null;
}

export interface LeakDatasetLimits { max_bytes: number; chunk_bytes: number; formats: string[] }

export interface LeakDatasetPreviewRow {
  email: string | null;
  username: string | null;
  domain: string | null;
  secret: string | null;
  secret_kind: "plain" | "hash" | null;
}
