"""Lightweight Discord OSINT.

  * Snowflake decode — pure arithmetic, no token, always available.
  * User lookup      — GET /users/{id}      (needs a bot token)
  * Invite lookup    — GET /invites/{code}  (public; token optional)
  * Guild widget     — GET /guilds/{id}/widget.json  (public if enabled)

Without DISCORD_BOT_TOKEN, user lookups are unavailable and the service
degrades to snowflake + public invite/widget only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..config import settings
from ..models import (
    DiscordInvite,
    DiscordLookupResponse,
    DiscordUser,
    SnowflakeInfo,
)

DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01T00:00:00Z
API = "https://discord.com/api/v10"

# public_flags bit → badge name
USER_FLAGS = {
    0: "Discord Staff",
    1: "Partnered Server Owner",
    2: "HypeSquad Events",
    3: "Bug Hunter Level 1",
    6: "HypeSquad Bravery",
    7: "HypeSquad Brilliance",
    8: "HypeSquad Balance",
    9: "Early Supporter",
    10: "Team User",
    14: "Bug Hunter Level 2",
    16: "Verified Bot",
    17: "Early Verified Bot Developer",
    18: "Moderator Programs Alumni",
    19: "Bot (HTTP interactions)",
    22: "Active Developer",
}


def _auth_headers() -> dict[str, str] | None:
    if not settings.discord_bot_token:
        return None
    return {
        "Authorization": f"Bot {settings.discord_bot_token}",
        "User-Agent": "decint-tools/1.0 (osint research)",
    }


def decode_snowflake(snowflake: str | int) -> SnowflakeInfo:
    sid = int(snowflake)
    ms = (sid >> 22) + DISCORD_EPOCH_MS
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return SnowflakeInfo(
        id=str(sid),
        created_at=dt.isoformat().replace("+00:00", "Z"),
        unix_ms=ms,
        worker_id=(sid & 0x3E0000) >> 17,
        process_id=(sid & 0x1F000) >> 12,
        increment=sid & 0xFFF,
    )


def _decode_flags(flags: int | None) -> list[str]:
    if not flags:
        return []
    return [name for bit, name in USER_FLAGS.items() if flags & (1 << bit)]


def _avatar_url(uid: str, avatar: str | None) -> str | None:
    if not avatar:
        return None
    ext = "gif" if avatar.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.{ext}?size=256"


async def lookup_snowflake(snowflake: str) -> DiscordLookupResponse:
    return DiscordLookupResponse(
        kind="snowflake",
        token_present=bool(settings.discord_bot_token),
        snowflake=decode_snowflake(snowflake),
        note="Creation time is derived from the ID; no token required.",
    )


async def lookup_user(user_id: str) -> DiscordLookupResponse:
    snowflake = decode_snowflake(user_id)
    headers = _auth_headers()
    if not headers:
        return DiscordLookupResponse(
            kind="user",
            token_present=False,
            snowflake=snowflake,
            note="No bot token configured — returning snowflake decode only. "
            "Set DISCORD_BOT_TOKEN for live user lookups.",
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{API}/users/{user_id}", headers=headers)
    if r.status_code == 404:
        return DiscordLookupResponse(
            kind="user", token_present=True, snowflake=snowflake,
            note="User not found.",
        )
    if r.status_code == 401:
        return DiscordLookupResponse(
            kind="user", token_present=True, snowflake=snowflake,
            note="Bot token rejected (401). Check DISCORD_BOT_TOKEN.",
        )
    r.raise_for_status()
    d = r.json()
    user = DiscordUser(
        id=d["id"],
        username=d.get("username"),
        global_name=d.get("global_name"),
        discriminator=d.get("discriminator"),
        avatar=d.get("avatar"),
        avatar_url=_avatar_url(d["id"], d.get("avatar")),
        banner=d.get("banner"),
        accent_color=d.get("accent_color"),
        public_flags=d.get("public_flags"),
        flags_decoded=_decode_flags(d.get("public_flags")),
        bot=d.get("bot"),
        created_at=snowflake.created_at,
    )
    return DiscordLookupResponse(
        kind="user", token_present=True, snowflake=snowflake, user=user
    )


async def lookup_invite(code: str) -> DiscordLookupResponse:
    # accept full URLs too
    code = code.rsplit("/", 1)[-1].strip()
    headers = _auth_headers() or {"User-Agent": "decint-tools/1.0"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{API}/invites/{code}",
            params={"with_counts": "true", "with_expiration": "true"},
            headers=headers,
        )
    if r.status_code == 404:
        return DiscordLookupResponse(
            kind="invite", token_present=bool(settings.discord_bot_token),
            note="Invite not found or expired.",
        )
    r.raise_for_status()
    d = r.json()
    guild = d.get("guild") or {}
    inviter = d.get("inviter") or {}
    guild_id = guild.get("id")
    created = decode_snowflake(guild_id).created_at if guild_id else None
    invite = DiscordInvite(
        code=d.get("code", code),
        guild_id=guild_id,
        guild_name=guild.get("name"),
        channel_name=(d.get("channel") or {}).get("name"),
        approximate_member_count=d.get("approximate_member_count"),
        approximate_presence_count=d.get("approximate_presence_count"),
        inviter=(inviter.get("username") if inviter else None),
        expires_at=d.get("expires_at"),
        created_at=created,
        raw=d,
    )
    return DiscordLookupResponse(
        kind="invite",
        token_present=bool(settings.discord_bot_token),
        snowflake=decode_snowflake(guild_id) if guild_id else None,
        invite=invite,
    )


async def lookup_guild_widget(guild_id: str) -> DiscordLookupResponse:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{API}/guilds/{guild_id}/widget.json",
            headers={"User-Agent": "decint-tools/1.0"},
        )
    snowflake = decode_snowflake(guild_id)
    if r.status_code == 403:
        return DiscordLookupResponse(
            kind="guild_widget", token_present=bool(settings.discord_bot_token),
            snowflake=snowflake, note="Widget is disabled for this guild.",
        )
    if r.status_code == 404:
        return DiscordLookupResponse(
            kind="guild_widget", token_present=bool(settings.discord_bot_token),
            snowflake=snowflake, note="Guild not found.",
        )
    r.raise_for_status()
    return DiscordLookupResponse(
        kind="guild_widget",
        token_present=bool(settings.discord_bot_token),
        snowflake=snowflake,
        widget=r.json(),
    )
