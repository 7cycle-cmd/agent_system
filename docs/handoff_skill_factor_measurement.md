# Handoff — skill factor measurement & the `applies_to` gate

**Status:** measurement DONE for `no_null_standard`; the `applies_to` tag source is
**BROKEN BY MY OWN FIX** and must be reverted before anything else.
**Gate:** never
**DB:** `agent.db` only
**Date:** 2026-09-21

---

## 1. What was asked, and what was delivered

The human asked for two things:

1. *"skill for template: First Principle to define factor and output must can audit
   by measured unit"*
2. *"開始量度（由一個 skill 開始填 proof）"* — start measuring, one skill first

Then: *"yes"* → measure the remaining 7 factors for `no_null_standard`.

**Delivered:**

| Item | Result |
|---|---|
| `factor_first_principle.py` | 5 ordered questions; a factor with no unit is NOT measurable |
| `measure_skill.py` | accepts a **COMMAND**, runs it, parses the number. A typed value is refused |
| `no_null_standard` proofs | **11 applicable / 9 measured / 8 PASS / 1 FAIL / 2 UNMEASURED / 1 invalidated** |
| Proofs | measure_skill 47/0 · skill_factor 56/0 · all 10 proofs green · pytest 208 passed |

---

## 2. THE RESULT (live, verified)

```
no_null_standard: 11 applicable / 9 measured / 8 PASS / 1 FAIL / 2 UNMEASURED / 1 invalidated

PASS  template_source_ontology_binding   100.0  python factor_first_principle.py --audit
PASS  field_meta_ontology_property       100.0  python _proof_p1_coverage.py
FAIL  lazy_fk_ontology_relation           49    python null_columns.py --classify
PASS  environment_register_check         100.0  python _proof_no_null.py
PASS  tdd_unit_test_eval                 100.0  python skill_tdd_runner.py SKILL.NO.NULL.STANDARD
PASS  schema_snapshot_rollback           100.0  python run_ontology_revision_tests.py
PASS  ntd_no_direct_production_execute     0    python _proof_p0_gates.py
PASS  ntd_no_bypass_register_ontology      0    python _proof_factor_first_principle.py
PASS  ntd_no_auto_purge_legal_null         0    python _proof_null_backfill.py
UNMEASURED table_meta_scoring_link             NO COMMAND EXISTS
UNMEASURED qc_review_scoring_approval          NO COMMAND EXISTS
```

### The FAIL is a real finding, not a broken test

`lazy_fk_ontology_relation` = **49**. 49 columns whose NAME promises a reference
(`_id` / `_ref` / `_key` / `_hash`) hold NULL. The name says it points at
something and it points at nothing. Target is 0, so **FAIL is correct**.

### The 2 with no command are REPORTED, never filled

- `table_meta_scoring_link` — `code_health.py verify-schema` prints a **boolean**
  `ok` per table, not a count of missing metadata fields. A boolean is not a count.
- `qc_review_scoring_approval` — `code_health.py report` prints a per-function
  `functional_score` that is **null for every function**, and no command combines
  ontology + test + security into one total. A null is not a score.

New state `NO_COMMAND` so a reader sees the factor **and why**, not a vanished row.

---

## 3. THE BLOCKER — my own fix is wrong, revert it first

### What I did

`skill_factor.applicable_factors` used to match `applies_to` against
`skill_register.description`. Measured: **0 of 48** descriptions look like a tag,
so `applies_to='code'` / `'crud'` matched NOTHING and **8 of 19 factors applied to
zero skills** — exactly the "decorative" factor the docstring warned about.

### What I changed it to (WRONG)

I pointed the tag source at `skill_register.taxonomy_path`.

**`taxonomy_path` is NOT a tag.** It is a canonical ontology path
(`module/task_center`, `channel/local_pc`, `db_field/code_register|register_id`)
that is **hard-validated against the ontology registry** by
`skill_contract_store.validate_taxonomy_path`. Reading it as a tag is a category
error — the same defect family as matching prose.

### What the correct source is NOT either

`skill_library_api.SKILL_CATALOG_FOLDERS` gives `core` / `db_schema` / `ui` /
`agent` / `qa`, derived from the `skills/<folder>/` path, and the code comment says
*"the skills/ folder structure is the authoritative catalog"*.

**But a catalog is a LOCATION, and `applies_to` is a CAPABILITY.** A
`2_db_schema` skill may generate CRUD; a `4_agent` skill may produce code. The two
vocabularies do not map. **Location is not capability.**

### The fix

Add a field that actually DECLARES a capability tag. See §4.

---

## 4. THE PLAN (not started)

### Phase 1 — revert the wrong change

1. `skill_factor.applicable_factors` — stop reading `taxonomy_path`. Keep
   `tag_source_empty()` (it correctly reports a dead match).
2. `_proof_skill_factor.py` section C/G — stop using `taxonomy_path='crud'` as a
   test fixture.

### Phase 2 — add a real tag field (additive)

3. `skill_register` gains `capability_tags TEXT NOT NULL DEFAULT 'NA'` (follows the
   `no_null` standard: empty is `NA`, never NULL). DDL in **two** places:
   - `db_schema.SKILL_REGISTER_DDL` (~line 1348) — builds a fresh DB
   - `split_skill_register.SKILL_REGISTER_DDL` (~line 87) — the migration
4. `applicable_factors` reads `capability_tags`, comma-separated, so one skill can
   be both `code` and `crud`.

### Phase 3 — derive a PROPOSAL from the catalog (not the truth)

5. New module `skill_capability_tag.py`: derive the catalog from the
   `skills/<folder>/` path (copy the existing pattern in
   `skill_library_api.sync_skill_library`), then **propose** a tag:
   - `2_db_schema` → propose `crud` (field/table register — closest to CRUD)
   - `4_agent` → propose `code` (`skill_worker_code_builder` lives here)
   - everything else → propose `NA` (no evidence, so no proposal)
6. **Report the CONSEQUENCE of each proposal**: which factors would newly apply.
   A wrong tag silently changes the applicable set — the exact defect fixed today.

### Phase 4 — a human approves, then it is written

7. Emit `capability_tag_proposal.tsv` (skill_key / catalog / proposed tag / factors
   it would unlock / reason).
8. **Do NOT auto-write.** Route it through the existing human-decision path
   (`skill_human_decision_submit.py` gate pattern, same as `decision_answers.tsv`).

### Phase 5 — measure

9. After writing: how many skills carry a tag, how many of the 8 scoped factors
   actually apply, and how `no_null_standard`'s `applicable` count changes.

---

## 5. Defects found by RUNNING (all mine, all this round)

1. **The unit caught a ratio above 100%.** `field_meta_ontology_property` came back
   **181.8** — impossible for a `pct`. Cause: `_proof_p1_coverage.py` printed a
   **hard-coded `/11`** while the loop iterated every contract (there are 20). The
   denominator is now COUNTED. Same family as every hard-coded count this session.
2. **The plan drifted in BOTH directions at once.** 10 entries vs 11 applicable: 2
   entries were for factors that do NOT apply (`dry_run_before_apply` is crud-only,
   `ntd_no_hardcoded_secrets` is code-only) and 3 applicable factors were missing.
   Invisible, because the run just reports fewer rows. Fix: `plan_coverage()`.
3. **`applies_to` was DEAD, and dead silently.** See §3.
4. **The gate was missing at the write site.** `record_proof` never checked
   applicability, so `no_null_standard` carried a proof for
   `ntd_no_hardcoded_secrets` (applies_to='code') while `applicable_factors` said it
   did not apply. **The module contradicted itself.** Fix: `record_proof` refuses an
   inapplicable factor ("a measurement of the wrong object").
5. **An empty result needed a POSITIVE CONTROL.** `LEAKED!` never appearing is only
   a measurement if `BLOCKED` DID appear — otherwise 0 is indistinguishable from a
   check that never ran. New state `NO_POSITIVE_CONTROL`.
6. **Invalidation, not deletion.** The inapplicable proof must stop counting, but
   deleting it destroys the evidence the gate was missing. Added
   `invalidated_at` / `invalidated_reason` (NOT NULL DEFAULT 'NA'); the row stays,
   `skill_table` excludes it and REPORTS it; a fresh measurement revalidates. An
   invalidation with no reason is REFUSED.

---

## 6. Files

| Path | Role |
|---|---|
| `factor_first_principle.py` | 5 ordered questions; `assert_measurable`, `audit_factor`, `unit_subject` |
| `measure_skill.py` | `PLAN` (per skill: factor → command → regex → ratio mode), `run_command`, `parse_metric`, `plan_coverage`, `measure_skill` |
| `skill_factor.py` | `FACTORS` (19), `FACTOR_REGISTER_DDL`, `FACTOR_PROOF_DDL`, `applicable_factors`, `tag_source_empty`, `record_proof` (action-point gate), `invalidate_proof`, `skill_table`, `render_table`, `drift_report` |
| `skill_factor_generator.py` | writes the table form to `skills_generated/` |
| `null_columns.py` | `classify`, `summary` (now reports `by_category`), `backfill` |
| `_proof_p1_coverage.py` | **fixed**: denominator counted, not hard-coded |
| `_proof_measure_skill.py` | 47/0 — sections A–L |
| `_proof_skill_factor.py` | 56/0 — sections A–J |
| `db_schema.py` | `SKILL_REGISTER_DDL` (~1348) |
| `split_skill_register.py` | `SKILL_REGISTER_DDL` (~87) |
| `skill_library_api.py` | `SKILL_CATALOG_FOLDERS` (~1938), `sync_skill_library` (the folder→catalog pattern to copy) |

---

## 7. Verification commands

```powershell
.\.venv\Scripts\python.exe _proof_factor_first_principle.py   # 39/0
.\.venv\Scripts\python.exe _proof_measure_skill.py            # 47/0
.\.venv\Scripts\python.exe _proof_skill_factor.py             # 56/0
.\.venv\Scripts\python.exe _proof_skill_factor_generator.py   # 35/0
.\.venv\Scripts\python.exe _proof_null_backfill.py            # 33/0
.\.venv\Scripts\python.exe _proof_no_null.py                  # 35/0
.\.venv\Scripts\python.exe _proof_contract_ref.py             # 29/0
.\.venv\Scripts\python.exe _proof_skill_ref_migration.py      # 26/0
.\.venv\Scripts\python.exe _proof_skill_register_split.py     # 41/0
.\.venv\Scripts\python.exe _proof_human_decision_submit.py    # 57/0
.\.venv\Scripts\python.exe -m pytest -q                       # 208 passed
```

Live state:

```powershell
.\.venv\Scripts\python.exe measure_skill.py --skill no_null_standard
```

---

## 8. Laws this work established

```
A number without a unit cannot be audited.
  '0' is a pass for a vulnerability count and a FAIL for a test pass rate.

A value must come from a COMMAND that RAN.
  A typed number is not a measurement.

An absent measurement is not a passing one.
  UNMEASURED is never a pass.

An empty result needs a POSITIVE CONTROL.
  0 events is only a measurement if the detector proved it can find one.

A rule stated but not enforced at the WRITE SITE is not a rule.
  `applies_to` was reported by a reader and enforced nowhere.

Invalidate, never delete.
  Deleting a wrong proof destroys the evidence the gate was missing.

A plan must be compared against the applicable set.
  Otherwise it drifts in both directions at once, invisibly.
```

---

## 9. Still open (NOT part of this handoff)

- 47 other skills have 0 proofs.
- `taxonomy_path` is empty for all 48 skills (my own defect, separate).
- 6028 UNDECIDED NULLs in 117 columns — needs a per-column human decision.
- `decision_answers.tsv` 19 rows awaiting the human.
- The 46 `.skill.md` files vs ssot: 14 identical, **12 DIFFERENT**, 20 with no row.