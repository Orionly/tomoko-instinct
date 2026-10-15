@echo off
cd /d "%~dp0"
echo ========================================================
echo Starting Tomoko Macro Intelligence Preview (Port 8001)
echo Production code remains untouched.
echo Open your browser to: http://127.0.0.1:8001/preview
echo ========================================================
python scripts/run_preview_server.py
pause
