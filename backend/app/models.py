"""Pydantic response/request schemas shared across routers.

Kept deliberately flat and snake_case so the TypeScript client in the
frontend maps to them 1:1.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# The dark-web engine's own models are the API shape: one definition, no copy to drift.
from .services.darkweb.models import EngineReport as DarkwebEngine
from .services.darkweb.models import PrunedItem as DarkwebPruned
from .services.darkweb.models import Result as DarkwebResult
from .services.darkweb.models import SearchStats as DarkwebStats

# ─────────────────────────── health ───────────────────────────


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    tor: bool
    tor_detail: str = ""
    sniffer_enabled: bool
    leak_providers: list[str]
    auth_enabled: bool


# ─────────────────────────── auth ───────────────────────────


class LoginRequest(BaseModel):
    token: str


class SessionResponse(BaseModel):
    authenticated: bool
    operator: str = "operator"
    is_admin: bool = False


# ─────────────────────────── leaks ───────────────────────────

LeakKind = Literal["email", "username", "domain", "name", "auto"]


class LeakHit(BaseModel):
    source: str  # provider key, e.g. "xposedornot"
    source_label: str
    breach: str | None = None  # named breach / dataset
    email: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    password: str | None = None  # masked unless reveal=true
    line: str | None = None  # raw combolist line (masked)
    date: str | None = None
    detail: str | None = None
    fields: list[str] = Field(default_factory=list)
    url: str | None = None


class LeakSource(BaseModel):
    key: str
    label: str
    ok: bool
    count: int
    status: str


class LeakSearchResponse(BaseModel):
    query: str
    kind: LeakKind
    total: int
    masked: bool
    sources: list[LeakSource]
    hits: list[LeakHit]
    note: str = (
        "Coverage is limited to free public sources and is not exhaustive."
    )


# ─────────────────────────── dark web ───────────────────────────

DarkwebMode = Literal["gateway", "tor"]


class DarkwebSearchRequest(BaseModel):
    # Quoted "phrases" and -excluded words are understood; the length cap matches the engines'.
    query: str = Field(min_length=1, max_length=300)
    # gateway: the engines with a clearnet gateway, no Tor, seconds.
    # tor:     every default onion engine over isolated Tor circuits, slower.
    mode: DarkwebMode = "gateway"
    limit: int = Field(25, ge=1, le=50)
    pages: int = Field(1, ge=1, le=10)  # clamped to DARKWEB_MAX_PAGES server-side
    experimental: bool = False  # tor mode only: also query the unvetted engines

    @field_validator("mode", mode="before")
    @classmethod
    def _legacy_mode(cls, v: Any) -> Any:
        # "ahmia" was the old fast mode (one clearnet index); it is now "gateway".
        return "gateway" if v == "ahmia" else v


class JobRef(BaseModel):
    job_id: str
    status: str


class DarkwebJob(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    mode: DarkwebMode
    query: str
    progress: float = 0.0
    message: str = ""
    transport: str = ""
    # Filled in live as each engine answers, so the console can show the fan-out happening.
    engines_planned: int = 0
    engines: list[DarkwebEngine] = Field(default_factory=list)
    results: list[DarkwebResult] = Field(default_factory=list)
    pruned: list[DarkwebPruned] = Field(default_factory=list)
    stats: DarkwebStats | None = None
    operators: dict[str, list[str]] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ─────────────────────────── password checker ───────────────────────────

PasswordSource = Literal["leakedpassword", "hibp_range"]


class PasswordCheckRequest(BaseModel):
    # SHA-1 hex digests, never passwords: the console hashes before it sends.
    # POSTed rather than put in a query string so they stay out of access logs.
    hashes: list[str] = Field(..., min_length=1)


class PasswordResult(BaseModel):
    hash: str  # lowercase SHA-1 hex
    ok: bool  # an answer came back; False means neither source could say
    leaked: bool = False
    seen: int = 0  # times this password appears across breach corpora
    source: PasswordSource | None = None
    error: str | None = None


class PasswordCheckResponse(BaseModel):
    total: int
    leaked: int
    failed: int
    results: list[PasswordResult]
    attribution: str


# ─────────────────────────── IP lookup ───────────────────────────

IpScope = Literal[
    "public", "private", "loopback", "link_local", "multicast", "reserved",
    "unspecified", "shared", "documentation",
]


class IpLookupRequest(BaseModel):
    # POSTed rather than put in a query string so it stays out of access logs.
    target: str = Field(..., min_length=1, max_length=300)


class IpLocation(BaseModel):
    city: str | None = None
    region: str | None = None
    country: str | None = None
    country_code: str | None = None
    continent: str | None = None
    in_eu: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    # Only GeoLite2 carries these two; DB-IP Lite leaves them empty.
    accuracy_km: int | None = None
    timezone: str | None = None


class IpNetwork(BaseModel):
    asn: int | None = None
    as_org: str | None = None  # the network operator: the ISP, host or company
    prefix: str | None = None  # the routed block the address sits in


class IpRegistration(BaseModel):
    registry: str | None = None  # ARIN, RIPE NCC, APNIC, LACNIC, AFRINIC
    handle: str | None = None
    name: str | None = None  # the network's registered name, e.g. GOGL
    type: str | None = None  # DIRECT ALLOCATION, ASSIGNED PA, ...
    range: str | None = None  # "first - last"
    cidrs: list[str] = Field(default_factory=list)
    country: str | None = None
    org: str | None = None
    org_address: str | None = None
    abuse_email: str | None = None
    registered: str | None = None
    last_changed: str | None = None


class IpResult(BaseModel):
    ip: str
    version: int
    scope: IpScope
    location: IpLocation | None = None
    network: IpNetwork | None = None
    registration: IpRegistration | None = None
    ptr: str | None = None  # reverse DNS
    # The PTR name resolves back to this address. Only then is it trustworthy:
    # whoever controls the reverse zone can put any name there.
    ptr_confirmed: bool | None = None
    tor_exit: bool | None = None  # None = the exit list could not be fetched
    # source key -> why it gave nothing for this address
    errors: dict[str, str] = Field(default_factory=dict)


class IpSource(BaseModel):
    key: str
    label: str
    ok: bool
    status: str


class IpLookupResponse(BaseModel):
    query: str
    kind: Literal["ip", "hostname"]
    hostname: str | None = None
    # Addresses the hostname resolved to beyond those looked up.
    more_addresses: list[str] = Field(default_factory=list)
    results: list[IpResult]
    sources: list[IpSource]
    attribution: list[str]


# ─────────────────────────── phone lookup ───────────────────────────
# Shaped like VeriRoute Intel's LRN answer, object for object, so the console
# and the raw response read the same: lrn, enhanced_lrn, messaging, cnam, trust.

class PhoneLookupRequest(BaseModel):
    # POSTed rather than put in a query string so it stays out of access logs.
    number: str = Field(..., min_length=1, max_length=40)


class PhoneEnhancedLrn(BaseModel):
    carrier: str | None = None
    carrier_type: str | None = None  # WIRELESS, LANDLINE, VOIP, ...
    city: str | None = None
    county: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country_code: str | None = None
    timezone: str | None = None  # a UTC offset, "-0500"
    rate_center: str | None = None
    lata: str | None = None
    ocn: str | None = None  # Operating Company Number of the serving carrier


class PhoneMessaging(BaseModel):
    provider: str | None = None
    enabled: bool | None = None
    country: str | None = None
    country_code: str | None = None
    reference_id: str | None = None


class PhoneTrust(BaseModel):
    is_spam: bool | None = None
    is_robocall: bool | None = None
    is_scam: bool | None = None
    spam_type: str | None = None  # NONE, SPAM, ROBOCALL, SCAM, TELEMARKETER
    reputation_score: int | None = None  # 0-100, higher = more trustworthy
    trust_level: str | None = None  # high >= 70, medium 40-69, low < 40
    verdict_status: str | None = None
    last_updated: str | None = None


class PhoneLookupResponse(BaseModel):
    query: str
    phone_number: str  # 11 digits, as VeriRoute takes it
    e164: str
    national: str  # (336) 408-6644
    lrn: str | None = None
    # When the number last ported to the carrier now serving it. Absent for a
    # number that never left its original carrier.
    lrn_activated_at: str | None = None
    line_type: Literal["mobile", "landline", "voip", "toll_free", "unknown"] = "unknown"
    cnam: str | None = None  # caller ID name
    enhanced_lrn: PhoneEnhancedLrn | None = None
    messaging: PhoneMessaging | None = None
    trust: PhoneTrust | None = None
    cached: bool = False
    looked_up_at: str
    # The add-ons this lookup asked for; one left out reads "not requested",
    # not "nothing found".
    requested: list[str]
    raw: dict[str, Any]
    attribution: list[str]


# ─────────────────────────── support tickets ───────────────────────────

TicketReason = Literal["billing", "technical", "other"]
TicketStatus = Literal["open", "resolved", "closed"]


class TicketCreate(BaseModel):
    reason: TicketReason
    subject: str
    message: str


class TicketReply(BaseModel):
    message: str


class TicketStatusUpdate(BaseModel):
    status: TicketStatus


class TicketMessage(BaseModel):
    id: int
    author_label: str
    is_staff: bool
    body: str
    created_at: str


class Ticket(BaseModel):
    id: int
    reason: TicketReason
    subject: str
    status: TicketStatus
    created_at: str
    updated_at: str
    message_count: int
    # Only populated for staff — whose ticket this is.
    user_email: str | None = None
    user_username: str | None = None


class TicketDetail(Ticket):
    messages: list[TicketMessage]


# ─────────────────────────── spider (correlation / pivoting) ───────────────────────────

SpiderSeedKind = Literal["email", "username", "domain", "name", "auto"]


class SpiderScanRequest(BaseModel):
    # The identifier to start from. `kind=auto` detects it from the seed's shape
    # (the same detector the leak search uses).
    seed: str = Field(..., min_length=2, max_length=320)
    kind: SpiderSeedKind = "auto"
    # Restrict the scan to these modules; omitted = every configured module.
    modules: list[str] | None = None
    # Cap the graph below the server's own ceiling (never above it).
    max_nodes: int | None = Field(None, ge=2, le=200)


class SpiderSeen(BaseModel):
    """One earlier scan by this account that held the same identifier."""
    scan_id: int
    at: str
    seed: str


class SpiderNode(BaseModel):
    type: str
    value: str
    label: str = ""
    depth: int = 0
    sources: list[str] = Field(default_factory=list)
    detail: str | None = None
    url: str | None = None
    masked: bool = False
    # Earlier scans this value showed up in — the "featured in a previous
    # search" signal. Empty when it's new to this account.
    seen_before: list[SpiderSeen] = Field(default_factory=list)


class SpiderEdge(BaseModel):
    src: list[str]  # [type, value] of the source node
    dst: list[str]  # [type, value] of the destination node
    source: str     # module key that found the link
    label: str = ""


class SpiderGraph(BaseModel):
    nodes: list[SpiderNode] = Field(default_factory=list)
    edges: list[SpiderEdge] = Field(default_factory=list)


class SpiderStats(BaseModel):
    nodes: int = 0
    edges: int = 0
    lookups: int = 0
    # True when a limit (nodes / lookups / depth) stopped the expansion early.
    truncated: bool = False


class SpiderJob(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "error"]
    seed: str
    kind: SpiderSeedKind
    progress: float = 0.0
    message: str = ""
    # Modules this scan is running, named for the live fan-out display.
    modules: list[dict] = Field(default_factory=list)
    graph: SpiderGraph = Field(default_factory=SpiderGraph)
    stats: SpiderStats | None = None
    # Set once the scan is saved, so the console can jump to it in history.
    scan_id: int | None = None
    error: str | None = None


class SpiderScanSummary(BaseModel):
    id: int
    seed: str
    seed_kind: str
    title: str | None = None
    modules: list[str] = Field(default_factory=list)
    node_count: int
    edge_count: int
    created_at: str


class SpiderScanDetail(SpiderScanSummary):
    graph: SpiderGraph = Field(default_factory=SpiderGraph)
