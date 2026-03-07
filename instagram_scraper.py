"""
Instagram Brand Scraper
Finds a brand's Instagram profile URL and follower count by searching online.
Extracts follower count directly from search result snippets — no Instagram login needed.

Usage:
    python instagram_scraper.py "Nike"
    python instagram_scraper.py "Nike" "Adidas" "Puma"
"""

import re
import time
import argparse
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus


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

# Matches "301K Followers" / "12.5M Followers" / "123,456 Followers"
FOLLOWERS_PATTERN = re.compile(
    r"([\d,]+(?:\.\d+)?[KkMmBb]?)\s*[Ff]ollowers?", re.IGNORECASE
)

EXCLUDED_HANDLES = {"explore", "p", "reel", "reels", "stories", "tv", "accounts", "about"}


def _is_valid_handle(handle: str) -> bool:
    return handle.lower() not in EXCLUDED_HANDLES


def _search_duckduckgo(brand_name: str) -> dict | None:
    """
    Search DuckDuckGo for '<brand> site:instagram.com' and parse the result snippets.
    Each snippet looks like:
      "301K Followers, 7 Following, 1,649 Posts - See Instagram photos and videos from ..."
    Returns a dict with 'instagram_url' and 'followers', or None on failure.
    """
    query = quote_plus(f"{brand_name} site:instagram.com")
    url = f"https://duckduckgo.com/html/?q={query}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] DuckDuckGo request failed: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Each result is a <div class="result ...">
    # containing a <a class="result__a"> link and a <a class="result__snippet"> or
    # <div class="result__snippet"> text block.
    for result in soup.select("div.result, div.results_links"):
        # Find the link to get the Instagram handle
        link_tag = result.select_one("a.result__a, a.result__url")
        if not link_tag:
            continue

        href = link_tag.get("href", "")
        url_match = INSTAGRAM_URL_PATTERN.search(href)
        if not url_match or not _is_valid_handle(url_match.group(1)):
            # Also try the visible URL text (DuckDuckGo sometimes puts the
            # real URL in the text of result__url)
            text_url = INSTAGRAM_URL_PATTERN.search(link_tag.get_text())
            if not text_url or not _is_valid_handle(text_url.group(1)):
                continue
            url_match = text_url

        handle = url_match.group(1)
        instagram_url = f"https://www.instagram.com/{handle}/"

        # Find the snippet text in the same result block
        snippet_tag = result.select_one(
            "a.result__snippet, div.result__snippet, span.result__snippet"
        )
        snippet = snippet_tag.get_text(" ", strip=True) if snippet_tag else ""

        followers_match = FOLLOWERS_PATTERN.search(snippet)
        followers = followers_match.group(1) if followers_match else None

        return {"instagram_url": instagram_url, "followers": followers}

    # Fallback: no structured result found — scan raw HTML
    url_match = None
    for m in INSTAGRAM_URL_PATTERN.finditer(resp.text):
        if _is_valid_handle(m.group(1)):
            url_match = m
            break

    if url_match:
        instagram_url = f"https://www.instagram.com/{url_match.group(1)}/"
        followers_match = FOLLOWERS_PATTERN.search(resp.text)
        return {
            "instagram_url": instagram_url,
            "followers": followers_match.group(1) if followers_match else None,
        }

    return None


def _search_google(brand_name: str) -> dict | None:
    """
    Fallback: search Google for '<brand> site:instagram.com' and parse snippets.
    Google snippets also contain "X Followers, Y Following..." for Instagram profiles.
    """
    query = quote_plus(f"{brand_name} site:instagram.com")
    url = f"https://www.google.com/search?q={query}&num=5"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Google request failed: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Google result blocks: each <div class="g"> contains a link + snippet
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

        instagram_url = f"https://www.instagram.com/{url_match.group(1)}/"
        snippet = result.get_text(" ", strip=True)
        followers_match = FOLLOWERS_PATTERN.search(snippet)

        return {
            "instagram_url": instagram_url,
            "followers": followers_match.group(1) if followers_match else None,
        }

    # Fallback: raw HTML scan
    for m in INSTAGRAM_URL_PATTERN.finditer(resp.text):
        if _is_valid_handle(m.group(1)):
            instagram_url = f"https://www.instagram.com/{m.group(1)}/"
            followers_match = FOLLOWERS_PATTERN.search(resp.text)
            return {
                "instagram_url": instagram_url,
                "followers": followers_match.group(1) if followers_match else None,
            }

    return None


def scrape_brand(brand_name: str) -> dict:
    """Search for the brand and return instagram_url + followers."""
    print(f"\nSearching for: {brand_name}")

    result = _search_duckduckgo(brand_name)

    if not result:
        print("  [~] DuckDuckGo yielded no result, trying Google...")
        time.sleep(1)
        result = _search_google(brand_name)

    if not result:
        print("  [x] Could not find Instagram profile.")
        return {"brand": brand_name, "instagram_url": None, "followers": None}

    print(f"  Instagram URL : {result['instagram_url']}")
    if result["followers"]:
        print(f"  Followers     : {result['followers']}")
    else:
        print("  [!] Follower count not found in search snippet.")

    return {
        "brand": brand_name,
        "instagram_url": result["instagram_url"],
        "followers": result["followers"],
    }


def print_table(results: list[dict]) -> None:
    """Pretty-print results as a table."""
    col_brand = max(len(r["brand"]) for r in results)
    col_url   = max(len(r["instagram_url"] or "N/A") for r in results)
    col_fol   = max(len(r["followers"]     or "N/A") for r in results)

    col_brand = max(col_brand, 5)
    col_url   = max(col_url,   13)
    col_fol   = max(col_fol,   9)

    header = (
        f"{'Brand':<{col_brand}}  "
        f"{'Instagram URL':<{col_url}}  "
        f"{'Followers':<{col_fol}}"
    )
    sep = "-" * len(header)

    print(f"\n{sep}")
    print(header)
    print(sep)
    for r in results:
        print(
            f"{r['brand']:<{col_brand}}  "
            f"{(r['instagram_url'] or 'N/A'):<{col_url}}  "
            f"{(r['followers']     or 'N/A'):<{col_fol}}"
        )
    print(sep)


def main():
    parser = argparse.ArgumentParser(
        description="Find Instagram URL and follower count for brand(s) via search snippets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python instagram_scraper.py Nike\n"
            "  python instagram_scraper.py Nike Adidas Puma"
        ),
    )
    parser.add_argument("brands", nargs="+", help="Brand name(s) to look up")
    args = parser.parse_args()

    results = []
    for i, brand in enumerate(args.brands):
        if i > 0:
            time.sleep(2)  # polite delay between brands
        results.append(scrape_brand(brand))

    if len(results) > 1:
        print_table(results)
    else:
        r = results[0]
        print(f"\nResult for '{r['brand']}':")
        print(f"  Instagram URL : {r['instagram_url'] or 'Not found'}")
        print(f"  Followers     : {r['followers']     or 'Not available'}")


if __name__ == "__main__":
    main()
