#!/bin/bash
# ─────────────────────────────────────────────
#  Instagram Brand Scraper — One-click launcher
# ─────────────────────────────────────────────

echo ""
echo "================================================"
echo "   Instagram Brand Scraper — Starting up..."
echo "================================================"
echo ""

# Step 1: Install dependencies (silently, only if needed)
echo "▶ Installing / checking dependencies..."
pip install -r requirements.txt -q
echo "  Done."
echo ""

# Step 2: Launch the app
echo "▶ Opening the app in your browser..."
echo "  (Press CTRL+C at any time to stop)"
echo ""
streamlit run app.py --server.headless false
