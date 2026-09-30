# -*- coding: utf-8 -*-
"""register_question_center.py — register the Question Center's WORDS and its
ELEMENTS, in that order.

WHY WORDS FIRST (and why this file exists at all)
--------------------------------------------------
The `terminology-register` rule, stated by the human:

    "no name without a registered term" — the term must exist in
    `terminology_registry` with a definition and a citation BEFORE the name is
    used, because a name that collides with another system's word makes every
    later reader pick the wrong one.

MEASURED (2026-09-27) before this script ran: of the words the Question Center
shows, only THREE were registered — `question_flow` (id 1291), `evidence` (1453)
and `step` (1505). `question`, `question_center`, `narrow`, `research`, `point`,
`area`, `frontier`, `direction` and `traceable` were all UNREGISTERED, so the page
was rendered in words the system does not own.

Then the ELEMENTS. `ui_element_registry` REFUSES an unknown `term_key` and an
unknown `unit_key`, so the element rows CANNOT be written before the terms are —
the refusal is what enforces the order, and this script runs the two steps in the
order the gate demands rather than working around it.

THE UNITS ARE THE EXISTING ONES, ON PURPOSE. MEASURED: `unit_registry` holds four
active units (`boolean`, `count`, `pct`, `score_0_100`). A question COUNT is a
`count`, and WHICH count it is comes from the `population` column — the same shape
`ui_element_registry.py`'s own seed uses for `sessions.num.measurements`
(`unit_key="count"`, `population="count of coordinate_session rows linked to this
session — 8 means measured 8 times, NOT 8 sessions"`). Inventing a `question` unit
would make "count of questions" and "count of rows" incomparable while both are
plain counts — the wrong-population defect, one layer down.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"

CITE = "register_question_center.py:TERMS/SEED_ELEMENTS"

# ---------------------------------------------------------------------------
# THE WORDS. Each one carries the definition a reader needs to tell it from the
# OTHER direction — because the two directions are the point of the page, and a
# pair of words that are not defined against each other is a pair nobody can use.
# ---------------------------------------------------------------------------
TERMS: tuple[dict[str, str], ...] = (
    dict(term_key="question_center",
         definition=("The page at /llm-tasks/question: per registry, the questions "
                     "that registry's own shape demands, and the result of asking "
                     "them. It is the SAME page for the NARROW and the RESEARCH "
                     "direction, because a direction is a property of a flow, not "
                     "a second page."),
         cite_ref="llm_task_monitor_ui/src/app.js:124"),
    dict(term_key="question",
         definition=("A check whose answer comes from EVIDENCE rather than an "
                     "opinion, and which carries BOTH a YES form and a NO form. A "
                     "question with only its YES form cannot fail, so a run would "
                     "certify a model that answers YES to everything "
                     "(logic_generator._q refuses it)."),
         cite_ref="logic_generator.py:878"),
    dict(term_key="narrow",
         definition=("The direction AREA -> POINT: ask every applicable question "
                     "and keep the ones that did NOT conform. It is a PROOF, "
                     "because it ends at a specific thing to fix, and its result is "
                     "smaller than its input. See `research` for the opposite."),
         cite_ref="research_direction.py:65"),
    dict(term_key="research",
         definition=("The direction POINT -> AREA: start at one point, collect "
                     "evidence, and EXPAND into the area that evidence opens. It is "
                     "the opposite of `narrow` and it GROWS. A research MUST name "
                     "the `narrow` flow that would close it — a research that "
                     "cannot name its closing question is an exploration with no "
                     "end (research_direction.ExplorationWithNoEnd)."),
         cite_ref="research_direction.py:65"),
    dict(term_key="point",
         definition=("WHAT A NARROW LEAVES: the set of questions that did NOT "
                     "conform, each with a MEASURED unit and a TRACEABLE evidence "
                     "reference. It is a POINT rather than an area because every "
                     "item in it can be opened and fixed. An item whose evidence is "
                     "a `<placeholder>` is NOT traceable and is reported separately."),
         cite_ref="research_direction.py:496"),
    dict(term_key="area",
         definition=("WHAT A NARROW STARTS FROM: every question the subject's own "
                     "shape demands that CAN be asked of it. A question that needs "
                     "a FILE is INAPPLICABLE for a TABLE and is counted OUT of the "
                     "area — counted in, it would inflate the area and make every "
                     "narrowing look more successful than it was."),
         cite_ref="question_generator.py:91"),
    dict(term_key="frontier",
         definition=("WHAT A RESEARCH LEAVES: the point it started at, the "
                     "evidence it collected, the area that opened, and the `narrow` "
                     "flow that would close it. Stored in `research_frontier` — the "
                     "ONE new table this feature adds, because the triple is a fact "
                     "nothing else records."),
         cite_ref="research_direction.py:98"),
    dict(term_key="direction",
         definition=("Which way a flow MOVES: `narrow` shrinks an area to a point, "
                     "`research` grows a point into an area. The two are RECIPROCAL "
                     "and that is MEASURED, not asserted — a narrow must shrink and "
                     "a research must grow, and if both shrink one of them is "
                     "mislabelled (research_direction.directions_are_reciprocal)."),
         cite_ref="research_direction.py:556"),
    dict(term_key="traceable",
         definition=("A citation a reader can OPEN and RE-RUN: non-empty and free of "
                     "any `<placeholder>`. It is STRICTER than 'has a reference' — "
                     "`hardcode_scan.scan_file(<no path>)` is non-empty and NOT "
                     "traceable, and a point item carrying it can never be fixed, "
                     "only skipped (research_direction.is_traceable)."),
         cite_ref="research_direction.py:496"),
    dict(term_key="registry",
         definition=("A TABLE that DECLARES things: it lists what exists in some "
                     "domain so a reader can look a name up instead of hard-coding "
                     "it. The standard suffix is `_registry` (v3 naming), and "
                     "`_registry` is the non-standard form — "
                     "terminology_registry.standard_registry_name reports the "
                     "standard spelling of either."),
         cite_ref="terminology_registry.py:408"),
)


def register_terms(conn: sqlite3.Connection, *, commit: bool = True) -> dict:
    """Register the Question Center's words. Idempotent, reports every refusal."""
    import terminology_registry as tr
    tr.ensure_schema(conn)
    added, refused = 0, []
    for t in TERMS:
        res = tr.add_term(conn, t["term_key"], definition=t["definition"],
                          cite_ref=t["cite_ref"], term_kind="part",
                          is_active=1, commit=False)
        if res.get("ok"):
            added += 1 if res.get("created", True) else 0
        else:
            refused.append({"term_key": t["term_key"], "code": res.get("code"),
                            "message": str(res.get("message"))[:160]})
    if commit:
        conn.commit()
    return {"ok": not refused, "declared": len(TERMS), "added": added,
            "refused": refused}


# ---------------------------------------------------------------------------
# THE ELEMENTS, one page_key per tab. Every `source_ref` is a real
# `question-center.js` symbol, and every `population` names WHAT is counted —
# because "27 questions" is unreadable without it.
#
# `why_clickable=1` + `why_text` on the numbers: the `ui-standard` rule is that a
# number the user cannot open leaves them unable to answer "why is it 27?", and
# the human's own example of a defect is a hover-only `title` (3 of them were
# found on the pinned page). A click is the only form that survives a touch screen.
# ---------------------------------------------------------------------------
SEED_ELEMENTS: tuple[dict, ...] = (
    # ---- Registries tab ---------------------------------------------------
    dict(element_key="question.tab.registries", page_key="question.registries",
         element_kind="action", rendered_text="Registries",
         user_label="Registries", term_key="registry", unit_key="count",
         population="one tab per view of the Question Center",
         source_ref="llm_task_monitor_ui/src/question-center.js:renderRegistries"),
    dict(element_key="question.num.registry_count", page_key="question.registries",
         element_kind="number", rendered_text="52 registries",
         user_label="Registries", term_key="registry", unit_key="count",
         population=("count of rows in db_table_registry, INCLUDING the ones that "
                     "cannot be asked about"),
         why_clickable=1,
         why_text=("db_table_registry declares 52; 47 of them are speccable and 5 "
                   "are not. The 5 are listed by name with their reason, so the "
                   "number never hides a refusal."),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderRegistries"),
    dict(element_key="question.num.question_total", page_key="question.registries",
         element_kind="number", rendered_text="1371 questions",
         user_label="Questions", term_key="question", unit_key="count",
         population=("sum of the questions the SPECABLE registries demand — NOT a "
                     "count of the 52 declared registries"),
         why_clickable=1,
         why_text=("each specable registry reports its own count; the total is the "
                   "sum of those, so a registry that could not be specced "
                   "contributes 0 and is listed separately."),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderRegistries"),
    dict(element_key="question.label.refused", page_key="question.registries",
         element_kind="label", rendered_text="Could not be asked about",
         user_label="Could not be asked about", term_key="registry",
         unit_key="count",
         population="registries db_table_registry declares but no spec can be built for",
         source_ref="llm_task_monitor_ui/src/question-center.js:renderRegistries"),
    dict(element_key="question.col.area", page_key="question.registries",
         element_kind="label", rendered_text="Area (askable)",
         user_label="Area (askable)", term_key="area", unit_key="count",
         population=("questions that CAN be asked of this subject — the registry's "
                     "question count MINUS the ones that need a file"),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderRegistries"),
    dict(element_key="question.badge.traceable", page_key="question.registries",
         element_kind="badge", rendered_text="traceable",
         user_label="Evidence", term_key="traceable", unit_key="boolean",
         population="whether every applicable question carries a re-runnable ref",
         why_clickable=1,
         why_text=("true when every applicable question's evidence ref is a usable "
                   "citation. A question whose ref is `<no path>` is reported by "
                   "name on the Points tab instead of being counted here."),
         source_ref="llm_task_monitor_ui/src/question-center.js:traceChip"),
    dict(element_key="question.empty.no_frontier",
         page_key="question.frontiers", element_kind="empty_state",
         rendered_text=("No research frontier has been recorded yet. A frontier is "
                        "what a RESEARCH leaves behind: the point it started at, "
                        "the evidence it collected, the area that opened, and the "
                        "NARROW question that would close it."),
         user_label="No research frontier", term_key="frontier", unit_key="count",
         population="rows in research_frontier",
         next_action=("open a registry on the Registries tab and read its questions"),
         # THE `why_text` IS REQUIRED, AND ITS ABSENCE WAS A MEASURED DEFECT.
         # MEASURED 2026-09-28: `ui_standard --audit --page question.frontiers`
         # DROPPED this element, because `unknown_names_source` reads a
         # `rendered_text` that starts with "no " as an UNKNOWN badge and then
         # requires a `why_text` naming the source. This was the ONLY empty state
         # in the register without one — the other five all pass.
         #
         # The rule is right: an empty state that says "nothing here" without
         # saying WHERE the nothing comes from cannot be checked by a reader.
         why_clickable=1,
         why_text=("`research_frontier` has 0 rows. A frontier is written only "
                   "when a RESEARCH step runs, so an empty table means no "
                   "research has been recorded yet — not that the page is "
                   "broken."),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderFrontiers"),

    # ---- Points tab -------------------------------------------------------
    dict(element_key="question.num.area", page_key="question.points",
         element_kind="number", rendered_text="AREA / 27 question",
         user_label="Area", term_key="area", unit_key="count",
         population=("every applicable question this registry demands — the "
                     "questions the narrowing STARTED from"),
         why_clickable=1,
         why_text=("a question that needs a FILE is INAPPLICABLE for a TABLE and is "
                   "counted OUT; the count of excluded questions is shown next to "
                   "this number so the area is never read as the full question list."),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderPoints"),
    dict(element_key="question.num.point", page_key="question.points",
         element_kind="number", rendered_text="POINT / 3 question",
         user_label="Point", term_key="point", unit_key="count",
         population=("questions that did NOT conform to the declared shape — what "
                     "is LEFT TO FIX, not how many were asked"),
         why_clickable=1,
         why_text=("this is the NARROWED set. 0 means nothing is left to fix and is "
                   "shown as a success, not as an empty table. The evidence answer "
                   "comes from the DATABASE, so the page needs no model running."),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderPoints"),
    dict(element_key="question.badge.direction_narrow", page_key="question.points",
         element_kind="badge", rendered_text="\u2192 narrow",
         user_label="Direction", term_key="direction", unit_key="boolean",
         population="which way the flow moved: area -> point",
         why_clickable=1,
         why_text=("narrow SHRINKS: it starts from every applicable question and "
                   "keeps the ones that failed. `research` is its opposite and is "
                   "shown with the other arrow."),
         source_ref="llm_task_monitor_ui/src/question-center.js:dirChip"),
    dict(element_key="question.col.column_tested", page_key="question.points",
         element_kind="label", rendered_text="Column it tests",
         user_label="Column it tests", term_key="question", unit_key="count",
         population=("the column a field question tests, READ from "
                     "PRAGMA table_info at generation time — never hardcoded, so a "
                     "new column yields a new question with no code change"),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderPoints"),
    dict(element_key="question.col.evidence_to_run", page_key="question.points",
         element_kind="label", rendered_text="Evidence to re-run",
         user_label="Evidence to re-run", term_key="traceable", unit_key="count",
         population=("the query, `path:line` or command that produced the answer — "
                     "a reader can re-run it to confirm the point item"),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderPoints"),
    dict(element_key="question.label.not_asked", page_key="question.points",
         element_kind="label", rendered_text="Not asked of this subject",
         user_label="Not asked of this subject", term_key="area", unit_key="count",
         population=("questions that need a FILE and so cannot be asked of a TABLE; "
                     "named with their reason instead of silently dropped"),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderPoints"),

    # ---- Research Frontiers tab -------------------------------------------
    dict(element_key="question.col.closed_by", page_key="question.frontiers",
         element_kind="label", rendered_text="Closed by",
         user_label="Closed by", term_key="narrow", unit_key="count",
         population=("the NARROW flow that would close this research — REQUIRED, "
                     "because a research that cannot name its closing question is "
                     "an exploration with no end"),
         source_ref="llm_task_monitor_ui/src/question-center.js:renderFrontiers"),
    dict(element_key="question.badge.direction_research",
         page_key="question.frontiers", element_kind="badge",
         rendered_text="\u2190 research", user_label="Direction",
         term_key="direction", unit_key="boolean",
         population="which way the flow moved: point -> area",
         why_clickable=1,
         why_text=("research GROWS: it starts at one point and expands into the "
                   "area the collected evidence opens. `narrow` is its opposite."),
         source_ref="llm_task_monitor_ui/src/question-center.js:dirChip"),
)


def register_elements(conn: sqlite3.Connection, *, commit: bool = True) -> dict:
    """Register the page's elements. Idempotent, reports every refusal."""
    import ui_element_registry as uer
    uer.ensure_schema(conn)
    added, refused = 0, []
    for el in SEED_ELEMENTS:
        # `update_existing=True`: this seeder DECLARES the page's elements, so an
        # existing row must be made to MATCH the declaration. MEASURED
        # 2026-09-28: without it, fixing `question.empty.no_frontier`'s missing
        # `why_text` reported `added: 0` and the audit kept dropping the element.
        res = uer.add_element(conn, cite_ref=CITE, commit=False,
                              update_existing=True, **el)
        if res.get("ok"):
            added += 1 if res.get("created") else 0
        else:
            refused.append({"element_key": el.get("element_key"),
                            "code": res.get("code"),
                            "message": str(res.get("message"))[:200]})
    if commit:
        conn.commit()
    return {"ok": not refused, "declared": len(SEED_ELEMENTS), "added": added,
            "refused": refused}


def register_all(db_path: Path | str = DEFAULT_DB) -> dict:
    """Terms FIRST, then elements — the order the element gate enforces."""
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        terms = register_terms(conn)
        elements = register_elements(conn)
    finally:
        conn.close()
    return {"ok": terms["ok"] and elements["ok"], "terms": terms,
            "elements": elements}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)
    out = register_all(args.db)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
