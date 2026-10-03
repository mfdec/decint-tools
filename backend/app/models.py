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
    discord_enabled: bool
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


# ─────────────────────────── discord ───────────────────────────


class SnowflakeInfo(BaseModel):
    id: str
    created_at: str  # ISO 8601 UTC
    unix_ms: int
    worker_id: int
    process_id: int
    increment: int


class DiscordUser(BaseModel):
    id: str
    username: str | None = None
    global_name: str | None = None
    discriminator: str | None = None
    avatar: str | None = None
    avatar_url: str | None = None
    banner: str | None = None
    accent_color: int | None = None
    public_flags: int | None = None
    flags_decoded: list[str] = Field(default_factory=list)
    bot: bool | None = None
    created_at: str | None = None


class DiscordInvite(BaseModel):
    code: str
    guild_id: str | None = None
    guild_name: str | None = None
    channel_name: str | None = None
    approximate_member_count: int | None = None
    approximate_presence_count: int | None = None
    inviter: str | None = None
    expires_at: str | None = None
    created_at: str | None = None  # from guild id snowflake
    raw: dict[str, Any] = Field(default_factory=dict)


class DiscordLookupResponse(BaseModel):
    kind: Literal["snowflake", "user", "invite", "guild_widget"]
    token_present: bool
    snowflake: SnowflakeInfo | None = None
    user: DiscordUser | None = None
    invite: DiscordInvite | None = None
    widget: dict[str, Any] | None = None
    note: str = ""


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
