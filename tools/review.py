#!/usr/bin/env python3
"""Second-opinion code review through Venice.ai.

Sends files to a Venice-hosted model and prints what it flags. This is an
*advisory* pass — a cheap extra reader, not a gate. Nothing here blocks a
deploy and nothing is auto-applied.

    python3 tools/review.py backend/app/auth.py
    python3 tools/review.py backend/app/routers/*.py
    python3 tools/review.py --model qwen3-coder-480b-a35b-instruct-turbo FILE

This repo has no git history, so there is no `--diff` mode: review takes
explicit paths. If it is ever put under git, a diff mode is the obvious
addition.

The key lives in backend/.env as VENICE_API_KEY (mode 600, git-ignored) so it
is never passed on a command line — argv is world-readable via /proc, and a key
in shell history outlives the session that typed it.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import BACKEND, ROOT, die, err, head, info, ok, warn  # noqa: E402

API_URL = "https://api.venice.ai/api/v1/chat/completions"
DEFAULT_MODEL = "qwen-3-8-27b"

# Sent as the system prompt. The explicit "say nothing" clause matters: without
# it the model pads every review with restated code and generic advice, which
# buries the two real findings in forty lines of noise.
SYSTEM = """You are reviewing code for a small production OSINT service.

Report ONLY concrete defects you can point at:
  - correctness bugs (wrong logic, unhandled error, race, off-by-one)
  - security issues (injection, authz gap, leaked secret, unsafe default)
  - resource bugs (unclosed handle, unbounded growth, N+1)

For each finding give: file:line, one sentence on the defect, and a concrete
failure case (inputs -> wrong result).

Do NOT report style, naming, formatting, or missing type hints. Do NOT restate
what the code does. Do NOT invent line numbers you are unsure of. If you find
nothing real, reply exactly: NO FINDINGS.
"""

# Roughly 100k chars ~ 25k tokens. Above this the model starts losing the early
# files entirely rather than degrading gracefully, so refuse instead of
# silently reviewing half the input.
MAX_CHARS = 100_000


def api_key() -> str:
    """Read VENICE_API_KEY from backend/.env.

    Parsed by hand rather than with python-dotenv: this tool must run with the
    system python before the venv exists, and .env here is plain KEY=value.
    """
    env = BACKEND / ".env"
    if not env.exists():
        die(f"{env} not found")
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("VENICE_API_KEY=") and not line.startswith("#"):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    die("VENICE_API_KEY is empty or missing in backend/.env")


def gather(paths: list[str]) -> tuple[str, int]:
    """Read the given files into one annotated blob, with line numbers.

    Line numbers are prefixed so the model can cite them; without them it
    guesses, and a citation you cannot trust is worse than none.
    """
    chunks, total, n = [], 0, 0
    for p in paths:
        f = Path(p)
        if not f.is_absolute():
            f = ROOT / p
        if not f.is_file():
            warn(f"skipped (not a file): {p}")
            continue
        try:
            body = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            warn(f"skipped ({e.__class__.__name__}): {p}")
            continue
        rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
        numbered = "\n".join(
            f"{i:5} {ln}" for i, ln in enumerate(body.splitlines(), 1)
        )
        chunk = f"\n--- {rel} ---\n{numbered}\n"
        total += len(chunk)
        if total > MAX_CHARS:
            die(
                f"input too large ({total} chars, limit {MAX_CHARS}). "
                "Review fewer files per run — a truncated review silently "
                "skips whatever landed past the cut."
            )
        chunks.append(chunk)
        n += 1
    if not n:
        die("no readable files given")
    return "".join(chunks), n


def ask(key: str, model: str, code: str) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": code},
            ],
            "temperature": 0.1,  # review should be reproducible, not creative
            "max_tokens": 2000,
        }
    ).encode()

    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        die(f"Venice returned HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        die(f"could not reach Venice: {e.reason}")
    except TimeoutError:
        die("Venice timed out after 180s — try fewer files")

    # Venice returns 200 with an {"error": ...} body for overload, so a status
    # check alone is not enough.
    if "error" in body:
        die(f"Venice error: {body['error']}")
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        die(f"unexpected response shape: {json.dumps(body)[:400]}")


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    model = DEFAULT_MODEL
    if args[0] == "--model":
        if len(args) < 3:
            die("--model needs a value and at least one file")
        model = args[1]
        args = args[2:]

    key = api_key()
    code, n = gather(args)

    head(f"reviewing {n} file(s) via {model}")
    info(f"{len(code):,} chars sent")
    verdict = ask(key, model, code)

    print()
    if verdict.strip() == "NO FINDINGS":
        ok("no findings")
    else:
        print(verdict)
        print()
        warn("advisory only — a second model's opinion, not a verified defect list.")
        warn("confirm each finding against the code before acting on it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
