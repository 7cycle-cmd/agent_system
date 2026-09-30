---
task_id: "DONE.CHAIN.VERIFY"
name: "skill_task_done_verification"
final_verdict: "PASS"
ingested: "yes"
modified_files: ["done_chain.py", "skill_task_done_verification/skill_task_done_verification.skill.md", "skill_task_done_verification/contract.yaml", "skill_tdd_runner.py"]
qc_summary: "P2 harder done-gate: a task cannot be recorded done until 5 rings (what/why/where/how/who) are each satisfied by a MEASURED unit. P3 a refusal becomes an is_active=0 factor proposal. P4 a refusal becomes a worked example. The skill is registered by contract.yaml, not a _registry_*.py."
reason: "user: worker report job is done without evidence. now the work is done only when 100% tracable proven evidence chain exists"
artifacts: ["done_chain.py", "_proof_done_chain.py", "_proof_task_done_skill.py"]
schema: "no new table — reads task_entity_link / route_register / skill_factor_register / skill_lesson"
---

# DONE.CHAIN.VERIFY

## 目的

**Worker 講「我做完了」之前，要先過一條硬門 (hard gate)。**
條門唔係靠一句 reason，係靠 5 個 ring，每個 ring 都要有一個**有單位、量得到**嘅答案。

用戶原話：

> worker report job is done without evidence, now we can have the work done is
> done by 100% tracable and proofed evidence chain, how to it be skill for verify
> each task before worker tell job is done

## 功能

1. **P2 硬門** — `done_chain.assert_may_complete()` 檢查 5 個 ring；任何一個唔過，
   `allow=False`，並且**指名**邊個 ring 唔過（`failed_ring`），唔係一句 free text。
2. **P3 收集** — 一次拒絕 → 一個 `is_active=0` 嘅 factor proposal
   （`propose_factor_from_refusal`），**永不自動啟用**。
3. **P4 回饋** — 一次拒絕 → 一條**做過嘅例子**（`file_refusal_example`），
   寫入 `skill_lesson`，帶 `root_cause` + `suggested_fix` + 可查證 `source_ref`。

**5 個 ring（`done_chain.RINGS`，唔喺呢度重寫一次）：**

| ring | 5W1H | 問嘅問題 | 量嘅單位 |
|------|------|----------|----------|
| `files_changed` | what | 今次 task 改咗幾個 file？ | 改動 file 嘅數量 |
| `task_link` | why | 有幾個改動 file 連返呢個 task？ | 連到 task 嘅 file 數量 |
| `route_health` | where | 有幾條受影響 route 唔係 OK？ | 非 OK route 嘅數量 |
| `verdict` | how | 有幾步判為 YES？ | verdict=YES 嘅步數 |
| `cited` | who/when | 有幾個 claim 嘅 cite_ref 查得到？ | 可查證 claim 嘅數量 |

**單位來源係 `done_chain.RING_SPEC`，唔准喺 skill 內再寫一份** — 同一條規則
兩份，就會令一份被改壞而另一份仲係綠，個 gate 就認證緊一條唔再行嘅規則。

## 流程

1. 收到「做完」嘅提交 → 收集 5 個輸入（changed files / affected routes /
   verdict / claims；`task_link` 由 `task_entity_link` 讀）。
2. 叫 `done_chain.walk_chain()` → 攞每個 ring 嘅觀察值。**每個 ring 都會報，
   唔會 raise。**
3. 叫 `done_chain.assert_may_complete()`。
4. `allow=True` → 可以寫 `done`。
   `allow=False` → **拒絕**，並攞 `failed_ring` 同 structured reason。
5. 對每個失敗 ring：
   - `propose_factor_from_refusal()` → factor proposal（`is_active=0`）
   - `file_refusal_example()` → 一條 lesson（帶 fix）
6. 條循環就係：「唔止擋，仲要回饋一個樣本，令 worker 下次易啲做」。

## 唔可以做的事 (Not to do)

- **唔准**用一句 free text 做拒絕理由。理由一定要 structured
  （`{ring, unit, observed, expected}`），否則 collector 會由 test payload 亂咁
  推 factor — 呢個係 `factor_template_growth` 已記錄嘅缺陷
  （讀 `proof_run.failure_reason` 產生咗垃圾 `oracle_999999999999999`）。
- **唔准**自動啟用 proposal。新 factor 一律 `is_active=0`，要人確認。
- **唔准**喺 skill 或 probe 內重寫 5 個 ring 或佢哋嘅單位 —
  一律由 `done_chain.RINGS` / `RING_SPEC` 讀。
- **唔准**冇 cite 就寫 lesson（`citation_discipline`：no citation, no finding）。
- **唔准**當「冇連 link」等於「全部 file 都冇連」而照過 — ring 唔過就係唔過。
- **唔准**略過一個失敗 ring 而只報第一個：`refused_rings` 要列齊全部。

## QC 檢查點

- [x] `done_chain.py` 冇 per-ring if/else 分支（rings 係 data）
- [x] 每個 ring 有單位，且單位有主體（`assert_units() == []`）
- [x] 硬門拒絕時指名 ring（`code='RING_FAILED'`）
- [x] 結構化 reason 先收；free text 一律 REFUSED
- [x] proposal 一律 `is_active=0`，且 idempotent
- [x] lesson 有 `root_cause` + `suggested_fix` + 可查證 `source_ref`
- [x] `contract.yaml` 通過 `skill_registrar.validate_declaration`
- [x] `TDV.` probe 已登記入 `skill_tdd_runner.PROBES`

---

# MEASURABLE FACTORS — experience for the next worker (2026-09-26)

**這五個 factor 已登記在此 skill 名下(`skill_key='skill_task_done_verification'`),
每個都有真實 metric 及一條量度它的 proof。不是建議;每一個都係 repo 內**量到**
的失敗。無 metric 的 factor = 有標題的散文。**

Run `.\\.venv\\Scripts\\python.exe _seed_coding_skill_factors.py` to (re)seed them,
and `_proof_measurable_coding_skill.py` to measure them.

| factor_key | rule | metric (kind / unit) | target | proof |
|---|---|---|---|---|
| `verdict_is_read` | a check asserts the property it NAMES; never asserts a NEIGHBOUR proof's health | count / checks asserting a neighbour's health | 0 | `_proof_proof_gate_skip_and_timeout.py` |
| `relation_not_count` | assert a RELATION, never a pinned live-population count | count / int-equality against a live population | 0 | `_proof_binding_cite_source.py` |
| `prose_is_not_code` | strip a docstring as an AST NODE, never by deleting quotes | count / text scans that strip prose by deleting quotes | 0 | `_proof_identity_llm_premise.py` |
| `control_present` | every "detects X" check carries a POSITIVE CONTROL | count / detector checks with no positive control | 0 | `_proof_logic_evidence_premise.py` |
| `green_is_measured` | DONE means a VERDICT was READ, never that a run looked fine | count / checks that conclude without reading a verdict | 0 | `_proof_measurable_coding_skill.py` |

## The MEASURED failure behind each rule

* **`verdict_is_read`** — `_proof_proof_gate_skip_and_timeout.py` QC-12
  required `_proof_identity_llm.py` to be GREEN, so it went **63/1** whenever an
  UNRELATED proof regressed. A proxy is a dependency. Assert the property you
  NAME; REPORT the neighbour.
* **`relation_not_count`** — a proof pinned `checkable == rows == 62` and
  `distinct_cite_refs == 6`, both true once, and went red when the register GREW
  while the data was MORE correct. Same family recurred 3 more times.
* **`prose_is_not_code`** — `replace(quotes, '')` does NOT remove a docstring's
  BODY, so `_proof_identity_llm.py` L-07b read the module's own MEASUREMENT
  EVIDENCE as a hardcoded model name: **49/1** on a module that hardcodes
  nothing (MEASURED: literals outside the docstring = 0, inside = 1).
* **`control_present`** — without a positive control, "found nothing" and "the
  detector is dead" are indistinguishable. MEASURED: the fixed detector was
  proved against FOUR probes (code DETECTED, constant DETECTED, docstring-only
  NOT, comment-only NOT).
* **`green_is_measured`** — `python X.py | Select-String` reported an EMPTY
  result on a cp950 console while the process had exited 0 and the proof was
  GREEN. Redirect to a file and read `$LASTEXITCODE`. A count also needs a SECOND
  SAMPLE: a reported "126 orphan processes" was 6 minutes later.
