# SKILL — terminology_node_kind

**Contract:** `TERMINOLOGY.NODE.KIND` · `skills/1_core/terminology_node_kind/contract.yaml`
**Taxonomy:** `module/task_center`
**Version:** `v1_strict`

---

## WHEN TO USE

Use this skill **before naming a term**, and when you catch yourself:

* unable to tell whether a term is a **container** or a **leaf**;
* about to name something after a **window title**, a **file name**, or any
  value that **changes with the data**;
* about to add a `group` value to `term_kind`, or an `is_group` column;
* about to **guess** whether a token is an instance.

---

## THE PROBLEM THIS SKILL ANSWERS

The human (2026-09-27):

> *"i have taskbar_vscode and taskbar_vscode_app"*
> *"which is rubbish"*
> *"or problem is term_key kind layer definition cite_ref -> table design"*
> *"why taskbar_vscode not = taskbar_vscode_app"*
> *"and taskbar_chrome_optical??? not taskbar_chrome_deepseek?"*

**MEASURED, and this is the answer:**

| fact | measurement |
|---|---|
| `taskbar_vscode` (1489) | **2 children** — a **GROUP** |
| `taskbar_vscode_app` (1474) | **0 children** — a **LEAF** |
| what the table said | **both `term_kind='entity'`** — two identical rows |
| the `term_kind` CHECK | `('count','action','role','entity','qualifier','part')` — **no `group`** |
| the population | **7 terms have children; 1490 do not** |
| `taskbar_chrome_optical` | encodes the **WINDOW TITLE `Optical`** — **NOT STABLE** |

**So the human's diagnosis is CORRECT: this is a TABLE DESIGN problem.** The
table can express *"what kind of thing is this"* (`term_kind`) and *"what
contains what"* (`parent_term_id`), but **not "is this a container"** — and that
is the one fact the reader needs.

---

## THE RULE

```
A GROUP is DERIVED from children > 0, and SHOWN.
A name is a KIND, never an INSTANCE.
```

### Rule 1 — the kind is DERIVED, not stored

| option | verdict |
|---|---|
| add `group` to `term_kind`'s CHECK | **REJECTED** — `term_kind` answers *"what kind of thing is this"*; a group is a **position in the tree**, not a KIND. Adding it would make `term_kind` answer two questions. |
| add an `is_group` column | **REJECTED** — it is a **second truth**. It can drift from `children > 0`, and a drifted `is_group` is worse than none. |
| **derive it from `children > 0`, and SHOW it** | **CHOSEN** |

**WHY:** the fact is **already in the table** (`parent_term_id`), and a derived
value **cannot drift**. The defect is not that the fact is missing; it is that
**nothing SHOWS it**. So the fix is a **reader** (`node_kind`) and a **column**,
not a schema change.

**AND it is ONE function**, so the API, the UI and the proof cannot disagree
about what a group is.

### Rule 2 — a name is a KIND, never an INSTANCE

```
A name must not encode a value that CHANGES with the data.

    taskbar_chrome_optical   ->  taskbar_chrome      (the instance stays in `label`)
```

**THE RULE IS NARROW, AND IT IS MEASURED.** It is **not** "no parenthetical" —
MEASURED: **6 labels carry a parenthetical, and only 1 is an instance**:

| label | parenthetical | is it an instance? |
|---|---|---|
| `Chrome (Optical) button` | `Optical` | **YES — a window title** |
| `Show desktop (far right)` | `far right` | no — a POSITION |
| `Tray input indicator (English)` | `English` | no — a VARIANT |
| `Tray input indicator (Traditional Chinese)` | `Traditional Chinese` | no — a VARIANT |
| `Show hidden icons (tray chevron)` | `tray chevron` | no — a DESCRIPTION |
| `Widgets button (weather)` | `weather` | no — a DESCRIPTION |

**So the rule cannot be "no parenthetical".** A checker cannot tell `Optical`
(a window title) from `English` (a variant) by looking at the string — the two
are the same shape. **A checker that guessed would refuse `English`.**

So the rule is: **a name may not contain a token that is DECLARED as an instance
value**, and the declared list is a **TABLE ROW with a reason** — not a Python
literal, because a declared value with no reason is a guess wearing a table's
clothes.

**AND NOTHING IS LOST.** MEASURED: the instance is **already recorded** in
`target_template.label = 'Chrome (Optical) button'` — the column that is **FOR
display**. So the name becomes a KIND and the instance stays where a reader looks
for it.

---

## HOW TO APPLY IT

```text
1. Ask: does this term CONTAIN other terms?
       yes -> it is a GROUP. Do not add a kind for it; node_kind derives it.
       no  -> it is a LEAF.

2. Ask: does this name carry a value that CHANGES with the data?
       a window title, a file name, a timestamp, a session id  ->  YES, refuse it.
       a position, a variant, a description                     ->  no, it is a KIND.

3. If it is an instance, DECLARE it in `terminology_instance_value` with a
   reason, and put the instance in `label` where a reader looks for it.
```

---

## WHAT THIS SKILL IS NOT

* **NOT a schema change.** No `group` in `term_kind`; no `is_group` column.
* **NOT a guesser.** The instance list is DECLARED, because a guesser would
  refuse `English` along with `Optical`.
* **NOT a retro-refuser.** The gate applies to a **NEW** term only — a rule that
  retro-refuses live data is a mass refusal, not a gate.
* **NOT a second copy of the rule.** `node_kind` is the ONE reader; the sweep
  phase and the proof both CALL it.

---

## THE MEASURED DEFECT THIS SKILL CLOSES

MEASURED: `_proof_terminology_rubbish.py` asserted the path display, the 4
definition rules, and the drift. **No check asserted that a GROUP is
distinguishable from a LEAF, and none asserted that a name is a KIND.**

**So the proof passed while the human could not tell a group from a leaf.** That
is the gap this skill closes.
