# DECINT v3 — source snapshot

Complete website source. Build artifacts, the analytics database, the
GeoLite2 files and `.env` are intentionally absent.

## Run it

```bash
python tools/decint.py install
cp backend/.env.example backend/.env   # then edit
python tools/decint.py serve dev
```

Open http://localhost:3000. See `tools/README.md` for the full tool list
and `deploy/README.md` for the server runbook.
