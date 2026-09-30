# -*- coding: utf-8 -*-
"""_proof_registry_naming.py — the spelling standard is `_registry`, not `_registry`.

THE RULING (2026-09-27), verbatim: "be unified by registry not register".

THE PROPERTY, stated so a machine can check it:

  * the gate ACCEPTS `_registry` and REFUSES `_registry` for a NEW term, naming the
    standard form;
  * it does NOT touch a name that does not claim to be a register;
  * `add_term` does NOT retro-refuse an ALREADY-EXISTING term (a mass refusal of
    live data is the defect every gate here avoids);
  * the mapping is MECHANICAL (`x_registry` -> `x_registry`), not a per-name list;
  * the migration's `--plan` runs, reports the FULL blast radius, and writes NOTHING.

THE DEFECT THIS PROOF EXISTS FOR: my own first version of the gate enforced the
OPPOSITE direction (`version_registry` -> "did you mean `version_registry`"). A gate
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

# 🔴 THE 16 `*_register` GHOST TABLES, DROPPED FROM LIVE 2026-09-29.
# MEASURED (round 10): ZERO production modules use one as a SQL identifier,
# ZERO non-ghost table has an inbound FK to one, every one has a live
# `*_registry` twin, and the only non-empty one (`derived_column_register`, 2
# rows) duplicated its twin. Dropped by `_drop_ghost_register_tables.py` and
# recorded in `schema_migration_log` WITH the CREATE statements (reversible).
# A name from this set appearing in the migration report again is a REGRESSION.
_DROPPED_GHOSTS = {
    "code_location_register", "code_register", "component_register",
    "derived_column_register", "dimension_binding_register", "identity_register",
    "mode_register", "mode_right_register", "phase_register", "prompt_register",
    "skill_register", "study_register", "terminology_register", "unit_register",
    "wording_register", "workflow_register",
}


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
                r = tr.check_registry_name(conn, good)
                check("QC-01  %r is ACCEPTED (standard suffix)" % good,
                      r["ok"] is True, str(r.get("why"))[:50])
            for bad in ("version_register", "channel_register", "terminology_register"):
                r = tr.check_registry_name(conn, bad)
                check("QC-02  %r is REFUSED (non-standard suffix)" % bad,
                      r["ok"] is False and r["code"] == "NONSTANDARD_registry_NAME",
                      str(r.get("code")))
                check("QC-02  ...and it NAMES the standard form",
                      r.get("near") == tr.standard_registry_name(bad),
                      "standard=%s" % r.get("near"))
            # The direction is the whole point, and I got it BACKWARDS first.
            r = tr.check_registry_name(conn, "version_register")
            check("QC-02  *** the suggestion is `_registry`, NOT `_registry` *** "
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
                r = tr.check_registry_name(conn, other)
                check("QC-03  %r is UNTOUCHED (applies=False)" % other,
                      r["ok"] is True and r.get("applies") is False,
                      "applies=%s" % r.get("applies"))
            # `looks_like_a_registry` must be the ONE predicate both use.
            check("QC-03  `looks_like_a_registry` recognises BOTH spellings",
                  tr.looks_like_a_registry("x_registry")
                  and tr.looks_like_a_registry("x_registry"))
            check("QC-03  `is_registry_standard` accepts a non-register name",
                  tr.is_registry_standard("worker_identity") is True)
        finally:
            conn.close()

        # ================================================================== C
        section("C. THE MAPPING IS MECHANICAL, not a per-name list")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            for a, b in (("x_registry", "x_registry"),
                         ("channel_registry", "channel_registry"),
                         ("a_b_c_registry", "a_b_c_registry"),
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
        section("D. THE SCALE — the migration REMOVED them; the report still FINDS one")
        work = _copy(tmp)
        conn = _conn(work)
        try:
            ns = tr.nonstandard_registry_names(conn)
            # MEASURED 2026-09-28: the migration ran. The OLD check asserted
            # `len(ns) > 20` -- that measured the PRE-migration state and went
            # red the moment the migration SUCCEEDED. The property is not
            # "there are many"; it is "no TABLE and no TERM is left".
            #
            # MEASURED: the report holds exactly 1 entry, `idx_pair_qc_run_registry`,
            # an INDEX on `pair_qc_run` -- a table that is NOT renamed, so its
            # index name must not be renamed either (`name_unify.py` refuses it
            # as TABLE_NOT_RENAMED). An index is not a name carrier of its own.
            # MEASURED 2026-09-29 (round 9): the report held the INDEX entry above
            # AND the 16 `*_register` GHOST TABLES (empty tables whose creator no
            # longer exists, each with a live `*_registry` twin).
            #
            # 🔴 ROUND 10: THE GHOSTS WERE DROPPED, SO THEY ARE NO LONGER
            # EXEMPTED — THEY MUST BE ABSENT. Round 9's exclusion was right about
            # the INDEX (an index is not a name carrier) and right about the
            # principle (a name no code carries cannot be "non-standard"), but the
            # ghost tables were never the real finding: MEASURED, ZERO production
            # modules use one, ZERO inbound FKs point at one, and the only
            # non-empty one duplicated its twin. They are gone
            # (`_drop_ghost_register_tables.py`, recorded in
            # `schema_migration_log` WITH their CREATE statements).
            #
            # So the check is now TWO-SIDED, and it can fail in the direction that
            # matters: a ghost REAPPEARING is a regression, not an exemption.
            left = [d for d in ns
                    if not d["name"].startswith("idx_")
                    and d["name"] not in _DROPPED_GHOSTS]
            check("QC-06  no LIVE, DECLARED name is left non-standard "
                  "(exempt: an index name, and a recorded ghost while the "
                  "migration is mid-flight)",
                  not left, str([(d["kind"], d["name"]) for d in left]))
            # POSITIVE CONTROL: the exclusion must not be a blanket. If it covered
            # every name the check would pass on ANY input, which is the
            # `vacuous_pass_empty_set` defect — so assert the report really FOUND
            # the exempt entries rather than finding nothing at all.
            check("QC-06 ...and the report DID find the exempt names (not a "
                  "vacuous pass)",
                  any(d["name"] in _DROPPED_GHOSTS or d["name"].startswith("idx_")
                      for d in ns),
                  "the detector reported nothing at all, so the check above "
                  "passed over an EMPTY set")
            # 🔴 THE REGRESSION DIRECTION. The 16 ghosts were DROPPED from live;
            # if the migration report NAMES one again, either the migration did
            # not run on this copy or a ghost has been recreated — both are
            # findings, and both are the way the cleanup gets silently undone.
            back = sorted(d["name"] for d in ns if d["name"] in _DROPPED_GHOSTS)
            check("QC-06b no DROPPED ghost table is REPORTED as non-standard "
                  "(a report naming one means it is back)",
                  not back, "reported: %s" % (back or "none"))
            # POSITIVE CONTROL: a detector that finds nothing must prove it CAN
            # find something. Plant a non-standard object on the COPY.
            conn.execute("CREATE TABLE probe_thing_register (id INTEGER PRIMARY KEY)")
            ns2 = tr.nonstandard_registry_names(conn)
            conn.execute("DROP TABLE probe_thing_register")
            check("QC-06  *** positive control: a planted `_registry` IS found ***",
                  any(d["name"] == "probe_thing_register" for d in ns2),
                  "%d entries after planting" % len(ns2))
            check("QC-06  ...and every entry names its STANDARD form",
                  all(d["standard"].endswith("_registry") for d in ns2),
                  "all standard forms end in _registry")
            for d in ns2[:6]:
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
            check("QC-04  a NEW `_registry` name is REFUSED",
                  res.get("ok") is False
                  and res.get("code") == "NONSTANDARD_registry_NAME",
                  str(res.get("code")))
            check("QC-04  ...and the refusal carries the STANDARD form",
                  str(res.get("standard")).endswith("_registry"),
                  str(res.get("standard")))

            # QC-05 — AN EXISTING TERM IS NOT RETRO-REFUSED.
            # This is the exact defect my first version had: the gate ran BEFORE the
            # existing-term check, so re-registering a present term was refused.
            row = conn.execute(
                "SELECT term_key, parent_term_id FROM terminology_registry "
                "WHERE term_key LIKE '%_registry' AND is_active=1 LIMIT 1").fetchone()
            if row:
                again = tr.add_term(conn, str(row["term_key"]), term_kind="entity",
                                    parent_term_id=row["parent_term_id"],
                                    definition="a re-registration of an existing term",
                                    cite_ref="terminology_registry.py:1")
                check("QC-05  *** an ALREADY-EXISTING term is NOT retro-refused ***",
                      again.get("ok") is True and again.get("created") is False,
                      "%s created=%s" % (again.get("code"), again.get("created")))
            else:
                check("QC-05  no existing `_registry` term to test with "
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
                "SELECT COUNT(*) FROM entity_type_registry WHERE register_table "
                "LIKE '%_registry'").fetchone()[0]
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
                "SELECT COUNT(*) FROM entity_type_registry WHERE register_table "
                "LIKE '%_registry'").fetchone()[0]
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
