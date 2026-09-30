# -*- coding: utf-8 -*-
"""Bind the 6-STEP capture to the LLM gates.

WHY THIS MODULE EXISTS (measured 2026-09-20)
--------------------------------------------
`step5_gate_evidence()` gates `evidence/<EVID>/`, and `render_pair()` /
`render_redcross()` need `evidence/<EVID>/shot.png` + a rect. But NOTHING in the
capture flow created that folder:

  * steps 1-4 write only `evidence_steps/<session>/stepN.png`
  * `target_capture._open_evidence_for()` had ZERO callers (dead code)
  * `evidence_store.open_evidence()` creates the FOLDER but never writes shot.png
  * `step4_confirm_rect(screenshot_id=...)` was the only way a session got an
    evidence id, and the UI never passed one

So STEP 5 asked a 7B-VL to judge a folder that did not exist. It failed
fail-closed (correct) but was UNSATISFIABLE — the operator saw a red light with
no way to earn a green one. `_proof_step5_no_shot.py` proves it.

The fix binds the two halves together:

    capture the window  ->  write evidence/<EVID>/shot.png
                        ->  write classify.json {rect_real: ...}  (using the
                            rect the operator actually measured)
                        ->  THEN the existing gates can judge a real artefact

`rect_real` is authoritative in `evidence_redbox.resolve_rect()`, which is
exactly right here: this rect is not inferred by a classifier, it is the
MEASURED rect the operator confirmed at STEP 4.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import evidence_store as es  # noqa: E402
import target_step_evidence as tse  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


class BindError(RuntimeError):
    """Raised when the gate evidence cannot be created. Never swallowed into a pass."""


def _rect_from_state(state: dict) -> tuple[int, int, int, int]:
    try:
        x1, y1 = int(state["x1"]), int(state["y1"])
        x2, y2 = int(state["x2"]), int(state["y2"])
    except (KeyError, TypeError, ValueError) as e:
        raise BindError("rect requires x1,y1,x2,y2 in the session: %s" % e)
    if x2 <= x1 or y2 <= y1:
        raise BindError(
            "refusing to bind a degenerate rect (%d,%d)-(%d,%d): a red box drawn "
            "around an impossible measurement would look like proof"
            % (x1, y1, x2, y2))
    return x1, y1, x2, y2


def ensure_gate_evidence(
    session_key: str,
    state: dict,
    *,
    evidence_id: str | None = None,
    capture_hwnd: int = 0,
    image=None,
    label: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Create `evidence/<EVID>/` with shot.png + classify.json, ready for a gate.

    Returns {ok, evidence_id, shot_path, classify_path, rect, size, error}.

    The shot and the rect MUST come from the same session measurement. The rect is
    written in the SAME coordinate space as the shot:

      * screen capture -> screen rect (as measured)
      * window capture -> rect translated into the window crop's own space,
        because the crop's (0,0) is the window's top-left, not the screen origin

    Getting this wrong draws a confident red box in the wrong place, so the
    translation is done explicitly rather than assumed.
    """
    out: dict[str, Any] = {"ok": False, "evidence_id": None, "shot_path": None,
                           "classify_path": None, "rect": None, "size": None,
                           "space": None, "error": None}
    sk = str(session_key or "").strip()
    if not sk:
        out["error"] = "session_key required"
        return out

    evid = str(evidence_id or "").strip()
    if not evid:
        # No id supplied -> a fresh folder. NOTE the UI does NOT take this path: it
        # derives `EVID-<session slug>`, so a RESUMED session reuses its id and
        # RE-WRITES its shot. That is intentional (the evidence belongs to the
        # session, and re-capturing is how a failed gate is retried) — but it does
        # mean the UI path overwrites a previous attempt's shot rather than
        # accumulating one folder per attempt.
        rec = es.open_evidence(str(state.get("name") or sk)[:40] or "target_capture")
        evid = rec.evidence_id
    out["evidence_id"] = evid
    evid_dir = es.EVIDENCE_ROOT / evid
    evid_dir.mkdir(parents=True, exist_ok=True)

    rect = _rect_from_state(state)

    # --- capture, and record WHICH space the rect is expressed in ---
    if image is not None:
        img = image
        space = "screen"
    elif capture_hwnd:
        try:
            img = tse.capture_window(int(capture_hwnd))
        except Exception as e:
            out["error"] = "window capture failed: %s: %s" % (type(e).__name__, e)
            return out
        rect = _rect_into_window_space(rect, int(capture_hwnd))
        space = "window"
    else:
        try:
            img = tse._capture()
        except Exception as e:
            out["error"] = "screen capture failed: %s: %s" % (type(e).__name__, e)
            return out
        space = "screen"

    w, h = img.size
    out["size"] = [w, h]
    out["space"] = space

    # A rect outside the image is a rect that cannot be marked. Clamping it would
    # put the box somewhere nobody measured, so refuse instead.
    if not (0 <= rect[0] < rect[2] <= w and 0 <= rect[1] < rect[3] <= h):
        out["error"] = (
            "measured rect %s (%s space) does not fit the %dx%d capture — the box "
            "would land outside the image, so no evidence is written"
            % (list(rect), space, w, h))
        return out

    shot_path = evid_dir / es.FILES.get("shot", "shot.png")
    try:
        img.convert("RGB").save(str(shot_path))
    except Exception as e:
        out["error"] = "shot write failed: %s: %s" % (type(e).__name__, e)
        return out
    out["shot_path"] = str(shot_path)

    classify_path = evid_dir / es.FILES.get("classify", "classify.json")
    payload = {
        "evidence_id": evid,
        "target_id": str(state.get("name") or sk),
        "task_id": sk,
        "rect_real": {"x1": rect[0], "y1": rect[1], "x2": rect[2], "y2": rect[3]},
        "rect_space": space,
        "source_ref": state.get("source_ref"),
        "source_kind": state.get("source_kind"),
        "source_id": state.get("source_id"),
        "label": label or state.get("name") or sk,
        "origin": ("measured by the 6-STEP capture (STEP 4 confirmed); NOT "
                   "classifier-inferred"),
    }
    try:
        classify_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    except Exception as e:
        out["error"] = "classify.json write failed: %s: %s" % (type(e).__name__, e)
        return out
    out["classify_path"] = str(classify_path)
    out["rect"] = list(rect)
    out["ok"] = True
    log("gate evidence -> %s (shot %dx%d, rect %s in %s space)"
        % (evid, w, h, list(rect), space))
    return out


def _rect_into_window_space(rect: tuple[int, int, int, int],
                            hwnd: int) -> tuple[int, int, int, int]:
    """Translate a SCREEN rect into the window crop's own pixel space.

    Uses the capture's OWN origin, returned by `capture_window_with_origin()`.
    MEASURED BUG 2026-09-20: subtracting the window's stored screen x/y put the
    red box 8px right of the real target, and applying a screen-vs-capture SCALE
    shifted it 26px up, because the crop is 1:1 and no such scale exists. Both
    produced a confidently-placed wrong box. Measuring the origin instead of
    guessing it is the whole point.
    """
    img, (ox, oy) = tse.capture_window_with_origin(int(hwnd))
    x1, y1, x2, y2 = rect
    return (int(x1) - ox, int(y1) - oy, int(x2) - ox, int(y2) - oy)