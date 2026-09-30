---
task_id: SKILL.ENV.TASK.PROOF
display_task_id: SKILL.ENV.TASK.PROOF
name: env_task_proof
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: Pre-action rules — prove the environment state and prove the task/target exists before any UI automation or state change
reason: Systematic root cause of repeated false verdicts; same rule needed by every UI automation path
artifacts:
  - env_task_proof.skill.md
schema: "result_yes_no"
---

# Skill: env_task_proof

**Goal:** before ANY UI automation or state change, **prove** the environment and
the target. Assume nothing. A rule that is only written down is prose, not a
gate — see "Why this skill needs a contract" below.

Package path: `skills/1_core/env_task_proof/`
Agent entry point: `.github/skills/env-task-proof/SKILL.md` (pointer only)
Related: `skills/5_qa/evidence_classify/`, `skills/5_qa/evidence_provenance/`,
`skills/5_qa/picker_presence_proof/`

## Rule 1 — Environment proof

Before ANY UI automation (vision detect, click, hotkey, screenshot action),
prove the environment state.

| State | How to prove | Where to log |
|-------|-------------|--------------|
| Window maximized | `WinGetPos` rect vs `A_ScreenWidth/Height` (AHK) or `ShowWindow` + rect check (PowerShell). If not maximized → `WinMaximize` → re-check. | `f9_log.txt` / script log |
| Window foreground | `WinActivate` + `Sleep 800` before snapshot. `WinActive` alone is unreliable (focus steal). | log |
| Screen size | `pyautogui.size()` / `GetSystemMetrics` — log it. Vision coords are normalized 0-1000 and scaled by this. | log |
| App/browser state | For browser: page loaded (HTTP 200 / DOM ready). For app: process alive (`Get-Process`). | log |

**Why:** 7B-VL coordinate estimates are only reliable on a FIXED layout.
Windowed vs maximized shifts every pixel → wrong click. Proof = log line showing
the measured rect, e.g. `MODE PROOF: Code rect=0,0 1920x1080 screen=1920x1080`.

## Rule 2 — Task proof

Before acting on a task, prove the task/target EXISTS.

| Check | How to prove |
|-------|-------------|
| State file exists & non-empty | `FileRead` + `Trim` + `if (x = "")` → FAIL branch with log |
| Target element on screen | vision detect returns coords → log them; empty → FAIL, do NOT click blindly |
| Registry/queue entry | query API/DB, log the row; missing → FAIL |
| Post-action result | verify step (e.g. `--verify` re-reads the selector text) → log got vs want |

**Never click without a proven coordinate. Never report success without a
proven post-action read.**

## Rule 3 — Container proof (added 2026-09-20, from a real false verdict)

Before judging the geometry of a target, prove the target's **container** is on
screen. A capture that does not contain the target cannot produce a geometry
verdict — only a fake one.

**The case:** `f_perm_click.py --verify-area default` reported a confident
`FAIL — reads 'VS code'` classified as `geometry_fail`. The operator was told to
re-measure the rect. The rect was never the problem: the screenshot was
**Chrome** showing an app-tile grid, and the rect landed on the `VS code` tile.

**Why the existing provenance did not catch it:**

```
source : pyautogui
size   : 1920x1080     <- matched the real screen
sha256 : 68dcae3e...   <- a real hash
```

Every field was true and the record was still useless. **A same-size image is
not a same-content image.** Size and hash are file properties; they cannot say
what was photographed.

| check | catches | fooled by |
|---|---|---|
| foreground process | wrong app in front | correct app with the menu closed |
| content probe (VL) | menu absent | a VL that misses a present menu |

**Require BOTH.** A false negative costs a re-run; a false positive judges
geometry on the wrong image. Record `foreground.process` + `foreground.title`
in every evidence record.

## Rule 4 — Classify absence FIRST, and call it UNKNOWN

```
picker_not_open  >  source_suspect  >  box_not_seen  >  geometry_fail  >  text_mismatch
```

A container-absent result is `UNKNOWN`, **not** `FAIL`. We did not learn
anything about the rect; recording FAIL asserts a judgement never made. If
`geometry_fail` is tested first the operator is sent to fix something that was
never broken — which is exactly how a wrong-source capture became an
unexplained FAIL in the Learning Center.

### 4a. The container must still be open AT CAPTURE TIME

A separate verification command takes its **own** screenshot when it runs. Run
that way, the container has already dismissed and the result is the (correct but
uninformative) `picker_not_open`.

Measured: `f_perm_click.py --verify-area default` run on its own always reported
`picker_not_open`. Running **open → measure → verify inside ONE process**, so the
capture happens while the menu is still on screen, produced the first-ever
`picker_ok: true` for this target.

**Rule:** open the container, then verify **in the same process**. Do not split
"open" and "verify" across two invocations when the container is transient.

### 4b. A control whose colour encodes STATE cannot be located by colour

The permission pill renders **yellow when that permission is the selected one**.
Locating it by finding yellow pixels therefore measures "what is currently
selected", not "where the control is" — and it silently moves as the user changes
the setting.

Measured: a yellow-pixel search returned a bounding box that spanned a terminal
status fragment and the pill text together, because a stray warm pixel widened
the box. The per-column cluster split revealed the truth:

```
run ( 363.. 363)   5 yellow px   <- stray, not the pill
run ( 551.. 681) 236 yellow px   <- the actual pill text
```

**Rule:** a signal that follows state cannot locate a fixed control. Use a fixed
coordinate measured once, or let a human point at it. When clustering colour
pixels, use **contiguous dense runs**, not min/max — one outlier otherwise
defines an edge.

## Rule 5 — Treat recorded findings as claims with a date

A stored "this does not work" can be true when written and false later. Measured:
the repo recorded `Ctrl+Alt+K` as **abandoned, 1/6 success**, and it was re-tested
as **working reliably** — the earlier failures were missing a `WinActivate` +
short wait before the keypress.

Re-test before relying on a negative finding, especially when a human says
otherwise:

| step | why |
|---|---|
| prove foreground **before** the action | a race is the usual cause of a flaky key/click |
| activate, wait ~0.6s, prove again | the earlier 1/6 failures lacked this |
| capture **before and after**, show both | a number ("6.6% of pixels changed") is not evidence of WHAT changed |
| view the pair, side by side | only then is "the menu opened" established |

And when the action is a **toggle** (Ctrl+Alt+K closes an already-open menu),
check state first or the retry will undo the previous success.

**Never overwrite a stale negative note with a fresh one from memory — with a
measurement.** The note is a claim; the before/after capture is the proof.

## Proof log format

Every proof = one timestamped log line: `PROOF: <what> = <measured value>`.
A task is only "done" when its proof lines exist in the log.

## Rule 6 — A recorded check is not a gate; ENFORCE at the point of action

Recording that something is unproven changes nothing. Measured: a rect carried
`checklist_confirm='no'` correctly, was read back correctly, and was still
**clicked** — while sitting 233px from its target.

Both mainstream automation frameworks fail rather than proceed:

| Framework | Behaviour | Documented as |
|---|---|---|
| Playwright | Before every `click()`: resolves to **exactly one** element, Visible, **Stable** (same box for 2 frames), **Receives Events**, Enabled. Any failure → `TimeoutError`, action not performed. | "Playwright performs a range of actionability checks... and only then performs the requested action." |
| Selenium | Raises when the stored element no longer resolves; also raises when the click would land on a different element. | `StaleElementReferenceException` — *"always relocate the element every time you go to use it"*; `ElementClickInterceptedException` — checks visible, unobscured, enabled **before** clicking. |

The lesson is not "add a check" — we had one. It is **the check must be able to
stop the action**:

```python
auto_rect_audit.assert_rect_ok("perm_pill")   # raises RectNotProven -> no click
```

### Make the discovery automatic, not a habit

A defect found by a hand-written check is found **once**. The same check run over
every registered rect, on a schedule and before every click, is what stops the
class recurring. Three shapes, all wired:

| shape | entry point | use |
|---|---|---|
| callable | `auto_rect_audit.audit_rects()` | any caller |
| pre-action gate | `auto_rect_audit.assert_rect_ok(id)` | immediately before a click |
| schedulable | `auto_rect_audit.py --watch N` | periodic re-audit |

### The reconciliation must allow "cannot judge"

A rect inside a **closed popup** cannot be assessed. Reporting it as a mismatch
is a false alarm, and false alarms train the reader to ignore the tool —
measured: the first version flagged all three picker rows while the menu was
simply shut.

Three outcomes, not two:

| outcome | meaning |
|---|---|
| `MATCH` | re-located by content and the rect covers it |
| `MISMATCH` | re-located and it does not cover it → fix + lesson |
| `NOT APPLICABLE` | container closed → no verdict, and say so |

Reconcile by **content**, never by list index: the detected band count varied
(9 → 10) and the first two bands were terminal text, not the menu.

Findings are written to `skill_lesson` with a `source_ref` idempotency key, so
they are queryable instead of prose.

## Why this skill needs a contract (not just this file)

Three artifacts already encode this same root cause: `env_task_proof`,
`evidence_provenance`, `picker_presence_proof`. None of them had a gate, so each
new automation path re-implemented its own checks and the failure recurred.

> A rule living where there is no contract, no test and no streak is **prose,
> not a gate.**

### Where enforcement now lives (implemented 2026-09-20)

| Layer | Artifact | What it does |
|---|---|---|
| Rule engine | `env_proof.py` | `proof_status()` / `corrected_classification()` — the shared checks |
| Write gate | `evidence_store.save_classify()` | RAISES `ProofRequired`; a judgement without proof is not writable |
| Contract | `MOD.MOUSE_SPOT_HELPER.ENV_PROOF` (taxonomy `module/mouse_spot_helper`) | declares the rules + Field Register |
| TDD cases | 5 `hard_fail` + 1 `pass` | executed by the runner |
| Runner | `skill_tdd_runner.py` | runs the cases, records a streak; exits non-zero on failure |
| Merge gate | `skill_learning.merge_candidate()` | Guard 2 runs the contract TDD; Guard 3 refuses a degenerate gold set |
| Proof of teeth | `test_env_proof_gate.py`, `test_merge_contract_gate.py` | mutation-test each protection |

Run the gate for real:

```
.\.venv\Scripts\python.exe skill_tdd_runner.py
.\.venv\Scripts\python.exe test_env_proof_gate.py
.\.venv\Scripts\python.exe test_merge_contract_gate.py
```

## The two enforcement rules

**Rule A — a judgement needs a proof record.** `PASS`/`FAIL` require
`provenance.source` (not `unknown`), `sha256`, and `foreground.process` +
`foreground.is_code`. `UNKNOWN` asserts nothing, so it stays writable — but it
**must name an absence category**. A silent mid-air `UNKNOWN` is refused.

**Rule B — the classification must fit the state.**

| state | required category | required verdict |
|---|---|---|
| container absent (`picker_ok=False`) | `picker_not_open` | `UNKNOWN` |
| container unproven + `FAIL` blamed on geometry | rejected | rejected |
| container present + absence category | rejected | — |

Rule B is the one that actually rejects the real failure. An earlier version
compared only (category, verdict), so `geometry_fail + FAIL` slipped through
even when the container was known absent — **the rule lacked the one input that
matters.** Container state is now a first-class input.

## Merge gates (a green streak is not proof)

`merge_candidate()` requires three things, and the third was added after a real
false pass was found already recorded in the DB.

| Guard | Requires | Added because |
|---|---|---|
| 1. test run | latest `pass_gate=1` | the original guard |
| 2. contract TDD | the skill's contract cases all pass | a contract that never runs is prose |
| 3. gold set | the gold set has **more than one** expected class | see below |

**The false pass already in the database.** `mouse_spot_verify` has 11 gold
cases and **every one expects `NO`**. A prompt that always answers "NO" scores
100%. Run `id=1` recorded exactly that: `accuracy_pct=100.0, pass_gate=1`, on a
single-class set — so guard 1 was satisfied without the skill discriminating
anything.

`captcha_cell_detect` is the contrast: 78 YES / 192 NO, so its 100% means
something because a lazy prompt can fail it.

**A test set with one expected class is not evidence.** Guard 3 makes that
machine-readable instead of leaving it to whoever reads the numbers.

Guards 2 and 3 are **opt-in by data**: a skill with no contract, or no gold
cases, reports `applies: false` and a reason saying it is unprotected. It is
allowed through but **declared**, never silently treated as passing — silently
reporting "gate ok" for an ungoverned skill is the same false-confidence defect
in a new costume.

## Pitfalls observed while implementing

1. **`{"ok": True, **out}` let the domain result overwrite the transport
   result.** `merge_candidate()` returns `ok: False` when it refuses, and
   spreading that over the HTTP envelope turned a *refusal* into a *transport
   failure* — the UI then could not read `reason`, so the refusal was invisible.
   A refused merge is a **successful call**. Keep `ok` (transport) and
   `result_ok` (domain) as separate names.
2. **A guard that fails open must say so.** `_probe_region()` indexed
   `DEFAULT_AREAS` as a tuple; it is a dict-of-dict. The `KeyError` was swallowed
   by the surrounding `except` and the check silently reported
   "no probe region known" — i.e. it disabled itself without failing.
3. **Optional dependencies degrade proof quality.** Without `psutil` the process
   name became `"unknown"` while `is_code` was still `True` — a record that
   cannot answer "which app". Fall back to `QueryFullProcessImageNameW`.
4. **The container may not be the foreground app.** The VS Code permission picker
   only exists over VS Code, so the foreground check is valid there. For a popup
   that can sit over any window, the foreground check is wrong — use the content
   probe alone, and say so.
5. **A test that asserts its own literal is not a test.** The first
   `absence_as_geometry` case hard-coded the corrected answer, so it stayed green
   when the rule engine was neutered. The mutation test caught it. Every probe
   must DERIVE its result from the code under test.
6. **Overlapping defences make weak tests.** Two protections catching the same
   bad payload is good defence-in-depth but useless as a test: neutering one
   leaves the case green. Each case must isolate exactly one protection.
7. **A reader/writer key mismatch silently empties every case.** `list_tdd_cases()`
   returns the decoded JSON under `input`, but the runner read `input_payload`,
   so every probe ran on an empty dict and passed while testing nothing. Accept
   both keys.
8. **"Flaky" was not flaky.** A test failed once and passed 30 times after. The
   cause was another session concurrently editing the data module it imports —
   not non-determinism in the test. Before calling something flaky, check
   whether anything else was writing to its inputs.

## Hard failure cases (encoded as TDD hard_fail)

1. Capture taken while a non-target app is foreground → NO geometry verdict.
2. Same image size but different content → NOT accepted as same source.
3. Verdict written with no proof record → write MUST be refused.
4. PASS reported with no post-action read-back → invalid.
5. Container absent classified as `geometry_fail` → invalid (must be
   `picker_not_open` / `UNKNOWN`).

## Verified

```
skill_tdd_runner.py           6/6 cases passed, streak recorded
test_env_proof_gate.py        15/15 checks, 3/3 mutations detected
test_skill_contract_store.py  68 passed
real path: --verify-area -> UNKNOWN / picker_not_open (no regression)
```

The mutation suite is the load-bearing evidence: each protection was neutered at
runtime and the matching case was confirmed to go RED. A green suite that has
never been seen to fail proves nothing.

## Rule 7 — A DECLARATION IS NOT A FACT (added 2026-09-21)

When a foreign key is `NOT NULL` and **nothing can derive the parent**, the
honest state is `DECLARED` with **zero** `CONFIRMED` rows. The pressure to
confirm *something* so the graph "looks complete" is the defect.

Measured (`init_ontology_registry.sql:77,103`):

```sql
api_registry.capability_id       INTEGER NOT NULL  FK -> capability_registry
function_registry.capability_id  INTEGER NOT NULL  FK -> capability_registry
```

So every route needs a capability parent today. But measured:

| Question | Provable? |
|---|---|
| does this namespace exist? | ✅ from a route literal (`file:line`) |
| **which capability does it serve?** | ❌ **no evidence anywhere** |
| does that capability exist? | ✅ from the registry row |

`capability_binding`'s own docstring: *"a made-up parent makes the FK graph lie
while looking complete."* A false confirmation is worse than an empty table,
because the empty table is visibly empty.

**What makes confirmation acceptable:** only that the matching rule **DECIDED**.
Measured discriminator — candidate count, not similarity:

| rule matched | meaning | may confirm |
|---|---|---|
| 1 capability | the rule decided | yes, a reviewer may look |
| >1 capability | the rule did not decide | **no** — it would pick at random |

Implemented in `binding_proposals.assert_may_confirm()`; proven by
`_proof_ambiguity_gate.py`. Note it grants **permission only** — the proof
asserts that calling it creates no `CONFIRMED` row.

**Live state (2026-09-21): 5 confirmable, 3 ambiguous, 0 confirmed.** That is
the honest state, not an incomplete task.

## Still open

The permission rects remain uncalibrated placeholders. This gate makes the
failure **honest and un-writable-as-a-verdict**; it does not make the rects
correct. A real PASS still needs a human to measure the four targets.

