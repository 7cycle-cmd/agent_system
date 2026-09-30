# -*- coding: utf-8 -*-
"""Evidence red box — the 2-PNG pair a human AND an LLM can both inspect.

WHY THIS EXISTS
---------------
A captured screenshot alone is ambiguous: neither a human nor a VL model can tell
WHICH part of the screen the worker meant. The fix is to draw the target area on
the image in RED, so the reader and the writer are looking at the same thing.

USER SPEC (2026-09-20), quoted because it defines the output exactly:
  - "4 red line will got the red box, and red box = area we are looking for"
  - "red line can help communication can be visible!"
  - "full screen > cut it to red box ... so 2 png in same file!"
  - full screen -> C:\\projects\\agent_system\\evidence
  - red box     -> C:\\projects\\agent_system\\evidence_redbox

So each evidence produces TWO images, and they live together:

  evidence/<EVID>/redbox.png      FULL SCREEN, red box drawn on it   (context)
  evidence_redbox/<EVID>.png      CUT to the red box, red marks kept (target)

THE 4 LINES ARE THE POINT
-------------------------
The box is formed by four FULL-SPAN red lines (2 vertical, 2 horizontal). A plain
rectangle only shows where the box is; a full-span line can be followed across
the image, so a reader can see whether the line really lands on the row boundary.
That is exactly what `evidence_overlay.draw_guide_box()` already draws, so this
module REUSES it rather than writing a second renderer that could drift.

FAIL-CLOSED RULE (the important one)
------------------------------------
A red box asserts "the target is HERE". Drawing one at a guessed location would be
a confident false claim — worse than no box, because it looks authoritative. So a
folder with NO recorded rect is recorded as UNKNOWN with a reason, and gets no
box. Measured 2026-09-20: 19 of 70 evidence folders carry a rect; the other ~51
hold a screenshot with no measured target anywhere.

Read/write scope: reads screenshots + classify.json, writes ONLY under
evidence/ and evidence_redbox/. Never touches another module's state.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import evidence_overlay as eo        # noqa: E402  (the existing red-line renderer)
import evidence_store as es          # noqa: E402

# Where the cropped red-box images go (user spec).
REDBOX_ROOT = BASE_DIR / "evidence_redbox"

# STEP 6 evidence root (user spec 2026-09-20): the FINAL artefact carries the 4
# red lines + the red box + a RED CROSS at the computed centre. It lives in its
# own root so "proven with a cross" is a directory fact, not a naming convention
# someone has to remember.
REDBOX_ROOT_FINAL = BASE_DIR / "evidence_final"
CROSS_NAME = "redcross.png"
CROSS_VL_NAME = "redcross_vl.png"

# Name of the full-screen red-box render, stored INSIDE the evidence folder so
# the pair is "in the same file" (same place) as the user asked.
REDBOX_NAME = "redbox.png"
# Text-free variant for the VL. Kept separate because of a MEASURED finding: with
# any text drawn on the image the 7B-VL reads the LABEL as the box contents and
# answers NO on a perfectly correct box (evidence_overlay.py, 2026-09-19).
REDBOX_VL_NAME = "redbox_vl.png"

# Context margin around the cropped box. NOT zero: a tight cut gives no way to
# see whether the box is clipping the wrong row, which is the failure the red box
# exists to expose.
CROP_MARGIN = 40

MANIFEST_JSON = "manifest.json"
MANIFEST_MD = "manifest.md"

# EVID-<target_id>-<YYYYmmdd-HHMMSS>[-n]
EVID_RE = re.compile(r"^EVID-(?P<tid>.+?)-(?P<ts>\d{8}-\d{6})(?:-\d+)?$")


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_evidence_id(name: str) -> str:
    """EVID folder name -> target_id, or '' when it does not match the format."""
    m = EVID_RE.match(str(name or "").strip())
    return m.group("tid") if m else ""


# ---------------------------------------------------------------------------
# rect resolution — NEVER guesses
# ---------------------------------------------------------------------------

def _rect_from_classify(evid_dir: Path) -> tuple[tuple[int, int, int, int] | None, str]:
    p = evid_dir / es.FILES.get("classify", "classify.json")
    if not p.is_file():
        return None, "no classify.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return None, "classify.json unreadable: %s" % e
    r = d.get("rect_real") or {}
    try:
        rect = (int(r["x1"]), int(r["y1"]), int(r["x2"]), int(r["y2"]))
    except Exception:
        return None, "classify.json has no usable rect_real"
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None, "classify.json rect_real is degenerate %s" % (rect,)
    return rect, "classify.json rect_real"


def _rect_from_target_area(evid_name: str) -> tuple[tuple[int, int, int, int] | None, str]:
    """Look the rect up in coords.db target_area by the target id in the EVID name.

    The popup_id is NOT recorded in the folder name, so every popup is searched
    for a matching target_id. A target id that matches nothing is UNKNOWN, not a
    fallback to some other row.
    """
    tid = parse_evidence_id(evid_name)
    if not tid:
        return None, "EVID name does not carry a target id"
    try:
        import coord_store
    except Exception as e:
        return None, "coord_store unavailable: %s" % e
    try:
        rows = coord_store.list_target_areas(active_only=False)
    except Exception as e:
        return None, "target_area read failed: %s" % e
    for r in rows:
        if str(r.get("target_id") or "") != tid:
            continue
        try:
            rect = (int(r["x1"]), int(r["y1"]), int(r["x2"]), int(r["y2"]))
        except Exception:
            continue
        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue
        return rect, "target_area (popup_id=%s)" % r.get("popup_id")
    return None, "no target_area row for target_id=%r" % tid


def resolve_rect(evid_dir: Path) -> tuple[tuple[int, int, int, int] | None, str]:
    """The rect for one evidence folder, with the reason it was chosen.

    Order: the recorded classify rect first (it is what the verdict was judged
    against), then the measured target_area row. Returns (None, reason) when no
    rect exists — the caller must record UNKNOWN and draw nothing.
    """
    rect, why = _rect_from_classify(evid_dir)
    if rect:
        return rect, why
    rect2, why2 = _rect_from_target_area(evid_dir.name)
    if rect2:
        return rect2, "classify.json unusable (%s); %s" % (why, why2)
    return None, why if why != "no classify.json" else why2


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def _open_shot(evid_dir: Path):
    p = evid_dir / es.FILES.get("shot", "shot.png")
    if not p.is_file():
        return None
    from PIL import Image
    try:
        return Image.open(str(p)).convert("RGB")
    except Exception:
        return None


def _clamp_rect(rect: tuple[int, int, int, int], w: int, h: int):
    """Clamp to the image and report whether anything was left.

    An OUT-OF-BOUNDS rect is not silently trimmed into a plausible-looking box:
    the caller is told, because a clamped box is a DIFFERENT box from the one that
    was measured, and pretending otherwise is the false-claim failure again.
    """
    x1, y1, x2, y2 = rect
    cx1, cy1 = max(0, min(w - 1, x1)), max(0, min(h - 1, y1))
    cx2, cy2 = max(0, min(w - 1, x2)), max(0, min(h - 1, y2))
    clamped = (cx1, cy1, cx2, cy2) != rect
    if cx2 - cx1 < 2 or cy2 - cy1 < 2:
        return None, clamped, "rect collapses to %dx%d inside a %dx%d image" % (
            cx2 - cx1, cy2 - cy1, w, h)
    return (cx1, cy1, cx2, cy2), clamped, ""


def _red_pixels(img) -> int:
    """Count strong red pixels — proves the red lines actually rendered.

    A verdict of "the box was drawn" must not rest on the code having run; this
    measures the artefact itself.
    """
    getter = getattr(img, "get_flattened_data", None)
    data = getter() if getter else img.getdata()
    return sum(1 for p in data if p[0] > 200 and p[1] < 90 and p[2] < 90)


def render_pair(evid_dir: Path, *, margin: int = CROP_MARGIN,
                write_vl: bool = True) -> dict[str, Any]:
    """Render the 2 red-box PNGs for one evidence folder. Never raises.

    Returns {ok, rect, source, full_path, crop_path, vl_path, red_px, error}.
    ok=False means NO box was drawn and the reason is in `error` — the caller
    records UNKNOWN, it must never treat a missing box as a pass.
    """
    out: dict[str, Any] = {
        "evidence_id": evid_dir.name, "ok": False, "rect": None, "source": "",
        "full_path": None, "crop_path": None, "vl_path": None,
        "red_px": 0, "clamped": False, "error": None,
    }
    img = _open_shot(evid_dir)
    if img is None:
        out["error"] = "shot.png missing or unreadable"
        return out

    rect, why = resolve_rect(evid_dir)
    out["source"] = why
    if not rect:
        out["error"] = why
        return out

    w, h = img.size
    crect, clamped, err = _clamp_rect(rect, w, h)
    out["clamped"] = clamped
    if not crect:
        out["error"] = err
        return out
    out["rect"] = list(crect)

    label = "target %s  rect %s" % (parse_evidence_id(evid_dir.name) or "?",
                                    crect)

    try:
        # (1) FULL SCREEN with the red box — the user's "full screen" artefact.
        #     Full-span lines are drawn by draw_guide_box(); they are what makes
        #     the box checkable by eye.
        full = eo.draw_guide_box(img, *crect, label=label, annotate=True)
        full_path = evid_dir / REDBOX_NAME
        full.save(str(full_path))

        # (2) CUT to the red box, red marks KEPT VISIBLE (user: "red line can
        #     help communication, can be visible"). The lines are re-drawn with
        #     the box's own offsets so they still span the crop and still meet at
        #     the box corners.
        x1, y1, x2, y2 = crect
        cx1, cy1 = max(0, x1 - margin), max(0, y1 - margin)
        cx2, cy2 = min(w, x2 + margin), min(h, y2 + margin)
        crop = img.crop((cx1, cy1, cx2, cy2))
        cut = eo.draw_guide_box(crop, x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1,
                               label=label, annotate=True)
        REDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        crop_path = REDBOX_ROOT / ("%s.png" % evid_dir.name)
        cut.save(str(crop_path))

        vl_path = None
        if write_vl:
            # Text-free variant: the middleware asks the VL "does the text inside
            # the RED BOX read X?" and an on-image label competes with the answer.
            vimg = eo.draw_guide_box(crop, x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1,
                                     annotate=False)
            vl_path = REDBOX_ROOT / ("%s_vl.png" % evid_dir.name)
            vimg.save(str(vl_path))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out

    # PROVE the marks are in the artefact, not merely that the code ran.
    red_full = _red_pixels(full)
    red_cut = _red_pixels(cut)
    out["red_px"] = red_cut
    if red_cut <= 0 or red_full <= 0:
        out["error"] = ("no red pixels found after drawing "
                        "(full=%d crop=%d) — the box did not render"
                        % (red_full, red_cut))
        return out

    out.update({"ok": True, "full_path": str(full_path),
                "crop_path": str(crop_path),
                "vl_path": str(vl_path) if vl_path else None})
    return out


# ---------------------------------------------------------------------------
# backfill + manifest
# ---------------------------------------------------------------------------

def list_evidence_dirs(root: Path | None = None) -> list[Path]:
    r = Path(root) if root else es.EVIDENCE_ROOT
    if not r.is_dir():
        return []
    return sorted([d for d in r.iterdir() if d.is_dir() and d.name.startswith("EVID-")])


def backfill(root: Path | None = None, *, margin: int = CROP_MARGIN,
             write_vl: bool = True) -> dict[str, Any]:
    """Render every evidence folder and write the manifest.

    EVERY folder gets a manifest row. A folder with no rect gets a row saying
    UNKNOWN and why — that is what makes "all PNG provided" a checkable claim
    instead of a folder listing someone has to eyeball.
    """
    dirs = list_evidence_dirs(root)
    rows: list[dict[str, Any]] = []
    for d in dirs:
        r = render_pair(d, margin=margin, write_vl=write_vl)
        r["status"] = "OK" if r["ok"] else "UNKNOWN"
        rows.append(r)
        if r["ok"]:
            log("redbox %s -> OK rect=%s red_px=%d" % (d.name, r["rect"], r["red_px"]))
        else:
            log("redbox %s -> UNKNOWN (%s)" % (d.name, r["error"]))

    ok = [r for r in rows if r["ok"]]
    unknown = [r for r in rows if not r["ok"]]
    man = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "evidence_root": str(Path(root) if root else es.EVIDENCE_ROOT),
        "redbox_root": str(REDBOX_ROOT),
        "total": len(rows),
        "ok": len(ok),
        "unknown": len(unknown),
        "rows": rows,
    }
    manifest = REDBOX_ROOT / MANIFEST_JSON
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(man, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    (REDBOX_ROOT / MANIFEST_MD).write_text(build_manifest_md(man), encoding="utf-8")
    return man


def build_manifest_md(man: dict[str, Any]) -> str:
    L = ["# Evidence red box manifest", "",
         "Generated: `%s`" % man["generated_at"], "",
         "| total | red box drawn | UNKNOWN |", "|---|---|---|",
         "| %d | %d | %d |" % (man["total"], man["ok"], man["unknown"]), "",
         "A red box is drawn ONLY where a rect was recorded. UNKNOWN rows have no",
         "box on purpose: a box at a guessed place would be a confident false claim.", "",
         "| evidence id | status | rect | rect source | red px | redbox (full) | cut |",
         "|---|---|---|---|---|---|---|"]
    for r in man["rows"]:
        L.append("| `%s` | %s | %s | %s | %d | %s | %s |" % (
            r["evidence_id"], r["status"],
            r["rect"] if r["rect"] else "-",
            (r["source"] or "-")[:70],
            r["red_px"],
            "`redbox.png`" if r["full_path"] else "-",
            "`%s`" % Path(r["crop_path"]).name if r["crop_path"] else "-",
        ))
    unk = [r for r in man["rows"] if not r["ok"]]
    if unk:
        L += ["", "## UNKNOWN — no rect, so no box was drawn", "",
              "| evidence id | reason |", "|---|---|"]
        for r in unk:
            L.append("| `%s` | %s |" % (r["evidence_id"], (r["error"] or "")[:120]))
    return "\n".join(L) + "\n"


def verify() -> dict[str, Any]:
    """Acceptance test for "all png provided".

    Every evidence folder must have EITHER a rendered box (full + cut, both with
    red pixels) OR a recorded UNKNOWN reason. A folder that is silently missing
    from the manifest fails, because silence is indistinguishable from success.
    """
    dirs = list_evidence_dirs()
    man = json.loads((REDBOX_ROOT / MANIFEST_JSON).read_text(encoding="utf-8")) \
        if (REDBOX_ROOT / MANIFEST_JSON).is_file() else {"rows": []}
    have = {r["evidence_id"]: r for r in man.get("rows", [])}
    missing: list[str] = []
    silent: list[str] = []
    bad: list[str] = []
    for d in dirs:
        r = have.get(d.name)
        if r is None:
            missing.append(d.name)
            continue
        if r.get("ok"):
            for k in ("full_path", "crop_path"):
                p = r.get(k)
                if not p or not Path(p).is_file():
                    bad.append("%s: %s not on disk" % (d.name, k))
            if not r.get("red_px"):
                bad.append("%s: no red pixels" % d.name)
        else:
            if not (r.get("error") or "").strip():
                silent.append(d.name)
    return {"total": len(dirs), "manifest_rows": len(have),
            "missing_from_manifest": missing, "unknown_without_reason": silent,
            "artefact_problems": bad,
            "ok": not (missing or silent or bad)}


def pair(evid: str) -> dict[str, Any]:
    """The 2 images to hand to the LLM photo-QC middleware, plus a VL-safe file."""
    d = _evid_dir(evid)
    return {"evidence_id": evid,
            "full_screen_with_red_box": str(d / REDBOX_NAME),
            "cut_to_red_box": str(REDBOX_ROOT / ("%s.png" % evid)),
            "cut_to_red_box_vl": str(REDBOX_ROOT / ("%s_vl.png" % evid))}


# ---------------------------------------------------------------------------
# target mode — box a MEASURED target_area, not an evidence folder
# ---------------------------------------------------------------------------

def render_target(target_id: str, image, *, out_name: str | None = None,
                  margin: int = CROP_MARGIN) -> dict[str, Any]:
    """Draw the red box for a MEASURED target_area onto `image` (path or PIL Image).

    WHY: a target can be measured and proven in coords.db while no evidence
    folder was ever opened for it — measured 2026-09-20 for the 4 `doubao_*`
    targets, which had proven rects but no evidence PNG at all. This lets those
    targets produce the same 2-PNG pair without inventing a rect: the rect comes
    from the DB, and a target with no row is refused.

    Returns the same shape as render_pair, plus the cut image path.
    """
    from PIL import Image

    out: dict[str, Any] = {
        "evidence_id": out_name or target_id, "target_id": target_id, "ok": False,
        "rect": None, "source": "", "full_path": None, "crop_path": None,
        "vl_path": None, "red_px": 0, "clamped": False, "error": None,
    }
    try:
        import coord_store
    except Exception as e:
        out["error"] = "coord_store unavailable: %s" % e
        return out

    rows = coord_store.list_target_areas(active_only=False)
    row = next((r for r in rows if str(r.get("target_id")) == target_id), None)
    if not row:
        out["error"] = "no target_area row for %r — refusing to invent a rect" % target_id
        return out
    rect = (int(row["x1"]), int(row["y1"]), int(row["x2"]), int(row["y2"]))
    if rect[2] <= rect[0] or rect[3] <= rect[1]:
        out["error"] = "target_area row %r is degenerate %s" % (target_id, rect)
        return out
    out["source"] = "target_area (popup_id=%s)" % row.get("popup_id")
    out["rect"] = list(rect)

    if hasattr(image, "size") and hasattr(image, "convert"):
        img = image.convert("RGB")
    else:
        try:
            img = Image.open(str(image)).convert("RGB")
        except Exception as e:
            out["error"] = "cannot open image: %s" % e
            return out

    w, h = img.size
    crect, clamped, err = _clamp_rect(rect, w, h)
    out["clamped"] = clamped
    if not crect:
        out["error"] = err
        return out

    label = "target %s  rect %s" % (target_id, crect)
    try:
        full = eo.draw_guide_box(img, *crect, label=label, annotate=True)
        x1, y1, x2, y2 = crect
        cx1, cy1 = max(0, x1 - margin), max(0, y1 - margin)
        cx2, cy2 = min(w, x2 + margin), min(h, y2 + margin)
        crop = img.crop((cx1, cy1, cx2, cy2))
        cut = eo.draw_guide_box(crop, x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1,
                                label=label, annotate=True)
        vl = eo.draw_guide_box(crop, x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1,
                               annotate=False)
        REDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        name = out_name or target_id
        full_path = REDBOX_ROOT / ("%s_full.png" % name)
        crop_path = REDBOX_ROOT / ("%s_box.png" % name)
        vl_path = REDBOX_ROOT / ("%s_box_vl.png" % name)
        full.save(str(full_path))
        cut.save(str(crop_path))
        vl.save(str(vl_path))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out

    out["red_px"] = _red_pixels(cut)
    if out["red_px"] <= 0 or _red_pixels(full) <= 0:
        out["error"] = "no red pixels after drawing — the box did not render"
        return out
    out.update({"ok": True, "full_path": str(full_path),
                "crop_path": str(crop_path), "vl_path": str(vl_path)})
    return out


def capture_screen():
    """Full-screen capture as a PIL Image (the base for target-mode boxes)."""
    import pyautogui

    pyautogui.FAILSAFE = False
    return pyautogui.screenshot().convert("RGB")


# ---------------------------------------------------------------------------
# the RED CROSS (STEP 6): 4 red lines + red box + cross at the computed centre
# ---------------------------------------------------------------------------

def draw_cross(img, x1: int, y1: int, x2: int, y2: int, *,
               annotate: bool = True, span_lines: bool = True):
    """The 4 full-span lines + the box + a RED CROSS at the EXACT centre.

    The cross is drawn from the rect's OWN midpoint, never from a stored cx/cy,
    so the image cannot show a cross that disagrees with the box. That is the
    same principle the target_position CHECKs enforce in the database: the derived
    value has ONE source.

    `span_lines=False` omits the full-span guide lines, leaving ONLY the box and
    the cross. That variant exists because of a MEASURED failure (2026-09-20): with
    the full-span lines present, the four lines themselves form a giant plus that
    extends OUTSIDE the box, so the 7B-VL answered "the red cross is outside the
    red box" on a correct artefact — it could not tell which mark was the cross.
    The VL render is minimal; the HUMAN render keeps the lines (they are what make
    the boundary checkable by eye).

    A plus must also LOOK like a plus: the arms are SYMMETRIC, and their LENGTH is
    `min(inner_w, inner_h) // 3` (see the comment inside — the LENGTH is what fixes
    the clearance to the red lines, so the rule is reusable for every box size).
    Measured on a 400x34 box, per-axis quarter arms produced a 100x8 mark — a flat
    line with a nub — and the VL reported "no red cross".
    """
    from PIL import ImageDraw

    import evidence_overlay as eo

    if span_lines:
        out = eo.draw_guide_box(
            img, x1, y1, x2, y2,
            label=("target rect (%d,%d)-(%d,%d)  centre (%d,%d)"
                   % (x1, y1, x2, y2, (x1 + x2) // 2, (y1 + y2) // 2))
            if annotate else "",
            annotate=annotate,
        )
    else:
        out = img.convert("RGB").copy()
        d0 = ImageDraw.Draw(out)
        RED0 = (255, 0, 0)
        d0.rectangle([x1, y1, x2, y2], outline=RED0, width=3)
        if annotate:
            d0.text((x1 + 4, max(0, y1 - 12)),
                    "target rect (%d,%d)-(%d,%d)" % (x1, y1, x2, y2), fill=RED0)
    d = ImageDraw.Draw(out)
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    inner_w = max(4, (x2 - x1) - 4)
    inner_h = max(4, (y2 - y1) - 4)
    # CROSS LENGTH = min(inner_w, inner_h) / 3   (user rule, 2026-09-20)
    # so arm = LENGTH / 2 = min/6.
    #
    # WHY THE LENGTH AND NOT THE ARM: the arm is what the eye reads, but the LENGTH
    # is what determines clearance — the cross spans `LENGTH`, so its gap to each
    # red line is (inner/2 - LENGTH/2). Sizing the LENGTH at min/3 therefore fixes
    # the gap at min/3 of the short side, for EVERY box size, which is what makes it
    # reusable. Sizing the ARM at min/3 (the earlier code) makes the LENGTH min*2/3
    # and leaves only min/6 of clearance — half as much.
    #
    # MEASURED (x-plain/_sweep_cross_arm.py, real artefacts + verify_cross_inside):
    #   arm = min/3 (old)  -> clearance 17% of the short side; a 30x20 box FAILED
    #                         containment ("cross bbox ... NOT inside")
    #   LENGTH = min/3     -> clearance 33% of the short side; every box down to
    #                         20x20 PASSES and the cross never touches a red line.
    #
    # The floor is applied to the LENGTH, not the arm, so the shape stays a
    # recognisable plus rather than a 4px nub.
    cross_len = max(4, min(inner_w, inner_h) // 3)
    ax = ay = cross_len // 2
    RED = (255, 0, 0)
    # drawn on top of the guide lines and thicker than them, so it reads as a
    # distinct mark rather than another border
    d.line([(cx - ax, cy), (cx + ax, cy)], fill=RED, width=4)
    d.line([(cx, cy - ay), (cx, cy + ay)], fill=RED, width=4)
    return out


def cross_length_for(x2: int, x1: int, y2: int, y1: int) -> int:
    """The cross LENGTH for a rect, so callers/tests share ONE formula.

    LENGTH = max(4, min(inner_w, inner_h) // 3). Kept public (and separate from
    `draw_cross`) because a proof and the renderer must not be able to disagree:
    two copies of a formula is how a check stays green while the artefact is wrong.

    GEOMETRIC LIMIT, stated honestly: the 4px floor means the rule guarantees
    non-contact only while the short interior is >= 24px. Below that a cross can be
    visible OR clear, not both — such a box is not capturable, and the gate FAILS it
    (with the measured reason) rather than silently accepting a touching cross.
    """
    inner_w = max(4, int(x2) - int(x1) - 4)
    inner_h = max(4, int(y2) - int(y1) - 4)
    return max(4, min(inner_w, inner_h) // 3)


def render_redcross(evid_dir: Path, *, margin: int = CROP_MARGIN,
                    out_name: str | None = None) -> dict[str, Any]:
    """STEP 6 artefact: source shot + 4 red lines + red box + red cross.

    Writes to `evidence_final/` (the STEP 6 root). Same fail-closed rule as
    render_pair(): no recorded rect means NO image is drawn and UNKNOWN is
    returned. A cross at a guessed location would be a confident false claim
    about where the centre is — the exact defect this project exists to remove.
    """
    out: dict[str, Any] = {
        "ok": False, "rect": None, "centre": None, "source": "",
        "full_path": None, "crop_path": None, "vl_path": None,
        "red_px": 0, "error": None,
    }
    img = _open_shot(evid_dir)
    if img is None:
        out["error"] = "shot.png missing or unreadable"
        return out
    rect, why = resolve_rect(evid_dir)
    out["source"] = why
    if not rect:
        out["error"] = why
        return out
    w, h = img.size
    crect, clamped, err = _clamp_rect(rect, w, h)
    if not crect:
        out["error"] = err
        return out
    if clamped:
        # A clamped rect is a DIFFERENT rect from the measured one, so the cross
        # would mark a centre nobody measured. Refuse rather than draw it.
        out["error"] = ("rect %s is outside the %dx%d image — refusing to draw a "
                        "cross at a clamped centre" % (list(rect), w, h))
        return out
    x1, y1, x2, y2 = crect
    out["rect"] = list(crect)
    out["centre"] = [(x1 + x2) // 2, (y1 + y2) // 2]
    # The crop is offset by `margin`, so the rect must be re-expressed in the
    # CROP's own pixel space before it can be used to measure containment. The
    # gate needs this; screen coords would check the wrong rectangle.
    out["crop_offset"] = [max(0, x1 - margin), max(0, y1 - margin)]
    out["box_in_crop"] = [margin, margin,
                          margin + (x2 - x1), margin + (y2 - y1)]
    name = out_name or evid_dir.name
    try:
        # HUMAN render keeps the full-span guide lines (they make the boundary
        # checkable by eye). The VL render drops them, because measured
        # 2026-09-20 the four lines form a giant plus outside the box and the
        # model then reports "the cross is outside the box" on a correct artefact.
        full = draw_cross(img, x1, y1, x2, y2, annotate=True, span_lines=True)
        cx1, cy1 = max(0, x1 - margin), max(0, y1 - margin)
        cx2, cy2 = min(w, x2 + margin), min(h, y2 + margin)
        crop = img.crop((cx1, cy1, cx2, cy2))
        cut = draw_cross(crop, x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1,
                         annotate=True, span_lines=True)
        vl = draw_cross(crop, x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1,
                        annotate=False, span_lines=False)
        REDBOX_ROOT_FINAL.mkdir(parents=True, exist_ok=True)
        full_path = REDBOX_ROOT_FINAL / ("%s_%s" % (name, CROSS_NAME))
        crop_path = REDBOX_ROOT_FINAL / ("%s_full.png" % name)
        vl_path = REDBOX_ROOT_FINAL / ("%s_%s" % (name, CROSS_VL_NAME))
        full.save(str(full_path))
        cut.save(str(crop_path))
        vl.save(str(vl_path))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    out["red_px"] = _red_pixels(cut)
    if out["red_px"] <= 0:
        out["error"] = "no red pixels after drawing — the cross did not render"
        return out
    out.update({"ok": True, "full_path": str(full_path),
                "crop_path": str(crop_path), "vl_path": str(vl_path)})
    return out


PROMPT_RED_BOX = (
    "Look at this image. Is a RED BOX (a red rectangle) drawn on it?\n"
    "Answer NO if you cannot see a red rectangle.\n"
    "Output exactly two lines:\n"
    "Result: [YES / NO]\n"
    "Reason: one short sentence.\n"
)

def verify_cross_inside(image_path: str | Path,
                        box: tuple[int, int, int, int],
                        *, margin: int = 4,
                        min_px: int = 12) -> dict[str, Any]:
    """DETERMINISTIC: are the cross's red pixels strictly inside the box?

    WHY NOT THE VL ALONE
    --------------------
    Containment is ARITHMETIC. Measured 2026-09-20 on a correct artefact, the
    7B-VL answered "the plus sign touches / overlaps the rectangle's outline"
    three times in a row while the cross sat 6px clear of a 3px border inside a
    34px-tall box. A model that cannot measure pixels is the wrong instrument for
    a geometric fact, and repeatedly rewording the prompt to chase a YES would
    only refit the question to one model on one image.

    So containment is MEASURED: the box's border is red, so "red pixels strictly
    inside the box interior" can only be the cross (the VL render deliberately
    omits the full-span lines). A cross-free render has ZERO such pixels, which is
    what makes this check discriminate rather than merely agree.

    LIMIT, stated honestly: this counts red pixels inside the interior, so red
    CONTENT in the source screenshot (e.g. a red diff marker) also contributes and
    the reported bbox can span more than the cross. It is therefore a containment
    + "did red increase" check rather than an isolated measurement of the cross.
    That is why the gate ALSO requires the VL's independent "is there a plus sign"
    answer, and why `min_px` is a floor rather than a precise size.

    Returns {ok, red_inside, interior, bbox, detail}.
    """
    from PIL import Image

    try:
        img = Image.open(str(image_path)).convert("RGB")
    except Exception as e:
        return {"ok": False, "error": "cannot open image: %s" % e,
                "red_inside": 0, "interior": None, "bbox": None}
    w, h = img.size
    x1, y1, x2, y2 = box
    ix1, iy1 = x1 + margin, y1 + margin
    ix2, iy2 = x2 - margin, y2 - margin
    # Clamp to the image, but REFUSE when the interior vanishes: a box too small
    # to contain a margin cannot demonstrate containment at all.
    if ix2 - ix1 < 3 or iy2 - iy1 < 3:
        return {"ok": False, "red_inside": 0, "interior": [ix1, iy1, ix2, iy2],
                "bbox": None,
                "error": "box %s has no interior at margin %d (too small)"
                         % (list(box), margin)}
    ix1, iy1 = max(0, ix1), max(0, iy1)
    ix2, iy2 = min(w, ix2), min(h, iy2)
    crop = img.crop((ix1, iy1, ix2, iy2))
    getter = getattr(crop, "get_flattened_data", None)
    data = getter() if getter else crop.getdata()
    cw = crop.size[0]
    n = 0
    minx = miny = 10 ** 9
    maxx = maxy = -1
    for i, p in enumerate(data):
        if p[0] > 200 and p[1] < 90 and p[2] < 90:
            n += 1
            px, py = i % cw, i // cw
            minx, maxx = min(minx, px), max(maxx, px)
            miny, maxy = min(miny, py), max(maxy, py)
    bbox = None if n == 0 else [minx, miny, maxx, maxy]
    # A cross must fit ENTIRELY within the interior, so its bbox may not touch the
    # interior's own edge. +1 tolerance for antialiasing on the outermost stroke.
    bbox_ok = bool(
        bbox and bbox[0] > 0 and bbox[1] > 0
        and bbox[2] < (ix2 - ix1) - 1 and bbox[3] < (iy2 - iy1) - 1
    )
    ok = n >= min_px and bbox_ok
    return {
        "ok": ok, "red_inside": n, "interior": [ix1, iy1, ix2, iy2],
        "bbox": bbox, "bbox_ok": bbox_ok, "min_px": min_px,
        "detail": ("%d red px inside the box, cross bbox %s %s"
                   % (n, bbox, "inside" if bbox_ok else "NOT inside/absent")),
    }


PROMPT_RED_CROSS = (
    "This image has a red rectangle outline. Inside the rectangle there may "
    "be a red PLUS SIGN (a cross, +) made of one horizontal stroke and one "
    "vertical stroke crossing at the middle.\n"
    "Look at the CENTRE of the rectangle. Is there a red + sign there?\n"
    "Output exactly two lines:\n"
    "Result: [YES / NO]\n"
    "Reason: one short sentence.\n"
)

PROMPT_CROSS_INSIDE = (
    "This image has a red rectangle outline and, at its CENTRE, a red PLUS SIGN "
    "(a cross, +) made of one horizontal stroke and one vertical stroke crossing "
    "each other.\n"
    "Question: is that plus sign ENTIRELY INSIDE the rectangle?\n"
    "Answer YES if the whole plus sign sits within the rectangle with clear "
    "space around it.\n"
    "Answer NO only if the plus sign overlaps, touches, or crosses the "
    "rectangle's outline, or is not visible at all.\n"
    "Output exactly two lines:\n"
    "Result: [YES / NO]\n"
    "Reason: one short sentence.\n"
)


def _evidence_root() -> Path:
    """The CURRENT evidence root, read at call time.

    WHY not a module constant: evidence_store exposes set_evidence_root() so a
    proof or test can redirect writes to a tmp dir. Capturing
    `es.EVIDENCE_ROOT` at import time would ignore that redirect, so a test would
    silently read and write the PRODUCTION evidence tree — the exact pollution
    the redirect exists to prevent.
    """
    return es.EVIDENCE_ROOT


def _evid_dir(evid: str) -> Path:
    return _evidence_root() / evid


# ---------------------------------------------------------------------------
# THE TWO-LEVEL LLM CONFIRM CONTRACT  (user spec 2026-09-20)
#
#   LEVEL 1  ENVIRONMENT PROOF  ->  evidence/
#   LEVEL 2  TARGET PROOF       ->  evidence_final/
#
# The two levels are DIFFERENT QUESTIONS about DIFFERENT directories, and that
# separation is the point:
#
#   LEVEL 1 asks "was the thing I am about to click on actually on screen, in a
#   normal window, with my measurement drawn over it?" It validates the ENVIRONMENT
#   and the MEASUREMENT. A wrong window, a blank shot, or a mis-measured rect all
#   die here — before any claim is made about the target.
#
#   LEVEL 2 asks "does the mark I computed land on the target?" It validates the
#   TARGET, using the red cross at the computed centre. A cross outside the box
#   proves the centre is wrong, which is the `perm_pill cy=652` defect class.
#
# WHY TWO LEVELS AND NOT ONE: a single gate cannot tell a wrong ENVIRONMENT from a
# wrong TARGET, so its failure says nothing actionable. Measured 2026-09-20: the
# earlier single-gate run returned a bare FAIL, and it took a separate diagnostic
# to learn the capture was fine and only the target question was malformed. Two
# levels make the failure name its own cause by construction.
#
# The directories ARE the contract: `evidence/` holds the environment proof and
# `evidence_final/` holds the target proof. A caller reading only one of them has
# only half a confirmation.
# ---------------------------------------------------------------------------
GATE_LEVELS = {
    1: {"level": 1, "name": "ENVIRONMENT PROOF", "root": "evidence",
        "question": "is the environment real, and is the measurement on it?"},
    2: {"level": 2, "name": "TARGET PROOF", "root": "evidence_final",
        "question": "does the computed mark land on the target?"},
}


def level_root(level: int) -> Path:
    """The directory LEVEL N's proof belongs in — the contract, expressed in code.

    A caller that wants to know WHERE a level's evidence lives should ask this
    rather than hard-code a path, so the contract has one definition.
    """
    meta = GATE_LEVELS.get(int(level))
    if not meta:
        raise ValueError("level must be 1 or 2, got %r" % (level,))
    return BASE_DIR / meta["root"]


def level_meta(level: int) -> dict[str, Any]:
    """The level's name + root + question, for stamping into a result."""
    meta = GATE_LEVELS.get(int(level))
    if not meta:
        raise ValueError("level must be 1 or 2, got %r" % (level,))
    return dict(meta, root_path=str(level_root(level)))


def qc_cross(image_path: str | Path, *,
             box: tuple[int, int, int, int] | None = None,
             verbose: bool = True) -> dict[str, Any]:
    """LEVEL 2 — TARGET PROOF (`evidence_final/`): red box + cross INSIDE it.

    TWO INSTRUMENTS, BOTH REQUIRED — the same pattern `evidence_classify` uses
    for edges + VL:

      * CONTAINMENT IS MEASURED (`verify_cross_inside`). It is arithmetic, and a
        7B-VL cannot do it reliably: measured 2026-09-20 it reported "touches the
        outline" on a cross sitting 6px clear. The measurement cannot hallucinate.
      * THE CROSS'S PRESENCE IS ASKED OF THE VL. Whether a mark looks like a plus
        is a perception question, which is what a VL is actually good at.

    Requiring both means neither can carry the verdict alone: pixels alone might
    see a stray red mark, the VL alone might accept a cross that is really the box
    corner. Any NO / UNKNOWN / missing measurement -> the gate FAILS, so an
    uncertain result is never a pass.

    `box` is the rect IN THE CROP'S OWN PIXEL SPACE. When omitted, only the VL
    questions run and the result is reported as UNKNOWN rather than PASS — a
    verdict without the measurement is not a verdict.
    """
    try:
        import evidence_classify as ec
    except Exception as e:
        return {"ok": False, "verdict": "UNKNOWN", "checks": {},
                "error": "evidence_classify unavailable: %s" % e}

    checks: dict[str, Any] = {}

    def _ask(tag: str, prompt: str) -> bool:
        r = ec.classify_yes_no(str(image_path), "see the prompt", prompt=prompt)
        checks[tag] = r.as_dict()
        ok = r.verdict == "PASS"
        if verbose:
            log("  gate %-18s %-4s %s" % (tag, r.answer, (r.reason or "")[:70]))
        return ok

    # Q1 first: if there is no box, judging the cross is meaningless — the answer
    # would be about the wrong thing, which is worse than no answer.
    if not _ask("q1_red_box", PROMPT_RED_BOX):
        return {"ok": False, "verdict": "UNKNOWN", "checks": checks,
                "error": "no red box visible — cannot judge the cross"}
    q2 = _ask("q2_red_cross", PROMPT_RED_CROSS)

    if box is None:
        return {"ok": False, "verdict": "UNKNOWN", "checks": checks,
                "error": ("box rect required to MEASURE containment — refusing to "
                          "pass on the VL's opinion alone")}

    inside = verify_cross_inside(image_path, box)
    checks["q3_cross_inside"] = inside
    if verbose:
        log("  gate %-18s %-4s %s" % ("q3_cross_inside",
                                      "YES" if inside["ok"] else "NO",
                                      str(inside.get("detail")
                                          or inside.get("error"))[:70]))
    # The deterministic measure is the authority on containment; the VL's answer
    # for that question is recorded as corroboration when it was asked.
    all_ok = bool(q2 and inside["ok"])
    failed = [k for k, v in checks.items() if not v.get("ok", v.get("verdict") == "PASS")]
    return {
        "ok": all_ok,
        "verdict": "PASS" if all_ok else "FAIL",
        "checks": checks,
        # LEVEL 2 stamp: a caller must never have to guess which level it read.
        **level_meta(2),
        "error": None if all_ok else ("LEVEL 2 TARGET PROOF not all-YES: "
                                      + ", ".join(failed)
                                      or (inside.get("error") or "unknown")),
    }


def qc_photo_evidence(evid: str, label: str | None = None) -> dict[str, Any]:
    """LEVEL 1 — ENVIRONMENT PROOF (`evidence/`): source, lines, box, input area.

    Four questions, ALL required (user spec 2026-09-20). Runs `build_overlay`
    first so the red box genuinely exists on the image being judged — asking a
    VL about a box that was never drawn is how a confident wrong answer happens.

    This level validates the ENVIRONMENT and the MEASUREMENT, not the target: a
    wrong window, a blank shot, or a rect over a menu all fail here, before any
    claim is made about what the target is.
    """
    d = _evid_dir(evid)
    out: dict[str, Any] = {"evidence_id": evid, "ok": False,
                           "verdict": "UNKNOWN", "checks": {}}
    if not (d / es.FILES.get("shot", "shot.png")).is_file():
        out["error"] = "shot.png missing — nothing to gate"
        return out
    rect, why = resolve_rect(d)
    if not rect:
        out["error"] = "no recorded rect (%s) — cannot form a red box" % why
        return out
    lab = label or _label_for(d)

    rp = render_pair(d)
    if not rp.get("ok"):
        out["error"] = "red box render failed: %s" % rp.get("error")
        return out
    vl = rp.get("vl_path") or rp.get("crop_path")
    try:
        import evidence_classify as ec
    except Exception as e:
        out["error"] = "evidence_classify unavailable: %s" % e
        return out
    checks: dict[str, Any] = {}
    # The FOUR questions, and why the FOURTH is not "is the target name inside".
    #
    # MEASURED FAILURE 2026-09-20: the fourth question used to ask whether the box
    # contained the TARGET NAME. The box outlines a chat input area, so its text is
    # the app's OWN placeholder ("发送消息..."), and the model answered NO with the
    # correct text quoted. That question can NEVER pass, which makes the whole gate
    # permanently red — a gate that always fails is not stricter, it is broken.
    #
    # So the fourth question asks a fact the box actually CONTAINS and that still
    # catches a wrong target: is there a text input area (and is it the place a
    # message is typed)? A mis-measured box over a sidebar or a menu would fail it.
    for tag, prompt in (
        ("q1_source_correct",
         "This is a screenshot of an application or web page. Is it a normal, "
         "readable window screenshot (not blank, not an error page, not a "
         "desktop with no window)?\nOutput exactly two lines:\n"
         "Result: [YES / NO]\nReason: one short sentence.\n"),
        ("q2_four_red_lines",
         "Look at this image. Are there FOUR red guide lines (two vertical, two "
         "horizontal) drawn across it?\nOutput exactly two lines:\n"
         "Result: [YES / NO]\nReason: one short sentence.\n"),
        ("q3_red_box", PROMPT_RED_BOX),
        ("q4_box_holds_input",
         "Look at the RED BOX. Does the region inside it contain a TEXT INPUT "
         "area — the place where a user types a message (it typically shows "
         "placeholder text such as \"send a message\", a blinking cursor, or a "
         "toolbar of compose icons)?\n"
         "Answer NO if the box covers a menu, a sidebar, a list of chats, a "
         "button, or any other non-input region.\n"
         "Output exactly two lines:\n"
         "Result: [YES / NO]\nReason: one short sentence.\n"),
    ):
        r = ec.classify_yes_no(str(vl), "see the prompt", prompt=prompt)
        checks[tag] = r.as_dict()
    out["checks"] = checks
    out["rect"] = list(rect)
    out["rect_source"] = why
    # `label` is kept in the result for the record (it names the target the operator
    # intended) but it is NO LONGER a gate question — see the note above.
    out["label"] = lab
    out["artefacts"] = {"full": rp["full_path"], "cut": rp["crop_path"]}
    failed = [k for k, v in checks.items() if v.get("verdict") != "PASS"]
    out["ok"] = not failed
    out["verdict"] = "PASS" if not failed else "FAIL"
    # LEVEL 1 stamp, so the two levels are distinguishable in any log or report.
    out.update(level_meta(1))
    if failed:
        out["error"] = "LEVEL 1 ENVIRONMENT PROOF not all-YES: " + ", ".join(failed)
    return out


# ---------------------------------------------------------------------------
# the LLM photo-QC middleware
# ---------------------------------------------------------------------------

def _label_for(evid_dir: Path, fallback: str = "") -> str:
    """The label the verdict was judged against, from classify.json."""
    p = evid_dir / es.FILES.get("classify", "classify.json")
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            lab = str(d.get("label") or "").strip()
            if lab:
                return lab
        except Exception:
            pass
    return fallback or parse_evidence_id(evid_dir.name) or "target"


def qc_photo(evid: str, label: str | None = None,
             out_path: str | Path | None = None) -> dict[str, Any]:
    """LLM PHOTO QC for one evidence: red box + 4 edge verdicts + 2 VL questions.

    This is the middleware the user asked for ("so LLM have middleware for photo
    QC now"). It hands the PAIR to the existing classifier rather than
    reimplementing QC:

        full screen  -> the context a human reads
        red box crop -> the region the VL is asked about

    The VL is given the TEXT-FREE render (evidence_classify documents why: any
    label drawn on the image competes with the answer and the model reports the
    label as the box contents, answering NO on a correct box).

    Returns evidence_classify's dict plus `pair` (the 2 image paths) and
    `redbox_ok`. A missing rect is returned as UNKNOWN with the reason — this
    function never fabricates a region to judge.
    """
    d = _evid_dir(evid)
    out: dict[str, Any] = {"evidence_id": evid, "pair": pair(evid),
                           "verdict": "UNKNOWN", "ok": False}

    if not (d / es.FILES.get("shot", "shot.png")).is_file():
        out["error"] = "shot.png missing — nothing to QC"
        return out

    rect, why = resolve_rect(d)
    if not rect:
        # No red box means no reference frame, and without a reference frame the
        # VL answer is not about anything in particular.
        out["error"] = "no recorded rect (%s) — cannot form a red box, refusing to QC" % why
        return out

    lab = label or _label_for(d)
    dest = Path(out_path) if out_path else (
        d / "qc_overlay.png")

    import evidence_classify as ec

    try:
        res = ec.classify_with_overlay(
            str(d / es.FILES.get("shot", "shot.png")),
            rect[0], rect[1], rect[2], rect[3], lab, str(dest),
        )
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out

    out.update(res)
    out["evidence_id"] = evid
    out["label"] = lab
    out["rect"] = list(rect)
    out["rect_source"] = why
    out["pair"] = pair(evid)
    out["ok"] = bool(res.get("ok"))
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true",
                    help="render a red box for every evidence folder + manifest")
    ap.add_argument("--one", metavar="EVID", help="render one evidence folder")
    ap.add_argument("--pair", metavar="EVID", help="print the 2-image QC pair")
    ap.add_argument("--qc", metavar="EVID",
                    help="LLM photo QC for one evidence: red box + VL verdicts")
    ap.add_argument("--verify", action="store_true",
                    help="assert every folder has a box or a recorded UNKNOWN")
    ap.add_argument("--targets", action="store_true",
                    help="red-box EVERY measured coords.db target against a live "
                         "full-screen capture (no evidence folder needed)")
    ap.add_argument("--manifest", action="store_true", help="print the manifest")
    ap.add_argument("--no-vl", action="store_true",
                    help="skip the text-free VL variant")
    ap.add_argument("--margin", type=int, default=CROP_MARGIN)
    a = ap.parse_args()

    if a.one:
        print(json.dumps(render_pair(es.EVIDENCE_ROOT / a.one, margin=a.margin,
                                     write_vl=not a.no_vl),
                         indent=2, ensure_ascii=False))
    elif a.backfill:
        m = backfill(margin=a.margin, write_vl=not a.no_vl)
        print("\n=== %d folders: %d with a red box, %d UNKNOWN ==="
              % (m["total"], m["ok"], m["unknown"]))
        print("manifest: %s" % (REDBOX_ROOT / MANIFEST_JSON))
    elif a.pair:
        print(json.dumps(pair(a.pair), indent=2))
    elif a.qc:
        r = qc_photo(a.qc)
        print(json.dumps({k: r.get(k) for k in
                          ("evidence_id", "label", "rect", "verdict", "ok",
                           "edges_all_pass", "error", "pair")},
                         indent=2, ensure_ascii=False))
        q = r.get("questions") or {}
        for k in ("q1_red_box_present", "q2_text_in_box"):
            if k in q:
                print("  %-22s %s  %s" % (k, q[k].get("answer"),
                                          (q[k].get("reason") or "")[:100]))
    elif a.verify:
        v = verify()
        print(json.dumps(v, indent=2, ensure_ascii=False))
        raise SystemExit(0 if v["ok"] else 1)
    elif a.targets:
        # One live full-screen capture as the base, then a red box per measured
        # target. The capture is shared so every box is on the SAME image — a
        # per-target capture would let the screen move between boxes and make the
        # set impossible to compare.
        import coord_store
        img = capture_screen()
        rows = coord_store.list_target_areas(active_only=False)
        n_ok = n_bad = 0
        for r in rows:
            tid = str(r.get("target_id"))
            res = render_target(tid, img, margin=a.margin)
            if res["ok"]:
                n_ok += 1
                print("target %-20s OK rect=%s red_px=%d"
                      % (tid, res["rect"], res["red_px"]))
            else:
                n_bad += 1
                print("target %-20s UNKNOWN (%s)" % (tid, res["error"]))
        print("\n=== %d measured targets: %d boxed, %d refused ==="
              % (len(rows), n_ok, n_bad))
    elif a.manifest:
        p = REDBOX_ROOT / MANIFEST_MD
        print(p.read_text(encoding="utf-8") if p.is_file() else "no manifest yet")
    else:
        ap.print_help()