@echo off
REM JobPilot backend launcher (Windows). Everything is installed into the
REM project's own venv (backend\.venv); the system Python is never modified.
REM Base interpreter: backend\.python (portable, optional) or Python 3.11+ on PATH.
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
  if exist ".python\python.exe" (
    set "BASE_PY=.python\python.exe"
  ) else (
    where python >nul 2>nul || (
      echo [JobPilot] Python 3.11+ not found. Install it from https://www.python.org/downloads/
      pause
      exit /b 1
    )
    set "BASE_PY=python"
  )
  echo [JobPilot] Creating virtual environment...
  call %%BASE_PY%% -m venv .venv
  echo [JobPilot] Installing dependencies...
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo [JobPilot] Installing Playwright Chromium...
  .venv\Scripts\python.exe -m playwright install chromium
)

if not exist ".env" (
  echo [JobPilot] Creating .env from template - EDIT IT before real use.
  copy .env.example .env
)

echo [JobPilot] Starting API on http://127.0.0.1:8077  (restart after editing .env; profile edits apply automatically)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8077
