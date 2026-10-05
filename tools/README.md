# DECINT tools

Runnable Python CLIs for installing, running, verifying and shipping the site.
They replace the old `deploy/*.sh` scripts.

**Why Python instead of shell:** the shell versions kept breaking on quoting
once they were invoked through `wsl -d Ubuntu -- bash -lc '...'` — each layer
ate an escape level, and in a couple of cases a mangled command *silently
reported a false pass*. `subprocess` takes an argument list, so nothing is
re-parsed by a shell and the same command behaves identically on Windows, in
WSL, and on the server.

## One entry point

```bash
python tools/decint.py <command> [args...]
```

| Command | What it does |
|---|---|
| `install` | Node LTS (nvm), Python venv + deps, npm install, seeds `.env`. `--system` also does apt + Tor. |
| `serve` | `dev` · `start` · `stop` · `restart` · `status` · `build` · `logs` |
| `verify` | `all` · `auth` · `api` · `analytics` · `theme` · `icons` · `offline` |
| `accounts` | `list` · `create-admin` · `create-user` · `set-role` · `set-tier` · `set-password` · `reset-mfa` · `audit` |
| `assets` | Regenerate the favicon/icon set from `scripts/make_favicon.py` |
| `snapshot` | Export the website source to a folder (optionally zipped) |
| `osint` | Run a vendored OSINT tool directly |

Each is also runnable standalone — `python tools/serve.py status` works the
same as going through `decint.py`.

## Typical session

```bash
python tools/decint.py install            # first time only
python tools/decint.py serve dev          # API :8000 + web :3000, Ctrl-C stops
python tools/decint.py verify all         # 39 checks
```

Detached instead of foreground:

```bash
python tools/decint.py serve start
python tools/decint.py serve status
python tools/decint.py serve logs web -n 50
python tools/decint.py serve stop
```

## Accounts

```bash
python tools/decint.py accounts create-admin --email you@decint.tools
python tools/decint.py accounts list
python tools/decint.py accounts set-tier --email client@example.com --tier professional
python tools/decint.py accounts audit -n 30
```

Passwords are always read from a prompt, never from `argv`, so they stay out of
shell history.

## OSINT tools directly

The vendored engines are runnable without the web app:

```bash
python tools/decint.py osint list
python tools/decint.py osint rerank "leaked database" --limit 5   # ahmia, no Tor
python tools/decint.py osint darkweb "keyword" --self-test        # Tor engine
```

## Export a copy

```bash
python tools/decint.py snapshot --out /mnt/c/Users/you/Desktop/decint-v3 --zip
```

Omits build artifacts, the analytics database (visitor IPs), the GeoLite2
files, and `.env` — `.env.example` is copied instead, so the result is safe to
archive or hand over.

## Verify against the live server

`verify` takes a base URL, so the same suite works against production:

```bash
python tools/decint.py verify auth --base https://decint.tools
```
