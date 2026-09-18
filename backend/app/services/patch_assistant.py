"""The Patch tab's assistant: Anthropic's Messages API, nothing else.

This module is intentionally read-only with respect to the server. It sends
the admin's conversation to Claude and returns the reply text - it never opens
files, runs a shell, touches git, or calls the executor directly. Per the
owner's explicit choice (propose-only, no auto-apply), the *only* downstream
effect a reply can have is the operator clicking "Save as draft Method" in the
frontend, which stages the code Claude wrote as a PRIVATE, unexecuted Method
via the same admin-gated create/upload-version endpoints any human would use
(routers/executor.py) - it still has to be opened and run from the Methods tab
like anything else there. This module has no path to create, upload, or
execute a Method itself.
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
    "cannot run code or touch the server yourself - you can only draft a "
    "Method for the operator to review and run manually from the Methods tab. "
    "\n\n"
    "If, and only if, the operator wants an actual runnable script: give a "
    "short name for it on its own line as `Suggested method name: <name>`, "
    "then one fenced ```python code block with the complete script (it must "
    "run standalone via `python3 script.py [--arg value ...]`, matching "
    "argparse-style flags). Do not use that name-line format for anything "
    "else. For everything else - explaining, planning, discussing tradeoffs - "
    "just answer in plain prose with no code block. Be concise."
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
