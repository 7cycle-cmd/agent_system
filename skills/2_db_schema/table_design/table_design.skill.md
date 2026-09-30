---
name: table_design
description: Use when designing, reviewing, or auditing a database table — or when a table is suspected of mixing kinds of truth. Enforces the FIVE factors a table must satisfy (DB driven, id = primary + autoincrement, field value as much as it can, priority by id, never null / null = NA), and REPORTS the gap rather than migrating it.
---

# table_design — the FIVE factors a table must satisfy

## What this skill is for

A table is not a container for columns. It is a **shape that must satisfy five
factors**, and each factor is a rule that can FAIL. This skill states the five,
audits a table against them, and reports the gap.

The five factors are declared **ONCE**, as data, in `table_design.FACTORS`. The
skill's contract is **DERIVED** from them — the same shape `skill_5w1h.DIMENSIONS`
uses, and for the same reason: a rule stated in two places is a rule that can
disagree with itself.

## The five factors

| # | factor | the rule |
|---|--------|----------|
| 1 | `db_driven` | the table is registered in `db_table_registry`, and its columns in `db_field_registry`, so a worker READS the definition instead of guessing |
| 2 | `id_primary_autoincrement` | `INTEGER PRIMARY KEY AUTOINCREMENT`, so a row has a stable identity that does not depend on its content |
| 3 | `field_value_max` | **the table must not MIX kinds of truth** — see below |
| 4 | `priority_by_id` | order by `id`, or by an explicit `sort_order` when the order is a DECISION rather than an accident of insertion |
| 5 | `never_null_na` | an empty value is standardised to the explicit `NA`, so a surviving NULL always means the standardiser did not run |

## Factor 3 is the one that is easy to misread

"field value as much as it can" does **NOT** mean "every column has a value".
It means: **a field carries as much MEANING as it can only if it means exactly
ONE thing — which requires the table to be SPLIT.**

A table that mixes kinds of truth forces a column to mean different things in
different rows, and then no rule can be stated about it.

**How mixing is detected — MEASURED, never judged.** A table is MIXED when it has
BOTH:

1. a **discriminator** column — bare (`kind`, `type`, `status`, `mode`, `level`,
   `key`, `category`) or suffixed (`*_kind`, `*_type`, `*_status`, …), AND
2. a column that is **NULL for every row of at least one discriminator value AND
   non-NULL for at least one row of another**.

(2) is a fact about the rows, not a similarity score. `independent_review`
forbids scoring overlap, so the detector measures instead.

**A table's LAYER is a first-class property.** `audit_table` reports
`layer_column` and `is_layered`. A FLAT table is **not** an error — only mixing
is. The repo already has three layered tables of the same shape:
`terminology_register.parent_term_id`, `task_type_registry.parent_type_id`,
`industry_registry.parent_industry_id`.

## What this skill does NOT do

- It does **NOT** migrate a table. It REPORTS. A migration is a separate decision
  with its own risk, and the audit states the gap so a human can decide.
- It does **NOT** reimplement factor 5. `no_null.py` already implements it
  completely (`standardize_empty`, `assert_no_null`, `fk_columns`), and a second
  implementation would be two answers to one question.
- It does **NOT** judge whether a sparse column is a defect. The detector reports
  the signal; a reader decides.

## How to run it

```text
.\.venv\Scripts\python.exe table_design.py --audit
.\.venv\Scripts\python.exe table_design.py --audit --table dev_task
.\.venv\Scripts\python.exe table_design.py --factors
```

## Not to do

- DO NOT hand-write a second copy of the five factors. Derive from `FACTORS`.
- DO NOT add a factor without adding its audit branch — a factor that is declared
  but not measured is a claim.
- DO NOT turn the audit into a migration. It reports; it never alters.
- DO NOT treat a FLAT table as an error. Only MIXING is an error.
- DO NOT reimplement `no_null`. Call it.
