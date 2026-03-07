@echo off
:: ─────────────────────────────────────────────
::  Instagram Brand Scraper — One-click launcher
:: ─────────────────────────────────────────────

echo.
echo ================================================
echo    Instagram Brand Scraper — Starting up...
echo ================================================
echo.

:: Step 1: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Step 2: Install dependencies
echo Installing / checking dependencies...
python -m pip install -r requirements.txt -q
echo Done.
echo.

:: Step 3: Launch the app
echo Opening the app in your browser...
echo (Close this window to stop the app)
echo.
python -m streamlit run app.py --server.headless false

pause
