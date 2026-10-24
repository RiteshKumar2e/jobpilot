@echo off
REM JobPilot backend launcher (Windows). Uses ONLY the project's own Python
REM (backend\.python) and venv (backend\.venv); the system Python is never touched.
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
  if not exist ".python\python.exe" (
    echo [JobPilot] backend\.python is missing - see docs\SETUP_WINDOWS.md
    pause
    exit /b 1
  )
  echo [JobPilot] Creating virtual environment...
  .python\python.exe -m venv .venv
  echo [JobPilot] Installing dependencies...
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo [JobPilot] Installing Playwright Chromium...
  .venv\Scripts\python.exe -m playwright install chromium
)

if not exist ".env" (
  echo [JobPilot] Creating .env from template - EDIT IT before real use.
  copy .env.example .env
)

echo [JobPilot] Starting API on http://127.0.0.1:8000  (restart after editing .env; profile edits apply automatically)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
