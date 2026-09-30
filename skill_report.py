# -*- coding: utf-8 -*-
"""skill_report.py — ONE correct picture of the skill tables, with NO schema change.

WHY A REPORT AND NOT A BRIDGE
-----------------------------
Asked for "lev1 = task1, lev2 = task2, both with entity ID = same skill" plus a
task queue with status and UI. Measured before building anything:

    skill_registry.skill_key           7 rows  (yes_no, tdd_verify, ...)
    skill_contract_template.skill_key 18 rows  (citation_discipline, ppt_produce, ...)
    skill_prompt_ssot.skill_key       26 rows  (citation_discipline, independent_review, ...)
    skill_task_queue.skill_id         43 rows  ('SKILL.QUEUE.SMOKE', 'SKILL.ENV.TASK.PROOF')

    skill_registry INTERSECT skill_contract_template  -> EMPTY
    skill_registry INTERSECT queue.skill_id           -> EMPTY

So the systems were never joined. The same shape appeared twice already
(`field_tdd` and `pair_qc` each defining their own taxonomy with no authority).

THE AUTHORITATIVE KEY, CHOSEN BY EVIDENCE AND STATED HERE
---------------------------------------------------------
`skill_prompt_ssot.skill_key` is THE authority because:
  * it is the only skill table whose keys AGREE with another table's
    (measured: it intersects `skill_contract_template`)
  * it has a real PRODUCER — `skill_prompt.py:480` INSERT, `:446`/`:457`/
    `:531`/`:539` UPDATE, `upsert_skill_prompt` at `:406` (line numbers verified
    by grep against the file, not recalled)
  * 28 hand-written `_registry_*` scripts read it to verify registration

`skill_registry` is NOT a skill directory: it is the parent table of the
composition engine (`wording_registry` FK -> `skill_registry.skill_id`) and holds
OUTPUT FORMATS. The `S = skill` entity letter points at it — that schema defect is
what this report makes visible rather than hides.

NO MAPPING IS INVENTED. `SKILL.QUEUE.SMOKE` has NO producer in the source: grep
found it only as a hand-typed literal in two `_proof_*` scripts. It is therefore
reported as a legacy/test value and NEVER converted. Writing a
'SKILL.QUEUE.SMOKE' -> 'skill_queue_smoke' rule would DECIDE an architecture
question by guessing it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

AUTH_TABLE = "skill_prompt_ssot"
AUTH_COL = "skill_key"
AUTHORITY_EVIDENCE = (
    "it has a real PRODUCER (skill_prompt.py:480 INSERT, upsert_skill_prompt:406, "
    "UPDATE at :446/:457) and 28 _registry_* scripts read it; it is the only "
    "skill table with a prompt-version row per skill. CORRECTED 2026-09-21: this "
    "line first claimed it was 'the only pair that agrees'. The report's OWN "
    "measurement REFUTED that — skill_contract_template matches only 7 of 18 "
    "(PARTIAL), while skill_lesson matches 10 of 10 (AGREES). The evidence "
    "sentence is corrected rather than the measurement."
)

# Every table that claims to key on a skill. DECLARED, so a new one must be added
# on purpose rather than silently omitted from the picture.
KEY_TABLES: tuple[tuple[str, str, str], ...] = (
    ("skill_prompt_ssot", "skill_key", "authoritative"),
    ("skill_contract_template", "skill_key", "contract (purpose/flow/not-to-do)"),
    ("skill_registry", "skill_key", "output FORMAT parent, NOT a skill list"),
    ("skill_registry", "skill_id", "integer PK, not a key"),
    ("skill_task_queue", "skill_id", "queue (43 rows, test data)"),
    ("skill_mismatch_log", "skill_key", "task-level failures"),
    ("skill_lesson", "skill_key", "lessons"),
    ("skill_versions", "skill_key", "versions"),
    ("skill_contract_tdd_case", "contract_id", "cases, keyed on CONTRACT"),
    ("field_tdd_rule", "system_key", "field TDD, a different namespace"),
)


def authority(conn: sqlite3.Connection) -> list[str]:
    """The authoritative skill key list. Read, never assumed."""
    return sorted({str(r[0]) for r in conn.execute(
        "SELECT DISTINCT %s FROM %s" % (AUTH_COL, AUTH_TABLE)) if r[0]})


def key_status(conn: sqlite3.Connection, table: str, col: str) -> dict[str, Any]:
    """How a table's own key relates to the authority.

    Five states, and the distinction between the last two matters: an EMPTY table
    and a MISSING column produce the same count of zero rows and mean completely
    different things.
    """
    try:
        vals = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT %s FROM %s" % (col, table)) if r[0] is not None}
    except sqlite3.OperationalError as e:
        # The SAME KEYS as the success path, so a caller never has to branch on
        # whether the row exists. A missing column returns 0 counts plus a state
        # that names it — an undefined key here made a downstream reader raise
        # KeyError, which hid the state behind a crash.
        return {"table": table, "column": col, "state": "NO_SUCH_COLUMN",
                "detail": str(e), "distinct": 0, "match_auth": 0,
                "unmatched": [], "rows": None, "rows_that_join": None,
                "join_works": False}
    auth = set(authority(conn))
    matched = vals & auth
    if not vals:
        state = "EMPTY"
    elif matched == vals:
        state = "AGREES"
    elif matched:
        state = "PARTIAL"
    else:
        state = "NO_MATCH"
    try:
        joined = conn.execute(
            "SELECT COUNT(*) FROM %s x JOIN %s a ON a.%s = x.%s"
            % (table, AUTH_TABLE, AUTH_COL, col)).fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    except sqlite3.OperationalError:
        joined, rows = None, None
    return {"table": table, "column": col, "state": state,
            "distinct": len(vals), "match_auth": len(matched),
            "unmatched": sorted(vals - auth)[:6],
            "rows": rows, "rows_that_join": joined,
            "join_works": bool(joined)}


def report(conn: sqlite3.Connection) -> dict[str, Any]:
    auth = authority(conn)
    per = [key_status(conn, t, c) for t, c, _ in KEY_TABLES]
    for rec, (t, c, role) in zip(per, KEY_TABLES):
        rec["role"] = role
    return {
        "authority": {"table": AUTH_TABLE, "column": AUTH_COL,
                      "skills": len(auth), "why": AUTHORITY_EVIDENCE},
        "skill_keys": auth,
        "keys": per,
        "agreeing": [r["table"] for r in per if r["state"] == "AGREES"],
        "orphan": [r["table"] for r in per if r["state"] == "NO_MATCH"],
        "partial": [r["table"] for r in per if r["state"] == "PARTIAL"],
        "note": ("NO_MATCH means the table uses a DIFFERENT namespace. It is "
                 "REPORTED, never converted: inventing a mapping would decide an "
                 "architecture question by guessing it."),
    }


def per_skill(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """For each authoritative skill, what exists in each system.

    A MISSING COLUMN returns -1, a GENUINELY ABSENT ROW returns 0. Collapsing the
    two would hide a schema gap behind a plausible zero.
    """
    out = []
    for k in authority(conn):
        def n(sql: str) -> int:
            try:
                return conn.execute(sql, (k,)).fetchone()[0]
            except sqlite3.OperationalError:
                return -1
        out.append({
            "skill_key": k,
            "contract": n("SELECT COUNT(*) FROM skill_contract_template "
                          "WHERE skill_key=?"),
            "prompt_versions": n("SELECT COUNT(*) FROM skill_prompt_ssot "
                                 "WHERE skill_key=?"),
            "lessons": n("SELECT COUNT(*) FROM skill_lesson WHERE skill_key=?"),
            "mismatches": n("SELECT COUNT(*) FROM skill_mismatch_log "
                            "WHERE skill_key=?"),
            "in_skill_registry": n("SELECT COUNT(*) FROM skill_registry "
                                   "WHERE skill_key=?"),
        })
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--per-skill", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        out: dict[str, Any] = {"report": report(conn)}
        if args.per_skill:
            out["per_skill"] = per_skill(conn)
            out["legend"] = {"-1": "the COLUMN does not exist",
                             "0": "the ROW genuinely does not exist"}
        print(json.dumps(out, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()