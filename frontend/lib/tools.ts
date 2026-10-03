/**
 * The canonical description of what DECINT does.
 *
 * Both the landing page and /tools render from this, so the toolkit is stated
 * once and can't drift between pages. `summary` is the short landing copy;
 * everything else is the full breakdown.
 */
export interface ToolSpec {
  no: string;
  key: string;
  name: string;
  input: string;
  local?: boolean;
  summary: string;
  how: string;
  accepts: string[];
  returns: string[];
  sources?: string[];
  limits: string;
  /** Standalone monthly price in cents — an anchor, not a buy button. */
  price_cents: number;
  /** Lowest plan that includes this tool — rendered as "In {included_in}". */
  included_in: string;
  /** Optional note about what a higher tier adds. */
  deeper?: { plan: string; what: string };
}

export const TOOLS: ToolSpec[] = [
  {
    no: "01",
    key: "leaks",
    price_cents: 1200,
    included_in: "Starter",
    name: "Leak database search",
    input: "email · username · domain",
    summary:
      "Find an identifier across public breach data — aggregated from multiple sources, deduped, and tagged by origin, with secrets revealed on paid plans and masked on the free trial.",
    how:
      "Your query fans out to several public breach-data services at once. Their answers are normalised into one table, deduplicated, and tagged with the source that produced each row, so you can see which service vouches for what.",
    accepts: ["Email address", "Username or handle", "Domain name", "First and/or last name"],
    returns: [
      "Breach or dataset name",
      "Date the data surfaced",
      "Which fields were exposed — passwords, phone numbers, addresses",
      "Matching credential lines — revealed on paid plans, masked on the free trial",
    ],
    sources: ["XposedOrNot", "ProxyNova COMB", "LeakCheck (public)", "HIBP breach catalogue"],
    limits:
      "Coverage comes from free public sources: broad, but not exhaustive. A source that rate-limits or goes offline is reported as failed rather than silently dropped from the results.",
  },
  {
    no: "02",
    key: "discord",
    price_cents: 900,
    included_in: "Starter",
    name: "Discord OSINT",
    input: "user / server ID · invite",
    summary:
      "Resolve an ID to its exact account-creation time, decode profile badges, and inspect invites and public server details.",
    how:
      "A Discord ID encodes its own creation timestamp, so account age is derived arithmetically — no lookup, no rate limit, and nothing is sent to Discord. Live profile and server details use the official API.",
    accepts: ["User ID", "Server (guild) ID", "Invite code or full invite link"],
    returns: [
      "Exact account or server creation time, to the millisecond",
      "Username, display name and avatar",
      "Profile badges decoded from account flags",
      "Server name, member and online counts, and who created the invite",
    ],
    limits:
      "Timestamp decoding always works offline. Live user lookups need a bot token configured on the server; invite and public-server details do not.",
  },
  {
    no: "03",
    key: "darkweb",
    price_cents: 1900,
    included_in: "Starter",
    deeper: {
      plan: "Pro",
      what:
        "every onion search engine is queried over its own live Tor circuit instead of the few with a clearnet gateway, with entity extraction and an evidence SHA-256 over the result set",
    },
    name: "Dark-web search",
    input: "keyword",
    summary:
      "One search across dozens of onion search engines, merged and ranked, with the scams, ads and look-alike sites filtered out — and a reason kept for everything dropped.",
    how:
      "A search goes to many onion search engines at once and the results are merged: the same page found by several engines becomes one result, near-identical pages on different addresses are grouped as mirrors or possible phishing clones, and paid ads, scam listings, dead addresses and typo-squatted look-alikes are dropped. What is left is ranked on relevance, how many independent indexes agree, and a quality score. Fast mode asks the engines that publish a clearnet gateway. Tor mode asks every onion engine, each on its own isolated circuit.",
    accepts: ["Any keyword", "Quoted phrases and excluded terms"],
    returns: [
      "Ranked .onion results with a score and the engines that returned each one",
      "Mirrors and possible clones of a site grouped, not repeated",
      "Everything that was dropped — ads, scam listings, look-alikes — with the reason, so nothing disappears silently",
      "Tor mode: extracted entities — onion addresses, emails, crypto wallets, PGP markers",
      "A corroboration count when several independent indexes agree on a result",
      "Tor mode: a SHA-256 hash over the result set, so a report can be shown to be unaltered",
    ],
    limits:
      "Onion engines come and go constantly, and many are down at any given moment. Dead ones are benched by a circuit breaker instead of stalling every search. Tor mode takes up to a minute because it is making real circuits. Only search-engine pages are read; the sites in the results are never visited, and searches for child sexual abuse material are refused.",
  },
  {
    no: "04",
    key: "packets",
    price_cents: 1500,
    included_in: "Enterprise",
    name: "Packet capture",
    input: "live interface",
    local: true,
    summary:
      "Watch decoded network traffic — protocols, DNS lookups, and TCP flags — in real time. Runs on the operator's own machine only.",
    how:
      "Captures on the host's own network interface and decodes each frame as it arrives, streaming a live table into the console.",
    accepts: ["A network interface on the machine running DECINT"],
    returns: [
      "Source and destination addresses",
      "Protocol — TCP, UDP, TLS, HTTP, DNS, ICMP, ARP",
      "TCP flags and packet length",
      "DNS query names as they resolve",
    ],
    limits:
      "Needs raw-socket access, and only ever sees traffic on the machine it runs on. That makes it an operator tool: it is disabled by default and switched off entirely on shared or public deployments.",
  },
  {
    no: "05",
    key: "fleet",
    price_cents: 2900,
    included_in: "Enterprise",
    name: "Fleet",
    input: "servers · tags · scripts",
    local: true,
    summary:
      "Run a saved script or a one-off command across every enrolled server at once, over SSH, and watch each host's output come back live.",
    how:
      "A separate hub process holds one SSH identity for every server enrolled in it. Pick hosts individually or by tag, choose a saved script or type a command, and the hub fans it out in parallel — each host's output streams into the console as it happens, with an exit code and duration when it finishes. Dry-run shows what would run without running it.",
    accepts: [
      "Enrolled servers, picked one at a time or by tag",
      "A saved script from the hub, with arguments",
      "An ad-hoc shell command, optionally under sudo",
    ],
    returns: [
      "Reachability of every server, re-probed on demand",
      "Per-host output as it arrives, with exit code and duration",
      "A run verdict — success, partial, failed, or cancelled",
      "The history of previous runs",
    ],
    limits:
      "The hub holds SSH keys for the whole fleet, so it runs as its own user, bound to loopback, and is never exposed through the reverse proxy — the console reaches it only through the admin-gated API. Without a fleet token configured the tab reports itself unconfigured. Scripts marked dangerous ask for confirmation before they run.",
  },
];

/**
 * What the Starter tools would cost bought one at a time.
 *
 * Summed rather than written down so the bundle argument on /tools cannot go
 * stale when a price above changes. Operator tools (`local`) are excluded —
 * they are Enterprise-only, not part of the Starter bundle the card
 * compares against, so counting them would overstate the saving.
 */
export const STANDALONE_TOTAL_CENTS: number = TOOLS.filter((t) => !t.local).reduce(
  (sum, t) => sum + t.price_cents,
  0
);
