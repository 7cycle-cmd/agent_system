# -*- coding: utf-8 -*-
"""seed_mouse_spot_yes_cases.py — add discriminating gold cases for mouse_spot_verify.

THE PROBLEM THIS FIXES
----------------------
`mouse_spot_verify` had 11 gold cases and **every one expected `NO`**. A prompt
that always answers "NO" scores 100%, so its recorded `pass_gate=1,
accuracy_pct=100.0` (run id=1) proves nothing — the skill was never shown to
distinguish a hit from a miss. The `_gold_set_gate()` in `skill_learning.py`
now refuses to merge on such a set, which is correct but leaves the skill
unmergeable until cases with the OPPOSITE expectation exist.

WHAT THIS ADDS
--------------
Cases composed from scratch: a flat canvas + the target icon pasted at a KNOWN
box + a red crosshair at a computed position. Ground truth is exact because the
composition defines it:

  HIT      crosshair inside the pasted icon box        -> expected YES
  NEAR     crosshair a few px outside the icon box     -> expected NO
  FAR      crosshair far from the icon                 -> expected NO

COMPOSE, DO NOT STAMP
---------------------
The first version stamped a crosshair onto the raw fixture. That was abandoned:
the fixtures are avatars inside a large blue circle, so the icon boundary cannot
be detected from the image (corner-colour detection degenerates to the whole
image) and the NEAR cases were labelled NO while geometrically sitting INSIDE
the detected bounds. The builder's own consistency check caught this and
aborted — which is why the check exists. Composing onto a known box removes the
ambiguity: the icon rect is whatever we paste, not something we must infer.

HONESTY ABOUT THE SOURCE
------------------------
These are **synthetic**, not real screenshots, and are labelled
`source='synthetic_crosshair'`, `labeler='seed_mouse_spot_yes_cases'`. The
crosshair is drawn at a computed position, so the label is exact by
construction — but a synthetic fixture cannot prove the model handles a real
screen. Treat them as a floor, and replace them with real captures when
available. Labelling them as if they were real would be the same defect this
whole workset exists to remove.

Usage:
    .\\.venv\\Scripts\\python.exe seed_mouse_spot_yes_cases.py            # dry run
    .\\.venv\\Scripts\\python.exe seed_mouse_spot_yes_cases.py --apply    # write
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image, ImageDraw  # noqa: E402

import skill_prompt_ext as spe  # noqa: E402

SKILL = "mouse_spot_verify"
OUT_DIR = HERE / "mouse_spot_targets" / "crosshair"
SOURCE_TAG = "synthetic_crosshair"
LABELER = "seed_mouse_spot_yes_cases"

# Canvas the icon is pasted onto. A neutral flat background so the icon box is
# the only structure the model can key on.
CANVAS = (640, 400)
BG = (240, 240, 245)
PANEL = (255, 255, 255)
ICON_BOX = (250, 140, 390, 280)   # (x1, y1, x2, y2) - chosen by us, so exact

CROSS_ARM = 9
CROSS_WIDTH = 2
RED = (255, 0, 0)


def _draw_crosshair(img: Image.Image, cx: int, cy: int) -> None:
    d = ImageDraw.Draw(img)
    d.line([(cx - CROSS_ARM, cy), (cx + CROSS_ARM, cy)], fill=RED, width=CROSS_WIDTH)
    d.line([(cx, cy - CROSS_ARM), (cx, cy + CROSS_ARM)], fill=RED, width=CROSS_WIDTH)


def _compose(icon_src: Path, dst: Path, cx: int, cy: int) -> tuple[int, int]:
    """Flat canvas + icon at ICON_BOX + crosshair at (cx, cy). Returns size."""
    canvas = Image.new("RGB", CANVAS, BG)
    d = ImageDraw.Draw(canvas)
    x1, y1, x2, y2 = ICON_BOX
    # A subtle panel behind the icon, so the scene is not just one floating image.
    d.rectangle([x1 - 40, y1 - 30, x2 + 40, y2 + 30], fill=PANEL)
    with Image.open(icon_src) as im:
        icon = im.convert("RGB").resize((x2 - x1, y2 - y1), Image.LANCZOS)
    canvas.paste(icon, (x1, y1))
    _draw_crosshair(canvas, cx, cy)
    dst.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dst)
    return canvas.size


def build_cases(targets_dir: Path, *, n_hit: int = 6, n_near: int = 4,
                n_miss: int = 2, n_far: int = 2) -> list[dict]:
    """Return the case specs (pure: no DB writes).

    BALANCE IS THE POINT. The previous version produced 2 YES against 14 NO —
    two classes, so the single-class check passed, but a model that always
    answers NO still scored 87.5% and a measured run answered NO 60/60, making
    two different prompts tie by construction. Class counts are exposed as
    parameters and the caller checks the resulting skew before writing.
    """
    cases: list[dict] = []
    sources = sorted(targets_dir.glob("*.png"))
    if not sources:
        return cases
    src = sources[0]
    x1, y1, x2, y2 = ICON_BOX
    icon_cx = (x1 + x2) // 2
    icon_cy = (y1 + y2) // 2
    w, h = x2 - x1, y2 - y1
    margin = 12

    def _hit(k, cx, cy, why):
        return (k, cx, cy, "YES", why)

    def _miss(k, cx, cy, why):
        return (k, cx, cy, "NO", why)

    specs: list[tuple] = []

    # --- HIT cases: crosshair INSIDE the icon box, ground truth exact -------
    _hit_sites = [
        ("msv_hit_icon_center", icon_cx, icon_cy,
         "Crosshair is inside the pasted icon box (icon centre)."),
        ("msv_hit_icon_q1", x1 + w // 4, y1 + h // 4,
         "Crosshair is inside the icon box, upper-left quadrant."),
        ("msv_hit_icon_q2", x2 - w // 4, y1 + h // 4,
         "Crosshair is inside the icon box, upper-right quadrant."),
        ("msv_hit_icon_q3", x1 + w // 4, y2 - h // 4,
         "Crosshair is inside the icon box, lower-left quadrant."),
        ("msv_hit_icon_q4", x2 - w // 4, y2 - h // 4,
         "Crosshair is inside the icon box, lower-right quadrant."),
        ("msv_hit_icon_edge_inside", x2 - 4, y1 + 4,
         "Crosshair is just inside the icon box's top-right corner."),
    ]
    for s in _hit_sites[:max(1, int(n_hit))]:
        specs.append(_hit(*s))

    # --- NEAR-MISS cases: a few px OUTSIDE the box. These are the ones that
    # distinguish a strict prompt from a lenient one: proximity must never be
    # accepted as PASS.
    _near_sites = [
        ("msv_near_outside_bottom", icon_cx, y2 + margin,
         "Crosshair is %dpx BELOW the icon box — close but not a hit." % margin),
        ("msv_near_outside_right", x2 + margin, icon_cy,
         "Crosshair is %dpx to the RIGHT of the icon box — close but not a hit."
         % margin),
        ("msv_near_outside_top", icon_cx, y1 - margin,
         "Crosshair is %dpx ABOVE the icon box — close but not a hit." % margin),
        ("msv_near_outside_left", x1 - margin, icon_cy,
         "Crosshair is %dpx to the LEFT of the icon box — close but not a hit."
         % margin),
    ]
    for s in _near_sites[:max(1, int(n_near))]:
        specs.append(_miss(*s))

    # --- CORNER-MISS: outside on BOTH axes. A diagonal-adjacent miss.
    _miss_sites = [
        ("msv_miss_diag_tl", x1 - margin, y1 - margin,
         "Crosshair is outside the icon box on both axes (upper-left diagonal)."),
        ("msv_miss_diag_br", x2 + margin, y2 + margin,
         "Crosshair is outside the icon box on both axes (lower-right diagonal)."),
    ]
    for s in _miss_sites[:max(1, int(n_miss))]:
        specs.append(_miss(*s))

    # --- FAR cases: nowhere near the icon. Whole-screen guards.
    _far_sites = [
        ("msv_far_corner", 30, 30,
         "Crosshair is far from the icon box, near the canvas corner."),
        ("msv_far_corner_br", CANVAS[0] - 30, CANVAS[1] - 30,
         "Crosshair is far from the icon box, near the opposite corner."),
    ]
    for s in _far_sites[:max(1, int(n_far))]:
        specs.append(_miss(*s))

    for case_key, cx, cy, expected, reason in specs:
        cases.append(
            {
                "case_key": case_key,
                "image": OUT_DIR / ("%s.png" % case_key),
                "src": src,
                "cx": cx,
                "cy": cy,
                "expected": expected,
                "reason": reason,
                "target_name": src.stem.replace("_", " "),
                "icon_bounds": ICON_BOX,
                "size": CANVAS,
            }
        )
    return cases


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write images + cases (default: dry run)")
    ap.add_argument("--targets-dir", default=str(HERE / "mouse_spot_targets"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--n-hit", type=int, default=6,
                    help="number of YES (crosshair inside the icon box) cases")
    ap.add_argument("--n-near", type=int, default=4,
                    help="number of NO near-miss cases (px outside the box)")
    ap.add_argument("--n-miss", type=int, default=2,
                    help="number of NO diagonal-miss cases")
    ap.add_argument("--n-far", type=int, default=2,
                    help="number of NO far cases")
    args = ap.parse_args(argv)

    targets = Path(args.targets_dir)
    cases = build_cases(targets, n_hit=args.n_hit, n_near=args.n_near,
                        n_miss=args.n_miss, n_far=args.n_far)
    if not cases:
        print("no source images under %s" % targets)
        return 1

    print("=== planned cases ===")
    for c in cases:
        inside = (c["icon_bounds"][0] <= c["cx"] <= c["icon_bounds"][2] and
                  c["icon_bounds"][1] <= c["cy"] <= c["icon_bounds"][3])
        print("  %-28s cross=(%4d,%4d) exp=%-3s inside_icon=%s"
              % (c["case_key"], c["cx"], c["cy"], c["expected"], inside))
    print()
    print("icon bounds = %s   image = %s" % (cases[0]["icon_bounds"], cases[0]["size"]))

    # The labels must be self-consistent with the geometry, or the fixtures are
    # wrong and would teach the model the wrong thing.
    bad = []
    for c in cases:
        x1, y1, x2, y2 = c["icon_bounds"]
        inside = (x1 <= c["cx"] <= x2 and y1 <= c["cy"] <= y2)
        if c["expected"] == "YES" and not inside:
            bad.append("%s: expected YES but cross is outside" % c["case_key"])
        if c["expected"] == "NO" and inside:
            bad.append("%s: expected NO but cross is inside" % c["case_key"])
    print()
    if bad:
        print("=== LABEL/GEOMETRY MISMATCH (aborting) ===")
        for b in bad:
            print("  " + b)
        return 2
    print("label/geometry consistency: OK (%d cases)" % len(cases))

    # CLASS BALANCE. A set that is 2 YES / 14 NO passes the >1-class check but
    # still lets a model that always answers NO score 87.5%, which makes two
    # different prompts tie by construction (measured: 60/60 NO, p=1.000).
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["expected"]] = counts.get(c["expected"], 0) + 1
    n = len(cases)
    minority = min(counts.values()) / n
    print()
    print("class mix: %s  minority_share=%.3f" % (counts, minority))
    if minority < 0.20:
        print()
        print("=== SKEWED CLASS MIX (aborting) ===")
        print("  smallest class is %.1f%% of cases. A model answering only the"
              % (minority * 100))
        print("  majority class scores %.1f%%, so a high score here says nothing"
              % (max(counts.values()) / n * 100))
        print("  about the prompt. Raise --n-hit.")
        return 4
    print("class balance: OK (minority %.1f%% >= 20%%)" % (minority * 100))

    if not args.apply:
        print()
        print("DRY RUN — re-run with --apply to write images and cases.")
        return 0

    print()
    print("=== writing images ===")
    for c in cases:
        size = _compose(c["src"], c["image"], c["cx"], c["cy"])
        print("  %-46s %dx%d" % (str(c["image"].relative_to(HERE)), size[0], size[1]))

    print()
    print("=== upserting cases ===")
    for c in cases:
        res = spe.upsert_skill_case(
            case_key=c["case_key"],
            skill_key=SKILL,
            image_path=str(c["image"]),
            target_name=c["target_name"],
            target_action="place the crosshair on the target icon",
            expected=c["expected"],
            expected_reason=c["reason"],
            notes="SYNTHETIC: crosshair stamped at a computed pixel position; "
                  "ground truth exact by construction, but not a real screen.",
            source=SOURCE_TAG,
            labeler=LABELER,
            status="active",
            db_path=args.db,
        )
        print("  %-28s exp=%-3s %s" % (c["case_key"], c["expected"], res.get("action")))

    print()
    print("=== verification ===")
    import skill_learning as sl
    g = sl._gold_set_gate(SKILL)
    import json
    print("  gold_set_gate:", json.dumps(g, ensure_ascii=False))
    print("  gate ok =", g["ok"], "(was False before these cases)")
    return 0 if g["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
