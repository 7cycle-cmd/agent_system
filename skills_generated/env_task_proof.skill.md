<!-- GENERATED FILE — DO NOT EDIT BY HAND.
     Source: skill_factor_register (25 factors) + skill_factor_proof (measured)
     Generator: skill_factor_generator.py
     Regenerate: .\.venv\Scripts\python.exe skill_factor_generator.py --skill env_task_proof
     An edit here is LOST on the next run. Change the register instead. -->

# Skill: env_task_proof

**registered from skill_prompt_ssot UNION skill_contract_template**

| # | Factor | Rule Definition | Action | Metric | Value | Proof Artifact | State |
|---|---|---|---|---|---|---|---|
| 1 | Template Source & Ontology Binding | CRUD generation SSOT = Table Register + Field Register. All entities/relationships must match the Ontology definition. No hardcoded table structure. | Load snapshots of Table Register, Field Register and Ontology; freeze the hash version. | `pct` target `100` | - | `proof_env_task_proof_template_source_ontology_binding_0065d040` | UNMEASURED |
| 2 | Table Meta Input + Scoring Link | Read table metadata (module, capability, TTL, MCP, whitelist) and attach quality scoring weights. | Inject metadata + scoring weights into the code comment; generate the MCP / OpenAPI snippet. | `count` target `0` | - | `proof_env_task_proof_table_meta_scoring_link_441c51e3` | UNMEASURED |
| 3 | Field Meta Input + Ontology Property | Load field definition (type, nullable, lazy FK flag, regex, boundary) and map each field to an Ontology property. Separate legal from illegal NULL. | Generate payload validation logic + boundary unit tests; map each field to an ontology property. | `pct` target `100` | - | `proof_env_task_proof_field_meta_ontology_property_ad398883` | UNMEASURED |
| 4 | Lazy FK Handling & Ontology Relation | No native DB FK constraint. Referential integrity is handled at the business layer. NULL = optional relationship, defined inside the Ontology. | Embed business-layer FK validation; skip the DB foreign key constraint. | `count` target `0` | - | `proof_env_task_proof_lazy_fk_ontology_relation_27e1dcfc` | UNMEASURED |
| 5 | Environment Requirement + Register Check | Dependencies: SQLite, Table/Field Register, Schema Worker, QC. Pre-check Ontology / Scoring module registration and MCP whitelist status. | Check that all required services and registered skills are alive and enabled. | `pct` target `100` | - | `proof_env_task_proof_environment_register_check_ff075481` | UNMEASURED |
| 6 | TDD Unit Test + Eval Scoring | TDD rule: test cases are generated BEFORE business code. Positive / negative / boundary cases. | Auto-generate the unit test suite and eval cases; execute the eval and compute the test dimension score. | `score_0_100` target `100` | - | `proof_env_task_proof_tdd_unit_test_eval_2cef96a5` | UNMEASURED |
| 7 | QC Review Gate + Scoring Approval | QC worker validates CRUD logic against census data. Combine ontology consistency, test score and security score into a total. Approve only when total >= threshold. | QC executes the full test suite, compares against census data, and approves/rejects with a score. | `score_0_100` target `100` | - | `proof_env_task_proof_qc_review_scoring_approval_eb706071` | UNMEASURED |
| 8 | Schema Snapshot & Rollback + Ontology Version | Save a schema snapshot; bind the CRUD code version to the snapshot hash. Freeze the matching Ontology version and scoring weight. | Store the snapshot hash, trace_id, rollback script, ontology backup and scoring rule backup. | `pct` target `100` | - | `proof_env_task_proof_schema_snapshot_rollback_92e0eb05` | UNMEASURED |
| 9 | Not To Do 1: No direct production execute | Never run generated CRUD code against production. Execution is delegated to the scheduler. Bypassing the ontology check is blocked. | Code output only; the scheduler holds execution permission; block any execution that skips the ontology check. | `count` target `0` | - | `proof_env_task_proof_ntd_no_direct_production_execute_340d0219` | UNMEASURED |
| 10 | Not To Do 2: No bypass Register / Ontology | Cannot modify a table/field definition bypassing the Table/Field Register. A new entity relationship must be registered into the Ontology. | Compare code against the register hash; block unregistered ontology relations. | `count` target `0` | - | `proof_env_task_proof_ntd_no_bypass_register_ontology_5c3ca65a` | UNMEASURED |
| 11 | Not To Do 3: No auto purge legal NULL | Do not batch delete legal business NULL. Changing ontology relationships requires a separate Check ID. | Protect legal NULL records; block a mass clean operation without a Check ID. | `count` target `0` | - | `proof_env_task_proof_ntd_no_auto_purge_legal_null_771b495a` | UNMEASURED |

## Summary

| applicable | measured | PASS | FAIL | UNMEASURED |
|---|---|---|---|---|
| 11 | 0 | 0 | 0 | 11 |

**UNMEASURED is not a pass.** A factor with no proof row has not been measured, and an absent measurement is not a passing one.
