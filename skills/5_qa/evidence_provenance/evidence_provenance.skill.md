---
task_id: SKILL.EVIDENCE.PROVENANCE
display_task_id: SKILL.EVIDENCE.PROVENANCE
name: evidence_provenance
catalog_id: 1
subcatalog_id: 5
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: An evidence record must prove where it came from, and a FAIL must say why — otherwise nobody can re-inspect it
reason: Learned from a real unexplained FAIL that reached the Skill Learning Center
artifacts:
  - evidence_provenance.skill.md
schema: "result_yes_no"
---

# Skill: evidence provenance

**Goal:** an evidence record must be able to prove **where it came from**, and a
non-PASS verdict must say **why** — otherwise nobody can re-inspect it.

Package path: `skills/5_qa/evidence_provenance/`
Related: `skills/5_qa/evidence_classify/`, `skills/5_qa/qc_evidence/`

## The case that produced this skill

A permission-row verification returned `FAIL`. The evidence folder held images
and a report. Nobody could answer **why** it failed:

- Was the target rect wrong?
- Was the label wrong?
- Was the screenshot itself wrong?

The report said only `verdict=FAIL`, and Q2's reason said *"the text inside the
red box reads 'Local', not 'Default permissions'"* — which **points at** a wrong
rect but was never promoted to a conclusion. The FAIL was a dead end.

Root cause: **two capture paths existed and the verdict recorded neither.**

| Path | Size | When |
|---|---|---|
| OpenClaw `screen.snapshot` | 1600×900 (`maxWidth=1600`) | may be a different moment |
| local `pyautogui` fallback | 1920×1080 | different moment again |

A verdict computed from one is **not comparable** with a verdict from the other,
and a FAIL cannot be re-inspected without knowing which was used.

## Rule 1 — record provenance

Every evidence record carries:

```json
"provenance": {
  "source": "openclaw | pyautogui | unknown",
  "path": "...",
  "captured_at": "2026-09-19T23:42:24",
  "bytes": 245483,
  "width": 1920,
  "height": 1080,
  "sha256": "68dcae3ebf040cbf...",
  "real_screen": [1920, 1080]
}
```

| Field | Why |
|---|---|
| `source` | tells a reviewer whether two verdicts are even comparable |
| `sha256` | proves the image was not swapped or re-encoded |
| `width`/`height` | exposes a source mismatch (1600×900 vs 1920×1080) |
| `captured_at` | a stale capture explains a stale verdict |
| `real_screen` | the coordinate space the rect was measured in |

**A missing `sha256` or `source == "unknown"` means the evidence is not
trustworthy — treat the verdict as UNKNOWN, not FAIL.**

## Rule 2 — a FAIL must name its cause

A bare `FAIL` is not actionable. Classify it:

| Category | Meaning | Next action |
|---|---|---|
| `pass` | edges all PASS and Q2 YES | none |
| `source_suspect` | capture untrustworthy (no hash / unknown source) | **re-capture first** |
| `geometry_fail` | edges not all PASS | re-measure the rect |
| `text_mismatch` | geometry fine, Q2 NO | wrong rect **or** wrong label — find out which |
| `box_not_seen` | Q1 NO | overlay did not draw / not visible |
| `no_vl_answer` | model did not answer yes/no | re-run |
| `unknown` | none of the above | inspect the overlay manually |

**Order matters.** Trustworthiness is checked **before** geometry: judging
geometry on a bad capture is exactly how a wrong source became an unexplained
FAIL.

## Rule 3 — never let a FAIL be the end of the trail

Every non-PASS verdict must leave:

1. the **overlay PNG** (what a human inspects)
2. the **text-free PNG** (what the model read)
3. the **provenance block**
4. the **root-cause category + action**

If any of the four is missing, the verdict is not reportable.

## Anti-patterns

- ❌ Recording only the image path. A path does not say which capture path ran.
- ❌ Reporting `FAIL` with no category. The reader cannot act.
- ❌ Treating a source mismatch as a target failure. Re-capture first.
- ❌ Assuming the two capture paths are interchangeable. They differ in size
  **and** in moment.
- ❌ Judging geometry before checking the capture is trustworthy.
- ❌ Reporting what a write **attempted** instead of what it **persisted**.

## Rule 4 — REPORT PERSISTED, NOT ATTEMPTED (2026-09-21)

Measured: `apply_proposals` reported `created: 8` and the table held **4 rows**.

Two causes, both the same shape — a claim about what happened, resting on
something that does not distinguish the outcomes:

```python
# 1. the subject could not distinguish groups
subj_kind, subj = "file", g["file"]     # groups are keyed by PREFIX;
                                        # 46 namespaces share one file
# 2. declare() returns ok:True for an UPDATE as well as an INSERT
if res.get("ok"): created += 1          # an update is not a creation
```

**Rule:** a write must report the number of rows **it caused to exist**, and
the subject key must be able to distinguish the units the report counts.

**Invariant to assert:** `reported_created == SELECT COUNT(*)` after the write.

## Rule 5 — A DRY RUN MUST NOT PROMISE AN IMPOSSIBLE PLAN (2026-09-21)

`taxonomy_backfill.apply()` checked `dry_run` **before** resolving its parent
gate. With zero confirmed bindings it promised `count: 214` — a plan that looks
successful and is impossible.

**Worse than no plan**, because the number reads as an estimate rather than a
fiction.

**Rule:** every gate runs **before** the dry-run branch, and the dry run
asserts `would_insert == real inserted`.

## Rule 6 — A PROOF THAT DIES BEFORE ITS SUMMARY HIDES ITS FAILURES (2026-09-21)

`_proof_binding_proposals.py` raised a `NameError` in section 7 (a bare temp DB
has no `capability_registry`). The script aborted **before printing its tally**,
so 6 stale failing assertions were invisible for as long as the file existed —
scanning the output found nothing, which reads as "no problems".

**Rule:** print the tally on the exception path too (`try/finally`), and treat
"no summary line" as a FAILURE, not as an absence of failures.

## Implementation

| Piece | Where |
|---|---|
| `_screenshot()` records the chosen source | `f_perm_click.py` |
| `_screenshot_provenance(path)` | `f_perm_click.py` |
| `_classify_failure(res)` | `f_perm_click.py` |
| `provenance` + `root_cause` in the record | `do_verify_area()` |
| Lesson in the Learning Center | `skill_learning.add_lesson()` |

## Verified 2026-09-19

| Check | Result |
|---|---|
| provenance recorded | `source=pyautogui`, `1920x1080`, `sha256=68dcae3e…` |
| root cause emitted | `geometry_fail` → *"re-measure the rect (X1, X2, Y1, Y2 not PASS)"* |
| source-missing case | `source_suspect` → *"re-capture; capture source not recorded"* |
| lesson stored | `ls_df7efc92d839`, `source_type=self_fail`, `status=draft` |
| lesson visible | `/api/learning/lessons` → `count=1` |
| overview | `lessons_self_fail: 1` |

## The bug in my own fix

The first version of `_screenshot()` assigned `_LAST_SHOT_SOURCE` **without
`global`**, so the module-level value never changed and every record said
`source=unknown` — while the image size (1920×1080) proved it was pyautogui.

Caught only because the provenance block was compared against the actual image
size. **Lesson: a provenance field that is always "unknown" is worse than no
field, because it looks like data.**