# Spider — correlation / pivoting

The companion to the leak search. The leak search gives flat, per-source rows;
the Spider turns **one seed identifier** into a graph — every source it turns up
in becomes a node, the emails / usernames / domains those reveal become new
nodes, and those are expanded in turn. You pick a common thread and follow it.

Lives in `backend/app/services/spider/`; the HTTP surface is
`backend/app/routers/spider.py` (`/api/v1/spider/*`); the console tab is
`frontend/components/console/apps/SpiderApp.tsx` (tty3, ⌃3, the `spider` shell
command).

## The one thing that makes it different: it remembers

Every other DECINT tool is stateless — the marketing copy and
`services/usage.py` promise that *what you search for is not stored*. The Spider
is the deliberate exception, because correlation is the point: a scan's seed and
the identifiers it found are saved **per account** (`spider_scans` /
`spider_nodes` in the app DB). That is what lets a later scan tell you an
identifier already showed up in a search you ran before — the **"seen before"**
flag, surfaced at the top of the results and on each node.

History is strictly owner-scoped: one account can never read, correlate against
or delete another's scans. A scan is kept until the account deletes it (from the
tool, or by deleting the account, which takes the rows with it), subject to
`SPIDER_HISTORY_MAX`. The privacy page carries this carve-out.

## How a scan works

```
seed ─▶ detect kind ─▶ bounded breadth-first expansion ─▶ correlate ─▶ save
         (email/username/domain/name)
```

The engine (`engine.py`) runs a breadth-first loop: expand a node with every
module that *consumes* its type, add what they find (deduped by canonical
`(type, value)`), enqueue the new nodes, repeat — until a cap is hit. It yields
progress events (`start` / `lookup` / `nodes` / `done`) in the same shape as the
dark-web search, so the router drives it as a background job the console polls.

Three caps bound a scan — this is what keeps one scan (which costs the customer
**one search**) from fanning out without end:

| cap | env | default | meaning |
|---|---|---|---|
| nodes | `SPIDER_MAX_NODES` | 40 | hard ceiling on graph size |
| lookups | `SPIDER_LOOKUP_BUDGET` | 25 | one node expanded by one module = one lookup |
| depth | `SPIDER_MAX_DEPTH` | 2 | hops from the seed |

`max_nodes` on the request can lower the node cap, never raise it above the
server's.

## Modules

Each module (`modules.py`) declares the node types it consumes and turns a
source's answer into findings. Like the leak providers, **a module never
raises** — a dead or rate-limited source returns nothing (and may leave a note
on the node). Set the active set with `SPIDER_MODULES`.

| key | consumes | source (free, keyless) | emits |
|---|---|---|---|
| `leaks` | email, username, domain, name | the leak aggregator (`services/leaks`) | breaches + co-located email/username/domain/password |
| `gravatar` | email | `gravatar.com/{md5}.json` | display name, preferred username, linked accounts |
| `username_sites` | username | a curated site list (`data/username_sites.json`) | an account per site the handle exists on |
| `domain` | domain | DNS over DoH (`dns.google`), crt.sh, RDAP | MX/NS hosts, subdomains; registrar annotated on the node |
| `darkweb_mentions` | email, username | the gateway dark-web search | co-occurring onions, emails, wallets |

What leaves this server, per pivot: a username to the sites it's checked on, an
email's MD5 to Gravatar, a domain to DNS/crt.sh/RDAP. **A person's name is never
sent to any third party** — it is expanded only by `leaks`, against uploaded
datasets.

The `username_sites` module is the noisiest (it fans out to many hosts), so it
is count- and concurrency-capped (`SPIDER_USERNAME_SITES_*`) and its list is a
deliberately small, high-signal subset. Point `SPIDER_USERNAME_SITES_PATH` at a
fuller WhatsMyName-style list to widen it.

## Metering & plan

Paid-plans only: the free trial gets the upgrade card, never a scan (the gate is
`usage.is_paid`, refused before anything is charged — mirrors the `reveal` gate
in the leak search). A whole scan, however many lookups it makes, costs one
search, refunded if the scan faults. Secrets ride the same mask-by-default
policy as the leak search.

## API

| method | path | |
|---|---|---|
| POST | `/api/v1/spider/scan` | start a scan → `{job_id}` |
| GET | `/api/v1/spider/jobs/{id}` | poll the running scan (owner-scoped) |
| GET | `/api/v1/spider/history` | this account's saved scans |
| GET | `/api/v1/spider/history/{id}` | one saved scan with its full graph |
| DELETE | `/api/v1/spider/history/{id}` | delete one saved scan |

Tests: `backend/tests/test_spider.py` (run on its own, like the other
DB-touching suites).
