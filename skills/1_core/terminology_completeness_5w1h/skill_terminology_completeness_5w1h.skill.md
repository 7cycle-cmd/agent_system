# SKILL — terminology_completeness_5w1h

**Contract:** `TERMINOLOGY.COMPLETENESS.5W1H` · `skills/1_core/terminology_completeness_5w1h/contract.yaml`
**Taxonomy:** `module/task_center`
**Version:** `v1_strict`

---

## WHEN TO USE

Use this skill **before naming a target**, and when you catch yourself:

* unable to say **WHICH** one you are looking for;
* about to put an **instance** back into a name;
* about to **guess** a selector;
* about to claim a question is answered when **no column** answers it.

---

## THE PROBLEM THIS SKILL ANSWERS

The human (2026-09-27):

> *"chrome can have many tab, without taskbar_chrome_deepseek, how to you know
> you are looking for deepseek?"*
> *"same as sample, taskbar_vscode without taskbar_vscode_app, how to you know
> it is APP not browser"*
> *"fuck! how to you have correct 5W1H in easy"*

**MEASURED, and this is the answer:**

| question | the column that answers it | present? |
|---|---|---|
| **WHAT** is it | `term_key` (the KIND) | **YES** — `taskbar_chrome` |
| **WHICH** one | **nothing** | **NO** — the instance was prose in `label` |
| **WHERE** is it | the coordinate | **YES** |
| **WHEN** | **nothing** | **NO** |
| **WHY** does it exist | `definition` | **YES** |
| **HOW** do I act on it | `field_type` | **YES** |

**4 of 6 answerable; the 2 that are not are exactly the 2 the human named.**

---

## THE RULE

```
A NAME answers WHAT. A FIELD answers WHICH.
A question with no column is a question nobody can answer.
```

### The name is a KIND; the selector is the INSTANCE

```
taskbar_chrome          <- WHAT  (the KIND: a Chrome button on the taskbar)
  + selector='Optical'  <- WHICH (the INSTANCE: the window titled 'Optical')
  + selector_kind='window_title'
  = the ONE button
```

**The human's own example is the proof:** *"chrome can have many tab"* — so
`taskbar_chrome` alone is **ambiguous**, and `taskbar_chrome_optical` is
**unstable**. **Neither the name alone nor the instance alone is enough. You need
BOTH, in two fields.**

### A selector with no kind is a value nobody can match

`'Optical'` means nothing until a reader knows it is a **window title** and not
an executable. So `selector_kind` is **required**, and it is one of:

| kind | what it matches | example |
|---|---|---|
| `window_title` | a window's title | `Optical` |
| `executable` | a process image name | `msedge.exe` |
| `url` | a page address | — |
| `hotkey` | a key combination | `win+n` |
| `position` | a place on the bar | `far right` |
| `variant` | a mode of one control | `English` |

### The selector is MEASURED, never guessed

MEASURED: **6 labels carry a parenthetical, and only 1 is an instance**:

| label | parenthetical | kind |
|---|---|---|
| `Chrome (Optical) button` | `Optical` | **window_title** |
| `Show desktop (far right)` | `far right` | position |
| `Tray input indicator (English)` | `English` | variant |
| `Tray input indicator (Traditional Chinese)` | `Traditional Chinese` | variant |
| `Show hidden icons (tray chevron)` | `tray chevron` | position |
| `Widgets button (weather)` | `weather` | position |

**A guesser would call all 6 an instance and refuse `English`.** So the selector
comes from the row's OWN measured `label`, never from a rule about parentheses.

---

## HOW TO APPLY IT

```text
1. Ask WHAT it is.        -> the NAME (a KIND, stable).
2. Ask WHICH one it is.   -> the SELECTOR + its KIND (the INSTANCE).
3. Ask WHERE it is.       -> the coordinate.
4. Ask WHEN.              -> MEASURED: there is no column. SAY SO.
5. Ask WHY it exists.     -> the definition.
6. Ask HOW to act on it.  -> the field_type.
```

**Step 4 is the honest one.** MEASURED: `when` is missing for **all 20** taskbar
targets. That is a **FINDING**, not a pass — and a skill that hid it would be
claiming a 5W1H it does not have.

---

## WHAT THIS SKILL IS NOT

* **NOT a reason to put an instance back into a name.** A window title changes,
  so a name carrying it is a snapshot.
* **NOT a replacement for `label`.** The prose is for the **human**; the selector
  is for the **machine**. Two readers, two fields.
* **NOT a guesser.** The selector is MEASURED.
* **NOT a second copy of the rule.** `completeness_5w1h` is the ONE reader; the sweep
  phase and the proof both CALL it.

---

## THE MEASURED DEFECT THIS SKILL CLOSES

MEASURED: the previous plan said *"the instance belongs in `label`"*. That is
correct for a **HUMAN** and **WRONG for a MACHINE** — a machine cannot match the
prose `'Chrome (Optical) button'` to a window.

**So the proof passed while "which Chrome?" had no machine-usable answer.** That
is the gap this skill closes.
