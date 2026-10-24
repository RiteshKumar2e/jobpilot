@echo off
REM JobPilot dashboard launcher (Windows).
cd /d "%~dp0frontend"

if not exist "node_modules" (
  echo [JobPilot] Installing frontend dependencies...
  call npm install
)

echo [JobPilot] Starting dashboard on http://localhost:5173
call npm run dev
