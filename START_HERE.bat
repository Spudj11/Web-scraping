@echo off
:: ─────────────────────────────────────────────
::  Brand Instagram Finder — Amazon Internal Tool
::  One-click launcher (opens as standalone app)
:: ─────────────────────────────────────────────

echo.
echo ================================================
echo    Brand Instagram Finder — Starting up...
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

:: Step 3: Start Streamlit in the background
echo Starting app server...
start /B python -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false >nul 2>&1

:: Step 4: Wait for the server to be ready
echo Waiting for app to load...
timeout /t 5 /nobreak >nul

:: Step 5: Open in Chromium/Chrome as a standalone app window (no browser UI)
echo Opening app window...

:: Try Chrome first, then Edge (both support --app mode)
set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
set CHROME86="C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
set EDGE="C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
set CHROMIUM="C:\Program Files\Chromium\Application\chrome.exe"

if exist %CHROME% (
    start "" %CHROME% --app=http://localhost:8501 --window-size=1400,900 --window-position=100,50
) else if exist %CHROME86% (
    start "" %CHROME86% --app=http://localhost:8501 --window-size=1400,900 --window-position=100,50
) else if exist %EDGE% (
    start "" %EDGE% --app=http://localhost:8501 --window-size=1400,900 --window-position=100,50
) else if exist %CHROMIUM% (
    start "" %CHROMIUM% --app=http://localhost:8501 --window-size=1400,900 --window-position=100,50
) else (
    echo Could not find Chrome/Edge. Opening in default browser instead...
    start http://localhost:8501
)

echo.
echo App is running. Close this window to stop the server.
echo.
pause
