"""
Instagram Brand Scraper
Finds a brand's Instagram profile URL and follower count by searching online.

Usage:
    python instagram_scraper.py "Nike"
    python instagram_scraper.py "Nike" "Adidas" "Puma"
"""

import re
import sys
import time
import argparse
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urljoin


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

# Matches e.g. "12.5M Followers" / "123,456 Followers" / "1.2K Followers"
FOLLOWERS_PATTERN = re.compile(
    r"([\d,]+(?:\.\d+)?[KkMmBb]?)\s*[Ff]ollowers?", re.IGNORECASE
)


EXCLUDED_HANDLES = {"explore", "p", "reel", "reels", "stories", "tv", "accounts", "about"}


def _extract_instagram_handle_from_html(html: str) -> str | None:
    """Extract the first valid Instagram profile URL found in raw HTML."""
    for match in INSTAGRAM_URL_PATTERN.finditer(html):
        handle = match.group(1)
        if handle.lower() not in EXCLUDED_HANDLES:
            return f"https://www.instagram.com/{handle}/"
    return None


def _search_duckduckgo(query: str) -> str | None:
    """Search DuckDuckGo HTML interface and return first Instagram profile URL."""
    url = f"https://duckduckgo.com/html/?q={query}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] DuckDuckGo request failed: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup.select("a.result__url, a.result__a, a[href]"):
        href = tag.get("href", "")
        match = INSTAGRAM_URL_PATTERN.search(href)
        if match and match.group(1).lower() not in EXCLUDED_HANDLES:
            return f"https://www.instagram.com/{match.group(1)}/"

    return _extract_instagram_handle_from_html(resp.text)


def _search_google(query: str) -> str | None:
    """Search Google and return first Instagram profile URL from results."""
    url = f"https://www.google.com/search?q={query}&num=5"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Google request failed: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        # Google wraps links in /url?q=<actual-url>
        if "/url?q=" in href:
            href = href.split("/url?q=")[1].split("&")[0]
        match = INSTAGRAM_URL_PATTERN.search(href)
        if match and match.group(1).lower() not in EXCLUDED_HANDLES:
            return f"https://www.instagram.com/{match.group(1)}/"

    return _extract_instagram_handle_from_html(resp.text)


def search_instagram_url(brand_name: str) -> str | None:
    """Search online for the brand's Instagram profile URL.

    Tries DuckDuckGo first, then falls back to Google.
    """
    query = quote_plus(f"{brand_name} site:instagram.com")

    result = _search_duckduckgo(query)
    if result:
        return result

    print("  [~] DuckDuckGo yielded no result, trying Google...")
    time.sleep(1)
    return _search_google(query)


def get_follower_count(instagram_url: str) -> str | None:
    """
    Fetch the Instagram profile page and extract the follower count.
    Instagram embeds follower info in the <meta name="description"> tag:
      "X Followers, Y Following, Z Posts - See Instagram photos..."
    """
    try:
        resp = requests.get(instagram_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Failed to fetch Instagram page: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Primary: <meta name="description"> or <meta property="og:description">
    for selector in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ]:
        tag = soup.find("meta", selector)
        if tag:
            content = tag.get("content", "")
            match = FOLLOWERS_PATTERN.search(content)
            if match:
                return match.group(1)

    # Fallback: scan all meta tags
    for tag in soup.find_all("meta"):
        content = tag.get("content", "")
        match = FOLLOWERS_PATTERN.search(content)
        if match:
            return match.group(1)

    # Last resort: scan raw HTML (Instagram sometimes embeds JSON)
    match = FOLLOWERS_PATTERN.search(resp.text)
    if match:
        return match.group(1)

    return None


def scrape_brand(brand_name: str) -> dict:
    """Return a dict with brand, instagram_url, and followers."""
    print(f"\nSearching for: {brand_name}")

    instagram_url = search_instagram_url(brand_name)
    if not instagram_url:
        print("  [x] Could not find Instagram profile.")
        return {"brand": brand_name, "instagram_url": None, "followers": None}

    print(f"  Found profile: {instagram_url}")

    # Polite delay before hitting Instagram
    time.sleep(1.5)

    followers = get_follower_count(instagram_url)
    if followers:
        print(f"  Followers: {followers}")
    else:
        print("  [!] Follower count not available (Instagram may require login).")

    return {
        "brand": brand_name,
        "instagram_url": instagram_url,
        "followers": followers,
    }


def print_table(results: list[dict]) -> None:
    """Pretty-print results as a table."""
    col_brand = max(len(r["brand"]) for r in results)
    col_url = max(len(r["instagram_url"] or "N/A") for r in results)
    col_fol = max(len(r["followers"] or "N/A") for r in results)

    col_brand = max(col_brand, 5)
    col_url = max(col_url, 13)
    col_fol = max(col_fol, 9)

    header = (
        f"{'Brand':<{col_brand}}  "
        f"{'Instagram URL':<{col_url}}  "
        f"{'Followers':<{col_fol}}"
    )
    separator = "-" * len(header)

    print(f"\n{separator}")
    print(header)
    print(separator)
    for r in results:
        print(
            f"{r['brand']:<{col_brand}}  "
            f"{(r['instagram_url'] or 'N/A'):<{col_url}}  "
            f"{(r['followers'] or 'N/A'):<{col_fol}}"
        )
    print(separator)


def main():
    parser = argparse.ArgumentParser(
        description="Find Instagram profile URL and follower count for brand(s).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python instagram_scraper.py Nike\n"
            "  python instagram_scraper.py Nike Adidas Puma\n"
            "  python instagram_scraper.py --url https://www.instagram.com/nike/ Nike"
        ),
    )
    parser.add_argument("brands", nargs="+", help="Brand name(s) to look up")
    parser.add_argument(
        "--url",
        metavar="INSTAGRAM_URL",
        help="Skip search and use this Instagram URL directly (only for single brand)",
    )
    args = parser.parse_args()

    if args.url and len(args.brands) > 1:
        parser.error("--url can only be used with a single brand name")

    results = []
    for i, brand in enumerate(args.brands):
        if i > 0:
            time.sleep(2)  # Polite delay between brands
        if args.url and i == 0:
            # User supplied the URL directly — skip search, only fetch followers
            print(f"\nUsing provided URL for: {brand}")
            instagram_url = args.url.rstrip("/") + "/"
            print(f"  Profile: {instagram_url}")
            time.sleep(0.5)
            followers = get_follower_count(instagram_url)
            if followers:
                print(f"  Followers: {followers}")
            else:
                print("  [!] Follower count not available (Instagram may require login).")
            results.append({"brand": brand, "instagram_url": instagram_url, "followers": followers})
        else:
            results.append(scrape_brand(brand))

    if len(results) > 1:
        print_table(results)
    else:
        r = results[0]
        print(f"\nResult for '{r['brand']}':")
        print(f"  Instagram URL : {r['instagram_url'] or 'Not found'}")
        print(f"  Followers     : {r['followers'] or 'Not available'}")


if __name__ == "__main__":
    main()
