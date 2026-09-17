"""Pydantic response/request schemas shared across routers.

Kept deliberately flat and snake_case so the TypeScript client in the
frontend maps to them 1:1.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

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

LeakKind = Literal["email", "username", "domain", "auto"]


class LeakHit(BaseModel):
    source: str  # provider key, e.g. "xposedornot"
    source_label: str
    breach: str | None = None  # named breach / dataset
    email: str | None = None
    username: str | None = None
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

DarkwebMode = Literal["ahmia", "tor"]


class DarkwebSearchRequest(BaseModel):
    query: str
    mode: DarkwebMode = "ahmia"
    verify: bool = False  # tor mode: fetch each onion to confirm live
    limit: int = 25


class DarkwebResult(BaseModel):
    title: str
    url: str
    snippet: str = ""
    score: float = 0.0
    coverage: float | None = None
    corroboration: int | None = None
    sources: list[str] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    live: bool | None = None


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
    results: list[DarkwebResult] = Field(default_factory=list)
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
