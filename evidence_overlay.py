"""Evidence overlay — draw a red guide box and verify its edges.

WHY THIS EXISTS
---------------
A worker produces evidence (a screenshot) and must classify it PASS/FAIL.
Two problems make that hard:

1. A bare crop is ambiguous. "Does this crop show X?" invites a VL model to
   hallucinate, because the crop has no context and no reference frame.
2. A measured rectangle is not a proven rectangle. Storing X1,Y1,X2,Y2 does not
   mean the box actually sits on the target row.

The red guide box solves both at once:
  - HUMAN: full-span lines show at a glance whether the box cuts the row edge.
  - VL: "does the text inside the RED BOX read X?" is far less ambiguous than
    "does this crop show X?", which measurably cuts hallucination.

The full-height / full-width lines are the point. A plain rectangle only shows
the box; a full-span line shows whether that line actually lands on the
boundary, because you can follow it across the whole image.

EDGE VERDICT
------------
Each of the 4 edges is verified independently:
    X1 vertical, X2 vertical, Y1 horizontal, Y2 horizontal
    delta = |detected - candidate|
    PASS <= tol_pass, WARN <= tol_warn, else FAIL
    edge not found -> FAIL (fail-closed: never assume)

The "nudge right 5 / left 5" search happens in IMAGE space as a 1D scan. No real
mouse movement is involved, so it is fast, repeatable, and cannot disturb other
windows.

Read-only with respect to the system: this module only reads images and writes
the overlay PNG it is asked to write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Verdict thresholds in image pixels. A picker row is ~34px tall, so 3px is
# ~9% of the row — strict enough to catch a mis-measure, loose enough not to
# fail on antialiasing.
TOL_PASS = 3
TOL_WARN = 8

# Edge scan window: how far either side of the candidate to look.
SCAN_RADIUS = 24

# A +/-24px window presumes the target is at least ~48px across. Measured on a
# 30x30 icon: X1, X2, Y1 and Y2 ALL resolved to detected=7, because the window
# contained the glyph's left edge whichever edge was being tested. The rect could
# then never pass no matter how well it was placed.
#
# So the window is also capped to a fraction of the target's own size. This is a
# SEPARATE failure mode from a borderless row: there, no luminance step exists at
# all (delta=None); here a step exists but several compete, and the strongest one
# wins regardless of which edge asked.
SCAN_RADIUS_FRACTION = 3        # radius <= target_span // this
SCAN_RADIUS_MIN = 2

RED = (255, 0, 0)
RED_SOFT = (255, 80, 80)
LABEL_BG = (0, 0, 0)


@dataclass
class EdgeResult:
    edge: str            # "X1" | "X2" | "Y1" | "Y2"
    axis: str            # "vertical" | "horizontal"
    candidate: int       # the stored coordinate
    detected: int | None # the strongest edge found nearby, None if none
    delta: int | None    # |detected - candidate|
    verdict: str         # "PASS" | "WARN" | "FAIL"
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge": self.edge,
            "axis": self.axis,
            "candidate": self.candidate,
            "detected": self.detected,
            "delta": self.delta,
            "verdict": self.verdict,
            "note": self.note,
        }


@dataclass
class OverlayResult:
    ok: bool
    overlay_path: str | None = None      # annotated — for the HUMAN
    vl_path: str | None = None           # text-free — for the VL
    edges: list[EdgeResult] = field(default_factory=list)
    all_pass: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "overlay_path": self.overlay_path,
            "vl_path": self.vl_path,
            "edges": [e.as_dict() for e in self.edges],
            "all_pass": self.all_pass,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# edge detection
# ---------------------------------------------------------------------------

def _gray_rows_cols(img):
    """Return (cols, rows) as float lists of mean luminance.

    cols[x] = mean luminance of column x  -> used to find VERTICAL edges
    rows[y] = mean luminance of row y     -> used to find HORIZONTAL edges

    Single pass over the pixels: both accumulators are filled together.
    """
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    cols = [0.0] * w
    rows = [0.0] * h
    for y in range(h):
        row_acc = 0
        for x in range(w):
            v = px[x, y]
            row_acc += v
            cols[x] += v
        rows[y] = row_acc
    inv_w = 1.0 / float(w) if w else 0.0
    inv_h = 1.0 / float(h) if h else 0.0
    for x in range(w):
        cols[x] *= inv_h
    for y in range(h):
        rows[y] *= inv_w
    return cols, rows


def _strongest_edge(profile: list[float], candidate: int, radius: int = SCAN_RADIUS):
    """Find the strongest gradient in profile within +/- radius of candidate.

    This is the "nudge right 5 / left 5" search, done in image space: we look at
    every offset in the window and keep the one with the largest luminance step.
    Returns (index, magnitude) or (None, 0.0) when the window is flat.
    """
    n = len(profile)
    if n < 3:
        return None, 0.0
    lo = max(1, candidate - radius)
    hi = min(n - 2, candidate + radius)
    if hi <= lo:
        return None, 0.0
    best_i, best_m = None, 0.0
    for i in range(lo, hi + 1):
        m = abs(profile[i + 1] - profile[i - 1])
        if m > best_m:
            best_i, best_m = i, m
    return best_i, best_m


def _verdict(delta: int | None, tol_pass: int, tol_warn: int) -> str:
    if delta is None:
        return "FAIL"
    if delta <= tol_pass:
        return "PASS"
    if delta <= tol_warn:
        return "WARN"
    return "FAIL"


def scan_radius_for(span: int) -> int:
    """Largest sane scan radius for a target that is `span` px across.

    A fixed +/-24px window lets a small target's other edges leak into the one
    being tested. Measured on a 30x30 icon: every edge resolved to detected=7, so
    X2/Y2 reported delta 17-19 while X1/Y1 reported delta 1 — an unavoidable FAIL
    caused by the window, not by the rect.

    The radius is therefore capped to a third of the target's span, so the window
    cannot reach the opposite edge. Returns the smaller of SCAN_RADIUS and that
    cap, never below SCAN_RADIUS_MIN.
    """
    span = max(0, int(span))
    capped = span // SCAN_RADIUS_FRACTION
    return max(SCAN_RADIUS_MIN, min(SCAN_RADIUS, capped))


def verify_edges(
    img,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    tol_pass: int = TOL_PASS,
    tol_warn: int = TOL_WARN,
    min_edge: float = 6.0,
    scan_radius: int | None = None,
) -> list[EdgeResult]:
    """Verify the 4 edges of a rect against the image.

    `min_edge` is the minimum luminance step that counts as a real edge. Below
    it the window is considered flat -> FAIL, because a flat window means the
    line is not on any boundary (fail-closed, never assume).

    `scan_radius` defaults to None, meaning "derive it from the target size" via
    scan_radius_for(). Pass an explicit value only to override that deliberately.
    """
    cols, rows = _gray_rows_cols(img)
    out: list[EdgeResult] = []

    span_x, span_y = abs(x2 - x1), abs(y2 - y1)
    r_x = scan_radius if scan_radius is not None else scan_radius_for(span_x)
    r_y = scan_radius if scan_radius is not None else scan_radius_for(span_y)

    for name, axis, profile, cand, radius in (
        ("X1", "vertical", cols, x1, r_x),
        ("X2", "vertical", cols, x2, r_x),
        ("Y1", "horizontal", rows, y1, r_y),
        ("Y2", "horizontal", rows, y2, r_y),
    ):
        det, mag = _strongest_edge(profile, cand, radius=radius)
        if det is None or mag < min_edge:
            out.append(EdgeResult(
                edge=name, axis=axis, candidate=cand, detected=det, delta=None,
                verdict="FAIL",
                note=("no edge found within +/-%dpx (max step %.1f < %.1f) — "
                      "line is not on a boundary" % (radius, mag, min_edge)),
            ))
            continue
        delta = abs(det - cand)
        out.append(EdgeResult(
            edge=name, axis=axis, candidate=cand, detected=det, delta=delta,
            verdict=_verdict(delta, tol_pass, tol_warn),
            note="step=%.1f radius=%d" % (mag, radius),
        ))
    return out


# ---------------------------------------------------------------------------
# overlay drawing
# ---------------------------------------------------------------------------

def _rects_overlap(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2) -> bool:
    return not (ax2 <= bx1 or ax1 >= bx2 or ay2 <= by1 or ay1 >= by2)


def _place_label(w: int, h: int, cx1: int, cy1: int, cx2: int, cy2: int,
                 bw: int, bh: int) -> tuple[int, int]:
    """Pick a label position that does NOT overlap the box.

    CRITICAL: the label carries the edge verdict text. If it lands inside the
    red box, the VL model reads that text as the box contents and question 2
    ("does the text inside the red box read X?") answers NO for the right box.
    Caught by qc_evidence_classify.py --vl: the model replied
    'The text inside the red box reads "Default permissions OK X1..."'.

    Preference order: above, below, right, left. Falls back to the least
    overlapping candidate if the image is too small for any clean spot.
    """
    pad = 2
    candidates = [
        (cx1, cy1 - bh - pad),          # above the box
        (cx1, cy2 + pad),               # below the box
        (cx2 + pad, cy1),               # right of the box
        (cx1 - bw - pad, cy1),          # left of the box
    ]
    best = None
    best_overlap = None
    for bx, by in candidates:
        bx = max(0, min(w - bw, bx))
        by = max(0, min(h - bh, by))
        overlap = _rects_overlap(bx, by, bx + bw, by + bh, cx1, cy1, cx2, cy2)
        if not overlap:
            return bx, by
        # measure how bad the overlap is, to pick the least bad fallback
        ox1, oy1 = max(bx, cx1), max(by, cy1)
        ox2, oy2 = min(bx + bw, cx2), min(by + bh, cy2)
        area = max(0, ox2 - ox1) * max(0, oy2 - oy1)
        if best_overlap is None or area < best_overlap:
            best, best_overlap = (bx, by), area
    return best if best else (0, 0)


def annotate_evidence(img, lines, *, anchor=None, scale=1.0):
    """Draw a BLACK BAND with RED text stating what the picture proves.

    THE HUMAN (2026-09-26): "evidence image be image, user can understand why
    this is evidence, not have screenshot only".

    MEASURED: the coordinate evidence images already explain themselves
    (`evidence_final/EVID-vscode_taskbar_icon-..._full.png` carries
    `target rect (40,40)-(77,77) centre (58,58)`), while a STEP image was a BARE
    screenshot — a reader could not tell whether it proved a PASS, a FAIL, or
    that the step never ran.

    ONE IMPLEMENTATION. This is the band that `draw_guide_box` already drew
    inline; it is extracted here so the coordinate images and the step images
    cannot drift into two different label styles.

    `anchor` is the region of interest `(x1, y1, x2, y2)` the band must NOT
    cover; when omitted the band is placed at the top-left.

    `scale` multiplies the font and line height. MEASURED (2026-09-26): a band
    drawn at 1x on a 1280px image is legible, but the SAME band on a 240px
    thumbnail is a smear — so the caller scales it to the image it is drawing on.

    Returns a NEW image; the input is not modified.
    """
    from PIL import ImageDraw

    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    lines = [str(s) for s in (lines or []) if str(s or "").strip()]
    if not lines:
        return out
    sc = max(0.5, float(scale or 1.0))
    pad = max(2, int(4 * sc))
    lh = max(8, int(12 * sc))
    cw = max(3, int(6 * sc))
    bw = max(len(s) for s in lines) * cw + pad * 2
    bh = len(lines) * lh + pad * 2
    if anchor:
        bx, by = _place_label(w, h, int(anchor[0]), int(anchor[1]),
                              int(anchor[2]), int(anchor[3]), bw, bh)
    else:
        bx, by = 0, 0
    # CLAMP, so a band wider than the image is still fully visible.
    bx = max(0, min(max(0, w - bw), bx))
    by = max(0, min(max(0, h - bh), by))
    d.rectangle([bx, by, bx + bw, by + bh], fill=LABEL_BG)
    for i, s in enumerate(lines):
        d.text((bx + pad, by + pad + i * lh), s, fill=RED_SOFT)
    return out


def draw_guide_box(
    img,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    label: str = "",
    edges: list[EdgeResult] | None = None,
    annotate: bool = True,
):
    """Draw the red guide: full-span X1/X2 lines, full-span Y1/Y2 lines, box.

    Full-span lines are deliberate — a plain rectangle only shows the box, while
    a line running the whole image height lets a human follow it and see whether
    it truly cuts the row boundary.

    `annotate=False` produces the VL-SAFE image: lines + box, NO text at all.

    WHY THE TWO VARIANTS EXIST (measured 2026-09-19)
    ------------------------------------------------
    Any text drawn on the overlay becomes a candidate answer to question 2
    ("does the text inside the red box read X?"). With full-span lines the
    label sits in a red-bounded region, so a 7B-VL reports the LABEL text as the
    box contents and answers NO on a perfectly correct box. Measured:

        full-span + box + text label   -> NO   ("reads <label> OK X1 cand=...")
        full-span + box + NO label     -> YES  ("reads <label>")   <- correct

    So: the VL reads the text-free image, the human reads the annotated one.
    """
    from PIL import ImageDraw

    out = img.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    w, h = out.size

    def _clamp(v, hi):
        return max(0, min(hi - 1, int(v)))

    cx1, cy1 = _clamp(x1, w), _clamp(y1, h)
    cx2, cy2 = _clamp(x2, w), _clamp(y2, h)

    # --- z-order matters ---
    # The annotation is drawn FIRST and the guide lines LAST, so a line is never
    # occluded by the text. An earlier version drew the text last and its
    # fallback position landed on the X1 line, hiding 69 of 200 px — which
    # defeats the entire point of full-span lines.

    # 1) annotation (human variant only)
    if annotate:
        lines = []
        if label:
            lines.append(label)
        if edges:
            for e in edges:
                mark = {"PASS": "OK", "WARN": "~", "FAIL": "X"}.get(e.verdict, "?")
                det = "-" if e.detected is None else str(e.detected)
                dlt = "-" if e.delta is None else str(e.delta)
                lines.append("%s %s cand=%d det=%s d=%s"
                             % (mark, e.edge, e.candidate, det, dlt))
        if lines:
            # ONE IMPLEMENTATION of the band. `annotate_evidence` owns the
            # drawing, so the coordinate images and the step images cannot drift
            # into two different label styles.
            out = annotate_evidence(out, lines,
                                    anchor=(cx1, cy1, cx2, cy2))
            d = ImageDraw.Draw(out)

    # 2) full-span guide lines (on top of the annotation)
    d.line([(cx1, 0), (cx1, h)], fill=RED, width=1)
    d.line([(cx2, 0), (cx2, h)], fill=RED, width=1)
    d.line([(0, cy1), (w, cy1)], fill=RED, width=1)
    d.line([(0, cy2), (w, cy2)], fill=RED, width=1)

    # 3) the box itself — thick, so "the red box" is unambiguous
    d.rectangle([cx1, cy1, cx2, cy2], outline=RED, width=3)

    # 4) corner ticks so the box is readable even on a busy background
    tick = 8
    for (px, py, dx, dy) in (
        (cx1, cy1, 1, 1), (cx2, cy1, -1, 1), (cx1, cy2, 1, -1), (cx2, cy2, -1, -1),
    ):
        d.line([(px, py), (px + dx * tick, py)], fill=RED_SOFT, width=2)
        d.line([(px, py), (px, py + dy * tick)], fill=RED_SOFT, width=2)

    return out


def build_overlay(
    image_path: str | Path | Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    out_path: str | Path,
    *,
    label: str = "",
    tol_pass: int = TOL_PASS,
    tol_warn: int = TOL_WARN,
) -> OverlayResult:
    """Verify edges, draw the guide box, save the overlay. Never raises.

    `image_path` accepts EITHER a filesystem path OR an already-open PIL Image.
    Synthetic tests and callers that hold an in-memory frame should not be forced
    to write a temp file first — an earlier version only accepted a path and
    silently returned ok=False for an Image, which made every in-memory case
    look like a detection failure.
    """
    try:
        from PIL import Image
    except Exception as e:
        return OverlayResult(ok=False, error="PIL unavailable: %s" % e)

    img = None
    if hasattr(image_path, "size") and hasattr(image_path, "convert"):
        img = image_path                      # already a PIL Image
    else:
        try:
            img = Image.open(str(image_path))
        except Exception as e:
            return OverlayResult(ok=False, error="cannot open %s: %s" % (image_path, e))

    try:
        edges = verify_edges(img, x1, y1, x2, y2,
                             tol_pass=tol_pass, tol_warn=tol_warn)
        # TWO renders from the same rect:
        #   vl_path      text-free  -> the VL reads ONLY the box contents
        #   overlay_path annotated  -> the human reads the verdicts
        # See draw_guide_box() for the measurement that made this necessary.
        out_p = Path(out_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        vl_p = out_p.with_name(out_p.stem + "_vl" + out_p.suffix)

        draw_guide_box(img, x1, y1, x2, y2, annotate=False).save(str(vl_p))
        draw_guide_box(img, x1, y1, x2, y2, label=label, edges=edges,
                       annotate=True).save(str(out_p))
    except Exception as e:
        return OverlayResult(ok=False, error="%s: %s" % (type(e).__name__, e))

    return OverlayResult(
        ok=True,
        overlay_path=str(out_p),
        vl_path=str(vl_p),
        edges=edges,
        all_pass=all(e.verdict == "PASS" for e in edges),
    )


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 7:
        print("usage: evidence_overlay.py IMG X1 Y1 X2 Y2 OUT [label]")
        raise SystemExit(1)
    res = build_overlay(
        sys.argv[1],
        int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]),
        sys.argv[6],
        label=sys.argv[7] if len(sys.argv) > 7 else "",
    )
    print(json.dumps(res.as_dict(), indent=2, ensure_ascii=False))