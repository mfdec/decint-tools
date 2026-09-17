#!/usr/bin/env bash
# =============================================================================
#  decint-server-install.sh  —  first-time DECINT install on a fresh Ubuntu VPS
#
#  Installs and wires together: Tor, Node (system-wide), Python venv, Caddy,
#  two systemd services, a firewall, and your first admin account.
#
#  IDEMPOTENT. Safe to re-run after a failure; it skips what is already done
#  and never overwrites an existing backend/.env.
#
#  Usage, on the server:
#      sudo bash decint-server-install.sh
#
#  Or straight from your Windows PC without copying it up first:
#      ssh -t box 'sudo bash -s' < .\decint-server-install.sh
# =============================================================================
set -Eeuo pipefail

APP_USER="${APP_USER:-decint}"
APP_DIR="${APP_DIR:-/opt/decint-tools}"
NODE_MAJOR="${NODE_MAJOR:-24}"

C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_HEAD=$'\e[36m'; C_DIM=$'\e[2m'; C_OFF=$'\e[0m'
STEP=0
step() { STEP=$((STEP+1)); echo; echo "${C_HEAD}━━━ ${STEP}. $* ━━━${C_OFF}"; }
ok()   { echo "  ${C_OK}✓${C_OFF} $*"; }
warn() { echo "  ${C_WARN}!${C_OFF} $*"; }
die()  { echo "  ${C_ERR}✗ $*${C_OFF}" >&2; exit 1; }
note() { echo "    ${C_DIM}$*${C_OFF}"; }
ask()  { local p="$1" d="${2:-}" v; if [[ -n "$d" ]]; then read -rp "  $p [$d]: " v </dev/tty; echo "${v:-$d}";
         else read -rp "  $p: " v </dev/tty; echo "$v"; fi; }

trap 'die "failed at line $LINENO — nothing after this point ran. Fix, then re-run; the script resumes."' ERR
[[ $EUID -eq 0 ]] || die "run this with sudo"

echo
echo "${C_HEAD}╔══════════════════════════════════════════════════════════╗${C_OFF}"
echo "${C_HEAD}║   DECINT — first-time server install                     ║${C_OFF}"
echo "${C_HEAD}╚══════════════════════════════════════════════════════════╝${C_OFF}"

# ─────────────────────────────────────────────────────────────────────────────
step "Check the machine"
. /etc/os-release
echo "  $PRETTY_NAME · $(uname -m) · $(nproc) vCPU · $(free -m | awk '/Mem:/{print $2}') MB RAM"
[[ "${ID:-}" == "ubuntu" ]] || warn "not Ubuntu ($ID) — package names may differ"
PUBIP="$(curl -fsS --max-time 6 https://api.ipify.org || echo '')"
[[ -n "$PUBIP" ]] && ok "public IP: $PUBIP" || warn "could not determine public IP"

RAM_MB=$(free -m | awk '/Mem:/{print $2}')
SWAP_MB=$(free -m | awk '/Swap:/{print $2}')
if [[ "$RAM_MB" -lt 2048 && "$SWAP_MB" -lt 1024 ]]; then
  warn "only ${RAM_MB}MB RAM and ${SWAP_MB}MB swap — the Next.js build will run out of memory."
  if [[ ! -f /swapfile ]]; then
    echo "  Creating a 2GB swapfile so the build can finish..."
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "2GB swap active (survives reboot)"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
step "Collect your settings"
DOMAIN="${DOMAIN:-$(ask 'Your domain, no https:// (e.g. decint.tools)')}"
[[ -n "$DOMAIN" ]] || die "a domain is required"
ADMIN_EMAIL="${ADMIN_EMAIL:-$(ask 'Your admin email (you will log in with this)')}"
[[ "$ADMIN_EMAIL" == *@*.* ]] || die "that does not look like an email address"
REPO="${REPO:-$(ask 'Git repo URL (blank if you already copied the code up)' '')}"

ok "site      https://$DOMAIN"
ok "admin     $ADMIN_EMAIL"
ok "install   $APP_DIR  (as user '$APP_USER')"

# ─────────────────────────────────────────────────────────────────────────────
step "System packages and Tor"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git ufw fail2ban debian-keyring \
                       debian-archive-keyring apt-transport-https \
                       python3-venv python3-pip build-essential libpcap0.8 tor >/dev/null
systemctl enable --now tor >/dev/null 2>&1 || true
sleep 2
TOR_STATE="$(systemctl is-active tor || true)"
[[ "$TOR_STATE" == "active" ]] && ok "tor is running (dark-web search needs this)" \
                               || warn "tor is '$TOR_STATE' — dark-web Tor mode will fail until it starts"
ok "base packages installed"

# ─────────────────────────────────────────────────────────────────────────────
step "Node ${NODE_MAJOR} LTS — system-wide, NOT nvm"
note "The decint-web service runs as a background process with no login shell,"
note "so it cannot see an nvm install. System Node is what makes systemd work."
if command -v node >/dev/null && [[ "$(node -v)" == v${NODE_MAJOR}.* ]]; then
  ok "already installed: $(node -v)"
else
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg --yes
  chmod a+r /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
    > /etc/apt/sources.list.d/nodesource.list
  apt-get update -qq
  apt-get install -y -qq nodejs >/dev/null
  ok "installed node $(node -v) / npm $(npm -v)"
fi
NPM_BIN="$(command -v npm)"

# ─────────────────────────────────────────────────────────────────────────────
step "Caddy (handles HTTPS certificates automatically)"
if command -v caddy >/dev/null; then
  ok "already installed: $(caddy version | head -1)"
else
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg --yes
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null
  ok "installed $(caddy version | head -1)"
fi

# ─────────────────────────────────────────────────────────────────────────────
step "Service account '$APP_USER'"
if id -u "$APP_USER" >/dev/null 2>&1; then
  ok "user exists"
else
  useradd -r -m -d "$APP_DIR" -s /usr/sbin/nologin "$APP_USER"
  ok "created system user (no login shell, no password)"
fi
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$APP_DIR"

# ─────────────────────────────────────────────────────────────────────────────
step "Get the code"
if [[ -d "$APP_DIR/.git" ]]; then
  ok "git checkout already present — pulling latest"
  sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only || warn "pull failed; continuing with what is on disk"
elif [[ -f "$APP_DIR/backend/requirements.txt" ]]; then
  ok "code already present (copied up manually) — not a git checkout"
elif [[ -n "$REPO" ]]; then
  install -d -o "$APP_USER" -g "$APP_USER" -m 0700 "$APP_DIR/.ssh"
  if [[ "$REPO" == git@* ]]; then
    if [[ ! -f "$APP_DIR/.ssh/id_ed25519" ]]; then
      sudo -u "$APP_USER" ssh-keygen -t ed25519 -N '' -q -C "deploy@$(hostname)" \
           -f "$APP_DIR/.ssh/id_ed25519"
    fi
    ssh-keyscan -t rsa,ecdsa,ed25519 github.com gitlab.com 2>/dev/null \
      >> "$APP_DIR/.ssh/known_hosts" || true
    sort -u -o "$APP_DIR/.ssh/known_hosts" "$APP_DIR/.ssh/known_hosts"
    chown -R "$APP_USER:$APP_USER" "$APP_DIR/.ssh"
    echo
    echo "${C_WARN}  ┌─ ACTION NEEDED ─────────────────────────────────────────┐${C_OFF}"
    echo "  │ Add this as a READ-ONLY deploy key on your repo:         │"
    echo "  │ GitHub ▸ repo ▸ Settings ▸ Deploy keys ▸ Add deploy key  │"
    echo "${C_WARN}  └─────────────────────────────────────────────────────────┘${C_OFF}"
    echo
    echo "${C_OK}$(cat "$APP_DIR/.ssh/id_ed25519.pub")${C_OFF}"
    echo
    read -rp "  Press Enter once you have added it... " _ </dev/tty
  fi
  sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR" \
    || die "clone failed — check the repo URL and that the deploy key was added"
  ok "cloned"
else
  die "no code at $APP_DIR and no repo URL given. Either pass REPO=... or copy the project up first."
fi
[[ -f "$APP_DIR/backend/requirements.txt" ]] || die "$APP_DIR does not look like the DECINT project"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
ok "code in place at $APP_DIR"

# ─────────────────────────────────────────────────────────────────────────────
step "Python environment and dependencies"
if [[ ! -x "$APP_DIR/backend/.venv/bin/python" ]]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/backend/.venv"
  ok "created virtualenv"
fi
sudo -u "$APP_USER" "$APP_DIR/backend/.venv/bin/python" -m pip install --upgrade pip -q
sudo -u "$APP_USER" "$APP_DIR/backend/.venv/bin/python" -m pip install -q -r "$APP_DIR/backend/requirements.txt"
ok "python deps installed ($("$APP_DIR/backend/.venv/bin/python" --version))"
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$APP_DIR/backend/data" "$APP_DIR/backend/data/geoip"
ok "data directory ready"

# ─────────────────────────────────────────────────────────────────────────────
step "Backend configuration (.env)"
ENVF="$APP_DIR/backend/.env"
if [[ -f "$ENVF" ]]; then
  warn ".env already exists — leaving it completely alone"
  note "delete it and re-run if you want a fresh one generated"
else
  SESSION_SECRET="$(openssl rand -hex 32)"
  OPERATOR_TOKEN="$(openssl rand -hex 24)"
  cp "$APP_DIR/backend/.env.example" "$ENVF"

  set_env() {  # set_env KEY VALUE — replaces the line, or appends if absent
    local k="$1" v="$2"
    if grep -qE "^${k}=" "$ENVF"; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$ENVF"
    else
      echo "${k}=${v}" >> "$ENVF"
    fi
  }

  # These five are the difference between a safe server and an open one.
  set_env SESSION_SECRET   "$SESSION_SECRET"   # signs session cookies
  set_env OPERATOR_TOKEN   "$OPERATOR_TOKEN"   # EMPTY WOULD DISABLE AUTH ENTIRELY
  set_env COOKIE_SECURE    true                # cookie only travels over HTTPS
  set_env SNIFFER_ENABLED  false               # never capture on a shared box
  set_env SIGNUP_DEFAULT_STATUS pending        # you approve every new account

  set_env CORS_ORIGINS     "https://${DOMAIN}"
  set_env PUBLIC_BASE_URL  "https://${DOMAIN}"
  set_env SIGNUP_ENABLED   true

  chown "$APP_USER:$APP_USER" "$ENVF"; chmod 600 "$ENVF"
  ok "generated $ENVF (mode 600, secrets are random)"
  note "SNIFFER_ENABLED forced to false — .env.example ships it true for a local box"
  note "OPERATOR_TOKEN set: an empty value would turn authentication OFF entirely"
fi

# ─────────────────────────────────────────────────────────────────────────────
step "Build the frontend"
note "This compiles the site. It is the slowest step — 1 to 3 minutes."
sudo -u "$APP_USER" bash -c "cd '$APP_DIR/frontend' && npm ci --no-audit --fund=false" \
  || sudo -u "$APP_USER" bash -c "cd '$APP_DIR/frontend' && npm install --no-audit --fund=false"
sudo -u "$APP_USER" bash -c "cd '$APP_DIR/frontend' && npm run build"
[[ -d "$APP_DIR/frontend/.next" ]] || die "build produced no .next directory"
ok "frontend built"

# ─────────────────────────────────────────────────────────────────────────────
step "systemd services"
# The shipped decint-web.service uses `/usr/bin/env npm`, which only resolves if
# npm is on the system PATH — it is not, under nvm. We write the absolute path.
sed -e "s|^User=.*|User=${APP_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${APP_DIR}/backend|" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=${APP_DIR}/backend/.env|" \
    -e "s|^ExecStart=.*|ExecStart=${APP_DIR}/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000|" \
    -e "s|^ReadWritePaths=.*|ReadWritePaths=${APP_DIR}/backend|" \
    "$APP_DIR/deploy/systemd/decint-api.service" > /etc/systemd/system/decint-api.service

sed -e "s|^User=.*|User=${APP_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${APP_DIR}/frontend|" \
    -e "s|^ExecStart=.*|ExecStart=${NPM_BIN} run start|" \
    "$APP_DIR/deploy/systemd/decint-web.service" > /etc/systemd/system/decint-web.service

systemctl daemon-reload
systemctl enable decint-api decint-web >/dev/null
systemctl restart decint-api
sleep 4
systemctl is-active --quiet decint-api || { journalctl -u decint-api -n 30 --no-pager; die "API failed to start"; }
ok "decint-api running on 127.0.0.1:8000"
systemctl restart decint-web
sleep 6
systemctl is-active --quiet decint-web || { journalctl -u decint-web -n 30 --no-pager; die "web failed to start"; }
ok "decint-web running on 127.0.0.1:3000"

# ─────────────────────────────────────────────────────────────────────────────
step "Caddy reverse proxy + HTTPS"
sed "s/decint\.tools/${DOMAIN}/g" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy fmt --overwrite /etc/caddy/Caddyfile >/dev/null 2>&1 || true
caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
  && ok "Caddyfile is valid" || warn "caddy validate reported a problem — check /etc/caddy/Caddyfile"
systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy
ok "caddy reloaded — it will fetch a certificate for $DOMAIN on first request"

# ─────────────────────────────────────────────────────────────────────────────
step "Firewall"
ufw allow 22/tcp   >/dev/null
ufw allow 80/tcp   >/dev/null
ufw allow 443/tcp  >/dev/null
ufw status | grep -q '^Status: active' || ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true
ok "only 22 / 80 / 443 are open; ports 3000 and 8000 stay on loopback"

# ─────────────────────────────────────────────────────────────────────────────
step "Local health check"
sleep 2
if curl -fsS --max-time 8 http://127.0.0.1:8000/api/v1/health >/tmp/dc_health.json 2>/dev/null; then
  ok "API answered:"
  python3 -m json.tool /tmp/dc_health.json 2>/dev/null | sed 's/^/      /' || cat /tmp/dc_health.json
else
  warn "API health check did not answer — see: journalctl -u decint-api -n 40"
fi
curl -fsS --max-time 8 -o /dev/null http://127.0.0.1:3000/ && ok "frontend answered on :3000" \
  || warn "frontend did not answer — see: journalctl -u decint-web -n 40"

# ─────────────────────────────────────────────────────────────────────────────
step "Create your admin account"
if sudo -u "$APP_USER" bash -c "cd '$APP_DIR/backend' && .venv/bin/python -m app.cli list" 2>/dev/null | grep -q "$ADMIN_EMAIL"; then
  ok "account $ADMIN_EMAIL already exists — skipping"
else
  echo "  Choose a password for $ADMIN_EMAIL."
  note "It is typed into a prompt, never stored in a file or your shell history."
  echo
  sudo -u "$APP_USER" bash -c "cd '$APP_DIR/backend' && .venv/bin/python -m app.cli create-admin --email '$ADMIN_EMAIL'" \
    || warn "account creation did not complete — run it again manually (command shown below)"
fi

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "${C_OK}╔══════════════════════════════════════════════════════════╗${C_OFF}"
echo "${C_OK}║   INSTALL COMPLETE                                       ║${C_OFF}"
echo "${C_OK}╚══════════════════════════════════════════════════════════╝${C_OFF}"
cat <<SUMMARY

  Visit          https://${DOMAIN}
  Sign in as     ${ADMIN_EMAIL}

  If the page does not load, DNS is the usual reason. Check that
  ${DOMAIN} resolves to ${PUBIP}, and if you use Cloudflare that the
  orange proxy cloud is OFF until the certificate is issued.

  Everyday commands
    systemctl status decint-api decint-web caddy
    journalctl -u decint-api -f
    journalctl -u decint-web -f
    systemctl restart decint-web

  Accounts (new signups land as 'pending' until you approve them)
    cd ${APP_DIR}/backend
    sudo -u ${APP_USER} .venv/bin/python -m app.cli list
    sudo -u ${APP_USER} .venv/bin/python -m app.cli create-user --email someone@example.com

  Update after a code change
    sudo -u ${APP_USER} git -C ${APP_DIR} pull
    sudo -u ${APP_USER} bash -c "cd ${APP_DIR}/frontend && npm ci && npm run build"
    sudo systemctl restart decint-web

  Config lives in ${APP_DIR}/backend/.env  (mode 600 — contains your secrets)

SUMMARY
