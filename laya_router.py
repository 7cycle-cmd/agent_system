# -*- coding: utf-8 -*-
"""laya_router.py — SCOPE P: the escalation ladder.

THE HUMAN (2026-09-28), verbatim:

    "laya can play mario game, it can help for DOM value ui by text not visual,
     for playwright is DOM value, it can research and go to that fast!!! is super
     tool for testing"
    "or say, it is very similar to question flow for 7B"
    "how to fast to have correct decideion"

THE LADDER
----------
    a decision arrives (a closed question over TEXT)
        |
        v
    [1] laya answers            ~33 ms, $0, returns a CALIBRATED PROBABILITY
        |
        +-- p >= TAU_HIGH  ->  ACCEPT
        +-- p <= TAU_LOW   ->  UNKNOWN   (NOT a guess)
        +-- in between     ->  ESCALATE  (to the 7B, or to the human)

THE ONE RULE THAT MAKES IT SAFE
-------------------------------
    A probability below the threshold becomes UNKNOWN. It is NEVER rounded up to
    an answer.

WHY THAT IS THE WHOLE POINT: a 7B that is wrong sounds exactly like a 7B that is
right. `laya` returns a calibrated probability, so "I am 51% sure" is a value you
can act on. The repo already enforces this rule for `playwright_step_run.status`
(UNKNOWN 145 of 4,942) and for `evidence_classify` — this module INHERITS it
rather than inventing a second one.

TAU IS READ FROM A REGISTERED FACTOR
------------------------------------
`TAU_LOW` / `TAU_HIGH` are read from `skill_factor_registry` row
`laya_decision_confidence` (factor_id 70). A threshold that lives in a call site
is a magic number; a threshold that is a REGISTERED FACTOR has a MEASURED unit
and can be audited. When the factor is missing the router REFUSES rather than
falling back to a literal — a silent default is how a threshold becomes
unfixable.

THE ENGINE IS OPTIONAL, AND ITS ABSENCE IS A REAL ANSWER
--------------------------------------------------------
MEASURED 2026-09-28: `laya` is NOT installed on this machine. So this module is
written to be provable TODAY, in the state the machine is actually in: with the
engine absent every decision is `ENGINE_UNAVAILABLE`, which is an honest outcome
and not a fabricated latency.

READ-ONLY. This module writes nothing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE / "agent.db"

FACTOR_KEY = "laya_decision_confidence"
TAU_LOW_KEY = "laya_tau_low"
TAU_HIGH_KEY = "laya_tau_high"

# THE OUTCOMES. `UNKNOWN` and `ENGINE_UNAVAILABLE` are DIFFERENT, and collapsing
# them would hide which one happened: UNKNOWN means the engine answered but was
# not sure; ENGINE_UNAVAILABLE means the engine never ran.
ACCEPT = "ACCEPT"
UNKNOWN = "UNKNOWN"
ESCALATE = "ESCALATE"
ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"

# The default thresholds are GONE, and that is a MEASURED fix.
#
# THE DEFECT: the first version derived `tau_high` from the coverage factor's
# `metric_target` ("100", a PERCENT), so `tau_high` became 1.0 and NOTHING could
# ever be ACCEPTed. The coverage target answers "what share of decisions should
# be confident?" — a DIFFERENT question from "how confident must one decision
# be?". Using one as the other is the `measurement-scope` defect: a number about
# the wrong population.
#
# So each threshold is READ from its OWN registered factor, and when either is
# missing the router REFUSES. A silent default is how a threshold becomes
# unfixable.


def engine_available() -> bool:
    """Is the `laya` package importable? MEASURED, never assumed."""
    try:
        return importlib.util.find_spec("laya") is not None
    except Exception:
        return False


# THE ROUTER IS CACHED, AND THIS IS A MEASURED FIX.
#
# THE DEFECT: `ask_laya` built a NEW `Router()` on every call. MEASURED
# 2026-09-28 on this machine:
#
#     Router() #1 ctor ms:      0.0     <- construction is LAZY and free
#     predict on #1 (first) ms: 6146.0  <- the FIRST predict pays the load
#     predict on #1 (again) ms: 36.4    <- a warm predict is 36 ms
#     Router() #2 ctor ms:      0.0
#     predict on #2 (fresh) ms: 475.5   <- a FRESH router re-pays it
#
# So every call re-paid the model load: 523 ms/call instead of 36 ms/call, a
# 13x slowdown I introduced. The cost is NOT the state text (MEASURED: a
# 20-char state still cost 515 ms) and NOT the cold start (MEASURED: run 2 of
# the same process was still 536 ms/decision). It is the per-call construction.
#
# The cache is keyed by MODEL, because the model is the thing being loaded.
_ROUTERS: dict[str, Any] = {}


def _router(model: str) -> Any:
    """The cached `Router` for `model`, built once. Raises if laya is absent."""
    r = _ROUTERS.get(model)
    if r is None:
        from laya import Router            # type: ignore
        r = Router()
        _ROUTERS[model] = r
    return r


def read_tau(conn: sqlite3.Connection) -> dict[str, Any]:
    """The thresholds, READ from their registered factors. Never a literal.

    Returns `ok: False` when EITHER factor is absent, so the caller can REFUSE
    rather than silently default. A router that defaults its own threshold is a
    router nobody can tune.

    THE TWO THRESHOLDS ARE SEPARATE FACTORS, and that is a MEASURED fix: the
    first version derived `tau_high` from the COVERAGE factor's target ("100", a
    percent), so `tau_high` became 1.0 and nothing could ever be ACCEPTed. A
    coverage target and a threshold answer different questions.
    """
    got: dict[str, Any] = {}
    for key in (TAU_LOW_KEY, TAU_HIGH_KEY):
        row = conn.execute(
            "SELECT factor_id, metric_kind, metric_unit, metric_target FROM "
            "skill_factor_registry WHERE factor_key=? AND is_active=1",
            (key,)).fetchone()
        if not row:
            return {"ok": False, "code": "FACTOR_NOT_REGISTERED",
                    "missing": key,
                    "message": ("the factor %r is not registered, so that "
                                "threshold has no home — a threshold in a call "
                                "site is a magic number" % key)}
        try:
            val = float(str(row["metric_target"]).strip()) / 100.0
        except (TypeError, ValueError):
            return {"ok": False, "code": "BAD_THRESHOLD", "missing": key,
                    "message": ("the factor %r declares metric_target %r, which "
                                "is not a number, so the threshold cannot be "
                                "read" % (key, row["metric_target"]))}
        got[key] = {"factor_id": int(row["factor_id"]), "value": val,
                    "metric_kind": row["metric_kind"],
                    "metric_unit": row["metric_unit"],
                    "metric_target": row["metric_target"]}
    low, high = got[TAU_LOW_KEY], got[TAU_HIGH_KEY]
    if low["value"] >= high["value"]:
        return {"ok": False, "code": "THRESHOLDS_INVERTED",
                "message": ("tau_low=%.4f is not below tau_high=%.4f, so no "
                            "probability could ever ESCALATE — the two "
                            "thresholds are inverted"
                            % (low["value"], high["value"]))}
    return {"ok": True, "tau_low": low["value"], "tau_high": high["value"],
            "tau_low_factor_id": low["factor_id"],
            "tau_high_factor_id": high["factor_id"],
            "metric_unit": high["metric_unit"],
            "source": ("register:skill_factor_registry:%d + %d"
                       % (low["factor_id"], high["factor_id"]))}


def decide(probability: float | None, tau: dict[str, Any]) -> dict[str, Any]:
    """The ladder, for ONE probability. Pure — no engine, no DB.

    `probability is None` means the engine did not answer, which is
    ENGINE_UNAVAILABLE and NOT a low-confidence answer. Collapsing the two would
    make "the model is missing" indistinguishable from "the model is unsure".
    """
    if probability is None:
        return {"outcome": ENGINE_UNAVAILABLE, "probability": None,
                "why": ("the engine did not answer, so there is no probability "
                        "to compare — this is NOT a low-confidence answer")}
    p = float(probability)
    if p >= float(tau["tau_high"]):
        return {"outcome": ACCEPT, "probability": p,
                "why": "p=%.4f >= tau_high=%.4f" % (p, tau["tau_high"])}
    if p <= float(tau["tau_low"]):
        return {"outcome": UNKNOWN, "probability": p,
                "why": ("p=%.4f <= tau_low=%.4f, so the answer is UNKNOWN — it "
                        "is NEVER rounded up to an answer"
                        % (p, tau["tau_low"]))}
    return {"outcome": ESCALATE, "probability": p,
            "why": ("tau_low=%.4f < p=%.4f < tau_high=%.4f, so the decision is "
                    "escalated rather than guessed"
                    % (tau["tau_low"], p, tau["tau_high"]))}


def ask_laya(state: str, questions: dict[str, Any],
             model: str = "multilingual") -> dict[str, Any]:
    """Ask the engine. Returns `probability: None` when it is not installed.

    THE API IS THE HUMAN'S ANALOGY, MEASURED from the model card:

        from laya import Router
        router = Router()
        result = router.predict(state, questions)
        result["answers"][name]["choice"|"noul"]

    WHICH CONFIDENCE FIELD, AND THIS IS A MEASURED FIX
    --------------------------------------------------
    MEASURED 2026-09-28, from the library's OWN docstring for
    `laya.answer_confidence`:

        "Probability mass on the answer being reported: max(p). This is the
         quantity temperature scaling fits, and the quantity every calibration
         figure in this repository is computed on ... It is therefore the one
         confidence with the property the README's gating section relies on: of
         the answers returned at confidence c, about c of them are right.
         `confidence_from_probs` below reports a DIFFERENT quantity on a
         DIFFERENT scale and carries NO such guarantee, so the two must not be
         compared against the same threshold."

    So the calibrated field is **`answer_confidence`** (= `max(probabilities)`),
    and `confidence` is a DIFFERENT quantity on a DIFFERENT scale.

    THE DEFECT THIS FIXES: the first version collected
    `("noul", "probability", "confidence", "score")` and took the MINIMUM, so
    it read `confidence` — the UNCALIBRATED one. MEASURED on a real answer:

        probabilities     {'YES': 0.4303, 'NO': 0.5697}
        confidence        0.0140   <- what the old code read
        answer_confidence 0.5697   <- the calibrated one

    The ladder would then have called a 0.5697 answer UNKNOWN, because 0.0140 is
    below `tau_low`. **A router that reads the wrong field is worse than no
    router: it is confidently wrong.**

    AND THE MINIMUM IS ALSO WRONG. Taking the lowest probability across the
    questions was a guess; the library reports ONE calibrated confidence per
    answer, so the router reports the LOWEST of those, which is the honest
    "one unsure answer makes the whole decision unsure" rule.
    """
    if not engine_available():
        return {"ok": False, "code": ENGINE_UNAVAILABLE, "probability": None,
                "why": ("the `laya` package is not importable, so no decision "
                        "was made — MEASURED, not assumed")}
    try:
        router = _router(model)
        t0 = time.perf_counter()
        result = router.predict(state, questions, model=model)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as exc:
        return {"ok": False, "code": "ENGINE_ERROR", "probability": None,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    answers = (result or {}).get("answers") or {}
    probs, per_question = [], {}
    for name, block in answers.items():
        if not isinstance(block, dict):
            continue
        # `answer_confidence` FIRST — it is the calibrated one. The others are
        # kept only as a fallback for a checkpoint that does not report it, and
        # the fallback is NAMED in the result so a reader can tell which was used.
        val, src = None, None
        for k in ("answer_confidence", "noul", "probability"):
            v = block.get(k)
            if isinstance(v, (int, float)):
                val, src = float(v), k
                break
        if val is None:
            probs_map = block.get("probabilities")
            if isinstance(probs_map, dict) and probs_map:
                val, src = max(float(x) for x in probs_map.values()), "max(probabilities)"
        if val is not None:
            probs.append(val)
            per_question[name] = {"probability": val, "source": src,
                                  "choice": block.get("choice")}
    return {"ok": True, "probability": (min(probs) if probs else None),
            "elapsed_ms": elapsed_ms, "per_question": per_question,
            "raw": result,
            "why": ("the engine answered; the ladder uses the LOWEST CALIBRATED "
                    "probability across the questions, because one unsure answer "
                    "is enough to make the whole decision unsure")}


def decide_yes_no(conn: sqlite3.Connection, question: str, state: str,
                  model: str = "multilingual") -> dict[str, Any]:
    """The WHOLE LADDER for a binary question, in ONE call.

    🔴 THIS IS THE FUNCTION THAT MAKES `laya_router` WIRED (added 2026-09-29).

    MEASURED before it existed: `laya_router` had **ZERO automatic callers**. The
    only thing in the repo that asked for the `python` transport (the transport
    laya is registered under) was a PROOF — so a model registered at priority 5,
    with a green proof and three live factor rows, could never be reached by any
    production path. That is the same defect as the deleted generated-DDL module:
    PREPARED, not WIRED.

    WHY IT BUILDS THE QUESTION HERE INSTEAD OF TAKING ONE
    ----------------------------------------------------
    The laya question shape is
    `{"type": "choice", "instructions": ..., "criteria": {...}}`, which is
    MEASURED to be the same shape `question_template_registry` stores. Building it
    from a plain `question` string means a caller that speaks only "YES/NO?" can
    use the engine, and it keeps this module the ONE place that knows the shape.

    THE LADDER IS NOT BYPASSED
    --------------------------
    It returns the SAME `outcome` vocabulary `decide()` uses (ACCEPT / UNKNOWN /
    ESCALATE / ENGINE_UNAVAILABLE), so a caller cannot confuse:
      * the engine ANSWERED and is sure      -> ACCEPT
      * the engine ANSWERED and is unsure    -> UNKNOWN  (never rounded up)
      * the engine answered, in between      -> ESCALATE (someone else decides)
      * the engine did NOT answer            -> ENGINE_UNAVAILABLE
    Collapsing the last two into "UNKNOWN" is the defect `decide()` documents.
    """
    tau = read_tau(conn)
    a = ask_laya(state, {"q": {
        "type": "choice",
        "instructions": question,
        "criteria": {"YES": "the state satisfies the question",
                     "NO": "the state does not satisfy the question"},
    }}, model=model)
    if not a.get("ok"):
        return {"outcome": ENGINE_UNAVAILABLE, "probability": None,
                "answer": None, "tau": tau, "cite": "laya_router.ask_laya:not-ok",
                "reason": str(a.get("why") or a.get("code") or "")[:200]}
    d = decide(a.get("probability"), tau)
    choice = ((a.get("per_question") or {}).get("q") or {}).get("choice")
    return {
        "outcome": d["outcome"],
        "probability": d.get("probability"),
        "answer": choice,
        "elapsed_ms": a.get("elapsed_ms"),
        "tau": tau,
        # The cite names the ROWS the thresholds were read from, not the module,
        # because a threshold is a registered factor and a reader must be able to
        # check the number.
        "cite": "laya_router.py:decide_yes_no <- %s" % tau.get("source", "?"),
        "reason": d.get("why"),
        "per_question": a.get("per_question"),
    }


def compare_with_ui_standard(conn: sqlite3.Connection, page_key: str,
                             tau: dict[str, Any]) -> dict[str, Any]:
    """Run the router BESIDE `ui_standard` and REPORT where the two disagree.

    THE LAW (independent-review): a disagreement triggers a MEASUREMENT, never a
    vote. This function changes NO verdict. It adds a second reader and reports
    where the two readers differ, because where they differ one of them is wrong
    and nobody knows which.

    MEASURED 2026-09-28: `laya` is not installed, so every decision here is
    ENGINE_UNAVAILABLE and the disagreement count is 0 — which is the HONEST
    result of running in the state the machine is actually in, not a fabricated
    agreement.

    THE EMPTY-REGISTER DEFECT, MEASURED AND FIXED 2026-09-28
    -------------------------------------------------------
    The first version passed `terms=set()` to `check_element`. MEASURED:
    `terminology_registry` has **2412 rows / 2411 distinct term_keys**, and
    `check_element(el, terms=set())` reports `label_registered` FAIL for EVERY
    element, because `tk in set()` is always False. So the regex reader was
    answering FAIL by CONSTRUCTION, and 7 of the 8 disagreements were an
    artefact of that — not a disagreement between two readers at all.

    `terms=None` is a DIFFERENT thing: it means "the register was NOT
    consulted", and the result SAYS SO (`weaker: True`). A comparison against a
    weaker guarantee is not a comparison, so this function now REFUSES it.
    """
    import ui_element_registry as uer
    import ui_standard as us

    rows = uer.list_elements(conn, page_key)
    # `us.audit_page(page_key)` was called here and its result NEVER USED. It is
    # removed, and that is a MEASURED fix: `audit_page` divides by `applicable`
    # without guarding zero, so on the 5 pages where NO rule applies it raised
    # `ZeroDivisionError` and killed the whole comparison. A dead call that can
    # only crash is not a call.
    #
    # THE DEFECT IS STILL IN `ui_standard.py:302` AND IS REPORTED, NOT PATCHED:
    # that file is NOT in this plan's allowlist. The guard exists one line BELOW
    # the division (`"pct": (... if applicable else None)`), so the fix is to
    # move it up.
    #
    # THE REAL REGISTER, read once. A comparison needs the STRONG guarantee.
    terms = {str(r[0]) for r in conn.execute(
        "SELECT term_key FROM terminology_registry")}
    decisions = []
    for el in rows:
        for rule in us.RULES:
            res = us.check_element(el, terms=terms)
            detail = (res.get("rules") or {}).get(rule) or {}
            # A WEAKER GUARANTEE IS NOT A COMPARISON. `weaker` is set when the
            # register was not consulted; comparing laya against that would
            # measure the missing register, not the two readers.
            if detail.get("weaker"):
                continue
            d = str(detail.get("detail", ""))
            if d.startswith("not applicable") or d.startswith("not a ") \
                    or d.startswith("not an "):
                continue
            regex_verdict = "PASS" if detail.get("pass") else "FAIL"
            # THE SECOND READER. The state is the element's OWN text, so the
            # question is asked about the same thing the regex judged.
            state = " | ".join(str(el.get(k) or "") for k in
                               ("element_key", "element_kind", "rendered_text",
                                "user_label", "why_text", "next_action"))
            got = ask_laya(state, {rule: {"type": "noul",
                                          "instructions": "Does this element "
                                                          "satisfy the rule %s?"
                                                          % rule}})
            ladder = decide(got.get("probability"), tau)
            # A DISAGREEMENT REQUIRES TWO OPINIONS, AND AN ABSTENTION IS NOT ONE.
            #
            # MEASURED 2026-09-28, and this is the SAME defect one level deeper.
            # The first version excluded only ENGINE_UNAVAILABLE. But the ladder
            # has TWO ways of not answering: ENGINE_UNAVAILABLE (the engine never
            # ran) and ESCALATE (the engine ran and was NOT SURE). MEASURED over
            # all 141 decisions: **25 of the 26 "disagreements" were
            # `regex=PASS, laya=ESCALATE/UNKNOWN`** — the regex said PASS and
            # laya ABSTAINED. That is not two readers disagreeing; it is ONE
            # reader speaking and the other declining.
            #
            # So `agrees` is None for BOTH abstention kinds, and the two are
            # counted SEPARATELY (`abstentions` vs `disagreements`) because they
            # mean different things: an abstention says the ladder needs a human,
            # a disagreement says one of the two readers is WRONG.
            if ladder["outcome"] in (ENGINE_UNAVAILABLE, ESCALATE):
                agrees = None
            else:
                agrees = ((ladder["outcome"] == ACCEPT
                           and regex_verdict == "PASS")
                          or (ladder["outcome"] == UNKNOWN
                              and regex_verdict == "FAIL"))
            decisions.append({
                "element_key": el.get("element_key"), "rule": rule,
                "regex_verdict": regex_verdict,
                "router_outcome": ladder["outcome"],
                "router_probability": ladder["probability"],
                "agrees": agrees,
                "why": ladder["why"],
            })
    by_outcome: dict[str, int] = {}
    for d in decisions:
        by_outcome[d["router_outcome"]] = by_outcome.get(d["router_outcome"], 0) + 1
    compared = [d for d in decisions if d["agrees"] is not None]
    abstained = [d for d in decisions if d["agrees"] is None]
    return {
        "ok": True, "page_key": page_key, "elements": len(rows),
        "decisions": len(decisions), "by_outcome": by_outcome,
        "compared": len(compared),
        "abstentions": len(abstained),
        "disagreements": sum(1 for d in compared if not d["agrees"]),
        "engine_available": engine_available(),
        "tau": tau,
        "rows": decisions,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true",
                    help="report the ladder's configuration, read-only")
    ap.add_argument("--compare", action="store_true",
                    help="run the router beside ui_standard and report disagreements")
    ap.add_argument("--page", default="consultant.step_walk")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        tau = read_tau(conn)
        if args.compare:
            if not tau.get("ok"):
                print(json.dumps({"ok": False, "tau": tau}, indent=2,
                                 ensure_ascii=False))
                return 1
            out = compare_with_ui_standard(conn, args.page, tau)
            if args.json:
                print(json.dumps(out, indent=2, ensure_ascii=False))
            else:
                print("=" * 78)
                print("LAYER ROUTER vs ui_standard — %s" % args.page)
                print("=" * 78)
                print("  elements        %d" % out["elements"])
                print("  decisions       %d" % out["decisions"])
                print("  by outcome      %s" % out["by_outcome"])
                print("  DISAGREEMENTS   %d" % out["disagreements"])
                print("  engine available %s" % out["engine_available"])
                print("  tau             low=%.4f high=%.4f (from %s)"
                      % (tau["tau_low"], tau["tau_high"], tau["source"]))
            return 0
        # --measure (the default)
        out = {"ok": bool(tau.get("ok")), "tau": tau,
               "engine_available": engine_available(),
               "ladder": [decide(p, tau) for p in (0.10, 0.70, 0.95, None)]
               if tau.get("ok") else []}
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["ok"] else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
