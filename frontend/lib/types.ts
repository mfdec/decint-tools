// Mirrors backend/app/models.py (snake_case kept 1:1).

export interface HealthResponse {
  status: "ok";
  version: string;
  tor: boolean;
  tor_detail: string;
  sniffer_enabled: boolean;
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

/* ────────────────────────── IP lookup ──────────────────────────
 * Mirrors IpLookupResponse in backend/app/models.py.
 */

export type IpScope =
  | "public" | "private" | "loopback" | "link_local" | "multicast"
  | "reserved" | "unspecified" | "shared" | "documentation";

export interface IpLocation {
  city: string | null;
  region: string | null;
  country: string | null;
  country_code: string | null;
  continent: string | null;
  in_eu: boolean | null;
  latitude: number | null;
  longitude: number | null;
  /** GeoLite2 only; DB-IP Lite leaves these null. */
  accuracy_km: number | null;
  timezone: string | null;
}

export interface IpNetwork {
  asn: number | null;
  /** The network operator: the ISP, host or company. */
  as_org: string | null;
  prefix: string | null;
}

export interface IpRegistration {
  registry: string | null;
  handle: string | null;
  name: string | null;
  type: string | null;
  range: string | null;
  cidrs: string[];
  country: string | null;
  org: string | null;
  org_address: string | null;
  abuse_email: string | null;
  registered: string | null;
  last_changed: string | null;
}

export interface IpResult {
  ip: string;
  version: number;
  scope: IpScope;
  location: IpLocation | null;
  network: IpNetwork | null;
  registration: IpRegistration | null;
  ptr: string | null;
  /** The PTR name resolves back to this address; only then is it trustworthy. */
  ptr_confirmed: boolean | null;
  /** null when the exit list could not be fetched — unknown, not "no". */
  tor_exit: boolean | null;
  errors: Record<string, string>;
}

export interface IpSource {
  key: string;
  label: string;
  ok: boolean;
  status: string;
}

export interface IpLookupResponse {
  query: string;
  kind: "ip" | "hostname";
  hostname: string | null;
  more_addresses: string[];
  results: IpResult[];
  sources: IpSource[];
  attribution: string[];
}

/* ────────────────────────── phone lookup ──────────────────────────
 * Mirrors PhoneLookupResponse in backend/app/models.py, which keeps
 * VeriRoute Intel's object names: lrn, enhanced_lrn, messaging, cnam, trust.
 */

export type PhoneLineType = "mobile" | "landline" | "voip" | "toll_free" | "unknown";

export interface PhoneEnhancedLrn {
  carrier: string | null;
  carrier_type: string | null;
  city: string | null;
  county: string | null;
  state: string | null;
  zip_code: string | null;
  country_code: string | null;
  timezone: string | null;
  rate_center: string | null;
  lata: string | null;
  ocn: string | null;
}

export interface PhoneMessaging {
  provider: string | null;
  enabled: boolean | null;
  country: string | null;
  country_code: string | null;
  reference_id: string | null;
}

export interface PhoneTrust {
  is_spam: boolean | null;
  is_robocall: boolean | null;
  is_scam: boolean | null;
  spam_type: string | null;
  reputation_score: number | null;
  trust_level: "high" | "medium" | "low" | string | null;
  verdict_status: string | null;
  last_updated: string | null;
}

export interface PhoneLookupResponse {
  query: string;
  phone_number: string;
  e164: string;
  national: string;
  lrn: string | null;
  lrn_activated_at: string | null;
  line_type: PhoneLineType;
  cnam: string | null;
  enhanced_lrn: PhoneEnhancedLrn | null;
  messaging: PhoneMessaging | null;
  trust: PhoneTrust | null;
  cached: boolean;
  looked_up_at: string;
  requested: string[];
  raw: Record<string, unknown>;
  attribution: string[];
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
