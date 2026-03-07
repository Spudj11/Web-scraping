"""
Streamlit web UI for the Instagram Brand Scraper.
Run with:  streamlit run app.py
"""

import io
import threading
import time
import queue

import streamlit as st
import pandas as pd

from instagram_scraper import (
    read_brands,
    RateLimiter,
    scrape_brand,
    OUTPUT_FIELDS,
)
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="Instagram Brand Scraper", page_icon="📸", layout="wide")

st.title("📸 Instagram Brand Scraper")
st.markdown("Upload a brand list, configure options, and download results as CSV.")

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")
    region = st.text_input("Region (optional)", placeholder="e.g. india")
    workers = st.slider("Parallel workers", min_value=1, max_value=10, value=3)
    delay = st.slider("Delay between requests (seconds)", min_value=0.5, max_value=5.0,
                      value=1.5, step=0.5)
    skip_website = st.checkbox("Skip website analysis (faster)", value=False)

# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Upload your brand list (.txt, .csv, or .tsv)",
    type=["txt", "csv", "tsv"],
)

brands = []
if uploaded_file is not None:
    # Save to a temp-like in-memory path so read_brands can parse it
    suffix = "." + uploaded_file.name.rsplit(".", 1)[-1].lower()
    tmp_path = f"/tmp/brands_upload{suffix}"
    with open(tmp_path, "wb") as f:
        f.write(uploaded_file.getvalue())

    col_num = 1
    if suffix in (".csv", ".tsv"):
        col_num = st.number_input("Which column contains brand names? (1-based)", min_value=1,
                                  max_value=20, value=1, step=1)

    brands = read_brands(tmp_path, int(col_num))
    st.success(f"Loaded **{len(brands):,}** brands from `{uploaded_file.name}`")
    with st.expander("Preview brands"):
        st.write(brands[:20])
        if len(brands) > 20:
            st.caption(f"… and {len(brands) - 20} more")

# ---------------------------------------------------------------------------
# Run scraper
# ---------------------------------------------------------------------------
if brands and st.button("🚀 Start Scraping", type="primary"):
    ddg_rl  = RateLimiter(delay)
    goog_rl = RateLimiter(delay)

    results   = []
    total     = len(brands)
    progress  = st.progress(0, text="Starting…")
    log_area  = st.empty()
    log_lines = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(scrape_brand, brand, ddg_rl, goog_rl, region, skip_website): brand
            for brand in brands
        }

        done = 0
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            done += 1

            pct   = done / total
            brand = result["brand"]
            ig    = result.get("instagram_url") or "not found"
            conf  = result.get("confidence") or "-"
            status_icon = "✓" if result["status"] == "ok" else (
                          "~" if result["status"] == "url_only" else "✗")

            log_lines.append(
                f"{status_icon} [{conf}]  **{brand}**  →  {ig}"
            )
            if len(log_lines) > 50:          # keep last 50 lines visible
                log_lines = log_lines[-50:]

            progress.progress(pct, text=f"Scraped {done}/{total} — {brand}")
            log_area.markdown("\n\n".join(log_lines))

    progress.progress(1.0, text="Done!")
    st.success(f"Finished! Scraped {total} brands.")

    # ---------------------------------------------------------------------------
    # Results table + download
    # ---------------------------------------------------------------------------
    df = pd.DataFrame(results, columns=OUTPUT_FIELDS)

    # Reorder to match input brand list
    brand_order = {b: i for i, b in enumerate(brands)}
    df["_order"] = df["brand"].map(brand_order)
    df = df.sort_values("_order").drop(columns="_order").reset_index(drop=True)

    st.subheader("Results")
    st.dataframe(df, use_container_width=True)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download results as CSV",
        data=csv_bytes,
        file_name="instagram_results.csv",
        mime="text/csv",
    )

    # Quick summary
    found = df[df["status"].isin(["ok", "url_only"])].shape[0]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total", total)
    col2.metric("Found", found)
    col3.metric("Not found", total - found)
    high = (df["confidence"] == "HIGH").sum()
    col4.metric("HIGH confidence", int(high))
