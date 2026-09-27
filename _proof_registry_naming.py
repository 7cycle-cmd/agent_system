# -*- coding: utf-8 -*-
"""_proof_registry_naming.py — the spelling standard is `_registry`, not `_register`.

THE RULING (2026-09-27), verbatim: "be unified by registry not register".

THE PROPERTY, stated so a machine can check it:

  * the gate ACCEPTS `_registry` and REFUSES `_register` for a NEW term, naming the
    standard form;
  * it does NOT touch a name that does not claim to be a register;
  * `add_term` does NOT retro-refuse an ALREADY-EXISTING term (a mass refusal of
    live data is the defect every gate here avoids);
  * the mapping is MECHANICAL (`x_register` -> `x_registry`), not a per-name list;
  * the migration's `--plan` runs, reports the FULL blast radius, and writes NOTHING.

THE DEFECT THIS PROOF EXISTS FOR: my own first version of the gate enforced the
OPPOSITE direction (`version_registry` -> "did you mean `version_register`"). A gate
that enforces the wrong direction makes the CORRECT name look like the error.

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
    tmp = Path(tempfile.mkdtemp(prefix="regname_"))
    try:
        # ================================================================== A
        section("A. THE DIRECTION — `_registry` is the standard (the ruling)")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            for good in ("version_registry", "channel_registry", "terminology_registry",
                         "db_table_registry", "capability_registry", "module_registry"):
                r = tr.check_register_name(conn, good)
                check("QC-01  %r is ACCEPTED (standard suffix)" % good,
                      r["ok"] is True, str(r.get("why"))[:50])
            for bad in ("version_register", "channel_register", "terminology_register"):
                r = tr.check_register_name(conn, bad)
                check("QC-02  %r is REFUSED (non-standard suffix)" % bad,
                      r["ok"] is False and r["code"] == "NONSTANDARD_REGISTER_NAME",
                      str(r.get("code")))
                check("QC-02  ...and it NAMES the standard form",
                      r.get("near") == tr.standard_registry_name(bad),
                      "standard=%s" % r.get("near"))
            # The direction is the whole point, and I got it BACKWARDS first.
            r = tr.check_register_name(conn, "version_register")
            check("QC-02  *** the suggestion is `_registry`, NOT `_register` *** "
                  "(my first version had this backwards)",
                  "_registry" in str(r.get("near")), str(r.get("near")))
        finally:
            conn.close()

        # ================================================================== B
        section("B. NO OVER-REACH — a name that does not claim to be a register")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            for other in ("worker_identity", "skill_5w1h", "chat_main",
                          "100_run_service", "run"):
                r = tr.check_register_name(conn, other)
                check("QC-03  %r is UNTOUCHED (applies=False)" % other,
                      r["ok"] is True and r.get("applies") is False,
                      "applies=%s" % r.get("applies"))
            # `looks_like_a_register` must be the ONE predicate both use.
            check("QC-03  `looks_like_a_register` recognises BOTH spellings",
                  tr.looks_like_a_register("x_register")
                  and tr.looks_like_a_register("x_registry"))
            check("QC-03  `is_registry_standard` accepts a non-register name",
                  tr.is_registry_standard("worker_identity") is True)
        finally:
            conn.close()

        # ================================================================== C
        section("C. THE MAPPING IS MECHANICAL, not a per-name list")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            for a, b in (("x_register", "x_registry"),
                         ("channel_register", "channel_registry"),
                         ("a_b_c_register", "a_b_c_registry"),
                         ("register_thing", "registry_thing")):
                check("QC-08  %r -> %r" % (a, b),
                      tr.standard_registry_name(a) == b,
                      tr.standard_registry_name(a))
            # POSITIVE CONTROL: a non-register name must be returned UNCHANGED, or
            # the mapping would be rewriting names it has no business touching.
            check("QC-08  a non-register name is UNCHANGED (no false rewrite)",
                  tr.standard_registry_name("worker_identity") == "worker_identity")
        finally:
            conn.close()

        # ================================================================== D
        section("D. THE SCALE — existing non-standard names are REPORTED")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            ns = tr.nonstandard_register_names(conn)
            kinds = sorted({d["kind"] for d in ns})
            check("QC-06  the report is non-trivial (the ruling has a blast radius)",
                  len(ns) > 20, "%d entries" % len(ns))
            check("QC-06  ...and covers BOTH objects and terms",
                  kinds == ["object", "term"], str(kinds))
            check("QC-06  ...and every entry names its STANDARD form",
                  all(d["standard"].endswith("_registry") for d in ns),
                  "all standard forms end in _registry")
            for d in ns[:6]:
                print("     %-7s %-32s -> %s" % (d["kind"], d["name"], d["standard"]))
        finally:
            conn.close()

        # ================================================================== E
        section("E. add_term REFUSES a NEW non-standard name, NEVER an existing term")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            res = tr.add_term(
                conn, "brand_new_register", term_kind="entity",
                definition="a brand new term that uses the non-standard spelling here",
                cite_ref="terminology_registry.py:1")
            check("QC-04  a NEW `_register` name is REFUSED",
                  res.get("ok") is False
                  and res.get("code") == "NONSTANDARD_REGISTER_NAME",
                  str(res.get("code")))
            check("QC-04  ...and the refusal carries the STANDARD form",
                  str(res.get("standard")).endswith("_registry"),
                  str(res.get("standard")))

            # QC-05 — AN EXISTING TERM IS NOT RETRO-REFUSED.
            # This is the exact defect my first version had: the gate ran BEFORE the
            # existing-term check, so re-registering a present term was refused.
            row = conn.execute(
                "SELECT term_key, parent_term_id FROM terminology_register "
                "WHERE term_key LIKE '%_register' AND is_active=1 LIMIT 1").fetchone()
            if row:
                again = tr.add_term(conn, str(row["term_key"]), term_kind="entity",
                                    parent_term_id=row["parent_term_id"],
                                    definition="a re-registration of an existing term",
                                    cite_ref="terminology_registry.py:1")
                check("QC-05  *** an ALREADY-EXISTING term is NOT retro-refused ***",
                      again.get("ok") is True and again.get("created") is False,
                      "%s created=%s" % (again.get("code"), again.get("created")))
            else:
                check("QC-05  no existing `_register` term to test with "
                      "(reported, not silently passed)", False,
                      "the register has no non-standard term")
        finally:
            conn.close()

        # ================================================================== F
        section("F. THE MIGRATION PLANNER — runs, reports the blast radius, writes NOTHING")
        import _unify_registry_naming as urn  # noqa: E402
        work = _copy(tmp)
        conn = _conn(work)
        try:
            objs_before = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
            ents_before = conn.execute(
                "SELECT COUNT(*) FROM entity_type_register WHERE register_table "
                "LIKE '%_register'").fetchone()[0]
            check("QC-06  the planner FINDS objects to rename",
                  len(urn.plan_objects(conn)) > 0,
                  "%d objects" % len(urn.plan_objects(conn)))
            ents = urn.plan_entities(conn)
            check("QC-06  the planner names the ENTITY LETTERS at risk",
                  len(ents) > 0, "%d letters" % len(ents))
            check("QC-06  ...and every affected letter's new table is the mechanical one",
                  all(e["new"] == tr.standard_registry_name(e["old"]) for e in ents),
                  "mechanical mapping holds")
            code = urn.plan_code()
            check("QC-06  the planner counts the CODE side too",
                  len(code) > 0,
                  "%d files / %d refs" % (len(code), sum(c["count"] for c in code)))
            # QC-07 — and it wrote NOTHING.
            objs_after = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
            ents_after = conn.execute(
                "SELECT COUNT(*) FROM entity_type_register WHERE register_table "
                "LIKE '%_register'").fetchone()[0]
            check("QC-07  *** the PLAN wrote nothing (object count unchanged) ***",
                  objs_before == objs_after, "%d == %d" % (objs_before, objs_after))
            check("QC-07  ...and the entity bindings are unchanged",
                  ents_before == ents_after, "%d == %d" % (ents_before, ents_after))
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
