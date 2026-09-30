---
name: redbox-cross-geometry
description: "Use when: drawing a red box + red plus on a screenshot for VL/LLM QC, measuring a cross/plus size, or debugging 'the cross touches the red line' / 'no red cross seen' on an artefact that looks correct. Enforces LENGTH = min(width,height)/3, symmetric arms, and one shared formula, so the plus never touches a red line on ANY box size."
---

# Red box + red cross geometry

**Goal:** a red plus that a VL cannot confuse with the red guide lines — at every
box size, not just the one screenshot that was tuned.

Full skill: `skills/3_ui/redbox_cross_geometry/redbox_cross_geometry.skill.md`

## When to use

- Drawing a red box + red plus on a screenshot for LLM / VL QC
- A worker must classify their own work from a red-box artefact
- Deciding the size of a cross, plus, or marker inside a measured rect
- The VL says "the plus touches the outline" or "no red cross" on an artefact
  that looks correct
- A red box looked right in one image and wrong in another

## The rule

```
width = X2-X1 ; height = Y2-Y1
smaller = min(width, height)
red cross LENGTH = smaller / 3          <- the LENGTH, not the arm
arm              = LENGTH / 2
centred at ((X1+X2)//2, (Y1+Y2)//2), arms symmetric (ax == ay)
```

Clearance to each red line is then `min/3` of the short side **at every size** —
which is what makes the rule reusable.

## Why it matters (measured 2026-09-20)

Two readings of "/3" differ by a factor of 2:

| rule | length | clearance | 30x20 box |
|---|---|---|---|
| `arm = min/3` (old) | `min*2/3` | **17%** of short side | **FAILED** containment |
| `LENGTH = min/3` | `min/3` | **33%** of short side | PASSES |

The old rule did not meet its own goal — it touched the border on 4 boxes.

## Rules

1. Size the **LENGTH**, not the arm. `LENGTH = min(inner_w, inner_h) // 3`.
2. Use the **shorter** side.
3. **One formula, one module** — `cross_length_for()` in `evidence_redbox.py`;
   the renderer calls the same expression so a proof cannot stay green while the
   artefact is wrong.
4. **Inner**, not outer: `inner = (side) - 4`, measured inside the 3px outline.
5. **Symmetric arms** (`ax == ay`). Per-axis arms make a flat line with a nub and
   the VL answers "no cross".
6. **Never draw a clamped box** — refuse (UNKNOWN) rather than move it.
7. The rect and the shot must be in the **SAME coordinate space**. Translate a
   screen rect by **subtracting the crop origin only** (returned by
   `capture_window_with_origin()`); no scale, no window x/y.

## The honest limit

The 4px floor means the guarantee holds while the short interior is **>= 24px**.
Below that a plus is visible **or** clear, not both; such a box is not capturable
and the gate FAILS it with the measured reason. Do not lower the floor — a sub-4px
plus is not perceivable and the gate would fail anyway, for a worse reason.

**Never verify by eyeballing the thumbnail.** Measure the artefact:
`_proof_cross_length_rule.py` (32/32), `_proof_redbox_geometry.py` (11/11).

## Note on this file

This is a **pointer**, not a second copy. The canonical skill lives at
`skills/3_ui/redbox_cross_geometry/` so it is visible to the Skill Library
scanner, has a contract and can accumulate a streak. Editing this file does not
change the enforced rule.