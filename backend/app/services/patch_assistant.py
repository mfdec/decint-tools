"""The Patch tab's assistant: Anthropic's Messages API, nothing else.

This module is intentionally read-only with respect to the server. It sends
the admin's message to Claude and returns the reply text - it does not open
files, run a shell, touch git, or call the executor. That boundary was a
deliberate choice, not an oversight: the admin panel already has an
admin-gated, audited way to run reviewed code (see routers/executor.py). Wiring
a chat box directly to "make any change needed" on a box that also holds
customer payment data would collapse that review step, so this module stops at
"draft a reply" until the owner has spelled out what an execution step is and
isn't allowed to touch. See admin-deploy-state project notes for that
conversation.
"""

from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("decint.patch_assistant")

API_BASE = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = 60.0
MAX_TOKENS = 1024

SYSTEM_PROMPT = (
    "You are the Patch assistant in the DECINT admin panel. You help the "
    "operator think through website/server changes in plain language. You "
    "cannot run code or touch the server yourself - if a change needs code, "
    "describe what should change and where, so the operator (or a reviewed "
    "Method) can apply it. Be concise."
)


def available() -> bool:
    return bool(settings.anthropic_api_key)


def _headers() -> dict:
    return {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


async def reply(history: list[dict[str, str]]) -> str:
    """Send the conversation so far to Claude and return its reply text.

    `history` is a list of {"role": "user"|"assistant", "content": str},
    oldest first. Raises httpx.HTTPStatusError on a non-2xx response.
    """
    if not available():
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    body = {
        "model": settings.patch_assistant_model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": history,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(API_BASE, headers=_headers(), json=body)
        resp.raise_for_status()
        data = resp.json()

    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parts if p) or "(no reply)"
