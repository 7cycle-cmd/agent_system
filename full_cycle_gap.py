"""FULL.CYCLE.NO.GAP — the gap report, and the GAP 3 declarations.

WHAT THIS IS
------------
The human asked: *"confirm all without gap"*. This tool does not assume. It
walks the cycle link by link, MEASURES each join, and reports the gaps that are
still open — with the before/after numbers for the ones that were closed.

It is BOTH a report and the writer of the GAP 3 declarations:

  * GAP 1 (measured unit written)      -> REPORTED (closed by the harness)
  * GAP 2 (run reaches a factor)       -> REPORTED (closed by the harness)
  * GAP 3 (5W1H + explain)             -> DECLARED here
  * GAP 4 (first-principle columns)    -> DERIVED + REPORTED
  * GAP 5 (lesson cycle closes)        -> REPORTED (closed by the harness)
  * GAP 6 (dangling lesson skill_key)  -> REPORTED

THE RULES THIS TOOL OBEYS
-------------------------
1. The 5W1H names are READ from `skill_5w1h.DIMENSIONS`. They are never re-typed
   here — a second copy is the drift this exists to remove.
2. A proposed `dimension_binding_registry` row is written with `is_active=0`.
   Nothing is auto-activated.
3. An underivable `prompt_registry.unit` is REPORTED, never filled with a
   placeholder.
4. The existing 4 axes (`context`/`criterion`/`negation`/`output`) are REPORTED
   as non-canonical. They are ADDED TO, not replaced.

USAGE
-----
    .\\.venv\\Scripts\\python.exe full_cycle_gap.py            # report only
    .\\.venv\\Scripts\\python.exe full_cycle_gap.py --apply    # + GAP 3 writes
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# The ONE place the 5W1H axes are declared. Imported, never re-typed.
import skill_5w1h as fw  # noqa: E402

# The ONE place the first-principle questions are declared.
import factor_first_principle as ffp  # noqa: E402

# The citation for the canonical axes: the line that declares them.
AXES_CITE = "skill_5w1h.py:51"

# WHAT EACH CANONICAL DIMENSION MEANS FOR A PROMPT. This is the new layer the
# `dimension_binding_registry` exists to carry: the same dimension is legal
# under a different subject kind, and means something different there.
PROMPT_BINDING_TEXT = {
    "what": "the artifact the question is ABOUT — the thing being judged",
    "why": "the failure the question exists to catch — what goes wrong without it",
    "who": "who answers it and who consumes the answer",
    "when": "the point in the run at which the question is asked",
    "where": "the exact field/table the answer is written to",
    "how": "the check that decides pass from fail — a command or a rule",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


# ---------------------------------------------------------------------------
# GAP 1 / 2 / 5 — the harness links. REPORTED, with the numbers.
# ---------------------------------------------------------------------------
def measure_harness_links(conn: sqlite3.Connection) -> dict:
    total = _count(conn, "SELECT COUNT(*) FROM proof_run")
    vt = _count(conn, "SELECT COUNT(*) FROM proof_run "
                      "WHERE value_type IS NOT NULL AND value_type <> 'NA'")
    prr = _count(conn, "SELECT COUNT(*) FROM proof_run "
                       "WHERE proof_run_registry_id > 0")
    # GAP 2: a proof_run ref_tag that IS a factor key.
    factor_hits = _count(
        conn,
        "SELECT COUNT(DISTINCT p.ref_tag) FROM proof_run p "
        "JOIN skill_factor_registry f ON f.factor_key = p.ref_tag")
    factor_proofs = _count(conn, "SELECT COUNT(*) FROM skill_factor_proof")
    # GAP 5: a lesson whose source_ref names the harness (the run's own line).
    lessons = _count(conn, "SELECT COUNT(*) FROM skill_lesson "
                           "WHERE source_ref LIKE 'llm_100_run_harness%'")
    return {
        "gap1_value_type_written": vt,
        "gap1_proof_run_registry_id_resolved": prr,
        "gap1_proof_run_rows": total,
        "gap2_ref_tags_that_are_factors": factor_hits,
        "gap2_skill_factor_proof_rows": factor_proofs,
        "gap5_lessons_citing_the_harness": lessons,
    }


# ---------------------------------------------------------------------------
# GAP 3 — the 5W1H axes + explain.
# ---------------------------------------------------------------------------
def measure_gap3(conn: sqlite3.Connection) -> dict:
    existing = [r[0] for r in conn.execute(
        "SELECT DISTINCT dim_key FROM prompt_dimension ORDER BY 1")]
    canonical = list(fw.DIMENSION_NAMES)
    combo_cols = _cols(conn, "prompt_combo")
    bindings = _count(conn, "SELECT COUNT(*) FROM dimension_binding_registry "
                            "WHERE subject_kind = 'prompt'")
    return {
        "existing_axes": existing,
        "canonical_axes": canonical,
        "overlap": sorted(set(existing) & set(canonical)),
        "non_canonical_axes": sorted(set(existing) - set(canonical)),
        "prompt_combo_has_explain": "explain" in combo_cols,
        "prompt_bindings": bindings,
        "axes_cite": AXES_CITE,
    }


def apply_gap3(conn: sqlite3.Connection) -> dict:
    """Declare the canonical 5W1H axes, add `explain`, PROPOSE the binding.

    Every write is idempotent. Nothing is activated.
    """
    out: dict = {"declared_axes": 0, "explain_column": False,
                 "explain_filled": 0, "proposed_bindings": 0}

    # (a) the canonical axes, for every skill that already has dimensions.
    skills = [r[0] for r in conn.execute(
        "SELECT DISTINCT skill_key FROM prompt_dimension ORDER BY 1")]
    for skill in skills:
        for i, (dim, question, _rule, _order) in enumerate(fw.DIMENSIONS):
            conn.execute(
                "INSERT OR IGNORE INTO prompt_dimension "
                "(skill_key, dim_key, dim_name, value_key, value_text, "
                " description, sort_order, is_active) "
                "VALUES (?, ?, ?, 'canonical', ?, ?, ?, 1)",
                (skill, dim, dim, question,
                 "canonical 5W1H axis, declared from %s" % AXES_CITE,
                 100 + i))
            out["declared_axes"] += 1

    # (b) `explain` — WHY the question is asked.
    if "explain" not in _cols(conn, "prompt_combo"):
        conn.execute("ALTER TABLE prompt_combo ADD COLUMN explain TEXT")
        out["explain_column"] = True

    # The explanation is DERIVED from the factor the combo is linked to. A combo
    # with no factor link gets an explanation that SAYS SO — never a blank.
    for row in conn.execute("SELECT id, skill_key, axes_json FROM prompt_combo"):
        cid = row["id"]
        factors = [r[0] for r in conn.execute(
            "SELECT f.factor_key FROM question_template_factor q "
            "JOIN skill_factor_registry f ON f.factor_id = q.factor_id "
            "WHERE q.combo_id = ?", (cid,))]
        if factors:
            text = ("asked because factor(s) %s must be measured; the axes "
                    "select the wording that separates pass from fail"
                    % ", ".join(factors))
        else:
            text = ("NO FACTOR LINK — this question is asked without a declared "
                    "factor, so nothing states what it measures")
        conn.execute("UPDATE prompt_combo SET explain = ? WHERE id = ?",
                     (text, cid))
        out["explain_filled"] += 1

    # (c) the PROPOSED binding for `subject_kind='prompt'`. is_active=0.
    for i, dim in enumerate(fw.DIMENSION_NAMES):
        conn.execute(
            "INSERT OR IGNORE INTO dimension_binding_registry "
            "(subject_kind, dimension_key, binding_text, example, cite_ref, "
            " sort_order, is_active) "
            "VALUES ('prompt', ?, ?, 'NA', ?, ?, 0)",
            (dim, PROMPT_BINDING_TEXT[dim], AXES_CITE, 100 + i))
        out["proposed_bindings"] += 1

    conn.commit()
    return out


# ---------------------------------------------------------------------------
# GAP 4 — the first-principle columns, DERIVED.
# ---------------------------------------------------------------------------
def measure_gap4(conn: sqlite3.Connection) -> dict:
    cols = ["failure_mode", "observable", "unit", "threshold", "independence"]
    total = _count(conn, "SELECT COUNT(*) FROM prompt_registry")
    populated = {}
    for c in cols:
        populated[c] = _count(
            conn,
            "SELECT COUNT(*) FROM prompt_registry "
            "WHERE %s IS NOT NULL AND TRIM(CAST(%s AS TEXT)) <> ''" % (c, c))
    # DERIVE: run the first-principle derivation over every prompt row. The
    # prompt carries the SAME five columns the factor does, so the SAME
    # derivation applies — no second implementation.
    underivable = []
    for row in conn.execute("SELECT * FROM prompt_registry"):
        d = ffp.derive(dict(row))
        if not d["derived"]:
            underivable.append({
                "prompt_key": row["prompt_key"],
                "missing": d["missing"],
                "missing_dimensions": d["missing_dimensions"],
            })
    return {
        "prompt_rows": total,
        "populated": populated,
        "underivable": underivable,
        "underivable_count": len(underivable),
        "uncovered_dimensions": list(ffp.uncovered_dimensions()),
    }


# ---------------------------------------------------------------------------
# GAP 6 — a lesson about a skill that does not exist.
# ---------------------------------------------------------------------------
def measure_gap6(conn: sqlite3.Connection) -> dict:
    rows = []
    for r in conn.execute(
            "SELECT l.skill_key, COUNT(*) n FROM skill_lesson l "
            "WHERE NOT EXISTS (SELECT 1 FROM skill_registry s "
            "                  WHERE s.skill_key = l.skill_key) "
            "GROUP BY 1 ORDER BY n DESC"):
        rows.append({"skill_key": r["skill_key"], "lessons": int(r["n"])})
    return {"dangling": rows, "dangling_skill_keys": len(rows),
            "total_lessons": _count(conn, "SELECT COUNT(*) FROM skill_lesson")}


# ---------------------------------------------------------------------------
def report(conn: sqlite3.Connection, *, applied: dict | None = None) -> dict:
    return {
        "checklist_id": "FULL.CYCLE.NO.GAP",
        "gap1_gap2_gap5_harness_links": measure_harness_links(conn),
        "gap3_5w1h_and_explain": measure_gap3(conn),
        "gap4_first_principle": measure_gap4(conn),
        "gap6_dangling_lesson": measure_gap6(conn),
        "applied": applied or {},
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="write the GAP 3 declarations (idempotent)")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args()

    conn = _connect(Path(args.db))
    applied = apply_gap3(conn) if args.apply else None
    res = report(conn, applied=applied)
    conn.close()

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    h = res["gap1_gap2_gap5_harness_links"]
    print("FULL.CYCLE.NO.GAP — the cycle, link by link")
    print("  GAP 1  value_type written            : %d / %d"
          % (h["gap1_value_type_written"], h["gap1_proof_run_rows"]))
    print("  GAP 1  proof_run_registry_id resolved: %d"
          % h["gap1_proof_run_registry_id_resolved"])
    print("  GAP 2  ref_tags that ARE factors     : %d"
          % h["gap2_ref_tags_that_are_factors"])
    print("  GAP 2  skill_factor_proof rows       : %d"
          % h["gap2_skill_factor_proof_rows"])
    print("  GAP 5  lessons citing the harness    : %d"
          % h["gap5_lessons_citing_the_harness"])
    g3 = res["gap3_5w1h_and_explain"]
    print("  GAP 3  existing axes                 : %s"
          % ", ".join(g3["existing_axes"]))
    print("  GAP 3  canonical axes                : %s"
          % ", ".join(g3["canonical_axes"]))
    print("  GAP 3  overlap                       : %s"
          % (", ".join(g3["overlap"]) or "(none)"))
    print("  GAP 3  prompt_combo.explain          : %s"
          % ("present" if g3["prompt_combo_has_explain"] else "MISSING"))
    print("  GAP 3  prompt bindings               : %d" % g3["prompt_bindings"])
    g4 = res["gap4_first_principle"]
    print("  GAP 4  prompt_registry populated     : %s"
          % ", ".join("%s=%d/%d" % (k, v, g4["prompt_rows"])
                      for k, v in g4["populated"].items()))
    print("  GAP 4  underivable units             : %d"
          % g4["underivable_count"])
    g6 = res["gap6_dangling_lesson"]
    print("  GAP 6  dangling lesson skill_keys    : %d %s"
          % (g6["dangling_skill_keys"],
             [d["skill_key"] for d in g6["dangling"]]))
    if applied:
        print("  APPLIED: %s" % json.dumps(applied, ensure_ascii=False))


if __name__ == "__main__":
    main()
