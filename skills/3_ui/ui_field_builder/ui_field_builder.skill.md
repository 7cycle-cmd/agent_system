---
task_id: SKILL.UI.FIELD.BUILDER
display_task_id: SKILL.UI.FIELD.BUILDER
name: ui_field_builder
catalog_id: 1
subcatalog_id: 1
# prompt_setting_id: 34  REMOVED 2026-09-20 — id 34 is 'yes_no' (YES/NO output only); this skill is not a YES/NO skill. Reassign to a correct row or leave unset.
final_verdict: "INCOMPLETE"
ingested: "yes"
modified_files: []
qc_summary: "Build UI for ontology fields where each entity class has a different request shape"
reason: "UI builder for ontology entities (Channel/Module/Capability/API/Function/Table/Field) with per-class request schemas"
artifacts: ["ui_field_builder.skill.md"]
schema: "ontology entity request schema per class"
---
# Prompt 內容
建立 UI 表單，用於建立/編輯 ontology 實體（Channel / Module / Capability / API / Function / Table / Field）。
每個實體 class 有唔同嘅 request 欄位，UI 要按 class 動態渲染對應表單。
Build UI forms for creating/editing ontology entities. Each entity class has a different request shape; the UI must render the form dynamically per class.

## Ontology entity classes (fixed order)
Channel → Module → Capability → API → Function → Table → Field
Optional extra: Job, Event

## Per-class request fields

| Class | Request fields |
|-------|----------------|
| Channel | name, description |
| Module | name, description, channel_id |
| Capability | name, description, module_id |
| API | name, method, path, capability_id |
| Function | name, description, capability_id |
| Table | name, description |
| Field | name, format, table_id |
| Job | name, action |
| Event | name, action |

## UI rules
1. Class selector (dropdown) determines which form fields render.
2. Switching class swaps the field set — never show fields for the wrong class.
3. Each field maps 1:1 to a request key (no merging).
4. Required fields per class: name is always required; parent_id (channel/module/capability/table) required where the class has one.
5. On save, POST the request with ONLY the fields for the selected class.
6. Validate before submit: required fields present, format valid (e.g. Field.format in INT/TEXT/...).

## Task ID coding rule
- Format: `{RootTaskId}.{GlobalSequenceNumber}`
- Within one RootTaskId, Function/API/Table/Field/Job/Event share ONE continuous global sequence.
- Do NOT reset sequence when item type changes.
- Item type metadata is stored separately; ID only has root + seq.
- Never renumber after delete; keep orphaned/skipped numbers.
- Types: F=Function, A=API, T=Table, D=Field, J=Job, E=Event

## Example
Building a test system with phone + region:
- Channel local → Module test → Capability member data → API member_data_api → Function get_member → Table test1 → Field phone (INT) → Field region (TEXT)
- 8 tasks, one per entity, each with its class-specific request shape.

## Dynamic form rendering — proven patterns (learned in production)

These are the patterns that survived real use on the Watchdog notification panel (channel select + per-channel url/token/extra fields). Apply the same to any class-switching form.

### 1. Server response must carry everything the re-render needs
- A POST that returns a partial config (e.g. config WITHOUT the `channels` list) makes the next re-render blank: `Object.keys({})` → empty `<select>` for ~1s until the next GET poll repopulates.
- **Rule:** after a POST, if the response is missing data the render needs, preserve it from the previous in-memory state before assigning:
  ```js
  const prevChannels = (state.cfg && state.cfg.channels) || {};
  state.cfg = data.config;
  state.cfg.channels = prevChannels; // avoid blank <select> flicker
  ```
- Symptom of the bug: value goes `updated → empty → updated` (flicker) after a change.

### 2. Rebuilding `<select>` options requires setting `.value`
- `sel.innerHTML = opts` rebuilds options but does NOT set the select's `value` property → dropdown renders blank until F5.
- **Rule:** always `sel.value = channel;` after `sel.innerHTML = opts;`.

### 3. Auto-refresh must never wipe in-progress typing
- A 5s full-page re-render destroys form input the user is typing.
- **Rule A (dirty flag):** set `state.dirty = true` on focus/input of form fields, `false` on blur; the full refresh skips re-render while dirty.
- **Rule B (select-only poll):** for the part that must stay in sync with the server (e.g. channel dropdown + setup hints), run a lightweight 1s poll that updates ONLY the `<select>` options + hint block — never the text inputs.
- **Rule C (manual-only page):** if the page is form-heavy, disable the full auto-refresh entirely; provide a Refresh button.

### 4. Per-class (per-channel) setup hints
- Each class/channel gets a `SETUP_HINTS` map: `{ url template, numbered setup steps, tokenLabel, tokenHint, extraLabel, extraHint }`.
- Descriptive labels beat generic ones: "Bot token (from BotFather)" > "Token"; "Recipient phone (Extra)" > "Extra".
- On class switch: auto-fill the URL template, clear token/extra, re-render hints.

### 5. Never auto-open the browser on service startup
- A helper/server that calls `webbrowser.open(url)` at startup pops the user's browser on EVERY restart — considered a bug.
- **Rule:** open on demand only, via an explicit endpoint (e.g. `POST /api/open-browser?url=/path`).

## Verification checklist (browser, Playwright)
1. Switch class/channel via dropdown → sample the select value 8× at 150ms: all samples must equal the new value (no blank gap = no flicker).
2. Type into a text field, wait past 2 auto-refresh cycles → value persists, same DOM element (no re-render).
3. Reload page (F5) → saved config renders correctly (channel, url, token, extra).
4. Setup hints block matches the selected class within one poll interval.
5. Reset config to a clean default after testing.

## Ops notes (this workspace)
- SPA: `llm_task_monitor_ui/` → `npm run build` → `dist/`; helper serves it on :18765.
- Restart helper (never pops browser anymore): kill pid on :18765, then `Start-Process .\.venv\Scripts\pythonw.exe -ArgumentList "mouse_spot_helper.py" -WindowStyle Hidden`.
- Open UI on demand: `POST http://127.0.0.1:18765/api/open-browser?url=/llm-tasks/watchdog`.