"""
Streamlit web UI for the Instagram Brand Scraper.
Run with:  streamlit run app.py
"""

import tempfile
import time

import streamlit as st
import pandas as pd

from instagram_scraper import (
    read_brands,
    RateLimiter,
    scrape_brand,
    OUTPUT_FIELDS,
)
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

st.set_page_config(page_title="Instagram Brand Finder", page_icon="🔍", layout="wide")

# ── Branding + CSS ────────────────────────────────────────────────────────────
st.markdown("""
<style>
:root {
    --amz-orange: #FF9900;
    --amz-navy:   #232F3E;
    --amz-blue:   #146EB4;
    --amz-bg:     #F7F8F8;
}
.stApp { background-color: var(--amz-bg); }
[data-testid="stHeader"] { background-color: var(--amz-navy) !important; }

[data-testid="stSidebar"] {
    background-color: var(--amz-navy) !important;
    border-right: 3px solid var(--amz-orange);
}
[data-testid="stSidebar"] * { color: #FFFFFF !important; }
[data-testid="stSidebar"] .stSlider > div > div > div { background: var(--amz-orange) !important; }

.stButton > button[kind="primary"] {
    background-color: var(--amz-orange) !important;
    color: var(--amz-navy) !important;
    border: none !important;
    font-weight: 700 !important;
    border-radius: 4px !important;
    padding: 0.5rem 2rem !important;
    font-size: 1rem !important;
}
.stButton > button[kind="primary"]:hover { background-color: #e68a00 !important; }

.stDownloadButton > button {
    background-color: var(--amz-blue) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 4px !important;
    font-weight: 600 !important;
}

.stProgress > div > div > div { background-color: var(--amz-orange) !important; }
[data-testid="stMetricValue"] { color: var(--amz-navy) !important; font-weight: 700; }

[data-testid="stFileUploaderDropzone"] {
    border: 2px dashed var(--amz-orange) !important;
    border-radius: 6px !important;
    background: #fff !important;
}

::-webkit-scrollbar-thumb { background: var(--amz-orange); border-radius: 4px; }
::-webkit-scrollbar { width: 8px; }
</style>

<!-- Header bar with Amazon logo -->
<div style="
    background: #232F3E;
    padding: 10px 24px;
    margin: -1rem -1rem 1.5rem -1rem;
    display: flex;
    align-items: center;
    gap: 20px;
    border-bottom: 3px solid #FF9900;
">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 52" width="140" height="52" style="flex-shrink:0">
        <text x="3" y="32" font-family="Arial Black,Arial,sans-serif"
              font-weight="900" font-size="30" fill="#FFFFFF" letter-spacing="-1">amazon</text>
        <path d="M12 43 C42 55 96 55 126 43"
              stroke="#FF9900" stroke-width="4.5" fill="none" stroke-linecap="round"/>
        <polygon points="122,39 131,44 122,48" fill="#FF9900"/>
    </svg>
    <span style="display:inline-block; width:2px; height:40px; background:#FF9900; opacity:0.5; flex-shrink:0;"></span>
    <span>
        <span style="display:block; color:#FF9900; font-size:1.35rem; font-weight:700; font-family:Arial,sans-serif; letter-spacing:0.4px;">Brand Instagram Finder</span>
        <span style="display:block; color:#aaa; font-size:0.78rem; font-family:Arial,sans-serif; margin-top:2px;">Amazon Internal Tool &nbsp;&middot;&nbsp; Seller &amp; Brand Intelligence</span>
    </span>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")
    region       = st.text_input("Region (optional)", placeholder="e.g. india")
    workers      = st.slider("Parallel workers",               min_value=1,   max_value=10,  value=3)
    delay        = st.slider("Delay between requests (sec)",   min_value=0.5, max_value=5.0, value=2.5, step=0.5)
    skip_website = st.checkbox("Skip website analysis (faster)", value=False)

# ---------------------------------------------------------------------------
# Brand input — Upload file  OR  Paste brand names
# ---------------------------------------------------------------------------
st.markdown("### Add Brands")
upload_tab, paste_tab = st.tabs(["📂 Upload File", "✏️ Paste Brand Names"])

file_brands  = []
paste_brands = []

with upload_tab:
    uploaded_file = st.file_uploader(
        "Upload a .txt, .csv, or .tsv brand list",
        type=["txt", "csv", "tsv"],
        label_visibility="collapsed",
    )
    if uploaded_file is not None:
        suffix = "." + uploaded_file.name.rsplit(".", 1)[-1].lower()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(uploaded_file.getvalue())
        tmp.close()

        col_num = 1
        if suffix in (".csv", ".tsv"):
            col_num = st.number_input(
                "Which column has brand names? (1-based)",
                min_value=1, max_value=20, value=1, step=1,
            )

        file_brands = read_brands(tmp.name, int(col_num))
        st.success(f"Loaded **{len(file_brands):,}** brands from `{uploaded_file.name}`")
        with st.expander("Preview"):
            st.write(file_brands[:20])
            if len(file_brands) > 20:
                st.caption(f"… and {len(file_brands) - 20} more")

with paste_tab:
    pasted_text = st.text_area(
        "One brand name per line",
        placeholder="mamaearth\nnykaa cosmetics\nboat lifestyle\nfoxtale\n...",
        height=200,
        label_visibility="collapsed",
    )
    if pasted_text.strip():
        paste_brands = [
            line.strip()
            for line in pasted_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if paste_brands:
            st.success(f"**{len(paste_brands)}** brands ready")

# Combine both sources, preserve order, deduplicate
brands = list(dict.fromkeys(file_brands + paste_brands))
if file_brands and paste_brands:
    st.info(
        f"Using **{len(brands)}** brands total "
        f"({len(file_brands)} from file + {len(paste_brands)} pasted, duplicates removed)."
    )

# ---------------------------------------------------------------------------
# Live results table helpers
# ---------------------------------------------------------------------------
_CONF_BADGE = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW"}

_LIVE_COLS = [
    "Brand", "Status", "Confidence", "Site Confirms",
    "Instagram URL", "Followers",
    "Website", "Amazon.in", "Nykaa",
]


def _site_confirms_label(r: dict) -> str:
    """Human-readable label for the website cross-check field."""
    val = r.get("website_confirms_instagram")
    if val == "yes":
        return "✅ Confirmed"
    if val and val.startswith("no"):
        # val looks like "no (website says @handle)"
        return f"⚠️ {val}"
    if val == "not_checked":
        return "🔍 No IG link on site"
    return "—"


def _build_live_df(results: list) -> pd.DataFrame:
    rows = []
    for r in results:
        status = r.get("status", "")
        if status == "network_error":
            icon = "⚠️ error"
        elif status == "ok":
            icon = "✅ found"
        elif status == "url_only":
            icon = "🔗 url only"
        else:
            icon = "❌ not found"

        rows.append({
            "Brand":         r["brand"],
            "Status":        icon,
            "Confidence":    _CONF_BADGE.get(r.get("confidence") or "", r.get("confidence") or "—"),
            "Site Confirms": _site_confirms_label(r),
            # Use None for missing URLs — LinkColumn only renders valid http:// strings;
            # empty strings "" cause blank non-clickable cells in Streamlit.
            "Instagram URL": r.get("instagram_url") or None,
            "Followers":     r.get("followers") or "—",
            "Website":       r.get("website_url") or None,
            "Amazon.in":     r.get("amazon_in_url") or None,
            "Nykaa":         r.get("nykaa_url") or None,
        })
    return pd.DataFrame(rows, columns=_LIVE_COLS)


_LINK_COLS = {
    "Instagram URL": st.column_config.LinkColumn("Instagram URL", display_text="Open ↗"),
    "Website":       st.column_config.LinkColumn("Website",       display_text="Open ↗"),
    "Amazon.in":     st.column_config.LinkColumn("Amazon.in",     display_text="Open ↗"),
    "Nykaa":         st.column_config.LinkColumn("Nykaa",         display_text="Open ↗"),
}

# ---------------------------------------------------------------------------
# Run scraper
# ---------------------------------------------------------------------------
if brands and st.button("🚀 Start Scraping", type="primary"):
    ddg_rl  = RateLimiter(delay)
    goog_rl = RateLimiter(delay)

    results        = []
    total          = len(brands)
    network_errors = 0
    brand_order    = {b: i for i, b in enumerate(brands)}

    progress     = st.progress(0, text="Starting…")
    net_err_area = st.empty()
    log_area     = st.empty()
    log_lines    = []

    st.markdown("#### Live Results")
    live_table = st.empty()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(scrape_brand, brand, ddg_rl, goog_rl, region, skip_website): brand
            for brand in brands
        }

        progress.progress(0, text=f"⏳ Submitted {total} brands — waiting for first result…")
        log_area.info(
            f"Scraping **{total}** brands with **{workers}** parallel workers. "
            f"Results appear as they complete."
        )

        start_time = time.time()
        done    = 0
        pending = set(futures.keys())

        while pending:
            done_set, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)

            elapsed     = int(time.time() - start_time)
            elapsed_str = f"{elapsed // 60}m {elapsed % 60}s" if elapsed >= 60 else f"{elapsed}s"

            if not done_set:
                progress.progress(
                    done / total,
                    text=f"⏳ {done}/{total} complete — {len(pending)} in progress — {elapsed_str} elapsed",
                )
                continue

            for future in done_set:
                result = future.result()
                results.append(result)
                done += 1

                brand_name = result["brand"]
                status     = result["status"]
                ig         = result.get("instagram_url") or "not found"
                conf       = result.get("confidence") or "-"

                if status == "network_error":
                    network_errors += 1
                    err_detail = result.get("confidence_reason", "unknown error")
                    log_lines.append(f"⚠️ **[NET ERROR]** {brand_name} → {err_detail}")
                    net_err_area.error(
                        f"**Network error** — {network_errors}/{done} brands failed.\n\n"
                        f"Cause: `{err_detail}`\n\n"
                        f"Check your internet connection or proxy settings."
                    )
                else:
                    icon = "✅" if status == "ok" else ("🔗" if status == "url_only" else "❌")
                    log_lines.append(f"{icon} **[{conf}]** {brand_name} → {ig}")

                if len(log_lines) > 60:
                    log_lines = log_lines[-60:]

            progress.progress(done / total, text=f"Scraped {done}/{total} — {elapsed_str} elapsed")
            log_area.markdown("\n\n".join(log_lines))

            # Rebuild live table in input order
            sorted_results = sorted(results, key=lambda r: brand_order.get(r["brand"], 9999))
            live_table.dataframe(
                _build_live_df(sorted_results),
                use_container_width=True,
                column_config=_LINK_COLS,
                hide_index=True,
            )

    progress.progress(1.0, text="Done!")

    if network_errors == total:
        st.error(
            f"**All {total} brands failed with network errors.** "
            f"The scraper cannot reach DuckDuckGo or Google from this machine."
        )
    elif network_errors > 0:
        st.warning(f"Finished with {network_errors} network errors out of {total} brands.")
    else:
        st.success("Search Complete!")

    # ── Summary metrics ──────────────────────────────────────────────────────
    found     = sum(1 for r in results if r.get("status") in ("ok", "url_only"))
    high      = sum(1 for r in results if r.get("confidence") == "HIGH")
    confirmed = sum(1 for r in results if r.get("website_confirms_instagram") == "yes")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total",           total)
    c2.metric("Found",           found)
    c3.metric("Not found",       total - found - network_errors)
    c4.metric("Network errors",  network_errors)
    c5.metric("HIGH confidence", high)
    c6.metric("Site confirmed",  confirmed)

    # ── Full results table + CSV download ────────────────────────────────────
    st.markdown("#### Full Results (all columns)")
    df_full = pd.DataFrame(results, columns=OUTPUT_FIELDS)
    df_full["_order"] = df_full["brand"].map(brand_order)
    df_full = df_full.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    st.dataframe(
        df_full,
        use_container_width=True,
        column_config={
            "instagram_url": st.column_config.LinkColumn("instagram_url", display_text="Open ↗"),
            "website_url":   st.column_config.LinkColumn("website_url",   display_text="Open ↗"),
            "amazon_in_url": st.column_config.LinkColumn("amazon_in_url", display_text="Open ↗"),
            "nykaa_url":     st.column_config.LinkColumn("nykaa_url",     display_text="Open ↗"),
            "flipkart_url":  st.column_config.LinkColumn("flipkart_url",  display_text="Open ↗"),
            "myntra_url":    st.column_config.LinkColumn("myntra_url",    display_text="Open ↗"),
        },
    )

    csv_bytes = df_full.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download full results as CSV",
        data=csv_bytes,
        file_name="instagram_results.csv",
        mime="text/csv",
    )
