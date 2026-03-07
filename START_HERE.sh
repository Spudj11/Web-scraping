#!/bin/bash
# ─────────────────────────────────────────────
#  Brand Instagram Finder — Amazon Internal Tool
#  One-click launcher (opens as standalone app)
# ─────────────────────────────────────────────

echo ""
echo "================================================"
echo "   Brand Instagram Finder — Starting up..."
echo "================================================"
echo ""

# Step 1: Install dependencies
echo "▶ Installing / checking dependencies..."
python3 -m pip install -r requirements.txt -q 2>/dev/null || python -m pip install -r requirements.txt -q
echo "  Done."
echo ""

# Step 2: Start Streamlit in the background
echo "▶ Starting app server..."
python3 -m streamlit run app.py \
    --server.headless true \
    --server.port 8501 \
    --browser.gatherUsageStats false \
    > /tmp/scraper_app.log 2>&1 &

STREAMLIT_PID=$!

# Step 3: Wait for the server to be ready
echo "▶ Waiting for app to load..."
sleep 4

# Step 4: Open in Chromium as a standalone app window (no browser chrome/UI)
echo "▶ Opening app window..."

APP_FLAGS="--app=http://localhost:8501 --window-size=1400,900"

if command -v chromium-browser &> /dev/null; then
    chromium-browser $APP_FLAGS &
elif command -v chromium &> /dev/null; then
    chromium $APP_FLAGS &
elif command -v google-chrome &> /dev/null; then
    google-chrome $APP_FLAGS &
elif command -v google-chrome-stable &> /dev/null; then
    google-chrome-stable $APP_FLAGS &
elif [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS — try Chrome
    open -a "Google Chrome" --args $APP_FLAGS 2>/dev/null || open http://localhost:8501
else
    echo "  Chromium/Chrome not found — opening in default browser..."
    xdg-open http://localhost:8501 2>/dev/null || open http://localhost:8501
fi

echo ""
echo "  App is running (PID: $STREAMLIT_PID)"
echo "  Press CTRL+C to stop the server."
echo ""

# Keep running until user stops it
wait $STREAMLIT_PID
