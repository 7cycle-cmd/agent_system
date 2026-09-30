---
name: coding-standard
description: "Use when: writing or changing ANY code in this repo — a module, a function, a check, a proof, a factor, a register row. Enforces that a coding CLAIM is checkable: every rule is a registered factor in skill_factor_register carrying a MEASURED unit, every factor has a proof that measures it, and the skill is auto-loaded so the rule is read BEFORE the code is written. Also use when you catch yourself pinning a live-population count, asserting on a source text's punctuation, borrowing a live precondition for a control, comparing two counts without naming each side's population, measuring while another writer is active, or writing a lesson to /memories/repo/ and calling it filed."
---

# Coding Standard — a coding claim is a registered factor, not a habit

## The rule

```
Rule 1: Every rule is a REGISTERED factor in skill_factor_register.
Rule 2: Every factor carries a MEASURED unit that names a SUBJECT.
Rule 3: Every factor has a proof_prefix that names a file that EXISTS.
Rule 4: A lesson that lives only in /memories/repo/ was NEVER FILED.
Rule 5: The factor's metric is reported as a NUMBER, never as a claim.
```

**One sentence:** a coding rule with no registered factor and no proof is a habit,
and a habit cannot be audited.

## Why this exists

THE HUMAN (2026-09-27):

> "### 6 條新教訓（已入 repo memory）"
> "at skill for coding_*? in auto now?"

**MEASURED, and this is the defect:** the six lessons of 2026-09-27 reached
`/memories/repo/` and **NOTHING ELSE** — `skill_factor_register` **0** hits,
`terminology_register` **0** hits, `skill_lesson` **0** hits. `/memories/repo/` is
the agent's **private** note store, not the repo's SSOT, so the next worker cannot
read it.

**MEASURED, the second defect:** `coding_*` names **NOTHING**. `skill_register`
0 rows, `skill_factor_register` 0 rows, `mode_register` 0 rows. `coding_writing`
is a **MODE** string (`mode_register.py:68`), not a skill. The thing that DOES
exist is `skill_worker_code_builder`.

**MEASURED, the third defect:** the skill carried **5** factors and had **NO**
`contract.yaml`, so `skill_registrar.register_skill()` REFUSED it:

> "no contract.yaml — a skill with no declaration is NOT auto-registered
> (inventing a contract is fabricating a rule)"

## The six lessons, and the factor that carries each

| # | lesson | factor_key | skill |
|---|--------|-----------|-------|
| 1 | a check that pins a NUMBER a legitimate operation moves is STALE | `relation_not_count` | `skill_task_done_verification` |
| 2 | a check on SOURCE TEXT's punctuation is not a check on the CODE | `punctuation_is_not_code` | `skill_task_done_verification` |
| 3 | a control that depends on LIVE DATA is a control that EXPIRES | `control_constructs_its_precondition` | `skill_task_done_verification` |
| 4 | two "independent directions" must measure the SAME POPULATION | `same_population_compared` | `skill_worker_code_builder` |
| 5 | a kind IN USE but NOT REGISTERED is the ambiguity the register exists to prevent | `declared_before_used` | `skill_worker_code_builder` |
| 6 | a proof run concurrently with another proof measures a MOVING target | `no_concurrent_measurement` | `skill_worker_code_builder` |

## The measured failure behind each rule

* **`same_population_compared`** — `_proof_metric_kind_derive.py` R-30a reported
  `audit=[] derive=['valid_phone_number_judgment']` because
  `factor_first_principle.audit_register()` classified an INACTIVE factor
  separately while `metric_kind_derive.derive()` still counted EVERY row. Both
  were correct in isolation; the **comparison** was wrong. **A count of a TABLE is
  not a count of a LIST.**

* **`no_concurrent_measurement`** — while a background sweep of 87 proofs wrote
  `agent.db`, `_proof_two_part_verify.py` flapped `41/0 -> 40/1 -> 41/0` and
  `_proof_proof_run_layers.py` `22/0 -> 21/1 -> 22/0`, with **NO** `FAIL` line
  when run alone. **A result that flaps between runs is not a result.**

* **`punctuation_is_not_code`** — `_proof_proof_run_layers.py` asserted
  `"layer_key)" in hsrc`, i.e. that `layer_key` is the LAST column in the INSERT
  list. The INSERT is `... dim_key, layer_key, value_type,
  proof_run_register_id)`, so `layer_key)` **NEVER** appears and the check went
  RED while the INSERT correctly named `layer_key`.

* **`control_constructs_its_precondition`** — `_proof_evidence_generator.py`
  picked the subject kind with the MOST bindings and asserted the generator
  REFUSES it, on the stated assumption *"EVERY subject kind in the SSOT is
  currently is_active=0"*. That stopped being true when 7 kinds were legitimately
  activated, so the proof picked an ACTIVE kind and went RED on **CORRECT** work.

* **`relation_not_count`** — `_proof_binding_cite_source.py` pinned
  `checkable == rows == 62` and `distinct_cite_refs == 6`, both true once, and
  went red when the register grew — while the data was **MORE** correct.

* **`declared_before_used`** — `goal` and `purpose` named ONE concept,
  `chat_register` and `case_register` named ONE table, and `mouse_spot_helper.core`
  and `CP-S-00` named ONE thing — three synonyms with no trace.

## How to check it

```text
.\.venv\Scripts\python.exe _proof_coding_standard.py
.\.venv\Scripts\python.exe skill_registrar.py --skill skill_worker_code_builder
.\.venv\Scripts\python.exe skill_tdd_runner.py --skill skill_worker_code_builder
```

## The formula

```
coding rule -> factor -> metric_unit -> proof -> proof row -> MEASURED
```

Every link already exists in the repo; only the coding rows were missing.
