---
task_id: SKILL.DEEPSEEK.COPY
display_task_id: SKILL.DEEPSEEK.COPY
name: deepseek_copy
final_verdict: complete
ingested: no
modified_files:
  - deepseek_strip.ahk
  - f9_cdp_copy.py
  - f9_cdp_probe.py
  - start_chrome_cdp.bat
  - f9_find_copy.py
  - clipboard_strip_last.py
qc_summary: "One-key F9: CDP grabs latest DeepSeek assistant message text -> strip trailing offer line -> clipboard -> paste into VS Code. Self-proven end-to-end (seq change + content verified)."
reason: "Free the user's hands: F9 hotkey uses Chrome DevTools Protocol (port 9222) to read the latest .ds-message assistant text directly from the DOM, strips the trailing '需要我幫你...？講聲，即刻寫...' offer line, writes to clipboard via ctypes SetClipboardData (persistent), and pastes into VS Code. No coords, no hover, no scroll dependency."
artifacts:
  - deepseek_copy.skill.md
schema: result_yes_no
---
# Skill: deepseek_copy

Package path: `skills/3_ui/deepseek_copy/`

## Purpose

User 嘅日常工作：由 DeepSeek chat 複製最新 AI 回覆 → 貼上 VS Code chat，但要手動刪走最後一行（PM 常加嘅「需要我幫你草擬...？講聲，即刻寫。」）。呢個 skill 用 F9 一鍵搞掂，解放雙手。

## Hotkeys (deepseek_strip.ahk, AutoHotkey v2)

| Hotkey | 作用 |
|--------|------|
| **F9** | 一鍵（CDP 路徑）：`f9_cdp_copy.py` 經 CDP 攞最新 assistant 訊息文字 → strip 尾行 → 寫剪貼簿 → 驗證 seq → 貼上 VS Code |
| **F10** | （舊路徑，備用）重新偵測 Copy 按鈕座標 → 寫入 `f9_coords.txt` |
| **F11** | 顯示 / 隱藏右下角 floating 座標小視窗（舊路徑用） |
| **F12** | 將而家 mouse 位置寫入 `f9_coords.txt`（舊路徑用） |
| **Ctrl+Shift+V** | 清理剪貼簿最後一行 + 貼上（唔 click，純清理） |

## CDP 路徑（現行 F9，2026-09-18 起）

**點解棄用座標 click**：DeepSeek Copy 按鈕係 hover-triggered + 位置隨 scroll 變，固定座標幾秒就失效（F9 自證多次 FAIL: clipboard unchanged）。CDP 直接讀 DOM，完全唔使座標。

**前提**：CDP Chrome 實例開緊（`start_chrome_cdp.bat`）：
```
chrome.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="C:\projects\agent_system\chrome_cdp_profile" <share_url>
```
- **Chrome 136+ 限制**：default user-data-dir 會拒絕開 debug port → 必須用獨立 `--user-data-dir`
- **Chrome 153 限制**：WebSocket handshake 403 → 必須加 `--remote-allow-origins=*`
- Share link（`/a/chat/s/...`）**唔使登入**，新 profile 直接載入完整對話（已驗證）

**DOM 結構**（已驗證）：訊息元素 class = `.ds-message`；assistant 訊息含 `.ds-assistant-message-main-content`；最新 = 最後一個 assistant `.ds-message` 嘅 `innerText`。

**`f9_cdp_copy.py` 流程**：
1. `http://127.0.0.1:9222/json` 搵 DeepSeek tab → `webSocketDebuggerUrl`
2. `websocket-client` 連入 → `Runtime.evaluate` 攞最新 assistant `.ds-message` innerText
3. Strip 尾行：regex `\n*\s*需要我幫你[^\n？?]*[？?]\s*講聲[^\n]*\s*$`
4. 寫剪貼簿：**ctypes `SetClipboardData(CF_UNICODETEXT)` + `GlobalAlloc(GMEM_MOVEABLE)`**（持久，唔需要 live owner）
5. 退出碼：0 ok / 1 CDP 唔通 / 2 冇 DeepSeek tab / 3 冇 assistant 訊息

**ctypes clipboard 陷阱（全部踩過）**：
- tkinter `clipboard_append` + `destroy()` 之後內容會失（Tk 係 clipboard owner，destroy 即釋放）→ 用 ctypes
- 所有 kernel32/user32 pointer 參數都要 set `argtypes`/`restype`（`GlobalAlloc`/`GlobalLock`/`GlobalUnlock`/`GlobalFree`/`SetClipboardData`），否則 64-bit pointer 截斷 → access violation / OverflowError
- `wintypes` 冇 `SIZE_T`，用 `ctypes.c_size_t`
- 驗證要開**獨立新 Tk 實例**讀（舊 Tk 實例會 cache 舊值）

## F9 Flow (CDP, step-by-step)

1. `ClipSeq()` 記錄 before
2. `RunWait(python f9_cdp_copy.py)`（攞文字 + strip + 寫剪貼簿，~1 秒）
3. **驗證**：`GetClipboardSequenceNumber` 有冇變，冇變 → log FAIL（CDP Chrome 未開）
4. `WinActivate("ahk_exe Code.exe")` → `SendInput("^v")`

## Floating 座標視窗 (F11/F12)

- 右下角常駐 160x36 小視窗（`+AlwaysOnTop -Caption +ToolWindow`），實時顯示 mouse X,Y（50ms SetTimer）
- **可靠流程**：hover 住 DeepSeek copy 按鈕 → 睇小視窗 X,Y → 按 F12 保存 → F9 用該座標
- 原因：copy 按鈕係 **hover-triggered**（action bar 只有 hover 先出現），programmatic click 唔穩定；F12 手動 capture 係最可靠路徑
- AHK v2 Gui API（source-verified，唔好猜）：`Gui.SetFont("s12 cLime", "Consolas")`（method 唔係 property）、`Gui.Add("Text", "x10 y8 w140 h24", ...)`（v2 冇 `AddLabel`/`Label` type）、size 用 `Show("... W160 H36 X.. Y..")`（冇 `Size`/`SetSize` method）、`MouseGetPos(&x, &y)`（VarRef，唔係 `MouseGetPos(1)`）、SetTimer 多行用 named function（arrow function 多行會 parse error）

## F9 Flow (舊座標路徑 — DEPRECATED，只留作參考)

> 已棄用：Copy 按鈕 hover-triggered + 位置隨 scroll 變，固定座標幾秒就失效。CDP 路徑取代。

## F10 Flow (dynamic coord detection)

1. 激活 Chrome + scroll 最底（同 F9 step 1-2）
2. `f9_find_copy.py`：
   - OpenClaw MCP `screen.snapshot`（http://127.0.0.1:8765/，token 喺 `.env` OPENCLAW_MCP_TOKEN）→ 全屏 PNG
   - Ollama `qwen2.5vl:7b`（http://127.0.0.1:18803/v1/chat/completions，OpenAI-compatible）→ 0-1000 正規化座標
   - 換算真實螢幕 X,Y（pyautogui.size()）→ 寫 `f9_coords.txt`
   - 任何失敗 → fallback `523 827`
3. Vision prompt 關鍵：「latest AI reply = 最底嗰條（input box 上面），忽略上面舊訊息嘅 copy 按鈕」

## Evidence / Logging

- `f9_log.txt`：每次 F9/F10 都有 timestamped 記錄（seq before/after、用咗咩座標、OK/FAIL）
- 驗證方式 = **結果驗證**（clipboard seq 有冇變），唔係位置驗證

## Dependencies

- AutoHotkey v2：`C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe`
- Python venv：`C:\projects\agent_system\.venv\Scripts\python.exe`
- **CDP Chrome 實例**（F9 必需）：`start_chrome_cdp.bat` → port 9222，獨立 profile `chrome_cdp_profile`
- Python 套件：`websocket-client`（CDP WebSocket）
- OpenClaw MCP（F10 only，舊路徑）：127.0.0.1:8765
- Ollama（F10 only，舊路徑）：127.0.0.1:18803，model qwen2.5vl:7b
- F9（CDP 路徑）完全唔需要 OpenClaw/Ollama/座標

## AHK Restart Pattern (改咗 .ahk 之後)

```powershell
Get-Process AutoHotkey64 -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Start-Process "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" -ArgumentList "C:\projects\agent_system\deepseek_strip.ahk"
```

## Known Failure Modes & Handling

| 症狀 | 原因 | 處理 |
|------|------|------|
| F9 FAIL: CDP 攞唔到（CDP 路徑） | CDP Chrome 實例未開 / port 9222 冇 bind | 跑 `start_chrome_cdp.bat`；確認 `http://127.0.0.1:9222/json/version` 有回應 |
| CDP WebSocket 403 Forbidden | Chrome 153 要求 `--remote-allow-origins` | bat 加 `--remote-allow-origins=*` 重開 CDP Chrome |
| CDP port 9222 拒絕連線（有 flag 都係） | Chrome 136+ 禁止 default user-data-dir 開 debug port | 用獨立 `--user-data-dir`（chrome_cdp_profile） |
| 剪貼簿 seq 有變但讀返舊值 | tkinter clipboard owner 已 destroy / 舊 Tk 實例 cache | 用 ctypes SetClipboardData；驗證開獨立新 Tk 實例 |
| ctypes access violation / OverflowError | kernel32/user32 pointer 參數冇 set argtypes/restype | 全部 set（見 f9_cdp_copy.py set_clipboard） |
| F9 FAIL: 剪貼簿冇變（舊座標路徑） | 座標失準（Chrome resize / scroll / 解析度變） | 已棄用；CDP 路徑冇呢個問題 |
| F10 偵測到舊訊息 copy 按鈕 | 截圖時未 scroll 最底 | 已修：F10 先 Click(640,400)+{End} 先截圖 |
| Vision 400 Bad Request | Ollama endpoint 係 OpenAI-compatible（/v1/chat/completions），唔係 native /api/chat | 已修：用 /v1 格式 |
| AHK "Missing """ 錯誤 | string 拼接引號唔配對 | 檢查 Tooltip/F9Log 行嘅引號數 |
| AHK "Function calls require a space or (''" | v2 語法：function call 要加括號 | `RunWait("...")` 唔係 `RunWait "..."` |
| F9 貼上咗 Chrome 唔係 VS Code | 貼上前冇激活 VS Code | 已修：`WinActivate("ahk_exe Code.exe")` 先至 `SendInput("^v")` |
| 多一個 `return` 喺 hotkey block 之間 | v2 頂層 return 會結束 script，後面 hotkey 唔註冊 | 移除頂層 return |
| "Clipboard never assigned" analyzer 警告 | AHK 內建變數 false positive | 包入 function 用 `A_Clipboard`；警告可忽略 |
| {End} 冇 scroll | focus 喺 input box 唔係 message list | 已修：先 Click(640,400) 再 {End} |
| F9 冇反應（hang） | `ControlFocus("", "", "ahk_exe chrome.exe")` 拋 "Target window not found" error dialog，script 停咗 | 已修：移除 ControlFocus，靠 WinActivate + {End} |
| 激活咗錯 Chrome 視窗 | Chrome 有兩個視窗（LLM Task Monitor + DeepSeek），`AppActivate('chrome.exe')` 匹配 title 唔係 exe | 已修：ctypes EnumWindows + title 含 "DeepSeek" 過濾 + QueryFullProcessImageNameW 確認 exe |
| Copy 按鈕 click 唔到 | action bar 係 hover-triggered，mouse 移過去 click 會失去 hover | 用 F12 手動 capture 座標（hover 住時按）；programmatic hover+click 唔穩定 |
| Vision 偵測座標失準 | qwen2.5vl:7b 太弱（偵測到 800,600 實際 727,767） | F12 手動 capture 優先；vision 只係 fallback |
| OpenClaw browser.proxy 用唔到 | "Browser control authentication was blocked because the local listener owner could not be verified" | 用 screen.snapshot 代替 CDP；CDP 路徑封咗 |
| SetForegroundWindow 失敗 | 背景 process 激活視窗被 Windows 封鎖（return False） | Chrome 已係 foreground 時 retry；或 taskbar click fallback |

## Cases Studied (user-reported)

1. **Scroll up 隱藏咗 copy 按鈕** → vision 會搵到舊訊息按鈕。Fix：F9/F10 都先 scroll 最底。
2. **Chrome 唔喺前台** → WinActivate + taskbar icon click fallback。
3. **貼上錯視窗** → 貼上前 WinActivate VS Code。
4. **座碼硬編碼失準** → F10 動態偵測 + f9_coords.txt + fallback。
5. **F9 冇反應** → ControlFocus 拋 error dialog 令 script hang。Fix：移除 ControlFocus。
6. **Copy 按鈕 hover-triggered** → programmatic click 唔穩定。Fix：F11/F12 floating 座標視窗手動 capture。
7. **AHK v2 Gui API 唔同 v1** → AddLabel/Font property/SetSize/MouseGetPos(1) 全部錯。Fix：source-verified API（見 Floating 座標視窗 section）。

## TODO / Future Cases (user 可以繼續畀 case)

- [ ] 多 display / 解析度變 → F10 重新偵測（screen_size 已處理換算）
- [ ] Chrome 最小化 → taskbar fallback 已處理，待實測
- [ ] DeepSeek 頁面 layout 改動（copy 按鈕位置/圖示變）→ 更新 vision prompt
- [ ] 一次 copy 多條回覆（而家只係最新一條）
