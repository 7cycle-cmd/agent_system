"""Phone 100 連勝 Proof v2 — multi-model orchestration runner (Step 4-5).

Runs the candidate pool in order (7b -> deepseek) against the SAME rule_version
with a FRESH streak per model. On QUALIFIED: writes assignment + field_tdd_rule
active:true + proof. On NOT_QUALIFIED: creates a HANDOFF task at task center
(dev_task + task_ssot dims + lifecycle validation_fail) with suggested_model =
next candidate. Pool exhausted without QUALIFIED -> NOT_QUALIFIED_ALL; job stays
NOT active; report (no infinite loop).

This is the orchestration layer ONLY. Per-round evidence stays in llm_100_run
(pure per-round). suggested_model / from_model / cause / fail_evidence_ref live
on the handoff task's task_ssot, NOT on round rows.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

from llm_100_run_harness import (
    DEFAULT_MODEL,
    JUDGE_SKILL_ID,
    run_harness,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "agent.db"
QC_EVIDENCE_DIR = BASE_DIR / "qc_evidence"

# Candidate pool order (Step 4). Pool exhausted -> NOT_QUALIFIED_ALL.
CANDIDATE_POOL = ["qwen2.5:7b-instruct", "deepseek-v4-flash"]

# task_ssot dim keys for handoff (Step 4.3)
DIM_FROM_MODEL = "from_model"
DIM_SUGGESTED_MODEL = "suggested_model"
DIM_CAUSE = "cause"
DIM_FAIL_EVIDENCE_REF = "fail_evidence_ref"
DIM_JOB_REF = "job_ref"
DIM_VERDICT = "verdict"

CAUSE_MODEL_GAP = "model_capability_gap"
CAUSE_SPEC_ISSUE = "spec_level_issue"  # all models fail same class -> report, no handoff

# lifecycle / task-center wiring
CHANNEL_CODE = "local_pc"
MODULE_CODE = "agent_db"
ACTION_CODE = "capability.ssot"
VERSION_LABEL = "1.1"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_task_center(conn: sqlite3.Connection) -> dict[str, int]:
    """Resolve (or create) channel/module/version/action ids for dev_task creation.

    Creates the minimal dims directly instead of calling seed_task_center_defaults
    (which has many hidden table dependencies). Idempotent.
    """
    from db_schema import (
        CHANNEL_DDL,
        MODULE_DDL,
        TASK_ACTION_NAME_DDL,
        VERSION_CENTER_DDL,
        DEV_TASK_DDL,
        TASK_SSOT_DDL,
        FIELD_TDD_RULE_DDL,
    )

    conn.executescript(CHANNEL_DDL)
    conn.executescript(MODULE_DDL)
    conn.executescript(TASK_ACTION_NAME_DDL)
    conn.executescript(VERSION_CENTER_DDL)
    conn.executescript(DEV_TASK_DDL)
    conn.executescript(TASK_SSOT_DDL)
    conn.executescript(FIELD_TDD_RULE_DDL)

    def _get_or_create(table: str, code: str, name: str) -> int:
        row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            f"INSERT INTO {table} (code, name) VALUES (?, ?)", (code, name)
        )
        return int(cur.lastrowid)

    channel_id = _get_or_create("channel", CHANNEL_CODE, "Local PC")
    module_id = _get_or_create("module", MODULE_CODE, "Agent DB / Schema")

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        ORDER BY id DESC LIMIT 1
        """,
        (channel_id, module_id, VERSION_LABEL),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (channel_id, module_id, VERSION_LABEL, "Phone 100 proof", "proof runner"),
        )
        version_id = int(cur.lastrowid)

    action = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ? ORDER BY id LIMIT 1",
        (ACTION_CODE,),
    ).fetchone()
    if action:
        action_id = int(action[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO task_action_name
                (element, action, code, name, requires_tdd, status)
            VALUES ('capability', 'ssot', ?, 'Capability multi-dim SSOT', 0, 'active')
            """,
            (ACTION_CODE,),
        )
        action_id = int(cur.lastrowid)

    conn.commit()
    return {
        "channel_id": channel_id,
        "module_id": module_id,
        "version_id": version_id,
        "action_id": action_id,
    }


def _write_qc_evidence(model: str, result: dict[str, Any]) -> Path:
    """Write qc_evidence/phone_100_run_<model>.json (Step 5)."""
    QC_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    safe = model.replace("/", "_").replace(":", "_")
    path = QC_EVIDENCE_DIR / f"phone_100_run_{safe}.json"
    payload = {
        "skill_id": JUDGE_SKILL_ID,
        "model": model,
        "seed": result.get("seed"),
        "rounds_run": result.get("rounds_run"),
        "max_streak": result.get("max_streak"),
        "final_streak": result.get("final_streak"),
        "rule_version": result.get("rule_version"),
        "verdict": result.get("verdict"),
        "failures": result.get("failures", []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _create_handoff_task(
    conn: sqlite3.Connection,
    *,
    ids: dict[str, int],
    from_model: str,
    suggested_model: str,
    cause: str,
    fail_evidence_ref: str,
    linked_trace_id: str | None,
) -> dict[str, Any]:
    """Create a HANDOFF dev_task + task_ssot dims + lifecycle validation_fail."""
    from db_schema import create_dev_task, upsert_task_ssot

    label = f"1.1-PHONE-100RUN-{from_model.replace(':', '_').replace('/', '_')}"
    title = f"Phone 100-run handoff: {from_model} NOT_QUALIFIED -> {suggested_model}"
    task = create_dev_task(
        conn,
        channel_id=ids["channel_id"],
        module_id=ids["module_id"],
        version_id=ids["version_id"],
        action_name_id=ids["action_id"],
        task_label=label,
        title=title[:240],
        payload_json={
            "job": "phone-judge",
            "ref_tag": "1.1F",
            "skill_id": JUDGE_SKILL_ID,
            "from_model": from_model,
            "suggested_model": suggested_model,
            "cause": cause,
            "fail_evidence_ref": fail_evidence_ref,
        },
        status="pending",
    )
    task_id = int(task["id"])

    dims = {
        DIM_JOB_REF: "1.1F",
        DIM_FROM_MODEL: from_model,
        DIM_SUGGESTED_MODEL: suggested_model,
        DIM_CAUSE: cause,
        DIM_FAIL_EVIDENCE_REF: fail_evidence_ref,
        DIM_VERDICT: "NOT_QUALIFIED",
    }
    for i, (key, val) in enumerate(dims.items()):
        upsert_task_ssot(
            conn,
            task_id=task_id,
            dim_key=key,
            value_text=str(val),
            value_type="string",
            source="phone_100_proof_runner",
            sort_order=i,
            commit=False,
        )
    conn.commit()

    # lifecycle validation_fail (rollback to proposal_draft; linked trace must exist)
    try:
        from src.task_center.lifecycle_log import append_lifecycle_event

        append_lifecycle_event(
            {
                "task_id": str(task_id),
                "event_type": "validation_fail",
                "task_state": "proposal_draft",
                "skill_id": JUDGE_SKILL_ID,
                "linked_trace_id": linked_trace_id,
                "event_summary": f"{from_model} NOT_QUALIFIED (streak cap); handoff to {suggested_model}",
                "event_detail": {
                    "cause": cause,
                    "from_model": from_model,
                    "suggested_model": suggested_model,
                    "fail_evidence_ref": fail_evidence_ref,
                },
                "actor": "phone_100_proof_runner",
            },
            conn=conn,
            commit=True,
        )
    except Exception as e:  # lifecycle is append-only audit; never a gate
        task["lifecycle_warning"] = f"{type(e).__name__}: {e}"

    return task


def _mark_qualified(
    conn: sqlite3.Connection,
    *,
    model: str,
    result: dict[str, Any],
    evidence_ref: str,
    linked_trace_id: str | None,
) -> dict[str, Any]:
    """Step 4.2: assignment + field_tdd_rule active:true + proof + lifecycle."""
    from db_schema import upsert_task_ssot

    # field_tdd_rule id=16 -> active:true + proof (Step 4.2)
    rule_id = 16
    row = conn.execute(
        "SELECT id, rule_json FROM field_tdd_rule WHERE id = ?", (rule_id,)
    ).fetchone()
    if not row:
        # id=16 may not exist on a fresh DB; locate by slice_key/field_name or create.
        row = conn.execute(
            """
            SELECT id, rule_json FROM field_tdd_rule
            WHERE slice_key = ? AND field_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            ("1.1F", "phone"),
        ).fetchone()
    if row:
        rule_id = int(row[0])
        try:
            rule = json.loads(row["rule_json"] or "{}")
        except Exception:
            rule = {}
    else:
        rule = {}
    rule["active"] = True
    rule["proof"] = {
        "model": model,
        "rule_version": result.get("rule_version"),
        "streak": result.get("max_streak"),
        "seed": result.get("seed"),
        "evidence_ref": evidence_ref,
    }
    if row:
        conn.execute(
            "UPDATE field_tdd_rule SET rule_json = ?, status = 'active', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (json.dumps(rule, ensure_ascii=False), rule_id),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO field_tdd_rule
                (system_key, slice_key, field_name, tdd_type_code, rule_json,
                 depends_on_json, status, notes, source)
            VALUES (?, ?, ?, 'text', ?, '[]', 'active', ?, 'phone_100_proof_runner')
            """,
            (
                "phone_100_judge",
                "1.1F",
                "phone",
                json.dumps(rule, ensure_ascii=False),
                f"Phone 100-run proof: {model} QUALIFIED",
            ),
        )
        rule_id = int(cur.lastrowid)
    conn.commit()

    # task_ssot assignment row (job=phone-judge, model, verdict=QUALIFIED)
    # Reuse the handoff task if one exists for this model, else create a fresh one.
    label = f"1.1-PHONE-100RUN-{model.replace(':', '_').replace('/', '_')}"
    task = conn.execute(
        "SELECT id FROM dev_task WHERE task_label = ? ORDER BY id DESC LIMIT 1",
        (label,),
    ).fetchone()
    if task:
        task_id = int(task[0])
    else:
        ids = _ensure_task_center(conn)
        from db_schema import create_dev_task

        created = create_dev_task(
            conn,
            channel_id=ids["channel_id"],
            module_id=ids["module_id"],
            version_id=ids["version_id"],
            action_name_id=ids["action_id"],
            task_label=label,
            title=f"Phone 100-run QUALIFIED: {model}"[:240],
            payload_json={
                "job": "phone-judge",
                "ref_tag": "1.1F",
                "skill_id": JUDGE_SKILL_ID,
                "model": model,
                "verdict": "QUALIFIED",
            },
            status="pass",
        )
        task_id = int(created["id"])

    dims = {
        DIM_JOB_REF: "1.1F",
        DIM_FROM_MODEL: model,
        DIM_SUGGESTED_MODEL: model,
        DIM_CAUSE: "qualified",
        DIM_FAIL_EVIDENCE_REF: evidence_ref,
        DIM_VERDICT: "QUALIFIED",
    }
    for i, (key, val) in enumerate(dims.items()):
        upsert_task_ssot(
            conn,
            task_id=task_id,
            dim_key=key,
            value_text=str(val),
            value_type="string",
            source="phone_100_proof_runner",
            sort_order=i,
            commit=False,
        )
    conn.commit()

    try:
        from src.task_center.lifecycle_log import append_lifecycle_event

        append_lifecycle_event(
            {
                "task_id": str(task_id),
                "event_type": "validation_pass",
                "task_state": "validated",
                "skill_id": JUDGE_SKILL_ID,
                "linked_trace_id": linked_trace_id,
                "event_summary": f"{model} QUALIFIED (streak {result.get('max_streak')})",
                "event_detail": {
                    "model": model,
                    "rule_version": result.get("rule_version"),
                    "streak": result.get("max_streak"),
                    "seed": result.get("seed"),
                    "evidence_ref": evidence_ref,
                },
                "actor": "phone_100_proof_runner",
            },
            conn=conn,
            commit=True,
        )
    except Exception as e:
        pass

    return {"task_id": task_id, "rule_id": rule_id}


def run_proof(
    *,
    seed: int = 7,
    ref_tag: str = "1.1F",
    entity_name: str = "phone",
    entity_type: str = "field",
    cap: int = 1000,
    db_path: Path | str | None = None,
    llm: bool = True,
    linked_trace_id: str | None = None,
    pool: list[str] | None = None,
) -> dict[str, Any]:
    """Run the candidate pool in order; stop on first QUALIFIED.

    Returns a report dict. Never loops forever: pool is finite, and if ALL models
    fail the SAME class under instruction v2, the caller reports a spec-level
    issue instead of handing off (cause=spec_level_issue).
    """
    path = Path(db_path) if db_path else DB_PATH
    pool = [m for m in (pool or CANDIDATE_POOL) if m]
    conn = _connect(path)
    ids = _ensure_task_center(conn)
    results: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    qualified: dict[str, Any] | None = None

    try:
        for idx, model in enumerate(pool):
            result = run_harness(
                seed=seed,
                ref_tag=ref_tag,
                entity_name=entity_name,
                entity_type=entity_type,
                model=model,
                cap=cap,
                db_path=path,
                llm=llm,
                linked_trace_id=linked_trace_id,
            )
            evidence_ref = str(_write_qc_evidence(model, result))
            result["evidence_ref"] = evidence_ref
            results.append(result)

            if result["verdict"] == "QUALIFIED":
                qualified = _mark_qualified(
                    conn,
                    model=model,
                    result=result,
                    evidence_ref=evidence_ref,
                    linked_trace_id=linked_trace_id,
                )
                qualified["model"] = model
                qualified["evidence_ref"] = evidence_ref
                break

            # NOT_QUALIFIED -> handoff to next candidate (if any)
            next_model = pool[idx + 1] if idx + 1 < len(pool) else None
            if next_model:
                task = _create_handoff_task(
                    conn,
                    ids=ids,
                    from_model=model,
                    suggested_model=next_model,
                    cause=CAUSE_MODEL_GAP,
                    fail_evidence_ref=evidence_ref,
                    linked_trace_id=linked_trace_id,
                )
                handoffs.append(
                    {
                        "from_model": model,
                        "suggested_model": next_model,
                        "task_id": task.get("id"),
                        "task_label": task.get("task_label"),
                        "evidence_ref": evidence_ref,
                    }
                )
            # else: pool exhausted -> NOT_QUALIFIED_ALL (handled below)

        verdict = "QUALIFIED" if qualified else "NOT_QUALIFIED_ALL"
        return {
            "ok": True,
            "verdict": verdict,
            "qualified": qualified,
            "results": results,
            "handoffs": handoffs,
            "pool": pool,
            "seed": seed,
            "ref_tag": ref_tag,
            "rule_version": results[-1]["rule_version"] if results else None,
            "note": (
                "ALL candidates NOT_QUALIFIED. If all failed the SAME class under "
                "instruction v2, treat as spec-level issue (do NOT keep handing off)."
                if verdict == "NOT_QUALIFIED_ALL"
                else None
            ),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    llm_flag = (sys.argv[2].lower() in ("1", "true", "yes")) if len(sys.argv) > 2 else True
    out = run_proof(seed=seed, llm=llm_flag)
    print(json.dumps(
        {k: v for k, v in out.items() if k != "results"},
        ensure_ascii=False, indent=2,
    ))
    for r in out["results"]:
        print(
            f"model={r['model']} verdict={r['verdict']} rounds={r['rounds_run']} "
            f"max_streak={r['max_streak']} evidence={r.get('evidence_ref')}"
        )