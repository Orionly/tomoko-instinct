@echo off
cd /d "%~dp0"

echo ========================================================
echo   TOMOKO V5.0 - AUTONOMOUS INTELLIGENCE TERMINAL
echo ========================================================

REM 1. Auto-launch Primary MT5 (HFM) if not running
powershell -NoProfile -Command "if (!(Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*HFM*' })) { if (Test-Path 'C:\Program Files\HFM Metatrader 5\terminal64.exe') { Start-Process 'C:\Program Files\HFM Metatrader 5\terminal64.exe' -WorkingDirectory 'C:\Program Files\HFM Metatrader 5' } }" >nul 2>&1
echo [1/4] Primary MT5 (HFM) checked.

REM 2. Auto-launch Secondary MT5 (VT Markets) if not running
powershell -NoProfile -Command "if (!(Get-Process terminal64 -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*VT*' })) { if (Test-Path 'C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe') { Start-Process 'C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe' -WorkingDirectory 'C:\Program Files\VT Markets (Pty) MT5 Terminal' } }" >nul 2>&1
echo [2/4] Secondary MT5 (VT Markets) checked.

REM 3. Auto-launch TradingView Desktop if not running
tasklist | findstr /i TradingView.exe >nul
if "%ERRORLEVEL%"=="0" (
    echo [3/4] TradingView Desktop is already active.
) else (
    echo [3/4] Launching TradingView Desktop...
    start "" "tradingview:"
)

echo [4/4] Starting Tomoko Autonomous Brain and Dashboard...
python Main.py
