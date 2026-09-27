# -*- coding: utf-8 -*-
"""_proof_version_flip.py — a NEW VERSION RETIRES THE ONE IT REPLACES.

THE BUG (MEASURED 2026-09-27). THE HUMAN:

    "problem is proof run > new version + and missing to is_active -> 0 for
    old version / that is BUG! fix it now"

THE PROPERTY, stated so a machine can check it:

  * at most ONE version of an entity is `is_active = 1` — after `activate()`
    AND after `ensure_version()`
  * the retirement is RECORDED in `version_cleanup` (record AND flip, never
    one without the other)
  * an UNCITED retirement is REFUSED and writes nothing
  * the retire has ONE definition, so `entity_registry` DELEGATES rather than
    reimplements
  * `activate_flow()` is UNTOUCHED — a flow has no version chain

Read-only against the real DB; every mutation runs on a COPY.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import activation_gate as ag  # noqa: E402
import entity_registry as er  # noqa: E402

DB = BASE / "agent.db"
# THE FIX LINE, not `activation_gate.py:1`. MEASURED DEFECT IN MY OWN FIRST
# VERSION: I cited `activation_gate.py:1`, and
# `_proof_activation_gate.py:580` uses that EXACT string as a SENTINEL
# ("no probe supersession leaked into the live DB" counts rows with
# `cite_ref='activation_gate.py:1'`). So the backfill's real row was
# indistinguishable from a leaked probe, and a sentinel whose value a
# legitimate write can occupy is a detector that cannot detect.
FIX_CITE = "activation_gate.py:567"
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL  %s%s" % (name, ("  [%s]" % detail) if detail else ""))
    return bool(cond)


def section(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def _copy(tmp: Path) -> Path:
    work = tmp / "copy.db"
    shutil.copy2(DB, work)
    return work


def _conn(p: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    return c


def _active_versions(conn, letter, ref_id):
    """The entity's currently ACTIVE versions, read from the DB — a list."""
    return [int(r["version"]) for r in conn.execute(
        "SELECT version FROM version_register WHERE entity_type=? "
        "AND entity_ref_id=? AND is_active=1 ORDER BY version",
        (letter, int(ref_id)))]


def _pick_entity(conn):
    """A live entity with at least one version row, so nothing is invented."""
    r = conn.execute(
        "SELECT entity_type, entity_ref_id, MIN(version) v FROM version_register "
        "GROUP BY 1, 2 ORDER BY 1").fetchone()
    return str(r["entity_type"]), int(r["entity_ref_id"]), int(r["v"])


def _seed_verdict(conn, ref_tag, *, letter, ref_id):
    """Make `ref_tag` resolve to an entity that HAS an APPROVED 2-part verdict.

    THE GATE HAS TWO CHECKS: the streak, and the 2-part verdict. A ref_tag that
    resolves to nothing is refused BEFORE the path this proof tests, so the seed
    points it at the live `worker_identity` -> `P/1`, the same entity
    `_proof_activation_gate.py` uses. If the row or its verdict is absent this
    RAISES rather than fabricating a pass.
    """
    conn.execute(
        "INSERT OR IGNORE INTO task_ssot (task_id, dim_key, value_text) "
        "VALUES (1, 'job_ref', ?)", (ref_tag,))
    row = conn.execute(
        "SELECT 1 FROM register_approve WHERE entity_type=? AND entity_ref_id=? "
        "AND verdict='APPROVED'", (letter, int(ref_id))).fetchone()
    if not row:
        raise AssertionError(
            "no APPROVED register_approve row for %s/%d — the seed cannot be "
            "invented" % (letter, int(ref_id)))
    conn.commit()


def _seed_run(conn, ref_tag, wins, *, model="qwen2.5:7b-instruct",
              rule_version=1):
    for i in range(int(wins)):
        conn.execute(
            "INSERT INTO proof_run (entity_type, ref_tag, entity_name, "
            "round_no, value, oracle_answer, llm_answer, win, rule_version, "
            "model) VALUES ('T', ?, 'probe', ?, 'v', 'YES', 'YES', 1, ?, ?)",
            (ref_tag, i + 1, int(rule_version), model))
    conn.commit()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verflip_"))
    try:
        # ================================================================== A
        section("A. THE UNIT — retire_other_versions()")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            letter, ref_id, v1 = _pick_entity(conn)
            print("  seeded on live entity %s/%d (version %d)" % (letter, ref_id, v1))
            # TWO versions, BOTH active — the exact state the bug produced.
            conn.execute("UPDATE version_register SET is_active=1 "
                         "WHERE entity_type=? AND entity_ref_id=?",
                         (letter, ref_id))
            conn.execute(
                "INSERT OR IGNORE INTO version_register (entity_type, "
                "entity_ref_id, version, is_active) VALUES (?,?,?,1)",
                (letter, ref_id, v1 + 1))
            conn.commit()
            check("PRECONDITION: two versions active at once (the bug's state)",
                  len(_active_versions(conn, letter, ref_id)) == 2,
                  str(_active_versions(conn, letter, ref_id)))

            res = ag.retire_other_versions(
                conn, letter, ref_id, keep_version=v1 + 1,
                reason="proof: v%d supersedes v%d" % (v1 + 1, v1),
                cite_ref=FIX_CITE)
            conn.commit()
            check("QC-01  it RETURNS ok", res.get("ok") is True, str(res.get("code")))
            check("QC-01  exactly ONE version is active afterwards",
                  len(_active_versions(conn, letter, ref_id)) == 1,
                  str(_active_versions(conn, letter, ref_id)))
            check("QC-02  the OLD version is 0, read from the DB",
                  _active_versions(conn, letter, ref_id) == [v1 + 1],
                  "kept=%s" % _active_versions(conn, letter, ref_id))
            check("QC-02  the kept version is UNCHANGED and still active",
                  ag.is_activated(conn, letter, ref_id, v1 + 1))
            check("QC-01  it reports WHICH version it retired",
                  res.get("retired") == [v1], str(res.get("retired")))
            check("QC-04  the supersession is RECORDED in version_cleanup",
                  len(res.get("cleanup_ids") or []) == 1,
                  str(res.get("cleanup_ids")))
            cr = conn.execute(
                "SELECT old_version, new_version, cite_ref, reason FROM "
                "version_cleanup WHERE cleanup_id=?",
                (int(res["cleanup_ids"][0]),)).fetchone()
            check("QC-04  ...with the OLD and NEW version",
                  int(cr["old_version"]) == v1 and int(cr["new_version"]) == v1 + 1,
                  "%s -> %s" % (cr["old_version"], cr["new_version"]))
            check("QC-04  ...and the CITATION",
                  cr["cite_ref"] == FIX_CITE, str(cr["cite_ref"]))

            # ---- idempotence: a re-run must not invent a supersession ------
            before = conn.execute(
                "SELECT COUNT(*) FROM version_cleanup").fetchone()[0]
            res2 = ag.retire_other_versions(
                conn, letter, ref_id, keep_version=v1 + 1,
                reason="proof re-run", cite_ref=FIX_CITE)
            conn.commit()
            check("QC-08  a SECOND retire reports retired=[] (idempotent)",
                  res2.get("retired") == [], str(res2.get("retired")))
            check("QC-08  ...and names the version as ALREADY inactive",
                  res2.get("already_inactive") == [v1],
                  str(res2.get("already_inactive")))
            check("QC-08  ...and writes NO new supersession",
                  conn.execute("SELECT COUNT(*) FROM version_cleanup"
                               ).fetchone()[0] == before,
                  "unchanged")

            # ---- refusals: an uncited or reasonless retire is a claim -----
            vu = conn.execute(
                "SELECT COUNT(*) FROM version_cleanup").fetchone()[0]
            r_u = ag.retire_other_versions(
                conn, letter, ref_id, keep_version=v1 + 1,
                reason="x", cite_ref="I remember it")
            check("QC-05  an UNCITED retire is REFUSED",
                  r_u.get("code") == "UNCITED", str(r_u.get("code")))
            r_n = ag.retire_other_versions(
                conn, letter, ref_id, keep_version=v1 + 1,
                reason="", cite_ref=FIX_CITE)
            check("QC-05  a reasonless retire is REFUSED",
                  r_n.get("code") == "MISSING_REASON", str(r_n.get("code")))
            check("QC-05  the refusals wrote NOTHING",
                  conn.execute("SELECT COUNT(*) FROM version_cleanup"
                               ).fetchone()[0] == vu,
                  "unchanged")
        finally:
            conn.close()

        # ================================================================== B
        section("B. THE INVARIANT AFTER activate() — the reported bug")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            import db_schema as ds
            ds.ensure_task_center_schema(conn)
            PROBE_RV = 99
            _seed_verdict(conn, "worker_identity", letter="P", ref_id=1)
            _seed_run(conn, "worker_identity", 20, rule_version=PROBE_RV)
            # v1 active, v2 NOT — the state before the bug is triggered.
            conn.execute("UPDATE version_register SET is_active=0 "
                         "WHERE entity_type='P' AND entity_ref_id=1")
            conn.execute("UPDATE version_register SET is_active=1 "
                         "WHERE entity_type='P' AND entity_ref_id=1 AND version=1")
            conn.execute(
                "INSERT OR IGNORE INTO version_register (entity_type, "
                "entity_ref_id, version, is_active) VALUES ('P',1,2,0)")
            conn.commit()
            res = ag.activate(conn, "P", 1, 2, ref_tag="worker_identity",
                              cite_ref="activation_gate.py:1", target=10,
                              rule_version=PROBE_RV)
            check("QC-01  activate() ACTIVATES v2",
                  res.get("code") == "ACTIVATED", str(res.get("code")))
            check("QC-01  v2 is active", ag.is_activated(conn, "P", 1, 2))
            check("QC-01  *** v1 is RETIRED — at most one active version ***",
                  not ag.is_activated(conn, "P", 1, 1),
                  "v1 active=%s" % ag.is_activated(conn, "P", 1, 1))
            check("QC-01  the entity has EXACTLY ONE active version",
                  _active_versions(conn, "P", 1) == [2],
                  str(_active_versions(conn, "P", 1)))
            check("QC-02  the retire is REPORTED by activate()",
                  res.get("retired") == [1], str(res.get("retired")))
            check("QC-04  the supersession is RECORDED",
                  conn.execute(
                      "SELECT COUNT(*) FROM version_cleanup WHERE "
                      "entity_type='P' AND entity_ref_id=1 AND old_version=1 "
                      "AND new_version=2").fetchone()[0] == 1)

            # ---- QC-06: v1 of a single-version entity retires nothing ------
            conn.execute("UPDATE version_register SET is_active=1 "
                         "WHERE entity_type='P' AND entity_ref_id=1 AND version=1")
            conn.execute("UPDATE version_register SET is_active=0 "
                         "WHERE entity_type='P' AND entity_ref_id=1 AND version=2")
            conn.commit()
            res1 = ag.activate(conn, "P", 1, 1, ref_tag="worker_identity",
                               cite_ref="activation_gate.py:1", target=10,
                               rule_version=PROBE_RV)
            check("QC-06  activating v1 of a single-version entity is a no-op "
                  "retire (no false positive)",
                  res1.get("retired") == [], str(res1.get("retired")))
            check("QC-06  ...and v1 is active", ag.is_activated(conn, "P", 1, 1))
        finally:
            conn.close()

        # ================================================================== C
        section("C. THE OTHER ENTRY — ensure_version(v>1)")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            letter, ref_id, v1 = _pick_entity(conn)
            conn.execute("UPDATE version_register SET is_active=1 "
                         "WHERE entity_type=? AND entity_ref_id=?",
                         (letter, ref_id))
            conn.commit()
            res = er.ensure_version(conn, letter, ref_id, v1 + 1,
                                    note="proof: a new version", created_by="proof")
            check("QC-03  ensure_version(v>1) CREATES the new version",
                  res.get("ok") is True and res.get("created") is True, str(res))
            check("QC-03  ...and RETIRES the older version(s)",
                  res.get("retired") == [v1], str(res.get("retired")))
            check("QC-01  ...so exactly ONE version is active",
                  _active_versions(conn, letter, ref_id) == [v1 + 1],
                  str(_active_versions(conn, letter, ref_id)))
            check("QC-04  ...and the supersession is RECORDED",
                  conn.execute(
                      "SELECT COUNT(*) FROM version_cleanup WHERE "
                      "entity_type=? AND entity_ref_id=? AND old_version=? "
                      "AND new_version=?", (letter, ref_id, v1, v1 + 1)
                  ).fetchone()[0] == 1)
            # version 1 of a fresh entity: nothing to retire.
            res_v1 = er.ensure_version(conn, letter, ref_id, v1,
                                       note="no-op", created_by="proof")
            check("QC-06  re-ensuring the SAME version creates nothing and "
                  "retires nothing (already exists)",
                  res_v1.get("created") is False, str(res_v1))
        finally:
            conn.close()

        # ================================================================== D
        section("D. ONE DEFINITION — entity_registry DELEGATES, not reimplements")
        ent_src = (BASE / "entity_registry.py").read_text(
            encoding="utf-8", errors="replace")
        ag_src = (BASE / "activation_gate.py").read_text(
            encoding="utf-8", errors="replace")
        check("QC-10  entity_registry CALLS activation_gate.retire_other_versions",
              "retire_other_versions(" in ent_src)
        check("QC-10  entity_registry does NOT reimplement the flip "
              "(no UPDATE version_register SET is_active=0 in it)",
              not re.search(
                  r"UPDATE\s+version_register\s+SET\s+is_active\s*=\s*0", ent_src, re.I),
              "no second answer to one question")
        check("QC-10  the flip has ONE home (activation_gate)",
              len(re.findall(
                  r"UPDATE\s+version_register\s+SET\s+is_active\s*=\s*0",
                  ag_src, re.I)) == 1,
              "%d occurrence(s)" % len(re.findall(
                  r"UPDATE\s+version_register\s+SET\s+is_active\s*=\s*0",
                  ag_src, re.I)))

        # ================================================================== E
        section("E. activate_flow() is UNTOUCHED — a flow has no version chain")
        body = ag_src[ag_src.index("def activate_flow("):]
        body = body[:body.index("\ndef record_supersession(")]
        check("QC-07  activate_flow() contains NO retire call",
              "retire_other_versions" not in body,
              "a flow has no version chain to retire")

        # ================================================================== F
        section("F. THE BACKFILL REPAIRS WHAT THE BUG ALREADY PRODUCED")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            # SEED the defect. MEASURED DEFECT IN MY OWN FIRST VERSION: this
            # section relied on the LIVE DB still containing the bug, so once
            # the backfill fixed the live data the check went RED on CORRECT
            # work. A check that depends on the DATA being broken is not a check
            # on the CODE -- the "pinned count" defect. So the broken state is
            # CONSTRUCTED here, and the repair is measured against it.
            letter, ref_id, v1 = _pick_entity(conn)
            conn.execute("UPDATE version_register SET is_active=1 "
                         "WHERE entity_type=? AND entity_ref_id=?",
                         (letter, ref_id))
            conn.execute(
                "INSERT OR IGNORE INTO version_register (entity_type, "
                "entity_ref_id, version, is_active) VALUES (?,?,?,1)",
                (letter, ref_id, v1 + 1))
            conn.commit()
            bad = list(conn.execute(
                "SELECT entity_type, entity_ref_id, MAX(version) mx FROM "
                "version_register WHERE is_active=1 GROUP BY 1,2 HAVING "
                "COUNT(*) > 1"))
            check("PRECONDITION: the defect is present (1 entity, 2 active)",
                  len(bad) == 1, "%d entity(ies)" % len(bad))
            print("  entities with >1 active version BEFORE: %d" % len(bad))
            for r in bad:
                ag.retire_other_versions(
                    conn, str(r["entity_type"]), int(r["entity_ref_id"]),
                    keep_version=int(r["mx"]),
                    reason="backfill: a new version superseded it but the old "
                           "version was never retired (measured 2026-09-27)",
                    cite_ref=FIX_CITE,
                    decided_by="_proof_version_flip.py")
            conn.commit()
            after = list(conn.execute(
                "SELECT entity_type, entity_ref_id FROM version_register "
                "WHERE is_active=1 GROUP BY 1,2 HAVING COUNT(*) > 1"))
            check("QC-09  AFTER the backfill, ZERO entities have >1 active version",
                  not after, str(after))
            check("QC-09  ...and the retire is recorded, not silent",
                  conn.execute(
                      "SELECT COUNT(*) FROM version_cleanup WHERE "
                      "decided_by='_proof_version_flip.py'").fetchone()[0] == 1,
                  "%d row(s)" % conn.execute(
                      "SELECT COUNT(*) FROM version_cleanup WHERE "
                      "decided_by='_proof_version_flip.py'").fetchone()[0])
            check("QC-09  ...and the record names the version it retired",
                  conn.execute(
                      "SELECT old_version FROM version_cleanup WHERE "
                      "decided_by='_proof_version_flip.py'").fetchone()[0] == v1,
                  "old_version=%s" % v1)
        finally:
            conn.close()

        print("\n" + "=" * 78)
        print("RESULT: %d passed / %d failed" % (PASS, FAIL))
        print("=" * 78)
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
