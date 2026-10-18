@echo off
cd /d "%~dp0"

echo ========================================================
echo   TOMOKO V5.0 - AUTONOMOUS INTELLIGENCE TERMINAL
echo ========================================================

:: Check if TradingView is already running to avoid Error 32 singleton lock conflicts
tasklist | findstr /i TradingView.exe >nul
if "%ERRORLEVEL%"=="0" (
    echo [1/2] TradingView Desktop is already active.
) else (
    echo [1/2] Launching TradingView Desktop...
    start "" "tradingview:"
)

echo [2/2] Starting Tomoko Autonomous Brain & Dashboard...
python Main.py