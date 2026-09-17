# DECINT — first install, step by step

Written assuming you have never deployed anything. ~45 minutes, mostly waiting.

Every command below says **where** it runs. Running one in the wrong place is
the most common way this goes wrong.

---

## What you are building

Four programs on one machine:

- **Caddy** — the doorman, and the only thing reachable from the internet. Gets
  your HTTPS certificate automatically, then passes each request inward:
  anything starting `/api/` to the backend, everything else to the website.
- **Next.js** — serves the pages. `127.0.0.1:3000`
- **FastAPI** — the backend: OSINT tools, accounts, searches. `127.0.0.1:8000`
- **Tor** — so dark-web search has a live circuit. `127.0.0.1:9050`

`127.0.0.1` means *this machine only*. The backend and website cannot be reached
from the internet at all — only Caddy can talk to them. That is why the firewall
only ever opens ports 22, 80 and 443.

**systemd** is the part of Ubuntu that keeps programs running and restarts them
if they crash. `systemctl restart decint-web` is you talking to systemd.

---

## Before you start

- [ ] Your server's IP, and `ssh box` working without a password prompt
- [ ] Your domain, and the Cloudflare login that manages its DNS
- [ ] A GitHub account, and `git --version` working on Windows
- [ ] An email + password you will use as the DECINT admin — decide now
- [ ] ~45 uninterrupted minutes

---

## Phase 1 — Put your code on GitHub  (on your PC, ~10 min)

Your project exists only on your PC. The server needs a way to fetch it.

**1.1** Go to <https://github.com/new>. Name it `decint-tools`. Choose
**Private**. Do *not* add a README or .gitignore — you have both.

**1.2** In PowerShell, from your project folder:

```powershell
cd $env:USERPROFILE\decint-tools
git init
git branch -M main
```

Confirm your secrets are excluded. **This must print nothing:**

```powershell
git status --porcelain | Select-String "\.env$|node_modules|\.venv|\.next"
```

> **Checkpoint.** Nothing printed. Your `.gitignore` already excludes `.env`,
> private keys, `node_modules`, the virtualenv and the analytics database.
> If something *did* print, stop — do not push.

**1.3** Commit and push:

```powershell
git add -A
git commit -m "DECINT: initial commit"
git remote add origin https://github.com/YOURNAME/decint-tools.git
git push -u origin main
```

If git asks for a password in the terminal, use a
[personal access token](https://github.com/settings/tokens) — GitHub stopped
accepting account passwords for this in 2021.

> **Checkpoint.** Your repo on GitHub shows `backend/`, `frontend/`, `deploy/`,
> `scripts/`. Inside `backend/` you see `.env.example` but **no `.env`**.

---

## Phase 2 — Point your domain at the server  (~5 min, then wait)

Do this **before** the install. Caddy proves you own the domain by answering a
challenge on it, so the domain must already point at your server.

**2.1** Find the server's IP:

```powershell
ssh box 'curl -s https://api.ipify.org; echo'
```

**2.2** In Cloudflare → your domain → **DNS ▸ Records**, add two:

| Type | Name  | IPv4       | Proxy status              |
|------|-------|------------|---------------------------|
| A    | `@`   | server IP  | **DNS only** (grey cloud) |
| A    | `www` | server IP  | **DNS only** (grey cloud) |

> **The Cloudflare trap.** New records default to *Proxied* (orange cloud).
> Leave it on and your certificate will fail to issue, with an error that never
> mentions Cloudflare. Click the cloud so it turns **grey**. You can re-enable
> proxying once the site is live.

**2.3** Verify:

```powershell
Resolve-DnsName decint.tools -Type A -Server 1.1.1.1
```

> **Checkpoint.** The `IPAddress` is your server's IP. If not, wait two minutes
> and try again. **Do not start Phase 3 until this matches** — almost every
> failed install is really this step not having finished.

---

## Phase 3 — Run the installer  (~15 min, mostly waiting)

```powershell
ssh -t box 'sudo bash -s' < .\deploy\decint-server-install.sh
```

The `-t` is load-bearing: it gives the script a real terminal so it can ask
questions and take a password. Without it the last step fails.

It asks for:

| Question | Answer with |
|---|---|
| Your domain | `decint.tools` — no `https://`, no trailing slash |
| Your admin email | The address you will sign in with |
| Git repo URL | The `https://github.com/...` URL from Phase 1 |
| Password (at the end) | Your admin password, typed twice |

Twelve steps scroll past. **Step 10, building the frontend, goes quiet for 1–3
minutes — that is normal, do not interrupt it.** If your server has under 2GB of
RAM the script adds a swapfile first, because that build runs out of memory on
small machines and fails confusingly.

If you used an SSH-style repo URL (`git@github.com:...`), the script prints a key
and waits: copy it into your repo → **Settings ▸ Deploy keys ▸ Add deploy key**,
leave *Allow write access* unticked, then press Enter.

> **Checkpoint.** A green **INSTALL COMPLETE** box. If it stopped early it names
> the line it failed on — fix that and run the same command again; it resumes
> rather than starting over.

---

## Phase 4 — First sign-in  (~5 min)

Open `https://decint.tools` in your browser. The first load can take 10–20
seconds while Caddy fetches the certificate; refresh once if it stalls.

Sign in with the email and password from the install.

> **Checkpoint.** You are in the console, the tab icon is the violet shield, and
> the address bar shows a padlock.

**Turn on your second factor now**, not later — you are an admin on a public
OSINT tool. Enrol an authenticator app from inside the console.

New signups arrive as `pending` and cannot sign in until you approve them:

```bash
cd /opt/decint-tools/backend
sudo -u decint .venv/bin/python -m app.cli list
sudo -u decint .venv/bin/python -m app.cli create-user --email someone@example.com
```

Optionally, switch the Cloudflare records back to the orange cloud now that the
certificate exists — then check the site still loads.

---

## Running it from here

```powershell
ssh box 'systemctl is-active decint-api decint-web caddy tor'   # four x active
ssh -t box 'journalctl -u decint-api -f'                        # live log
ssh box 'journalctl -u decint-web -n 50'
```

Ship a change:

```powershell
git add -A; git commit -m "what changed"; git push origin main
ssh -t box 'sudo -u decint git -C /opt/decint-tools pull'
ssh -t box 'sudo -u decint bash -c "cd /opt/decint-tools/frontend && npm ci && npm run build"'
ssh -t box 'sudo systemctl restart decint-web'
```

The rebuild is the step that matters — pulling alone changes nothing a visitor
sees, because Next.js serves compiled output rather than source. Backend-only
change? No build; just `sudo systemctl restart decint-api`.

---

## When something is wrong

| What you see | What it means | What to do |
|---|---|---|
| Site unreachable | DNS not pointing at the server | Re-run the Phase 2 check — the most common cause by far |
| Certificate warning | Caddy couldn't get a cert, usually the orange cloud | Set DNS records grey, `sudo systemctl restart caddy` |
| 502 Bad Gateway | Caddy is up, the app behind it isn't | `systemctl status decint-web decint-api` |
| `decint-web` won't start | Frontend build didn't finish | `journalctl -u decint-web -n 40`, re-run the build |
| Build killed, no error | Out of memory | Re-run the installer; it adds swap |
| Dark-web search fails only | Tor isn't running | `sudo systemctl restart tor` |
| Can't sign in | 5 failures locks the account 15 min | Wait, or `app.cli set-password` |
| Signed in then out again | Cookie needs HTTPS, you used HTTP | Use the `https://` address |
| Old design showing | Browser cache | Private window, or Ctrl-Shift-R |

When asking for help, send the relevant `journalctl` output. That says what
actually happened; the browser error almost never does.
