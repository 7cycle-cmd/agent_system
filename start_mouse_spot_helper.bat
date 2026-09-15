@echo off
setlocal
title Mouse Spot Helper
cd /d "c:\projects\agent_system"

set "PY=c:\projects\agent_system\.venv\Scripts\python.exe"
set "APP=c:\projects\agent_system\mouse_spot_helper.py"
set "URL=http://127.0.0.1:18765/"

if not exist "%PY%" (
  echo ERROR: Python venv not found:
  echo   %PY%
  pause
  exit /b 1
)

if not exist "%APP%" (
  echo ERROR: App not found:
  echo   %APP%
  pause
  exit /b 1
)

rem If already running, just open the browser.
powershell -NoProfile -Command ^
  "try { $r = Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -ge 200) { Start-Process '%URL%'; exit 0 } } catch { exit 1 }"
if %ERRORLEVEL%==0 (
  echo Mouse Spot Helper is already running.
  echo Opened %URL%
  exit /b 0
)

echo Starting Mouse Spot Helper...
echo Keep this window open. Close it to stop the server.
echo URL: %URL%
echo.

"%PY%" "%APP%"
set "EC=%ERRORLEVEL%"
echo.
echo Server stopped. Exit code: %EC%
pause
exit /b %EC%
