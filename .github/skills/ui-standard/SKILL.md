---
name: ui-standard
description: "Use when: building, changing, or reviewing ANY UI in this repo — a page, a table, a column header, a badge, a number, an empty state, a button. Enforces that 'user friendly' is a CHECKABLE STANDARD, not an adjective: every visible element is a registered row in ui_element_register carrying a MEASURED unit, and five rules decide whether it meets the standard. Also use when you catch yourself writing 'user friendly', 'clean', 'intuitive', or 'better UX' without a number, or when a column header is a raw DB column name (coords, status, session_id), or when a 'why' is a hover-only title attribute instead of a click."
---

# UI Standard — "user friendly" is a table, not a word

## The rule

```
Rule 1: Every visible element is a REGISTERED row in ui_element_register.
Rule 2: Every row carries a MEASURED unit_key that resolves in unit_register.
Rule 3: Five rules decide the verdict. All five ALWAYS run, even when all pass.
Rule 4: A failing element is DROPPED at the write site, never downgraded.
Rule 5: The five baselines are reported as NUMBERS, never as an adjective.
```

**One sentence:** a UI claim without the five numbers is not a claim.

## Why this exists

THE HUMAN (2026-09-27):

> "be user fiendly as we have ui helper team , why i never have good experience ?
> i just have question and question for all your ui job?"
> "how does it can be better, github can help you by skill how to have ssot for
> all ui element to proof user friendly is not a word is standardize"

**MEASURED, and this is the defect:** `user_friendly` is **NOT REGISTERED** in
`terminology_register` (0 rows of 1483) and appears in **0** files under `docs/`.
It is an adjective with no definition, no unit and no proof — so every time the
agent says "done", the human has no way to check it.

**MEASURED, the second defect:** the repo has **10** `contract.yaml` files and
**0** of them were UI. UI was the ONLY domain with skills but no contract — so it
had no LOCKED QC checklist, no proof, and nothing that could fail.

## The five rules

| # | rule | metric_kind | metric_unit | target |
|---|------|-------------|-------------|--------|
| 1 | `label_registered` | `pct` | pct of visible labels with a `terminology_register` row | 100 |
| 2 | `why_clickable` | `count` | count of hover-only `title=` attributes | 0 |
| 3 | `unknown_names_source` | `pct` | pct of "unknown" badges naming the table AND the value | 100 |
| 4 | `number_names_population` | `pct` | pct of rendered numbers stating what they count | 100 |
| 5 | `empty_state_names_action` | `pct` | pct of empty states naming a next action | 100 |

## The formula

```
UI element -> 5W1H -> first_principle -> factor -> proof -> activation
```

Every link already exists in the repo; only the UI rows were missing:

| link | module | citation |
|---|---|---|
| 5W1H declared once | `skill_5w1h.py` | `DIMENSIONS` at `skill_5w1h.py:51` |
| 5W1H -> first principle | `factor_first_principle.py` | `dimension_of()` |
| first principle -> factor | `factor_first_principle.py` | `derive()`, `audit_register()` |
| factor -> proof | `skill_factor.py` | `record_proof()` — `metric_pass` is DERIVED |
| proof -> activation | `activation_gate.py` | `streak_detail()` |
| the cycle walker | `full_cycle_gap.py` | walks the cycle link by link |

## How to use it

```text
.\.venv\Scripts\python.exe ui_element_register.py --seed
.\.venv\Scripts\python.exe ui_element_register.py --check-divergence
.\.venv\Scripts\python.exe ui_standard.py --audit --page <page_key>
.\.venv\Scripts\python.exe ui_standard.py --check <element_key>
```

## The traps, all MEASURED

1. **A `count` rule's metric is the count of FAILURES, not of passes.** The first
   version reported `passing`, so `why_clickable` read `7/7` while its target is
   `0` — a number about the wrong population.
2. **A rule that does not APPLY must not be counted in the denominator.** The
   first version tested only `not a `, so `not an empty state` slipped through and
   `empty_state_names_action` reported `18/18` when only 1 element is an empty
   state.
3. **A check on a DOCSTRING is not a check on the CODE.** A probe that searched
   the source for a forbidden phrase matched the docstring that FORBIDS it. Read
   the module's STATUS VOCABULARY instead.
4. **A register that agrees with itself proves nothing.** `check_divergence()`
   compares the register against the RENDERED page. MEASURED 2026-09-27: the
   register passed 5/5 while the rendered page failed 4/5.
5. **A glob does not work in an allowlist.** Name the exact path.

## The three UI skills point here

`ui_skill` (`SKILL.UI.DESIGN`), `skill_ui_ux_audit` (`SKILL.UI.UX.AUDIT`) and
`ui_field_builder` (`SKILL.UI.FIELD.BUILDER`) each declare a contract that
**points at** `SKILL.UI.STANDARD`. They do not each get their own rules — that is
the human's `3 > UI template > 1`.
