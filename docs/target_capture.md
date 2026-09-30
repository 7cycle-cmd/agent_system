# Target capture — 6-STEP flow, 2 tables, 2 evidence roots

User spec 2026-09-20. Replaces the old `target_area` model, whose centre column was
documented as "derived" in a COMMENT and therefore never enforced.

## The new UI — every step carries its own evidence

Page: **`http://127.0.0.1:18765/target-capture`** (standalone HTML/JS, served by the
helper — not the Vite SPA, which needs an `npm run build` before a change is even
visible).

A 6-row stepper. Each row shows its **own artefact thumbnail**, its **sha256**, its
size, and a green tick **only when the SERVER reports that step's evidence present
and hash-verified**. The tick comes from `GET /api/target-capture/<sk>/verify`, so
the client never awards itself a tick.

| Step | Evidence kind | The picture shows |
|---|---|---|
| 1 | `point_1` | a crosshair at (X1,Y1) on the live screen |
| 2 | `confirm_1` | the crosshair + the echoed X1,Y1 the user confirmed |
| 3 | `point_2` | a crosshair at (X2,Y2) |
| 4 | `rect` | the rectangle + **WIDTH / HEIGHT / CENTRE** as real numbers |
| 5 | `box_gate` | the `evidence/` gate render (4 red lines + red box) |
| 6 | `cross_gate` | the `evidence_final/` render (box + red cross inside) |

Steps 5 and 6 are **the same bytes the gate judged**, not a second renderer — a
re-draw could drift from the image that was actually verified.

### Why evidence is recorded, not just saved to a folder

A PNG on disk proves a file exists. It does not prove *which step* produced it, nor
that it was not swapped afterwards. So each artefact is bound in
`target_capture_evidence`:

```
session_key, step_no (CHECK 1..6), evidence_id, kind,
png_path, sha256, width, height, detail, UNIQUE(session_key, step_no)
```

`verify_step_evidence()` re-hashes every file, so a **deleted or edited PNG fails**
rather than passing a mere existence check. Measured: editing one pixel produced
`step 1: sha256 MISMATCH (recorded e315d0f6f4.., found 1114f0ae88..)`.

A step **cannot advance without writing its artefact** — `render_step` runs before
the session moves, and a failure raises, so the wizard cannot tick a step with
nothing behind it. When an artefact cannot be written, the step stays put and says
why.

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/target-capture` | the wizard page |
| POST | `/api/target-capture/session` | create **or resume** (key derived from the name) |
| GET | `/api/target-capture/<sk>` | session + evidence + verify + missing plan |
| POST | `/api/target-capture/<sk>/step1..step6` | run one step, write its artefact |
| POST | `/api/target-capture/<sk>/fill` | render every step the session supports |
| GET | `/api/target-capture/<sk>/verify` | **409** unless all 6 are hash-verified |
| GET | `/target-capture/evidence/<sk>/step<N>.png` | serve one artefact |

`step1` / `step3` read the **cursor** when no coordinates are posted: the operator
aims the mouse and the value comes from the machine, not from something typed.
`/verify` answers **409** rather than 200-with-`ok:false`, so a caller checking only
the status code cannot read "5 of 6" as success.

## The 6 steps

| Step | Name | What it does |
|---|---|---|
| 1 | `capture_x1y1` | record the first corner |
| 2 | `confirm_x1y1` | echo X1,Y1 back; user confirms -> advance |
| 3 | `capture_x2y2` | record the second corner |
| 4 | `confirm_rect` | compute + echo **width / height / centre** -> advance |
| 5 | `gate_evidence` | LLM gate against `evidence/` |
| 6 | `gate_redcross_register` | red-cross gate against `evidence_final/`, then REGISTER |

**Confirmed formula** (a typo in the original request is corrected here):

```
width  = X2 - X1
height = Y2 - Y1        <-- NOT X2 - X1
cx     = (X1 + X2) / 2
cy     = (Y1 + Y2) / 2
```

## Step 5 gate — `evidence/` (LEVEL 1 ENVIRONMENT PROOF)

Four questions, **all** required:

1. source correct (a real, readable window — not blank / error / bare desktop)
2. four red guide lines present
3. red box present
4. the box holds the input area

### The gate needs something to judge — `target_gate_evidence.py`

Measured bug 2026-09-20: nothing in the capture flow ever **created** the folder
these gates read. Steps 1-4 write `evidence_steps/<session>/stepN.png`; the gate
needs `evidence/<EVID>/shot.png` + a rect. `_open_evidence_for()` existed with zero
callers, and the UI never supplied a `screenshot_id`. So STEP 5 judged an absent
folder — fail-closed, but **unsatisfiable**: a red light nobody could clear.

`ensure_gate_evidence()` binds the two halves:

```
capture the window -> evidence/<EVID>/shot.png
                   -> evidence/<EVID>/classify.json { rect_real: ... }
                      using the rect the operator measured at STEP 4
```

`rect_real` being authoritative in `resolve_rect()` is correct here: this rect is
not classifier-inferred, it is the **measured** rect the operator confirmed.

The rect is written in the **same coordinate space as the shot**:

| capture | rect |
|---|---|
| screen | as measured |
| window | translated by the crop's own origin |

`capture_window_with_origin()` **returns** that origin. Measured bug 2026-09-20:
subtracting the window's stored screen x/y put the box 8px right (doubao reports
x1=-8, but the crop origin is `max(0, x1)` = 0); additionally applying a
"screen vs capture" scale shifted it 26px up, because the crop is 1:1 and no such
scale exists. Both produced a confidently-placed wrong box. One computation, used
by every caller.

## Step 6 gate — `evidence_final/` (LEVEL 2 TARGET PROOF)

Two instruments, **both** required:

| Instrument | Question | Why that instrument |
|---|---|---|
| VL | is there a red plus sign? | "does this look like a cross" is perception |
| pixels (`verify_cross_inside`) | are the cross pixels strictly inside the box? | containment is arithmetic |

Measured 2026-09-20: asked whether the cross was inside the box, the 7B-VL said
*"touching the border"* / *"overlaps the outline"* on three correct artefacts. A
model that cannot measure pixels is the wrong instrument for a geometric fact, and
rewording the prompt until it agreed would only refit the question to one model on
one image. So containment is measured and the VL is kept for the question it is
good at.

### The perception question had to be measured too

On a **verified-correct** artefact (cross centre (538,93) == box centre (539,94),
arms 71/71 symmetric, 552 red px entirely inside the interior), the old prompt got:

```
q1 red box   -> YES   (correct)
q2 red cross -> NO    "The red rectangle is not a cross"
q3 inside    -> NO    "The plus sign overlaps the rectangle's outline"
```

Containment PASSED (arithmetic). Only the perception question failed, so the gate
was **unsatisfiable** and STEP 6 could never register.

Five wordings were compared against a **size-matched negative control** (same
crop, same box, same canvas — the plus removed), each run 3× so a single YES can
be told apart from sampling luck:

| wording | POS | NEG | discriminates |
|---|---|---|---|
| old (*"a plain rectangle is NOT a cross"*) | NO NO NO | NO NO NO | no |
| **name the plus at the centre** | **YES×3** | **NO×3** | **yes** |
| describe-then-answer | YES×3 | YES×3 | no |
| count strokes outside the rectangle | NO×3 | NO×3 | no |
| "two marks: rectangle + plus" | YES×3 | YES×3 | no |

Adopted the second. The deletion of the "a plain rectangle is NOT a cross" clause
matters: that clause **primed** the model to answer "the rectangle is not a cross".

An earlier comparison used a negative control of a different size (480x114 vs
1079x164), which confounds "is there a plus" with "how big is the image" — it made
non-discriminating wordings look meaningful. A control must differ in exactly one
dimension.

## Cross LENGTH = min(width, height) / 3

Skill: `skills/3_ui/redbox_cross_geometry/` (also a short pointer at
`.github/skills/redbox-cross-geometry/SKILL.md`).

```
width = X2-X1 ; height = Y2-Y1
smaller = min(width, height)
red cross LENGTH = smaller / 3        <- the LENGTH, not the arm
arm              = LENGTH / 2
centred, arms symmetric (ax == ay)
```

The plus spans `LENGTH`, so its clearance to each red line is
`inner/2 - LENGTH/2 = min/3` — **33% of the short side at every size**, which is
what makes the rule reusable instead of tuned to one screenshot.

The two readings of "/3" differ by a factor of 2, and the old one did not meet its
own goal (measured with real artefacts + `verify_cross_inside`):

| rule | length | clearance | 30x20 box |
|---|---|---|---|
| `arm = min/3` (old) | `min * 2/3` | **17%** of short side | **FAILED** containment |
| **`LENGTH = min/3`** | `min/3` | **33%** of short side | PASSES |

```
tiny wide     30  20 | len= 10 clr=  3.0 FAIL | len=  5 clr=  5.5 fits
tiny tall     20  30 | len= 10 clr=  3.0 FAIL | len=  5 clr=  5.5 fits
minimal       12  12 | len=  8 clr=  0.0 FAIL | len=  4 clr=  2.0 fits
```

`cross_length_for()` in `evidence_redbox.py` is public and `draw_cross()` uses the
same expression, so a proof and the renderer cannot disagree.

**Honest limit:** the 4px floor means the guarantee holds while the short interior
is **>= 24px**. Below that a plus is visible **or** clear, not both; such a box is
not capturable and the gate FAILS it with the measured reason rather than silently
accepting a touching plus. Do not lower the floor — a sub-4px plus is not
perceivable and the gate would fail anyway, for a worse reason.

## `evidence_redbox/` is derived — safe to delete

`evidence_redbox/` holds the per-evidence crops (human close-up + `_vl.png` for the
VL) plus `manifest.*`. It is **derived**, not raw: the raw capture is
`evidence/EVID-*/shot.png`, and no database row references the crops, so deleting
the folder leaves nothing dangling.

Verified 2026-09-20 by deleting it: `POST /api/evidence/redbox/backfill` rebuilt the
whole set from `evidence/` (84 folders → 36 boxed, 48 UNKNOWN-with-reason,
`verify ok=True`), pytest stayed 207 passed / 0 failed.

```bash
.\.venv\Scripts\python.exe evidence_redbox.py --backfill   # rebuild
.\.venv\Scripts\python.exe evidence_redbox.py --targets    # coords.db targets
```

**Deleting it found two proofs that depended on leftover state** (both fixed, both
the same family as the mismatched negative control — a test measuring residue
instead of code):

- `_proof_cross_gate.py` opened a file an **earlier run of itself** had produced
  (`evidence_redbox/<evid>_vl.png`) → `FileNotFoundError`. It now builds the
  cross-free control with `render_pair()` in the same run, so the only difference
  from the positive is the cross.
- `_proof_redbox_api.py` assumed the manifest existed and hard-asserted
  `total == 67` — a snapshot of one moment. It now POSTs backfill first (as one of
  its checks) and compares `total` against the live count of `evidence/EVID-*` on
  disk, so adding an evidence folder no longer turns it red for an unrelated reason.

## `isactive` contract

`isactive=1` is written **only** by an all-YES pair of gates. Any NO or UNKNOWN
leaves it 0 and the row is **not** registered. `create_target_position()` defaults
`isactive=0`, so a caller that forgets to prove gets an inactive row rather than a
live unproven rect.

The gate runs **before** the INSERT, so there is no code path that registers first
and explains later. A failed attempt still writes a `FAIL`/`UNKNOWN` row to
`target_capture_log`, with the failing question names in `gate_json`.

## Tables (coords.db)

```sql
CREATE TABLE target_position (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE,
    source_id         INTEGER NOT NULL,          -- -> source(id) in agent.db
    source_kind       TEXT    NOT NULL CHECK (source_kind IN ('APP','URL')),
    source_ref        TEXT    NOT NULL,          -- exe path OR url
    x1,y1,x2,y2       INTEGER NOT NULL,
    width,height      INTEGER NOT NULL,
    cx,cy             INTEGER NOT NULL,
    screenshot_id, evidence_id, cross_evidence_id   TEXT,
    isactive          INTEGER NOT NULL DEFAULT 0 CHECK (isactive IN (0,1)),
    created_at, updated_at,
    CHECK (x2 > x1 AND y2 > y1),
    CHECK (width  = x2 - x1),
    CHECK (height = y2 - y1),
    CHECK (cx = (x1 + x2) / 2),
    CHECK (cy = (y1 + y2) / 2)
);
```

`target_autocal_data` — `position_id` FK, width/height/centre, `cross_evidence_id`,
`isactive`. `target_capture_session` makes the wizard resumable (`step_no` CHECK
1..6). `target_capture_log` is the green-tick LOG keyed by `register_id`.

Register id format: **`TGT-<id>`** (e.g. `TGT-1`). The date lives in `created_at`,
not in the id string.

### Why the CHECK constraints matter

They are the fix for a live defect. The old `target_area.perm_pill` row holds rect
`(585,944)-(800,975)` — midpoint `(692,959)` — but stores **`cy=652`, 307px wrong**.
A comment cannot fail; a CHECK runs on every write. Proven: inserting that exact
row is now refused with `CHECK constraint failed: cy = (y1 + y2) / 2`.

## `source` (agent.db) — added columns

| Column | Why |
|---|---|
| `source_kind` | APP vs URL — the user's "type", chooses the input control |
| `app_id` | links `app`, which already owns `exe_path` |
| `instruction_limit` | the column `docs/paste_outside_worker_skill_short.md:112` already claimed existed |

`source_kind` is backfilled for the 4 seeded rows (VS Code / 豆包 -> APP,
Chrome->DeepSeek / Edge->Gemini -> URL) and only where it is NULL.

## Command

```
python target_capture.py --list
python target_capture.py --step1 SES 400 916
python target_capture.py --step2 SES
python target_capture.py --step3 SES 1399 1025
python target_capture.py --step4 SES --name "豆包 send button" --source-id 2 \
        --source-kind APP --source-ref "C:\...\Doubao.exe"
python target_capture.py --step5 SES EVID-...
python target_capture.py --step6 SES EVID-...
python target_capture.py --log TGT-1
python target_capture.py --db <path>     # isolate (proofs/tests)
```

## Migration

```
python _migrate_target_area_dryrun.py     # read-only
```

Dry-run output (2026-09-20): 8 candidates, all migrate, and it NAMES the repair
rather than performing it silently — `perm_pill stored=[692,652]
derived=[692,959] delta=[0,307]`. It also flags the 3 `perm_picker` rects as
**height=20** (a picker row is ~34px) — re-measure candidates.

Migrated rows land `isactive=0`: a copied rect is not a proven rect. The old
`target_area` table is **kept, not dropped** — dropping a table in the same change
that repoints its readers is how a rename becomes an outage.

## Proven

| Proof | Result |
|---|---|
| `_proof_target_tables.py` | 33/33 — incl. the live `perm_pill` row being refused |
| `_proof_target_capture_gate.py` | 24/24 — gate blocks registration, FAIL is logged |
| `_proof_step_evidence.py` | 38/38 — 6 distinct hash-verified artefacts; tamper detected |
| `_proof_target_capture_ui.py` | **48/48** — 6 cards, 4 ticks, 4 decoded thumbnails, resume |
| `_proof_cross_gate.py` | correct -> PASS ; cross-free -> FAIL |
| `_proof_redbox.py` | 17/17 (regression) |
| `_proof_two_level_gate.py` | 28/28 — LEVEL 1 -> `evidence/`, LEVEL 2 -> `evidence_final/` |
| `_proof_step5_no_shot.py` | **12/12** — the gate was judging an absent folder; now bound |
| `_proof_redbox_geometry.py` | **11/11** — the DRAWN box: `(400,916,1399,1025)`, y matches |
| `_proof_setting_ref_key.py` | 16/16 — a per-DB id is not a cross-DB reference |
| `_proof_cross_length_rule.py` | **32/32** — LENGTH=min/3 clears the lines at every size |
| `_proof_task_capture.py` | **34/34** — STEP 6 really registers, then cleans up |
| `run_validate_new_task_tests.py` | 27 PASS / 0 FAIL |
| pytest | **207 passed, 0 failed** |

### A proof must clean up after itself

`_proof_task_capture.py` registered rows into the LIVE `coords.db` and left them
**active** — indistinguishable from a real registration. A proof that writes
production state is not a proof. It now deletes its own row via
`delete_target_position()`, which by default **refuses to delete an active row**
(a proven target must be deactivated deliberately, never removed for a test's
convenience). Live DB after every run: `target_position = 0`.

### Measure the pixels, not the thumbnail

All three late defects were found by measuring the artefact, not by looking at it.
An 8px or 26px offset is invisible in a 1079x164 thumbnail; a red-pixel row count
shows it immediately. `_proof_redbox_geometry.py` asserts the **drawn** box edges:

```
step5 -> (400, 916, 1399, 1025)   # y=916 == the measured y1
step6 -> (400, 916, 1399, 1025)
```

## Three UI defects the proof caught

1. **`refresh()` wrote to `#evid` before `renderGate()` created it.** The null
   access threw and aborted `refresh()` *before* the stepper was drawn, so the page
   showed correctly-set badges over an empty step list. Guarded, and the value now
   flows through `renderGate()`.
2. **`/api/sources` answers `{ok, count, rows:[...]}`**, not a bare array. Reading
   only an array left the Source dropdown silently EMPTY while everything else
   looked healthy.
3. **The helper has no module-level `log()`** — it prints. Passing `log` raised a
   `NameError` that my own `try/except` turned into a silent "registration skipped",
   so the page 404'd. The registration result is now PRINTED, not swallowed, and the
   helper passes `print` explicitly.

## Trap: a live re-capture is not a replay

`--targets` in `evidence_redbox.py` draws boxes on a CURRENT capture, so if the
target app is not foreground the box is correct geometry over wrong content
(measured: the `doubao_chatbox` box drawn over VS Code). Use it for geometry only.