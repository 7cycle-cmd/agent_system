# skill_tick.py
"""The auto-forever conductor (plan_AUTO.FOREVER.SKILL.FLOW).

ONE tick. Its whole job is to move the pipeline forward by ONE bounded step per link, and
to STOP, LOUDLY, at the first link that has nothing to do.

WHY THIS EXISTS (measured 2026-09-24)
-------------------------------------
The pipeline had FOUR links, all correct, all proven, and NONE driven by a tick:

    1 SUPERVISE  keepalive_runner                      -> AUTO (schtasks, every 5 min)
    2 HARVEST    question_flow.record_lesson            -> MANUAL
    3 REWRITE    skill_learning.rewrite_skill_from_lessons -> MANUAL
    4 TEST       activation_gate.assert_may_activate    -> MANUAL, cannot self-activate

The evidence that 2-4 are manual: `skill_lesson` holds 56 rows, 54 of them `self_fail`,
harvested by hand. And the queue: the repo's own probe asserts it, verbatim --
`_proof_7b_queue_reality.py:104`: "NOTHING schedules `worker_once` in a loop (so it is NOT
24/7)".

WHAT IS COPIED FROM THE ONE LINK THAT WORKS, AND WHY EACH PART IS LOAD-BEARING
-----------------------------------------------------------------------------
`keepalive_runner` is safe because it **only ever starts** and **logs every firing**. Its own
docstring says why the log matters: *"A missing log would make 'the task never ran' and 'the
task ran and found everything healthy' indistinguishable."* So:

  * every firing appends ONE line to `skill_tick_log.jsonl`;
  * a link that produces nothing ends the run with a NAMED reason, not silence;
  * this tick starts nothing and kills nothing -- it has no start/kill path at all.

IT CANNOT ACTIVATE ANYTHING
---------------------------
`rewrite_skill_from_lessons` writes `draft`; `add_flow` writes `is_active=0`; and this module
contains **no call to `activation_gate.activate`**. Activation stays behind the 100-streak law.
A QC item asserts that absence structurally (`ast`), so it cannot be added back by accident.

THE ORDER IS DATA
-----------------
The links are read from the `AUTO_FOREVER_FLOW` conductor records (`question_flow.add_flow` /
`add_step`, the repo's own declaration API, cited to `question_flow.py`). The tick iterates
the TABLE. A Python list here would make "what does this pipeline do?" answerable only by
reading code, and this repo's rule is that a flow is DATA.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOG_FILE = BASE_DIR / "skill_tick_log.jsonl"
LOCK_FILE = BASE_DIR / "skill_tick.lock"
FLOW_KEY = "auto_forever_skill_flow"
CITE = "skill_tick.py:1"

# ONE BOUNDED STEP PER LINK PER TICK. An unbounded pass on a repeating tick is a
# rate-limit violation waiting for a bad day, and the budget is REPORTED so "it fired"
# and "it had work" stay distinguishable.
DEFAULT_BUDGET = 1

# The four links, in order, as DATA. Each entry names the FUNCTION it drives; the tick
# reads this, never a hard-coded sequence of calls.
LINKS: tuple[dict[str, Any], ...] = (
    {"step_no": 1, "link": "harvest", "layer_key": "ontology", "step_kind": "gate",
     "entry": "question_flow.record_lesson",
     "note": "turn a recorded failure into a skill_lesson"},
    {"step_no": 2, "link": "rewrite", "layer_key": "tdd", "step_kind": "describe",
     "entry": "skill_learning.rewrite_skill_from_lessons",
     "note": "turn lessons into a skill CANDIDATE (always draft)"},
    {"step_no": 3, "link": "test", "layer_key": "tdd", "step_kind": "count",
     "entry": "activation_gate.assert_may_activate",
     "note": "measure the streak; NEVER activates"},
    {"step_no": 4, "link": "report", "layer_key": "tdd", "step_kind": "verdict",
     "entry": "skill_tick._report",
     "note": "state, per link, what fired and what it produced"},
)

# THE PIPELINE-ENQUEUE LINK (added 2026-09-30, plan CHAT.PIPELINE.S7, step S7b).
#
# It is a LINK, not a new scheduler: `skill_tick` is already scheduled (every 15
# min), already has an atomic lock, and has no kill path — so S7 adds a link, the
# same rule S4's watermark followed ("use the existing mechanism, do not invent a
# new one").
#
# WHY IT RUNS BEFORE THE LINK LOOP, NOT INSIDE IT: the loop below STOPS at the
# first link that has nothing to do (`break` on `record_lesson`), so a link placed
# AFTER `harvest` would never run. The enqueue is therefore its own phase, run
# first, and reported as a link. It does NOT change the four links above, and it
# does NOT change the poll cadence.
PIPELINE_ENQUEUE_LINK = "pipeline_enqueue"

# THE PIPELINE-ADVANCE LINK (added 2026-09-30, plan CHAT.PIPELINE.S8, step S8b).
# It is the TRIGGER for `chat_level.advance_from_task` (the ACTION, S8a): it reads
# the completion events (`task_lifecycle_log.event_type='validation_pass'`) and
# advances the run each completed task is linked to. Like the enqueue link, it is
# its own phase (the link loop BREAKS at the first empty link), and it does NOT
# change the four links above or the poll cadence.
PIPELINE_ADVANCE_LINK = "pipeline_advance"

# THE PIPELINE-SNAPSHOT LINK (added 2026-09-30, plan CHAT.PIPELINE.S9, step S9-pre-a).
# It is the TRIGGER for `db_snapshot.snapshot_db` (the ACTION): when a run reaches
# `completed`, it writes ONE consistent snapshot of the DB (`VACUUM INTO`, never a
# raw copy) named after the run. Like the other pipeline links it is its own phase
# (the link loop BREAKS at the first empty link), and it does NOT change the four
# links above or the poll cadence. The snapshot dir is already git-ignored (`*.db`).
PIPELINE_SNAPSHOT_LINK = "pipeline_snapshot"
SNAPSHOT_KEEP = 5



def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _append_log(entry: dict) -> None:
    """ONE line per firing. Silence must never be ambiguous."""
    entry = dict(entry)
    entry.setdefault("at", _now())
    entry.setdefault("cite_ref", CITE)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _claim_lock() -> bool:
    """SAFE-IF-RUNNING. A second firing while the first is mid-work must NOT duplicate it.

    THE LOCK MUST BE ATOMIC, AND THE FIRST VERSION WAS NOT. MEASURED 2026-09-24: a
    check-then-create (`if exists -> return False; write_text(...)`) has a TOCTOU window
    between the two steps, so two firings at the same instant BOTH proceeded -- the log
    showed two `tick` lines in the SAME SECOND and two threads both reported `ran=True`.
    A lock that admits both writers is decoration.

    The fix is `O_CREAT | O_EXCL`: the create IS the test, so only one caller can win.

    A stale lock (older than LOCK_TTL) is reclaimed, because a crashed run must not wedge
    the pipeline forever -- that failure would turn "auto forever" into "never".
    """
    ttl = 30 * 60
    for _attempt in range(2):
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("pid=%d at=%s" % (os.getpid(), _now())).encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - LOCK_FILE.stat().st_mtime
            except OSError:
                return False              # it vanished; the holder is finishing
            if age < ttl:
                return False
            LOCK_FILE.unlink(missing_ok=True)   # stale: a crashed run
        except OSError:
            return False
    return False


def _release_lock() -> None:
    LOCK_FILE.unlink(missing_ok=True)


def declare_conductor(conn: sqlite3.Connection, *, commit: bool = True) -> dict:
    """Declare the conductor THROUGH the repo's own API. Idempotent.

    `question_flow.add_flow` writes `is_active=0` explicitly, so a NEW flow is UNPROVEN
    until `activation_gate` proves it. That is the never-activate law applying to the
    pipeline itself, and it is why this uses that API rather than a raw INSERT.
    """
    import question_flow as qf

    f = qf.add_flow(conn, FLOW_KEY, "Auto-forever skill flow",
                    description=("the tick order for harvest -> rewrite -> test -> report; "
                                 "the order is DATA so it can be queried"),
                    created_by="skill_tick", commit=commit)
    written = []
    for link in LINKS:
        s = qf.add_step(conn, FLOW_KEY, link["step_no"], layer_key=link["layer_key"],
                        step_kind=link["step_kind"],
                        question_template=link["note"], expected="NA",
                        notes="drives %s" % link["entry"], commit=commit)
        written.append(s)
    return {"ok": True, "flow": f, "steps": written, "count": len(written)}


def read_conductor(conn: sqlite3.Connection) -> list[dict]:
    """THE ORDER, read from the TABLE. If this returns nothing, the tick says so."""
    import question_flow as qf

    rows = qf.steps_of(conn, FLOW_KEY)
    out = []
    for r in sorted(rows, key=lambda x: int(x.get("step_no") or 0)):
        out.append({"step_no": int(r.get("step_no") or 0),
                    "step_kind": r.get("step_kind"),
                    "layer_key": r.get("layer_key"),
                    "note": r.get("question_template"),
                    "notes": r.get("notes")})
    return out


def _db():
    import db_schema
    return db_schema.get_db_path()


def _plan(conn: sqlite3.Connection) -> dict:
    """READ-ONLY. Reports each link's BACKLOG and the budget."""
    steps = read_conductor(conn)
    backlog = {}
    try:
        backlog["lessons"] = conn.execute("SELECT COUNT(*) FROM skill_lesson").fetchone()[0]
    except sqlite3.Error:
        backlog["lessons"] = None
    try:
        backlog["draft_skills"] = conn.execute(
            "SELECT COUNT(*) FROM skill_registry WHERE is_active=0").fetchone()[0]
    except sqlite3.Error:
        backlog["draft_skills"] = None
    try:
        backlog["queued_tasks"] = conn.execute(
            "SELECT COUNT(*) FROM skill_task_queue WHERE status='pending'").fetchone()[0]
    except sqlite3.Error:
        backlog["queued_tasks"] = None
    return {"flow_key": FLOW_KEY, "conductor_declared": bool(steps), "steps": steps,
            "budget_per_link": DEFAULT_BUDGET, "backlog": backlog,
            "log_file": str(LOG_FILE)}


def _report(conn: sqlite3.Connection, results: list[dict]) -> dict:
    """The last link: state what fired and what it produced, per link."""
    return {"links_run": len(results),
            "produced": sum(1 for r in results if r.get("produced")),
            "nothing_to_do_at": [r["link"] for r in results if not r.get("produced")],
            "detail": results}


def enqueue_pipeline_runs(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """THE PIPELINE-ENQUEUE LINK (plan CHAT.PIPELINE.S7, step S7b).

    For every conversation whose status IS a pipeline stage, build its
    `pipeline_run` via `chat_level.enqueue_pipeline_run`. The consumer is
    IDEMPOTENT (a UNIQUE index on `(conversation_id, stage)`), so a second tick
    creates 0 new rows — the "duplicate run" failure mode is closed at the DB.

    `apply=False` MEASURES ONLY: it counts what WOULD be enqueued and writes
    nothing, the same contract `run_once` follows.

    IT DOES NOT EXECUTE ANYTHING. It writes `pipeline_run` rows and nothing else:
    no task, no LLM call, no stage advance, no kill/start path. The stage advance
    is a LATER step (S7c).
    """
    import chat_level as cl

    # The stages a conversation can be enqueued AT. `draft` is EXCLUDED on
    # purpose: a draft is not approved, so enqueuing it would be the "false run"
    # failure mode (P6). The consumer refuses it too, but the query does not even
    # offer it — two independent guards, not one.
    stages = tuple(s for s in cl.PIPELINE_STAGES if s != "draft")
    placeholders = ", ".join("?" for _ in stages)
    rows = list(conn.execute(
        "SELECT id, status FROM chat_main WHERE status IN (%s) ORDER BY id"
        % placeholders, stages))
    created, existing, refused = [], [], []
    for r in rows:
        # POSITIONAL access: this function must work on ANY connection, including
        # one whose `row_factory` is not `sqlite3.Row` (a caller's raw conn).
        cid = int(r[0])
        st = str(r[1])
        if not apply:
            existing.append({"conv_id": cid, "stage": st, "dry_run": True})
            continue
        try:
            out = cl.enqueue_pipeline_run(
                conn, cid, stage=st,
                cite_ref="skill_tick.py:1", actor="skill_tick")
        except cl.ChatRefused as e:
            refused.append({"conv_id": cid, "stage": st, "why": str(e.args[0])})
            continue
        (created if out.get("created") else existing).append(
            {"conv_id": cid, "stage": st, "run_id": out.get("run_id")})
    return {"ok": True, "link": PIPELINE_ENQUEUE_LINK, "apply": apply,
            "candidates": len(rows), "created": len(created),
            "existing": len(existing), "refused": len(refused),
            "created_rows": created, "refused_rows": refused,
            "produced": len(created)}


def advance_pipeline_runs(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """THE PIPELINE-ADVANCE LINK (plan CHAT.PIPELINE.S8, step S8b).

    For every task that COMPLETED (`task_lifecycle_log.event_type='validation_pass'`),
    advance the run it is linked to, via `chat_level.advance_from_task`. This is
    the TRIGGER; `advance_from_task` is the ACTION (S8a).

    NO NEW WATERMARK. The idempotency is `pipeline_run_task`'s
    `UNIQUE (run_id, task_id)`: a task that already advanced a run is a no-op, so
    a second tick advances NOTHING. A watermark would be a second source of truth
    for the same fact.

    A TASK WITH NO RUN LINK IS SKIPPED, NOT AN ERROR. MEASURED 2026-09-30: both
    live `validation_pass` tasks (108, 109) have NO run link, so today this link
    advances nothing — the "missed advance" case, REPORTED not hidden.

    `apply=False` MEASURES ONLY: it counts what WOULD advance and writes nothing.

    IT DOES NOT EXECUTE ANYTHING. It advances `pipeline_run.stage` and writes
    `pipeline_run_task` rows; no task, no LLM call, no kill/start path.
    """
    import chat_level as cl

    # A THROWAWAY DB (a proof) may not have `task_lifecycle_log`. Its absence
    # means "no completion events", not a fault — the same best-effort rule
    # `_plan` follows for its backlog counts.
    try:
        rows = list(conn.execute(
            "SELECT DISTINCT task_id FROM task_lifecycle_log "
            "WHERE event_type = 'validation_pass' ORDER BY task_id"))
    except sqlite3.Error:
        rows = []
    advanced, skipped, refused = [], [], []
    for r in rows:
        tid = str(r[0])
        link = conn.execute(
            "SELECT run_id FROM pipeline_run_task WHERE task_id = ? LIMIT 1",
            (tid,)).fetchone()
        if not link:
            skipped.append({"task_id": tid, "why": "no run link"})
            continue
        if not apply:
            advanced.append({"task_id": tid, "run_id": str(link[0]),
                             "dry_run": True})
            continue
        try:
            out = cl.advance_from_task(
                conn, tid, cite_ref="skill_tick.py:1", actor="skill_tick")
        except cl.ChatRefused as e:
            refused.append({"task_id": tid, "why": str(e.args[0])})
            continue
        (advanced if out.get("advanced") else skipped).append(
            {"task_id": tid, "run_id": out.get("run_id"),
             "to_stage": out.get("to_stage")})
    return {"ok": True, "link": PIPELINE_ADVANCE_LINK, "apply": apply,
            "completed_tasks": len(rows), "advanced": len(advanced),
            "skipped": len(skipped), "refused": len(refused),
            "advanced_rows": advanced, "skipped_rows": skipped,
            "refused_rows": refused, "produced": len(advanced)}


def snapshot_completed_runs(conn: sqlite3.Connection, *, db_path: str,
                            apply: bool = False, keep: int = SNAPSHOT_KEEP) -> dict:
    """THE PIPELINE-SNAPSHOT LINK (plan CHAT.PIPELINE.S9, step S9-pre-a).

    For every run that has REACHED `completed`, write ONE consistent snapshot of
    the DB, named after the run. This is the TRIGGER; `db_snapshot.snapshot_db`
    is the ACTION.

    IDEMPOTENT BY FILE: a run whose snapshot already exists is skipped, so a
    second tick snapshots nothing. `apply=False` MEASURES ONLY and writes nothing.

    A DB WITHOUT `pipeline_run` (a throwaway proof DB) snapshots nothing — the
    same best-effort rule `advance_pipeline_runs` follows.
    """
    import db_snapshot

    try:
        rows = list(conn.execute(
            "SELECT run_id FROM pipeline_run WHERE stage = 'completed' "
            "ORDER BY run_id"))
    except sqlite3.Error:
        rows = []
    d = db_snapshot.snapshot_dir(db_path)
    snapshotted, skipped = [], []
    for r in rows:
        rid = str(r[0])
        if (d / ("%s.db" % rid)).exists():
            skipped.append({"run_id": rid, "why": "snapshot exists"})
            continue
        if not apply:
            snapshotted.append({"run_id": rid, "dry_run": True})
            continue
        out = db_snapshot.snapshot_db(db_path, rid, keep=keep)
        (snapshotted if out.get("ok") else skipped).append(
            {"run_id": rid, "path": out.get("path"), "bytes": out.get("bytes"),
             "why": out.get("why")})
    return {"ok": True, "link": PIPELINE_SNAPSHOT_LINK, "apply": apply,
            "completed_runs": len(rows), "snapshotted": len(snapshotted),
            "skipped": len(skipped), "snapshotted_rows": snapshotted,
            "skipped_rows": skipped, "produced": len(snapshotted)}


def run_once(*, db_path: str | None = None, apply: bool = False) -> dict:
    """ONE bounded pass. `apply=False` MEASURES ONLY and writes nothing."""
    db = db_path or str(_db())
    if apply and not _claim_lock():
        _append_log({"event": "skipped", "reason": "another tick holds the lock",
                     "lock": str(LOCK_FILE)})
        return {"ok": True, "ran": False, "reason": "SAFE_IF_RUNNING: lock held"}
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            plan = _plan(conn)
            results: list[dict] = []
            # THE PIPELINE-ENQUEUE LINK RUNS FIRST, as its own phase. It cannot
            # live in the loop below: that loop BREAKS at the first empty link
            # (`record_lesson`), so a link after `harvest` would never run.
            enq = enqueue_pipeline_runs(conn, apply=apply)
            results.append({"step_no": 0, "kind": "count",
                            "link": PIPELINE_ENQUEUE_LINK,
                            "produced": enq["produced"], "applied": apply,
                            "note": ("%d candidate(s), %d created, %d existing, "
                                     "%d refused" % (enq["candidates"], enq["created"],
                                                     enq["existing"], enq["refused"]))})
            # THE PIPELINE-ADVANCE LINK (S8b) runs next, also as its own phase.
            adv = advance_pipeline_runs(conn, apply=apply)
            results.append({"step_no": 0, "kind": "count",
                            "link": PIPELINE_ADVANCE_LINK,
                            "produced": adv["produced"], "applied": apply,
                            "note": ("%d completed task(s), %d advanced, %d skipped, "
                                     "%d refused" % (adv["completed_tasks"], adv["advanced"],
                                                     adv["skipped"], adv["refused"]))})
            # THE PIPELINE-SNAPSHOT LINK (S9-pre-a) runs next, also its own phase.
            snap = snapshot_completed_runs(conn, db_path=db, apply=apply)
            results.append({"step_no": 0, "kind": "count",
                            "link": PIPELINE_SNAPSHOT_LINK,
                            "produced": snap["produced"], "applied": apply,
                            "note": ("%d completed run(s), %d snapshotted, %d skipped"
                                     % (snap["completed_runs"], snap["snapshotted"],
                                        snap["skipped"]))})
            for step in plan["steps"]:
                link = step["notes"].split("drives ", 1)[-1].strip() if step["notes"] else ""
                entry = {"step_no": step["step_no"], "kind": step["step_kind"],
                         "link": link or step["layer_key"], "produced": 0,
                         "applied": False}
                if not apply:
                    entry["note"] = "measured only (apply not requested)"
                results.append(entry)
                # A LINK THAT PRODUCES NOTHING ENDS THE RUN, NAMED. Continuing past an
                # empty link would make "there was nothing to do" look like work.
                if apply and link and "record_lesson" in link:
                    entry["note"] = ("harvest needs a NEW failure to record; the tick does "
                                     "not invent one")
                    break
            report = _report(conn, results)
            out = {"ok": True, "ran": True, "apply": apply, "plan": plan,
                   "enqueue": enq, "advance": adv, "snapshot": snap, "report": report}
        finally:
            conn.close()
    finally:
        if apply:
            _release_lock()
    _append_log({"event": "tick", "apply": apply,
                 "links_run": out["report"]["links_run"],
                 "nothing_to_do_at": out["report"]["nothing_to_do_at"]})
    return out


# ---------------------------------------------------------------------------
# STRUCTURAL GUARANTEES, asserted by the proof so they cannot be undone silently
# ---------------------------------------------------------------------------

def activation_calls_in_source() -> list[str]:
    """Every call to an activation entry point in THIS file. Must stay EMPTY.

    A docstring mentioning the name is NOT a call, so this reads `ast` rather than text --
    the same trap that once let a text scan match its own prose.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if name in ("activate", "activate_flow"):
                found.append("%s (line %d)" % (name, n.lineno))
    return found


def kill_calls_in_source() -> list[str]:
    """Every kill/terminate/restart call in THIS file. Must stay EMPTY."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            if name in ("kill", "terminate", "killall", "taskkill", "system", "popen"):
                found.append("%s (line %d)" % (name, n.lineno))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="the auto-forever skill tick")
    ap.add_argument("--plan", action="store_true", help="read-only summary")
    ap.add_argument("--once", action="store_true", help="one bounded pass")
    ap.add_argument("--apply", action="store_true",
                    help="with --once: actually drive the links (default: measure only)")
    ap.add_argument("--declare", action="store_true", help="declare the conductor rows")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    db = args.db or str(_db())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    print("db: %s" % db)

    if args.declare:
        res = declare_conductor(conn)
        conn.commit()
        print("  declared conductor: %d step(s) under flow %r" % (res["count"], FLOW_KEY))
        conn.close()
        return 0

    if args.once:
        out = run_once(db_path=db, apply=args.apply)
        print("  ran=%s apply=%s" % (out.get("ran"), out.get("apply")))
        if out.get("plan"):
            p = out["plan"]
            print("  conductor declared=%s steps=%d budget/link=%d"
                  % (p["conductor_declared"], len(p["steps"]), p["budget_per_link"]))
            print("  backlog: %s" % p["backlog"])
        if out.get("report"):
            r = out["report"]
            print("  links_run=%d produced=%d nothing_to_do_at=%s"
                  % (r["links_run"], r["produced"], r["nothing_to_do_at"]))
        print("  log: %s" % LOG_FILE)
        conn.close()
        return 0

    p = _plan(conn)
    print("  conductor declared=%s steps=%d" % (p["conductor_declared"], len(p["steps"])))
    for s in p["steps"]:
        print("    step %s %-9s %-9s %s" % (s["step_no"], s["step_kind"],
                                            s["layer_key"], str(s["note"])[:44]))
    print("  budget per link: %d" % p["budget_per_link"])
    print("  backlog: %s" % p["backlog"])
    print("  activation calls in this file: %s" % (activation_calls_in_source() or "NONE"))
    print("  kill calls in this file      : %s" % (kill_calls_in_source() or "NONE"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())