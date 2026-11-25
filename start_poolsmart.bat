@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  start "" "http://127.0.0.1:8000"
  ".venv\Scripts\python.exe" app.py --demo
) else (
  start "" "http://127.0.0.1:8000"
  python app.py --demo
)
