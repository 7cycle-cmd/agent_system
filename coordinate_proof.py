"""coordinate_proof.py -- prove a COORDINATE value: a rect on a screen.

THE HUMAN, verbatim:

    "playwright as sample, coordinate i have data now, proof run need to proof
     that too"
    "both side need to have same language"

THE DATA EXISTS (MEASURED 2026-09-26, `coords.db`):

    target_area                8 rows   (target_id, label, x1,y1,x2,y2, checklist_confirm)
    target_capture_evidence  160 rows   (kind: rect 36, point_1 38, point_2 36,
                                         confirm_1 36, box_gate 12, cross_gate 2)

    perm_default        Default permissions   (640,671)-(1145,691)
    perm_allow_all      Allow all             (640,771)-(1145,791)
    perm_autopilot      Autopilot             (640,837)-(1145,857)
    perm_pill           Permission pill       (585,944)-(800,975)
    doubao_window       豆包 app window        (-8,-8)-(1928,1040)
    doubao_chatbox      豆包 chat box          (400,916)-(1399,1025)
    doubao_send_button  豆包 send button       (1354,970)-(1384,1005)
    doubao_textarea     豆包 text area         (400,916)-(1399,964)

THE MACHINERY EXISTS (MEASURED):

    evidence_classify.classify_with_overlay(image_path, x1,y1,x2,y2, label, out_path)
        -> overlay -> edge verdicts -> 2 VL questions
        Q1 "do you see a red box at the image?"   <- GATING self-check
        Q2 "image inside red box = <label>, yes or no?"
        VERDICT_PASS / VERDICT_FAIL / VERDICT_UNKNOWN

So a coordinate proof is: **a rect + a label + `classify_with_overlay`**. The
pieces existed; nothing joined them to `proof_run`. This module is the join.

THE VALUE FORMAT IS `coordinate`, and `value_type.py` says its proof method is
`coordinate` and its route is `llm.vision` -- the SAME word on both sides.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import value_type as vt

BASE = Path(__file__).resolve().parent
COORDS_DB = BASE / "coords.db"


class CoordinateRefused(ValueError):
    """A coordinate proof that cannot be made, with a named reason."""


def rect_for(conn: sqlite3.Connection, target_id: str) -> dict[str, Any]:
    """The rect for a `target_id`, read from `target_area`. REFUSES if absent.

    A rect that does not exist cannot be proved, and inventing one would be
    fabricating evidence.
    """
    row = conn.execute(
        "SELECT target_id, popup_id, label, x1, y1, x2, y2, isactive "
        "FROM target_area WHERE target_id=?", (str(target_id),)).fetchone()
    if not row:
        known = [r["target_id"] for r in conn.execute(
            "SELECT target_id FROM target_area ORDER BY target_id")]
        raise CoordinateRefused(
            "target_id %r is not in target_area (known: %s). A rect that does not "
            "exist cannot be proved." % (target_id, ", ".join(known) or "none"))
    d = dict(row)
    if int(d["x2"]) <= int(d["x1"]) or int(d["y2"]) <= int(d["y1"]):
        raise CoordinateRefused(
            "target_id %r has a DEGENERATE rect (%s,%s)-(%s,%s): x2 must exceed "
            "x1 and y2 must exceed y1, or the box has no area."
            % (target_id, d["x1"], d["y1"], d["x2"], d["y2"]))
    return d


def prove_rect(image_path: str | Path, *, target_id: str, label: str,
               x1: int, y1: int, x2: int, y2: int,
               out_path: str | Path) -> dict[str, Any]:
    """Prove ONE rect with `evidence_classify.classify_with_overlay`.

    Returns the classify result PLUS the `value_type` and `proof_method`, so a
    caller never has to re-derive them -- the human: "proof run need to understand
    and know which data format need to how to proof = same language".
    """
    import evidence_classify as ec
    r = ec.classify_with_overlay(image_path, int(x1), int(y1), int(x2), int(y2),
                                 str(label), out_path)
    return dict(r, target_id=str(target_id), value_type="coordinate",
                proof_method=vt.proof_method_for("coordinate"),
                route=vt.route_for("coordinate"))


def prove_target(image_path: str | Path, *, target_id: str,
                 out_path: str | Path,
                 coords_db: Path | str | None = None) -> dict[str, Any]:
    """Read a REAL rect from `target_area` and prove it. The whole path, one call."""
    path = Path(coords_db) if coords_db else COORDS_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rect = rect_for(conn, target_id)
    finally:
        conn.close()
    return prove_rect(image_path, target_id=rect["target_id"],
                      label=rect["label"], x1=rect["x1"], y1=rect["y1"],
                      x2=rect["x2"], y2=rect["y2"], out_path=out_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coords-db", default=str(COORDS_DB))
    ap.add_argument("--target-id")
    ap.add_argument("--image")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.coords_db)
    conn.row_factory = sqlite3.Row
    try:
        print("=== the coordinate data (target_area) ===")
        for r in conn.execute("SELECT target_id, label, x1,y1,x2,y2, isactive "
                              "FROM target_area ORDER BY target_id"):
            print("   %-22s %-22s (%s,%s)-(%s,%s) active=%s"
                  % (r["target_id"], r["label"], r["x1"], r["y1"], r["x2"],
                     r["y2"], r["isactive"]))
        print()
        print("=== the value_type resolution for a coordinate ===")
        print("   %s" % vt.describe("coordinate"))
        if args.target_id:
            print()
            print("=== the rect for %r ===" % args.target_id)
            print("   %s" % rect_for(conn, args.target_id))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
