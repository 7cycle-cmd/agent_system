# LLM Status + Queue — Evidence Report (5 records)

**Created:** 2026-09-20
**Gate:** `f_env_preflight.py` (scope `llm`) — **15/16 passed, READY**, blocking: none, advisory: `ffmpeg`
**Record JSON:** `qc_evidence/llm_status_evidence_5x_record.json`
**Proof:** `_proof_llm_status_evidence_5x.py` — **11/11 checks passed**

---

## Open each record in the UI

| # | aspect | verdict | UI URL |
|---|--------|---------|--------|
| 1 | helper | **PASS** | http://127.0.0.1:18765/llm-tasks/evidence/EVID-llm_status_helper-20260920-141653 |
| 2 | ollama | **PASS** | http://127.0.0.1:18765/llm-tasks/evidence/EVID-llm_status_ollama-20260920-141654 |
| 3 | queue | **PASS** | http://127.0.0.1:18765/llm-tasks/evidence/EVID-llm_status_queue-20260920-141654 |
| 4 | env | **PASS** | http://127.0.0.1:18765/llm-tasks/evidence/EVID-llm_status_env-20260920-141654 |
| 5 | worker | **PASS** | http://127.0.0.1:18765/llm-tasks/evidence/EVID-llm_status_worker-20260920-141654 |

**Evidence Center (all records):** http://127.0.0.1:18765/llm-tasks/evidence/list
**Filtered to these 5:** http://127.0.0.1:18765/llm-tasks/evidence/list?target=llm_status

Each URL was verified: page **HTTP 200**, detail API `ok=true`, verdict **PASS**,
3 artefacts each (`shot.png`, `classify.json`, `report.md`).

---

## What each record proves

### 1. `EVID-llm_status_helper` — the helper is reachable
- `GET /api/health` → OK
- `GET /api/task_center/queue` → `ok=true`
- **Measured:** helper answering, queue API answering

### 2. `EVID-llm_status_ollama` — the model server has the ASSIGNED model
- `GET 127.0.0.1:11434/api/tags` → 2 models
- **Measured:** `qwen2.5vl:7b`, `qwen2.5:7b-instruct`; assigned model
  `qwen2.5:7b-instruct` **present**

> This checks the *assigned* model id, not just "some model". A gate that only
> checks "ollama is up" passes while the assigned model is absent, and every
> task then fails at call time.

### 3. `EVID-llm_status_queue` — the queue is readable, with the model breakdown
- **Measured:** `total=19`
- `by_status`: pending 0 · running 0 · retry 1 · handoff 0 · success 16 · failed 2
- `by_assigned_model`: `qwen2.5:7b-instruct` → 19

### 4. `EVID-llm_status_env` — screen, foreground, evidence root
- **Measured:** screen `1920x1080`; foreground process `Code.exe`; evidence root
  exists + writable, no test noise

> `env_task_proof` Rule 3: a same-size image is not a same-content image. Size
> and hash are file properties; they cannot say what was photographed. The
> foreground process is the field that catches a capture of the wrong window.

### 5. `EVID-llm_status_worker` — the gate, end to end
- **Measured:** `gate_ready=true`, `gate_blocking=[]`
- rows `22 → 23`; task `stq_1f1e3d5a68d2`
- worker `status=success`, `model=qwen2.5:7b-instruct`, `result={"label":"question"}`

---

## The gate (runs BEFORE an LLM gets a new task)

`f_env_preflight.py` — 16 checks. `--scope llm` makes only the LLM-relevant ones
blocking:

| check | scope llm | measured |
|---|---|---|
| `helper` | **required** | GET /api/health OK |
| `queue_api` | **required** | queue API OK, total=19 |
| `queue_db` | **required** | skill_task_queue total=19 |
| `ollama` | **required** | 2 models |
| `model_present` | **required** | `qwen2.5:7b-instruct` present |
| `screen` | **required** | 1920x1080 |
| `foreground` | **required** | `Code.exe` |
| `contracts` | **required** | 17 contracts, 97 TDD cases green |
| `evidence_root` | **required** | 30 folders, no test noise |
| `gpu` / `cuda` | advisory | RTX 5060 Ti / torch 2.11.0+cu128 |
| `comfyui_server` / `comfyui_model` | advisory | port 8188 / ltxv-2b visible |
| `ffmpeg` | **advisory** | **NOT on PATH** |
| `cdp_browser` | advisory | CDP 9222 OK |
| `incidents` | advisory | 0 incidents |

**Enforcement** (`env_task_proof` Rule 6 — a recorded check is not a gate):

```
POST /api/task_center/enqueue   scope=llm    -> 200 + row
POST /api/task_center/enqueue   scope=video  -> HTTP 409, rows 22 -> 22 (unchanged)
```

---

## Caveats — read these before quoting any number

1. **`status=success` is a FORMAT gate, not a correctness gate.** It means the
   output satisfied the schema, **not** that it was true. Measured 2026-09-20:
   asked for a port in a sentence with no port, the 7B answered `{"port": 0}` and
   the row was recorded **success**. A malformed answer *is* refused into retry.
2. **`ffmpeg` is genuinely absent** on this machine, so `--scope video` and
   `--scope all` are correctly **NOT READY**. That is a true finding, not a bug.
3. **n=5 aspects, one record each.** This proves the environment was *measured*
   before a task — it is not a certification of model accuracy.
4. **Nothing schedules `worker_once()`.** The queue only moves when a human runs
   it, so "the queue is working" means "it works when invoked", not "it runs 24/7".
5. **Browser DOM verification was not performed** for the UI panel (CDP
   `connectOverCDP` times out). Verified statically: bundle contains the panel
   code, route HTTP 200, evidence detail API returns the records.

---

## Reproduce

```powershell
# the gate, scoped to an LLM task
.\.venv\Scripts\python.exe f_env_preflight.py --scope llm

# the gate's own mutation proof (proves it REFUSES, and catches its removal)
.\.venv\Scripts\python.exe _proof_preflight_gate.py

# write the 5 evidence records
.\.venv\Scripts\python.exe _proof_llm_status_evidence_5x.py
```
