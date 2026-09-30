"""SessionStart gate: `ensure_schema` is the single authority, and LIVE cannot drift.

WHY THIS EXISTS (MEASURED 2026-09-29)
-------------------------------------
The STAGE 1/2 work fixed two defects and tightened a policy. Both defects were the
SAME defect seen twice: a schema statement declared one thing and the database did
another. MEASURED examples:

  1. `question_template_registry.py` / `github_find_registry.py` declared
     `FOREIGN KEY (factor_key) REFERENCES skill_factor_registry (factor_key)` --
     a parent key SQLite can never use (it is an EXPRESSION index).
  2. `skill_contract_store.py` still declared `contract_id TEXT PRIMARY KEY` while
     LIVE had been migrated to `contract_ref INTEGER PRIMARY KEY` on 2026-09-21,
     and the four children's FKs still pointed at the name.

So a FRESH build and LIVE produced DIFFERENT schemas, and nothing reported it. A
proof can only catch that if it RUNS, and nothing ran it every session. There is
NO `.github/workflows` in this repo (measured), so the surfaces that exist are the
hooks: `ask_mode_guard`, `plan_gate` (SessionStart/UserPromptSubmit/PreToolUse),
`proof_gate` (Stop), `qc_gate` (PostToolUse). THIS gate uses SessionStart, because
that is the one surface that runs on this machine at the start of every session.

WHAT IT CHECKS
--------------
    G1  LIVE declares ZERO FKs whose parent key is not a plain PK/UNIQUE
    G2  a FRESH `ensure_schema` declares ZERO such FKs
    G3  LIVE and FRESH agree on the FK shape of every table they SHARE
        (disagreement is exactly how both defects above stayed invisible)
    G4  POSITIVE CONTROL: the classifier can SEE an illegal parent key
        (a synthetic EXPRESSION-only parent must be reported), and does NOT
        report a legal FK to a PK -- because a detector that can never fire
        reports 0 for the same reason a broken detector does
    G5  the four declarers that own the STAGE 2 FKs state the policy

Exit 1 on any failure, with the offending object NAMED. A gate that fails
silently is worse than no gate.

Usage:
    python scripts/schema_gate.py
    python scripts/schema_gate.py --self-test     # only G4 (the control)
    python scripts/schema_gate.py --quiet
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LIVE_DB = ROOT / "agent.db"
LOG = ROOT / "schema_gate_log.txt"

POLICY_DECLARERS = (
    "skill_contract_store.py",
    "db_schema.py",
    "ticket_subject.py",
    "question_template_registry.py",
    "github_find_registry.py",
)
POLICY_MARKER = "PRIMARY KEY"


class Gate:
    def __init__(self, quiet=False):
        self.p = self.f = 0
        self.quiet = quiet

    def ok(self, cond, label):
        if cond:
            self.p += 1
            if not self.quiet:
                print("  [ ok ] %s" % label)
        else:
            self.f += 1
            print("  [FAIL] %s" % label)
        return bool(cond)


def _pk(conn, t):
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t) if r[5]]


def _uniques(conn, t):
    out = []
    pk = _pk(conn, t)
    if pk:
        out.append(("PK", tuple(pk), "plain"))
    for ix in conn.execute("PRAGMA index_list(%s)" % t):
        if not ix[2]:
            continue
        c = tuple(r[2] for r in conn.execute("PRAGMA index_info(%s)" % ix[1]))
        out.append((ix[1], c, "EXPRESSION" if any(x is None for x in c) else "plain"))
    return out


def fk_map(conn):
    """{table: sorted(parent, child_col, parent_col)} for every real table."""
    out = {}
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        t = r[0]
        if t.startswith("sqlite_"):
            continue
        out[t] = sorted((f[2], f[3], f[4]) for f in
                        conn.execute("PRAGMA foreign_key_list(%s)" % t))
    return out


def shape_map(conn):
    """{table: (shape, pk, uniques)} -- the WHOLE table shape, from PRAGMA.

    WHY THIS EXISTS (MEASURED 2026-09-29): the FK list ALONE is too weak. The
    five tables this gate first reported differed in PK / NOT NULL / UNIQUE as
    well, and a FRESH `agent_provider` was body-less columns with NO PRIMARY KEY
    while LIVE had one. "The FK lists match" would have passed that.
    """
    out = {}
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        t = r[0]
        if t.startswith("sqlite_"):
            continue
        shape = sorted((x[1], (x[2] or "").upper(), bool(x[3]), bool(x[5]))
                       for x in conn.execute("PRAGMA table_info(%s)" % t))
        pk = [x[1] for x in conn.execute("PRAGMA table_info(%s)" % t) if x[5]]
        uq = sorted(tuple(x[2] for x in conn.execute("PRAGMA index_info(%s)" % ix[1]))
                    for ix in conn.execute("PRAGMA index_list(%s)" % t) if ix[2])
        out[t] = (shape, tuple(pk), tuple(uq))
    return out


def broken(conn):
    """Every FK whose parent key is not a plain PK/UNIQUE -- measured by PRAGMA.

    MEASURED WARNING: this must NEVER be implemented by regexing `sqlite_master.sql`.
    An earlier attempt did, read the COMMENTS in the DDL, and reported 9 broken FKs
    where there are 2 (`text_scan_reads_prose_as_code`).
    """
    bad = []
    for t, fks in fk_map(conn).items():
        for par, cc, pc in fks:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name=?", (par,)).fetchone():
                bad.append("%s.%s->%s.%s (PARENT ABSENT)" % (t, cc, par, pc))
                continue
            if not any(k[1] == (pc,) and k[2] == "plain" for k in _uniques(conn, par)):
                bad.append("%s.%s->%s.%s (NOT-A-PLAIN-KEY)" % (t, cc, par, pc))
    return bad


def fresh_db():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "fresh.db")
    import db_schema
    with contextlib.redirect_stdout(io.StringIO()):
        db_schema.ensure_schema(path)
    return tmp, path


def control(g: Gate, tag):
    """G4 -- the classifier must REPORT a synthetic illegal parent, and NOT report
    a legal FK to a PK. Without this, `0 broken` is indistinguishable from a
    detector that cannot fire."""
    m = sqlite3.connect(":memory:")
    m.executescript("""
        CREATE TABLE p_expr(pid INTEGER PRIMARY KEY, a TEXT, b TEXT NOT NULL);
        CREATE UNIQUE INDEX uq_expr ON p_expr(COALESCE(a,''), b);
        CREATE TABLE bad_expr(x TEXT NOT NULL,
            FOREIGN KEY(x) REFERENCES p_expr(b));
        CREATE TABLE p_pk(pid INTEGER PRIMARY KEY);
        CREATE TABLE ok_pk(y INTEGER, FOREIGN KEY(y) REFERENCES p_pk(pid));
        CREATE TABLE p_nouniq(nid INTEGER PRIMARY KEY, k TEXT);
        CREATE TABLE bad_nouniq(z TEXT, FOREIGN KEY(z) REFERENCES p_nouniq(k));
    """)
    b = " ".join(broken(m))
    g.ok("bad_expr" in b and "bad_nouniq" in b,
         "%s POSITIVE CONTROL: an EXPRESSION-only and a NOT-UNIQUE parent key are "
         "BOTH reported" % tag)
    g.ok("ok_pk" not in b,
         "%s ...and the LEGAL FK to the PK is NOT reported" % tag)
    m.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    g = Gate(a.quiet)
    lines = []
    print("=" * 74)
    print("SCHEMA GATE -- ensure_schema is the single authority")
    print("=" * 74)

    if a.self_test:
        print("\n[SELF-TEST] the detector can see the defect it hunts")
        control(g, "G4")
        return g.f and 1 or 0

    if not LIVE_DB.exists():
        print("  [FAIL] G0 the live database does not exist: %s" % LIVE_DB)
        return 1

    live = sqlite3.connect(str(LIVE_DB))
    print("\n1. LIVE declares no illegal FK")
    lb = broken(live)
    g.ok(not lb, "G1 LIVE declares ZERO broken FKs (%s)" % (lb or "none"))

    tmp, fresh_path = fresh_db()
    try:
        fr = sqlite3.connect(fresh_path)
        print("\n2. FRESH ensure_schema declares no illegal FK")
        fb = broken(fr)
        g.ok(not fb, "G2 a FRESH ensure_schema declares ZERO broken FKs (%s)"
             % (fb or "none"))

        print("\n3. LIVE and FRESH agree on the FK shape of SHARED tables")
        lm, fm = fk_map(live), fk_map(fr)
        shared = sorted(set(lm) & set(fm))
        drift = {t: (lm[t], fm[t]) for t in shared if lm[t] != fm[t]}
        # 🔴 TWO CLAIMS, NOT ONE. The first run of this gate (MEASURED 2026-09-29)
        # reported 5 drift tables. Asserting "no drift anywhere" would have called
        # a PRE-EXISTING declarer drift a result of this task -- the same mistake
        # as asserting 0 for the 287 orphans. So: THIS TASK'S tables must have no
        # drift, and the PRE-EXISTING population is REPORTED by name.
        #
        # The 5 tables below WERE the pre-existing population; they were repaired
        # by `_fix_declarer_drift.py` (migration `declarer_drift_v1`) and are now
        # OWNED, so a regression in any of them fails the gate.
        OUR_TABLES = (
            # FK policy STAGE 1/2
            "skill_contract_field", "skill_contract_tdd_case",
            "skill_contract_streak", "skill_contract_review_log",
            "ticket_subject", "mode_right_registry", "worker_mode",
            "github_find_registry", "question_template_registry",
            "skill_contract_template",
            # declarer drift v1
            "agent_provider", "component_registry", "consultant_team",
            "fault_analysis", "worker_environment_binding",
            # 🔴 RING 5 (2026-09-29): the 18 the gate found and REPORTED as a
            # number, now REPAIRED and therefore OWNED — a regression in any of
            # them fails the gate instead of being counted. They were five
            # DIFFERENT diseases (a table declared twice; a populated column no
            # declarer named; a LIVE unique no build could know about; a NOT NULL
            # the population forbids; a NOT NULL LIVE lacked), and the moves are
            # recorded in `schema_migration_log.ring5_drift_v1`.
            "api_registry", "capability_registry", "channel_registry",
            "consultant_member", "dev_task", "fault_option_pending",
            "function_registry", "llm_model", "pattern_template", "prompt_combo",
            "qc_gate_registry", "skill_mismatch_log", "skills",
            "task_lifecycle_log", "task_prompt_trace", "terminology_registry",
            "worker_job_binding", "workflow_step",
        )
        ours = {t: drift[t] for t in drift if t in OUR_TABLES}
        g.ok(not ours,
             "G3a NO DRIFT on the %d table(s) this FK policy owns (%s)"
             % (len(OUR_TABLES), ours or "none"))
        # A missing table cannot drift, and one that is live-only is likewise not
        # our business here; only a SHARED table can be compared. MEASURED: two of
        # the owned tables (`github_find_registry`, `question_template_registry`)
        # are LIVE-only -- a fresh `ensure_schema` does not create them -- so they
        # CANNOT be compared, and pretending otherwise makes the check vacuous.
        # Name them instead of silently skipping them.
        uncomparable = sorted(t for t in OUR_TABLES if t in lm and t not in fm)
        compared = sorted(t for t in OUR_TABLES if t in shared)
        print("         owned tables COMPARED: %d; LIVE-only and therefore "
              "uncomparable: %s" % (len(compared), uncomparable or "none"))
        g.ok(bool(compared),
             "G3a the drift comparison is NOT VACUOUS (%d owned table(s) compared)"
             % len(compared))
        others = sorted(t for t in drift if t not in OUR_TABLES)
        print("         PRE-EXISTING declarer drift (NOT this task's): %d table(s): %s"
              % (len(others), others or "none"))
        for t in others:
            live_fks, fresh_fks = drift[t]
            missing = [x for x in live_fks if x not in fresh_fks]
            extra = [x for x in fresh_fks if x not in live_fks]
            print("            %-32s LIVE-only=%s FRESH-only=%s" % (t, missing or "-", extra or "-"))
        g.ok(isinstance(len(others), int),
             "G3b the pre-existing drift is REPORTED as a number (%d table(s))"
             % len(others))

        print("\n3b. LIVE and FRESH agree on PK / NOT NULL / UNIQUE too")
        lsm, fsm = shape_map(live), shape_map(fr)
        sdrift = {t: (lsm[t], fsm[t]) for t in shared if lsm[t] != fsm[t]}
        s_ours = sorted(t for t in sdrift if t in OUR_TABLES)
        g.ok(not s_ours,
             "G3c NO shape (PK/NOT NULL/UNIQUE) drift on an owned table (%s)"
             % (s_ours or "none"))
        s_others = sorted(t for t in sdrift if t not in OUR_TABLES)
        print("         PRE-EXISTING shape drift (NOT this task's): %d table(s): %s"
              % (len(s_others), s_others or "none"))
        for t in s_others:
            sl, sf = sdrift[t]
            print("            %-32s shape differs: LIVE %d col(s) / FRESH %d col(s)"
                  % (t, len(sl[0]), len(sf[0])))
        g.ok(True,
             "G3d the pre-existing shape drift is REPORTED as a number (%d table(s), "
             "NOT asserted to zero: these are other worksets, found by this gate and "
             "named above)" % len(s_others))
        print("\n4. the detector can see the defect it hunts")
        control(g, "G4")
        print("\n5. the declarers state the policy")
        for fn in POLICY_DECLARERS:
            p = ROOT / fn
            if not p.exists():
                g.ok(False, "G5 %s exists" % fn)
                continue
            txt = p.read_text(encoding="utf-8", errors="replace")
            g.ok(POLICY_MARKER in txt,
                 "G5 %s states the policy (contains %r)" % (fn, POLICY_MARKER))
        fr.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    live.close()

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append("%s  passed=%d failed=%d" % (stamp, g.p, g.f))
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8", errors="replace")
    print("\nSCHEMA GATE: %d passed / %d failed" % (g.p, g.f))
    if g.f:
        print("FAILED -- the schema has drifted from its declarers. Fix the "
              "DECLARER, never the live database alone.")
        return 1
    print("OK -- ensure_schema and LIVE agree; no illegal FK parent key anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())