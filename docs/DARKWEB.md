# Dark-web search

A meta-search over onion search engines. One query goes to many engines in
parallel; the merged hits are cleaned, de-duplicated, scored and pruned into one
ranked list, and everything that was dropped keeps its reason.

It is built on **onionscope** (`github.com/mfdec/data-acquisition`), ported into
`backend/app/services/darkweb/` and extended for DECINT (query syntax, entity
extraction, evidence hash, metering, job ownership).

Only search-engine result pages are fetched. The sites the results point at are
**never visited**.

## Modes

| | `gateway` ("fast mode") | `tor` ("full Tor mode") |
|---|---|---|
| Engines | the 5 that publish a clearnet gateway: Ahmia, OnionLand, VormWeb, Onion Search Engine, Onion Engine | all 45 default-tier engines, plus 23 experimental on request |
| Transport | plain HTTPS from this server (no Tor) | Tor SOCKS5h, **one isolated circuit per engine**, DNS through Tor |
| Typical time | 3–10 s | 45–75 s (dead onion services take ~45 s to give up) |
| Extras | — | entity extraction, evidence SHA-256 |
| Plan | Free, Starter, Pro | Pro (see "Known gaps") |

Each mode has its own aggregator, so an engine's onion mirrors and its clearnet
gateway have separate health records and cache directories.

## How a search works

```
query ─▶ parse operators ─▶ safety gate ─▶ fan-out (parallel, global deadline)
      ─▶ per-engine parser (generic fallback) ─▶ unwrap redirect links ─▶ normalise
      ─▶ safety filter ─▶ hard filters ─▶ merge same URL across engines
      ─▶ near-duplicate + mirror/clone clustering (SimHash)
      ─▶ operator filter ─▶ quality score ─▶ rank ─▶ prune ─▶ host crowding ─▶ results
```

* **Hard filters** (each hit is listed under *dropped*, with the reason):
  `sponsored`, `self_link`, `non_onion`, `dead_v2`, `invalid_address` (v3
  checksum mismatch: catches typo-squatted phishing look-alikes), `empty`.
* **Clones.** Near-identical pages on different onion hosts form a mirror
  cluster; the best-supported copy is shown and the rest listed under *mirrors /
  possible clones*. Onion phishing sites copy their target exactly, and this is
  how they show up.
* **Ranking** blends BM25 relevance, weighted reciprocal-rank fusion across
  engines, cross-engine consensus (two front-ends of one index count once) and
  a quality score, and quality also gates the whole score so spam cannot ride on
  keyword matches. Hover a score in the console for the breakdown.
* **Pruning.** `low_relevance`, `spam` (scam lexicon, emoji spam, stuffing),
  and `near_duplicate`. At most `DARKWEB_MAX_PER_HOST` results per site; the
  rest fold under the best one.

## Query syntax

```
leaked database               plain words
"leaked database"             exact phrase, required
-conti                        a word the result must not contain
-"hire a hacker"              a phrase the result must not contain
+acme                         same as a plain word
```

Engines only ever receive the plain words: onion engines mostly ignore
operators, so they are enforced afterwards on the merged results, judged on
everything any engine said about the page. A result dropped by an operator is
listed as `excluded` or `phrase_missing`, with the term quoted back. A query of
only exclusions is refused (422) before it is charged.

## Safety

* **Child-safety filtering is mandatory and not configurable.** Queries seeking
  CSAM are refused (HTTP 422, not charged, audited as `darkweb.blocked` by
  *count only*, never the text). Matching results, and hosts on Ahmia's abuse
  banlist, are withheld: they are only counted (`stats.safety_blocked`), never
  listed. The check runs on the query as typed *and* with operators stripped,
  so quoting or excluding cannot disguise a term. Do not add a way to switch it
  off.
* **Customers never see internals.** Failures read as plain sentences; the SOCKS
  address and exception types are shown to `admin`/`operator` roles only.
* **Result addresses are text, never links** in the console (untrusted, and they
  only resolve in Tor Browser). Engine-supplied strings are rendered as text,
  never HTML.
* **Exports are spreadsheet-safe.** Titles and snippets are attacker-controlled;
  CSV cells starting with `= + - @` get a leading apostrophe so they cannot run
  as formulas (console export and CLI both).
* **Redirects are confined.** From an onion engine, only to another onion
  address. From a clearnet gateway (gateway mode leaves *this server*), only
  within the same site, so a hijacked gateway cannot bounce the server onto an
  internal address.
* **Jobs are private.** `GET /darkweb/jobs/{id}` returns 404 to anyone but the
  account that started it (and admins). Queries live only in the in-memory job
  store, which keeps the last 200 jobs; nothing about what was searched is
  written to disk or logged.

## API

All under `/api/v1/darkweb`, session required.

| | |
|---|---|
| `POST /search` | `{query, mode: "gateway"\|"tor", limit ≤ 50, pages ≤ 10 (capped by DARKWEB_MAX_PAGES), experimental}` → `{job_id}`. `mode: "ahmia"` is accepted as an alias of `gateway`. |
| `GET /jobs/{id}` | poll (the console polls every 0.8 s) |
| `GET /engines?mode=` | the engines a search in that mode queries, with live health |

A job moves `queued` → `running` → `done` \| `error`. While running, `engines`
gains one entry per engine as it answers, so the console can show the fan-out.
On completion: `results`, `pruned` (≤ 40 per reason; the full counts are in
`stats.pruned_by_reason`), `stats`, `operators`, `manifest`.

**Billing.** Validated first, then charged at submission (`usage.take`); the
search is **refunded** when our side fails: no engine answered, the job crashed,
or it overran `DARKWEB_DEADLINE + 45 s`. An engine that answered with zero hits
counts as an answer and is not refunded.

**Concurrency.** At most `DARKWEB_MAX_CONCURRENT_SEARCHES` run at once; the rest
show as `queued`. The hang timeout starts when a slot is acquired, not at
submission.

**Evidence (Tor mode).** `manifest.sha256` is the SHA-256 of
`json.dumps([{"url","title","score"}, …], sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` over the results in order. Recompute it from an export to
show a report was not altered.

## Operating it

* **Tor** must be running on `TOR_HOST:TOR_PORT` for `tor` mode
  (`systemctl is-active tor`). The status dot in the console is a cached
  socket probe; the search itself reports per-engine failures.
* **Engines churn.** Onion addresses rotate and engines die. In a live run
  (2026-10-02) 23 of 45 default engines answered; the rest were unreachable. That is
  normal. After `DARKWEB_BREAKER_FAILURES` failed searches in a row an engine
  is benched for `DARKWEB_BREAKER_COOLDOWN` s, so the first searches after a
  restart are the slow ones. Health persists in `DARKWEB_DATA_DIR`
  (`data/darkweb/{tor,direct}/health.json`), which **must be writable by the
  service user** (`decint`). Create it as that user, not as root.
* **See which engines answer today:**
  ```bash
  cd backend
  .venv/bin/python -m app.services.darkweb engines --mode tor --probe
  ```
* **Add or fix an engine** without code: copy
  `app/services/darkweb/engines/catalog.toml`, edit, and point
  `DARKWEB_CATALOG_PATH` at it. Addresses are checksum-validated at load; bad
  ones are dropped with a warning. The `generic` parser handles most HTML
  engines; add a dedicated parser in `engines/parsers.py` (with a fixture) only
  when it mislabels results.
* **CLI** (same code path as the API):
  ```bash
  cd backend
  .venv/bin/python -m app.services.darkweb search '"leaked database" -conti' --mode tor --show-pruned
  .venv/bin/python -m app.services.darkweb search bitcoin --json - | jq .manifest
  .venv/bin/python -m app.services.darkweb tor-check
  ```
  or `python tools/decint.py osint darkweb search …`.
* **Demo without a network:** `DARKWEB_REPLAY_DIR=tests/darkweb/fixtures/replay`
  (never in production) or `--replay DIR` on the CLI.
* **Upgrading from the previous engine:** needs `httpx[socks]` and `lxml`
  (`pip install -r requirements.txt`) and a restart. The API stays compatible
  for a rolling deploy except that `score` is now 0–1 (was 0–100) and `sources`
  is now `engines`, so deploy the backend and the frontend together.

## Tests

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt        # once
.venv/bin/python -m pytest -q tests/darkweb          # offline, ~4 s
```

Engine pages are replayed from `tests/darkweb/fixtures/replay/` (real captures
of four clearnet gateways plus synthetic pages for the onion-only engines; see
`fixtures/README.md`). Nothing touches the network. Live behaviour that replay
cannot show (dead onion services answering with Tor's own SOCKS codes, for one)
has been found only by running against Tor, so after changing the transport do
a real `search --mode tor` too.

## Known gaps

* **Tor mode is described as a Pro feature** (plans page, `/tools`), but the
  server does not enforce it: any signed-in account can call `mode: "tor"`. This
  was already true before the overhaul; Tor mode is now much heavier (≈45
  circuits), so gate it in `routers/darkweb.py` if the tier matters.
* The in-memory job store is per process. Run one backend worker, or move jobs to
  Redis before scaling out.
* The safety filter is deliberately conservative (regex over minors + sexual
  terms, plus Ahmia's banlist), so it also withholds some legitimate hits
  (a plain `bitcoin` search withheld hundreds). It is counted, never listed.
