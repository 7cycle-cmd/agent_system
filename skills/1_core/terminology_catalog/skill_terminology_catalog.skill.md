# SKILL — terminology_catalog

**Contract:** `TERMINOLOGY.CATALOG` · `skills/1_core/terminology_catalog/contract.yaml`
**Taxonomy:** `module/task_center`
**Version:** `v1_strict`

---

## WHEN TO USE

Use this skill **before naming anything that a target uses** — a
`target_template.name`, a catalog entry, a group of related names.

Use it when you catch yourself:

* seeing **two names for one thing** and not knowing whether that is a hierarchy
  or a duplicate;
* seeing a name in **two languages** and not knowing which one is the identity;
* about to create a `catalog` / `subcatalog` **table**;
* about to write a name with **no definition**.

---

## THE PROBLEM THIS SKILL ANSWERS

The human (2026-09-27):

> *"why you have 2 language for the same thing, so we need to have catalog, and
> how does it can standardize, by termontology ?"*
> *"catalog table and subcatalog table, name defintion by termontology"*
> *"by skill and auto forever"*

**MEASURED, and this is the answer:**

| fact | measurement |
|---|---|
| the two rows | `taskbar_doubao` (label `豆包 button`) and `taskbar_doubao_browser` (label `豆包浏览器 button`) |
| what they ARE | **two products from one vendor** — the 豆包 desktop app (`Doubao.exe`) and the 豆包 browser window |
| what recorded that | **NOTHING** |
| taskbar names registered | **0 of 20** |
| `catalog` / `subcatalog` | **already exist**, 2 + 3 rows, every `description = 'auto-created by chat_center'` |
| hierarchies already in `terminology_register` | **two** — `parent_term_id` (self-FK) and `taxonomy_level` + `taxonomy_path` |

---

## THE RULE

```
A CATALOG is a term whose children are its entries.   -> parent_term_id
A LAYER is what KIND of thing a term is.              -> taxonomy_level
```

**The two existing hierarchies get ONE job each, so a third is never needed.**

| hierarchy | job | example |
|---|---|---|
| `parent_term_id` | **the CATALOG** — what contains what | `taskbar` → `taskbar_doubao` |
| `taxonomy_level` + `taxonomy_path` | **the LAYER** — what KIND of thing it is | `db_table` / `erp.order` |

**A catalog is a ROOT term** (`parent_term_id IS NULL`).
**An entry is a CHILD term** (`parent_term_id = <catalog term_id>`).

**No new table. No second truth.** The human's `catalog`/`subcatalog` becomes a
**VIEW** over `terminology_register`.

---

## THE LANGUAGE RULE (the human's actual question)

```
term_key  = the IDENTIFIER    lowercase ASCII, e.g. taskbar_doubao
label     = the DISPLAY text  any language,     e.g. 豆包 button
```

**The two are DIFFERENT COLUMNS.** The register records the IDENTIFIER; the
Chinese label stays in `target_template.label`.

**That is why there are "2 languages for the same thing": one is an identifier
and one is a label, and nothing said which was which.**

---

## FLOW

1. Ask: is this a **CATALOG** (it contains others) or an **ENTRY**?
2. A catalog is a ROOT term: `add_term(..., parent_term_id=None)`
3. An entry is a CHILD term: `add_term(..., parent_term_id=<catalog term_id>)`
4. Set `taxonomy_level` to the KIND of thing it is, or `'NA'`
5. Write the definition so it says what the thing **IS** and what it is **NOT**
6. Cite a real `path:line` — `add_term` **REFUSES** an unciteable citation
7. Read the catalog back: `terminology_catalog.catalog_tree(conn, root_key)`
8. Measure the gap: `terminology_catalog.unregistered_names(conn)`

---

## NOT TO DO

* **DO NOT** create a `catalog` / `subcatalog` table — they exist, and they are
  chat_center's. Reusing them puts two meanings in one table.
* **DO NOT** write `taxonomy_level_registry.is_active` by hand. MEASURED: it
  blocks nothing (`is_valid_level()` does not filter on it), and
  `taxonomy_level_registry` is IN the activation scope
  (`activation_gate.py:110`) and NOT in `NO_ACTIVATION_BECAUSE`. **A hand-flip
  would be the gate proving itself.**
* **DO NOT** delete or renumber a `terminology_register` row.
* **DO NOT** store a term with no definition or no citation.
* **DO NOT** accept a `definition_sha256` from a caller — the writer derives it.
* **DO NOT** render an unregistered name as blank. **It is a GAP.**

---

## REFUSALS (as data, in the contract)

| code | when | because |
|---|---|---|
| `MISSING_DEFINITION` | the definition is empty | a term with no definition is a word nobody defined |
| `MISSING_CITE_REF` | the citation is empty | no citation, no term |
| `UNCITEABLE_CITE_REF` | the citation is prose, not a path/command/`measured:` note | a citation that cannot be checked is not evidence |
| `UNKNOWN_PARENT` | `parent_term_id` names no row | a child of nothing is not in the catalog |
| `BAD_TAXONOMY_LEVEL` | the level is not declared | a layer nobody declared is a layer nobody can group by |

---

## THE MEASURED RESULT (2026-09-27)

```
terminology_register rows : 1461 -> 1482   (+21: 1 catalog root + 20 entries)
taskbar names registered  : 0 -> 20
unregistered taskbar names: 20 -> 0
catalog children          : 20
sweep phase `catalog`     : added (cap 25, sort_order 40)
```

**The `catalog` sweep phase measures the GAP** — it discovers names in
`target_template` that have NO term, so a name added tomorrow is caught by the
same phase without anyone remembering to look.

---

## WHY THIS RUNS FOREVER

`skill_registrar.register_all` discovers a skill by its `contract.yaml`, so this
declaration is what makes the rule **auto forever** — the human's words. The
`catalog` phase in `terminology_sweep_phase` is the recurring measurement.
