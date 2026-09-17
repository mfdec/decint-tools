#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║         DECINT // DARK WEB KEYWORD SEARCH v1.1.0        ║
║         OSINT across .onion indexes & repositories       ║
╚══════════════════════════════════════════════════════════╝

Requirements:
    pip install "requests[socks]" beautifulsoup4

Tor must be running:
    sudo apt install tor && sudo systemctl start tor
    Default SOCKS5 proxy: 127.0.0.1:9050

Usage:
    python3 search.py "keyword"
    python3 search.py "keyword" --sources ahmia torch haystak darksearch
    python3 search.py "keyword" --json --out results.json
    python3 search.py "keyword" --debug          # dumps raw HTML to /tmp/
    python3 search.py "keyword" --probe          # connectivity check per source, no search
    python3 search.py "keyword" --skip-tor-check
"""

import requests
import argparse
import json
import sys
import os
import time
import textwrap
from urllib.parse import quote
from datetime import datetime, timezone

# ─── Tor proxy ────────────────────────────────────────────────────────────────
TOR_PROXY = {
    "http":  "socks5h://127.0.0.1:9050",
    "https": "socks5h://127.0.0.1:9050",
}

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "DNT":             "1",
}

TIMEOUT = 45   # .onion circuits are slow — give them room
DEBUG   = False # set by --debug flag


# ─── Helpers ──────────────────────────────────────────────────────────────────

def fetch(url: str, label: str = "") -> requests.Response | None:
    """
    GET via Tor with status logging.
    Saves raw HTML to /tmp/decint_<label>.html when DEBUG=True.
    """
    try:
        r = requests.get(url, proxies=TOR_PROXY, headers=HEADERS, timeout=TIMEOUT)
        tag = label or url[:30]
        print(f"    [HTTP {r.status_code}] {len(r.text):,} bytes  ← {tag}")
        if DEBUG and label:
            path = f"/tmp/decint_{label}.html"
            with open(path, "w", encoding="utf-8", errors="replace") as f:
                f.write(r.text)
            print(f"    [DEBUG] HTML dumped → {path}")
        return r
    except Exception as e:
        print(f"    [!] fetch error ({label or url[:40]}): {e}")
        return None


def probe_url(url: str, label: str) -> bool:
    """Connectivity-only check — print status, return True if reachable."""
    try:
        r = requests.get(url, proxies=TOR_PROXY, headers=HEADERS, timeout=TIMEOUT)
        ok = r.status_code < 500
        sym = "+" if ok else "!"
        print(f"  [{sym}] {label:15s}  HTTP {r.status_code}  {len(r.text):,}b  {url[:60]}")
        return ok
    except Exception as e:
        print(f"  [!] {label:15s}  UNREACHABLE  {url[:60]}")
        print(f"       {e}")
        return False


# ─── Tor check ────────────────────────────────────────────────────────────────

def check_tor() -> bool:
    print("[*] Checking Tor connectivity...")
    try:
        r = requests.get(
            "http://check.torproject.org/api/ip",
            proxies=TOR_PROXY, headers=HEADERS, timeout=TIMEOUT,
        )
        data = r.json()
        if data.get("IsTor"):
            print(f"[+] Tor OK — exit node: {data.get('IP', 'unknown')}")
            return True
        print(f"[-] NOT through Tor (IP: {data.get('IP')})")
        return False
    except requests.exceptions.ConnectionError:
        print("[!] Connection refused — is Tor running?  →  sudo systemctl start tor")
        return False
    except Exception as e:
        print(f"[!] Tor check failed: {e}")
        return False


# ─── Scrapers ─────────────────────────────────────────────────────────────────

def search_ahmia(keyword: str) -> list[dict]:
    """
    Ahmia — largest curated .onion index.
    Tries .onion v3 address, then clearnet ahmia.fi (both routed via Tor).
    Multiple selector fallbacks for HTML variance between instances.
    """
    from bs4 import BeautifulSoup
    q = quote(keyword)
    targets = [
        ("ahmia_onion",    f"http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q={q}"),
        ("ahmia_clearnet", f"https://ahmia.fi/search/?q={q}"),
    ]

    for label, url in targets:
        r = fetch(url, label)
        if r is None or r.status_code >= 400:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        # Strategy 1: canonical Ahmia structure <li class="result">
        for item in soup.select("li.result"):
            title_el   = item.select_one("h4 a") or item.select_one("a")
            snippet_el = item.select_one("p.description") or item.select_one("p")
            cite_el    = item.select_one("cite")
            if not title_el:
                continue
            results.append({
                "source":  "Ahmia",
                "title":   title_el.get_text(strip=True),
                "url":     cite_el.get_text(strip=True) if cite_el else title_el.get("href", ""),
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })

        # Strategy 2: generic anchor scan if strategy 1 empty
        if not results:
            for a in soup.select("a[href*='.onion']"):
                parent = a.find_parent(["li", "div", "article"])
                snippet = parent.get_text(" ", strip=True) if parent else ""
                results.append({
                    "source":  "Ahmia",
                    "title":   a.get_text(strip=True) or a["href"],
                    "url":     a["href"],
                    "snippet": snippet[:200],
                })

        if results:
            return results

    return []


def search_torch(keyword: str) -> list[dict]:
    """
    Torch — oldest .onion search engine.
    Primary + two known mirror addresses, multiple selector strategies.
    """
    from bs4 import BeautifulSoup
    q = quote(keyword)
    targets = [
        ("torch_v1", f"http://torchdeedp3i2jigzjdmfpn5ttjhthh5wbmda2rr3jvqjg5p77c54dqd.onion/search?query={q}&action=search"),
        ("torch_v2", f"http://torchqsxkqf6sb77gw2xbdgmnnuohiyb6bzzmml3uxqpbqiokjowxid.onion/search?query={q}&action=search"),
    ]

    for label, url in targets:
        r = fetch(url, label)
        if r is None or r.status_code >= 400:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        # Strategy 1: <dl><dt><a> structure
        for dl in soup.select("dl"):
            title_el   = dl.select_one("dt a")
            snippet_el = dl.select_one("dd")
            if not title_el:
                continue
            results.append({
                "source":  "Torch",
                "title":   title_el.get_text(strip=True),
                "url":     title_el.get("href", ""),
                "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
            })

        # Strategy 2: generic result divs
        if not results:
            for item in soup.select(".result, .searchresult, .search-result"):
                a = item.find("a")
                p = item.find("p")
                if a:
                    results.append({
                        "source":  "Torch",
                        "title":   a.get_text(strip=True),
                        "url":     a.get("href", ""),
                        "snippet": p.get_text(strip=True) if p else "",
                    })

        if results:
            return results

    return []


def search_haystak(keyword: str) -> list[dict]:
    """
    Haystak — ~1.5B page index.
    Multiple known .onion addresses (they rotate/change frequently).
    """
    from bs4 import BeautifulSoup
    q = quote(keyword)
    targets = [
        ("haystak_a", f"http://haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion/?q={q}"),
        ("haystak_b", f"http://haystakvxad7wbk5jo2a7vcmh7tsmkrqep4btykvtsbugbqpv6jcwyd.onion/?q={q}"),
        ("haystak_c", f"http://haystakvxad7wbk5jo2a7vcmh7tsmkrqep4btykvtsbugbqpv6jcwyd.onion/search?q={q}"),
    ]

    for label, url in targets:
        r = fetch(url, label)
        if r is None or r.status_code >= 400:
            continue

        soup = BeautifulSoup(r.text, "html.parser")
        results = []

        for item in soup.select(".result, .searchresult, li, article"):
            a = item.select_one("a[href]")
            p = item.select_one("p, .description, .desc, span")
            if not a or not a.get("href", "").strip():
                continue
            href = a["href"]
            # skip nav/boilerplate anchors
            if href in ("#", "/", "") or href.startswith("?"):
                continue
            results.append({
                "source":  "Haystak",
                "title":   a.get_text(strip=True) or href,
                "url":     href,
                "snippet": p.get_text(strip=True) if p else "",
            })

        if results:
            return results

    return []


def search_darksearch(keyword: str) -> list[dict]:
    """
    DarkSearch.io REST API — clearnet-accessible index, no Tor required.
    Free tier: 100 req/day. Routed through Tor anyway for opsec.
    """
    q = quote(keyword)
    url = f"https://darksearch.io/api/search?query={q}&page=1"
    r = fetch(url, "darksearch")
    if r is None:
        return []
    if r.status_code == 429:
        print("    [!] DarkSearch rate limit hit (100/day free tier)")
        return []
    try:
        data = r.json()
        return [
            {
                "source":  "DarkSearch",
                "title":   item.get("title", ""),
                "url":     item.get("link", ""),
                "snippet": item.get("description", ""),
            }
            for item in data.get("data", [])
        ]
    except Exception as e:
        print(f"    [!] DarkSearch JSON parse error: {e}")
        return []


def search_excavator(keyword: str) -> list[dict]:
    """
    Excavator — forum/market focused .onion index.
    """
    from bs4 import BeautifulSoup
    q = quote(keyword)
    url = f"http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/search/?search={q}"
    r = fetch(url, "excavator")
    if r is None or r.status_code >= 400:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for item in soup.select(".result, article, .item, li"):
        a = item.select_one("a[href]")
        p = item.select_one("p, .desc, .snippet, span")
        if not a:
            continue
        href = a.get("href", "")
        if not href or href in ("#", "/"):
            continue
        results.append({
            "source":  "Excavator",
            "title":   a.get_text(strip=True) or href,
            "url":     href,
            "snippet": p.get_text(strip=True) if p else "",
        })
    return results


def search_paste_sites(keyword: str) -> list[dict]:
    """
    .onion paste repos — scan recent paste index pages for keyword hits.
    """
    from bs4 import BeautifulSoup
    kw_lower = keyword.lower()
    targets = [
        ("paste_zerob",  "http://zerobinqmdqd236y.onion"),
        ("paste_priv",   "http://pastio7qbhlpkdlgq4pgsaqn4lfbqhxbgzv4gd3m75tpk5o3bh7i7ad.onion"),
    ]
    results = []
    for label, base_url in targets:
        r = fetch(base_url, label)
        if r is None:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            text  = a.get_text(strip=True)
            title = a.get("title", "")
            if kw_lower in text.lower() or kw_lower in title.lower():
                href = a["href"]
                if not href.startswith("http"):
                    href = base_url.rstrip("/") + "/" + href.lstrip("/")
                results.append({
                    "source":  "Paste/.onion",
                    "title":   text or title,
                    "url":     href,
                    "snippet": "",
                })
    return results


# ─── Probe mode ───────────────────────────────────────────────────────────────

def run_probe():
    """
    Test connectivity to every .onion target without searching.
    Use this to find which addresses are currently reachable.
    """
    print("\n[*] PROBE MODE — testing connectivity to all .onion targets\n")
    checks = [
        ("Ahmia (.onion)",    "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/"),
        ("Ahmia (clearnet)",  "https://ahmia.fi/"),
        ("Torch v1",          "http://torchdeedp3i2jigzjdmfpn5ttjhthh5wbmda2rr3jvqjg5p77c54dqd.onion/"),
        ("Torch v2",          "http://torchqsxkqf6sb77gw2xbdgmnnuohiyb6bzzmml3uxqpbqiokjowxid.onion/"),
        ("Haystak a",         "http://haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion/"),
        ("Haystak b",         "http://haystakvxad7wbk5jo2a7vcmh7tsmkrqep4btykvtsbugbqpv6jcwyd.onion/"),
        ("DarkSearch API",    "https://darksearch.io/api/search?query=test"),
        ("Excavator",         "http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/"),
        ("Paste ZeroBin",     "http://zerobinqmdqd236y.onion"),
        ("Paste PrivateBin",  "http://pastio7qbhlpkdlgq4pgsaqn4lfbqhxbgzv4gd3m75tpk5o3bh7i7ad.onion"),
    ]
    reachable = 0
    for label, url in checks:
        if probe_url(url, label):
            reachable += 1
        time.sleep(1)
    print(f"\n  {reachable}/{len(checks)} targets reachable\n")


# ─── Output ───────────────────────────────────────────────────────────────────

CYAN  = "\033[96m"
DIM   = "\033[2m"
RESET = "\033[0m"
BOLD  = "\033[1m"


def print_banner(keyword: str, total: int):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{CYAN}{'═'*68}{RESET}")
    print(f"{CYAN}  DECINT // DARK WEB SEARCH{RESET}")
    print(f"  keyword : {BOLD}{keyword}{RESET}")
    print(f"  time    : {DIM}{ts}{RESET}")
    print(f"  results : {BOLD}{total}{RESET}")
    print(f"{CYAN}{'═'*68}{RESET}\n")


def print_results_table(results: list[dict], keyword: str):
    print_banner(keyword, len(results))
    if not results:
        print(f"  {DIM}No results. Try --probe to check which .onions are up,"
              f" or try --debug to inspect raw HTML.{RESET}\n")
        return
    for i, r in enumerate(results, 1):
        src     = r.get("source", "?")
        title   = r.get("title", "(no title)").strip()
        url     = r.get("url", "").strip()
        snippet = r.get("snippet", "").strip()
        print(f"{CYAN}[{i:03d}]{RESET} {BOLD}{src}{RESET}")
        print(f"  Title   : {title[:90]}")
        if url:
            print(f"  URL     : {DIM}{url[:90]}{RESET}")
        if snippet:
            wrapped = textwrap.fill(
                snippet, width=80,
                initial_indent="  Snippet : ",
                subsequent_indent="           ",
            )
            print(f"{DIM}{wrapped[:300]}{RESET}")
        print()


def deduplicate(results: list[dict]) -> list[dict]:
    seen = set()
    out  = []
    for r in results:
        key = (r.get("url") or r.get("title") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


# ─── Main ─────────────────────────────────────────────────────────────────────

SOURCE_MAP = {
    "ahmia":      ("Ahmia",      search_ahmia),
    "torch":      ("Torch",      search_torch),
    "haystak":    ("Haystak",    search_haystak),
    "darksearch": ("DarkSearch", search_darksearch),
    "excavator":  ("Excavator",  search_excavator),
    "paste":      ("Paste",      search_paste_sites),
}
DEFAULT_SOURCES = ["ahmia", "torch", "haystak", "darksearch", "excavator"]


def main():
    global DEBUG

    parser = argparse.ArgumentParser(
        description="DECINT Dark Web Keyword Search v1.1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              python3 search.py "keyword"
              python3 search.py "keyword" --sources ahmia torch darksearch
              python3 search.py "keyword" --debug          # raw HTML → /tmp/
              python3 search.py "keyword" --probe          # connectivity check, no search
              python3 search.py "keyword" --json --out results.json
              python3 search.py "keyword" --skip-tor-check
        """),
    )
    parser.add_argument("keyword", help="Search term or phrase")
    parser.add_argument(
        "--sources", nargs="+", choices=list(SOURCE_MAP.keys()),
        default=DEFAULT_SOURCES, metavar="SOURCE",
        help=f"Sources (default: {' '.join(DEFAULT_SOURCES)}). "
             f"Available: {', '.join(SOURCE_MAP.keys())}",
    )
    parser.add_argument("--json",           action="store_true", help="Raw JSON output")
    parser.add_argument("--out",            metavar="FILE",       help="Save JSON to file")
    parser.add_argument("--skip-tor-check", action="store_true", help="Skip Tor check")
    parser.add_argument("--debug",          action="store_true", help="Dump raw HTML to /tmp/decint_*.html")
    parser.add_argument("--probe",          action="store_true", help="Check .onion reachability without searching")
    parser.add_argument("--delay",          type=float, default=2.0,
                        help="Seconds between engine queries (default: 2.0)")

    args   = parser.parse_args()
    DEBUG  = args.debug

    print(f"\n  {BOLD}DECINT // DARK WEB SEARCH v1.1.0{RESET}")

    if not args.skip_tor_check:
        if not check_tor():
            print("\n[!] Aborting — Tor required.")
            sys.exit(1)
        print()

    if args.probe:
        run_probe()
        sys.exit(0)

    print(f"  Sources : {', '.join(args.sources)}")
    if DEBUG:
        print(f"  Debug   : HTML dumps → /tmp/decint_*.html")
    print()

    all_results = []
    for key in args.sources:
        name, fn = SOURCE_MAP[key]
        print(f"[*] Querying {name}...", end=" ", flush=True)
        r = fn(args.keyword)
        print(f"{len(r)} result(s)")
        all_results.extend(r)
        time.sleep(args.delay)

    all_results = deduplicate(all_results)

    if args.json:
        print(json.dumps(all_results, indent=2))
    else:
        print_results_table(all_results, args.keyword)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"[+] Saved → {args.out}")


if __name__ == "__main__":
    main()