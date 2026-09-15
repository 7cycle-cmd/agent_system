' Fully hidden keep-alive at login: start helper_watchdog with no window, no browser.
' Startup shortcut should target: wscript.exe "c:\projects\agent_system\start_llm_bg_hidden.vbs"
Option Explicit
Dim sh, fso, root, py, pyw, wd, pidFile, running, hit, cmd, p, proc, runner
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = "c:\projects\agent_system"
py = root & "\.venv\Scripts\python.exe"
pyw = root & "\.venv\Scripts\pythonw.exe"
wd = root & "\helper_watchdog.py"
pidFile = root & "\helper_watchdog.pid"

' Prefer pythonw (no console). Fall back to python.exe hidden.
If fso.FileExists(pyw) Then
  runner = pyw
ElseIf fso.FileExists(py) Then
  runner = py
Else
  WScript.Quit 1
End If
If Not fso.FileExists(wd) Then WScript.Quit 1

running = False
If fso.FileExists(pidFile) Then
  On Error Resume Next
  p = Trim(fso.OpenTextFile(pidFile, 1).ReadAll)
  If IsNumeric(p) Then
    If CLng(p) > 0 Then
      Set proc = GetObject("winmgmts:").ExecQuery( _
        "SELECT ProcessId FROM Win32_Process WHERE ProcessId=" & CLng(p))
      If proc.Count > 0 Then running = True
    End If
  End If
  On Error GoTo 0
End If

If Not running Then
  On Error Resume Next
  Set hit = GetObject("winmgmts:").ExecQuery( _
    "SELECT ProcessId FROM Win32_Process WHERE CommandLine LIKE '%helper_watchdog.py%'")
  If hit.Count > 0 Then running = True
  On Error GoTo 0
End If

If running Then WScript.Quit 0

' WindowStyle 0 = hidden (also used if runner is python.exe)
cmd = """" & runner & """ """ & wd & """"
sh.CurrentDirectory = root
sh.Run cmd, 0, False
WScript.Quit 0
