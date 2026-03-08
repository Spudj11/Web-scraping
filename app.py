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

# ── Amazon corporate branding ────────────────────────────────────────────────
st.markdown("""
<style>
/* ---- Base palette ---- */
:root {
    --amz-orange:  #FF9900;
    --amz-navy:    #232F3E;
    --amz-blue:    #146EB4;
    --amz-bg:      #F7F8F8;
    --amz-text:    #0F1111;
    --amz-border:  #D5D9D9;
}

/* Page background */
.stApp { background-color: var(--amz-bg); }

/* ---- Top navbar ---- */
[data-testid="stHeader"] { background-color: var(--amz-navy) !important; }

/* ---- Sidebar ---- */
[data-testid="stSidebar"] {
    background-color: var(--amz-navy) !important;
    border-right: 3px solid var(--amz-orange);
}
[data-testid="stSidebar"] * { color: #FFFFFF !important; }
[data-testid="stSidebar"] .stSlider > div > div > div { background: var(--amz-orange) !important; }

/* ---- Buttons ---- */
.stButton > button[kind="primary"] {
    background-color: var(--amz-orange) !important;
    color: var(--amz-navy) !important;
    border: none !important;
    font-weight: 700 !important;
    border-radius: 4px !important;
    padding: 0.5rem 2rem !important;
    font-size: 1rem !important;
}
.stButton > button[kind="primary"]:hover {
    background-color: #e68a00 !important;
}

/* ---- Download button ---- */
.stDownloadButton > button {
    background-color: var(--amz-blue) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 4px !important;
    font-weight: 600 !important;
}

/* ---- Progress bar ---- */
.stProgress > div > div > div { background-color: var(--amz-orange) !important; }

/* ---- Metrics ---- */
[data-testid="stMetricValue"] { color: var(--amz-navy) !important; font-weight: 700; }

/* ---- File uploader ---- */
[data-testid="stFileUploaderDropzone"] {
    border: 2px dashed var(--amz-orange) !important;
    border-radius: 6px !important;
    background: #fff !important;
}

/* ---- Scrollbar ---- */
::-webkit-scrollbar-thumb { background: var(--amz-orange); border-radius: 4px; }
::-webkit-scrollbar { width: 8px; }
</style>

<!-- Amazon-style header bar -->
<div style="
    background: #232F3E;
    padding: 10px 24px;
    margin: -1rem -1rem 1.5rem -1rem;
    display: flex;
    align-items: center;
    gap: 16px;
    border-bottom: 3px solid #FF9900;
">
    <span style="font-size:2rem;">🔍</span>
    <div>
        <div style="color:#FF9900; font-size:1.4rem; font-weight:700; font-family:'Amazon Ember',Arial,sans-serif; letter-spacing:0.5px;">
            Brand Instagram Finder
        </div>
        <div style="color:#ccc; font-size:0.8rem; font-family:Arial,sans-serif;">
            Amazon Internal Tool &nbsp;|&nbsp; Seller & Brand Intelligence
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")
    region = st.text_input("Region (optional)", placeholder="e.g. india")
    workers = st.slider("Parallel workers", min_value=1, max_value=10, value=3)
    delay = st.slider("Delay between requests (seconds)", min_value=0.5, max_value=5.0,
                      value=2.5, step=0.5)
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
    st.info(f"Using **{len(brands)}** brands total ({len(file_brands)} from file + {len(paste_brands)} pasted, duplicates removed).")

# ---------------------------------------------------------------------------
# Live results table helper
# ---------------------------------------------------------------------------
_CONF_BADGE = {"HIGH": "🟢 HIGH", "MEDIUM": "🟡 MEDIUM", "LOW": "🔴 LOW"}
_LIVE_COLS  = ["Brand", "Status", "Confidence", "Instagram URL", "Followers",
               "Website", "Amazon.in", "Nykaa"]


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
            "Brand":       r["brand"],
            "Status":      icon,
            "Confidence":  _CONF_BADGE.get(r.get("confidence") or "", r.get("confidence") or "—"),
            "Instagram URL": r.get("instagram_url") or "",
            "Followers":   r.get("followers") or "",
            "Website":     r.get("website_url") or "",
            "Amazon.in":   r.get("amazon_in_url") or "",
            "Nykaa":       r.get("nykaa_url") or "",
        })
    return pd.DataFrame(rows, columns=_LIVE_COLS)


# ---------------------------------------------------------------------------
# Run scraper
# ---------------------------------------------------------------------------
if brands and st.button("🚀 Start Scraping", type="primary"):
    ddg_rl  = RateLimiter(delay)
    goog_rl = RateLimiter(delay)

    results        = []
    total          = len(brands)
    network_errors = 0

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

            # Update live table — ordered to match input brand list
            brand_order = {b: i for i, b in enumerate(brands)}
            sorted_results = sorted(results, key=lambda r: brand_order.get(r["brand"], 9999))
            live_table.dataframe(
                _build_live_df(sorted_results),
                use_container_width=True,
                column_config={
                    "Instagram URL": st.column_config.LinkColumn("Instagram URL"),
                    "Website":       st.column_config.LinkColumn("Website"),
                    "Amazon.in":     st.column_config.LinkColumn("Amazon.in"),
                    "Nykaa":         st.column_config.LinkColumn("Nykaa"),
                },
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
        st.success(f"Search Complete! Scraped {total} brands.")

    # ---------------------------------------------------------------------------
    # Summary metrics
    # ---------------------------------------------------------------------------
    found    = sum(1 for r in results if r.get("status") in ("ok", "url_only"))
    high     = sum(1 for r in results if r.get("confidence") == "HIGH")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total",          total)
    c2.metric("Found",          found)
    c3.metric("Not found",      total - found - network_errors)
    c4.metric("Network errors", network_errors)
    c5.metric("HIGH confidence", high)

    # ---------------------------------------------------------------------------
    # Full results download
    # ---------------------------------------------------------------------------
    st.markdown("#### Full Results (all columns)")
    df_full = pd.DataFrame(results, columns=OUTPUT_FIELDS)
    brand_order = {b: i for i, b in enumerate(brands)}
    df_full["_order"] = df_full["brand"].map(brand_order)
    df_full = df_full.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    st.dataframe(df_full, use_container_width=True)

    csv_bytes = df_full.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download full results as CSV",
        data=csv_bytes,
        file_name="instagram_results.csv",
        mime="text/csv",
    )
