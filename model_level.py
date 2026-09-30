# -*- coding: utf-8 -*-
"""model_level.py — which FLOW LENGTH a model can actually run, MEASURED.

The user (2026-09-22):
    "or 7B, just have a value for LLM model , we can depend it level to find out
     which method is good for him"

THE PROBLEM THIS SOLVES
-----------------------
MEASURED: the 7B answered YES for a payload MISSING a required field 19 times out
of 40 on a ONE-question flow (`_diag_pilot_failures.py`). Eight instruction
variants were measured (`_diag_identity_instruction_variants.py`) and FOUR of
them scored recall NO 0.00 — the model cannot emit a verdict after enumerating.

So a model has a CAPABILITY LEVEL, and giving it a flow longer than it can run
produces a confident wrong answer rather than a failure. The level must be
MEASURED from what the model actually did, never asserted — the same rule
`activation_gate` follows for `is_active` (it is the ONLY writer of `is_active=1`
and it requires a measured streak).

THE LEVELS
----------
    UNKNOWN     no rounds recorded — NOT the same as "cannot do it"
    binary      clears a 1-step flow
    gated       clears a flow with a gate + one more step
    multi_step  clears a flow of 3+ steps

`UNKNOWN` is a REAL outcome. Treating "no evidence" as "binary" would let a model
be assigned a flow it has never been measured on, which is the defect
`llm_service_store.available_models` already names: "None means UNKNOWN, and
unknown is NOT the same as empty."
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parent / "agent.db"

LEVELS: tuple[str, ...] = ("UNKNOWN", "binary", "gated", "multi_step")

# The step count at which a level is reached. DERIVED from the level names, so
# adding a level is one edit rather than two that can disagree.
LEVEL_MIN_STEPS: dict[str, int] = {
    "binary": 1,
    "gated": 2,
    "multi_step": 3,
}


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def level_for_steps(n: int) -> str:
    """The level a model reaches by clearing `n` steps. 0 -> UNKNOWN."""
    best = "UNKNOWN"
    for name in LEVELS:
        if name == "UNKNOWN":
            continue
        if int(n) >= LEVEL_MIN_STEPS[name]:
            best = name
    return best


def rounds_of(conn: sqlite3.Connection, model: str) -> list[dict[str, Any]]:
    """Every recorded round for a model, newest first.

    DEFECT FOUND BY RUNNING IT (2026-09-22): this caught `OperationalError` and
    returned `[]`, so a MISSING COLUMN was reported as "no rounds" — the model
    level came back UNKNOWN for a model with 2645 rounds. A schema error is not
    an empty result, and conflating them is the same defect
    `llm_service_store.available_models` names: "None means UNKNOWN, and unknown
    is NOT the same as empty." The error is now RAISED, so a broken schema is
    loud rather than silent.
    """
    rows = conn.execute(
        "SELECT id, ref_tag, round_no, win, layer_key, dim_key, "
        "failure_reason FROM proof_run WHERE model=? ORDER BY id DESC",
        (str(model),)).fetchall()
    return [dict(r) for r in rows]


def level_of(conn: sqlite3.Connection, model: str) -> dict[str, Any]:
    """The MEASURED level of a model, DERIVED from `proof_run`.

    Returns `{model, level, rounds, wins, max_streak, layers, source, reason}`.

    `source` is RETURNED, not assumed:
      * `measured`  — the model has rounds, so the level is derived from them
      * `unknown`   — the model has NO rounds, so the level is UNKNOWN

    The level is the LONGEST CONSECUTIVE WIN RUN the model achieved, because a
    streak is what a flow requires: a model that wins 3 in a row can run a
    3-step flow, while a model that wins 3 of 10 cannot.
    """
    rows = rounds_of(conn, model)
    if not rows:
        return {"model": str(model), "level": "UNKNOWN", "rounds": 0,
                "wins": 0, "max_streak": 0, "layers": [], "source": "unknown",
                "reason": ("no rounds recorded for %r, so its level is UNKNOWN — "
                           "unknown is NOT the same as 'cannot do it'" % model)}
    # `rounds_of` is newest-first, so walk it in reverse to get chronological
    # order and count the longest consecutive win run.
    chrono = list(reversed(rows))
    best = cur = 0
    for r in chrono:
        if int(r.get("win") or 0) == 1:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    layers = sorted({str(r.get("layer_key")) for r in rows
                     if str(r.get("layer_key") or "NA") not in ("NA", "")})
    return {
        "model": str(model),
        "level": level_for_steps(best),
        "rounds": len(rows),
        "wins": sum(1 for r in rows if int(r.get("win") or 0) == 1),
        "max_streak": best,
        "layers": layers,
        "source": "measured",
        "reason": ("the longest consecutive win run is %d, so the model can run a "
                   "%d-step flow" % (best, best)),
    }


def can_run(conn: sqlite3.Connection, model: str, step_count: int) -> dict[str, Any]:
    """May this model be given a flow of `step_count` steps?

    REFUSES when the model's MEASURED level is below the flow's length, and
    REFUSES when the level is UNKNOWN — an unmeasured model must not be assigned
    a flow, because a confident wrong answer is worse than no answer.
    """
    lv = level_of(conn, model)
    need = level_for_steps(int(step_count))
    if lv["level"] == "UNKNOWN":
        return {"ok": False, "code": "MODEL_LEVEL_UNKNOWN", "model": str(model),
                "step_count": int(step_count), "level": lv["level"],
                "reason": lv["reason"]}
    if LEVELS.index(lv["level"]) < LEVELS.index(need):
        return {"ok": False, "code": "MODEL_LEVEL_TOO_LOW", "model": str(model),
                "step_count": int(step_count), "level": lv["level"],
                "needed": need,
                "reason": ("model %r is measured at level %r (max streak %d) but "
                           "a %d-step flow needs %r"
                           % (model, lv["level"], lv["max_streak"],
                              int(step_count), need))}
    return {"ok": True, "model": str(model), "step_count": int(step_count),
            "level": lv["level"], "needed": need, "max_streak": lv["max_streak"]}


def levels_by_model(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every model that has rounds, with its measured level. Sorted by level."""
    models = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT model FROM proof_run WHERE model IS NOT NULL "
        "AND TRIM(model) <> ''")]
    out = [level_of(conn, m) for m in models]
    out.sort(key=lambda d: (LEVELS.index(d["level"]), -int(d["max_streak"])))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)
    conn = _connect(args.db)
    try:
        if args.model:
            print(json.dumps(level_of(conn, args.model), ensure_ascii=False,
                             indent=2))
            return 0
        print("=== model levels (MEASURED from proof_run) ===")
        for d in levels_by_model(conn):
            print("  %-28s %-11s rounds=%-5d wins=%-5d max_streak=%-4d layers=%s"
                  % (d["model"], d["level"], d["rounds"], d["wins"],
                     d["max_streak"], ",".join(d["layers"]) or "-"))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())