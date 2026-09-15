@echo off
setlocal
title Helper Watchdog
cd /d "c:\projects\agent_system"

set "PY=c:\projects\agent_system\.venv\Scripts\python.exe"
set "WATCHDOG=c:\projects\agent_system\helper_watchdog.py"

if not exist "%PY%" (
  echo ERROR: Python venv not found: %PY%
  pause
  exit /b 1
)
if not exist "%WATCHDOG%" (
  echo ERROR: Watchdog not found: %WATCHDOG%
  pause
  exit /b 1
)

echo Starting helper_watchdog (keep Mouse Spot Helper alive on :18765)...
echo Close this window to stop the watchdog (helper may keep running).
echo.
"%PY%" "%WATCHDOG%"
set "EC=%ERRORLEVEL%"
echo.
echo Watchdog stopped. Exit code: %EC%
pause
exit /b %EC%
