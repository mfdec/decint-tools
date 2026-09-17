# Putting the new DECINT design on your server

Three phases. Phase A is on your Windows PC, phase B is on the server, phase C
is checking it worked. Nothing here touches your backend, your database, or
your accounts — this is a frontend-only change.

Replace `box` with your SSH alias and `YOURDOMAIN` with your real domain.

---

## A. On your Windows PC

**A1.** Unzip `decint-design-v2.zip` somewhere temporary, e.g. Downloads.

**A2.** Copy the contents over your local repo folder, keeping the folder
structure. The zip mirrors your repo layout exactly, so files land in the right
places and overwrite the old versions.

```powershell
# adjust the source and destination to match your machine
Copy-Item -Path "$env:USERPROFILE\Downloads\decint-design-v2\*" `
          -Destination "$env:USERPROFILE\decint-tools" -Recurse -Force
```

**A3.** Check what changed — you should see 15 modified files and 1 new one
(`Wordmark.tsx`):

```powershell
cd $env:USERPROFILE\decint-tools
git status
```

**A4.** Commit and push:

```powershell
git add -A
git commit -m "New identity: empty violet shield, single-hue palette"
git push origin main
```

Nothing is on your server yet. The push only updates GitHub.

---

## B. On the server

**B1.** Pull the new code:

```powershell
ssh -t box 'sudo -u decint git -C /opt/decint-tools pull'
```

**B2.** Rebuild the frontend. This is the step that actually turns your source
into the pages visitors see — skipping it means nothing changes:

```powershell
ssh -t box 'cd /opt/decint-tools/frontend && sudo -u decint bash -lc ". ~/.nvm/nvm.sh; npm ci && npm run build"'
```

Takes 1–3 minutes. Wait for `✓ Generating static pages`.

**B3.** Restart the web service:

```powershell
ssh -t box 'sudo systemctl restart decint-web'
```

Your API, Tor and sessions are untouched — no need to restart `decint-api`.

---

## C. Check it worked

**C1.** Confirm the service came back up:

```powershell
ssh box 'systemctl is-active decint-web'
```

Expect `active`. If it says `failed`, see Troubleshooting below.

**C2.** Confirm the new colours are actually being served:

```powershell
ssh box 'curl -s https://YOURDOMAIN | grep -o "/_next/static/css/[^\"]*\.css" | head -1'
```

Take that path and fetch it — it must contain `#8d5bf6` and must NOT contain
`#4989e5`:

```powershell
ssh box 'curl -s https://YOURDOMAIN/_next/static/css/THE-FILE.css | grep -c "8d5bf6"'
```

Or just run your own checker, which now tests exactly this:

```powershell
ssh box 'cd /opt/decint-tools && python3 tools/verify.py'
```

**C3.** Open `https://YOURDOMAIN` in a **private window** — a normal window will
show you the cached old CSS and you will think it failed. Check:

- the nav shows the empty violet shield and the wide-tracked DECINT
- the browser tab icon is a violet shield on near-black
- nothing on the page is blue or cyan any more

---

## Troubleshooting

| What you see | What it means | Fix |
|---|---|---|
| Still the old design | Browser cache | Hard-refresh: Ctrl-Shift-R, or a private window |
| Still the old design in a private window too | Build didn't run or didn't finish | Re-run B2 and read the output to the end |
| `decint-web` shows `failed` | Build produced no output | `ssh box 'journalctl -u decint-web -n 50'` |
| `npm ci` fails on the server | Out of memory on a small VPS | Add swap: `ssh -t box 'sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile'` then retry B2 |
| Favicon unchanged | Favicons cache hardest of all | Private window, or visit `https://YOURDOMAIN/favicon-32x32.png` directly |
| `git pull` says local changes | Something was edited on the server | `ssh -t box 'sudo -u decint git -C /opt/decint-tools checkout -- .'` then retry B1 |

---

## If you are NOT using git

Copy the files straight up instead of A4 + B1, then continue from B2:

```powershell
cd $env:USERPROFILE\Downloads\decint-design-v2
scp -r frontend/app frontend/components frontend/public box:/tmp/decint-new/
ssh -t box 'sudo cp -r /tmp/decint-new/* /opt/decint-tools/frontend/ && sudo chown -R decint:decint /opt/decint-tools'
```

Git is worth wiring up though: it is the difference between "restore the old
design" being one command and being a manual reconstruction.

---

## Rolling back

If you dislike it, the old design is one commit behind:

```powershell
ssh -t box 'sudo -u decint git -C /opt/decint-tools revert --no-edit HEAD'
```

Then repeat B2 and B3.
