@echo off
REM JobPilot - start automatically when you log in to Windows.
REM   autostart_jobpilot.bat          -> turn ON  (adds a shortcut to your Startup folder)
REM   autostart_jobpilot.bat off      -> turn OFF (removes it)
REM Only your own Startup folder is touched; nothing else on the system changes.
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\JobPilot.lnk"

if /i "%~1"=="off" (
  del "%LNK%" >nul 2>nul
  echo [JobPilot] Autostart turned OFF.
  timeout /t 3 >nul
  exit /b 0
)

powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);" ^
  "$s.TargetPath = '%~dp0start_jobpilot.bat'; $s.Arguments = 'silent';" ^
  "$s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save()"
echo [JobPilot] Autostart turned ON - JobPilot will start (minimized) every time you log in.
echo            To undo: autostart_jobpilot.bat off
timeout /t 5 >nul
