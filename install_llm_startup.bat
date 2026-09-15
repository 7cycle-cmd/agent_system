@echo off
setlocal
title Install LLM Hidden Auto Start
cd /d "c:\projects\agent_system"

set "TARGET=c:\projects\agent_system\start_llm_bg_hidden.vbs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LNK=%STARTUP%\LLM Auto Start.lnk"

if not exist "%TARGET%" (
  echo ERROR: Missing %TARGET%
  pause
  exit /b 1
)

if not exist "%STARTUP%" mkdir "%STARTUP%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%LNK%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%TARGET%\"'; $s.WorkingDirectory = 'c:\projects\agent_system'; $s.WindowStyle = 7; $s.Description = 'Hidden helper_watchdog keep-alive (no browser, no console)'; $s.Save(); Write-Output ('Installed: %LNK%'); Write-Output ('Target: wscript.exe %TARGET%')"

if errorlevel 1 (
  echo ERROR: Failed to create Startup shortcut.
  pause
  exit /b 1
)

echo.
echo Done. Hidden keep-alive will run at Windows login (no visible window, no browser).
echo Shortcut: %LNK%
echo Manual UI open: start_llm_auto.bat
echo.
exit /b 0
