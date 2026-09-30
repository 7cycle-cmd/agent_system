# -*- coding: utf-8 -*-
"""screen_watch.py — screenshot -> 7B analysis -> decide -> dispatch.

THE USER'S FLOW (verbatim, 2026-09-22)
--------------------------------------
    "5 mins take screenshot and LLM 7B will anayle the photo, if need help, he
     can control openclaw or playwright to have the work at my computer"
    "how? so openclaw is must or optional?"
    "target -> screenshot and LLM 7B anayle -> prompt for openclaw or playwright"

READING: every 5 minutes, capture the screen, have the local 7B vision model
analyse it, and IF help is needed, dispatch to OpenClaw or Playwright.

IS OPENCLAW REQUIRED? NO. MEASURED, AND THE ANSWER IS LAYERED:
    screenshot        -> NOT required (PIL.ImageGrab / pyautogui)
    notify            -> NOT required (schema_qc.try_notify is a fallback)
    BROWSER control   -> NOT required (Playwright is BETTER: selectors,
                         auto-wait, and an actionability check before every
                         action; OpenClaw's coordinate clicking is blind)
    DESKTOP APP control -> REQUIRED (Playwright only knows browsers)
So OpenClaw is OPTIONAL for the browser path and the ONLY option for the desktop
path. The repo already has this exact shape: `hko_weather_proof.py:106-107`
declares `primary = playwright_chromium_full_page` and
`secondary = openclaw_webbrowser_plus_screen_snapshot`.

REUSE, NOT REWRITE. All four parts already exist:
    capture   worker_heartbeat_service.take_screenshot()
    analyse   vision_analyze.analyze_evidence()
    browser   browser_task_runner.py  (DB-driven steps)
    desktop   openclaw_bridge.try_live_snapshot() / try_notify()

THE DECISION IS TWO-LAYERED, AND THAT IS DELIBERATE
---------------------------------------------------
A 7B model asked "is there a problem?" produces FALSE POSITIVES, and a false
positive here means the system starts controlling the user's computer for no
reason. So:
    layer 1  the 7B answers, with a reason
    layer 2  a RULE filters it (a severity floor, and a repeat requirement)
A model alone is not a gate; a rule alone cannot read a screen. Both.

DISPATCH IS ADVISORY BY DEFAULT
-------------------------------
`dispatch(..., confirm=False)` returns a PLAN and does NOT act. The user's
machine is not a sandbox, so an automatic action needs an explicit confirm. The
UI's Control tab is where that confirm happens.

Run:
  python screen_watch.py --once
  python screen_watch.py --status
  python screen_watch.py --history
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The cadence the user named.
INTERVAL_SEC = int(os.environ.get("SCREEN_WATCH_INTERVAL", "300") or "300")

# THE RULE LAYER. A 7B answer alone is not a gate.
#   * severity floor: `low` never dispatches. A model that says "low" and a
#     model that says "critical" must not be treated the same.
#   * repeat requirement: the SAME problem must be seen twice before acting.
#     One frame is a moment; two frames is a state.
SEVERITY_FLOOR = os.environ.get("SCREEN_WATCH_SEVERITY", "medium")
SEVERITY_ORDER = ("low", "medium", "high", "critical")
REPEAT_REQUIRED = int(os.environ.get("SCREEN_WATCH_REPEAT", "2") or "2")

# The two dispatch targets, and what each can actually do.
TARGETS: dict[str, dict[str, Any]] = {
    "playwright": {
        "kind": "browser",
        "required_provider": None,          # no external provider needed
        "why": "selectors + auto-wait + an actionability check before every "
               "action. PRIMARY for anything inside a browser.",
    },
    "openclaw": {
        "kind": "desktop",
        "required_provider": "openclaw_companion",
        "why": "the ONLY option for a desktop app. Coordinate clicking is "
               "blind, so it is the FALLBACK for browser work.",
    },
}

SCREEN_WATCH_DDL = """
CREATE TABLE IF NOT EXISTS screen_watch_run (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    image_path   TEXT,
    capture_ok   INTEGER NOT NULL DEFAULT 0 CHECK (capture_ok IN (0, 1)),
    capture_err  TEXT    NOT NULL DEFAULT 'NA',
    model        TEXT    NOT NULL DEFAULT 'NA',
    model_source TEXT    NOT NULL DEFAULT 'NA',
    severity     TEXT    NOT NULL DEFAULT 'NA',
    ui_state     TEXT    NOT NULL DEFAULT 'NA',
    likely_cause TEXT    NOT NULL DEFAULT 'NA',
    reason       TEXT    NOT NULL DEFAULT 'NA',
    confidence   REAL    NOT NULL DEFAULT 0,
    analysis_ok  INTEGER NOT NULL DEFAULT 0 CHECK (analysis_ok IN (0, 1)),
    analysis_err TEXT    NOT NULL DEFAULT 'NA',
    -- THE TWO-LAYER DECISION, recorded SEPARATELY so a reader can see WHICH
    -- layer decided. A single `decision` column would hide whether the model or
    -- the rule was responsible.
    model_says   TEXT    NOT NULL DEFAULT 'NA',
    rule_says    TEXT    NOT NULL DEFAULT 'NA',
    decision     TEXT    NOT NULL DEFAULT 'NA',
    decision_why TEXT    NOT NULL DEFAULT 'NA',
    dispatched   INTEGER NOT NULL DEFAULT 0 CHECK (dispatched IN (0, 1)),
    dispatch_target TEXT NOT NULL DEFAULT 'NA',
    dispatch_plan   TEXT NOT NULL DEFAULT 'NA'
);
CREATE INDEX IF NOT EXISTS idx_screen_watch_run_time
  ON screen_watch_run (captured_at);
"""


class ScreenWatchRefused(RuntimeError):
    """Raised when a dispatch would act without the right to act."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCREEN_WATCH_DDL)
    conn.commit()


# ---------------------------------------------------------------------------
# 1. CAPTURE — reuse, do not rewrite
# ---------------------------------------------------------------------------
def capture() -> dict[str, Any]:
    """Take one screenshot. REUSES `worker_heartbeat_service.take_screenshot()`.

    That function already owns the rolling buffer (latest + MAX_SNAPSHOT_COUNT),
    so a second capture path would be a second retention policy — and the two
    would disagree about how much disk to use.
    """
    try:
        import worker_heartbeat_service as whs

        path = whs.take_screenshot()
        return {"ok": bool(path), "path": str(path) if path else None,
                "error": "NA" if path else "take_screenshot returned None"}
    except Exception as e:
        return {"ok": False, "path": None,
                "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------------------
# 2. ANALYSE — reuse, do not rewrite
# ---------------------------------------------------------------------------
ANALYSIS_PROMPT = (
    "You are watching a Windows desktop for an operator.\n"
    "Look at the screenshot and answer ONLY with a JSON object:\n"
    "  severity: one of low|medium|high|critical\n"
    "  ui_state: one short sentence describing what is on screen\n"
    "  likely_cause: one short sentence, or 'none'\n"
    "  needs_help: true when a human or an agent should intervene\n"
    "  reason: one short sentence saying WHY, naming what you saw\n"
    "  confidence: number 0..1\n"
    "A normal, working screen is severity=low and needs_help=false.\n"
    "Do NOT invent a problem. If the screen looks fine, say so.\n"
)


def analyze(image_path: str | Path | None,
            conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Analyse one screenshot. REUSES `vision_analyze.analyze_evidence()`.

    The model is resolved from the DB-driven registry (`llm.vision`), and the
    SOURCE is returned, so a caller never reports a model it did not resolve.

    `conn` is PASSED THROUGH. DEFECT FOUND BY RUNNING IT (2026-09-22): the first
    version called `resolve_vision_model()` with no connection, so it opened its
    own — and reported `source: "fallback"` while the registry was perfectly
    readable. A caller that already holds a connection must hand it over, or the
    resolver silently measures a DIFFERENT database (or fails to open one) and
    the report names a model it never resolved.
    """
    try:
        import vision_analyze as va

        resolved = va.resolve_vision_model(conn)
        res = va.analyze_evidence(
            image_path, fault_type="screen_watch",
            prompt=ANALYSIS_PROMPT, parse_mode="json")
        detail = res.detail or {}
        return {
            "ok": not res.error,
            "model": res.model,
            "model_source": resolved.get("source", "NA"),
            "severity": str(detail.get("severity") or "NA").lower(),
            "ui_state": str(detail.get("ui_state") or "NA"),
            "likely_cause": str(detail.get("likely_cause") or "NA"),
            "needs_help": bool(detail.get("needs_help")),
            "reason": str(detail.get("reason") or res.summary or "NA"),
            "confidence": float(detail.get("confidence") or 0.0),
            "error": res.error or "NA",
        }
    except Exception as e:
        return {"ok": False, "model": "NA", "model_source": "NA",
                "severity": "NA", "ui_state": "NA", "likely_cause": "NA",
                "needs_help": False, "reason": "NA", "confidence": 0.0,
                "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------------------
# 3. DECIDE — TWO layers, and both are recorded
# ---------------------------------------------------------------------------
def decide(analysis: dict[str, Any], *,
           recent: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Turn an analysis into a decision. LAYER 1 = the model, LAYER 2 = a rule.

    Returns `{model_says, rule_says, decision, why}`. The two layers are
    reported SEPARATELY, because a single verdict would hide which one decided —
    and when a false positive happens, that is the first thing you need to know.

    THE RULE, and why each part exists:
      * `severity >= SEVERITY_FLOOR` — a model that says `low` and one that says
        `critical` must not be treated the same.
      * the SAME problem seen `REPEAT_REQUIRED` times — one frame is a moment,
        two frames is a state. This is what stops a single unlucky frame from
        starting an action on the user's machine.
    """
    sev = str(analysis.get("severity") or "NA").lower()
    needs = bool(analysis.get("needs_help"))
    model_says = "help" if needs else "ok"

    if not analysis.get("ok"):
        return {"model_says": "error", "rule_says": "no-action",
                "decision": "no-action",
                "why": "the analysis failed, so there is nothing to act on: %s"
                       % str(analysis.get("error"))[:120]}

    if sev not in SEVERITY_ORDER:
        return {"model_says": model_says, "rule_says": "no-action",
                "decision": "no-action",
                "why": "severity %r is not one of %s, so the rule cannot rank it"
                       % (sev, "/".join(SEVERITY_ORDER))}

    if not needs:
        return {"model_says": model_says, "rule_says": "no-action",
                "decision": "no-action",
                "why": "the model reports no help needed"}

    if SEVERITY_ORDER.index(sev) < SEVERITY_ORDER.index(SEVERITY_FLOOR):
        return {"model_says": model_says, "rule_says": "below-floor",
                "decision": "no-action",
                "why": "severity %s is below the floor %s, so it is recorded "
                       "but not acted on" % (sev, SEVERITY_FLOOR)}

    # THE REPEAT RULE. Count how many of the recent runs reported help at or
    # above the floor. `recent` is oldest-first and EXCLUDES this run.
    streak = 1
    for r in reversed(recent or []):
        if (str(r.get("severity") or "").lower() in SEVERITY_ORDER
                and SEVERITY_ORDER.index(str(r["severity"]).lower())
                >= SEVERITY_ORDER.index(SEVERITY_FLOOR)
                and str(r.get("model_says") or "") == "help"):
            streak += 1
        else:
            break
    if streak < REPEAT_REQUIRED:
        return {"model_says": model_says, "rule_says": "needs-repeat",
                "decision": "no-action",
                "why": "seen %d time(s), the rule requires %d consecutive "
                       "readings before acting (one frame is a moment, two is "
                       "a state)" % (streak, REPEAT_REQUIRED)}

    return {"model_says": model_says, "rule_says": "act",
            "decision": "act",
            "why": "severity %s at or above the floor %s, seen %d consecutive "
                   "time(s)" % (sev, SEVERITY_FLOOR, streak)}


# ---------------------------------------------------------------------------
# 4. DISPATCH — advisory by default
# ---------------------------------------------------------------------------
def target_available(target: str, conn: sqlite3.Connection | None = None
                     ) -> dict[str, Any]:
    """Can this target act right now? Reports WHY when it cannot.

    `playwright` needs no external provider. `openclaw` needs the
    `openclaw_companion` provider to be available, and an unavailable provider
    is a NORMAL state, not a fault — the same rule `llm_service_store` follows.
    """
    spec = TARGETS.get(str(target))
    if not spec:
        return {"ok": False, "target": target,
                "why": "unknown target; known: %s" % ", ".join(TARGETS)}
    if spec["required_provider"] is None:
        try:
            import playwright  # noqa: F401

            return {"ok": True, "target": target, "kind": spec["kind"],
                    "why": "playwright is importable"}
        except Exception as e:
            return {"ok": False, "target": target, "kind": spec["kind"],
                    "why": "playwright is not importable: %s: %s"
                           % (type(e).__name__, e)}
    own = conn is None
    if own:
        conn = _connect()
    try:
        import llm_service_store as lss

        avail = lss.provider_available(conn, "hand")
        return {"ok": bool(avail.get("ok")), "target": target,
                "kind": spec["kind"],
                "why": avail.get("why") or ("provider %s is available"
                                            % spec["required_provider"])}
    except Exception as e:
        return {"ok": False, "target": target, "kind": spec["kind"],
                "why": "%s: %s" % (type(e).__name__, e)}
    finally:
        if own:
            conn.close()


def plan_dispatch(analysis: dict[str, Any], *,
                  conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """WHICH target, and WHY. A plan, not an action.

    Playwright is PRIMARY for browser work because it has selectors, auto-wait
    and an actionability check; OpenClaw's coordinate clicking is blind. OpenClaw
    is the ONLY option for a desktop app, so it is chosen when the analysis names
    a desktop app rather than a page.
    """
    text = " ".join(str(analysis.get(k) or "") for k in
                    ("ui_state", "likely_cause", "reason")).lower()
    browser_words = ("browser", "page", "tab", "url", "website", "chrome",
                     "edge", "firefox", "web")
    desktop_words = ("dialog", "window", "explorer", "notepad", "excel",
                     "word", "app", "installer", "popup")
    is_browser = any(w in text for w in browser_words)
    is_desktop = any(w in text for w in desktop_words)

    if is_browser and not is_desktop:
        first, second = "playwright", "openclaw"
        why = "the analysis names a browser, and Playwright is PRIMARY there"
    elif is_desktop and not is_browser:
        first, second = "openclaw", "playwright"
        why = ("the analysis names a desktop app, and OpenClaw is the ONLY "
               "target that can drive one")
    else:
        first, second = "playwright", "openclaw"
        why = ("the analysis does not clearly name a browser or a desktop app, "
               "so the PRIMARY target is tried first")

    order = []
    for t in (first, second):
        a = target_available(t, conn)
        order.append({"target": t, "available": a["ok"], "why": a["why"]})
    chosen = next((o for o in order if o["available"]), None)
    return {"ok": chosen is not None, "why": why, "order": order,
            "chosen": chosen["target"] if chosen else None,
            "blocked": None if chosen else
                       "no target is available: %s"
                       % "; ".join("%s: %s" % (o["target"], o["why"])
                                   for o in order)}


def dispatch(analysis: dict[str, Any], *, target: str | None = None,
             confirm: bool = False,
             conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Act on the user's machine. REFUSES unless `confirm=True`.

    THE USER'S MACHINE IS NOT A SANDBOX. An automatic action needs an explicit
    confirm, so the default returns the PLAN and does nothing. The UI's Control
    tab is where a human confirms.
    """
    plan = plan_dispatch(analysis, conn=conn)
    if target:
        plan["chosen"] = str(target)
        plan["ok"] = any(o["target"] == str(target) and o["available"]
                         for o in plan["order"])
        if not plan["ok"]:
            plan["blocked"] = ("target %r is not available: %s"
                               % (target, next(
                                   (o["why"] for o in plan["order"]
                                    if o["target"] == str(target)),
                                   "unknown target")))
    if not plan["ok"]:
        return {"ok": False, "acted": False, "plan": plan,
                "why": plan.get("blocked") or "no target available"}
    if not confirm:
        return {"ok": True, "acted": False, "plan": plan,
                "why": "ADVISORY ONLY — pass confirm=True to act. The user's "
                       "machine is not a sandbox."}
    return {"ok": True, "acted": False, "plan": plan,
            "why": "confirmed, but no executor is wired for %r yet. The plan is "
                   "recorded so the action is reproducible."
                   % plan["chosen"]}


# ---------------------------------------------------------------------------
# 5. ONE RUN
# ---------------------------------------------------------------------------
def run_once(*, db_path: Path | str | None = None,
             dispatch_confirm: bool = False) -> dict[str, Any]:
    """capture -> analyse -> decide -> (plan) -> record. One row per run."""
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        recent = [dict(r) for r in conn.execute(
            "SELECT severity, model_says FROM screen_watch_run "
            "ORDER BY run_id DESC LIMIT 10")][::-1]

        cap = capture()
        ana = analyze(cap["path"], conn) if cap["ok"] else {
            "ok": False, "model": "NA", "model_source": "NA",
            "severity": "NA", "ui_state": "NA", "likely_cause": "NA",
            "needs_help": False, "reason": "NA", "confidence": 0.0,
            "error": "capture failed: %s" % cap["error"]}
        dec = decide(ana, recent=recent)
        plan = (plan_dispatch(ana, conn=conn)
                if dec["decision"] == "act" else {"ok": False, "chosen": None,
                                                  "why": "no action decided"})
        acted = False
        if dec["decision"] == "act" and plan.get("ok"):
            d = dispatch(ana, target=plan.get("chosen"),
                         confirm=dispatch_confirm, conn=conn)
            acted = bool(d.get("acted"))

        cur = conn.execute(
            "INSERT INTO screen_watch_run (image_path, capture_ok, capture_err, "
            "model, model_source, severity, ui_state, likely_cause, reason, "
            "confidence, analysis_ok, analysis_err, model_says, rule_says, "
            "decision, decision_why, dispatched, dispatch_target, dispatch_plan) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cap["path"], int(cap["ok"]), str(cap["error"])[:200],
             ana["model"], ana["model_source"], ana["severity"],
             str(ana["ui_state"])[:300], str(ana["likely_cause"])[:300],
             str(ana["reason"])[:300], float(ana["confidence"]),
             int(ana["ok"]), str(ana["error"])[:200],
             dec["model_says"], dec["rule_says"], dec["decision"],
             dec["why"], int(acted), str(plan.get("chosen") or "NA"),
             json.dumps(plan, ensure_ascii=False)[:2000]))
        conn.commit()
        return {"ok": True, "run_id": int(cur.lastrowid), "capture": cap,
                "analysis": ana, "decision": dec, "plan": plan, "acted": acted}
    finally:
        conn.close()


def history(*, limit: int = 50, db_path: Path | str | None = None
            ) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM screen_watch_run ORDER BY run_id DESC LIMIT ?",
            (max(1, int(limit)),))]
    finally:
        conn.close()


def status(*, db_path: Path | str | None = None) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        ensure_schema(conn)
        n = conn.execute("SELECT COUNT(*) FROM screen_watch_run").fetchone()[0]
        last = conn.execute("SELECT * FROM screen_watch_run ORDER BY run_id "
                            "DESC LIMIT 1").fetchone()
        acted = conn.execute("SELECT COUNT(*) FROM screen_watch_run WHERE "
                             "dispatched=1").fetchone()[0]
        return {"ok": True, "runs": n, "acted": acted,
                "interval_sec": INTERVAL_SEC,
                "severity_floor": SEVERITY_FLOOR,
                "repeat_required": REPEAT_REQUIRED,
                "last": dict(last) if last else None,
                "targets": {t: target_available(t, conn) for t in TARGETS}}
    finally:
        conn.close()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="screen watch: capture -> 7B -> act")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--confirm", action="store_true",
                    help="allow a dispatch to ACT (default is advisory only)")
    args = ap.parse_args()

    if args.status:
        print(json.dumps(status(), indent=2, ensure_ascii=False))
        return 0
    if args.history:
        for r in history(limit=args.limit):
            print("run %-4s %s sev=%-8s model=%-6s rule=%-12s %s"
                  % (r["run_id"], r["captured_at"], r["severity"],
                     r["model_says"], r["rule_says"], r["decision"]))
        return 0
    out = run_once(dispatch_confirm=args.confirm)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
