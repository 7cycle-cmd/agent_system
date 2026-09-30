# SKILL — terminology_rubbish

**Contract:** `TERMINOLOGY.RUBBISH` · `skills/1_core/terminology_rubbish/contract.yaml`
**Taxonomy:** `module/task_center`
**Version:** `v1_strict`

---

## WHEN TO USE

Use this skill **before writing a definition**, and when you catch yourself:

* about to write a definition that **restates the name**;
* about to leave a **placeholder** (`TODO`, `a name from tables`);
* about to write a **one-word** definition;
* seeing a definition and wondering whether it **says anything**.

---

## THE PROBLEM THIS SKILL ANSWERS

The human (2026-09-27):

> *"you love rubbish? taskbar_vscode_app!!!????"*
> *"or you need to have helper to cleanup or rubbish definition"*

**MEASURED, and this is the answer:**

| fact | measurement |
|---|---|
| what `add_term` refused | an empty definition, an empty citation, an unciteable citation, a scratch citation, a bad `term_kind`, a bad taxonomy level, an unknown parent, a misspelled key, a bad key form |
| what it did **NOT** refuse | **a definition that says nothing** |
| the sweep's own fallback | `"a name from %s" % scope` — **rubbish by construction** |
| rubbish present today | **0** — the shortest real definition is **64 chars** |
| the gate that would stop it | **did not exist** |

---

## THE RULE

```
A definition must say what the thing IS.
A definition that only restates the name, or is boilerplate, is REFUSED.
```

**THE FOUR RULES, AND THEY ARE MEASURED, NOT A TASTE JUDGEMENT:**

| rule | fires when | why |
|---|---|---|
| `empty` | the definition is empty | a term with no definition is a word nobody defined |
| `restates_the_name` | it equals the `term_key` (or its spaces form) | repeating the name says nothing about the thing |
| `boilerplate` | it starts with a declared placeholder | a placeholder is not a definition |
| `too_short` | below the 20-char floor | MEASURED: the shortest real definition is 64 chars |

**THE ORDER IS DELIBERATE.** MEASURED: with the length rule first,
`taskbar_widgets` (15 chars) was reported as `too_short` — **true, but it hides
the more useful fact that the definition RESTATES THE NAME.** A refusal that
names the vaguest applicable rule sends the reader looking in the wrong place.

---

## WHAT THIS SKILL IS **NOT**

**IT IS NOT A REWRITER.** The helper reports; the human decides.

> A cleanup that rewrites definitions it did not write is a **SECOND AUTHOR**.

**AND IT IS NOT A CORRECTNESS CHECK.** It cannot read meaning, so it refuses only
what it can **PROVE** says nothing. A definition that is wrong but well-written
passes — and that is honest, because the alternative is a check that claims to
judge meaning.

---

## FLOW

1. **The gate:** `terminology_registry.check_definition(definition, term_key)`
   runs inside `add_term`, for a **NEW term only**
2. **The measurement:** `terminology_rubbish.scan(conn)` reports every existing
   term the gate would refuse
3. **The phase:** the `rubbish` sweep phase calls the **SAME** function

**WHY ONE FUNCTION:** a report with its own copy of the rules would drift from
the gate, and the report would then certify a rule the gate no longer runs.

---

## NOT TO DO

* **DO NOT** rewrite an existing definition. Report it; the human decides.
* **DO NOT** retro-refuse an existing row — the gate applies to a **NEW** term only.
* **DO NOT** restate the rules in a second place. `check_definition` is the ONE copy.
* **DO NOT** claim a definition is CORRECT.
* **DO NOT** write `taxonomy_path` — it is a SECOND path, and a third hierarchy
  was already refused.

---

## THE MEASURED RESULT (2026-09-27)

```
register rows checked      : 1497
false refusals             : 0
rubbish found              : 0
rules isolated             : 4 of 4 (each fires on its own case)
sweep phases               : 4 -> 5 (+rubbish)
```

**0 rubbish means the gate has never had to fire — NOT that the gate is
unnecessary.** The gate exists so a NEW rubbish definition cannot enter.

---

## WHY THIS RUNS FOREVER

`skill_registrar.register_all` discovers a skill by its `contract.yaml`, so this
declaration is what makes the rule **auto forever** — the human's words. The
`rubbish` phase in `terminology_sweep_phase` is the recurring measurement.
