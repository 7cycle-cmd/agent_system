"""Tests for the size-aware edge scan radius.

THE BUG THIS PINS DOWN
----------------------
`verify_edges` searched +/-24px around each candidate for the strongest
luminance step. On a 30x30 icon that window contained the glyph's LEFT edge
whichever edge was being tested, so X1/X2/Y1/Y2 ALL resolved to detected=7.
X2 then reported delta=17 and Y2 delta=19 against a rect that was correctly
placed — an unavoidable FAIL produced by the window, not by the rect.

This is a DIFFERENT failure mode from a borderless row:
  * borderless row  -> no step exists at all         -> delta=None
  * small target    -> several steps exist and compete -> same detected everywhere

Each check isolates one property. The mutation block proves the cap is
load-bearing: without it, a small target's four edges collapse onto one value.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")

import evidence_overlay as eo  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print("  %s  %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("        %s" % detail)


def small_target(span: int = 30, inset: int = 6) -> Image.Image:
    """Dark panel with a light inset rectangle, mimicking a small UI icon."""
    im = Image.new("RGB", (span, span), (30, 30, 32))
    d = ImageDraw.Draw(im)
    d.rectangle([inset, inset, span - 1 - inset, span - 1 - inset],
                fill=(210, 210, 215))
    return im


def main() -> int:
    print("=== test_scan_radius ===")

    # ---- 1. the radius function -------------------------------------------
    check("large target (200px) keeps the full SCAN_RADIUS (backward compatible)",
          eo.scan_radius_for(200) == eo.SCAN_RADIUS,
          "got %d, expected %d" % (eo.scan_radius_for(200), eo.SCAN_RADIUS))
    check("200px span is NOT capped (200//3 = 66 > 24)",
          eo.scan_radius_for(200) == 24)
    check("30px target is capped to 30//3 = 10",
          eo.scan_radius_for(30) == 10, "got %d" % eo.scan_radius_for(30))
    check("60px target -> 20 (still below 24)",
          eo.scan_radius_for(60) == 20, "got %d" % eo.scan_radius_for(60))
    check("tiny target (6px) does not go below the floor",
          eo.scan_radius_for(6) == eo.SCAN_RADIUS_MIN,
          "got %d" % eo.scan_radius_for(6))
    check("degenerate span 0 -> floor, not 0",
          eo.scan_radius_for(0) == eo.SCAN_RADIUS_MIN)
    check("negative span -> floor (no crash)",
          eo.scan_radius_for(-5) == eo.SCAN_RADIUS_MIN)

    # ---- 2. live behaviour on a small target ------------------------------
    im = small_target(30, 6)
    edges = eo.verify_edges(im, 6, 6, 24, 24)
    det = {e.edge: e.detected for e in edges}
    print("  small target (30px) detected per edge: %s" % det)
    check("all four edges found something (no delta=None on a real edge)",
          all(e.detected is not None for e in edges),
          str([(e.edge, e.detected) for e in edges]))
    # The invariant is PER AXIS: X1 and X2 are opposite edges of the same span,
    # so they must resolve to DIFFERENT positions. On a square target X1 and Y1
    # legitimately coincide (same inset), so requiring four distinct values
    # would be wrong — that was my first assertion and it was mistaken.
    check("X1 and X2 resolve to DIFFERENT edges (the original collapse was X1==X2)",
          det.get("X1") != det.get("X2"),
          "X1=%s X2=%s" % (det.get("X1"), det.get("X2")))
    check("Y1 and Y2 resolve to DIFFERENT edges (the original collapse was Y1==Y2)",
          det.get("Y1") != det.get("Y2"),
          "Y1=%s Y2=%s" % (det.get("Y1"), det.get("Y2")))
    check("X1 resolves near its own edge, not the opposite one",
          det.get("X1") is not None and abs(det["X1"] - 6) <= eo.TOL_WARN,
          "X1 detected=%s" % det.get("X1"))
    check("X2 resolves near its own edge (this was the failing one)",
          det.get("X2") is not None and abs(det["X2"] - 24) <= eo.TOL_WARN,
          "X2 detected=%s (was 7 before the fix)" % det.get("X2"))
    check("Y2 resolves near its own edge (this was the failing one)",
          det.get("Y2") is not None and abs(det["Y2"] - 24) <= eo.TOL_WARN,
          "Y2 detected=%s (was 5 before the fix)" % det.get("Y2"))

    # ---- 3. explicit override still honoured ------------------------------
    forced = eo.verify_edges(im, 6, 6, 24, 24, scan_radius=eo.SCAN_RADIUS)
    fdet = {e.edge: e.detected for e in forced}
    print("  forced radius=24 detected per edge: %s" % fdet)
    check("an explicit scan_radius overrides the cap",
          fdet != det, "forced result identical to capped result")

    # ---- 4. MUTATION: is the cap load-bearing? ---------------------------
    # Reproduce the old behaviour by forcing the wide window. If the four edges
    # do NOT collapse here, then the cap is decorative and the fix would be
    # pointless.
    collapsed_x = fdet.get("X1") == fdet.get("X2")
    collapsed_y = fdet.get("Y1") == fdet.get("Y2")
    check("MUTATION: wide window makes X1 == X2 (the original defect)",
          collapsed_x, "X1=%s X2=%s" % (fdet.get("X1"), fdet.get("X2")))
    check("MUTATION: wide window makes Y1 == Y2 (the original defect)",
          collapsed_y, "Y1=%s Y2=%s" % (fdet.get("Y1"), fdet.get("Y2")))
    check("so the cap IS load-bearing (capped result differs from wide)",
          not (det.get("X1") == det.get("X2") and det.get("Y1") == det.get("Y2")))

    # ---- 5. a large target is unaffected ---------------------------------
    big = Image.new("RGB", (400, 400), (30, 30, 32))
    d = ImageDraw.Draw(big)
    d.rectangle([100, 80, 300, 114], fill=(210, 210, 215))
    be = eo.verify_edges(big, 100, 80, 300, 114)
    bdet = {e.edge: e.detected for e in be}
    print("  large target (200x34) detected per edge: %s" % bdet)
    check("large target still passes all four edges",
          all(e.verdict == "PASS" for e in be),
          str([(e.edge, e.verdict, e.delta) for e in be]))
    # Radius is derived PER AXIS. This rect is 200 wide but only 34 tall, so the
    # X edges may use the full 24 while the Y edges are capped to 34//3 = 11.
    # Demanding radius=24 on every edge was my second mistaken assertion.
    xr = {e.edge: e.note for e in be if e.edge.startswith("X")}
    yr = {e.edge: e.note for e in be if e.edge.startswith("Y")}
    print("  X-edge notes: %s" % xr)
    print("  Y-edge notes: %s" % yr)
    check("X edges use the FULL radius (span 200 is wide enough)",
          all("radius=24" in n for n in xr.values()), str(xr))
    check("Y edges are capped to 34//3 = 11 (span is only 34 tall)",
          all("radius=11" in n for n in yr.values()), str(yr))

    print()
    n = sum(1 for _, p, _ in CHECKS if p)
    print("%d/%d checks passed" % (n, len(CHECKS)))
    return 0 if n == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
