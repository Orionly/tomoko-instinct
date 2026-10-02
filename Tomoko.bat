@echo off
title TOMOKO BRAIN V4.4 Block 3
cd /d "%~dp0"
echo ======================================================
echo TOMOKO BRAIN V4.4 Block 3 - STARTUP
echo ======================================================
echo Syncing latest web_dashboard.html...
if exist ui\web_dashboard.html (
  if not exist backend\static mkdir backend\static
  copy /Y ui\web_dashboard.html backend\static\web_dashboard.html >nul
  copy /Y ui\web_dashboard.html web_dashboard.html >nul 2>nul
)
if exist ui\pair.html (
  if not exist backend\static mkdir backend\static
  copy /Y ui\pair.html backend\static\pair.html >nul
)
echo Starting VT Markets MT5 terminal...
start "" "C:\Program Files\VT Markets (Pty) MT5 Terminal\terminal64.exe"
timeout /t 5 /nobreak >nul
echo Starting EA Bridge on port 18001...
start "TOMOKO EA:18001" cmd /k python core\mt5_bridge.py --port 18001
timeout /t 3 /nobreak >nul
echo Starting Web Dashboard on port 8000...
start "TOMOKO WEB:8000" cmd /k uvicorn backend.api:app --host 127.0.0.1 --port 8000 --reload --log-level info
timeout /t 4 /nobreak >nul
echo Opening browser...
start http://localhost:8000/
echo ======================================================
echo TOMOKO Brain V4.4 running:
echo   EA Bridge: 127.0.0.1:18001
echo   Web Dashboard: http://127.0.0.1:8000
echo CSV: logs\brain_signals.csv
echo Close this window to keep running in background.
echo ======================================================
timeout /t 2 >nul
exit /b