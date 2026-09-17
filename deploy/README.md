# Deploy runbook — production server

For a **fresh Ubuntu VPS** you control, with a domain pointed at it.

> **If this is your first install, use the script.** Everything below the first
> section is reference material for understanding or repairing what the script
> did — not a list of things to type by hand.

---

## The short version

```bash
sudo bash deploy/decint-server-install.sh
```

Or, without copying it to the server first:

```powershell
ssh -t box 'sudo bash -s' < .\deploy\decint-server-install.sh
```

It is idempotent — safe to re-run after a failure; it skips whatever is already
done and never overwrites an existing `backend/.env`.

It asks for three things: your domain, your admin email, and your git repo URL.
It ends by prompting for an admin password.

---

## What the script does, in order

| # | Step | Why it matters |
|---|---|---|
| 1 | Checks RAM, adds 2GB swap if under 2GB | The Next.js build OOMs on small VPSes and fails with a confusing error |
| 2 | Collects domain / email / repo | |
| 3 | apt packages + Tor | Dark-web search needs a live Tor SOCKS proxy on :9050 |
| 4 | Node LTS **system-wide** | See the nvm warning below |
| 5 | Caddy | Automatic HTTPS, no certbot to configure |
| 6 | Creates the `decint` system user | No login shell, no password |
| 7 | Clones the repo | Generates a read-only deploy key for SSH URLs |
| 8 | Python venv + `requirements.txt` | |
| 9 | Generates `backend/.env` | Random secrets; five safety-critical values forced |
| 10 | `npm ci && npm run build` | The slow step, 1–3 minutes |
| 11 | Installs + starts both systemd units | With paths rewritten for this machine |
| 12 | Caddyfile, firewall, health check, admin account | |

---

## Things that will bite you if you do it manually

### Do not install Node with nvm

`decint-web.service` runs `npm run start`. A systemd service has no login shell,
so it never sources `~/.nvm/nvm.sh` and cannot find an nvm-installed npm — the
service fails to start with `203/EXEC`.

Use the NodeSource repository so `node` and `npm` live at `/usr/bin`, and point
`ExecStart` at the absolute path. The script does both.

`tools/install.py` **is** nvm-based. That is correct for a development box and
wrong for a server; do not run it here.

### `OPERATOR_TOKEN` empty means authentication is OFF

In `backend/app/config.py`:

```python
@property
def auth_enabled(self) -> bool:
    return bool(self.operator_token)
```

`.env.example` ships it blank, which is deliberate for a solo local box. Copying
the example to `.env` unchanged puts a public server online **with no
authentication at all**. Always set it.

### `SNIFFER_ENABLED` must be false

`.env.example` ships `true`. The packet sniffer captures the *host's own* NIC and
needs raw sockets — meaningful on your workstation, wrong and privileged on a
shared server.

### TLS is a config value, not a code edit

Set `COOKIE_SECURE=true` in `backend/.env`. Earlier versions of this runbook said
to edit `secure=True` in `backend/app/auth.py`; that no longer applies, and such
an edit would be reverted by your next `git pull`.

### Cloudflare's orange cloud breaks certificate issuance

If DNS is on Cloudflare, set the A records to **DNS only** (grey cloud) until the
certificate exists. With proxying on, Cloudflare answers the ACME challenge
instead of your server and issuance fails without mentioning Cloudflare.
Re-enable proxying afterwards if you want it.

---

## Manual equivalent

Only if you are repairing something. Assumes the repo is at `/opt/decint-tools`
owned by a `decint` system user.

```bash
# system
sudo apt update
sudo apt install -y python3-venv python3-pip build-essential libpcap0.8 tor ufw
sudo systemctl enable --now tor

# node (system-wide, NOT nvm)
curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
  | sudo gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg
echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" \
  | sudo tee /etc/apt/sources.list.d/nodesource.list
sudo apt update && sudo apt install -y nodejs

# caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# backend
cd /opt/decint-tools/backend
sudo -u decint python3 -m venv .venv
sudo -u decint .venv/bin/pip install -r requirements.txt
sudo -u decint mkdir -p data/geoip
sudo -u decint cp .env.example .env
sudo -u decint chmod 600 .env
```

Then edit `backend/.env` — at minimum:

```
OPERATOR_TOKEN=<openssl rand -hex 24>
SESSION_SECRET=<openssl rand -hex 32>
COOKIE_SECURE=true
SNIFFER_ENABLED=false
CORS_ORIGINS=https://YOURDOMAIN
PUBLIC_BASE_URL=https://YOURDOMAIN
SIGNUP_DEFAULT_STATUS=pending
```

```bash
# frontend
cd /opt/decint-tools/frontend
sudo -u decint npm ci
sudo -u decint npm run build

# services — edit User/paths/ExecStart to match this machine first
sudo cp deploy/systemd/decint-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now decint-api decint-web

# caddy
sudo sed 's/decint.tools/YOURDOMAIN/' deploy/Caddyfile | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy

# firewall — 3000 and 8000 stay on loopback, never exposed
sudo ufw allow 22,80,443/tcp && sudo ufw enable

# first admin
cd /opt/decint-tools/backend
sudo -u decint .venv/bin/python -m app.cli create-admin --email you@example.com
```

---

## Verify

```bash
curl -s https://YOURDOMAIN/api/v1/health | python3 -m json.tool
```

Expect `auth_enabled: true`, `sniffer_enabled: false`, `tor: true`.

```bash
cd /opt/decint-tools && python3 tools/verify.py
```

---

## Updating

```bash
sudo -u decint git -C /opt/decint-tools pull
sudo -u decint bash -c "cd /opt/decint-tools/frontend && npm ci && npm run build"
sudo systemctl restart decint-web          # backend-only change? restart decint-api instead
```

The rebuild is what visitors actually see. Pulling alone changes nothing, because
Next.js serves compiled output rather than source.

---

## Accounts

```bash
cd /opt/decint-tools/backend
sudo -u decint .venv/bin/python -m app.cli list
sudo -u decint .venv/bin/python -m app.cli create-user --email someone@example.com
sudo -u decint .venv/bin/python -m app.cli set-password --email you@example.com
sudo -u decint .venv/bin/python -m app.cli token-create --email client@example.com --label "Acme"
```

With `SIGNUP_DEFAULT_STATUS=pending`, new registrations cannot sign in until you
set them active.

---

## Notes

- **The sniffer stays off** on any shared server. It captures the server's own
  NIC, not a visitor's, and needs root. It belongs on your local box only.
- **Free leak sources** rate-limit and change. A failing source degrades to
  `ok:false` for that source rather than breaking the whole query.
- Rotate `SESSION_SECRET` to force everyone to re-login. Rotate `OPERATOR_TOKEN`
  to revoke the break-glass path.
- `backend/.env` is mode 600 and git-ignored. It should never leave the server.
