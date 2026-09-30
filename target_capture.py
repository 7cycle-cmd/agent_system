# -*- coding: utf-8 -*-
"""6-STEP target capture middleware — the gate the capture flow calls.

WHY THIS MODULE EXISTS
----------------------
The user's spec (2026-09-20) is a 6-step capture, and the important half is the
last two steps: they are PROOF gates, not confirmations. Writing a target row and
then explaining why it was wrong is the failure mode this module removes — the
gates run BEFORE the write, so an unproven target has nowhere to be stored.

    STEP 1  capture X1,Y1            (UI drag / cursor corners)
    STEP 2  confirm X1,Y1            -> the value is ECHOED and confirmed
    STEP 3  capture X2,Y2
    STEP 4  confirm the rect         -> computes width/height/centre, ECHOED
    STEP 5  gate: evidence/          -> source correct, 4 red lines, red box
    STEP 6  gate: evidence_final/    -> red box, red cross, cross INSIDE box
            then REGISTER + green tick + LOG + register id

CONFIRMED FORMULA (user 2026-09-20, correcting a typo in the request):
    width  = X2 - X1
    height = Y2 - Y1          <-- NOT X2-X1

WHY THE GATE IS A CONJUNCTION AND NOT A SCORE
---------------------------------------------
`isactive=1` is written ONLY when every question in both gates is YES. A partial
pass is not a softer pass; it is a FAIL, because the whole point of the flag is
that a click may trust it. UNKNOWN is included in "not YES": an uncertain result
is never a silent pass.

FAIL-CLOSED: every step refuses rather than guesses. Step 4 raises on a degenerate
rect (an inverted rect has no width to compute), and step 6 leaves isactive=0 and
writes a FAIL row to the log, so the attempt is still recorded.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import coord_store as cs          # noqa: E402
import evidence_redbox as erb     # noqa: E402
import evidence_store as es       # noqa: E402
import target_gate_evidence as tge  # noqa: E402
import target_step_evidence as tse  # noqa: E402

STEP_NAMES = cs.TARGET_STEP_NAMES


def _evidence_for(session_key: str, step_no: int, state: dict, *, image=None,
                  extra: dict | None = None, db_path: Path | None = None) -> dict:
    """Write ONE step's artefact. Raises rather than advancing without it.

    WHY IT RAISES: the user's requirement is that EVERY step has evidence. If the
    artefact cannot be produced, advancing anyway would tick the step in the UI
    with nothing behind it — a green tick that proves nothing. So a failed
    evidence write is a STEP FAILURE, not a warning.
    """
    res = tse.render_step(session_key, step_no, state, image=image, extra=extra,
                          db_path=db_path)
    if not res.get("ok"):
        raise StepError("STEP %d evidence not written: %s"
                        % (step_no, res.get("error")))
    return res


def log(msg: str) -> None:
    print(msg, flush=True)


class StepError(RuntimeError):
    """Raised when a step cannot proceed. Never swallowed into a pass.

    ONE error type for every refusal, so a caller cannot accidentally catch only
    some of the ways a step declines to advance.
    """


def _metrics(state: dict, *, where: str) -> dict:
    """compute_rect_metrics, but raising StepError instead of a bare ValueError.

    The underlying function raises ValueError (it is a pure helper). Leaking that
    type out of the middleware would mean a caller which catches StepError — as
    the contract says to — misses the single most likely refusals: an inverted or
    zero-size rect.
    """
    try:
        return cs.compute_rect_metrics(state["x1"], state["y1"],
                                       state["x2"], state["y2"])
    except (ValueError, TypeError) as e:
        raise StepError("%s: %s" % (where, e))


# ---------------------------------------------------------------------------
# STEP 1-4: capture + confirm the rect
# ---------------------------------------------------------------------------

def step1_capture_p1(session_key: str, x1: int, y1: int, *,
                     db_path: Path | None = None, image=None,
                     capture_hwnd: int = 0) -> dict:
    """STEP 1: record the first corner, write its artefact, advance to STEP 2.

    `capture_hwnd` (when given) captures THAT WINDOW's region, so the artefact
    shows the target app rather than whatever window is in front — including this
    tool's own UI.
    """
    x1, y1 = int(x1), int(y1)
    if x1 < 0 or y1 < 0:
        raise StepError("STEP 1: negative coordinate (%d,%d)" % (x1, y1))
    ev = _evidence_for(session_key, 1, {"x1": x1, "y1": y1}, image=image,
                       extra={"capture_hwnd": capture_hwnd}, db_path=db_path)
    s = cs.upsert_capture_session(
        session_key, step_no=2, x1=x1, y1=y1, db_path=db_path,
        append_step={"step": 1, "name": STEP_NAMES[1], "x1": x1, "y1": y1,
                     "evidence": ev.get("png_path"),
                     "sha256": ev.get("sha256")},
    )
    log("STEP 1 captured (%d,%d) -> STEP 2" % (x1, y1))
    return {"ok": True, "step_no": s["step_no"], "session": s, "evidence": ev}


def step2_confirm_p1(session_key: str, *,
                     db_path: Path | None = None, image=None,
                     capture_hwnd: int = 0) -> dict:
    """STEP 2: the user confirms the echoed X1,Y1, then advance to STEP 3.

    Refuses when STEP 1 has not run: confirming a value that was never captured
    is the first place a "confirmed" claim can become empty.
    """
    s = cs.get_capture_session(session_key, db_path=db_path)
    if not s:
        raise StepError("STEP 2: no session %r — run STEP 1 first" % session_key)
    if s.get("x1") is None or s.get("y1") is None:
        raise StepError("STEP 2: no captured corner to confirm — run STEP 1 first")
    ev = _evidence_for(session_key, 2, s, image=image,
                       extra={"capture_hwnd": capture_hwnd}, db_path=db_path)
    s = cs.upsert_capture_session(
        session_key, step_no=3, db_path=db_path,
        append_step={"step": 2, "name": STEP_NAMES[2], "confirmed": True,
                     "echo": {"x1": s["x1"], "y1": s["y1"]},
                     "evidence": ev.get("png_path"),
                     "sha256": ev.get("sha256")},
    )
    log("STEP 2 confirmed X1,Y1 = (%s,%s) -> STEP 3" % (s["x1"], s["y1"]))
    return {"ok": True, "step_no": s["step_no"],
            "echo": {"x1": s["x1"], "y1": s["y1"]}, "evidence": ev}


def step3_capture_p2(session_key: str, x2: int, y2: int, *,
                     db_path: Path | None = None, image=None,
                     capture_hwnd: int = 0) -> dict:
    """STEP 3: record the second corner, write its artefact, advance to STEP 4."""
    s = cs.get_capture_session(session_key, db_path=db_path)
    if not s or s.get("x1") is None:
        raise StepError("STEP 3: STEP 1 has not run — no first corner")
    x2, y2 = int(x2), int(y2)
    if x2 < 0 or y2 < 0:
        raise StepError("STEP 3: negative coordinate (%d,%d)" % (x2, y2))
    ev = _evidence_for(session_key, 3,
                       {"x1": s["x1"], "y1": s["y1"], "x2": x2, "y2": y2},
                       image=image, extra={"capture_hwnd": capture_hwnd},
                       db_path=db_path)
    s = cs.upsert_capture_session(
        session_key, step_no=4, x2=x2, y2=y2, db_path=db_path,
        append_step={"step": 3, "name": STEP_NAMES[3], "x2": x2, "y2": y2,
                     "evidence": ev.get("png_path"),
                     "sha256": ev.get("sha256")},
    )
    log("STEP 3 captured (%d,%d) -> STEP 4" % (x2, y2))
    return {"ok": True, "step_no": s["step_no"], "session": s, "evidence": ev}


def step4_confirm_rect(session_key: str, *, screenshot_id: str | None = None,
                       name: str | None = None, source_id: int | None = None,
                       source_kind: str | None = None,
                       source_ref: str | None = None,
                       db_path: Path | None = None, image=None,
                       capture_hwnd: int = 0) -> dict:
    """STEP 4: compute + ECHO the real WIDTH/HEIGHT and centre, then advance.

    Raises on a degenerate rect rather than advancing: the user asked to
    CALCULATE the size, and a calculation over an inverted rect has no meaning.
    Clamping it here would put a plausible number on an impossible measurement.
    """
    s = cs.get_capture_session(session_key, db_path=db_path)
    if not s:
        raise StepError("STEP 4: no session %r" % session_key)
    for k in ("x1", "y1", "x2", "y2"):
        if s.get(k) is None:
            raise StepError("STEP 4: %s not captured yet" % k)
    m = _metrics(s, where="STEP 4")
    # The artefact is rendered BEFORE the session advances, so a step that cannot
    # produce evidence does not move the wizard forward.
    ev = _evidence_for(session_key, 4, s, image=image,
                       extra={"capture_hwnd": capture_hwnd}, db_path=db_path)
    s = cs.upsert_capture_session(
        session_key, step_no=5, name=name, source_id=source_id,
        source_kind=source_kind, source_ref=source_ref,
        screenshot_id=screenshot_id, db_path=db_path,
        append_step={"step": 4, "name": STEP_NAMES[4], "confirmed": True,
                     "metrics": m, "evidence": ev.get("png_path"),
                     "sha256": ev.get("sha256")},
    )
    log("STEP 4 confirmed rect (%d,%d)-(%d,%d) -> width=%d height=%d centre=(%d,%d)"
        % (s["x1"], s["y1"], s["x2"], s["y2"],
           m["width"], m["height"], m["cx"], m["cy"]))
    return {"ok": True, "step_no": s["step_no"],
            "rect": [s["x1"], s["y1"], s["x2"], s["y2"]],
            "metrics": m, "evidence": ev}


# ---------------------------------------------------------------------------
# STEP 5/6: the two proof gates
# ---------------------------------------------------------------------------

def _open_evidence_for(name: str):
    """Create a fresh evidence folder for this capture (append-only store)."""
    return es.open_evidence(name or "target_capture")


def step5_gate_evidence(session_key: str, *, evidence_id: str | None = None,
                        label: str | None = None,
                        capture_hwnd: int = 0, image=None,
                        db_path: Path | None = None) -> dict:
    """STEP 5: LLM gate against `evidence/`.

    Four questions, all required. When `evidence_id` is not supplied the gate
    refuses — it will not invent a folder to judge, because a verdict about
    nothing is exactly the "looks successful" failure being removed.

    MEASURED BUG 2026-09-20: nothing in the capture flow ever CREATED that
    folder. Steps 1-4 write `evidence_steps/<session>/stepN.png`; the gate needs
    `evidence/<EVID>/shot.png` plus a rect. So the gate judged an empty folder —
    fail-closed but UNSATISFIABLE, a red light the operator could never clear.
    `tge.ensure_gate_evidence()` now binds the session's measured rect to a real
    shot first, so the gate has an artefact that actually exists. Proven by
    `_proof_step5_no_shot.py`.
    """
    s = cs.get_capture_session(session_key, db_path=db_path)
    if not s:
        raise StepError("STEP 5: no session %r" % session_key)
    if not evidence_id:
        raise StepError(
            "STEP 5: evidence_id required — the gate must judge a REAL capture, "
            "not an assumed one")

    # --- bind the session's measured rect to a real shot BEFORE gating ---
    bound = tge.ensure_gate_evidence(
        session_key, s, evidence_id=evidence_id, capture_hwnd=capture_hwnd,
        image=image, label=label, db_path=db_path)
    if not bound.get("ok"):
        # No shot means nothing to gate. Refuse the step loudly rather than let
        # the gate fail against an absent folder and blame the model for it.
        cs.write_capture_log(
            "UNREGISTERED", 5, "UNKNOWN",
            "gate evidence not bound: %s" % bound.get("error"),
            db_path=db_path)
        raise StepError("STEP 5: cannot bind gate evidence: %s" % bound.get("error"))

    res = erb.qc_photo_evidence(evidence_id, label or s.get("name"))
    ok = bool(res.get("ok"))
    # The STEP 5 artefact is the gate render itself, written whether the gate
    # PASSES or FAILS: the user needs to SEE why it failed, and a failed gate with
    # no picture is an unexplained red light.
    ev = None
    try:
        ev = _evidence_for(session_key, 5, s,
                           extra={"evidence_id": evidence_id,
                                  "detail": "gate %s: %s"
                                            % (res.get("verdict"),
                                               res.get("error") or "all YES")},
                           db_path=db_path)
    except StepError as e:
        log("STEP 5 evidence FAIL: %s" % e)
    cs.upsert_capture_session(
        session_key, step_no=6 if ok else 5, db_path=db_path,
        append_step={"step": 5, "name": STEP_NAMES[5], "evidence_id": evidence_id,
                     "verdict": res.get("verdict"),
                     "evidence": (ev or {}).get("png_path"),
                     "sha256": (ev or {}).get("sha256"),
                     "failed": [k for k, v in (res.get("checks") or {}).items()
                                if v.get("verdict") != "PASS"]},
    )
    # The log id is derived from the position when one exists. A step-5 outcome
    # has no register id yet (nothing was registered), so it is logged as
    # UNREGISTERED rather than under a fabricated "TGT-0".
    cs.write_capture_log(
        "UNREGISTERED", 5, "PASS" if ok else "FAIL",
        "LEVEL 1 ENVIRONMENT PROOF: %s"
        % (res.get("error") or "all four questions YES"),
        gate_json=json.dumps(res.get("checks") or {}, ensure_ascii=False),
        db_path=db_path,
    )
    if not ok:
        log("LEVEL 1 ENVIRONMENT PROOF FAIL: %s — staying at STEP 5, isactive "
            "will stay 0" % res.get("error"))
        return {"ok": False, "verdict": res.get("verdict"), "gate": res,
                "bound": bound}
    log("LEVEL 1 ENVIRONMENT PROOF PASS (evidence/): source + 4 red lines + red "
        "box + input area all YES -> STEP 6")
    return {"ok": True, "verdict": "PASS", "gate": res, "bound": bound}


def step6_gate_and_registry(session_key: str, *, evidence_id: str | None = None,
                            dry_run: bool = False, capture_hwnd: int = 0,
                            image=None,
                            db_path: Path | None = None) -> dict:
    """STEP 6: red-cross gate, then REGISTER. Returns the green-tick LOG + id.

    The gate runs FIRST and the INSERT happens after it. That ordering is the
    whole contract: no code path can register a target whose evidence was never
    proven. The log row is written on FAIL too, so a failed attempt leaves a
    recorded reason instead of nothing.

    Like STEP 5, the red-cross render needs `evidence/<EVID>/shot.png`. Checked
    here as well because STEP 6 can be reached directly on a resumed session, and
    a cross drawn on a missing shot would fail on the model instead of on the
    real cause.
    """
    s = cs.get_capture_session(session_key, db_path=db_path)
    if not s:
        raise StepError("STEP 6: no session %r" % session_key)
    if s.get("step_no", 0) < 6:
        raise StepError("STEP 6: not ready (at step %s) — STEP 5 must pass first"
                        % s.get("step_no"))
    for k in ("x1", "y1", "x2", "y2", "name"):
        if s.get(k) in (None, ""):
            raise StepError("STEP 6: %s missing from the session" % k)
    if not s.get("source_id") or not s.get("source_kind") or not s.get("source_ref"):
        raise StepError("STEP 6: source_id / source_kind / source_ref required "
                        "(the source is entered ONCE and reused)")
    if not evidence_id:
        raise StepError("STEP 6: evidence_id required for the red-cross gate")

    m = _metrics(s, where="STEP 6")

    # --- make sure the shot the cross is drawn on EXISTS (see step5) ---
    evid_dir = es.EVIDENCE_ROOT / evidence_id
    if not (evid_dir / es.FILES.get("shot", "shot.png")).is_file():
        bound = tge.ensure_gate_evidence(
            session_key, s, evidence_id=evidence_id, capture_hwnd=capture_hwnd,
            image=image, label=s.get("name"), db_path=db_path)
        if not bound.get("ok"):
            cs.write_capture_log(
                "UNREGISTERED", 6, "UNKNOWN",
                "gate evidence not bound: %s" % bound.get("error"),
                db_path=db_path)
            raise StepError("STEP 6: cannot bind gate evidence: %s"
                            % bound.get("error"))

    # --- draw the STEP 6 artefact into evidence_final/ ---
    drawn = erb.render_redcross(evid_dir, out_name=evidence_id)
    if not drawn.get("ok"):
        cs.write_capture_log("UNREGISTERED", 6, "UNKNOWN",
                             "red-cross render failed: %s" % drawn.get("error"),
                             db_path=db_path)
        raise StepError("STEP 6: red cross not drawn: %s" % drawn.get("error"))

    # --- the gate: pixels MEASURE containment, the VL confirms the cross is a cross ---
    vl_path = drawn.get("vl_path") or drawn.get("crop_path")
    # The rect must be expressed in the VL crop's own pixel space, because the
    # crop is offset by the margin. Passing screen coordinates here would measure
    # containment against the wrong rectangle — a silent, plausible-looking wrong
    # answer, which is the whole failure class this project removes.
    box_in_crop = drawn.get("box_in_crop")
    gate = erb.qc_cross(vl_path, box=tuple(box_in_crop) if box_in_crop else None)

    # STEP 6 evidence is written REGARDLESS of the gate outcome, exactly as step 5
    # does. Measured 2026-09-20: writing it only on the pass path left a FAILING
    # step 6 with no picture at all, so the operator saw a red light with nothing
    # to inspect — and `verify_step_evidence` reported the step as not covered.
    # The artefact must exist for the step to be REVIEWABLE, which is a different
    # requirement from the step having PASSED.
    ev6 = None
    try:
        ev6 = _evidence_for(
            session_key, 6, s,
            extra={"evidence_id": evidence_id,
                   "detail": "gate %s%s" % (
                       gate.get("verdict"),
                       "" if gate.get("ok") else " — " + str(gate.get("error")))},
            db_path=db_path)
    except StepError as e:
        log("STEP 6 evidence FAIL: %s" % e)

    if not gate.get("ok"):
        cs.write_capture_log(
            "UNREGISTERED", 6, "FAIL",
            "LEVEL 2 TARGET PROOF not all-YES: %s" % (gate.get("error") or ""),
            gate_json=json.dumps(gate.get("checks") or {}, ensure_ascii=False),
            db_path=db_path,
        )
        log("LEVEL 2 TARGET PROOF FAIL: %s — NOT registered, isactive stays 0"
            % (gate.get("error") or "not all-YES"))
        return {"ok": False, "verdict": gate.get("verdict"), "gate": gate,
                "registered": None, "artefact": drawn, "step_evidence": ev6}

    if dry_run:
        return {"ok": True, "verdict": "PASS", "gate": gate, "registered": None,
                "would_registry": {"name": s["name"], "rect": [s["x1"], s["y1"],
                                                               s["x2"], s["y2"]],
                                   **m},
                "artefact": drawn}

    # --- register ONLY now, with isactive=1 because BOTH gates passed ---
    try:
        row = cs.create_target_position(
            s["name"], int(s["source_id"]), s["source_kind"], s["source_ref"],
            s["x1"], s["y1"], s["x2"], s["y2"],
            screenshot_id=s.get("screenshot_id"), evidence_id=evidence_id,
            cross_evidence_id=evidence_id, isactive=1, db_path=db_path,
        )
    except Exception as e:
        cs.write_capture_log("UNREGISTERED", 6, "FAIL",
                             "register failed: %s: %s" % (type(e).__name__, e),
                             db_path=db_path)
        raise StepError("STEP 6: register failed: %s: %s" % (type(e).__name__, e))

    cs.create_autocal_row(
        row["id"], s["name"], m["width"], m["height"], m["cx"], m["cy"],
        cross_evidence_id=evidence_id, isactive=1, db_path=db_path,
    )
    reg = row["register_id"]
    # Re-stamp step 6's caption now that the register id exists, so the artefact
    # shows WHICH target it proved rather than only that a gate ran.
    try:
        ev6 = _evidence_for(
            session_key, 6, s,
            extra={"evidence_id": evidence_id,
                   "detail": "registered %s  w=%d h=%d"
                             % (reg, m["width"], m["height"])},
            db_path=db_path)
    except StepError as e:
        log("STEP 6 evidence re-stamp FAIL: %s" % e)
    cs.write_capture_log(
        reg, 6, "PASS",
        "LEVEL 1 + LEVEL 2 both all-YES; registered %s rect (%d,%d)-(%d,%d) "
        "w=%d h=%d"
        % (reg, s["x1"], s["y1"], s["x2"], s["y2"], m["width"], m["height"]),
        position_id=row["id"], gate_json=json.dumps(gate.get("checks") or {},
                                                    ensure_ascii=False),
        db_path=db_path,
    )
    cs.upsert_capture_session(
        session_key, status="registered", db_path=db_path,
        append_step={"step": 6, "name": STEP_NAMES[6], "register_id": reg,
                     "registered": True,
                     "evidence": (ev6 or {}).get("png_path"),
                     "sha256": (ev6 or {}).get("sha256")},
    )
    log("LEVEL 2 TARGET PROOF PASS (evidence_final/): green tick  "
        "register_id=%s  registered %s  w=%d h=%d"
        % (reg, row["created_at"], m["width"], m["height"]))
    return {"ok": True, "verdict": "PASS", "gate": gate, "registered": row,
            "register_id": reg, "metrics": m, "artefact": drawn,
            "step_evidence": ev6, "created_at": row.get("created_at")}


# ---------------------------------------------------------------------------
# convenience: the whole flow in one call (used by proofs/tests)
# ---------------------------------------------------------------------------

def run_all(session_key: str, *, name: str, source_id: int, source_kind: str,
            source_ref: str, x1: int, y1: int, x2: int, y2: int,
            evidence_id: str, screenshot_id: str | None = None,
            label: str | None = None, db_path: Path | None = None,
            image=None, capture_hwnd: int = 0) -> dict:
    """Drive STEP 1..6 in order and return every step's result.

    Stops at the first step that does not advance, so the returned `failed_at`
    names the exact step rather than leaving the caller to infer it.
    """
    out: dict = {"steps": {}, "failed_at": None}
    try:
        out["steps"]["1"] = step1_capture_p1(session_key, x1, y1, db_path=db_path,
                                              image=image,
                                              capture_hwnd=capture_hwnd)
        out["steps"]["2"] = step2_confirm_p1(session_key, db_path=db_path,
                                              image=image,
                                              capture_hwnd=capture_hwnd)
        out["steps"]["3"] = step3_capture_p2(session_key, x2, y2, db_path=db_path,
                                              image=image,
                                              capture_hwnd=capture_hwnd)
        out["steps"]["4"] = step4_confirm_rect(
            session_key, screenshot_id=screenshot_id, name=name,
            source_id=source_id, source_kind=source_kind, source_ref=source_ref,
            db_path=db_path, image=image, capture_hwnd=capture_hwnd)
    except StepError as e:
        out["failed_at"] = "1-4"
        out["error"] = str(e)
        return out
    s5 = step5_gate_evidence(session_key, evidence_id=evidence_id, label=label,
                             db_path=db_path)
    out["steps"]["5"] = s5
    if not s5["ok"]:
        out["failed_at"] = 5
        out["error"] = s5["gate"].get("error")
        return out
    s6 = step6_gate_and_registry(session_key, evidence_id=evidence_id,
                                db_path=db_path)
    out["steps"]["6"] = s6
    if not s6["ok"]:
        out["failed_at"] = 6
        out["error"] = s6["gate"].get("error")
        return out
    out["ok"] = True
    out["register_id"] = s6.get("register_id")
    out["truth"] = s6.get("registered")
    # Final proof for the UI: all 6 steps must have a verifiable artefact. Reported
    # here so a caller cannot conclude "registered" while a step is unticked.
    out["step_evidence"] = cs.verify_step_evidence(session_key, db_path=db_path)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step1", nargs=3, metavar=("SESSION", "X1", "Y1"))
    ap.add_argument("--step2", metavar="SESSION")
    ap.add_argument("--step3", nargs=3, metavar=("SESSION", "X2", "Y2"))
    ap.add_argument("--step4", metavar="SESSION")
    ap.add_argument("--step5", nargs=2, metavar=("SESSION", "EVID"))
    ap.add_argument("--step6", nargs=2, metavar=("SESSION", "EVID"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--name")
    ap.add_argument("--source-id", type=int)
    ap.add_argument("--source-kind", choices=["APP", "URL"])
    ap.add_argument("--source-ref")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--log", metavar="REGISTER_ID", nargs="?", const="")
    # Lets a proof/test point at its own DB instead of the live coords.db. A
    # verification run that writes production state is not a verification run.
    ap.add_argument("--db", metavar="PATH", help="override the coords DB path")
    a = ap.parse_args()
    DBP = Path(a.db) if a.db else None

    if a.step1:
        print(json.dumps(step1_capture_p1(a.step1[0], int(a.step1[1]),
                                          int(a.step1[2]), db_path=DBP), indent=2,
                         ensure_ascii=False, default=str))
    elif a.step2:
        print(json.dumps(step2_confirm_p1(a.step2, db_path=DBP), indent=2,
                         ensure_ascii=False, default=str))
    elif a.step3:
        print(json.dumps(step3_capture_p2(a.step3[0], int(a.step3[1]),
                                          int(a.step3[2]), db_path=DBP), indent=2,
                         ensure_ascii=False, default=str))
    elif a.step4:
        print(json.dumps(step4_confirm_rect(
            a.step4, name=a.name, source_id=a.source_id,
            source_kind=a.source_kind, source_ref=a.source_ref,
            db_path=DBP), indent=2, ensure_ascii=False, default=str))
    elif a.step5:
        print(json.dumps(step5_gate_evidence(a.step5[0], evidence_id=a.step5[1],
                                             db_path=DBP),
                         indent=2, ensure_ascii=False, default=str))
    elif a.step6:
        print(json.dumps(step6_gate_and_registry(
            a.step6[0], evidence_id=a.step6[1], dry_run=a.dry_run,
            db_path=DBP),
            indent=2, ensure_ascii=False, default=str))
    elif a.list:
        for r in cs.list_target_positions(active_only=False, db_path=DBP):
            print("%-10s %-24s (%5s,%5s)-(%5s,%5s) w=%-4s h=%-4s active=%s"
                  % (r["register_id"], r["name"], r["x1"], r["y1"], r["x2"],
                     r["y2"], r["width"], r["height"], r["isactive"]))
    elif a.log is not None:
        for r in cs.read_capture_log(a.log or None, db_path=DBP):
            print("%-16s step %s %-24s %-7s %s"
                  % (r["register_id"], r["step_no"], r["step_name"], r["status"],
                     (r["detail"] or "")[:70]))
    else:
        ap.print_help()