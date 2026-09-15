# 待办

## Skill Prompt SSOT + llm-tasks — DONE (Phase 0–5)

**设计锁定**（`docs/plan_skill_prompt_ssot.md`）
- Prompt = versioned skill SSOT（非硬编码）；`mouse_spot_verify` v1_strict
- 输出 `Result: YES/NO` + `Reason:`；parser 用 YES 不 SUCCESS
- 100-run proof · pass_gate ≥95% · promote 需 proof（可 force）
- UI：`http://127.0.0.1:18765/llm-tasks` → Skill Prompt SSOT panel
- CLI：`python skill_prompt.py seed|list|show|set-active|test`
- CLI Phase5：`python skill_prompt_ext.py seed-all|lines|list-cases|test-gold|improve-draft`
- DB：`skill_prompt_ssot` / `case` / `test_run` / `inference`
- Gold：`msv_shot_vscode_no` / `msv_shot_generic_target_no` / `msv_shot_doubao_no` + draft icons
- Task Center：root `10` · `10.1`–`10.20` · module `mouse_spot_helper` · version `skill-1.0`
- Improve→draft only；promote still gated

```powershell
python create_db.py --migrate
python skill_prompt.py seed
python skill_prompt_ext.py seed-all
python skill_prompt_ext.py lines
python skill_prompt_ext.py list-cases
python skill_prompt.py test --expected NO --runs 10 --target "Visual Studio Code"
# open http://127.0.0.1:18765/llm-tasks
```

**Follow-on：** YES gold shot · `case_id` on test_run · suite→inference ledger

## Pair QC L1 (Pipeline E) — DONE (+ L1.2 region/phone split)

**设计锁定**（`docs/plan_pair_qc.md` · `docs/ssot_member_phone_region.md`）
- 双路径：MCP/SSOT value 🆚 UI value；**normalize before compare**
- **detect ≠ fix**；`match_ok` 永不被 schema gate 翻转；gate = **never**
- **L1.2 split：** `region = +CC`（例 `+86`）· `phone = local only`（例 `13800138000`）· `e164 = compose only`
- ISO2 `CN/HK` = display lookup only；**唔好**把整段 E.164 存进 phone；phone **唔好**用 INT
- normalize = **纯函数**；raw 永不改写；只存档比对
- fail_class：`business_defect`（正式 case）| `transient_execution`（缺路径/截图空 → 重试，唔开正式 case）
- case 必带 `register_id` + `tdd_rule_id` + `fail_class` + both raws
- 流水线：A Hard Gate ⊥ B FH ⊥ C Code Health ⊥ D MCS ⊥ **E Pair QC**

**已做**
- DDL：`pair_qc_run`（L1 列）、`pair_value_ssot`；legacy rebuild 迁移
- `pair_qc.py`：split normalize（region=+CC / phone=local / e164 compose）/ compare / fail_class / case / CLI
- MCS `FIELD_TDD_TEMPLATES`：region `^\+\d{1,4}$` primary；phone `local_digits_by_region` depends_on region；country lookup
- **Shared TDD SSOT：** `field_tdd.py` → load/validate/import into existing `field_tdd_rule`（agent.db；no member_db fork）
- Canonical 6 rules：region / phone / address / name / gender / contact_method（bound to real `register_id`）
- seed：`qc.pair_verify`、`pair_qc_mismatch`；`managed_coding seed` + `import-tdd` refresh rules
- UI：`/pair` · `/managed` Function Builder（Task header · 8 action cards · field contracts）
- selftest：`python field_tdd.py selftest` · `python pair_qc.py selftest`

```powershell
python create_db.py --migrate
python pair_qc.py selftest
python pair_qc.py run --field phone --region +86 --mcp-value "+86 138-0013-8000" --ui-value "138-0013-8000"
python pair_qc.py run --field phone --region +86 --mcp-value "13800138000" --ui-value "1380013800"
python db_browser.py --port 8768
# http://127.0.0.1:8768/pair
# http://127.0.0.1:8768/managed?system=membership
```

## CH7 Source location + dead cleanup — DONE

**设计锁定**（`docs/plan_source_location_cleanup.md`）
- `code_register`: `file_path` / `line_start` / `line_end` / `code_span`
- usage = total · pass/fail counters · score = pass/usage*100
- dead easy find: status ∈ {zombie, dead_candidate} + usage=0
- watchdog / `/health` → `code.cleanup` pending tasks（**只 mark，不删源码**）
- CLI：`code_health.py bind|cleanup|report` · API：`/api/health/bind` · `/api/health/cleanup`

```powershell
python create_db.py --migrate
python code_health.py selftest
python code_health.py cleanup --migrate
python db_browser.py --port 8768
# http://127.0.0.1:8768/health
```

## Managed Coding Spine (Pipeline D) — DONE (+ MCS8 Function Builder)

**设计锁定**
- 定律：`value + value = output`；同 pattern 不同 value
- **register_id 法**：无 register → 不算 managed code；`managed_invoke` 拒绝未登记
- Profile **A–H = task_ssot dims**（非每字段 8 个 task）
- **主流程**：human request → research（reuse table / design table）→ multi-dim SSOT → build（register_id + field TDD）
- Ontology = Task 1 map（channel+module / +function）；**never gate**；rubbish 只 mark 不删源码
- **MCS = Ontology & Registry + Function Builder**；runtime orchestrator 仍下游
- `channel.code` = env ontology（local_pc），**不是** `exec.driver`
- 每 field 有 TDD：`region=+CC`；`phone` local digits by region（+86→11）；country text lookup；`field_tdd_rule` DB-driven
- Membership 在 version `mem-1.0`，root label `1`，slices `1.1`… — **不与** agent_db vision `1.1` 冲突
- Function Builder IA：Task ID · module · Function ID+name · Related table/field · **8 action sub-tasks** · field contract cards
- 计划：`docs/plan_managed_coding.md`
- **Next MCS9：** Function API Docs（Swagger-like per Function ID）— mapping + explanation generated on build；plan `docs/plan_function_api_docs.md`- **Next MCS9：** Function API Docs（Swagger-like）— per `register_id` mapping+explanation；build 时投影；`docs/plan_function_api_docs.md`
**已做 MCS0–MCS8 (+ L1.2 UI)**
- DDL：`code_register`、`fn_request`、`fn_research`、`field_tdd_rule`、onto_*；`function_scoring.register_id`
- `managed_coding.py`：builder flow create/research/build + contracts/layer + seed + managed_invoke + worker report
- UI：`/managed` Task header + 8 action cards + region/phone/country contracts + advanced build + layer map + handoff
- API：`POST /api/managed/build` · `GET /api/managed/builder` · `/api/managed`
- CLI：`python managed_coding.py selftest|seed|report|contracts|cleanup|build --text ... --module ...`

```powershell
python create_db.py --migrate
python managed_coding.py selftest
python managed_coding.py report --system membership
python db_browser.py --port 8768
# http://127.0.0.1:8768/managed?system=membership
```

## Fault Event ID Hub + SSOT + Task/QC

**设计锁定**
- 每个 `fault_event` 一次 vision → `fault_analysis`（保留）
- `fault_event` = ID hub + fault SSOT
- 心跳恢复 ≠ 自动结案
- Schema QC：**唯一 hard gate** = PRAGMA exact set pass/fail；MCP/api/notify 永不做 gate
- **流水线**：A Hard Gate ⊥ B Fail-Handling ⊥ C Code Health ⊥ **D Managed Coding**（共享 agent.db ID；B/C/D **不**依赖 gate）
- Fail → fault + 新 remediate task；STEP2–5 = goal → multi-dim SSOT → report（标准表）
- Bug 本质：goal 期望 vs SSOT/观察 不一致（SSOT missing / update / del）
- Task 层级：channel → module → action → version → task（**parent_task_id**）；能力/SSOT 多维可挂 task_ssot
- 计划全文：session `plan_fail_handling.md`

**已做**
- Phase 0–2：hub/SSOT/watchdog facts
- QC0：`db_browser` `/api/schema/{table}` + 页内 Schema 面板
- T1：`task_action_name`, `tdd_type`, `version_center`, `dev_task`(+parent_task_id), `dev_task_field`, `schema_ssot`, `schema_qc_run`
- `schema_qc.py`：对 `vision_asset` QC PASS 并写 SSOT；task `1.1b` → pass
- **T2 Task Center UI**
  - `GET /tasks` 树（channel→module→version→parent/child）
  - `GET /tasks?task_id=N` 同页详情 + Open QC
  - API：`/api/tasks`、`/api/tasks/{id}`、`/api/versions|actions|…`
  - POST：`/api/versions`、`/api/tasks`、`/api/tasks/bundle`、`/api/tasks/{id}/fields`
  - `db_schema.create_task_bundle` / `create_version` / `create_dev_task` / `add_dev_task_field`
  - 双页 top nav；create **不**自动 DDL
- **T3 MCP QC + migrate spawn**
  - `schema_qc.py --mcp-shot`：webbrowser.open + MCP `screen.snapshot` → `qc_evidence/` → `vision_asset` → `schema_qc_run.vision_id` + `dev_task.qc_vision_id`
  - soft HTTP `/api/schema` → 真实 `api_ok`（**非 gate**）
  - 默认 try MCP `system.notify` pass/fail（`--no-notify` 可关）
  - `create_db.py --migrate --qc` / `--qc-mcp`：detached spawn 所有 pending/fail/running `qc.verify_schema`
  - `mcp_client.extract_image_bytes` 支持 Companion JSON+base64 文本包
- **HARD GATE only（pass/fail）**
  - 唯一 gate：PRAGMA observed **exact set** == expected（missing **或** extra = fail；order 非 gate）
  - MCP / api_ok / notify = telemetry only，**永不**改 match_ok
  - 任何 hard fail：`fault_event`（`fault_type=schema_qc_fail`）+ `fault_event_fact` + Task Center child `schema.remediate`（`parent_task_id`=失败 QC task，label `{qc}-fix-N`）
  - 每次 fail run 新 fault + 新 remediate task（不 dedupe）
  - pass **不** auto-resolve fault_event
  - seed：`task_action_name.schema.remediate`、`fault_option.schema_qc_fail`
  - 链接 SSOT：facts 的 `remediation_task_id`（`fault_event.task_id` 历史 FK→task_queue，best-effort）
- **FH0 + FH1（Fail-Handling 起步）**
  - `fail_handling.py`：goal_type enum、STD_ROW 契约、`infer_schema_qc_goal`、`pragma_diff_std_rows`、`contracts`/`selftest` CLI
  - hard fail → remediate `payload_json` 强制 `goal_type` / `goal_text` / `success_criteria` / `plan_steps` / `goal_context` / `step1_std_rows` / `pipeline=B_fail_handling`
  - facts 写 `goal_*`；返回值带 goal 字段；`schema_qc` 打印 goal_type
  - Task Center 详情：**Goal (Fail-Handling FH1)** 面板 + STEP1 std rows 表
  - 分类：missing-only→`fix_schema`；extra-only→`fix_expected`；both/空→`investigate`
  - **不**改 hard gate；**不**依赖 `--write-ssot`
- **FH2 task_ssot（多维能力 SSOT）**
  - 表 `task_ssot`：`UNIQUE(task_id,dim_key)` + parent_dim_id/sort/notes
  - helpers：`list_task_ssot` / `upsert_task_ssot` / `delete_task_ssot`
  - seed：`capability.ssot` action、version `cap-1.0`、task `oc.open-browser`（#10）+ 12 dims（**truth**：MCP 无 browser.open；fallback webbrowser）
  - Task Center：Task SSOT 面板 + upsert form；API `POST /api/tasks/{id}/ssot`、`DELETE /api/tasks/{id}/ssot/{sid}`
  - 非 gate；与 schema_ssot 分离
- **FH3 STEP4 SSOT assemble**
  - `assemble_fail_ssot`（纯函数）+ `run_fail_ssot_assemble`（读 DB 写 payload/facts）
  - hard fail 自动 assemble → `plan_steps` / `step4_delta_rows` / counts
  - CLI：`python fail_handling.py assemble --task-id N`（或 `--case-id`）
  - API：`POST /api/tasks/{id}/assemble`；UI Goal 面板 STEP4 表 + Re-run 按钮
  - 分类：schema missing→DDL add；extra→expected del；capability false→update+fallback keep
  - **永不**改 hard gate
- **FH4 STEP2 Ollama QC PNG → vision dims**
  - `vision_detail_to_std_rows` / `analyze_qc_png` / `resolve_qc_vision_path` / `run_fail_vision_step2`
  - dims：`vision.severity|ui_state|table_visible|column_headers_json|likely_cause|…`（source=ollama|vision）
  - CLI：`python fail_handling.py vision --task-id N [--vision-id|--image|--no-ollama]`
  - API：`POST /api/tasks/{id}/vision`；UI Goal 面板 STEP2 表 + Run Ollama / resolve-only
  - hard fail：若有 `vision_id` 只挂 `step2_pending`（**不** inline 调 Ollama，避免拖慢 gate 路径）
  - schema_qc fail 打印 STEP2 提示命令
  - **永不**改 hard gate / match_ok；Ollama 失败只写 error 行
- **FH5 STEP5 fault/task report**
  - 表 `fault_report`：event/task 链接 + summary + markdown + report_json + notified_at
  - `build_fail_report`（纯）+ `run_fail_report`（写 DB/payload/facts，可选 MCP notify）
  - rollup：gate + goal + STEP1/2/4 + plan_steps + top_deltas + links
  - CLI：`python fail_handling.py report --task-id N [--notify]`
  - API：`POST /api/tasks/{id}/report`；UI Goal 面板 STEP5 markdown + Build/notify
  - helpers：`insert_fault_report` / `list_fault_reports`
  - **永不**改 hard gate；notify 仅 telemetry
- **FH6 Phase 3 fault option SSOT match**
  - `match_fault_option_ssot`（纯）+ `run_fault_ssot_match`（写 event/analysis/facts/payload）
  - statuses：`matched|weak|unmatched|ambiguous`；score 阈值；**永不** gate
  - seed：`schema_qc_fail` fault_type/gate/match_ok/fact_keys + solution「Remediate schema QC hard fail」；heartbeat `fault_type` 对齐
  - CLI：`python fail_handling.py match --case-id 9 --task-id 11`
  - bridge：`openclaw_bridge.process_event` 分析后挂 match_score/option/solution（失败不回滚 trunk）
  - API：`POST /api/tasks/{id}/match`；UI Goal 面板 FH6 + Run match
  - smoke：event#9 → `schema_qc_fail` score≈0.99 matched；solution_id=2；selftest fh6_qc/hb
- **Phase 5 supervisor + Task Center Run QC/FH**
  - `run_fail_handling_supervisor`：默认 assemble → vision? → match → report；单步失败不中断；**永不** gate
  - `resolve_supervisor_target`：QC parent → 最新 schema.remediate child
  - `spawn_detached_fail_handling` / `spawn_pending_fail_handling`（开 remediate 批量 detached）
  - CLI：`python fail_handling.py supervise --task-id 11 [--ollama] [--no-vision] [--spawn-pending]`
  - `schema_qc.run_qc_for_task` / `load_qc_task` / `build_qc_argv_for_task`（remediate 可回 QC parent）
  - API：`POST /api/tasks/{id}/supervise`、`/run-qc`、`/api/fail-handling/spawn-pending`
  - UI：Task detail Run QC / Run fail-handling；Goal 面板 Phase5 supervisor
  - smoke：task#11 steps assemble+match+report ok=3；match schema_qc_fail；report#2；gate=never

**手动 QC**
```powershell
python create_db.py --migrate
python fail_handling.py selftest
python fail_handling.py contracts
python fail_handling.py assemble --task-id 11
python fail_handling.py assemble --task-id 10
python fail_handling.py vision --task-id 11 --vision-id 3
python fail_handling.py vision --task-id 11 --vision-id 3 --no-ollama
python fail_handling.py report --task-id 11
python fail_handling.py report --task-id 11 --notify
python fail_handling.py match --case-id 9 --task-id 11
python fail_handling.py supervise --task-id 11 --no-vision
python fail_handling.py supervise --task-id 11 --ollama
python fail_handling.py supervise --spawn-pending
python db_browser.py --port 8766
# http://127.0.0.1:8766/tasks
# http://127.0.0.1:8766/tasks?task_id=11  # remediate + STEP2/4/5/FH6 + Phase5
# http://127.0.0.1:8766/tasks?task_id=10  # oc.open-browser capability assemble
python schema_qc.py --table vision_asset --task-label 1.1b --write-ssot
python schema_qc.py --table vision_asset --task-label 1.1b --write-ssot --mcp-shot
python schema_qc.py --table demo_x --task-label 1.2b --expected id,name
python create_db.py --migrate --qc
python create_db.py --migrate --qc-mcp
```

**下一阶段 — Code Health / Function Trace（管道 C，非 gate）**
- 计划全文：`docs/plan_code_health_trace.md` + session `plan_code_health_trace.md`（**WHY-ID W1–W10**）
- 管道关系：A Hard Gate ⊥ B Fail-Handling ⊥ **C Code Health**（共享 agent.db + task_label≈TACID；C 永不改 match_ok）
- **CH0 DONE**：`code_health.py` contracts / WHY / score+status pure helpers / CLI `contracts|selftest`
- **CH1 DONE**：`function_invoke_trace` + `function_scoring` DDL in `db_schema`；`ensure_schema` migrate；CLI `verify-schema --migrate`
- **CH2 DONE**：`function_invoker` + rollup + CLI `demo`（ok+fail→50；3 calls→66.6667；sub_steps；reraise）
- **唯一名 DB 驱动**：`allocate_unique_function_name` 对 `function_scoring UNIQUE(module,function)` 占位；`register_impl_function` 写 `task_ssot` impl.*（一 task 一 fn）
- **CH3 DONE**：`harvest_static_impl_refs` ← `task_ssot` impl.module/function/required → static refs + zombie
- **CH4 DONE**：`generate_code_health_report` + `get_tacid_branch_function_report`；CLI `harvest|report|branch`
- **CH5 DONE**：`fail_handling._safe_step` → `function_invoker`（assemble/vision/match/report）；`schema_qc.run_qc_for_task` → invoker（launch 轨迹，gate 仍 PRAGMA）
- **CH6 DONE**：Task Center `/health` + `/api/health` + `/api/health/branch` + POST harvest；nav Code Health
- 状态：zombie / dead_candidate / active / inactive；score 仅 success/total*100
- 禁止：AI 打分、自动删源码、墙钟当唯一名、第三 gate、平行 experiment.db
**更后 backlog**
- fault_event.task_id → dev_task FK 重定向
- hard-fail 后可选 auto `--fail-handle` spawn（现已有 on-demand supervise）
- Phase 更重 supervisor（跨 case 队列 / 重试策略）

---

## OpenClaw bridge + MCP register_id trace — DONE (v1)

**做了什么**
- 表 `fault_analysis`（`init_db.sql` + `db_schema.py` 增量迁移）
- `mcp_client.py`：Companion Local MCP（Windows loopback）
- `vision_analyze.py`：Ollama `qwen2.5vl:7b`
- `openclaw_bridge.py`：按 `event_id` 分析一次 + `system.notify`（经 traced wrapper）
- `watchdog.py`：主干 `commit` 后 `spawn_detached` bridge（失败不回滚故障）
- `bridge_boundary_test.py`：防抖 / live 回退（mock MCP/Ollama）
- **`openclaw_mcp_trace.py`（新）**：MCP 工具 = membership 同款 spine
  - seed：`openclaw_companion` / `cap-1.0` / root `oc.mcp-tools` + slices `oc.1`–`oc.4`
  - `code_register.register_id`：`mcp.screen_snapshot` · `mcp.system_notify` · `mcp.ping` · `fallback.webbrowser_open`
  - runtime：`traced_*` → `managed_invoke` → `function_invoke_trace`（gate=never；缺 DB 仍 soft-call MCP）
  - wire：`openclaw_bridge.try_*` · `schema_qc.try_mcp_shot` / `try_notify` / `open_browser_best_effort`
  - migrate hook：`ensure_task_center_schema` → `seed_openclaw_mcp_registers`
  - enroll：`code_health.V1_ENROLLED_SOURCES`
  - CLI：`python openclaw_mcp_trace.py selftest|seed|list|sync|notify|snapshot`

**你要手动开**
1. Companion → Permissions → **Local MCP Server ON**，复制 URL + token 到 `.env`（参考 `.env.example`）
2. 确认 Windows 能访问 Ollama：`OLLAMA_BASE_URL=http://127.0.0.1:18803`
3. 迁移：`python create_db.py --migrate`
4. 冒烟：`python openclaw_mcp_trace.py selftest`；`python openclaw_mcp_trace.py list`
5. 可选 live：`python openclaw_mcp_trace.py sync`；`python mcp_client.py --ping`；`python openclaw_bridge.py --event-id N`
6. 回归：`python bridge_boundary_test.py --yes-wipe`

**v1 不做**
- 自动 `system.run` / 重启 worker
- 用 OpenClaw 截图取代 heartbeat `mss`
- 自动把 `fault_event.status` 改成 resolved
- Pair QC L2 screen OCR / 真 MCP `browser.open`

---

## HKO dual-path weather proof — DONE (v1)

**做了什么**
- `hko_weather_proof.py`：Playwright primary 🆚 OpenClaw secondary
  - primary：Chromium goto HKO → full_page PNG + DOM regex scrape
  - secondary：`traced_webbrowser_open` + `traced_screen_snapshot` + `extract_image_bytes`
  - Ollama vision JSON：time / region / temp / humidity / weather（`gate=never`）
  - normalize → compare → `hko_proof/run_*.json` + `latest.json`
  - CLI：`run|list|latest|selftest|contracts|html`
- `requirements-hko.txt`：`playwright>=1.40.0`
- `db_browser.py`：nav **HKO Proof** · `/hko` · `/api/hko` · `/hko/img?f=` · `POST /api/hko/run`

**你要手动开**
1. `pip install -r requirements-hko.txt`
2. `python -m playwright install chromium`
3. Companion Local MCP ON（OpenClaw path）；Ollama `qwen2.5vl:7b` on `18803`（vision）
4. `python hko_weather_proof.py selftest`
5. `python hko_weather_proof.py run`（可加 `--skip-openclaw` / `--skip-ollama`）
6. `python db_browser.py --port 8768` → `http://127.0.0.1:8768/hko`

**v1 不做**
- 真 MCP `browser.open`（仍 webbrowser fallback）
- 把 compare `match_ok` 写成 schema gate
- 把截图混入 `hb_snapshots` 滚动清理

---

## P2.x：收紧 `check_workers()` 嘅事务粒度（并入 Phase 2）

**问题**：一轮多 worker 一次 commit，中途失败会丢审计。
**打算**：每 worker 一 commit + watchdog hardening（见 SSOT Phase 2）。

---

## 其他已知取舍（暂唔改，记录在案）

- **P2 § 心跳线程卡住**：`take_screenshot()`（mss）锁死会标成 crash 而非 hang。
- **P2 § 卡死取证时机**：`business_alive=0` 最多迟一个 HEARTBEAT_INTERVAL。
