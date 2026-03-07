"""
Instagram Brand Scraper — bulk edition
Reads brand names from a file and finds each brand's Instagram URL + follower
count by parsing DuckDuckGo search result snippets (no Instagram login needed).

Usage:
    python instagram_scraper.py brands.txt
    python instagram_scraper.py brands.txt --output results.csv --workers 8
    python instagram_scraper.py brands.csv --col 2          # brand name is in column 2
    python instagram_scraper.py brands.txt --workers 5 --delay 1.5

Resume:  if the output CSV already exists the script automatically skips brands
         that were already scraped — safe to re-run after a crash.

Input file formats supported:
    • Plain text  — one brand name per line
    • CSV/TSV     — specify which column holds the brand name with --col (1-based)
"""

import csv
import os
import re
import sys
import time
import random
import argparse
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

INSTAGRAM_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)/?", re.IGNORECASE
)
FOLLOWERS_PATTERN = re.compile(
    r"([\d,]+(?:\.\d+)?[KkMmBb]?)\s*[Ff]ollowers?", re.IGNORECASE
)
EXCLUDED_HANDLES = {"explore", "p", "reel", "reels", "stories", "tv", "accounts", "about"}

OUTPUT_FIELDS = ["brand", "instagram_url", "followers", "status"]

# ---------------------------------------------------------------------------
# Rate limiter — enforces a minimum gap between outgoing requests globally
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, min_gap_seconds: float):
        self._gap = min_gap_seconds
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            wait_for = self._last + self._gap - now
            if wait_for > 0:
                time.sleep(wait_for)
            # Add small random jitter to avoid thundering-herd
            time.sleep(random.uniform(0.05, 0.2))
            self._last = time.monotonic()


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def _is_valid_handle(handle: str) -> bool:
    return handle.lower() not in EXCLUDED_HANDLES


def _parse_result_blocks(soup: BeautifulSoup, raw_html: str) -> dict | None:
    """
    Walk DuckDuckGo result <div class="result"> blocks.
    Each block has an anchor with the Instagram URL and a snippet with follower count.
    """
    for result in soup.select("div.result, div.results_links"):
        link_tag = result.select_one("a.result__a, a.result__url")
        if not link_tag:
            continue

        href = link_tag.get("href", "")
        url_match = INSTAGRAM_URL_PATTERN.search(href)
        if not url_match or not _is_valid_handle(url_match.group(1)):
            text_url = INSTAGRAM_URL_PATTERN.search(link_tag.get_text())
            if not text_url or not _is_valid_handle(text_url.group(1)):
                continue
            url_match = text_url

        instagram_url = f"https://www.instagram.com/{url_match.group(1)}/"
        snippet_tag = result.select_one(
            "a.result__snippet, div.result__snippet, span.result__snippet"
        )
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag else ""
        followers_match = FOLLOWERS_PATTERN.search(snippet)
        return {
            "instagram_url": instagram_url,
            "followers": followers_match.group(1) if followers_match else None,
        }

    # Fallback: raw HTML scan
    for m in INSTAGRAM_URL_PATTERN.finditer(raw_html):
        if _is_valid_handle(m.group(1)):
            fm = FOLLOWERS_PATTERN.search(raw_html)
            return {
                "instagram_url": f"https://www.instagram.com/{m.group(1)}/",
                "followers": fm.group(1) if fm else None,
            }
    return None


def _search_duckduckgo(brand_name: str, rate_limiter: RateLimiter) -> dict | None:
    query = quote_plus(f"{brand_name} site:instagram.com")
    url = f"https://duckduckgo.com/html/?q={query}"
    rate_limiter.wait()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            return {"_blocked": True}
        resp.raise_for_status()
    except requests.RequestException:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    return _parse_result_blocks(soup, resp.text)


def _search_google(brand_name: str, rate_limiter: RateLimiter) -> dict | None:
    query = quote_plus(f"{brand_name} site:instagram.com")
    url = f"https://www.google.com/search?q={query}&num=5"
    rate_limiter.wait()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            return {"_blocked": True}
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for result in soup.select("div.g, div[data-hveid]"):
        link_tag = result.find("a", href=True)
        if not link_tag:
            continue
        href = link_tag["href"]
        if "/url?q=" in href:
            href = href.split("/url?q=")[1].split("&")[0]
        url_match = INSTAGRAM_URL_PATTERN.search(href)
        if not url_match or not _is_valid_handle(url_match.group(1)):
            continue
        snippet = result.get_text(" ", strip=True)
        fm = FOLLOWERS_PATTERN.search(snippet)
        return {
            "instagram_url": f"https://www.instagram.com/{url_match.group(1)}/",
            "followers": fm.group(1) if fm else None,
        }

    for m in INSTAGRAM_URL_PATTERN.finditer(resp.text):
        if _is_valid_handle(m.group(1)):
            fm = FOLLOWERS_PATTERN.search(resp.text)
            return {
                "instagram_url": f"https://www.instagram.com/{m.group(1)}/",
                "followers": fm.group(1) if fm else None,
            }
    return None


# ---------------------------------------------------------------------------
# Per-brand scrape with retry logic
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 45]   # seconds to wait after each failed attempt


def scrape_brand(brand_name: str, ddg_rl: RateLimiter, goog_rl: RateLimiter) -> dict:
    """
    Try DuckDuckGo first; fall back to Google.
    Retries up to MAX_RETRIES times if rate-limited or blocked.
    Returns a dict: brand, instagram_url, followers, status
    """
    base = {"brand": brand_name, "instagram_url": None, "followers": None}

    for attempt in range(MAX_RETRIES):
        result = _search_duckduckgo(brand_name, ddg_rl)

        if result and result.get("_blocked"):
            # Rate-limited — back off and try again
            sleep_for = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            time.sleep(sleep_for)
            continue

        if not result:
            # DDG failed, try Google
            result = _search_google(brand_name, goog_rl)

        if result and result.get("_blocked"):
            sleep_for = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
            time.sleep(sleep_for)
            continue

        if result:
            return {
                **base,
                "instagram_url": result["instagram_url"],
                "followers": result["followers"],
                "status": "ok" if result["followers"] else "url_only",
            }

    # All retries exhausted
    return {**base, "status": "not_found"}


# ---------------------------------------------------------------------------
# Input / output helpers
# ---------------------------------------------------------------------------

def read_brands(filepath: str, col: int) -> list[str]:
    """Read brand names from a plain-text or CSV file."""
    brands = []
    ext = os.path.splitext(filepath)[1].lower()

    with open(filepath, newline="", encoding="utf-8-sig") as fh:
        if ext in (".csv", ".tsv"):
            delimiter = "\t" if ext == ".tsv" else ","
            reader = csv.reader(fh, delimiter=delimiter)
            for row in reader:
                if not row:
                    continue
                idx = col - 1
                if idx < len(row):
                    name = row[idx].strip()
                    if name:
                        brands.append(name)
        else:
            for line in fh:
                name = line.strip()
                if name and not name.startswith("#"):
                    brands.append(name)

    return brands


def load_already_done(output_path: str) -> set[str]:
    """Return set of brand names already present in the output CSV."""
    done = set()
    if not os.path.exists(output_path):
        return done
    with open(output_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = row.get("brand", "").strip()
            if name:
                done.add(name)
    return done


def open_output_csv(output_path: str) -> tuple:
    """Open output CSV for appending; write header if new file."""
    is_new = not os.path.exists(output_path)
    fh = open(output_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
    if is_new:
        writer.writeheader()
    return fh, writer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Bulk Instagram brand scraper — reads from file, writes to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python instagram_scraper.py brands.txt\n"
            "  python instagram_scraper.py brands.csv --col 2 --output results.csv\n"
            "  python instagram_scraper.py brands.txt --workers 8 --delay 0.8\n"
            "\nnote: re-running the same command resumes from where it left off."
        ),
    )
    parser.add_argument("input", help="Input file (.txt, .csv, or .tsv) with brand names")
    parser.add_argument("--output", default="instagram_results.csv",
                        help="Output CSV file (default: instagram_results.csv)")
    parser.add_argument("--col", type=int, default=1,
                        help="Column number (1-based) for brand name in CSV input (default: 1)")
    parser.add_argument("--workers", type=int, default=5,
                        help="Parallel worker threads (default: 5; max recommended: 10)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Minimum seconds between requests per search engine (default: 1.0)")
    args = parser.parse_args()

    if args.workers > 10:
        print(f"Warning: {args.workers} workers is aggressive and may trigger rate limiting.")

    # Load brands
    print(f"Reading brands from: {args.input}")
    all_brands = read_brands(args.input, args.col)
    if not all_brands:
        print("No brands found in input file.")
        sys.exit(1)
    print(f"Total brands in file : {len(all_brands):,}")

    # Skip already-processed brands (resume support)
    done = load_already_done(args.output)
    brands_to_do = [b for b in all_brands if b not in done]
    print(f"Already done         : {len(done):,}")
    print(f"Remaining            : {len(brands_to_do):,}")

    if not brands_to_do:
        print("All brands already processed. Nothing to do.")
        sys.exit(0)

    # Shared rate limiters — one per search engine (shared across all threads)
    ddg_rate  = RateLimiter(args.delay)
    goog_rate = RateLimiter(args.delay)

    # Output CSV (append mode)
    out_fh, writer = open_output_csv(args.output)
    write_lock = threading.Lock()

    total     = len(brands_to_do)
    completed = 0
    errors    = 0
    start     = time.monotonic()

    print(f"\nStarting scrape with {args.workers} workers...\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(scrape_brand, brand, ddg_rate, goog_rate): brand
            for brand in brands_to_do
        }

        for future in as_completed(futures):
            result = future.result()
            completed += 1
            if result["status"] == "not_found":
                errors += 1

            with write_lock:
                writer.writerow(result)
                out_fh.flush()   # Ensure data is written even if we crash

            # Progress line
            elapsed  = time.monotonic() - start
            rate     = completed / elapsed if elapsed else 0
            eta_secs = (total - completed) / rate if rate else 0
            eta_str  = time.strftime("%H:%M:%S", time.gmtime(eta_secs))
            pct      = completed / total * 100

            status_icon = "✓" if result["status"] == "ok" else (
                          "~" if result["status"] == "url_only" else "✗")
            print(
                f"[{pct:5.1f}%] {completed:>{len(str(total))}}/{total}  "
                f"ETA {eta_str}  {status_icon} {result['brand'][:40]}"
                f"  →  {result['instagram_url'] or 'not found'}"
                f"  {result['followers'] or ''}",
                flush=True,
            )

    out_fh.close()

    elapsed = time.monotonic() - start
    ok      = completed - errors
    print(f"\n{'─'*60}")
    print(f"Done in {elapsed/60:.1f} min")
    print(f"  Found     : {ok:,} / {total:,}")
    print(f"  Not found : {errors:,}")
    print(f"  Output    : {args.output}")


if __name__ == "__main__":
    main()
