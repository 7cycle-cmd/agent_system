# -*- coding: utf-8 -*-
"""question_generator.py — the QUESTIONS a registry's own shape demands.

THE HUMAN'S SPEC (2026-09-27)
-----------------------------
    "it should = question generation, as we have a lot of experience for that
     to help *_registry"
    "question flow can narrow area -> to the point, that is the way to have work
     for LLM ( not 7B only)"

    "narrow area -> to the point = proof"
    "point -> collect evidence -> expend area = research"

So this module produces the NARROWING side: the questions that take "a registry"
(a whole table, 3620 fields) and reduce it to the POINT — the specific column
whose declared type does not match its live type, or the name that is not
registered.

WHAT THIS MODULE DOES NOT DO — AND WHY THAT IS THE WHOLE DESIGN
---------------------------------------------------------------
It does NOT invent a question format, and it does NOT re-implement an evidence
reader. BOTH already exist and are measured:

    logic_generator.generate(spec)   the question LIST (2N + 4, each with a YES
                                     form AND a NO form)
    logic_generator.answer_by_evidence(conn, spec, question)
                                     the EVIDENCE answer, with its query
    logic_generator.oracle_from_evidence(...)
                                     the pass/fail oracle over that answer

MEASURED, and this is why a second engine would be a defect and not a feature:
`answer_by_evidence` REFUSES an answer with no query (`ANSWER_WITHOUT_EVIDENCE`),
and `_q()` REFUSES a question with no NO form. A new generator would have to
re-earn both refusals, and the only way to re-earn them is to become a copy.

This module is therefore a BINDING, in the exact sense `question_flow.
steps_from_questions` is a binding: it takes a registry, derives the spec from
the registry's OWN declared shape, and hands the questions back WITH the two
facts a caller cannot get from the raw list —

    layer    which of `question_flow.LAYERS` the question belongs to (so it can
             become a step)
    column   WHICH COLUMN it tests, read from `PRAGMA table_info` — never
             hardcoded, because a hardcoded column index goes stale the moment
             the table gains a column (the defect `_ev_field_name` records)

A REGISTRY WITH NO DECLARED SHAPE IS REFUSED, NOT SKIPPED
----------------------------------------------------------
`spec_from_table` already raises `SpecError` for a table with no `db_schema.*_DDL`
declaration, because a spec with no unit cannot be measured against. That refusal
is CARRIED OUT here, with the table named — it is never turned into an empty list,
because an empty list reads as "this registry needs no questions", which is the
opposite of the truth.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"

# The name of THIS file, used as the `cite` on every finding it reports. A
# finding without a checkable reference is discarded at the write site, so the
# reader of a question row can always see which code produced it.
CITE = "question_generator.py:questions_for"

# THE QUESTION FAMILIES THAT ASK ABOUT **CODE**, AND SO NEED A FILE.
#
# WHY THIS IS A NAMED CONSTANT AND NOT AN INLINE CHECK. MEASURED (2026-09-27),
# and it is the defect this file's `--all` run exposed: `literal_derivable` and
# `param_shadowed` are answered by `hardcode_scan.scan_file` /
# `code_shape.redefined_params`, both of which read `spec['path']`. A spec built
# by `spec_from_table` CARRIES NO PATH — a table has no file — so both readers
# return `NO` with the ref `hardcode_scan.scan_file(<no path>)`.
#
# MEASURED ON EVERY REGISTRY: exactly these two families, on all 47 specable
# registries, i.e. 94 refs that are `<no path>`. A worker cannot open `<no path>`,
# so those two questions can never be FIXED — they can only be skipped.
#
# THE QUESTION IS NOT A DEFECT; ASKING IT OF A TABLE IS. It is a perfectly good
# question ABOUT A FILE, and `spec_from_skill` (which carries a path) answers it
# for real. So the fix is NOT to delete the families — it is to mark them
# NOT APPLICABLE when the spec carries no file, and to count them OUT of the
# area. An inapplicable question counted in the area would inflate the area and
# make every narrowing look more successful than it was.
CODE_SOURCE_FAMILIES: tuple[str, ...] = ("literal_derivable", "param_shadowed")


def _needs_a_file(question_id: str) -> bool:
    """True when the question is answered from a FILE, so needs `spec['path']`."""
    return str(question_id or "").strip() in CODE_SOURCE_FAMILIES


def _has_a_file(spec: dict[str, Any]) -> bool:
    """True when the spec names a file the code readers can actually open."""
    return bool(str(spec.get("path") or spec.get("file") or "").strip())


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """The table's columns, from `PRAGMA table_info`. The ONE read of the shape.

    Delegating to `logic_generator._pragma_columns` would be a second import for
    one `PRAGMA`; this module reads it directly so a caller can see the question's
    `column` is a LIVE read and not a remembered one.
    """
    return [dict(r) for r in conn.execute("PRAGMA table_info(%s)" % table)]


def registries(conn: sqlite3.Connection, *,
               pattern: str = "%regist%") -> list[dict[str, Any]]:
    """Every REGISTRY in `db_table_registry`, with its row count MEASURED.

    WHY `db_table_registry` AND NOT A NAME GLOB OVER `sqlite_master`. A glob over
    object names matches SHADOWS: `terminology_registry_backup_20260926` matches
    `%register%` and is not a registry. `db_table_registry` is the table that
    DECLARES what is a registered table, and `_ev_registered` already treats it as
    the authority — so this reads the SAME source rather than a convenient one.

    The row count is measured here and REPORTED (`row_count`), which is the
    opposite of the defect `definition_from_evidence.describe` records: that
    function must NOT write a live count into a definition, because a definition
    is a durable claim and the count moves. Here the count is a SNAPSHOT for
    ordering the work, and the caller is told it is one.
    """
    rows = conn.execute(
        "SELECT db_table_id, table_key, is_active FROM db_table_registry "
        "WHERE table_key LIKE ? ORDER BY table_key", (pattern,)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        table = str(r["table_key"])
        try:
            n = conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        except sqlite3.Error as exc:
            # A DECLARED table that cannot be counted is REPORTED, never dropped.
            out.append({"table": table, "db_table_id": int(r["db_table_id"]),
                        "is_active": int(r["is_active"]), "row_count": None,
                        "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        out.append({"table": table, "db_table_id": int(r["db_table_id"]),
                    "is_active": int(r["is_active"]), "row_count": int(n)})
    return out


def _column_for(question_id: str, spec: dict[str, Any]) -> str:
    """WHICH COLUMN a question tests, from the SPEC — '' when it tests none.

    The per-field families carry their index (`field_3_name`), and the index is
    resolved against the spec's DECLARED column list. That list came from the code
    DDL (`spec_from_table`), while the question is answered against the LIVE table
    (`_ev_field_name` looks the name up by NAME, per the `code_registry`
    measurement) — so this reports a NAME, not a position.

    A question about the table AS A WHOLE (`db_driven`, `registered`) tests no
    single column, and '' says so. A guessed `"id"` would make every row look like
    it tests the primary key.
    """
    qid = str(question_id or "")
    if not qid.startswith("field_"):
        return ""
    parts = qid.split("_")
    if len(parts) < 3:
        return ""
    try:
        n = int(parts[1])
    except ValueError:
        return ""
    names = spec.get("field_names") or []
    return str(names[n - 1]) if 1 <= n <= len(names) else ""
    qid = str(question_id or "")
    if not qid.startswith("field_"):
        return ""
    parts = qid.split("_")
    if len(parts) < 3:
        return ""
    try:
        n = int(parts[1])
    except ValueError:
        return ""
    names = spec.get("field_names") or []
    return str(names[n - 1]) if 1 <= n <= len(names) else ""


def questions_for_registry(conn: sqlite3.Connection, table: str, *,
                           code_questions: bool = True,
                           register_wording: bool = True) -> dict[str, Any]:
    """The questions ONE registry's shape demands. REFUSES an unspecable table.

    Returns:
        {ok, table, count, columns_read, questions, layers, refused, cite}

    `questions` is the list `logic_generator.generate` produces, EXTENDED with two
    per-question facts: `layer` (from `question_flow.layer_for_question`) and
    `column` (from the spec, see `_column_for`).

    `columns_read` is the live column COUNT, so a caller can see the spec was
    derived from a real table rather than accepting a number on trust.

    `refused` lists the questions `answer_by_evidence` could not answer, WITH
    their refusal code — never silently dropped, because a question with no
    evidence is a question the flow cannot measure.

    `layers` is the SET of layers the questions produce, so a caller can see at a
    glance whether the list touches both `tdd` and `ontology` — and an unknown
    family is caught here (it would make `steps_from_questions` refuse), not at
    step-writing time.
    """
    import logic_generator as lg
    import question_flow as qf

    table = str(table or "").strip()
    if not table:
        return {"ok": False, "code": "MISSING_TABLE",
                "reason": "a registry name is required", "cite": CITE}

    try:
        spec = lg.spec_from_table(conn, table)
    except lg.SpecError as exc:
        # CARRIED OUT, NOT SWALLOWED. `spec_from_table` refuses a table with no
        # declared shape because a spec with no UNIT cannot be measured against;
        # turning that into `questions: []` would report "nothing to ask about
        # this registry", which reads as a clean result.
        return {"ok": False, "code": "NOT_SPECABLE", "table": table,
                "reason": str(exc), "cite": CITE}

    result = lg.generate(spec, code_questions=code_questions,
                         conn=conn if register_wording else None,
                         subject_kind="table")

    questions: list[dict[str, Any]] = []
    unknown_layers: list[str] = []
    # THE EVIDENCE ANSWER IS TAKEN **PER QUESTION**, AT GENERATION TIME.
    #
    # WHY HERE AND NOT AT RUN TIME. MEASURED (2026-09-27): on EVERY one of the 47
    # specable registries, TWO questions answer with a `<placeholder>` evidence ref
    # (`hardcode_scan.scan_file(<no path>)` / `code_shape.redefined_params(<no
    # path>)`) — 94 untraceable answers in total. A worker cannot open `<no path>`,
    # so those two questions can never be FIXED; they can only be skipped, and a
    # skipped question inside a "point" silently shrinks the work while the report
    # still counts the item.
    #
    # Marking it HERE means the defect is VISIBLE at the point the questions are
    # produced, instead of surfacing only after a flow has run. The predicate is
    # `research_direction.is_traceable` — ONE implementation, so "traceable" cannot
    # mean two things in two modules.
    import research_direction as rd

    # The generator's OWN question dicts, keyed by id, so `answer_by_evidence` gets
    # the shape it expects (`question_id` + its family).
    gen_by_id = {str(q.get("question_id")): q for q in result["questions"]}

    for q in result["questions"]:
        qid = str(q.get("question_id") or "")
        layer = qf.layer_for_question(qid)
        if not layer:
            unknown_layers.append(qid)
        # APPLICABILITY — decided BEFORE the evidence call, because a question
        # that cannot apply to this subject has no evidence to gather. MEASURED:
        # the two CODE-source families need a file, and a TABLE spec carries
        # none, so on all 47 registries they answered `<no path>`.
        applicable = not (_needs_a_file(qid) and not _has_a_file(spec))
        if not applicable:
            questions.append({
                "question_id": qid,
                "question": str(q.get("question") or ""),
                "dimension": str(q.get("dimension") or ""),
                "yes_form": str(q.get("yes_form") or ""),
                "no_form": str(q.get("no_form") or ""),
                "layer": layer,
                "column": _column_for(qid, spec),
                "wording_source": str(q.get("wording_source") or ""),
                "applicable": False,
                "skip_reason": ("answered from a FILE, but this subject is the "
                                "table %r, which carries no path — ask it of a "
                                "skill/file spec instead" % table),
                "evidenced": False,
                "evidence_ref": "",
                "traceable": False,
                "cite": CITE,
            })
            continue
        # THE EVIDENCE, WITH ITS REF — measured now, not asserted later.
        ev = lg.answer_by_evidence(conn, spec, gen_by_id[qid])
        ref = str(ev.get("evidence_ref") or "") if ev.get("ok") else ""
        questions.append({
            "question_id": qid,
            "question": str(q.get("question") or ""),
            "dimension": str(q.get("dimension") or ""),
            "yes_form": str(q.get("yes_form") or ""),
            "no_form": str(q.get("no_form") or ""),
            "layer": layer,
            "column": _column_for(qid, spec),
            "wording_source": str(q.get("wording_source") or ""),
            "applicable": True,
            "skip_reason": None,
            # `evidenced` — the question HAS an answer with a query.
            # `traceable` — that query is a USABLE citation (no `<placeholder>`).
            # They are DIFFERENT, and the measurement above is exactly the case
            # where the first is True and the second is False.
            "evidenced": bool(ev.get("ok")),
            "evidence_ref": ref,
            "traceable": bool(ev.get("ok")) and rd.is_traceable(ref),
            "cite": CITE,
        })

    # THE SHAPE CHECK, APPLIED TO EVERY QUESTION AND REPORTED AS A SET. The human
    # measured the ABSENT shape at recall NO 0.70 while the per-key checklist
    # measured 1.00 CERTIFIED over 5 runs, so an ABSENT-shaped question is a
    # question the target model fails. `question_shape` is the ONE implementation
    # of that rule and it does not raise, so all offenders are collected here.
    unanswerable = []
    for q in questions:
        s = qf.question_shape(q["question"])
        if not s.get("ok"):
            unanswerable.append({"question_id": q["question_id"],
                                 "shape": s.get("shape"), "why": s.get("why"),
                                 "fix": s.get("fix")})

    # DERIVED FROM THE QUESTIONS ALREADY ANSWERED (no second `answer_by_evidence`
    # call): a question with no answer, and a question whose answer has no usable
    # citation, are TWO different reports and both are surfaced.
    refused: list[dict[str, Any]] = [
        {"question_id": q["question_id"], "code": "NO_EVIDENCE_ANSWER",
         "message": "the evidence reader returned no answer for this question"}
        for q in questions if q["applicable"] and not q["evidenced"]]
    untraceable: list[dict[str, Any]] = [
        {"question_id": q["question_id"], "column": q["column"],
         "evidence_ref": q["evidence_ref"],
         "why": ("the evidence ref is a `<placeholder>`, so a worker cannot open "
                 "it and the question can never be fixed")}
        for q in questions if q["applicable"] and q["evidenced"] and not q["traceable"]]
    # THE AREA — ONLY THE APPLICABLE QUESTIONS. A question that cannot apply to
    # this subject is not part of the area to narrow, and counting it would inflate
    # the area so that every narrowing looked larger than it was. `area` is
    # therefore a MEASURED unit (`unit` names it) and the inapplicable questions
    # are reported SEPARATELY, by name, so the exclusion is visible.
    applicable = [q for q in questions if q["applicable"]]
    inapplicable = [q for q in questions if not q["applicable"]]

    return {
        "ok": not unknown_layers and not unanswerable,
        "code": ("QUESTIONS_OK"
                 if not unknown_layers and not unanswerable
                 else "QUESTIONS_UNUSABLE"),
        "table": table,
        "subject_kind": spec.get("kind"),
        "count": int(result["count"]),
        "area": len(applicable),
        "unit": "question",
        "columns_read": len(_columns(conn, table)),
        "field_count": spec.get("field_count"),
        "shape_source": spec.get("shape_source"),
        "questions": questions,
        "applicable_count": len(applicable),
        "inapplicable": inapplicable,
        "layers": sorted({q["layer"] for q in questions if q["layer"]}),
        "unknown_layers": unknown_layers,
        "unanswerable": unanswerable,
        "refused": refused,
        # TRACEABILITY IS REPORTED, NOT ASSUMED. `fully_traceable` covers the
        # APPLICABLE questions only — an inapplicable question has no evidence by
        # construction, and counting it as untraceable would make the metric
        # unreadable by making it constant.
        "fully_traceable": not untraceable,
        "traceable_count": sum(1 for q in applicable if q["traceable"]),
        "untraceable": untraceable,
        "formula": result.get("formula"),
        "cite": CITE,
    }


def all_registry_questions(conn: sqlite3.Connection, *,
                           pattern: str = "%regist%",
                           limit: int | None = None
                           ) -> dict[str, Any]:
    """Every registry, its questions, and — LOUDLY — the ones that were refused.

    A registry that cannot be specced is NOT omitted: it appears with `ok: False`
    and its `reason`. MEASURED, and this is the point of the function: the user's
    requirement was "must work for EVERY registry in `entity_type_registry`, none
    skipped" — so a refusal has to be VISIBLE in the result, not absent from it.
    """
    regs = registries(conn, pattern=pattern)
    if limit is not None:
        regs = regs[:int(limit)]
    results: list[dict[str, Any]] = []
    for r in regs:
        if r.get("error"):
            results.append({"ok": False, "code": "UNCOUNTABLE",
                            "table": r["table"], "reason": r["error"],
                            "cite": CITE})
            continue
        results.append(questions_for_registry(conn, r["table"]))
    ok = [x for x in results if x.get("ok")]
    return {
        "ok": True,
        "registry_count": len(results),
        "specced": len(ok),
        "refused": len(results) - len(ok),
        "question_total": sum(int(x.get("count") or 0) for x in ok),
        "results": results,
        "cite": CITE,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: `question_generator.py [--table NAME] [--all] [--limit N]`."""
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", help="one registry")
    ap.add_argument("--all", action="store_true", help="every registry")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if args.table:
            out = questions_for_registry(conn, args.table)
        else:
            out = all_registry_questions(conn, limit=args.limit)
    finally:
        conn.close()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
