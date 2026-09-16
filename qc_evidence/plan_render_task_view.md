# PLAN — render_task_view

**Mode:** PLAN (frozen)  
**task_id:** `render_task_view`  
**checklist_id:** `qc.render_task_view.v1`

## Scope

Create `render_task_view.py`; scan `qc_evidence/` JSON triples; write static root `task_view.html`. CLI `--render` / `--open` / `--help`. No DB. No JS. No edits to foreign artifacts or `ingest_task_log.py`.

## Step list

1. Implement `render_task_view.py` (discover, merge, HTML, argparse)
2. Run `--help` and `--render`
3. Verify HTML columns, verdict CSS, marker ingested flags, no script/sqlite
4. Write agent_log twins; stop for QC

## Locked checklist

QC-01 … QC-15 per approved plan `qc.render_task_view.v1` — frozen.
