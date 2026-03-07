"""
Instagram Brand Scraper — bulk edition
Reads brand names from a file and finds each brand's Instagram URL, follower
count, D2C website, ecommerce platform (Shopify / WooCommerce / etc.), and
cross-checks the Instagram handle found on the brand's own website.

Usage:
    python instagram_scraper.py brands.txt
    python instagram_scraper.py brands.txt --region india    # bias search to India
    python instagram_scraper.py brands.txt --no-website      # skip website analysis (faster)
    python instagram_scraper.py brands.txt --output results.csv --workers 8
    python instagram_scraper.py brands.csv --col 2           # brand name in column 2

Resume:  if the output CSV already exists the script automatically skips brands
         that were already scraped — safe to re-run after a crash.

Input file formats:
    • Plain text  — one brand name per line
    • CSV/TSV     — specify which column holds the brand name with --col (1-based)

Output columns:
    brand                     Brand name from your input file
    instagram_url             Instagram profile URL found via search
    followers                 Follower count from search snippet (if available)
    confidence                HIGH / MEDIUM / LOW — how well the handle matches the brand
    confidence_reason         Explanation of the confidence score
    website_url               Brand's D2C / ecommerce website
    platform                  Shopify / WooCommerce / Unknown
    shopify_store             myshopify.com subdomain (e.g. brandname.myshopify.com)
    instagram_on_website      Instagram handle linked on the brand's own website
    website_confirms_instagram  yes / no / not_checked
    nykaa_url                 Brand's page on Nykaa (if found)
    amazon_in_url             Brand's page on Amazon India (if found)
    flipkart_url              Brand's page on Flipkart (if found)
    myntra_url                Brand's page on Myntra (if found)
    status                    ok / url_only / not_found

Confidence guide:
    HIGH    Handle closely matches the brand name             → almost certainly correct
    MEDIUM  Partial match or brand name found in snippet     → probably correct, spot-check
    LOW     No clear link between handle and brand name      → manual verification needed
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
from urllib.parse import quote_plus, urlparse, unquote
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

# Noise words stripped before comparing brand name vs. handle
_NOISE_WORDS = {
    "india", "official", "store", "shop", "brand", "beauty", "skincare",
    "care", "co", "the", "by", "get", "wear", "fashion", "style",
    "hq", "in", "uk", "us", "global", "world", "original", "real",
}

# Domains that are not a brand's own D2C website
_MARKETPLACE_KEYWORDS = {
    "amazon", "flipkart", "nykaa", "myntra", "meesho", "ajio", "snapdeal",
    "paytmmall", "tatacliq", "jiomart", "instagram", "facebook", "twitter",
    "x.com", "youtube", "linkedin", "indiamart", "justdial", "zomato",
    "swiggy", "bigbasket", "firstcry", "purplle", "healthkart", "1mg",
    "pharmeasy", "wikipedia", "reddit", "quora", "glassdoor", "crunchbase",
    "tracxn", "yourstory", "entrackr", "ambitionbox", "trustpilot",
}

# Marketplaces to search for the brand's listing page.
# Each entry: (csv_field_name, site_domain)
MARKETPLACE_TARGETS = [
    ("nykaa_url",     "nykaa.com"),
    ("amazon_in_url", "amazon.in"),
    ("flipkart_url",  "flipkart.com"),
    ("myntra_url",    "myntra.com"),
]

OUTPUT_FIELDS = [
    "brand",
    "instagram_url", "followers",
    "confidence", "confidence_reason",
    "website_url", "platform", "shopify_store",
    "instagram_on_website", "website_confirms_instagram",
    "nykaa_url", "amazon_in_url", "flipkart_url", "myntra_url",
    "status",
]

# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _normalize_remove_noise(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    words = [w for w in text.split() if w not in _NOISE_WORDS]
    return "".join(words)


def confidence_check(brand_name: str, instagram_url: str, snippet: str) -> tuple[str, str]:
    """
    Return (level, reason) where level is 'HIGH', 'MEDIUM', or 'LOW'.

    Scoring (0–100):
      50 pts  — handle contains brand name (normalized)
      35 pts  — match after stripping noise words (india / official / store …)
      20 pts  — brand name appears in the search snippet bio text
      10 pts  — handle contains the brand's first significant word
       5 pts  — handle has an official/india/store legitimacy suffix
    """
    handle = instagram_url.rstrip("/").split("/")[-1]
    brand_norm        = _normalize(brand_name)
    brand_norm_clean  = _normalize_remove_noise(brand_name)
    handle_norm       = _normalize(handle)
    handle_norm_clean = _normalize_remove_noise(handle)

    score   = 0
    reasons = []

    if brand_norm and (brand_norm in handle_norm or handle_norm in brand_norm):
        score += 50
        reasons.append("handle matches brand name")
    elif brand_norm_clean and (
        brand_norm_clean in handle_norm_clean
        or (handle_norm_clean in brand_norm_clean and len(brand_norm_clean) >= 3)
    ):
        score += 35
        reasons.append("handle matches brand (after removing common words)")
    else:
        brand_words = [
            w for w in re.sub(r"[^a-z0-9 ]", " ", brand_name.lower()).split()
            if w not in _NOISE_WORDS and len(w) >= 3
        ]
        if brand_words and brand_words[0] in handle_norm:
            score += 10
            reasons.append(f"handle contains '{brand_words[0]}'")

    if brand_name.lower() in snippet.lower():
        score += 20
        reasons.append("brand name in snippet")

    legitimacy = [w for w in _NOISE_WORDS if w in handle_norm and w in
                  {"official", "india", "store", "shop", "hq", "original", "real"}]
    if legitimacy:
        score += 5
        reasons.append(f"handle has '{legitimacy[0]}'")

    if score >= 50:
        level = "HIGH"
    elif score >= 20:
        level = "MEDIUM"
    else:
        level = "LOW"

    return level, "; ".join(reasons) if reasons else "no match found"


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, min_gap_seconds: float):
        self._gap  = min_gap_seconds
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            now      = time.monotonic()
            wait_for = self._last + self._gap - now
            if wait_for > 0:
                time.sleep(wait_for)
            time.sleep(random.uniform(0.05, 0.2))
            self._last = time.monotonic()


# ---------------------------------------------------------------------------
# Instagram search helpers
# ---------------------------------------------------------------------------

def _is_valid_handle(handle: str) -> bool:
    return handle.lower() not in EXCLUDED_HANDLES


def _parse_ddg_blocks(soup: BeautifulSoup, raw_html: str) -> dict | None:
    # DDG HTML structure varies; try multiple selector combos
    result_selectors = [
        "div.result",
        "div.results_links",
        "div.web-result",
        "article[data-testid]",
    ]
    link_selectors = [
        "a.result__a",
        "a.result__url",
        "h2 a",
        "a[href*='instagram.com']",
    ]
    snippet_selectors = [
        "a.result__snippet",
        "div.result__snippet",
        "span.result__snippet",
        "div.result__body",
        "span[class*='snippet']",
    ]

    for result_sel in result_selectors:
        for result in soup.select(result_sel):
            instagram_url = None
            for link_sel in link_selectors:
                link_tag = result.select_one(link_sel)
                if not link_tag:
                    continue
                href = link_tag.get("href", "")
                # DDG wraps links: extract real URL from uddg= param
                if "uddg=" in href:
                    m = re.search(r"uddg=([^&]+)", href)
                    if m:
                        href = unquote(m.group(1))
                url_match = INSTAGRAM_URL_PATTERN.search(href)
                if not url_match:
                    url_match = INSTAGRAM_URL_PATTERN.search(link_tag.get_text())
                if url_match and _is_valid_handle(url_match.group(1)):
                    instagram_url = f"https://www.instagram.com/{url_match.group(1)}/"
                    break

            if not instagram_url:
                continue

            snippet = ""
            for snip_sel in snippet_selectors:
                snip_tag = result.select_one(snip_sel)
                if snip_tag:
                    snippet = snip_tag.get_text(" ", strip=True)
                    break
            if not snippet:
                snippet = result.get_text(" ", strip=True)

            followers_match = FOLLOWERS_PATTERN.search(snippet)
            return {
                "instagram_url": instagram_url,
                "followers":     followers_match.group(1) if followers_match else None,
                "snippet":       snippet,
            }

    # Final fallback: scan raw HTML for any instagram.com URL
    for m in INSTAGRAM_URL_PATTERN.finditer(raw_html):
        if _is_valid_handle(m.group(1)):
            fm = FOLLOWERS_PATTERN.search(raw_html)
            return {
                "instagram_url": f"https://www.instagram.com/{m.group(1)}/",
                "followers":     fm.group(1) if fm else None,
                "snippet":       "",
            }
    return None


def _search_duckduckgo(query: str, rate_limiter: RateLimiter) -> dict | None:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    rate_limiter.wait()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 429:
            return {"_blocked": True}
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return _parse_ddg_blocks(BeautifulSoup(resp.text, "html.parser"), resp.text)


def _search_google(query: str, rate_limiter: RateLimiter) -> dict | None:
    url = f"https://www.google.com/search?q={quote_plus(query)}&num=5"
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
        fm      = FOLLOWERS_PATTERN.search(snippet)
        return {
            "instagram_url": f"https://www.instagram.com/{url_match.group(1)}/",
            "followers":     fm.group(1) if fm else None,
            "snippet":       snippet,
        }

    for m in INSTAGRAM_URL_PATTERN.finditer(resp.text):
        if _is_valid_handle(m.group(1)):
            fm = FOLLOWERS_PATTERN.search(resp.text)
            return {
                "instagram_url": f"https://www.instagram.com/{m.group(1)}/",
                "followers":     fm.group(1) if fm else None,
                "snippet":       "",
            }
    return None


# ---------------------------------------------------------------------------
# Website discovery helpers
# ---------------------------------------------------------------------------

def _is_marketplace_url(url: str) -> bool:
    """Return True if the URL belongs to a marketplace / social / non-D2C domain."""
    host = urlparse(url).netloc.lower().replace("www.", "")
    return any(kw in host for kw in _MARKETPLACE_KEYWORDS)


def _decode_ddg_href(href: str) -> str | None:
    """
    DDG wraps all result links as /l/?uddg=ENCODED_URL&rut=...
    Extract and decode the real destination URL.
    Falls through for direct http(s) links.
    """
    if not href:
        return None
    if "uddg=" in href:
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return unquote(m.group(1))
    if href.startswith("http"):
        return href
    return None


def _find_website_ddg(query: str, rate_limiter: RateLimiter) -> str | None:
    """Search DDG and return first non-marketplace URL."""
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    rate_limiter.wait()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for result in soup.select("div.result, div.results_links"):
        link_tag = result.select_one("a.result__a")
        if not link_tag:
            continue
        real_url = _decode_ddg_href(link_tag.get("href", ""))
        if real_url and not _is_marketplace_url(real_url):
            return real_url

        # Fallback: display URL text (e.g. "mamaearth.in › shop")
        url_span = result.select_one("span.result__url, div.result__url")
        if url_span:
            display = url_span.get_text(strip=True).split("›")[0].strip()
            if display and "." in display and not _is_marketplace_url(display):
                # Reconstruct full URL from display domain
                if not display.startswith("http"):
                    display = "https://" + display
                return display
    return None


def find_brand_website(brand_name: str, region: str, rate_limiter: RateLimiter) -> str | None:
    """Return the brand's D2C / ecommerce website URL, or None if not found."""
    suffix = f" {region}" if region else ""
    query  = f"{brand_name}{suffix} official website"
    return _find_website_ddg(query, rate_limiter)


def _search_on_site(brand_name: str, site: str, rate_limiter: RateLimiter) -> str | None:
    """
    Search DDG for a brand on a specific marketplace domain.
    Returns the first result URL that belongs to that domain.
    """
    query = f'"{brand_name}" site:{site}'
    url   = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    rate_limiter.wait()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for result in soup.select("div.result, div.results_links"):
        link_tag = result.select_one("a.result__a")
        if not link_tag:
            continue
        real_url = _decode_ddg_href(link_tag.get("href", ""))
        if real_url and site in real_url:
            return real_url

        # Fallback: display URL span
        url_span = result.select_one("span.result__url, div.result__url")
        if url_span:
            display = url_span.get_text(strip=True).split("›")[0].strip()
            if site in display:
                return "https://" + display if not display.startswith("http") else display
    return None


def find_marketplace_urls(brand_name: str, rate_limiter: RateLimiter) -> dict:
    """
    Search for the brand's listing page on each configured marketplace.
    Returns a dict keyed by the CSV field name, e.g.:
      {"nykaa_url": "https://...", "amazon_in_url": None, ...}
    """
    results = {}
    for field, site in MARKETPLACE_TARGETS:
        results[field] = _search_on_site(brand_name, site, rate_limiter)
    return results


# ---------------------------------------------------------------------------
# Website analysis — platform detection + Instagram handle extraction
# ---------------------------------------------------------------------------

_SHOPIFY_STORE_RE = re.compile(
    r'(?:"shop"\s*:\s*"|myshopify\.com/|Shopify\.shop\s*=\s*")([\w-]+)\.myshopify\.com',
    re.IGNORECASE,
)


def _detect_platform(html: str, final_url: str) -> tuple[str, str | None]:
    """
    Return (platform_name, shopify_store_domain).
    platform_name: 'Shopify' | 'WooCommerce' | 'Unknown'
    shopify_store_domain: e.g. 'brandname.myshopify.com' or None
    """
    # 1. URL itself redirected to myshopify.com
    if "myshopify.com" in final_url:
        m = re.search(r"([\w-]+\.myshopify\.com)", final_url)
        return "Shopify", m.group(1) if m else None

    # 2. Shopify signals in HTML
    if (
        "cdn.shopify.com" in html
        or 'content="Shopify"' in html
        or "Shopify.theme" in html
        or "shopify-section" in html
        or "window.Shopify" in html
    ):
        m = _SHOPIFY_STORE_RE.search(html)
        store = f"{m.group(1)}.myshopify.com" if m else None
        return "Shopify", store

    # 3. WooCommerce
    html_lower = html.lower()
    if "woocommerce" in html_lower or "wp-content/plugins/woo" in html_lower:
        return "WooCommerce", None

    return "Unknown", None


def _extract_instagram_handles_from_html(html: str, soup: BeautifulSoup | None = None) -> list[str]:
    """
    Return Instagram handles found on the page, ordered by how likely they are
    to be the brand's own account:
      1. Links inside <footer> or elements with class containing 'footer'/'social'
      2. All other <a href> links to instagram.com
      3. Any raw instagram.com URLs anywhere in the HTML
    """
    seen    = set()
    handles = []

    def _add(handle: str):
        h = handle.lower().strip(".")
        if h and h not in EXCLUDED_HANDLES and h not in seen:
            seen.add(h)
            handles.append(h)

    if soup:
        # Priority 1: footer / social-icon sections (most likely the brand's own link)
        for section in soup.select(
            "footer, [class*='footer'], [class*='social'], [id*='footer'], [id*='social']"
        ):
            for a in section.find_all("a", href=True):
                m = INSTAGRAM_URL_PATTERN.search(a["href"])
                if m:
                    _add(m.group(1))

        # Priority 2: remaining <a> tags
        for a in soup.find_all("a", href=True):
            m = INSTAGRAM_URL_PATTERN.search(a["href"])
            if m:
                _add(m.group(1))

    # Priority 3: raw scan of full HTML (catches JS-rendered or inline text)
    for m in INSTAGRAM_URL_PATTERN.finditer(html):
        _add(m.group(1))

    return handles


def analyze_website(url: str) -> dict:
    """
    Fetch the brand's website and return:
      platform, shopify_store, instagram_handles (list, priority-ordered)
    Tries with SSL verification first, falls back without if the cert is broken.
    """
    empty = {"platform": None, "shopify_store": None, "instagram_handles": []}

    def _fetch(verify_ssl: bool):
        return requests.get(
            url, headers=HEADERS, timeout=10,
            allow_redirects=True, verify=verify_ssl,
        )

    resp = None
    try:
        resp = _fetch(verify_ssl=True)
        resp.raise_for_status()
    except requests.exceptions.SSLError:
        try:
            resp = _fetch(verify_ssl=False)
            resp.raise_for_status()
        except requests.RequestException:
            return empty
    except requests.RequestException:
        return empty

    html      = resp.text
    final_url = resp.url
    soup      = BeautifulSoup(html, "html.parser")

    platform, shopify_store = _detect_platform(html, final_url)
    handles                  = _extract_instagram_handles_from_html(html, soup)
    return {
        "platform":          platform,
        "shopify_store":     shopify_store,
        "instagram_handles": handles,
    }


# ---------------------------------------------------------------------------
# Per-brand scrape
# ---------------------------------------------------------------------------

MAX_RETRIES   = 3
RETRY_BACKOFF = [5, 15, 45]


def _guess_instagram_handle(brand_name: str) -> list[str]:
    """
    Generate likely Instagram handle candidates from the brand name.
    Returns a list of handles to try, most specific first.
    """
    # Normalize: lowercase, only alphanumeric + dots/underscores
    base = re.sub(r"[^a-z0-9]", "", brand_name.lower())
    candidates = [base]
    # Common suffixes brands add to their handles
    for suffix in ("official", "india", "store", "skincare", "beauty", "hq"):
        candidates.append(base + suffix)
    return candidates


def _check_instagram_handle(handle: str) -> dict | None:
    """
    Verify a handle exists by checking the Instagram profile page.
    Returns a minimal result dict on success, None if the profile doesn't exist.
    """
    url = f"https://www.instagram.com/{handle}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
        # 404 = profile doesn't exist; anything else (200, 302) = likely exists
        if resp.status_code == 404:
            return None
        if resp.status_code == 200:
            # Quick follower extract from page source
            fm = FOLLOWERS_PATTERN.search(resp.text)
            return {
                "instagram_url": url,
                "followers":     fm.group(1) if fm else None,
                "snippet":       "",
            }
    except requests.RequestException:
        pass
    return None


def _build_ig_queries(brand_name: str, region: str) -> list[str]:
    """
    Return a prioritised list of search queries to try for finding
    a brand's Instagram profile.
    """
    r = f" {region}" if region else ""
    quoted = f'"{brand_name}"'
    return [
        f"{brand_name}{r} site:instagram.com",           # narrow site: search
        f"{brand_name}{r} instagram",                    # broad mention search
        f"{quoted} instagram official profile{r}",       # quoted + profile keyword
        f"{brand_name} instagram brand{r}",              # brand keyword
    ]


def scrape_brand(
    brand_name:  str,
    ddg_rl:      RateLimiter,
    goog_rl:     RateLimiter,
    region:      str,
    skip_website: bool,
) -> dict:
    base = {
        "brand":                      brand_name,
        "instagram_url":              None,
        "followers":                  None,
        "confidence":                 None,
        "confidence_reason":          None,
        "website_url":                None,
        "platform":                   None,
        "shopify_store":              None,
        "instagram_on_website":       None,
        "website_confirms_instagram": None,
        **{field: None for field, _ in MARKETPLACE_TARGETS},
    }

    # --- Step 1: find Instagram profile ---
    # Try multiple query strategies in order; stop at first hit
    ig_result = None
    for ig_query in _build_ig_queries(brand_name, region):
        if ig_result:
            break
        for attempt in range(MAX_RETRIES):
            result = _search_duckduckgo(ig_query, ddg_rl)
            if result and result.get("_blocked"):
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                continue
            if not result:
                result = _search_google(ig_query, goog_rl)
            if result and result.get("_blocked"):
                time.sleep(RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)])
                continue
            if result:
                ig_result = result
                break

    # --- Step 1b: direct handle guessing as last resort ---
    if not ig_result:
        for handle in _guess_instagram_handle(brand_name):
            if _is_valid_handle(handle):
                checked = _check_instagram_handle(handle)
                if checked:
                    ig_result = checked
                    break

    if not ig_result:
        return {**base, "status": "not_found"}

    conf_level, conf_reason = confidence_check(
        brand_name, ig_result["instagram_url"], ig_result.get("snippet", "")
    )
    row = {
        **base,
        "instagram_url":     ig_result["instagram_url"],
        "followers":         ig_result["followers"],
        "confidence":        conf_level,
        "confidence_reason": conf_reason,
        "status":            "ok" if ig_result["followers"] else "url_only",
    }

    if skip_website:
        return row

    # --- Step 2: find D2C website ---
    website_url = find_brand_website(brand_name, region, ddg_rl)
    if not website_url:
        return row

    row["website_url"] = website_url

    # --- Step 3: analyse the website ---
    site_info = analyze_website(website_url)
    row["platform"]      = site_info["platform"]
    row["shopify_store"] = site_info["shopify_store"]

    handles = site_info["instagram_handles"]
    if handles:
        row["instagram_on_website"] = handles[0]   # highest-priority handle on the page

        found_handle   = ig_result["instagram_url"].rstrip("/").split("/")[-1].lower()
        website_handle = handles[0].lower()

        if found_handle == website_handle:
            # Website confirms the Instagram we found — upgrade confidence to HIGH
            row["website_confirms_instagram"] = "yes"
            row["confidence"]        = "HIGH"
            row["confidence_reason"] = (conf_reason + "; confirmed by brand website").lstrip("; ")
        else:
            # Website links to a DIFFERENT Instagram — this is the strongest
            # signal that we have the wrong account. Switch to the website's handle.
            row["website_confirms_instagram"] = f"no (website says @{website_handle})"
            row["instagram_url"]     = f"https://www.instagram.com/{website_handle}/"
            row["confidence"]        = "HIGH"
            row["confidence_reason"] = f"overridden by brand website (@{website_handle})"
    else:
        row["website_confirms_instagram"] = "not_checked"

    # --- Step 4: find marketplace listing pages ---
    row.update(find_marketplace_urls(brand_name, ddg_rl))

    return row


# ---------------------------------------------------------------------------
# Input / output helpers
# ---------------------------------------------------------------------------

def read_brands(filepath: str, col: int) -> list[str]:
    brands = []
    ext    = os.path.splitext(filepath)[1].lower()
    with open(filepath, newline="", encoding="utf-8-sig") as fh:
        if ext in (".csv", ".tsv"):
            delimiter = "\t" if ext == ".tsv" else ","
            reader    = csv.reader(fh, delimiter=delimiter)
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


def load_existing_rows(output_path: str) -> dict[str, dict]:
    """
    Load existing CSV rows into a dict keyed by brand name.
    Preserves all data including manually-added values.
    """
    rows = {}
    if not os.path.exists(output_path):
        return rows
    with open(output_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = row.get("brand", "").strip()
            if name:
                rows[name] = dict(row)
    return rows


def _needs_rescrape(row: dict, skip_website: bool) -> bool:
    """
    Return True if an existing row is missing data that could be filled in.
    Brands with manually-added website_url are considered complete.
    """
    if not row.get("instagram_url"):
        return True
    if not skip_website and not row.get("website_url"):
        return True
    return False


def open_output_csv(output_path: str) -> tuple:
    """Open output CSV in write mode (fresh), writing the header."""
    fh     = open(output_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDS)
    writer.writeheader()
    return fh, writer


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Bulk Instagram + website scraper for brands.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python instagram_scraper.py brands.txt\n"
            "  python instagram_scraper.py brands.txt --region india\n"
            "  python instagram_scraper.py brands.txt --no-website\n"
            "  python instagram_scraper.py brands.csv --col 2 --output results.csv\n"
            "  python instagram_scraper.py brands.txt --workers 8 --delay 0.8\n"
            "\nnote: re-running the same command resumes from where it left off.\n"
            "\nconfidence guide:\n"
            "  HIGH   → handle closely matches brand name (almost certainly correct)\n"
            "  MEDIUM → partial match or brand name in bio (probably correct)\n"
            "  LOW    → no clear match (manual verification recommended)\n"
            "\nwebsite_confirms_instagram:\n"
            "  yes          → website links to the same Instagram we found\n"
            "  no (@handle) → website links to a DIFFERENT Instagram handle\n"
            "  not_checked  → website was found but no Instagram link on it"
        ),
    )
    parser.add_argument("input",
                        help="Input file (.txt, .csv, .tsv) with brand names")
    parser.add_argument("--output",     default="instagram_results.csv",
                        help="Output CSV (default: instagram_results.csv)")
    parser.add_argument("--col",        type=int, default=1,
                        help="Column number (1-based) for brand name in CSV (default: 1)")
    parser.add_argument("--workers",    type=int, default=5,
                        help="Parallel worker threads (default: 5; max: 10)")
    parser.add_argument("--delay",      type=float, default=1.0,
                        help="Min seconds between search-engine requests (default: 1.0)")
    parser.add_argument("--region",     default="",
                        help="Region keyword appended to searches, e.g. 'india'")
    parser.add_argument("--no-website", action="store_true",
                        help="Skip website discovery and analysis (faster)")
    args = parser.parse_args()

    if args.workers > 10:
        print(f"Warning: {args.workers} workers may trigger rate limiting.")

    print(f"Reading brands from : {args.input}")
    if args.region:
        print(f"Region filter       : {args.region}")
    if args.no_website:
        print("Website analysis    : disabled (--no-website)")

    all_brands = read_brands(args.input, args.col)
    if not all_brands:
        print("No brands found in input file.")
        sys.exit(1)
    print(f"Total brands        : {len(all_brands):,}")

    # Load all existing rows — preserves manually-added data
    existing_rows = load_existing_rows(args.output)

    # Only re-scrape brands that are new or have missing data
    brands_to_do = [
        b for b in all_brands
        if b not in existing_rows or _needs_rescrape(existing_rows[b], args.no_website)
    ]
    already_complete = len(all_brands) - len(brands_to_do)
    print(f"Already complete    : {already_complete:,}")
    print(f"To scrape / update  : {len(brands_to_do):,}")

    ddg_rate  = RateLimiter(args.delay)
    goog_rate = RateLimiter(args.delay)

    # In-memory store of final results (start with everything we already have)
    results_store: dict[str, dict] = dict(existing_rows)
    store_lock = threading.Lock()

    total     = len(brands_to_do)
    completed = 0
    errors    = 0
    start     = time.monotonic()
    conf_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

    if not brands_to_do:
        print("All brands already complete. Nothing to scrape.")
    else:
        print(f"\nStarting scrape with {args.workers} workers...\n")

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    scrape_brand, brand, ddg_rate, goog_rate, args.region, args.no_website
                ): brand
                for brand in brands_to_do
            }

            for future in as_completed(futures):
                result    = future.result()
                completed += 1

                if result["status"] == "not_found":
                    errors += 1
                if result.get("confidence"):
                    conf_counts[result["confidence"]] = (
                        conf_counts.get(result["confidence"], 0) + 1
                    )

                with store_lock:
                    brand_key = result["brand"]
                    if brand_key in results_store:
                        # Merge: new scraped values fill in empty fields;
                        # existing non-empty values (e.g. manually added) are kept.
                        merged = dict(results_store[brand_key])
                        for field, value in result.items():
                            if value and not merged.get(field):
                                merged[field] = value
                        # Always update instagram/confidence/status from fresh scrape
                        for field in ("instagram_url", "followers", "confidence",
                                      "confidence_reason", "status"):
                            if result.get(field):
                                merged[field] = result[field]
                        results_store[brand_key] = merged
                    else:
                        results_store[brand_key] = result

                elapsed  = time.monotonic() - start
                rate     = completed / elapsed if elapsed else 0
                eta_secs = (total - completed) / rate if rate else 0
                eta_str  = time.strftime("%H:%M:%S", time.gmtime(eta_secs))
                pct      = completed / total * 100

                conf     = result.get("confidence") or "-"
                conf_tag = {"HIGH": "[H]", "MEDIUM": "[M]", "LOW": "[L]"}.get(conf, "[ ]")
                s_icon   = "✓" if result["status"] == "ok" else (
                           "~" if result["status"] == "url_only" else "✗")

                platform   = result.get("platform") or ""
                shopify    = f" ({result['shopify_store']})" if result.get("shopify_store") else ""
                site       = f"  🌐 {result['website_url'][:35]} [{platform}{shopify}]" \
                             if result.get("website_url") else ""
                markets    = "  ".join(
                    f"{field.replace('_url','').upper()}✓"
                    for field, _ in MARKETPLACE_TARGETS
                    if result.get(field)
                )
                market_str = f"  [{markets}]" if markets else ""

                print(
                    f"[{pct:5.1f}%] {completed:>{len(str(total))}}/{total}  "
                    f"ETA {eta_str}  {s_icon}{conf_tag} {result['brand'][:28]}"
                    f"  →  {result['instagram_url'] or 'not found'}"
                    f"  {result['followers'] or ''}"
                    f"{site}{market_str}",
                    flush=True,
                )

    # Write the final merged CSV (all brands, in input order)
    out_fh, writer = open_output_csv(args.output)
    for brand in all_brands:
        row = results_store.get(brand)
        if row:
            # Ensure only known fields are written (handle extra cols from manual edits)
            writer.writerow({f: row.get(f, "") for f in OUTPUT_FIELDS})
    out_fh.close()

    elapsed = time.monotonic() - start
    found   = completed - errors
    print(f"\n{'─'*70}")
    print(f"Done in {elapsed/60:.1f} min  |  Output: {args.output}")
    print(f"  Found        : {found:,} / {total:,}")
    print(f"  Not found    : {errors:,}")
    print(f"  Confidence   →  HIGH: {conf_counts.get('HIGH',0):,}  "
          f"MEDIUM: {conf_counts.get('MEDIUM',0):,}  "
          f"LOW: {conf_counts.get('LOW',0):,}")
    print(f"\nTip: in Excel, filter 'confidence' = LOW or "
          f"'website_confirms_instagram' starts with 'no' to find mismatches.")


if __name__ == "__main__":
    main()
