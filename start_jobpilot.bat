@echo off
REM ==========================================================================
REM  JobPilot - one-click start.
REM  Starts the backend and the dashboard (minimized windows), waits until both
REM  are up, then opens the dashboard. The backend runs a full cycle on start-up
REM  (applies to queued jobs, discovers new ones, syncs Google Sheets) and then
REM  repeats on its schedule. Close everything with stop_jobpilot.bat.
REM
REM  start_jobpilot.bat silent   -> same, but don't open the browser
REM ==========================================================================
setlocal
cd /d "%~dp0"
title JobPilot launcher

curl.exe -s -o nul http://127.0.0.1:8000/api/health && (
  echo [JobPilot] Backend already running.
) || (
  echo [JobPilot] Starting backend...
  start "JobPilot Backend" /min cmd /k call "%~dp0run_backend.bat"
)

curl.exe -s -o nul http://localhost:5173 && (
  echo [JobPilot] Dashboard already running.
) || (
  echo [JobPilot] Starting dashboard...
  start "JobPilot Dashboard" /min cmd /k call "%~dp0run_frontend.bat"
)

REM First launch installs dependencies, so allow up to 10 minutes.
echo [JobPilot] Waiting for backend...
set /a tries=0
:wait_backend
curl.exe -s -o nul http://127.0.0.1:8000/api/health && goto backend_up
set /a tries+=1
if %tries% geq 300 goto failed_backend
timeout /t 2 /nobreak >nul
goto wait_backend
:backend_up
echo [JobPilot] Backend is up - automation is running.

echo [JobPilot] Waiting for dashboard...
set /a tries=0
:wait_frontend
curl.exe -s -o nul http://localhost:5173 && goto frontend_up
set /a tries+=1
if %tries% geq 300 goto failed_frontend
timeout /t 2 /nobreak >nul
goto wait_frontend
:frontend_up

if /i not "%~1"=="silent" start "" http://localhost:5173
echo [JobPilot] All set. Dashboard: http://localhost:5173
timeout /t 5 >nul
exit /b 0

:failed_backend
echo [JobPilot] Backend did not start. Open the "JobPilot Backend" window to see why.
pause
exit /b 1

:failed_frontend
echo [JobPilot] Dashboard did not start. Open the "JobPilot Dashboard" window to see why.
pause
exit /b 1
