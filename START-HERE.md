# START HERE

This is the complete DECINT project — frontend, backend, tools, and everything
needed to put it on a server.

## What is in this bundle

```
frontend/    Next.js 14 site + console        (the "empty shield" design)
backend/     FastAPI service + vendored OSINT tools
deploy/      decint-server-install.sh, Caddyfile, systemd units, runbook
scripts/     make_favicon.py — regenerates the icon set
tools/       install.py, verify.py, decint.py and friends (development)
docs/        design notes and the deploy walkthrough
```

## I want to put this on my server

You need three things first: a fresh Ubuntu VPS you can SSH into, a domain whose
DNS points at that server, and this code pushed to a git repository.

Then, one command:

```bash
sudo bash deploy/decint-server-install.sh
```

From Windows without copying it up first:

```powershell
ssh -t box 'sudo bash -s' < .\deploy\decint-server-install.sh
```

It asks for your domain, your admin email, and your repo URL, then installs and
starts everything and creates your admin account. Takes about 15 minutes, most
of it waiting on the frontend build.

**Read `docs/INSTALL-WALKTHROUGH.md` first if you have not deployed before.** It
covers the two steps that happen *before* the script — getting the code onto
GitHub and pointing DNS at the server — and those are where first installs
actually go wrong.

`deploy/README.md` is the reference for what the script does and how to repair
it by hand.

## I want to run it locally to develop

```bash
python3 tools/install.py --system    # apt packages + Tor (needs sudo)
python3 tools/install.py             # node, venv, npm install
python3 tools/decint.py serve dev    # API :8000 + web :3000
```

Then open http://localhost:3000.

Do **not** use `tools/install.py` on a server — it installs Node via nvm, which
systemd cannot see, and it never builds the frontend.

## Three things that will catch you out

**`OPERATOR_TOKEN` empty means authentication is switched off entirely.**
`backend/.env.example` ships it blank, which is fine for a solo local box and
catastrophic on a public one. The install script generates a random value; if
you configure by hand, set it yourself.

**If your DNS is on Cloudflare, set the A records to DNS-only (grey cloud) until
the certificate is issued.** With the orange proxy on, Cloudflare answers the
certificate challenge instead of your server and issuance fails with an error
that never mentions Cloudflare.

## Config

Everything lives in `backend/.env` — copy `backend/.env.example` and edit. Every
value is documented in place. It is mode 600, git-ignored, and per-machine: it
should never leave the server it belongs to.

## Health check

```bash
curl -s https://YOURDOMAIN/api/v1/health | python3 -m json.tool
```

Expect `auth_enabled: true`, `tor: true`.

For a fuller check, `python3 tools/verify.py` exercises the API, the design
tokens and the icon set against a running instance.

## Running the tests

Most of the `test_*.py` files in `backend/` are **scripts**, not pytest modules —
they hold state at module level and share a process, so running them together
under pytest makes the second one fail on the first one's leftovers. Run them
individually:

```bash
cd backend
.venv/bin/python test_security.py
.venv/bin/python test_accounts.py
.venv/bin/python test_moderation.py
.venv/bin/python test_email_flows.py
.venv/bin/python test_profile.py        # profile page: change password / email
.venv/bin/python -m pytest -q tests/test_units.py
.venv/bin/python -m pytest -q tests/darkweb      # dark-web engine: offline, replayed engine pages
```

The `tests/` modules are pytest, but a few keep state at module level too (billing,
activation, usage): run those one file per invocation, as above. `tests/darkweb` needs
the dev extras once: `.venv/bin/pip install -r requirements-dev.txt`.

All of them pass on a clean checkout. `pytest test_*.py` in one invocation does not
— that is the harness, not the code.
