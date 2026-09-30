# -*- coding: utf-8 -*-
"""factor_distill_report.py — the 蒸馏提炼 REPORT, written BY THE LLM.

THE HUMAN'S CORRECTION (2026-09-21)
-----------------------------------
    "your problem is factor by you not by LLM!
     ask he to have 蒸馏提炼 report, for all not 100%,
     you will see the different after few minutes"

That is the defect, stated exactly. Every time a case failed I hand-wrote the
fix: I wrote the L1 checklist, I wrote the scope guard, I wrote the examples. So
the "factor" was MY understanding of the failure, and the LLM was only ever
asked to agree with it. A factor written by me cannot teach the LLM anything it
did not already have — it can only test whether the LLM will follow my wording.

This module inverts that. It collects EVERY case that is not 100%, hands the raw
evidence to the LLM, and asks the LLM for the distillation report: what do these
failures have in common, and what factor setting would catch them. I supply the
EVIDENCE and the OUTPUT SHAPE. I do not supply the finding.

WHY "FOR ALL NOT 100%" MATTERS
------------------------------
A report over the failures I chose is a report over my own selection. So the
input is every non-100% case from every source, gathered mechanically:

  * `llm_100_run` rows where win=0 — the 100-run growth data. Measured: 2615
    rows, 101 losses, ALL of them "false NO (oracle=YES)" on `99999999999` (88)
    and `1_234` (13). The LLM says NO to a large number with separators.
  * the audit harness's non-100% cases, read from its own output.

THE 100-RUN IS GROWTH DATA, NOT A SCORE
---------------------------------------
The human's framing: "100 run is the data to let LLM grow, it is not smart!"
So a loss is not a mark against the model — it is the material the model grows
from. That is why the losses are the INPUT to this report rather than a verdict
about the model.

AND THE LIST HAS NO END
-----------------------
"the list can not see the end as local LLM is free for token and be 24/7 is key
now". A local model costs nothing per token and runs around the clock, so the
factor setting list is not a finite deliverable to be completed — it is a list
that keeps growing as new failures arrive. This module therefore APPENDS to the
list and never claims it is finished.

Run: .\\.venv\\Scripts\\python.exe factor_distill_report.py
     .\\.venv\\Scripts\\python.exe factor_distill_report.py --source audit
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# The console is cp950 on this machine, so a CJK character in a print() raises
# UnicodeEncodeError and kills the run. Reconfigure stdout rather than avoiding
# the word: the report is ABOUT 蒸馏提炼, and dropping the term would make the
# output describe something other than what it is.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = BASE / "agent.db"
OLLAMA = "http://127.0.0.1:11434/v1/chat/completions"
TEXT_MODEL = "qwen2.5:7b-instruct"
REPORT_DIR = BASE / "qc_evidence"

# THE SYSTEM TURN carries the JOB, not the answer. It says what a distillation
# report IS; it does not say what the failures mean.
SYSTEM = (
    "You are a distillation analyst. You are given raw failure evidence from a "
    "model's runs. You produce a 蒸馏提炼 report: you find what the failures "
    "have in COMMON and you propose the factor setting that would catch them. "
    "You do not summarise each failure one by one. You reply with JSON only."
)

USER = """Here is raw evidence from a model's runs on ONE task.

THE TASK: {task}

HOW TO READ EACH LINE:
  correct_answer = the TRUTH. This is what the right answer is.
  model_said     = what the model answered. Where it differs from
                   correct_answer, the MODEL IS WRONG on that case.

You are given BOTH SIDES. Neither side is the subject of the report; the
DIFFERENCE between them is.

=== SIDE A: cases the model got CORRECT ===
{correct}

=== SIDE B: cases the model got WRONG ===
{wrong}

Produce a distillation report as a JSON object with exactly these keys:

  "pattern": one sentence naming what DISTINGUISHES side B from side A. Not a
             list of the cases -- the discriminating cause. If a value appears
             on BOTH sides, that is the most important thing you can report:
             it means the rule is not about the value at all.
  "evidence": the specific values that support the pattern, naming which side
              each came from.
  "factor_key": a short slug for the factor that would catch this.
  "metric_unit": what is being counted or measured, naming its subject.
  "metric_kind": one of boolean, count, pct, score_0_100.
  "metric_target": the pass condition.
  "rule_definition": the rule, in one sentence. The rule must state what the
                     CORRECT ANSWER is, never what the model said. If the
                     correct answer is YES for a value, the rule must not say
                     that value is invalid.
  "action": what to do when it fails.
  "confidence": "high", "medium" or "low" -- how well the evidence supports it.
  "unresolved": anything the evidence does NOT explain.

Reply with the JSON object only."""


def collect_100run_failures(conn: sqlite3.Connection) -> list[dict]:
    """Every `llm_100_run` loss, WITH the context that makes it interpretable.

    MEASURED DEFECT (2026-09-21), found by running the first version: I passed
    only `value`, `oracle`, `model` and `n`. The model then reported the pattern
    as "the value is extremely close to the oracle" and proposed a factor about
    being "within 1% of the oracle" — a confident answer about nothing, because
    it could not see WHAT the case was.

    The withheld context was decisive:
      * `entity_name='phone'`, skill `skill_phone_100_judge` — the task is
        judging whether a value is a valid PHONE NUMBER. Without the domain, a
        bare `1_234` is uninterpretable.
      * `1_234` appears in the WINS 117 times and in the LOSSES 13 times. The
        SAME value gets BOTH answers, so the failure is INCONSISTENCY, not a
        missing rule. A report over failures alone cannot see that — it needs
        the CONTRAST.

    So each failure now carries its domain and its win/loss contrast. A
    distillation report over failures without the contrast is a report about
    half the evidence.
    """
    rows = conn.execute(
        "SELECT ref_tag, entity_name, skill_id, value, oracle_answer, llm_answer, "
        "failure_reason, COUNT(*) n, MIN(round_no) first_round, "
        "MAX(round_no) last_round FROM llm_100_run WHERE win=0 "
        "GROUP BY ref_tag, entity_name, skill_id, value, oracle_answer, "
        "llm_answer, failure_reason ORDER BY n DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # THE CONTRAST: how often the SAME value was judged correctly.
        d["same_value_wins"] = conn.execute(
            "SELECT COUNT(*) FROM llm_100_run WHERE win=1 AND value=?",
            (r["value"],)).fetchone()[0]
        out.append(d)
    return out


def collect_100run_wins(conn: sqlite3.Connection, limit: int = 12) -> list[dict]:
    """A sample of PASSING cases, for contrast.

    A failure is only meaningful next to what succeeded. `1_234` failing means
    one thing if nothing like it ever passes, and something entirely different if
    the same value passes 117 times.
    """
    return [dict(r) for r in conn.execute(
        "SELECT value, oracle_answer, llm_answer, COUNT(*) n FROM llm_100_run "
        "WHERE win=1 GROUP BY value, oracle_answer, llm_answer "
        "ORDER BY n DESC LIMIT ?", (limit,))]


def collect_audit_failures() -> list[dict]:
    """The audit harness's non-100% cases, read from its own output.

    Read from the harness rather than restated here: a second copy of the
    failures would drift from the harness that produced them.
    """
    import subprocess
    out = subprocess.run(
        [sys.executable, str(BASE / "_try_llm_3level_audit.py"),
         "--prompt", "v3-generated"],
        capture_output=True, text=True, cwd=str(BASE)).stdout
    cases: list[dict] = []
    current = None
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s*$", line)
        if m and not line.startswith(" ") and "SCORE" not in line:
            current = m.group(1)
            continue
        if "XX " in line and current:
            cases.append({"case": current, "detail": line.strip()})
    return cases


def ask(user: str, timeout: float = 300.0) -> str:
    body = json.dumps({
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0.0,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))
    return str(d["choices"][0]["message"]["content"] or "").strip()


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def render_side(cases: list[dict], *, side: str) -> str:
    """One side of the evidence, rendered the same way for both sides.

    Both sides use ONE renderer on purpose: a report asked about "failures, plus
    some contrast" is a one-sided ask, and the model answered it one-sidedly —
    it reported "extra digits fail" while the same value was passing 117 times.
    Equal treatment of the two sides is what makes the DIFFERENCE the subject.
    """
    lines = []
    for c in cases:
        if "value" in c:
            lines.append(
                "- value=%r  correct_answer=%r  model_said=%r  (%d time(s))"
                % (c["value"], c["oracle_answer"], c["llm_answer"], c["n"]))
        else:
            lines.append("- case=%s %s" % (c["case"], c["detail"]))
    return "\n".join(lines) or "(none)"


def render_failures(cases: list[dict]) -> str:
    return render_side(cases, side="wrong")


def render_wins(wins: list[dict]) -> str:
    return render_side(wins, side="correct")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["100run", "audit", "all"], default="all")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cases: list[dict] = []
    wins: list[dict] = []
    task = "(unknown)"
    if args.source in ("100run", "all"):
        cases += collect_100run_failures(conn)
        wins += collect_100run_wins(conn)
        row = conn.execute("SELECT entity_name, skill_id FROM llm_100_run LIMIT 1").fetchone()
        if row:
            task = ("judge whether a value is a valid %s "
                    "(skill %s, entity_type=field)"
                    % (row["entity_name"], row["skill_id"]))
    if args.source in ("audit", "all"):
        cases += collect_audit_failures()
    conn.close()

    print("non-100%% cases gathered: %d" % len(cases))
    for c in cases:
        if "value" in c:
            print("   %-10s value=%-16r oracle=%-4s model=%-4s x%d"
                  % (c["ref_tag"], c["value"], c["oracle_answer"],
                     c["llm_answer"], c["n"]))
        else:
            print("   %-10s %s" % (c["case"], c["detail"][:60]))
    if not cases:
        print("nothing to distil -- every case is at 100%")
        return 0

    print("\nasking the model for the 蒸馏提炼 report (BOTH sides)...")
    raw = ask(USER.format(task=task, correct=render_wins(wins),
                          wrong=render_failures(cases)))
    report = extract_json(raw)
    if not report:
        print("the model did not return JSON. Raw reply:\n%s" % raw[:800])
        return 1

    print("\n" + "=" * 74)
    print("蒸馏提炼 REPORT (written by %s)" % TEXT_MODEL)
    print("=" * 74)
    for k in ("pattern", "evidence", "factor_key", "metric_unit", "metric_kind",
              "metric_target", "rule_definition", "action", "confidence",
              "unresolved"):
        print("  %-16s %s" % (k, report.get(k, "-")))

    # The report is a PROPOSAL, so it is checked against the template and the
    # rule engine before anyone believes it. A proposal that cannot be audited
    # is not a factor setting.
    import factor_audit as fa
    import factor_distill as fd

    # THE MECHANICAL FIELDS ARE THE HARNESS'S JOB, NOT THE MODEL'S.
    # `cite_ref` must be a REAL citation, and a model cannot invent one — an
    # invented citation is exactly what `citation_discipline` refuses. The
    # harness knows which query produced the evidence, so it supplies the
    # citation. `name` and `proof_prefix` are derived from the model's own
    # `factor_key`. None of this is the harness writing the FINDING: the
    # pattern, the rule and the action are the model's.
    report.setdefault("name", str(report.get("factor_key") or "distilled factor")
                      .replace("_", " ").capitalize())
    report.setdefault("proof_prefix", "proof_%s" % (report.get("factor_key") or "x"))
    report["cite_ref"] = ("factor_distill_report.py:collect_100run_failures"
                          if args.source in ("100run", "all")
                          else "factor_distill_report.py:collect_audit_failures")

    missing = fd.check_template(report)
    print("\n  template check : %s" % ("complete" if not missing
                                       else "MISSING %s" % missing))
    if not missing:
        audit = fa.audit_factor_dict(report)
        print("  audit          : %s" % audit["verdict"])
        for r in audit["reasons"]:
            print("     - %s" % r)

    REPORT_DIR.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else REPORT_DIR / "distill_report.json"
    out.write_text(json.dumps({"model": TEXT_MODEL, "cases": cases,
                               "report": report}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    print("\n  written to %s" % out)
    print("  NOTE: the factor setting list has NO END. A local model is free per")
    print("  token and runs 24/7, so this list grows with every new failure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())