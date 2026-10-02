# Test fixtures

`fixtures/replay/` is the saved state of one search for **bitcoin** across 11 engines. It is used
by the end-to-end tests, by the parser tests, and by `DARKWEB_REPLAY_DIR`
for offline demos.

File naming follows `ReplayFetcher`: `<engine>.html` (or `.json`) is page 1, `<engine>.prep.html`
is the page a pre-request hook reads (e.g. Ahmia's search form with its token), and
`<engine>.banlist.txt` is Ahmia's banned-host MD5 list.

## Real captures (clearnet gateways, 2026-10-02)

Trimmed to ~20 results with scripts/styles removed; the markup is otherwise untouched.

| File | Source |
|---|---|
| `vormweb.html` | https://vormweb.de/en/search?q=bitcoin |
| `onionland.html` | https://onionlandsearchengine.net/search?q=bitcoin&page=1 (includes the 4 injected ads) |
| `onionsearchengine.html` | https://onionsearchengine.com/search.php?q=bitcoin&page=1 |
| `onionengine.html` | https://onionengine.com/?q=bitcoin |

## Synthetic pages (onion-only engines)

`ahmia*`, `tor66`, `tordex`, `torch`, `haystak`, `deepsearch` and `danwin` could not be fetched
without Tor, so they are hand-built from each engine's documented markup (ahmia-site's
`tor_results.html`, and the selectors used by OnionSearch, darkdump, SpiderFoot and Robin). They
deliberately overlap with the real captures and contain known traps:

- the same page reported by several engines with different URL spellings (tracking params,
  `index.html`, `www.`) - must merge into one result;
- a byte-for-byte clone of Trocador on another onion host - must collapse as a mirror;
- a Trocador look-alike with a broken checksum, a dead v2 address, clearnet results;
- a near-duplicate `/en/` page of Bitcoin Core - must be absorbed;
- scam spam (bitcoin doubler, hacker-for-hire) - must be pruned;
- an off-topic page (sourdough recipes) - must be pruned as low relevance;
- a host on Ahmia's banlist - must be withheld and only counted.

Onion addresses for synthetic sites come from `conftest.fake_onion()` (checksum-valid but random).
