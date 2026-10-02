@echo off
cd /d "%~dp0"
REM Use venv python if exists, else system python
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" main.py
) else (
  python main.py
)
pause
