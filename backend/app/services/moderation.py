"""Username / display-text moderation.

Two jobs:

1. **Reject** usernames at signup that are profane, that impersonate staff, or
   that are reserved system words.
2. **Censor** free text before it is displayed, for anywhere user-controlled
   strings reach a screen.

The important part is `normalize()`. A naive substring blocklist is trivially
defeated — `sh1t`, `f.u.c.k`, `AAAdmin`, `аdmin` (Cyrillic а). So before any
comparison the input is folded: unicode-normalised, homoglyphs mapped to their
Latin lookalikes, leetspeak digits mapped to letters, separators stripped, and
runs of a repeated character collapsed. Matching then happens on that folded
form, while the *censored output* preserves the original text's shape.

The word lists are intentionally editable at runtime — see
`backend/config/moderation.json`. Every deployment wants a slightly different
line, and shipping a hardcoded list means nobody can move it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

# ─────────────────────────── normalisation ───────────────────────────

# Homoglyphs: characters that render like a Latin letter but aren't one.
_HOMOGLYPHS = {
    "а": "a", "ᴀ": "a", "ⓐ": "a", "α": "a", "å": "a", "á": "a", "à": "a", "â": "a", "ä": "a",
    "ь": "b", "β": "b", "б": "b",
    "с": "c", "ç": "c", "ⅽ": "c",
    "ԁ": "d", "ⅾ": "d",
    "е": "e", "é": "e", "è": "e", "ê": "e", "ë": "e", "ε": "e",
    "ɡ": "g", "ģ": "g",
    "һ": "h",
    "і": "i", "ı": "i", "í": "i", "ì": "i", "î": "i", "ï": "i", "ⅰ": "i",
    "ј": "j",
    "κ": "k", "к": "k",
    "ⅼ": "l", "ł": "l",
    "м": "m", "ⅿ": "m",
    "ո": "n", "ñ": "n", "η": "n",
    "о": "o", "ο": "o", "ó": "o", "ò": "o", "ô": "o", "ö": "o", "ø": "o", "θ": "o",
    "р": "p", "ρ": "p",
    "ԛ": "q",
    "г": "r", "я": "r",
    "ѕ": "s", "š": "s", "ș": "s",
    "т": "t", "τ": "t",
    "ս": "u", "ú": "u", "ù": "u", "û": "u", "ü": "u", "μ": "u",
    "ν": "v", "ⅴ": "v",
    "ѡ": "w", "ω": "w",
    "х": "x", "χ": "x", "ⅹ": "x",
    "у": "y", "ý": "y", "ÿ": "y",
    "ᴢ": "z", "ž": "z",
}

_LEET = str.maketrans({
    "4": "a", "@": "a", "^": "a",
    "8": "b",
    "(": "c", "<": "c", "{": "c",
    "3": "e", "€": "e",
    "6": "g", "9": "g",
    "1": "i", "!": "i", "|": "i", "l": "i",  # l/1/i collapse together
    "0": "o",
    "5": "s", "$": "s",
    "7": "t", "+": "t",
    "2": "z",
})

_STRIP = re.compile(r"[^a-z0-9]+")
_RUNS = re.compile(r"(.)\1{1,}")


def normalize(text: str) -> str:
    """Fold text to its comparison form. Not reversible — matching only."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", text).lower()
    s = "".join(_HOMOGLYPHS.get(ch, ch) for ch in s)
    # Drop combining marks left over from NFKD (e.g. zalgo).
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.translate(_LEET)
    s = _STRIP.sub("", s)
    s = _RUNS.sub(r"\1", s)          # fuuuuck -> fuck, aaadmin -> adminx
    return s


# ─────────────────────────── word lists ───────────────────────────

# Folded forms. Because normalize() collapses repeats and maps l->i, entries
# here must themselves be written in folded form to match.
DEFAULT_PROFANITY = [
    "fuck", "shit", "cunt", "bitch", "bastard", "asshole", "dickhead",
    "motherfucker", "wanker", "twat", "prick", "slut", "whore", "pussy",
    "cock", "dick", "penis", "vagina", "anus", "rape", "rapist",
    "nigger", "nigga", "faggot", "fag", "retard", "tranny", "chink",
    "spic", "kike", "gook", "wetback", "coon", "paki",
    "pedo", "pedophile", "childporn", "cp", "kys", "killyourself",
    "nazi", "hitler", "isis",
]

# Words nobody should be able to register, because they imply authority or
# collide with system routes.
DEFAULT_RESERVED = [
    "admin", "administrator", "adminstrator", "sysadmin", "root", "superuser",
    "staff", "moderator", "mod", "owner", "operator", "official", "support",
    "helpdesk", "security", "abuse", "billing", "payments", "legal",
    "system", "daemon", "service", "bot", "webmaster", "hostmaster",
    "postmaster", "noreply", "nobody", "anonymous", "guest",
    "decint", "decinttools", "decintsupport", "decintstaff", "decintadmin",
    "api", "www", "mail", "ftp", "console", "login", "logout", "signup",
    "register", "account", "settings", "dashboard", "null", "undefined",
    "me", "self", "team",
]

_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "moderation.json"


@lru_cache(maxsize=1)
def _lists() -> tuple[list[str], list[str]]:
    # Both sides of every comparison MUST go through normalize(), including the
    # word lists themselves. Folding collapses repeated letters, so a literal
    # "nigger" in the list would never match the folded input "niger" — the
    # list is written in readable form and folded here exactly once.
    profanity = [normalize(w) for w in DEFAULT_PROFANITY]
    reserved = [normalize(w) for w in DEFAULT_RESERVED]
    try:
        if _CONFIG.exists():
            raw = json.loads(_CONFIG.read_text(encoding="utf-8"))
            profanity += [normalize(w) for w in raw.get("profanity_extra", [])]
            reserved += [normalize(w) for w in raw.get("reserved_extra", [])]
            for w in raw.get("allow", []):
                n = normalize(w)
                if n in profanity:
                    profanity.remove(n)
                if n in reserved:
                    reserved.remove(n)
    except (OSError, ValueError):
        pass
    return profanity, reserved


def reload_lists() -> None:
    _lists.cache_clear()


# ─────────────────────────── checks ───────────────────────────

def find_profanity(text: str) -> list[str]:
    folded = normalize(text)
    profanity, _ = _lists()
    return [w for w in profanity if w and w in folded]


def is_profane(text: str) -> bool:
    return bool(find_profanity(text))


def find_reserved(text: str) -> list[str]:
    """Reserved words match the WHOLE folded name, or a folded name that is the
    reserved word plus decoration (admin_01, official-decint). Substring-only
    matching would reject 'modern' for containing 'mod'."""
    folded = normalize(text)
    _, reserved = _lists()
    hits = []
    for w in reserved:
        if not w:
            continue
        if folded == w:
            hits.append(w)
        elif folded.startswith(w) and folded[len(w):].isdigit():
            hits.append(w)
        elif len(w) >= 5 and w in folded:
            hits.append(w)
    return hits


def staff_names() -> set[str]:
    """Folded usernames and email local-parts of real admins/operators, so a
    user cannot register a lookalike of an actual staff account."""
    from .. import db

    out: set[str] = set()
    try:
        rows = db.query(
            "SELECT username, email FROM users WHERE role IN ('admin','operator')"
        )
    except Exception:
        return out
    for r in rows:
        if r.get("username"):
            out.add(normalize(r["username"]))
        if r.get("email"):
            out.add(normalize(r["email"].split("@")[0]))
    return {s for s in out if len(s) >= 3}


USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,24}$")


def check_username(name: str) -> str | None:
    """Return a rejection reason, or None if the name is acceptable."""
    if not name or not USERNAME_RE.match(name):
        return "Username must be 3–24 characters, using letters, numbers, dot, dash or underscore."
    folded = normalize(name)
    if len(folded) < 2:
        return "That username isn't distinctive enough."
    if find_profanity(name):
        return "That username contains language we don't allow."
    if find_reserved(name):
        return "That username is reserved."
    if folded in staff_names():
        return "That username is reserved."
    return None


def check_text(text: str) -> str | None:
    if find_profanity(text):
        return "That text contains language we don't allow."
    return None


# ─────────────────────────── censoring ───────────────────────────

def censor(text: str, mask: str = "*") -> str:
    """Mask profanity in free text for display, preserving the original shape.

    Works by folding each candidate span rather than the whole string, so the
    original spacing/punctuation survives and the offsets stay aligned.
    """
    if not text:
        return text
    profanity, _ = _lists()
    if not profanity:
        return text

    out = list(text)
    # Scan word-ish spans (letters, digits and common separators between them).
    for m in re.finditer(r"[^\s]+", text):
        span = m.group(0)
        folded = normalize(span)
        if not folded:
            continue
        if any(w and w in folded for w in profanity):
            for i in range(m.start(), m.end()):
                if not out[i].isspace():
                    out[i] = mask
    return "".join(out)


def censor_staff(text: str, mask: str = "*") -> str:
    """Also mask real staff/admin names — for anywhere untrusted text is shown
    back to other users, so an account cannot be used to impersonate by
    reference."""
    names = staff_names()
    if not names:
        return text
    out = list(text)
    for m in re.finditer(r"[^\s]+", text):
        if normalize(m.group(0)) in names:
            for i in range(m.start(), m.end()):
                if not out[i].isspace():
                    out[i] = mask
    return "".join(out)


def clean(text: str) -> str:
    return censor_staff(censor(text))
