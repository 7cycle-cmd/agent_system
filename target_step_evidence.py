# -*- coding: utf-8 -*-
"""Per-step evidence — ONE PROVABLE ARTEFACT FOR EACH OF THE 6 STEPS.

WHY THIS MODULE EXISTS
----------------------
"has evidence for each step" is not satisfied by a folder of screenshots. A PNG
proves a file exists; it does not prove WHICH STEP produced it, nor that it was
not swapped afterwards. So every render here also RECORDS the artefact against
(session_key, step_no) with a sha256 of the real bytes (coord_store
.record_step_evidence). The claim "step 4 has evidence" is then answerable by
QUERY and re-checkable by HASH.

    STEP 1  point_1      a crosshair at (X1,Y1)
    STEP 2  confirm_1    the crosshair + the echoed X1,Y1 the user confirmed
    STEP 3  point_2      a crosshair at (X2,Y2)
    STEP 4  rect         the rectangle + WIDTH / HEIGHT / centre as real numbers
    STEP 5  box_gate     full screen + 4 red lines + red box   (evidence/  gate)
    STEP 6  cross_gate   box + RED CROSS inside the box        (evidence_final/ gate)

A LIVE CAPTURE, NOT A DRAWING
-----------------------------
Steps 1-4 capture the screen AT THE MOMENT THE STEP RUNS and draw the mark onto
that frame. Plotting the coordinates onto a blank canvas would prove the numbers
were stored; it would not show they were read from the screen at that position.
The capture is what makes the artefact evidence rather than an illustration.

WHY STEPS 5/6 DELEGATE
----------------------
They call `evidence_redbox.render_pair` / `render_redcross` so the UI artefact and
the GATE artefact are the same bytes. Re-drawing them here would create a second
renderer that could drift from the one the gate judges — two pictures of one step,
disagreeing, with no way to say which is authoritative.

`image=` may be injected so a proof can run without a screen. It is not a
convenience: without it these paths could only be tested on a live desktop.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import coord_store as cs          # noqa: E402
import evidence_redbox as erb     # noqa: E402
import evidence_store as es       # noqa: E402

# A THIRD evidence root, kept separate on purpose:
#   evidence/       the STEP 5 gate
#   evidence_final/ the STEP 6 gate
#   evidence_steps/ per-step artefacts for the wizard UI
STEP_EVIDENCE_ROOT = BASE_DIR / "evidence_steps"

# The evidence KIND per step. Deliberately SEPARATE from TARGET_STEP_NAMES: the
# step name describes what the STEP does, the kind describes what the ARTEFACT is.
# Measured 2026-09-20: reusing the step name made step 6's artefact report its kind
# as "gate_redcross_registry", which reads like an action rather than a picture and
# would make "which artefacts are cross renders?" unanswerable by query.
STEP_EVIDENCE_KINDS = {
    1: "point_1", 2: "confirm_1", 3: "point_2", 4: "rect",
    5: "box_gate", 6: "cross_gate",
}

RED = (255, 0, 0)
AMBER = (255, 176, 0)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def log(msg: str) -> None:
    print(msg, flush=True)


def _root() -> Path:
    # Read at CALL time, not import time: set_evidence_root() must be able to
    # redirect a proof's writes away from the production tree.
    return STEP_EVIDENCE_ROOT


def _session_dir(session_key: str) -> Path:
    """The per-session evidence folder. Raises on failure — the caller reports it.

    Deliberately NOT swallowed: if the folder cannot be created then no artefact
    can be written, and a step that cannot produce evidence must not advance. The
    failure is translated into `ok=False` by render_step so the single StepError
    contract holds.
    """
    d = _root() / str(session_key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _capture():
    """A real full-screen frame. Injected by callers that pass `image`."""
    import pyautogui

    pyautogui.FAILSAFE = False
    return pyautogui.screenshot().convert("RGB")


def capture_window(hwnd: int, *, pad: int = 0):
    """Capture the TARGET APP's own window region, whatever is in front of it.

    See capture_window_with_origin() for the contract. Kept as a thin wrapper so
    existing callers keep working.
    """
    img, _origin = capture_window_with_origin(hwnd, pad=pad)
    return img


def capture_window_with_origin(hwnd: int, *, pad: int = 0):
    """Capture the TARGET APP's own window region. Returns (image, (ox, oy)).

    WHY THE ORIGIN IS RETURNED
    --------------------------
    A screen rect can only be drawn onto this capture if it is translated by the
    capture's TRUE origin. MEASURED BUG 2026-09-20: the caller subtracted the
    window's stored screen x/y (doubao reports x1=-8) while the crop origin is
    `max(0, x1)` = 0 — so every mark was 8px off in x. WORSE, a later version
    additionally applied a screen-vs-capture SCALE, but the crop takes actual
    pixels 1:1, so it shifted y by 26px for a maximized window and drew the red
    box across the wrong part of the app. Both bugs looked deliberate.

    Returning the origin removes the guesswork: there is now ONE computation and
    every caller uses its result.

    WHY THIS EXISTS (measured 2026-09-20)
    -------------------------------------
    A full-screen capture taken to serve the wizard's own HTTP request shows the
    WIZARD: the browser is foreground because it just made the request. The
    resulting artefact had the correct rect over the correct app but with the
    capture page's pixels on top — an image that LOOKS like evidence and is not.
    A per-step artefact must show the TARGET, not the tool.

    So the window is raised, the raise is PROVEN (foreground + maximized), the
    screen is captured, and the result is cropped to that window's rect. If the
    raise cannot be proven, this RAISES rather than returning a screenshot of
    whatever happened to be on top — a silent fallback here is exactly how the
    first screenshot ended up misleading.

    Returns (PIL Image, (origin_x, origin_y)), or raises RuntimeError.
    """
    sys.path.insert(0, str(BASE_DIR))
    import f_doubao_send as fd

    fd.activate_window(int(hwnd))
    rep = fd.prove_env(int(hwnd))
    if not rep.get("ok"):
        raise RuntimeError(
            "cannot capture hwnd %s: environment not proven (%s) — refusing to "
            "return a screenshot of an unproven window" % (hwnd, rep.get("detail")))
    img = _capture()
    x1, y1, x2, y2 = rep["rect"]
    w, h = img.size
    # The window rect is in REAL screen space and the screenshot can differ in
    # size (DPR / a scaled display), so scale the rect before cropping.
    sw, sh = fd._screen_size()
    sx, sy = (w / float(sw or w)), (h / float(sh or h))
    cx1 = max(0, min(w - 1, int(x1 * sx) - pad))
    cy1 = max(0, min(h - 1, int(y1 * sy) - pad))
    cx2 = max(0, min(w, int(x2 * sx) + pad))
    cy2 = max(0, min(h, int(y2 * sy) + pad))
    if cx2 - cx1 < 10 or cy2 - cy1 < 10:
        raise RuntimeError("window rect %s collapses in a %dx%d capture"
                           % (rep["rect"], w, h))
    return img.crop((cx1, cy1, cx2, cy2)), (cx1, cy1)


def _label(draw, x: int, y: int, text: str, *, bg=BLACK, fg=WHITE) -> None:
    """A readable caption. Drawn on a filled box so it survives any background."""
    pad = 3
    w = len(text) * 6 + pad * 2
    h = 12 + pad * 2
    draw.rectangle([x, y, x + w, y + h], fill=bg)
    draw.text((x + pad, y + pad), text, fill=fg)


def _crosshair(draw, x: int, y: int, *, arm: int = 14, color=RED, width: int = 3):
    """A crosshair + a small circle, so a human sees WHERE the point is."""
    draw.line([(x - arm, y), (x + arm, y)], fill=color, width=width)
    draw.line([(x, y - arm), (x, y + arm)], fill=color, width=width)
    draw.ellipse([x - arm - 3, y - arm - 3, x + arm + 3, y + arm + 3],
                 outline=color, width=2)


# ---------------------------------------------------------------------------
# per-step rendering
# ---------------------------------------------------------------------------

def _render_point(img, state: dict, which: int):
    """STEP 1 / STEP 3: one corner, marked on the live frame."""
    from PIL import ImageDraw

    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    x = int(state["x1"] if which == 1 else state["x2"])
    y = int(state["y1"] if which == 1 else state["y2"])
    _crosshair(d, x, y)
    _label(d, max(0, x + 18), max(0, y - 26),
           "STEP %d  %s = (%d,%d)" % (which,
                                      "X1,Y1" if which == 1 else "X2,Y2", x, y))
    return out


def _render_confirm1(img, state: dict):
    """STEP 2: the user CONFIRMED X1,Y1 — show the value that was confirmed."""
    from PIL import ImageDraw

    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    x, y = int(state["x1"]), int(state["y1"])
    _crosshair(d, x, y, color=AMBER, arm=18)
    _label(d, 12, 12, "STEP 2 CONFIRMED  X1=%d  Y1=%d" % (x, y), bg=AMBER, fg=BLACK)
    _label(d, max(0, x + 22), max(0, y - 28), "confirmed (%d,%d)" % (x, y))
    return out


def _render_rect(img, state: dict):
    """STEP 4: the rectangle + the REAL width/height/centre as numbers."""
    from PIL import ImageDraw

    m = cs.compute_rect_metrics(state["x1"], state["y1"],
                               state["x2"], state["y2"])
    x1, y1, x2, y2 = (int(state["x1"]), int(state["y1"]),
                      int(state["x2"]), int(state["y2"]))
    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    d.rectangle([x1, y1, x2, y2], outline=RED, width=3)
    # full-span lines so the boundary can be followed across the image
    w, h = out.size
    for px in (x1, x2):
        d.line([(px, 0), (px, h)], fill=RED, width=1)
    for py in (y1, y2):
        d.line([(0, py), (w, py)], fill=RED, width=1)
    _crosshair(d, m["cx"], m["cy"], arm=8, width=2)
    _label(d, max(0, x1), max(0, y1 - 30),
           "STEP 4  W=%d  H=%d  centre=(%d,%d)"
           % (m["width"], m["height"], m["cx"], m["cy"]))
    return out, m


def _rect_into_window_space(state: dict, hwnd: int) -> dict:
    """Translate the rect from SCREEN coordinates into WINDOW-LOCAL coordinates.

    The stored rects are screen coordinates (e.g. doubao_chatbox 400,916), but a
    window capture's (0,0) is the window's top-left, which is NOT the screen
    origin. Drawing an untranslated rect onto a window capture would put the mark
    in the wrong place — a wrong artefact that still looks deliberate.

    The translation is done by the capture's OWN origin, obtained from
    `capture_window_with_origin()`. MEASURED 2026-09-20: two earlier versions were
    both wrong in different ways — one subtracted the window's stored screen x/y
    (doubao reports x1=-8) while the crop origin is `max(0, x1)` = 0, making every
    mark 8px off; another added a screen-vs-capture scale that does not exist
    because the crop is 1:1, shifting y by 26px on a maximized window. Measuring
    the origin instead of guessing it is the whole point.
    """
    img, (ox, oy) = capture_window_with_origin(int(hwnd))
    out = dict(state)
    for k in ("x1", "x2"):
        if state.get(k) is not None:
            out[k] = int(state[k]) - ox
    for k in ("y1", "y2"):
        if state.get(k) is not None:
            out[k] = int(state[k]) - oy
    return out


def render_step(session_key: str, step_no: int, state: dict, *,
                image=None, extra: dict | None = None,
                db_path: Path | None = None) -> dict[str, Any]:
    """Render + RECORD the artefact for one step. Returns {ok, step_no, ...}.

    For STEP 5/6 the artefact is the GATE render itself (same bytes the gate
    judges), so `evidence_id` must be supplied for those steps — without it there
    is no gate image to bind, and inventing one would make the UI artefact
    disagree with the thing that was actually verified.

    `extra["capture_hwnd"]` captures that window's own region instead of the whole
    desktop. The coordinates are then translated into the crop's space, so the
    crosshair still lands on the real screen position of the target.
    """
    sn = int(step_no)
    if sn not in cs.TARGET_STEP_NAMES:
        return {"ok": False, "step_no": sn, "error": "step_no must be 1..6"}
    sk = str(session_key or "").strip()
    if not sk:
        return {"ok": False, "step_no": sn, "error": "session_key required"}

    out: dict[str, Any] = {"ok": False, "step_no": sn, "session_key": sk,
                           "kind": STEP_EVIDENCE_KINDS[sn], "png_path": None,
                           "sha256": None, "error": None, "metrics": None}
    evid = str((extra or {}).get("evidence_id") or "")
    metrics = None

    try:
        # INSIDE the try: creating the folder can fail (permissions, a path that
        # is a file, a full disk), and a raw OSError escaping here would bypass the
        # single StepError contract that callers catch — turning "cannot write
        # evidence" into an unhandled crash instead of a refused step.
        dest = _session_dir(sk) / ("step%d.png" % sn)
        if sn in (5, 6):
            # Delegate so the UI artefact IS the gate artefact (same bytes).
            if not evid:
                return dict(out, error=("STEP %d needs evidence_id — its artefact "
                                        "must BE the gate render" % sn))
            evid_dir = es.EVIDENCE_ROOT / evid
            if sn == 5:
                r = erb.render_pair(evid_dir)
                if not r.get("ok"):
                    return dict(out, error="gate render failed: %s" % r.get("error"))
                src = r.get("full_path")
                metrics = {"rect": r.get("rect"), "source": r.get("source")}
            else:
                r = erb.render_redcross(evid_dir, out_name=evid)
                if not r.get("ok"):
                    return dict(out, error="cross render failed: %s" % r.get("error"))
                src = r.get("full_path")
                metrics = {"rect": r.get("rect"), "centre": r.get("centre"),
                           "box_in_crop": r.get("box_in_crop")}
            from PIL import Image
            img = Image.open(str(src)).convert("RGB")
        else:
            hwnd = (extra or {}).get("capture_hwnd")
            if image is not None:
                img = image
            elif hwnd:
                # Capture the TARGET APP's own window, not the desktop. A desktop
                # capture taken to serve the wizard's request shows the WIZARD.
                img = capture_window(int(hwnd))
                # The rect is in SCREEN coordinates; the capture is window-local.
                # Translate it, or the crosshair lands outside the image on a
                # window that does not start at 0,0 (measured: doubao's rect
                # starts at -8,-8, so an untranslated point is off by the origin).
                state = _rect_into_window_space(state, int(hwnd))
            else:
                img = _capture()

        if sn == 1:
            rendered = _render_point(img, state, 1)
        elif sn == 2:
            rendered = _render_confirm1(img, state)
        elif sn == 3:
            rendered = _render_point(img, state, 3)
        elif sn == 4:
            rendered, metrics = _render_rect(img, state)
        else:
            rendered = img  # 5/6 already carry their own marks

        if not isinstance(rendered, tuple):
            rendered = (rendered, None)
        final, metrics2 = rendered
        if metrics2:
            metrics = metrics2
        final.save(str(dest))
    except Exception as e:
        return dict(out, error="%s: %s" % (type(e).__name__, e))

    w, h = final.size
    detail = (extra or {}).get("detail") or ""
    if metrics and not detail:
        detail = ", ".join("%s=%s" % (k, v) for k, v in metrics.items())
    try:
        row = cs.record_step_evidence(
            sk, sn, evid or sk, out["kind"], str(dest),
            width=w, height=h, detail=detail, db_path=db_path,
        )
    except Exception as e:
        # A step whose artefact could not be RECORDED is not covered: reporting
        # success here would let the wizard tick a step with no provable evidence.
        return dict(out, error="evidence recorded failed: %s: %s"
                                % (type(e).__name__, e))
    out.update({"ok": True, "png_path": str(dest),
                "sha256": row.get("sha256"), "width": w, "height": h,
                "metrics": metrics, "evidence_id": evid or sk,
                "detail": detail})
    log("STEP %d evidence -> %s (%dx%d, sha256 %s..)"
        % (sn, dest.name, w, h, str(row.get("sha256"))[:10]))
    return out


def render_all(session_key: str, *, image=None, evidence_id: str = "",
               db_path: Path | None = None) -> dict[str, Any]:
    """Render every step whose data is already in the session.

    Used by the UI's "fill the missing steps" action and by the proof. It SKIPS a
    step whose inputs are absent rather than inventing them, so a partial session
    produces a partial (and honestly reported) set of artefacts.
    """
    s = cs.get_capture_session(session_key, db_path=db_path)
    if not s:
        return {"ok": False, "error": "no session %r" % session_key, "steps": {}}
    steps: dict[str, Any] = {}
    have_1 = s.get("x1") is not None and s.get("y1") is not None
    have_2 = have_1 and s.get("x2") is not None and s.get("y2") is not None
    if have_1:
        steps["1"] = render_step(session_key, 1, s, image=image, db_path=db_path)
        # STEP 2 only exists once the user confirmed, which the session records in
        # step_log. Guessing it from "x1 exists" would fabricate a confirmation.
        if _confirmed(s, 2):
            steps["2"] = render_step(session_key, 2, s, image=image, db_path=db_path)
    if have_2:
        steps["3"] = render_step(session_key, 3, s, image=image, db_path=db_path)
        if _confirmed(s, 4):
            steps["4"] = render_step(session_key, 4, s, image=image, db_path=db_path)
    if evidence_id and have_2:
        steps["5"] = render_step(session_key, 5, s, image=image,
                                 extra={"evidence_id": evidence_id},
                                 db_path=db_path)
        if _confirmed(s, 6) or int(s.get("step_no") or 0) >= 6:
            steps["6"] = render_step(session_key, 6, s, image=image,
                                     extra={"evidence_id": evidence_id},
                                     db_path=db_path)
    v = cs.verify_step_evidence(session_key, db_path=db_path)
    return {"ok": bool(v["ok"]), "steps": steps, "verify": v}


def _confirmed(session: dict, step_no: int) -> bool:
    """Did the user actually CONFIRM this step? Reads step_log, not the coords."""
    import json

    try:
        log_rows = json.loads(session.get("step_log") or "[]")
    except Exception:
        return False
    return any(int(r.get("step") or 0) == int(step_no) and r.get("confirmed")
               for r in log_rows if isinstance(r, dict))


def missing_plan(session_key: str, *, db_path: Path | None = None) -> dict[str, Any]:
    """Which steps still lack evidence, and whether the session has the inputs.

    Separates the two reasons a step can be unticked — "not reached yet" vs
    "reached but the artefact was never written" — because they need different
    actions from the operator and lumping them together hides which one it is.
    """
    s = cs.get_capture_session(session_key, db_path=db_path)
    missing = cs.steps_missing_evidence(session_key, db_path=db_path)
    ready: list[int] = []
    blocked: dict[int, str] = {}
    for n in missing:
        if n in (1, 3) or (n == 2 and s and s.get("x1") is not None) or \
           (n == 4 and s and s.get("x2") is not None) or \
           (n in (5, 6) and s and s.get("x2") is not None):
            ready.append(n)
        else:
            blocked[n] = "inputs not captured yet"
    return {"session_key": session_key, "missing_steps": missing,
            "regenerable_now": ready, "blocked": blocked}


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--render", metavar="SESSION",
                    help="render + record every step the session already supports")
    ap.add_argument("--evidence-id", default="")
    ap.add_argument("--list", metavar="SESSION")
    ap.add_argument("--verify", metavar="SESSION")
    ap.add_argument("--missing", metavar="SESSION")
    a = ap.parse_args()

    if a.render:
        print(json.dumps(render_all(a.render, evidence_id=a.evidence_id),
                         indent=2, ensure_ascii=False, default=str))
    elif a.list:
        for r in cs.list_step_evidence(a.list):
            print("step %s %-22s %-40s %s" % (r["step_no"], r["kind"],
                                              Path(r["png_path"]).name,
                                              str(r["sha256"])[:12]))
    elif a.verify:
        print(json.dumps(cs.verify_step_evidence(a.verify), indent=2,
                         ensure_ascii=False))
    elif a.missing:
        print(json.dumps(missing_plan(a.missing), indent=2, ensure_ascii=False))
    else:
        ap.print_help()