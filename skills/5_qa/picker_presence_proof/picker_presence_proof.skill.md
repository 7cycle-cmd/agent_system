---
task_id: SKILL.PICKER.PRESENCE.PROOF
display_task_id: SKILL.PICKER.PRESENCE.PROOF
name: picker_presence_proof
catalog_id: 1
subcatalog_id: 5
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: Prove the target UI is actually on screen before judging any geometry — a same-size capture of the wrong window looks like data
reason: Learned from a capture of Chrome being cropped with picker coordinates and reported as a confident geometry FAIL
artifacts:
  - picker_presence_proof.skill.md
schema: "result_yes_no"
---

# Skill: picker presence proof

**Goal:** before judging the geometry of a UI target, **prove the target's
container is actually on screen**. A capture that does not contain the target
cannot produce a geometry verdict — only a fake one.

Package path: `skills/5_qa/picker_presence_proof/`
Related: `skills/5_qa/evidence_provenance/`, `skills/5_qa/evidence_classify/`

## The case that produced this skill

`f_perm_click.py --verify-area default` reported a confident:

```
verdict    : FAIL
q2         : NO — "reads 'VS code', not 'Default permissions'"
root_cause : geometry_fail
action     : re-measure the rect
```

The operator was told to re-measure a rect. **The rect was never the problem.**

The `shot.png` was **Chrome** showing the Mouse Spot Helper page — an app-tile
grid with rows reading `豆包 AI / VS code / VS code / 豆包 AI`. The picker rect
`(700,721)-(1100,755)` landed on the `VS code` tile, so the crop legitimately
read "VS code".

### Why the existing provenance did NOT catch it

The provenance block from that run looked **healthy**:

```
source : pyautogui
size   : 1920x1080
sha256 : 68dcae3e...
```

The image size matched the real screen. The hash was real. The source was
recorded correctly. **Every field was true and the record was still useless**,
because the one fact that mattered was missing: *which window was in front*.

`source_suspect` existed but only tested size/hash/source-name — so a
same-size capture of an entirely different application passed every check.

## The rules

### 1. Presence before geometry

```
1. capture
2. PROVE the target container is on screen   <- if NO: stop, do NOT judge
3. only then measure edges / ask the label question
```

Judging a rect that is not in the image yields a number that *looks like* a
measurement. That is worse than no measurement.

### 2. Record the foreground window, not just the image

Image size and hash are properties of the file. They cannot tell you **what was
photographed**. Record:

```
foreground.process     # e.g. "Code.exe" / "chrome.exe"
foreground.title       # window title
foreground_is_code     # did the capture happen over the right app?
```

Two independent checks are needed because either alone is fooled:

| check | catches | fooled by |
|---|---|---|
| foreground process | wrong app in front | a correct app with the menu closed |
| content probe (VL) | menu absent | a VL that misses a present menu |

**Require both.** A false negative only costs a re-run; a false positive judges
geometry on the wrong image and is exactly this bug.

### 3. A same-size image is not a same-content image

`1920x1080 == 1920x1080` proves nothing. Do not treat a size match as a source
match. This is the specific inference that failed here.

### 4. Classify it as its own category, and check it FIRST

```
picker_not_open   -> action: open the picker and re-capture
```

It must be tested **before** `geometry_fail`, otherwise the operator is sent to
re-measure a rect that was never wrong. Order of classification:

```
picker_not_open  >  source_suspect  >  box_not_seen  >  geometry_fail  >  text_mismatch
```

### 5. The verdict is UNKNOWN, not FAIL

We did not learn anything about the rect. Recording `FAIL` asserts a judgement
that was never made.

## Evidence shape

```json
{
  "verdict": "UNKNOWN",
  "picker_ok": false,
  "picker_reason": "no permissions menu visible in [580,601,1220,999] (VL said NO...)",
  "picker_checks": {
    "foreground": {"process": "Code.exe", "is_code": true},
    "content": {"ok": false, "answer": "NO", "region": [580,601,1220,999]}
  },
  "root_cause": {"category": "picker_not_open",
                 "action": "open the picker (pill click) and re-capture"},
  "provenance": {"source": "openclaw", "width": 1600, "height": 900,
                 "foreground_is_code": true}
}
```

## Pitfalls hit while implementing this

1. **`DEFAULT_AREAS` is a dict, not a tuple.** Indexing it as `a[0]` raised
   `KeyError`, was swallowed by the surrounding `except`, and produced
   `"no picker probe region known"` — a check that silently disabled itself.
   *A guard that fails open must say so.* Read the LIVE DB areas first.
2. **`psutil` is optional.** Without it the process name degraded to
   `"unknown"` while `is_code` was still `True` — a record that cannot answer
   "which app". Fall back to `QueryFullProcessImageNameW` (Win32, no dependency).
3. **The container may not be the foreground app.** The VS Code permission
   picker *only* exists over VS Code, so the foreground check is valid here. For
   a popup that can sit over any window, the foreground check would be wrong —
   use the content probe alone, and say so.

## Verified

```
fg process : Code.exe
fg title   : LLM Task Monitor (127.0.0.1:18765) - agent_system - Visual Studio Code
fg is_code : True
picker_ok  : False
reason     : no permissions menu visible in [580,601,1220,999] (VL said NO)
root_cause : picker_not_open      (was geometry_fail before this skill)
verdict    : UNKNOWN
```

## Open

The permission rects are still **uncalibrated placeholders**. This skill does
not fix that — it makes the failure *honest*. A real PASS still requires a human
to measure the four targets with the picker open.
