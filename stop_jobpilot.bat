@echo off
REM JobPilot - stop the backend and dashboard started by start_jobpilot.bat.
echo [JobPilot] Stopping...
REM The launcher windows and everything they started (uvicorn, vite, browser).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | Where-Object { $_.CommandLine -match 'run_backend\.bat|run_frontend\.bat' } | ForEach-Object { taskkill /pid $_.ProcessId /t /f | Out-Null }"
REM Anything still listening on the JobPilot ports (e.g. started by hand).
powershell -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort 8000,5173 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
echo [JobPilot] Stopped.
timeout /t 3 >nul
