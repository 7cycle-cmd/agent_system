# -*- coding: utf-8 -*-
"""verify_definition_7b.py — the SECOND DOOR: a local 7B checks a definition.

THE HUMAN (2026-09-27), verbatim:

    "proof by LLM 7B, to have 2nd verfity report! is it easy for him"

THE MEASURED ANSWER: **YES, but ONLY with a checklist contract.**

    prompt v1 — an OPEN question ("Does the definition describe the facts? YES or NO"):
        accuracy **1 / 3**. One case was a rubber stamp; the other "failure" was the
        model CORRECTLY refusing a definition of mine that claimed something the
        facts did not show.

    prompt v2 — a CHECKLIST contract ("every claim must match a FACT; a general
        claim has no fact, so it FAILS"):
        accuracy **5 / 5**.

So the prompt is the whole difference, and this module ships v2 and NOT v1. An open
question produces a rubber stamp, and a rubber stamp is not a second door.

HONEST LIMIT, kept next to the claim so it cannot be forgotten: 5/5 over 5 cases
with a 4:2 PASS:FAIL split is a SMALL sample. `prompt-measurement-discipline` rule 4
requires REPEATED 100% for certification, so this module does NOT certify the model.
`measure_split()` exists so the claim can be re-measured at scale instead of trusted.

LATENCY IS NOT A FACTOR: warm mean 60 ms measured (the earlier 6475 ms figure was
COLD — 6332 ms of it was model load).

WHAT A FAIL IS NOT: a `FAIL` is a finding to act on. It does NOT delete a term, does
NOT downgrade one, and does NOT authorise an edit.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"
TEXT_MODEL = "qwen2.5:7b-instruct"

# THE CONTRACT, verbatim and readable. Every clause closes a measured failure of
# the open-ended form:
#   * "STRICTLY"                 -- the open form answered YES to obvious prose
#   * "only if every claim ...    -- makes PASS require POSITIVE support, not
#     is present in the FACTS"       merely "not contradicted"
#   * the general-claim clause    -- names the exact prose shape that fooled v1:
#                                    "stores information about X" has no fact
#   * "Reply exactly PASS|FAIL"   -- an unbounded answer cannot be parsed reliably
CHECKLIST_CONTRACT = (
    "You check a definition STRICTLY against FACTS. A definition PASSES only if "
    "every claim it makes is present in the FACTS block. A general claim such as "
    "'stores information about X' has NO corresponding fact, so it FAILS. "
    "Reply exactly 'PASS' or 'FAIL', then one short reason."
)

VERDICT_RE = re.compile(r"\b(PASS|FAIL)\b", re.I)


def _facts_block(facts: dict) -> str:
    """The FACTS as text. ONLY what `facts_for` read — no adjective, no purpose.

    IT MUST CARRY EVERY FIELD `describe()` CAN USE. MEASURED DEFECT IN MY OWN FIRST
    VERSION, found BY the second door: `describe()` named the primary key, the
    required columns and the unique indexes, but this block printed only the columns
    and the row count. The 7B answered

        "FAIL — The definition claims a primary key, which is not present in the
         facts."

    and IT WAS RIGHT. The definition made a claim the brief did not contain, so the
    check failed for a reason that was COMPLETELY my own: a verifier given an
    incomplete brief will refuse a correct definition, and the defect looks like the
    verifier's fault. The two functions must be TOTAL over the same facts.
    """
    cols = facts.get("columns") or []
    pk = sorted(c["name"] for c in cols if c.get("pk"))
    required = sorted(c["name"] for c in cols if c.get("notnull") and not c.get("pk"))
    typed = ", ".join("%s %s" % (c["name"], (c.get("type") or "?").upper())
                      for c in cols)
    fks = "; ".join("%s -> %s.%s" % (f["from"], f["to_table"], f["to_col"])
                    for f in (facts.get("foreign_keys") or [])) or "none"
    uniq = sorted(i["name"] for i in (facts.get("indexes") or []) if i.get("unique"))

    # ---- THE WORD CASE. MEASURED, and it is the defect this closes. ----------
    #
    # THE HUMAN (2026-09-27): "evidence + logic generator can't help?"
    #
    # MEASURED by `register_vocabulary.py --review`: for 6 of 6 vocabulary words the
    # door's brief carried NONE of the names the word is used in, because this
    # function was TOTAL over a TABLE's fields and had no case for a WORD. The brief
    # read `columns (none)`, so a draft that described the word's USAGE was checked
    # against nothing and FAILED. The door was RIGHT and the BRIEF was the defect —
    # the same lesson the docstring above already records for the primary key.
    #
    # A WORD'S ONLY FACTS ARE ITS USES, so they are printed here. This is what makes
    # the brief TOTAL over BOTH kinds `definition_from_evidence` can produce.
    if str(facts.get("kind") or "") == "word":
        used = facts.get("used_in") or []
        senses = facts.get("senses") or []
        pos = facts.get("position") or {}
        # POSITION IS PRINTED because it is the fact that carries a word's ROLE in a
        # compositional language. MEASURED: without it the door FAILED a role claim
        # (usage alone does not say whether a word is a prefix or a suffix count).
        return ("FACTS (the only admissible evidence):\n"
                "  word %r EXISTS, kind = word\n"
                "  registered as = %s\n"
                "  used in %d registered name(s), namely = %s\n"
                "  occurrences by position = PREFIX=%d SUFFIX=%d MIDDLE=%d ALONE=%d\n"
                "  DOMINANT position = %s\n"
                "  equivalent spelling(s) = %s\n"
                "  definitions this word carries = %s"
                % (facts.get("name"),
                   ", ".join(facts.get("registered_as") or []) or "(not registered as a word)",
                   len(used), ", ".join(used[:24]) or "(used in no name)",
                   pos.get("prefix", 0), pos.get("suffix", 0),
                   pos.get("middle", 0), pos.get("alone", 0),
                   facts.get("dominant_position") or "unused",
                   ", ".join(facts.get("equivalent_spellings") or []) or "(none)",
                   " | ".join("%s: %s" % (s["where"], s["definition"][:80])
                              for s in senses) or "(none declared)"))

    return ("FACTS (the only admissible evidence):\n"
            "  object %r EXISTS, kind = %s\n"
            "  column count = %d\n"
            "  columns (name type) = %s\n"
            "  primary key columns = %s\n"
            "  required (NOT NULL, non-PK) columns = %s\n"
            "  row count = %s\n"
            "  foreign keys = %s\n"
            "  unique index names = %s"
            % (facts.get("name"), facts.get("kind"), len(cols), typed or "(none)",
               ", ".join(pk) or "(none)", ", ".join(required) or "(none)",
               facts.get("row_count"), fks, ", ".join(uniq) or "(none)"))


def ask(system: str, user: str, *, timeout: float = 120.0,
        url: str = OLLAMA_URL, model: str = TEXT_MODEL) -> str:
    """One completion. Raises on transport failure — the CALLER decides UNKNOWN."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.0, "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return str(json.loads(r.read().decode("utf-8", errors="replace"))
                   ["choices"][0]["message"]["content"] or "").strip()


def parse_verdict(text: str) -> str | None:
    """`'PASS' | 'FAIL' | None`. None means the answer did not say either — an
    unparseable answer is UNKNOWN, NEVER a pass."""
    m = VERDICT_RE.search(str(text or ""))
    return m.group(1).upper() if m else None


def verify_definition(definition: str, facts: dict, *, timeout: float = 120.0,
                      url: str = OLLAMA_URL) -> dict:
    """The SECOND DOOR on ONE definition.

    Returns `{ok, verdict, reason, ms, model}`.
      * `verdict` is `'PASS'`, `'FAIL'`, or `None`.
      * `None` (UNKNOWN) is returned when the verifier could not be reached, when
        the answer did not parse, or when there are no facts to check against.
        **An unreachable verifier is UNKNOWN, never a pass** — the same rule the
        evidence gates use for a missing detector.
    """
    if not facts or not facts.get("ok"):
        return {"ok": False, "verdict": None, "ms": 0.0, "model": TEXT_MODEL,
                "reason": "no facts to check the definition against"}
    user = ("%s\n\nDEFINITION under test:\n  %s\n\n"
            "Every claim in the definition must match a FACT above. "
            "Reply 'PASS' or 'FAIL'." % (_facts_block(facts), str(definition or "")))
    t0 = time.time()
    try:
        text = ask(CHECKLIST_CONTRACT, user, timeout=timeout, url=url)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"ok": False, "verdict": None, "ms": (time.time() - t0) * 1000.0,
                "model": TEXT_MODEL,
                "reason": "verifier UNREACHABLE: %s: %s — UNKNOWN, never a pass"
                          % (type(e).__name__, e)}
    ms = (time.time() - t0) * 1000.0
    v = parse_verdict(text)
    if v is None:
        return {"ok": False, "verdict": None, "ms": ms, "model": TEXT_MODEL,
                "reason": "the answer did not say PASS or FAIL: %r" % text[:80]}
    return {"ok": True, "verdict": v, "ms": ms, "model": TEXT_MODEL,
            "reason": text.splitlines()[0][:160] if text else ""}


def measure_split(cases: list[dict], *, url: str = OLLAMA_URL) -> dict:
    """Run a labelled set and report the SPLIT, not a bare accuracy.

    Each case is `{definition, facts, expect}` where `expect` is True (should PASS),
    False (should FAIL) or None (expected to be refused by the contract).

    `prompt-measurement-discipline` rules 2 and 6: report BALANCED accuracy and the
    PER-CLASS recall, and report a RANGE by naming the sample size. A single number
    over an unbalanced set measures the set's MIX, not the prompt.
    """
    tp = tn = fp = fn = unknown = 0
    rows = []
    for c in cases:
        r = verify_definition(c["definition"], c["facts"], url=url)
        exp = c.get("expect")
        got = r["verdict"]
        if got is None:
            unknown += 1
        elif exp is True:
            tp += 1 if got == "PASS" else 0
            fn += 1 if got == "FAIL" else 0
        elif exp is False:
            tn += 1 if got == "FAIL" else 0
            fp += 1 if got == "PASS" else 0
        rows.append({"expect": exp, "verdict": got, "ms": round(r["ms"], 1),
                     "reason": r["reason"][:70]})
    n_pos, n_neg = tp + fn, tn + fp
    out = {
        "n": len(cases), "positive_cases": n_pos, "negative_cases": n_neg,
        "unknown": unknown, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "recall_pass": (tp / n_pos) if n_pos else None,
        "recall_fail": (tn / n_neg) if n_neg else None,
        "balanced_accuracy": (((tp / n_pos) if n_pos else 0)
                              + ((tn / n_neg) if n_neg else 0)) / 2
                             if (n_pos and n_neg) else None,
        "rows": rows,
    }
    return out


def main(argv=None) -> int:
    """A ONE-SHOT check of a definition against a named object's facts."""
    import argparse
    import sqlite3

    import definition_from_evidence as dfe
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name", help="the object the definition is about")
    ap.add_argument("--definition", default="",
                    help="the definition to check; empty = check the GENERATED one")
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        f = dfe.facts_for(conn, a.name)
        if not f.get("ok"):
            print("NO FACTS:", f.get("why"))
            return 1
        d = a.definition or dfe.describe(f)
        r = verify_definition(d, f)
        print("OBJECT : %s (%s)" % (f["name"], f["kind"]))
        print("DEF    : %s" % d[:200])
        print("7B     : verdict=%s  %.0f ms" % (r["verdict"], r["ms"]))
        print("REASON : %s" % r["reason"])
        return 0 if r["verdict"] == "PASS" else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
