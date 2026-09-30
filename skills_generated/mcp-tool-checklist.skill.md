<!-- GENERATED FILE — DO NOT EDIT BY HAND.
     Source: skill_factor_register (25 factors) + skill_factor_proof (measured)
     Generator: skill_factor_generator.py
     Regenerate: .\.venv\Scripts\python.exe skill_factor_generator.py --skill mcp-tool-checklist
     An edit here is LOST on the next run. Change the register instead. -->

# Skill: mcp-tool-checklist

**added by the evidence rule: a canonical .skill.md exists on disk**

| # | Factor | Rule Definition | Action | Metric | Value | Proof Artifact | State |
|---|---|---|---|---|---|---|---|
| 1 | Template Source & Ontology Binding | CRUD generation SSOT = Table Register + Field Register. All entities/relationships must match the Ontology definition. No hardcoded table structure. | Load snapshots of Table Register, Field Register and Ontology; freeze the hash version. | `pct` target `100` | - | `proof_mcp-tool-checklist_template_source_ontology_binding_79828c02` | UNMEASURED |
| 2 | Table Meta Input + Scoring Link | Read table metadata (module, capability, TTL, MCP, whitelist) and attach quality scoring weights. | Inject metadata + scoring weights into the code comment; generate the MCP / OpenAPI snippet. | `count` target `0` | - | `proof_mcp-tool-checklist_table_meta_scoring_link_299d05ce` | UNMEASURED |
| 3 | Field Meta Input + Ontology Property | Load field definition (type, nullable, lazy FK flag, regex, boundary) and map each field to an Ontology property. Separate legal from illegal NULL. | Generate payload validation logic + boundary unit tests; map each field to an ontology property. | `pct` target `100` | - | `proof_mcp-tool-checklist_field_meta_ontology_property_9eb1dd80` | UNMEASURED |
| 4 | Lazy FK Handling & Ontology Relation | No native DB FK constraint. Referential integrity is handled at the business layer. NULL = optional relationship, defined inside the Ontology. | Embed business-layer FK validation; skip the DB foreign key constraint. | `count` target `0` | - | `proof_mcp-tool-checklist_lazy_fk_ontology_relation_e4903102` | UNMEASURED |
| 5 | Environment Requirement + Register Check | Dependencies: SQLite, Table/Field Register, Schema Worker, QC. Pre-check Ontology / Scoring module registration and MCP whitelist status. | Check that all required services and registered skills are alive and enabled. | `pct` target `100` | - | `proof_mcp-tool-checklist_environment_register_check_3b560176` | UNMEASURED |
| 6 | TDD Unit Test + Eval Scoring | TDD rule: test cases are generated BEFORE business code. Positive / negative / boundary cases. | Auto-generate the unit test suite and eval cases; execute the eval and compute the test dimension score. | `score_0_100` target `100` | - | `proof_mcp-tool-checklist_tdd_unit_test_eval_6d2f412a` | UNMEASURED |
| 7 | Security SAST Scan Gate | Check SQL injection, sensitive data leak and privilege escalation. Security score is one dimension of Scoring. | Run a static SAST scan; block code on high/critical vulnerabilities. | `count` target `0` | - | `proof_mcp-tool-checklist_security_sast_gate_f0278d32` | UNMEASURED |
| 8 | QC Review Gate + Scoring Approval | QC worker validates CRUD logic against census data. Combine ontology consistency, test score and security score into a total. Approve only when total >= threshold. | QC executes the full test suite, compares against census data, and approves/rejects with a score. | `score_0_100` target `100` | - | `proof_mcp-tool-checklist_qc_review_scoring_approval_452f8668` | UNMEASURED |
| 9 | Schema Snapshot & Rollback + Ontology Version | Save a schema snapshot; bind the CRUD code version to the snapshot hash. Freeze the matching Ontology version and scoring weight. | Store the snapshot hash, trace_id, rollback script, ontology backup and scoring rule backup. | `pct` target `100` | - | `proof_mcp-tool-checklist_schema_snapshot_rollback_85818979` | UNMEASURED |
| 10 | Not To Do 1: No direct production execute | Never run generated CRUD code against production. Execution is delegated to the scheduler. Bypassing the ontology check is blocked. | Code output only; the scheduler holds execution permission; block any execution that skips the ontology check. | `count` target `0` | - | `proof_mcp-tool-checklist_ntd_no_direct_production_execute_c143c8e7` | UNMEASURED |
| 11 | Not To Do 2: No bypass Register / Ontology | Cannot modify a table/field definition bypassing the Table/Field Register. A new entity relationship must be registered into the Ontology. | Compare code against the register hash; block unregistered ontology relations. | `count` target `0` | - | `proof_mcp-tool-checklist_ntd_no_bypass_register_ontology_3373bd82` | UNMEASURED |
| 12 | Not To Do 3: No auto purge legal NULL | Do not batch delete legal business NULL. Changing ontology relationships requires a separate Check ID. | Protect legal NULL records; block a mass clean operation without a Check ID. | `count` target `0` | - | `proof_mcp-tool-checklist_ntd_no_auto_purge_legal_null_084031ae` | UNMEASURED |
| 13 | Not To Do 5: No hardcoded secrets | No secret / API key hardcoded in source. The secret scan result feeds into security scoring. | Scan the source code; fail if a hardcoded secret is found. | `count` target `0` | - | `proof_mcp-tool-checklist_ntd_no_hardcoded_secrets_ab803bc8` | UNMEASURED |
| 14 | Tag Definition Carries A Citation | A capability tag's definition must cite where its meaning comes from. An invented definition is a guess with a heading, and a tag with no meaning matches zero skills silently. | Require cite_ref on register_tag(); refuse a definition without one. | `count` target `0` | - | `proof_mcp-tool-checklist_tag_definition_has_cite_4dc69751` | UNMEASURED |
| 15 | Tag Is Not A Silent Duplicate | Registering a tag that already exists must not silently overwrite the existing definition. A clobber destroys a definition someone else wrote and the caller never learns. | Refuse a duplicate unless update=True is passed explicitly. | `count` target `0` | - | `proof_mcp-tool-checklist_tag_not_duplicate_3e9870ee` | UNMEASURED |
| 16 | Health Probe Is Side-Effect Free | A health probe must be SIDE-EFFECT-FREE and CHEAP. The default answer for an unproven tool is 'do not touch it', NOT 'it is probably fine'. A blacklist cannot keep up: `screen.snapshot` was probed as a 'health check' and produced 597 screen captures in bursts of 7 within one second, because the list named 8 of 59 tools. | For every tool a probe may call, PROVE it is read-only. A tool that cannot be proven read-only is marked unsafe. The safe set is an ALLOWLIST, so a new tool is unsafe until proven otherwise. | `count` target `0` | - | `proof_mcp-tool-checklist_probe_side_effect_free_609debdd` | UNMEASURED |

## Summary

| applicable | measured | PASS | FAIL | UNMEASURED |
|---|---|---|---|---|
| 16 | 0 | 0 | 0 | 16 |

**UNMEASURED is not a pass.** A factor with no proof row has not been measured, and an absent measurement is not a passing one.
