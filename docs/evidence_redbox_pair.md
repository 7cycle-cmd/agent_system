# Evidence red box — the 2-PNG pair for LLM photo QC

User spec, quoted because it defines the output exactly (2026-09-20):

> 4 red line will got the red box, and red box = area we are looking for
> red line can help communication can be visible!
> full screen > cut it to red box ... so 2 png in same file!
> -> this is the requirement to send to LLM to QC
> so LLM have middleware for photo QC now
> non stop until you can provide all png with red box

## The pair

Every evidence produces TWO images, stored together:

| # | Path | What it is | Who reads it |
|---|---|---|---|
| 1 | `evidence/<EVID>/redbox.png` | FULL SCREEN with the red box drawn | human (context) |
| 2 | `evidence_redbox/<EVID>.png` | CUT to the red box, red lines kept visible | human (close-up) |
| 3 | `evidence_redbox/<EVID>_vl.png` | same cut, **no text** | the 7B-VL |

The red box is formed by **four full-span red lines** (2 vertical, 2 horizontal).
They are drawn full-span on purpose: a plain rectangle only shows where the box
is, while a line running the whole image can be followed across it, so a reader
can see whether the line really lands on the row boundary.

## Command

```
python evidence_redbox.py --backfill          # every evidence folder + manifest
python evidence_redbox.py --targets           # box every measured coords.db target
python evidence_redbox.py --pair  EVID-...    # the 2 images for one evidence
python evidence_redbox.py --qc    EVID-...    # run the LLM photo QC
python evidence_redbox.py --verify            # exit 0 = every folder accounted for
```

## The LLM photo-QC middleware

The middleware already existed — `evidence_classify.classify_with_overlay()`
(red box -> 4 independent edge verdicts -> Q1 "is a red box present?" -> Q2 "does
the text inside the RED BOX read X?"). `evidence_redbox.qc_photo()` feeds it the
pair, and PASS requires **all three**: every edge PASS **and** Q1 YES **and**
Q2 YES.

The VL is given the **text-free** render. Measured 2026-09-19: with a label drawn
on the image, the 7B-VL reports the *label* as the box contents and answers NO on
a perfectly correct box.

## Fail-closed rule

A red box asserts *"the target is HERE"*. Drawing one at a guessed location would
be a confident false claim — worse than no box, because it looks authoritative.

So:

- No recorded rect -> **no box is drawn**; the row is recorded `UNKNOWN` with a reason.
- Rect resolution order: `classify.json` → `rect_real` → `coord_store` `target_area`
  (matched by the target id inside the EVID name) → UNKNOWN.
- An out-of-bounds or degenerate rect -> UNKNOWN, and **no box PNG is written**.

`--verify` fails when a folder is silently absent from the manifest, because a
silence is indistinguishable from a success.

## Measured state (2026-09-20)

| | count |
|---|---|
| evidence folders | 67 |
| with a rect -> red box drawn | **19** (18 `perm_*` + `perm_pill`) |
| no rect anywhere -> UNKNOWN + reason | **48** (`llm_status_*`, `comfyui_*`, `video_produce_*`, `env_preflight`, `prompt_generator`, `streak_composition`, `cli_proof`, `qc_proof_target`) |
| measured `target_area` rows | 8 (all boxed by `--targets`) |

Only 8 `target_area` rows exist, so the 48 genuinely cannot be boxed without
inventing a rect. The manifest lists every one with its reason.

## Trap: a live re-capture is not a replay

`--targets` draws the box on a **current** screen capture. If the target app is
not foreground, the box is correct geometry over **wrong content** — measured: the
`doubao_chatbox` box was drawn over VS Code because 豆包 was behind it. Use that
output to check the geometry only, never as evidence that the app looked that way.

## API (`mouse_spot_helper`, port 18765)

| Method | Path | Result |
|---|---|---|
| GET | `/api/evidence/redbox` | manifest JSON |
| GET | `/api/evidence/redbox?evidence_id=EVID-...` | the 2-image pair |
| POST | `/api/evidence/redbox/backfill` | re-render everything |
| GET | `/api/evidence/redbox/verify` | 200 ok / 409 not ok |