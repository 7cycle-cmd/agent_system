@echo off
setlocal
title LLM Auto Start
cd /d "c:\projects\agent_system"

set "PY=c:\projects\agent_system\.venv\Scripts\python.exe"
set "PYW=c:\projects\agent_system\.venv\Scripts\pythonw.exe"
if exist "%PYW%" set "PY=%PYW%"
set "HELPER=c:\projects\agent_system\mouse_spot_helper.py"
set "WATCHDOG=c:\projects\agent_system\helper_watchdog.py"
set "HOME=http://127.0.0.1:18765/"
set "STATUS=http://127.0.0.1:18765/api/system-status"
set "URL=http://127.0.0.1:18765/llm-tasks"

if not exist "%PY%" (
  echo ERROR: Python venv not found: %PY%
  pause
  exit /b 1
)
if not exist "%HELPER%" (
  echo ERROR: Helper not found: %HELPER%
  pause
  exit /b 1
)
if not exist "%WATCHDOG%" (
  echo ERROR: Watchdog not found: %WATCHDOG%
  pause
  exit /b 1
)

rem Start keep-alive watchdog if not already running (owns helper lifecycle).
rem WindowStyle Hidden: no console flash. Login auto-start uses start_llm_bg_hidden.vbs instead.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pidFile='c:\projects\agent_system\helper_watchdog.pid'; $running=$false; if (Test-Path $pidFile) { try { $p=[int](Get-Content $pidFile -ErrorAction Stop).Trim(); if ($p -gt 0) { $proc=Get-Process -Id $p -ErrorAction SilentlyContinue; if ($proc) { $running=$true } } } catch {} }; if (-not $running) { $hit=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -like '*helper_watchdog.py*' } | Select-Object -First 1; if ($hit) { $running=$true } }; if ($running) { Write-Host 'helper_watchdog already running.'; exit 0 }; Write-Host 'Starting helper_watchdog...'; Start-Process -FilePath '%PY%' -ArgumentList '"%WATCHDOG%"' -WorkingDirectory 'c:\projects\agent_system' -WindowStyle Hidden; exit 0"

echo Waiting for LLM stack ready...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$statusUrl='%STATUS%'; $pageUrl='%URL%'; $msg='Helper starting...'; $ok=$false; $id=''; $name=''; $ol='Ollama ?'; for ($i=0; $i -lt 60; $i++) { try { $r = Invoke-RestMethod -Uri $statusUrl -TimeoutSec 2; if ($r.ok) { $ok=$true; $id = [string]$r.identity.computer_id; $name = [string]$r.identity.computer_name; if ($r.ollama.ok) { $ol = 'Ollama ON' } else { $ol = 'Ollama OFF' }; $model = [string]$r.ollama.model; $msg = \"$name | $id | Helper ON | $ol | $model\"; break } } catch {} ; Start-Sleep -Milliseconds 500 }; Start-Process $pageUrl; try { Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; $n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; $n.BalloonTipTitle = $(if ($ok) { 'LLM Ready' } else { 'LLM Starting' }); $n.BalloonTipText = $msg; $n.ShowBalloonTip(8000); Start-Sleep -Seconds 4; $n.Dispose() } catch { Write-Host $msg }; if (-not $ok) { exit 2 } else { exit 0 }"

set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo WARNING: Helper/Ollama may not be fully ready. Opened %URL% anyway.
) else (
  echo Ready. Opened %URL%
)
echo Keep-alive: helper_watchdog.py will restart the server if it goes down.
exit /b %EC%

