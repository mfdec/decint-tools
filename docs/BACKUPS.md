# Backups and restore points

A full backup here has two halves, because GitHub never holds secrets or personal data
(`CLAUDE.md`, `.gitignore`):

| Half | Lives in | Holds |
|---|---|---|
| Code | this repo, as a tag | every file that isn't a secret, personal data or rebuildable |
| Private | `/var/backups/decint-tools/` on the server (root, mode 600) | `backend/.env*`, the analytics DB (visitor IPs), live nginx / systemd config |

The private half sits on the same machine as the site. It protects against a bad deploy or a
deleted file, not against losing the server — copy it off-box for that.

Not covered by either half, because it lives elsewhere or can be re-created: TLS certificates
(certbot), Cloudflare DNS / SSL / Access settings, the Stripe and mail-provider dashboards,
`node_modules`, `.venv` and `.next`. Tor runs with the stock `torrc`.

## 2026-09-30 — full backup of the live site

**Code** — tag `backup-2026-09-30` (commit `04b6555`): `main` at `c2e1c51` plus three changes that
existed only in the live working tree. Checked byte-for-byte (184 files) against a fresh clone of the tag.

- `backend/app/routers/executor.py` (new) and its registration in `backend/app/main.py`: the
  admin-only local Python runner (runs uploaded code as the `decint` user, no sandbox). The API
  process started 2026-09-24 serves `/api/v1/executor/*` (401 without an admin session).
  **Kept off `main` on purpose.**
- `frontend/app/layout.tsx`: an unbuilt AdSense edit, replaced by the fix that is now on `main`.

**Private** — `live-env-db-config-2026-09-30.tar.gz`
(sha256 `8730e0dafb2e04970cd8f477e020316921e371019865c0a561d0a24e8794c884`): `.env` and `.env.bak-*`,
a consistent copy of `analytics.db` (SQLite backup API; `integrity_check` ok), the live nginx vhosts and
`conf.d`, the systemd units, `decint-beta-deploy`, runtime versions, a checksum manifest and its own
`README-RESTORE.txt`.

## 2026-09-17 — pre-Qwen restore point

- Code: private repo `mfdec/decint-tools-backup-2026-09-17` (`main` = `c2e1c51`; it is the `backup`
  remote). Restore point only — never develop there.
- Private: `live-env-and-db-2026-09-17.tar.gz` (`.env*` and the analytics DB).

## Restoring (as root)

1. **Code.** `git clone --branch backup-2026-09-30 https://github.com/mfdec/decint-tools /var/www/html/decint-tools`
   restores the live tree exactly as it was. Plain `main` is the last reviewed state, without the executor.
2. **Private half.** `mkdir /root/decint-restore && tar -xpzf /var/backups/decint-tools/live-env-db-config-2026-09-30.tar.gz --numeric-owner -C /root/decint-restore`.
   Copy `backend/.env*` and `backend/data/` into `backend/` (owner `decint`). Copy `server-config/` into
   `/etc/nginx` and `/etc/systemd/system`, then `nginx -t && systemctl daemon-reload`.
3. **Rebuild.** System packages, the venv (`backend/requirements.txt`) and `npm ci && npm run build`
   in `frontend/` (as `decint`) are in `deploy/README.md`.
4. `chown -R decint:decint /var/www/html/decint-tools`, then `systemctl restart decint-api decint-web`.
