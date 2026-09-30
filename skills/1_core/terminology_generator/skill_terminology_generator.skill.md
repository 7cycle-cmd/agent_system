# SKILL — terminology_generator

**Contract:** `TERMINOLOGY.GENERATOR` · `skills/1_core/terminology_generator/contract.yaml`
**Taxonomy:** `module/task_center`
**Version:** `v1_strict`

---

## WHEN TO USE

Use this skill **before naming anything that lives in a catalog** — a
`target_template.name`, a catalog entry, a group of related names.

Use it when you catch yourself:

* about to **ask a model for a name**;
* seeing **two conventions in one catalog** (`taskbar_widgets` beside
  `vscode_taskbar_icon`);
* about to **rename** something and wondering whether that moves it.

---

## THE PROBLEM THIS SKILL ANSWERS

The human (2026-09-27):

> *"you are not helping to have standardize for terminology generator ? with
> catalog > subcatalog"*
> *"example taskbar > widgets , terminology generator = taskbar_widgets"*
> *"and we found vscode_taskbar_icon"*
> *"au to rename to taskbar > vscode > APP"*
> *"have same language, not different language in different place"*

**MEASURED, and this is the answer:**

| fact | measurement |
|---|---|
| the names | 18 rows `taskbar_*`, **2 rows `vscode_taskbar_*`** |
| what that is | **ONE catalog named by TWO conventions** — `taskbar_widgets` puts the CATALOG first, `vscode_taskbar_icon` puts the VENDOR first |
| the generator | **did not exist** — `terminology_sweep.draft()` asked the 7B for `term_key`, so the NAME was the MODEL'S CHOICE, per call |
| the human's example | `taskbar_widgets` (term_id 1469) — **already the correct shape**, so the rule was in use for 18 of 20 rows |

---

## THE RULE

```
A child's term_key MUST START WITH its parent's term_key + '_'.

    taskbar > widgets          ->  taskbar_widgets
    taskbar > vscode > app     ->  taskbar_vscode_app
```

**The catalog path is the SSOT; the name is a PROJECTION of it.** So the name
**cannot drift** from the catalog, because it is computed from it.

**AND the model is removed from the NAME path.** The 7B keeps the DEFINITION
(MEASURED: its definitions were usable every time); the NAME becomes
deterministic.

---

## WHY THE RULE IS "STARTS WITH", NOT "JOIN THE FULL PATH"

**MEASURED, and it corrected this module's own first draft.** The first version
joined the FULL path, including the term's own key:

```
path_of(taskbar_widgets) -> ['taskbar', 'taskbar_widgets']
name_for(...)            -> 'taskbar_taskbar_widgets'      <- DOUBLED
```

and it reported **20 of 20 as MISMATCH**, including the 18 that are correct.

**The bug was mine: a term's `term_key` IS its full name, not its leaf segment.**
So the path is built from the **ANCESTORS**, and the leaf segment is what the
term's own key adds on top of its parent.

---

## FLOW

1. Read the catalog path: `ancestors(conn, term_id)` walks `parent_term_id` upward
2. Derive the name: `name_for(parent_key, segment)` joins with `_`
3. Check: `expected_name(conn, term_id)` — does the `term_key` EQUAL the derived name?
4. Generate: `generate(conn, catalog_key, entry_key, definition, cite_ref)`
5. Rename: `rename(conn, term_id, new_segment, new_parent_key)` — alias kept

---

## NOT TO DO

* **DO NOT** let the model choose the NAME. The 7B drafts the DEFINITION only.
* **DO NOT** move a term **OUT of its catalog root** — that changes what
  **ANOTHER** catalog contains. MEASURED: `vscode` (term_id 3) belongs to
  `vscode_conversation`, so moving it into `taskbar` is refused.
* **DO NOT** write `taxonomy_level_registry.is_active` by hand — the gate owns it.
* **DO NOT** create a second catalog table.
* **DO NOT** delete or renumber a `terminology_register` row.
* **DO NOT** accept a `definition_sha256` from a caller — the writer derives it.

---

## REFUSALS (as data, in the contract)

| code | when | because |
|---|---|---|
| `UNKNOWN_CATALOG` | the catalog root does not exist | a name with no catalog has no path to derive from |
| `UNKNOWN_PARENT` | the named parent term does not exist | a child of nothing is not in the catalog |
| `REFUSES_REPARENT` | the move would change the term's catalog ROOT | that changes what ANOTHER catalog contains |
| `NAME_TAKEN` | the derived name already exists under the target parent | `UNIQUE(parent_term_id, term_key)` would raise |
| `BAD_PATH` | a path element is empty | `taskbar_` is a name nobody can read |

---

## THE MEASURED RESULT (2026-09-27)

```
terminology_register rows : 1482 -> 1483   (+1: the intermediate term)
taskbar terms with layer  : 20 'NA' -> 20 'ui_element'
name mismatches           : 2 -> 0
target_template drift     : 2 -> 0
taxonomy_level_registry   : 8 -> 9 levels, 0 active (UNCHANGED)
catalog / subcatalog      : 2 / 3 (UNCHANGED)
```

**The path the human asked for now exists as `parent_term_id` alone:**

```
taskbar                    1468  (the catalog root)
taskbar > taskbar_vscode   1489  (NEW -- the intermediate term)
taskbar > vscode > app     1474  taskbar_vscode_app   (was vscode_taskbar_icon)
taskbar > vscode > win_n   1488  taskbar_vscode_win_n (was vscode_taskbar_win_n)
```

**AND the pre-existing `vscode` (term_id 3) and `app` (term_id 8) were REPORTED,
NOT MOVED** — they belong to other catalogs.

---

## WHY THIS RUNS FOREVER

`skill_registrar.register_all` discovers a skill by its `contract.yaml`, so this
declaration is what makes the rule **auto forever** — the human's words. The
`catalog` phase in `terminology_sweep_phase` is the recurring measurement, and it
now **derives** the name instead of asking the model for it.
