@echo off
cd /d "%~dp0"

echo ========================================================
echo   TOMOKO V5.0 - AUTONOMOUS INTELLIGENCE TERMINAL
echo ========================================================

echo [1/2] Launching TradingView Desktop...
start "" "tradingview:"

echo [2/2] Starting Tomoko Autonomous Brain & Dashboard...
python Main.py