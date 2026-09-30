"""run_factory_line_once.py — run the WHOLE line ONCE, on a REAL subject.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "why 100-run is next step? trigger point is different"
    "and the task flow is 1 -> 2-> 3..."
    "so it is factory line, why have these problem"

The user's diagnosis was CORRECT and mine was wrong. 100-run is
`activation_gate.assert_may_activate` — a SHIP-GATE ("may this entity go live?"),
which needs `streak >= target`. It is NOT a step of the line. Running the line
once and running the ship-gate 100 times are different questions:

    THE LINE   "does station 3 receive what station 2 produced?"   ONE run
    THE GATE   "is this entity good enough to activate?"           streak runs

MEASURED before this: the pieces all EXISTED and did not call each other.
`logic_generator.generate()` produced questions that were thrown away;
`prompt_generator.compose_layer_prompts()` produced prompts nothing attached;
`workflow_step` was filled by hand. So the line had NEVER been run, and every
defect found so far was a CONNECTION defect (a deadlock, a FAULT, a truncated
allowlist, a false docstring, an identical cite_ref), never a "station 3 did its
job badly" defect.

That is what this script measures: **the line, once**, end to end.

    spec -> logic_generator -> question_flow.steps_from_questions
         -> prompt_generator (the scoped prompt)
         -> run_flow with an EVIDENCE ask
         -> a COMPUTED verdict
         -> a report

It writes NOTHING durable: the flow it creates is deleted at the end, so a
re-run is a TRUE re-run rather than a second reading of the first run's state.
It is a MEASUREMENT, not a fixture — if a joint is missing, it says WHICH joint.

Run:  .\\.venv\\Scripts\\python.exe run_factory_line_once.py
      .\\.venv\\Scripts\\python.exe run_factory_line_once.py --subject table:code_registry
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The flow key this run writes. Deleted at the end, so the live DB keeps no
# residue and a re-run is clean.
FLOW_KEY = "factory_line_once"

# The station results, so the summary can say WHICH joint failed rather than
# only that the run did not finish.
STATIONS: list[dict[str, Any]] = []


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(BASE / "agent.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _cleanup(conn: sqlite3.Connection) -> None:
    """Remove this run's flow. A measurement must not leave state behind."""
    try:
        row = conn.execute("SELECT workflow_id FROM workflow_registry "
                           "WHERE workflow_key=?", (FLOW_KEY,)).fetchone()
        if row:
            conn.execute("DELETE FROM workflow_step WHERE workflow_id=?",
                         (int(row[0]),))
            conn.execute("DELETE FROM workflow_registry WHERE workflow_id=?",
                         (int(row[0]),))
            conn.commit()
    except Exception:
        pass


def step(n: int, title: str) -> None:
    print()
    print("=" * 74)
    print("STATION %d  %s" % (n, title))
    print("=" * 74)


def ok(label: str, cond: bool, detail: str = "") -> bool:
    STATIONS.append({"label": label, "ok": bool(cond)})
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", label,
                          ("  [%s]" % detail) if detail else ""))
    return bool(cond)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run the factory line once")
    ap.add_argument("--subject", default="table:code_registry",
                    help="<kind>:<name>, e.g. table:code_registry")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    kind, _, name = str(args.subject).partition(":")
    if not kind or not name:
        print("--subject must be <kind>:<name>, got %r" % args.subject)
        return 2

    conn = _connect()
    report: dict[str, Any] = {"subject": args.subject, "kind": kind}
    try:
        _cleanup(conn)

        # ---------------------------------------------------------------- 1
        step(1, "SPEC — where the line starts")
        import logic_generator as lg
        # A spec comes from a REAL subject, not a literal. `spec_from_table`
        # reads PRAGMA + the table's own design factors.
        if kind == "table":
            try:
                spec = lg.spec_from_table(conn, name)
            except Exception as exc:
                print("  FAIL  spec_from_table(%r): %s: %s"
                      % (name, type(exc).__name__, exc))
                return 1
        else:
            spec = lg.spec_from_skill(conn, name)
        spec["subject_kind"] = kind
        print("  subject      : %s" % spec.get("subject"))
        print("  kind         : %s" % spec.get("kind"))
        print("  field_count  : %s" % spec.get("field_count"))
        ok("the spec exists and names a subject", bool(spec.get("subject")),
           str(spec.get("subject")))

        # ---------------------------------------------------------------- 2
        step(2, "logic_generator — the question list, WORDED from the registers")
        res = lg.generate(spec, conn=conn, subject_kind=kind)
        print("  count        : %d questions / %d cases"
              % (res["count"], res["case_count"]))
        print("  formula      : %s" % res["formula"])
        print("  wording      : %s" % res["wording_by_source"])
        report["questions"] = res["count"]
        report["wording_by_source"] = res["wording_by_source"]
        ok("questions were generated", res["count"] > 0, str(res["count"]))
        ok("every question reports its wording source",
           all(q.get("wording_source") for q in res["questions"]),
           str([q["question_id"] for q in res["questions"]
                if not q.get("wording_source")]))
        ok("the wording came from the REGISTER, not a constant",
           res["wording_by_source"].get("register", 0) > 0,
           str(res["wording_by_source"]))

        # ---------------------------------------------------------------- 3
        step(3, "question_flow — the questions become STEPS (some are GATES)")
        import question_flow as qf
        # WHICH STEPS GATE. MEASURED design (2026-09-24): a step is a GATE only
        # if its answer has an INDEPENDENT expectation — a value the question is
        # checked AGAINST. `logic_generator.gate_questions_for()` DERIVES the set
        # from `GATE_ABLE_FAMILIES`, so the list cannot go stale when the field
        # count changes (a hand-typed `field_7_name` would).
        #
        # EVERY step is still ASKED; the gates JUDGE. `registered_inactive` and
        # the `code`-source families are REPORTS: a table may legitimately be
        # active, a name may be unregistered by design — so a NO there is
        # information, not a stop. Gating on them would fail a CORRECT table.
        gate_qs = lg.gate_questions_for(res["questions"])
        write = qf.steps_from_questions(conn, FLOW_KEY, res["questions"],
                                        spec=spec, skill_key=None,
                                        gate_questions=gate_qs)
        steps = qf.steps_of(conn, FLOW_KEY)
        print("  steps written: %d (+1 verdict)" % write["step_count"])
        print("  gates        : %d of %d steps judge"
              % (len(gate_qs), write["step_count"]))
        for s in steps:
            print("    %d. %-12s %-9s %s"
                  % (int(s["step_no"]), str(s["layer_key"]),
                     str(s["step_kind"]),
                     str(s["question_template"])[:48]))
        report["steps"] = write["step_count"]
        report["gates"] = len(gate_qs)
        ok("every question became a step",
           write["step_count"] == res["count"],
           "%d vs %d" % (write["step_count"], res["count"]))
        ok("a COMPUTED verdict step was appended",
           str(steps[-1]["step_kind"]) == "verdict",
           str(steps[-1]["step_kind"]))
        ok("at least one step GATES (so the verdict can be a judgement)",
           len(gate_qs) > 0, str(len(gate_qs)))
        ok("REPORT-only families are NOT gates",
           all(lg.evidence_family(q) not in lg.REPORT_ONLY_FAMILIES
               for q in gate_qs),
           str([q for q in gate_qs
                if lg.evidence_family(q) in lg.REPORT_ONLY_FAMILIES]))
        v = qf.validate_flow(conn, FLOW_KEY)
        ok("the written flow satisfies validate_flow", v["ok"] is True,
           str(v["problems"]))

        # ---------------------------------------------------------------- 4
        step(4, "run_flow — the line RUNS, with an EVIDENCE ask")
        # THE ASK IS THE EVIDENCE ORACLE, not a model and not a stub. This is the
        # joint the user's "answer come from evidence not feeling" requires: the
        # answer is a DB read, and it carries the query that produced it.
        #
        # THE QUESTION IS FOUND BY ITS DIMENSION AND POSITION, not by matching a
        # substring of the prompt. MEASURED, and my first version was WRONG: it
        # searched for the question text INSIDE the composed prompt, but a
        # register-backed wording REWRITES the text, so the match failed and
        # every answer became a silent "NO" — a line that "ran" while measuring
        # nothing. The step and the question share `step_no` <-> list position by
        # construction (`steps_from_questions` writes them in that order), so the
        # position IS the link.
        ordered = res["questions"]
        asked: list[dict[str, Any]] = []

        def ask(prompt_text: str, st: dict[str, Any]) -> str:
            idx = int(st.get("step_no") or 0) - 1
            if idx < 0 or idx >= len(ordered):
                asked.append({"step_no": st.get("step_no"), "qid": None,
                              "answer": "NO", "why": "no question at that step"})
                return "NO"
            qid = str(ordered[idx]["question_id"])
            r = lg.answer_by_evidence(conn, spec, {"question_id": qid})
            ans = str(r.get("answer")) if r.get("ok") else "NO"
            asked.append({"step_no": st.get("step_no"), "qid": qid,
                          "answer": ans, "evidence_ref": r.get("evidence_ref"),
                          "source": r.get("source"), "ok": bool(r.get("ok"))})
            return ans

        out = qf.run_flow(conn, FLOW_KEY, ask)
        print("  asked        : %d of %d steps"
              % (out["asked_count"], out["step_count"]))
        print("  stopped_at   : %s" % out["stopped_at"])
        print("  failed_steps : %s" % out["failed_steps"])
        print("  VERDICT      : %s" % out["verdict"])
        report["verdict"] = out["verdict"]
        report["asked"] = out["asked_count"]
        report["stopped_at"] = out["stopped_at"]
        ok("the line ran to the end (nothing stopped it)",
           out["stopped_at"] is None, str(out["stopped_at"]))
        ok("every asked step got a REAL evidence answer (not a silent NO)",
           all(a.get("ok") for a in asked if a.get("qid")),
           str([a for a in asked if a.get("qid") and not a.get("ok")]))
        ok("the verdict step was NOT asked (it is computed)",
           not any(str(d.get("step_no")) == str(out["step_count"])
                   and not d.get("computed") for d in out["steps"]),
           str(out["steps"][-1]))
        ok("a verdict string was produced", bool(out["verdict"]),
           str(out["verdict"]))
        ok("every asked answer carried its EVIDENCE",
           all(a.get("evidence_ref") for a in asked if a.get("qid")),
           str([a for a in asked if a.get("qid") and not a.get("evidence_ref")]))

        # ---------------------------------------------------------------- 5
        step(5, "THE REPORT — what the line produced, with its evidence")
        for a in asked:
            print("    step %-3s %-18s %-4s %s"
                  % (a.get("step_no"), a.get("qid"), a.get("answer"),
                     str(a.get("evidence_ref"))[:50]))
        report["answers"] = asked
        ok("every answer in the report names WHERE it came from",
           all(a.get("evidence_ref") or not a.get("qid") for a in asked))


        # ---------------------------------------------------------------- 6
        step(6, "NEGATIVE CONTROL ? the SAME line on a WRONG expectation")
        # A VERDICT THAT CANNOT FAIL CERTIFIES NOTHING. Station 4 produced
        # `VERDICT=YES`; that is only meaningful if the SAME machinery answers
        # `NO` when the expectation is broken. So this station MUTATES one
        # declared type and re-runs the flow: the verdict must go `NO` and the
        # failed step must be NAMED. This is the `independent-review` rule
        # applied to the line itself: a uniform result needs a POSITIVE CONTROL.
        bad = dict(spec)
        bad["field_types"] = list(spec.get("field_types") or [])
        if bad["field_types"]:
            # Declare column 1 as TEXT while the live table says INTEGER.
            bad["field_types"][0] = "TEXT"
        qf.steps_from_questions(conn, FLOW_KEY, res["questions"],
                                spec=bad, skill_key=None,
                                gate_questions=gate_qs)
        asked_b: list[dict[str, Any]] = []

        def ask_b(prompt_text: str, st: dict[str, Any]) -> str:
            idx = int(st.get("step_no") or 0) - 1
            if idx < 0 or idx >= len(ordered):
                return "NO"
            qid = str(ordered[idx]["question_id"])
            r = lg.answer_by_evidence(conn, bad, {"question_id": qid})
            asked_b.append({"step_no": st.get("step_no"), "qid": qid,
                            "answer": str(r.get("answer"))})
            return str(r.get("answer")) if r.get("ok") else "NO"

        out_b = qf.run_flow(conn, FLOW_KEY, ask_b)
        print("  VERDICT (mutated): %s" % out_b["verdict"])
        print("  failed_steps     : %s" % out_b["failed_steps"])
        report["negative_verdict"] = out_b["verdict"]
        report["negative_failed_steps"] = out_b["failed_steps"]
        ok("the POSITIVE control passed (a correct table -> YES)",
           out["verdict"] == "YES", str(out["verdict"]))
        ok("the NEGATIVE control FAILS (a wrong declaration -> NO)",
           out_b["verdict"] == "NO", str(out_b["verdict"]))
        ok("...and it NAMES the step that failed",
           bool(out_b["failed_steps"]), str(out_b["failed_steps"]))
        ok("...and the failed step is the one whose DECLARATION we mutated",
           "field_1_type" in str([a.get("qid") for a in asked_b
                                 if a.get("answer") == "NO"]),
           str([a.get("qid") for a in asked_b if a.get("answer") == "NO"]))

        if args.json:
            print()
            print(json.dumps(report, indent=2, ensure_ascii=False))
    finally:
        _cleanup(conn)
        conn.close()

    passed = sum(1 for s in STATIONS if s["ok"])
    failed = [s["label"] for s in STATIONS if not s["ok"]]
    print()
    print("=" * 74)
    print("FACTORY LINE RAN ONCE  subject=%s:%s" % (kind, name))
    print("  stations checked : %d  passed: %d  failed: %d"
          % (len(STATIONS), passed, len(failed)))
    if failed:
        print("  THE LINE IS NOT COMPLETE. Missing joint(s):")
        for f in failed:
            print("    - %s" % f)
    else:
        print("  THE LINE IS COMPLETE: spec -> questions -> steps -> run -> "
              "verdict, every answer carrying its evidence.")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
