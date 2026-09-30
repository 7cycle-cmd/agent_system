<!-- GENERATED FILE — DO NOT EDIT BY HAND.
     Source: skill_factor_register (37 factors) + skill_factor_proof (measured)
     Generator: skill_factor_generator.py
     Regenerate: .\.venv\Scripts\python.exe skill_factor_generator.py --skill condition_based_waiting
     An edit here is LOST on the next run. Change the register instead. -->

# Skill: condition_based_waiting

**registered from skill_prompt_ssot UNION skill_contract_template**

| # | Factor | Rule Definition | Action | Metric | Value | Proof Artifact | State |
|---|---|---|---|---|---|---|---|
| 1 | Template Source & Ontology Binding | CRUD generation SSOT = Table Register + Field Register. All entities/relationships must match the Ontology definition. No hardcoded table structure. | Load snapshots of Table Register, Field Register and Ontology; freeze the hash version. | `pct` target `100` | - | `proof_condition_based_waiting_template_source_ontology_binding_6a22be48` | UNMEASURED |
| 2 | Table Meta Input + Scoring Link | Read table metadata (module, capability, TTL, MCP, whitelist) and attach quality scoring weights. | Inject metadata + scoring weights into the code comment; generate the MCP / OpenAPI snippet. | `count` target `0` | - | `proof_condition_based_waiting_table_meta_scoring_link_1e6c9c3e` | UNMEASURED |
| 3 | Field Meta Input + Ontology Property | Load field definition (type, nullable, lazy FK flag, regex, boundary) and map each field to an Ontology property. Separate legal from illegal NULL. | Generate payload validation logic + boundary unit tests; map each field to an ontology property. | `pct` target `100` | - | `proof_condition_based_waiting_field_meta_ontology_property_3a928365` | UNMEASURED |
| 4 | Lazy FK Handling & Ontology Relation | No native DB FK constraint. Referential integrity is handled at the business layer. NULL = optional relationship, defined inside the Ontology. | Embed business-layer FK validation; skip the DB foreign key constraint. | `count` target `0` | - | `proof_condition_based_waiting_lazy_fk_ontology_relation_b4425495` | UNMEASURED |
| 5 | Environment Requirement + Register Check | Dependencies: SQLite, Table/Field Register, Schema Worker, QC. Pre-check Ontology / Scoring module registration and MCP whitelist status. | Check that all required services and registered skills are alive and enabled. | `pct` target `100` | - | `proof_condition_based_waiting_environment_register_check_42e35657` | UNMEASURED |
| 6 | TDD Unit Test + Eval Scoring | TDD rule: test cases are generated BEFORE business code. Positive / negative / boundary cases. | Auto-generate the unit test suite and eval cases; execute the eval and compute the test dimension score. | `score_0_100` target `100` | - | `proof_condition_based_waiting_tdd_unit_test_eval_64f5862e` | UNMEASURED |
| 7 | QC Review Gate + Scoring Approval | QC worker validates CRUD logic against census data. Combine ontology consistency, test score and security score into a total. Approve only when total >= threshold. | QC executes the full test suite, compares against census data, and approves/rejects with a score. | `score_0_100` target `100` | - | `proof_condition_based_waiting_qc_review_scoring_approval_9682e0d5` | UNMEASURED |
| 8 | Schema Snapshot & Rollback + Ontology Version | Save a schema snapshot; bind the CRUD code version to the snapshot hash. Freeze the matching Ontology version and scoring weight. | Store the snapshot hash, trace_id, rollback script, ontology backup and scoring rule backup. | `pct` target `100` | - | `proof_condition_based_waiting_schema_snapshot_rollback_a5bfdb37` | UNMEASURED |
| 9 | Not To Do 1: No direct production execute | Never run generated CRUD code against production. Execution is delegated to the scheduler. Bypassing the ontology check is blocked. | Code output only; the scheduler holds execution permission; block any execution that skips the ontology check. | `count` target `0` | - | `proof_condition_based_waiting_ntd_no_direct_production_execute_1a6496f9` | UNMEASURED |
| 10 | Not To Do 2: No bypass Register / Ontology | Cannot modify a table/field definition bypassing the Table/Field Register. A new entity relationship must be registered into the Ontology. | Compare code against the register hash; block unregistered ontology relations. | `count` target `0` | - | `proof_condition_based_waiting_ntd_no_bypass_register_ontology_76e00630` | UNMEASURED |
| 11 | Not To Do 3: No auto purge legal NULL | Do not batch delete legal business NULL. Changing ontology relationships requires a separate Check ID. | Protect legal NULL records; block a mass clean operation without a Check ID. | `count` target `0` | - | `proof_condition_based_waiting_ntd_no_auto_purge_legal_null_b21835eb` | UNMEASURED |
| 12 | No fixed sleep in automation code | A fixed sleep is wrong in both directions: too short is flaky, too long is slow, and worst it expires SILENTLY and lets the next step run against a UI that never became ready. | Count `sleep(` / `time.sleep(` calls in automation paths and require a condition wait with a timeout that fails loudly. | `count` target `0` | - | `proof_condition_based_waiting_cbw_no_fixed_sleep_c4b1a7c0` | UNMEASURED |
| 13 | A repeated action carries a cooldown, and a suppressed run is recorded | A repeated action with NO condition is the SAME failure as a fixed sleep, in mirror image: the sleep expires silently, the repeat fires silently. Measured 2026-09-21: `helper_watchdog` has 4 `take_shot=True` sites on a 15-second loop with NO cooldown, so a crash loop captures 4 screenshots per minute, each joining a 2-second thread and re-sorting all 40 files. The DISK is bounded (MAX_SNAPS=40) — what is unbounded is the CPU churn during the exact period the machine is already struggling. | Give every repeating action a per-KEY cooldown (per event KIND, so one event cannot steal another's evidence), and RECORD the suppression with its reason. A silent skip is indistinguishable from 'there was nothing to capture'. | `count` target `0` | - | `proof_condition_based_waiting_cbw_repeated_action_has_rate_limit_3a11e3eb` | UNMEASURED |

## Lessons (what this skill learned)

| # | Lesson | Root Cause | Suggested Fix | Cite | Status |
|---|---|---|---|---|---|
| 1 | A repeated action with NO cooldown is the SAME defect as a fixed sleep, in mirror image: the sleep expires silently, the repeat fires silently. Both are wrong in both directions, and both hide the problem. The cooldown must be PER-KEY (per event kind), and a suppressed run must be RECORDED with its reason. | `helper_watchdog.record_event(take_shot=True)` captured unconditionally at 4 call sites on a 15-second loop. A crash loop therefore captured 4 screenshots per minute, each joining a 2-second thread and re-sorting all 40 files. The DISK was bounded (MAX_SNAPS=40) — the unbounded part was CPU churn during the exact period the machine was already struggling. | Give every repeating action a per-KEY cooldown, so one event family cannot steal another's evidence, and record the suppression. A silent skip is indistinguishable from 'there was nothing to capture'. | `helper_watchdog.py:892` | draft |

## Summary

| applicable | measured | PASS | FAIL | UNMEASURED |
|---|---|---|---|---|
| 13 | 0 | 0 | 0 | 13 |

**UNMEASURED is not a pass.** A factor with no proof row has not been measured, and an absent measurement is not a passing one.
