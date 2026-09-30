---
task_id: SKILL.REDBOX.CROSS.GEOMETRY
display_task_id: SKILL.REDBOX.CROSS.GEOMETRY
name: redbox_cross_geometry
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: Red-box evidence geometry — cross LENGTH = min(inner_w, inner_h)/3 so the plus never touches a red line on any box size
reason: Measured that arm=min/3 left only half the clearance and failed containment on a 30x20 box; LENGTH=min/3 fixes the gap at min/3 of the short side for every size
artifacts:
  - redbox_cross_geometry.skill.md
  - evidence_redbox.py
schema: "result_yes_no"
---

# Skill: redbox_cross_geometry

**Goal:** draw a red box + a red plus on a screenshot so a VL model can check
**where** a target is, and make the plus **impossible to confuse** with the red
guide lines — for **every** box size, not just the one that was measured first.

Package path: `skills/3_ui/redbox_cross_geometry/`
Implementation: `evidence_redbox.py` (`draw_cross`, `cross_length_for`,
`verify_cross_inside`).
Proof: `_proof_cross_length_rule.py` (32/32), `_proof_redbox_geometry.py` (11/11).

## The rule

```
chatbox X1,X2 and Y1,Y2        ->  width = X2-X1       height = Y2-Y1
smaller = min(width, height)
red cross LENGTH = smaller / 3
arm              = LENGTH / 2
```

The plus is centred at `((x1+x2)//2, (y1+y2)//2)` and its arms are symmetric, so
the plus spans `LENGTH` in both directions and its gap to each red line is

```
clearance = inner/2 - LENGTH/2 = min/3
```

That is the whole point: the gap is **min/3 of the short side at every size**,
which is what makes the rule reusable instead of tuned to one screenshot.

## Why the LENGTH and not the arm

The arm is what the eye reads; the **length** is what determines clearance. The two
readings of "/3" differ by a factor of 2:

| rule | length | clearance | 30x20 box |
|---|---|---|---|
| `arm = min/3` (old) | `min * 2/3` | `min/6` — **17%** of short side | **FAILED** containment |
| **`LENGTH = min/3`** | `min/3` | `min/3` — **33%** of short side | PASSES |

Measured 2026-09-20 by rendering real artefacts across a size grid and applying the
gate's own arithmetic (`_sweep_cross_arm.py`):

```
case                  W    H | CURRENT arm=min/3      | USER length=min/3
real doubao chatbox  999  109 | len= 70 clr= 17.5 PASS | len= 35 clr= 35.0 fits
pickup row           400   34 | len= 20 clr=  5.0 PASS | len= 10 clr= 10.0 fits
tiny wide             30   20 | len= 10 clr=  3.0 FAIL | len=  5 clr=  5.5 fits
tiny tall             20   30 | len= 10 clr=  3.0 FAIL | len=  5 clr=  5.5 fits
minimal               12   12 | len=  8 clr=  0.0 FAIL | len=  4 clr=  2.0 fits
```

The old rule did **not** satisfy its own goal: it touched the border on 4 boxes.

## Rules

1. **Size the LENGTH, not the arm.** `LENGTH = min(inner_w, inner_h) // 3`.
2. **Use the SHORTER side.** The long side would make the plus wider than the box
   is tall, so the vertical arm would reach the horizontal lines.
3. **One formula, one module.** `cross_length_for()` is public and `draw_cross()`
   uses the same expression, so a proof and the renderer cannot disagree.
4. **Inner, not outer.** `inner = (x2-x1) - 4`, measured inside the 3px outline.
5. **Symmetric arms.** `ax = ay`. Per-axis arms make a flat line with a nub, which
   the VL reads as "no cross".
6. **Never draw a clamped box.** A rect outside the image, or one that collapses,
   is refused (UNKNOWN) rather than moved — a box at a location nobody measured is
   a confident false claim.

## The honest limit

The floor `max(4, ...)` means the guarantee holds while the short interior is
**>= 24px**. Below that a plus can be visible **or** clear, not both. Such a box is
not capturable, and the gate **FAILS** it with the measured reason instead of
silently accepting a touching plus. Do not "fix" this by shrinking the floor: a
sub-4px plus is not perceivable, so the gate would fail anyway and for a less
informative reason.

## Coordinate space (the trap that cost two bugs)

The rect and the shot must be in the **same** space. `capture_window_with_origin()`
returns the crop's true origin; a screen rect is translated by **subtracting that
origin only**.

Measured 2026-09-20, two wrong versions:
- subtracting the window's stored screen x/y -> box **8px right**
  (doubao reports `x1=-8`, but the crop origin is `max(0, x1)` = 0)
- additionally applying a "screen vs capture" scale -> box **26px up**, because the
  crop is 1:1 and no such scale exists

Both drew a confidently-placed wrong box. One computation, used by every caller.

## How to verify (do not eyeball the thumbnail)

```
.\.venv\Scripts\python.exe _proof_cross_length_rule.py     # 32/32
.\.venv\Scripts\python.exe _proof_redbox_geometry.py       # 11/11
```

`_proof_redbox_geometry.py` measures the **drawn pixels** and asserts the box
edges, e.g. `(400, 916, 1399, 1025)`. An 8px or 26px offset is invisible in a
1079x164 thumbnail; a red-pixel row count shows it immediately.

## Failure modes this skill exists to catch

| Symptom | Cause |
|---|---|
| VL says "no red cross" on a correct artefact | arms too short/nub-like, or a per-axis arm |
| VL says "the plus touches the outline" | length too long (`arm = min/3` instead of `length = min/3`) |
| The plus looks fine but the box is off-target | rect translated by the wrong origin/scale |
| Gate always fails, never passes | the question is unsatisfiable (measure whether the rule can even be met) |
| Box correct in one artefact, wrong in another | two coordinate spaces, one code path assumed |