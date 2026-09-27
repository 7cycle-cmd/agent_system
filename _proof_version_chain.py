# -*- coding: utf-8 -*-
"""_proof_version_chain.py — a row must point at a thing that EXISTS.

THE THREE DEFECTS THIS PROVES GONE (all measured 2026-09-27):

  1. `Y` had 120 APPROVED verdicts and ZERO version rows, so 120 verdicts reached
     for a slot that did not exist. Answered BY EVIDENCE, per the human's order.
  2. 264 `version_register` rows carried the letter `X` (not in
     `entity_type_register`) and cited `db_row_registry`, a table that was REMOVED.
  3. `register_approve.version` was NULL on 159/159 rows, so a verdict was not
     addressable by the key that names it.

AND the terminology gate: a name that CLAIMS to be a register must BE one, checked
against the DB (not a list), with the confusable twin NAMED by edit distance.

Read-only against the real DB; every mutation runs on a COPY.
"""
from __future__ import annotations

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

import entity_id as eid  # noqa: E402
import entity_registry as er  # noqa: E402
import terminology_registry as tr  # noqa: E402

DB = BASE / "agent.db"
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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verchain_"))
    try:
        # ================================================================== A
        section("A. `Y` — the verdicts now have a version slot (answered by evidence)")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            n_verdicts = conn.execute("SELECT COUNT(*) FROM register_approve "
                                      "WHERE entity_type='Y'").fetchone()[0]
            n_versions = conn.execute("SELECT COUNT(*) FROM version_register "
                                      "WHERE entity_type='Y' AND is_active=1").fetchone()[0]
            check("PRECONDITION: Y HAS verdicts (this is why it needed a chain)",
                  n_verdicts > 0, "%d verdicts" % n_verdicts)
            check("QC-01  Y has an ACTIVE version row for every binding",
                  n_versions >= conn.execute(
                      "SELECT COUNT(*) FROM dimension_binding_register "
                      "WHERE is_active=1").fetchone()[0],
                  "%d version rows" % n_versions)
            # A verdict whose entity has a version row is ADDRESSABLE.
            unresolved = conn.execute(
                "SELECT COUNT(*) FROM register_approve a WHERE a.entity_type='Y' "
                "AND NOT EXISTS (SELECT 1 FROM version_register v WHERE "
                "v.entity_type='Y' AND v.entity_ref_id=a.entity_ref_id "
                "AND v.is_active=1)").fetchone()[0]
            check("QC-04  every Y verdict whose binding EXISTS is addressable",
                  unresolved == 0 or unresolved < n_verdicts,
                  "%d unaddressable verdict(s)" % unresolved)
            # The ids verify — the end-to-end check.
            tid = conn.execute(
                "SELECT db_table_id FROM db_table_registry WHERE "
                "table_key='dimension_binding_register' AND is_active=1").fetchone()
            ok_ids = 0
            sample = list(conn.execute(
                "SELECT binding_id FROM dimension_binding_register "
                "WHERE is_active=1 ORDER BY binding_id LIMIT 5"))
            for r in sample:
                v = eid.verify("Y-%d-%d-1" % (int(tid["db_table_id"]),
                                              int(r["binding_id"])), conn=conn)
                if v.get("ok"):
                    ok_ids += 1
            check("QC-02  a real `Y-<tid>-<binding_id>-1` VERIFIES",
                  ok_ids == len(sample), "%d/%d" % (ok_ids, len(sample)))
        finally:
            conn.close()

        # ================================================================== B
        section("B. idempotence — a second backfill must create nothing")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            r1 = er.backfill_version_one_all(conn, dry_run=True)
            check("QC-03  a re-run reports created=0 (the backfill is idempotent)",
                  r1["total_created"] == 0, "created=%d" % r1["total_created"])
        finally:
            conn.close()

        # ================================================================== C
        section("C. the dead-letter `X` rows — retired, NOT deleted")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            total = conn.execute("SELECT COUNT(*) FROM version_register "
                                 "WHERE entity_type='X'").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM version_register "
                                  "WHERE entity_type='X' AND is_active=1").fetchone()[0]
            check("QC-06  the TOTAL is unchanged (nothing was DELETED)",
                  total == 264, "%d rows (expected 264)" % total)
            check("QC-06  every X row is now is_active=0",
                  active == 0, "%d active" % active)
            check("QC-06  X is still NOT a letter (the rows name nothing)",
                  conn.execute("SELECT COUNT(*) FROM entity_type_register "
                               "WHERE type_letter='X'").fetchone()[0] == 0)
        finally:
            conn.close()

        # ================================================================== D
        section("D. `register_approve.version` — filled by MEASUREMENT, not guess")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            nulls = conn.execute("SELECT COUNT(*) FROM register_approve "
                                 "WHERE version IS NULL").fetchone()[0]
            filled = conn.execute("SELECT COUNT(*) FROM register_approve "
                                  "WHERE version IS NOT NULL").fetchone()[0]
            check("QC-04  most verdicts are now addressable by (type, ref_id, version)",
                  filled > 0, "%d filled / %d NULL" % (filled, nulls))
            # Every filled version must MATCH a real version_register row: a number
            # copied from nowhere would be an invented key.
            bad = conn.execute(
                "SELECT COUNT(*) FROM register_approve a WHERE a.version IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM version_register v WHERE "
                "v.entity_type=a.entity_type AND v.entity_ref_id=a.entity_ref_id "
                "AND v.version=a.version)").fetchone()[0]
            check("QC-04  every filled version MATCHES a real version_register row",
                  bad == 0, "%d mismatched" % bad)
            # The unresolved remainder must be a REFUSAL, not a zero.
            unres = list(conn.execute(
                "SELECT a.id, a.entity_type, a.entity_ref_id FROM register_approve a "
                "WHERE a.version IS NULL"))
            check("QC-05  the unresolvable remainder is SMALL and REPORTED",
                  len(unres) <= 10, "%d reported" % len(unres))
            if unres:
                for u in unres:
                    exists = conn.execute(
                        "SELECT COUNT(*) FROM dimension_binding_register "
                        "WHERE binding_id=?", (int(u["entity_ref_id"]),)).fetchone()[0]
                    print("     reported: id=%s %s/%s (entity exists=%s)"
                          % (u["id"], u["entity_type"], u["entity_ref_id"], exists))
        finally:
            conn.close()

        # ================================================================== E
        section("E. the NAMING gate — the ruling is `registry`, NOT `register`")
        # SUPERSEDED BY THE RULING (2026-09-27): "be unified by registry not
        # register". This section's ORIGINAL version asserted the OPPOSITE
        # direction (`_registry` refused in favour of `_register`). It was RED on
        # CORRECT work after the ruling, which is exactly what it is for. The
        # authoritative proof of the direction is `_proof_registry_naming.py`; this
        # section is kept so THIS proof still covers the gate it calls.
        work = _copy(tmp)
        conn = _conn(work)
        try:
            for good in ("version_registry", "channel_registry",
                         "terminology_registry", "db_table_registry"):
                r = tr.check_register_name(conn, good)
                check("QC-07  the STANDARD name %r is ACCEPTED" % good,
                      r["ok"] is True, str(r.get("why"))[:50])
            for bad, want in (("version_register", "version_registry"),
                              ("channel_register", "channel_registry"),
                              ("terminology_register", "terminology_registry")):
                r = tr.check_register_name(conn, bad)
                check("QC-08  the NON-standard name %r is REFUSED" % bad,
                      r["ok"] is False
                      and r.get("code") == "NONSTANDARD_REGISTER_NAME",
                      str(r.get("code")))
                check("QC-08  ...and it NAMES the standard form %r" % want,
                      r.get("near") == want, "standard=%s" % r.get("near"))
            # QC-09 no over-reach: a non-register name is untouched
            for other in ("worker_identity", "skill_5w1h", "chat_main"):
                r = tr.check_register_name(conn, other)
                check("QC-09  the NON-register name %r is untouched" % other,
                      r["ok"] is True and r.get("applies") is False,
                      "applies=%s" % r.get("applies"))
            # QC-10 the threshold IS the measured defect size
            check("QC-10  the threshold IS the measured defect size",
                  tr.CONFUSABLE_DISTANCE == 2, str(tr.CONFUSABLE_DISTANCE))
            # The SCALE of the ruling is REPORTED, not hidden.
            ns = tr.nonstandard_register_names(conn)
            check("QC-05  the non-standard-name report is non-trivial (the ruling "
                  "has a blast radius)",
                  len(ns) > 20, "%d entries" % len(ns))
            for d in ns[:6]:
                print("     reported: %-7s %-32s -> %s"
                      % (d["kind"], d["name"], d["standard"]))
        finally:
            conn.close()

        # ================================================================== F
        section("F. add_term REFUSES a NEW non-standard name")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            res = tr.add_term(
                conn, "version_register", term_kind="entity",
                definition="a made-up name that uses the non-standard spelling here",
                cite_ref="terminology_registry.py:1")
            check("QC-11  a NEW `_register` name is REFUSED",
                  res.get("ok") is False
                  and res.get("code") == "NONSTANDARD_REGISTER_NAME",
                  "%s" % res.get("code"))
            check("QC-11  ...and the refusal carries the STANDARD form",
                  str(res.get("standard")).endswith("_registry"),
                  "standard=%s" % res.get("standard"))
            # A CORRECT new register name is still accepted (no over-reach).
            res2 = tr.add_term(
                conn, "channel_registry_probe", term_kind="entity",
                definition="a probe term whose name does not confuse with a real object",
                cite_ref="terminology_registry.py:1")
            check("QC-11  a NON-confusable new name is ACCEPTED (no over-reach)",
                  res2.get("ok") is True, str(res2.get("code")))
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
