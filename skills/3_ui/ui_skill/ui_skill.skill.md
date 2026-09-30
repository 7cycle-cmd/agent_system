---
task_id: SKILL.UI.DESIGN
name: ui_skill
description: 通用 UI 設計 skill — card→detail modal、status badge、empty state、hover hint、minimal click cost
catalog_id: 1
subcatalog_id: 1
ingested: "no"
artifacts:
  - ui_skill.skill.md
---

# ui_skill — 通用 UI 設計 skill

## 適用範圍

任何 SPA 面板（/llm-tasks 等）的 UI 設計與改動。原則：**盡量只改前端**，
唔好為 UI 改動加後端 endpoint（除非數據真係冇）。

## 核心模式

### 1. Card → Detail Modal（P0 交互）

- 卡片用 `<button type="button" data-xxx="{id}">`（唔好 div），`w-full text-left`。
- hover：`transition hover:ring-2 hover:ring-accent cursor-pointer`。
- 卡片底部加 hint：`<div class="mt-3 text-right text-[11px] font-medium text-accent">click 睇詳情 →</div>`。
- Modal 跟 `openTaskDetailPopup` 模式：
  - `fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4`
  - 內層 `w-full max-w-2xl rounded-2xl border border-line bg-panel p-5 shadow-panel`
  - 關閉：✕ button + backdrop click（`e.target === el`）+ Escape key listener（close 時 removeEventListener）。
  - 開前先 `document.getElementById(id)?.remove()` 防重疊 —— **只適用於命令式 DOM popup**（`openTaskDetailPopup`）。Vue `v-if` modal **唔准手動 remove**（見陷阱 2）。
  - Vue `v-if` modal 加 `:key="selected.id"`，令換 item 時乾淨重建。

### 2. Status badge 顏色語義

- ✅ 已交付 → green（`bg-green-100 text-green-700`）
- 🚧 進行中 → amber（`bg-amber-100 text-amber-700`）
- ❌ 失敗 → red

### 3. Empty state

- 表格空：`<tr><td colspan="N" class="px-3 py-4 text-center text-xs text-muted">無相關記錄。</td></tr>`
- 唔好顯示空白表格。

### 4. Minimal click cost

- 一 click 睇到全部詳情（modal 內含相關記錄，唔好再跳轉）。
- Tab 切換用 `history.pushState` + `mount(false)`，唔好整頁 reload。

## Vue 3 陷阱（實測踩過，全部喺 code review 睇唔出）

### 陷阱 1：展開 `reactive()` proxy 會斷響應性

```js
// ✗ 錯：展開 proxy 會複製屬性，reactive 綁定失效
return { ...s, shown, load };
// ✓ 啱：Object.assign 落同一個 proxy
return Object.assign(s, { shown, load });
```

**實測**：header 顯示 `0 of 0` 但 23 張卡片照 render（卡片走另一條 render 路徑），
所以「睇落有嘢」掩蓋咗「數字死咗」。
**`chat-center.js:481-484` 已明文記載呢個陷阱** —— 寫新元件前**先讀隔離元件**。

### 陷阱 2：手動移除 Vue `v-if` 管理嘅 node

```js
// ✗ 錯：Vue 仍以為 node 存在，之後唔會再建
document.getElementById('discovery-modal')?.remove();
```

**實測**：第一次開成功，**第二次開靜靜失敗**（`reopen=false`）。
**修法**：唔好手動 remove，用 `:key="selected.id"`。
`ui_skill` 嘅「防重疊 remove」係講**命令式 DOM popup**，唔係 `v-if` modal。

### 陷阱 3：binding 加錯函數

新 handler 加喺 `bindUserEnvironmentPanel()` 結尾 → 呢個函數**只喺 user-environment
頁跑** → 目標頁面永遠冇綁定。
**實測**：點 tab 之後 URL / mount root / heading **三樣全部不變**。
**修法**：加落 `bind()`（每次 mount 都跑）。
**落手前**：確認你正喺**邊個函數**嘅大括號內（`grep -n "^function"` 或數括號）。

### 陷阱 4：`workspaceHtml()` 內掛 Vue app

`#chat-center-root` 等 mount root **唔喺** `[data-body]` element 內 →
`mountBody()` 乜都唔會 refresh，要用 `mount(false)`。
另外 `refreshAll()`（5 秒輪詢）會**明確跳過** chat-center：
「Vue owns this subtree — do NOT touch it here.」

## 驗證（必須「量度」，唔准「讀碼」）

改完必跑：`node --check src/app.js` → `npm run build` → 喺瀏覽器**揸住 DOM 實測**。

**唔可以睇碼就當通過。** 上面三個陷阱喺 code review 完全睇唔出，
三次都係**驅動 UI 之後**才現形：

| 量度 | 睇咩 |
|------|------|
| `document.querySelectorAll('[data-...]').length` | 卡片數係唔係 > 0 |
| 抄低**兩次**開 modal 前後 `!!document.getElementById('modal')` | 第一次同第二次開都要 true（防「開一次死」） |
| 切 tab 後讀 `location.pathname` + mount root + `main h2` | 三樣都要變；唔變 = binding 冇掛 |
| 讀 header 文字（例如 `23 of 23`） | 兩個數字要一致；`0 of 0` 但卡片照出 = 響應性斷 |

**測試工具唔可靠時要識別**：`page.keyboard.press('Escape')` 曾經送唔到入頁面
（`document.hasFocus()` 係 true，但 capture-phase 探針計到 0 個事件），
而 synthetic `KeyboardEvent` 就成功。
**唔准因為工具 artefact 去改正常嘅碼。**

## 註冊（兩層，兩層都要做）

1. **Skill Library** — `test_render.sync_skills_to_dev_tasks(conn, './skills')`
   （靠 frontmatter `task_id` = `SKILL.UI.DESIGN`）
2. **Skill Prompt SSOT** — `skill_prompt.upsert_skill_prompt(...)`
3. **驗證同步** — `_check_skill_ssot_drift.py` 必須報 `in sync: True`

> 編輯本檔之後**必須**重新 upsert，否則 SSOT 保持舊文字。
> 本 skill 曾經只有 layer 1（dev_task id=329），**冇** SSOT row —— 即係
> Skill Library 見到但 SSOT dropdown 見唔到。已補。

## 現有 Tailwind 詞彙（跟齊）

`rounded-2xl border border-line bg-panel shadow-panel`（卡片/面板）
`bg-soft/60`（表頭）`text-muted`（次要文字）`text-ink`（主文字）
`text-accent`（強調/可點擊）`mono`（代碼/ID）
`bg-emerald-100 text-emerald-700` / `bg-amber-100 text-amber-700` /
`bg-rose-100 text-rose-700` / `bg-sky-100 text-sky-700`（badge 語義）

## 驗證

改完必跑：`node --check src/app.js` → `npm run build` → Playwright 實測
（click card → modal 出現 → Esc 關閉）。

> 完整量度清單見上方「驗證（必須「量度」，唔准「讀碼」）」。
