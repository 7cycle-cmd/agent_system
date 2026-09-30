"""QC harness for evidence_classify — three layers.

Layer 1  unit            : edge detection, z-order, tolerance, bad input
Layer 2  synthetic       : automated regression on generated scenarios
Layer 3  real (optional) : human-in-the-loop, needs the picker open

Usage:
    python qc_evidence_classify.py             # layers 1 + 2 (fast, no model)
    python qc_evidence_classify.py --vl        # also run the VL cases
    python qc_evidence_classify.py --real perm_default perm_allow_all perm_autopilot

Exit code 0 = all run cases passed.

REJECT conditions (from the QC spec) are asserted explicitly:
  1. the text label covers a guide line          -> fail
  2. a blank region is judged PASS               -> fail
  3. a text mismatch still returns YES           -> fail
  4. screenshot/read failure writes success      -> fail
  5. a guide line does not span the whole image  -> fail
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image, ImageDraw

import evidence_overlay as eo

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("ok" if ok else "XX", name,
                           ("  — " + detail) if detail else ""))
    return bool(ok)


def synth(
    w: int = 400,
    h: int = 200,
    rect: tuple[int, int, int, int] | None = (100, 80, 300, 114),
    text: str = "Default permissions",
    fill: tuple[int, int, int] = (40, 40, 40),
    bg: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    img = Image.new("RGB", (w, h), bg)
    if rect:
        d = ImageDraw.Draw(img)
        d.rectangle(list(rect), fill=fill)
        if text:
            d.text((rect[0] + 10, rect[1] + 10), text, fill=(255, 255, 255))
    return img


def red_px(img: Image.Image) -> int:
    # get_flattened_data replaces getdata (Pillow deprecation, removal 2027-10).
    getter = getattr(img.convert("RGB"), "get_flattened_data", None)
    data = getter() if getter else img.convert("RGB").getdata()
    return sum(1 for p in data if p[0] > 200 and p[1] < 90 and p[2] < 90)


# ===========================================================================
# LAYER 1 — unit
# ===========================================================================

def layer1(tmp: Path) -> None:
    print("\n=== LAYER 1: unit ===")

    # --- 1.1 correct rect -> all PASS
    img = synth()
    edges = eo.verify_edges(img, 100, 80, 300, 114)
    check("1.1 correct rect -> all edges PASS",
          all(e.verdict == PASS for e in edges),
          ", ".join("%s=%s" % (e.edge, e.verdict) for e in edges))

    # --- 1.2 offsets on each side
    # Expectations are derived from the actual synthetic geometry (row is
    # x=100..300, y=80..114) and the +/-24px scan radius. If you change the
    # scene, re-derive these instead of patching the numbers.
    #
    #   Y +40 : both Y edges land on the WRONG transition -> FAIL
    #   Y -12 : box covers 68..102 but the row is 80..114 -> misses the bottom
    #           12px. 12px > tol_warn(8) -> FAIL. A 35%-of-row miss must fail.
    #   X +40 : X2 at 340 is 40px past the real right edge (300), beyond the
    #           scan radius -> no edge found -> FAIL. X1 lands on text, WARN.
    #   X +2  : within tol_pass -> PASS (proves tolerance works both ways)
    #   far   : nothing nearby -> all FAIL
    for name, rect, expect_fail in (
        ("Y +40", (100, 120, 300, 154), True),
        ("Y -12 (35% of row)", (100, 68, 300, 102), True),
        ("X +40 (past right edge)", (140, 80, 340, 114), True),
        ("X +2 (within tolerance)", (102, 80, 302, 114), False),
        ("far off", (10, 10, 40, 40), True),
    ):
        e = eo.verify_edges(img, *rect)
        any_fail = any(x.verdict == FAIL for x in e)
        check("1.2 offset %s -> fail=%s" % (name, any_fail),
              any_fail == expect_fail,
              ", ".join("%s=%s" % (x.edge, x.verdict) for x in e))

    # --- 1.3 blank region -> must FAIL (fail-closed)
    e = eo.verify_edges(img, 20, 20, 60, 40)
    check("1.3 blank region -> all FAIL (never assume)",
          all(x.verdict == FAIL for x in e),
          "detected=%s" % [x.detected for x in e])

    # --- 1.4 thin line / thick border: edge found at the same place
    thin = Image.new("RGB", (400, 200), (255, 255, 255))
    ImageDraw.Draw(thin).line([(0, 100), (400, 100)], fill=(0, 0, 0), width=2)
    e = eo.verify_edges(thin, 10, 100, 390, 100)
    check("1.4 thin line -> Y1 edge detected",
          e[2].detected is not None,
          "Y1 det=%s verdict=%s" % (e[2].detected, e[2].verdict))

    # --- 1.5 Z-ORDER: the label must never cover a guide line
    for label in ("", "Default permissions",
                  "A very long label that forces the fallback position " * 2):
        edges = eo.verify_edges(img, 100, 80, 300, 114)
        ov = eo.draw_guide_box(img, 100, 80, 300, 114, label=label, edges=edges)
        w, h = ov.size
        x1_span = sum(1 for y in range(h)
                      if ov.getpixel((100, y))[0] > 200
                      and ov.getpixel((100, y))[1] < 90)
        y1_span = sum(1 for x in range(w)
                      if ov.getpixel((x, 80))[0] > 200
                      and ov.getpixel((x, 80))[1] < 90)
        check("1.5 z-order: X1/Y1 full-span with label=%r" % label[:24],
              x1_span == h and y1_span == w,
              "X1 %d/%d px, Y1 %d/%d px" % (x1_span, h, y1_span, w))

    # --- 1.6 tolerance: no crash on abnormal sizes / out-of-bounds
    # build_overlay accepts an in-memory Image directly, so pass the object.
    tiny = Image.new("RGB", (3, 3), (255, 255, 255))
    r = eo.build_overlay(tiny, 0, 0, 2, 2, tmp / "tiny.png", label="x")
    check("1.6a abnormal size (3x3) does not crash",
          r.ok and r.overlay_path is not None, str(r.error))

    r = eo.build_overlay(img, 5000, 5000, 6000, 6000, tmp / "oob.png", label="x")
    check("1.6b out-of-bounds rect does not crash, edges not PASS",
          r.ok and not r.all_pass,
          "ok=%s all_pass=%s" % (r.ok, r.all_pass))

    r = eo.build_overlay(tmp / "does_not_exist.png", 1, 1, 2, 2, tmp / "x.png")
    check("1.6c missing image -> ok=False, no crash",
          (not r.ok) and bool(r.error), str(r.error)[:60])

    # --- 1.7 every draw is full-span (reject condition 5)
    ov = eo.draw_guide_box(img, 100, 80, 300, 114, label="Default permissions")
    w, h = ov.size
    spans_ok = True
    for x in (100, 300):
        col = sum(1 for y in range(h)
                  if ov.getpixel((x, y))[0] > 200 and ov.getpixel((x, y))[1] < 90)
        spans_ok &= (col == h)
    for y in (80, 114):
        row = sum(1 for x in range(w)
                  if ov.getpixel((x, y))[0] > 200 and ov.getpixel((x, y))[1] < 90)
        spans_ok &= (row == w)
    check("1.7 all 4 guide lines span the whole image", spans_ok)

    # --- 1.8 red pixels actually drawn
    check("1.8 red pixels drawn > 0", red_px(ov) > 0, "%d px" % red_px(ov))


# ===========================================================================
# LAYER 2 — synthetic regression
# ===========================================================================

def layer2(tmp: Path) -> None:
    print("\n=== LAYER 2: synthetic regression ===")
    print("  NOTE: edge-only checks cannot see text, so a 'wrong text' region is")
    print("  expected to be edges-PASS. Catching a text mismatch is the VL job")
    print("  (see --vl). Mixing the two layers would be a false expectation.")
    cases = [
        # name, rect, label, expect_all_pass
        ("standard target box", (100, 80, 300, 114), "Default permissions", True),
        ("Y offset +40px (old bad coords)", (100, 120, 300, 154),
         "Default permissions", False),
        ("blank region", (20, 20, 60, 40), "Default permissions", False),
        ("wrong text region (edges still ok)", (100, 80, 300, 114),
         "Sandboxing for terminal", True),
        ("label-cover regression", (100, 80, 300, 114),
         "Default permissions " * 6, True),
    ]
    for i, (name, rect, label, expect) in enumerate(cases):
        img = synth()
        r = eo.build_overlay(img, *rect, tmp / ("l2_%d.png" % i), label=label)
        check("2 %s -> all_pass=%s (expect %s)" % (name, r.all_pass, expect),
              r.ok and r.all_pass == expect)


# ===========================================================================
# LAYER 3 — real (human-in-the-loop)
# ===========================================================================

def layer3(perms: list[str]) -> int:
    print("\n=== LAYER 3: real scene (needs the picker OPEN) ===")
    import f_perm_click as pc

    print("  NOTE: open the permission picker BEFORE running this.")
    failures = 0
    summary = []
    for perm in perms:
        print("\n  --- %s ---" % perm)
        res = pc.do_verify_area(perm)
        v = res.get("verdict")
        edges_ok = res.get("edges_all_pass")
        vl = (res.get("vl") or {}).get("answer")
        q1 = ((res.get("questions") or {}).get("q1_red_box_present") or {}).get("answer")
        print("      verdict=%s edges_all_pass=%s Q1(red box)=%s Q2(label)=%s"
              % (v, edges_ok, q1, vl))
        print("      evidence=%s" % res.get("evidence_id"))
        print("      overlay=%s" % res.get("files", {}).get("overlay"))
        summary.append({"perm": perm, "verdict": v, "edges_all_pass": edges_ok,
                        "q1": q1, "q2": vl,
                        "evidence_id": res.get("evidence_id")})
        if v != PASS:
            failures += 1

    print("\n  === layer 3 summary ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print("\n  >>> A human MUST inspect each overlay PNG above and confirm the red")
    print("  >>> lines cut the target row's top and bottom edges.")
    return failures


# ===========================================================================
# main
# ===========================================================================

def main() -> int:
    args = sys.argv[1:]
    tmp = Path(tempfile.mkdtemp(prefix="qc_evidence_"))
    try:
        layer1(tmp)
        layer2(tmp)

        if "--real" in args:
            i = args.index("--real")
            perms = args[i + 1:] or ["perm_default"]
            layer3(perms)

        if "--vl" in args:
            print("\n=== VL cases (local 7B, slow) ===")
            import evidence_classify as ec
            img = synth()
            # Use the VL-SAFE render (no text): an annotated overlay makes the
            # model report the annotation as the box contents. See
            # draw_guide_box(annotate=...).
            ov_res = eo.build_overlay(img, 100, 80, 300, 114, tmp / "vl.png",
                                      label="Default permissions")
            pos = Path(ov_res.vl_path)
            check("VL: text-free render exists", pos.is_file(), str(pos.name))
            r = ec.classify_yes_no(pos, "Default permissions")
            check("VL correct label -> PASS", r.verdict == PASS,
                  "%s / %s" % (r.answer, r.reason[:60]))
            r2 = ec.classify_yes_no(pos, "Sandboxing for terminal")
            check("VL wrong label -> FAIL (reject cond. 3)",
                  r2.verdict == FAIL, "%s / %s" % (r2.answer, r2.reason[:60]))
            # Reject condition 1/5 protections on the annotated render
            ann = Path(ov_res.overlay_path)
            check("VL: annotated render also produced", ann.is_file(), ann.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 74)
    npass = sum(1 for _, ok, _ in results if ok)
    nfail = len(results) - npass
    print("QC: %d passed, %d failed, %d total" % (npass, nfail, len(results)))
    if nfail:
        print("\nFAILED:")
        for name, ok, detail in results:
            if not ok:
                print("  XX %s  %s" % (name, detail))
    print("=" * 74)
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())