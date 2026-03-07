@echo off
:: ─────────────────────────────────────────────
::  Instagram Brand Scraper — One-click launcher
:: ─────────────────────────────────────────────

echo.
echo ================================================
echo    Instagram Brand Scraper — Starting up...
echo ================================================
echo.

:: Step 1: Install dependencies
echo Installing / checking dependencies...
pip install -r requirements.txt -q
echo Done.
echo.

:: Step 2: Launch the app
echo Opening the app in your browser...
echo (Close this window to stop the app)
echo.
streamlit run app.py --server.headless false

pause
