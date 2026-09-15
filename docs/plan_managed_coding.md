# Managed Coding Spine (MCS) — full plan

**Status:** MCS0–MCS8 done · **Next MCS9 Function API Docs** (`docs/plan_function_api_docs.md`)  
**Gate:** never (does not replace Pipeline A PRAGMA gate)  
**DB:** `agent.db` only — no parallel experiment.db  
**Goal:** every coding path is **DB-declared, register_id-bound, invoker-traced**; workers see **active vs rubbish** without noise.

**Positioning (locked):** MCS is the **Ontology & Registry + Function Builder** layer.  
Primary missing flow (now first-class):

```text
human → request function/system
  A) AI research (if work not already there)
     · what exists on channel+module
     · WITH table  → slices from fields (1.1 region, 1.2 phone…)
     · WITHOUT table → design-table tasks (1.1 member_id, 1.2 name…)
  → multi-dim SSOT plan (task_ssot + TDD per field)
  → build → register_id each function + field_tdd_rule
  → everything output traceable
```

It is **not** the full runtime orchestrator (driver route, GUI evidence capture, pair QC execute).

---

## 0. Law

```text
value + value = output
same pattern, different values
no register_id → not managed code
no invoker hit (when required) → zombie / rubbish mark (never auto-delete source)
channel.code = environment ontology (e.g. local_pc) — NOT exec driver
exec.driver  = orchestrator dim (mcp | openclaw | sql | api) — NOT MCS core
```

API and SQL sync are the **same recipe**; only dim values change.

---

## 0.1 Layer split (why /managed is not the whole job)

| Layer | Owner | Owns | Does **not** own |
|-------|--------|------|------------------|
| **D · Ontology & Registry** | **Managed Coding** (`/managed`) | system, module, slice, field, register_id, TACID, naming law, equation, work_queue, active vs rubbish | MCP/OpenClaw route, Screen-Frame, causal pack, pair QC, TDD pack |
| **Intent SSOT** | MCS + task_ssot | slice intent dims (`plan.table`, `slice.key`, goal.*) | runtime mutation of ERP rows |
| **Template & driver routing** | **Orchestrator** (downstream) | template match, `exec.driver` | rewriting register tables |
| **Execution** | Orchestrator | MCP / OpenClaw / SQL / API calls | inventing function names |
| **Trace & evidence** | Orchestrator + C | run_id, artifacts, screenshots, SSOT before/after | replacing code_register |
| **QC / pair / TDD** | A (schema gate) + Orchestrator business QC | PRAGMA hard gate; pair dual-path; regression | ontology as hard gate |

```text
Managed Coding (D)
  system · module · slice · register_id · TACID · equation · work_queue
        ↓ handoff keys: register_id + slice_task_id + slice.key + plan.table
Orchestrator
  template · exec.driver · run · evidence · QC pair · TDD
```

**Reuse rule:** do **not** rebuild TACID/slice/register tables. Bind orchestrator **onto** `register_id`.

**UI rule:** `/managed` must show (1) what MCS owns, (2) what is downstream, (3) handoff keys, (4) live membership map.

---

## 1. Pipelines (unchanged orthogonality)

| ID | Name | Gate |
|----|------|------|
| A | Schema QC PRAGMA exact-set | **hard** |
| B | Fail-Handling | never |
| C | Code Health trace/score | never |
| **D** | **Managed Coding Spine** (this plan) | **never** |

D **uses** C (register + invoker + score) and **task_ssot** (multi-dim).  
D does **not** flip `match_ok`.  
D does **not** choose MCP vs OpenClaw (that is orchestrator `exec.driver`).

---

## 2. Label grammar (no clash with vision `1.1`)

Labels are unique per **`version_id`** (`UNIQUE(version_id, task_label)`).

| Scope | version_label | root label | slice labels |
|-------|---------------|------------|--------------|
| Agent DB / vision hub | `1.1` (module agent_db) | `1.1` create table… | `1.1a`, `1.1b` |
| **Membership system** | `mem-1.0` (module membership) | **`1`** | **`1.1` region, `1.2` phone, …** |

So membership **Task 1** and slices **1.1…** live under `mem-1.0` and do **not** collide with vision tasks.

Profile letters **A–H are dims / checklist**, not 8 tasks × N fields (anti rubbish-mountain).

```text
1          membership system (root)
1.1        region          ← one slice task
  dims:    profile.A … profile.H + slice.* + impl.* + register_id
1.2        phone
…
```

Child **dev_task** only when lifecycle needs it (QC fail, ship, remediate).

---

## 3. Slice profile (fixed checklist)

| Code | dim_key prefix | Meaning |
|------|----------------|---------|
| A | `profile.A.field` | field / column contract |
| B | `profile.B.table` | table / SQL sync target |
| C | `profile.C.api` | API exposure |
| D | `profile.D.function` | impl function name (DB-unique) |
| E | `profile.E.tdd` | TDD type / test hook |
| F | `profile.F.trace` | trace required + TACID |
| G | `profile.G.module` | module code |
| H | `profile.H.register_id` | **mandatory** managed register id |

Plus system/slice dims:

- `system.key`, `system.output`, `goal.text`, `goal.equation`
- `slice.key`, `slice.sort`, `slice.status` (`active|draft|rubbish|deprecated`)
- `impl.module`, `impl.function`, `impl.required`, `impl.register_id`

Incomplete required dims → **incomplete** (worker should not invent free-form work).

---

## 4. Tables

### 4.1 `code_register` (law for coding)

- `register_id` TEXT UNIQUE (opaque id, DB-allocated)
- `module_name`, `function_name` UNIQUE pair
- `task_id`, `tacid`, `system_task_id`, `slice_task_id`
- `status`: `active|zombie|rubbish|deprecated|draft`
- links to scoring via module+function; never delete hit history

### 4.2 Ontology (Task 1 map)

- `onto_concept` — concept nodes (system, module, function grain)
- `onto_link` — concept→concept
- `onto_binding` — concept→channel/module/task/register
- `onto_monitor_rollup` — active vs rubbish counts (cache only)

Gate: **never**. Mark only; no auto source delete.

### 4.3 Reuse

- `task_ssot` — multi-dim SSOT per task  
- `function_invoke_trace` / `function_scoring` — Pipeline C  
- `dev_task` tree — worker plan home  

---

## 5. Phases

| Phase | Deliverable |
|-------|-------------|
| MCS0 | contracts + this plan |
| MCS1 | DDL + migrate ensure |
| MCS2 | `managed_coding.py`: profile, seed membership, register_id |
| MCS3 | wire `register_impl_function` + `managed_invoke` require register |
| MCS4 | worker clean report (active / incomplete / rubbish) |
| MCS5 | Task Center `/managed` + APIs |
| MCS6 | selftest + smoke seed |
| **MCS7** | **Layer map in plan + contracts + `/managed` UI** (ontology vs orchestrator; handoff keys) |
| **MCS8** | **Function Builder**: `fn_request` / `fn_research` / `field_tdd_rule` + research paths + UI form + `/api/managed/build` |

### 5.2 Function Builder tables (DB-driven)

| Table | Role |
|-------|------|
| `fn_request` | human request (text, channel, module, system, table, status) |
| `fn_research` | research path: reuse_table_fields / design_table_fields / extend_system |
| `field_tdd_rule` | per-field TDD JSON + depends_on + bound `register_id` |
| `code_register` | function output identity |
| `task_ssot` | multi-dim SSOT (profile A–H + `tdd.*` + builder.request_id) |
| `dev_task` / `dev_task_field` | TACID tree + field rows |

**TDD example (phone):** depends on `region`; when region=CN → `+86` + **11** digits (not 10, not 12). Stored in `field_tdd_rule.rule_json` and `task_ssot` dims `tdd.rules_json` / `tdd.depends_on`.

### 5.1 Orchestrator handoff keys (adapter)

```text
code_register.register_id
  + slice_key              (e.g. region)
  + plan.table             (member)          ← task_ssot
  + impl.function          (member_region)
  + dev_task.id            (slice TACID home)
  + channel.code           (local_pc = env, not driver)
```

Optional dims **owned by orchestrator** (may live on task_ssot later, never as MCS gate):

- `exec.driver` = `mcp` | `openclaw` | `sql` | `api`
- `exec.template_id`
- `trace.run_id` → artifacts / frames / SSOT snapshots
- `qc.pair_ref` / `qc.business_check`

---

## 6. Membership seed (example system)

```text
equation: region + phone + contact_method + name + gender (+…) = member_profile
table plan: member
channel: local_pc
module: membership
version: mem-1.0
root: task_label=1
slices: 1.1…1.5 with full A–H dims
one managed function per slice (register_id + impl.* + invoker path)
```

---

## 7. Worker rules (no unnecessary time)

1. Open **system root** or `/managed` — not raw demo noise.  
2. Only **active + incomplete** slices are work.  
3. **rubbish / zombie / deprecated** = mark, do not re-plan unless un-rubbish.  
4. Coding tasks must show **register_id** before claim “done code”.  
5. Do not create parallel labels outside version grammar.  
6. Do not wrap stdlib; do not new event_type zoo.  

---

## 8. Reject list

- wall-clock unique function names  
- code without `code_register` / `impl.register_id`  
- ontology as hard gate  
- auto-delete `.py`  
- collapsing `schema_ssot` into `task_ssot`  
- using membership labels on agent_db version (collision risk across humans, not DB)

---

## 9. Debug path

```text
symptom
  → task_label (e.g. 1.1 under mem-1.0)
  → task_ssot profile + impl
  → register_id
  → function_invoke_trace / scoring
  → onto_binding (channel+module+function)
  → active vs rubbish rollup
```
