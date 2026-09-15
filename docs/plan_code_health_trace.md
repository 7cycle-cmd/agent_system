# Code Health + Function Trace — anti rubbish-mountain

**Locked:** 2026-09-14  
**Full session plan:** also mirrored under session memory `plan_code_health_trace.md`.

## Why this exists

Every registered capability/action must leave an auditable trail of **whether it actually ran**, under which **task_label (TACID)**, with an **objective** success rate — so Task / SSOT / Fail-Handling code stays purposeful, not dead weight.

If a change cannot cite a **WHY-ID (W1–W10)** below → **out of scope**.

## Pipelines (one DB, three roles)

| ID | Role | Gate? |
|----|------|-------|
| **A** | Hard Gate = PRAGMA exact-set | **Yes** (structure only) |
| **B** | Fail-Handling = goal → SSOT → report → match | **Never** |
| **C** | Code Health = register → invoker → trace → score → report | **Never** |

Shared: `agent.db`, `dev_task.task_label` ≈ TACID, `task_action_name` + `task_ssot`.  
Not shared: gate decision, auto-delete source, parallel `experiment.db`, new event_type zoo.

## WHY catalog (cite in code / PRs)

| ID | Need | Build | Do **not** build |
|----|------|-------|------------------|
| W1 | What fn does the system *claim* to use? | Static refs via `task_ssot` `impl.module` / `impl.function` | AI/import-graph guess |
| W2 | Did it *run* under a task? | Append-only `function_invoke_trace` | Full APM/profiler product |
| W3 | Objective quality | `score = total>0 ? success/total*100 : null` | LLM subjective scores |
| W4 | Declared but never hit | `status=zombie` | Delete as dead code |
| W5 | Undeclared and never hit | `status=dead_candidate` mark only | Auto-delete `.py` |
| W6 | Branch audit (`1.1*`) | `get_tacid_branch_function_report` | Brittle JSON LIKE only |
| W7 | Human rot dashboard | `generate_code_health_report` + UI | Score as CI/QC gate |
| W8 | Stats must not lie | `function_invoker` choke point | Wrap stdlib day-one |
| W9 | Replay / provenance | Trace rows never erase hits | Overwrite hit history away |
| W10 | Fast UI rollup | `function_scoring` counters | Rollup as only store |
| W11 | File + line (+ code span) | `code_register.file_path/line_*` | Import-graph guess only |
| W12 | Dead → cleanup task | `spawn_dead_function_cleanup_tasks` + watchdog | Auto-delete source |

## Vocabulary map

| Plan term | This repo |
|-----------|-----------|
| TACID | `dev_task.task_label` (+ optional `dev_task.id`) |
| Action register | `task_action_name` |
| Static require | `task_ssot`: `impl.module`, `impl.function`, `impl.required` |
| Dynamic hit | `function_invoke_trace.tacid` |
| Score table | `function_scoring` (rollup; UNIQUE module+function) |

## Data (minimal)

1. **`function_invoke_trace`** (W2, W9) — append-only ledger: tacid, module, function, ok, ts, `sub_steps_json`, optional `parent_trace_id`. Retries are **not** new top-level business events.
2. **`function_scoring`** (W3, W4, W5, W10) — one row per module.fn: counters, score, cached static/hit tacid lists, status. UPDATE counters/status only; **hits remain forever in trace**.
3. **Status** (deterministic):  
   `total==0 & static → zombie` · `total==0 & !static → dead_candidate` · `total>0 recent → active` · `total>0 stale → inactive`.

## Enrollment (anti mountain)

**v1 must use invoker:** supervisor steps (assemble/vision/match/report), `run_qc_for_task` path, demo, any task with `impl.required=true`.

**v1 do not wrap:** db_browser HTML, tiny SQLite helpers, stdlib, random one-off scripts.

## Phases

| Phase | Deliverable | WHY |
|-------|-------------|-----|
| CH0 | **DONE** `code_health.py` contracts + WHY W1–W10 + CLI | drift lock |
| CH1 | **DONE** `function_invoke_trace` + `function_scoring` DDL + migrate | W2,W9,W10 |
| CH2 | **DONE** `function_invoker` + rollup + CLI `demo`；唯一名=DB `UNIQUE(module,function)` 分配 | W8,W3 |
| CH3 | **DONE** `harvest_static_impl_refs` from `task_ssot` impl.* → zombie | W1,W4,W5 |
| CH4 | **DONE** `generate_code_health_report` + branch query | W6,W7 |
| CH5 | **DONE** supervise steps + `run_qc_for_task` via invoker | W8 real traffic |
| CH6 | **DONE** `/health` + `/api/health*` read-only panel | W7 |
| CH7 | **DONE** file+line bind · usage/pass/fail UI · `code.cleanup` tasks · watchdog side-job | W11, W12 |

No “wrap everything” phase without a new WHY.

See also: `docs/plan_source_location_cleanup.md`.

## Report shape (stable for UI)

```json
{
  "report_time": "ISO-8601",
  "gate": "never",
  "why": ["W4", "W5", "W7"],
  "zombie_functions": [],
  "dead_candidate_functions": [],
  "low_score_functions": [],
  "notes": "objective score; no source deleted"
}
```

## Reject in review

- Parallel DB file · score as QC gate · AI writes score · DELETE trace hits for “cleanup” · auto-delete source · unregistered event_type · invoker on every internal one-liner · health system that cannot link `task_label`

## Done when a human can answer

1. What did we **register**? (W1)  
2. What **ran** on this experiment branch? (W2, W6)  
3. What is **zombie / dead_candidate / low score**? (W4, W5, W7)  
4. Why is this module in the repo? → task_label + trace, or marked candidate — **not** unexplained bulk.
