# PLAN MODE — capability_tag_qc

**Mode:** PLAN (read-only)
**IDE:** vscode_autopilot
**Checklist ID:** `capability_tag_qc_v1`
**Status:** DRAFT → freeze on human `APPROVE plan`
**Audit trace under QC:** `TAG-20260921-065543-31d1f602`

## 1. Task Boundary & Scope

### In scope
- QC the capability-tag submission that produced trace
  `TAG-20260921-065543-31d1f602`.
- Four modules: data layer, citation/reason evidence, proof suite, audit log.
- Emit a QC report (MD + JSON twin) with pass/fail, issues, review items,
  proof summary, audit verification, and QC worker identity.

### Out of scope
- Any change to `skill_register.capability_tags` (read-only QC).
- Any change to `capability_tag_registry` definitions.
- Any change to the append-only `skill_capability_tag_log`.
- The deferred `tag_submit_evidence` + SHA256 gate workset.
- `taxonomy_path` 48/48 NULL (separate defect).

## 2. Step-by-step implementation plan

1. Write `docs/plan_capability_tag_qc.md` (this file) + JSON twin
   `qc_evidence/plan_capability_tag_qc.json`.
2. Write `_qc_capability_tag.py` — the QC runner, read-only against `agent.db`.
3. Module 1: data-layer checks against `skill_register` + the proposal TSV.
4. Module 2: citation/reason checks on every filled row.
5. Module 3: run the 9 proofs + `pytest -q` as subprocesses; capture counts.
6. Module 4: audit-log checks on `skill_capability_tag_log`.
7. Emit `qc_evidence/qc_report_capability_tag_qc.md` + `.json`.
8. Report any check that CANNOT be verified as written, rather than passing it.

## 3. LOCKED QC CHECKLIST

> **FROZEN after approval.** AGENT MUST NOT add, remove, renumber, or rewrite items.

| ID | Verification criterion | Verification method |
|----|------------------------|---------------------|
| QC-01 | `capability_tags` distribution is `code` 9 / `NA` 39 | `SELECT capability_tags, COUNT(*) ... GROUP BY 1` |
| QC-02 | Zero NULL in `capability_tags` | `SELECT COUNT(*) ... WHERE capability_tags IS NULL` |
| QC-03 | `skills_with_a_tag` = 9, total skills = 48 | `sf.tag_source_empty(conn)` |
| QC-04 | No skill carries `crud` | `SELECT COUNT(*) ... WHERE capability_tags LIKE '%crud%'` |
| QC-05 | `code`'s 2 factors are reachable | union of `applicable_factors` over all skills |
| QC-06 | `crud`'s 6 factors remain unreachable | same union, intersected with the crud set |
| QC-07 | Every tag value is `code` / `crud` / `NA` | split on comma, compare to `registered_tags` |
| QC-08 | Every filled row has non-empty `decided_by` and `cite_ref` | parse the TSV, check the 11 filled rows |
| QC-09 | Every `cite_ref` matches `citation_discipline.is_citation` | call the shared predicate |
| QC-10 | The 2 `db_schema` rows cite `skill_field_register.py:67` | exact string compare |
| QC-11 | The 9 `code` rows each carry a valid citation | `is_citation` per row |
| QC-12 | `skill_worker_code_builder` is flagged for review | present in the review list |
| QC-13 | The 37 skipped rows remain `NA` and were not written | TSV blank + DB value `NA` |
| QC-14 | `_proof_capability_tag.py` is green | run it, parse `N passed / M failed` |
| QC-15 | The other 8 proofs are green | run each, parse the summary line |
| QC-16 | `pytest -q` reports 208 passed | run it, parse the last line |
| QC-17 | The proof no longer asserts an absolute world-state | source contains the DELTA note |
| QC-18 | `skill_capability_tag_log` holds 11 rows | `SELECT COUNT(*)` |
| QC-19 | Every log row carries proposal / before / after / decider / cite / ts / trace | column-by-column non-empty check |
| QC-20 | All 11 rows share trace `TAG-20260921-065543-31d1f602` | `SELECT DISTINCT trace_id` |
| QC-21 | The log is append-only (no UPDATE/DELETE in the module) | source scan of the submit module |
| QC-22 | The human's REASON is persisted and every override carries one | `decision_reason` column + per-skill coverage |
| QC-22a | The log carries a `decision_reason` column | `PRAGMA table_info` |
| QC-22b | The log marks `live` vs `retroactive` rows | `PRAGMA table_info` |
| QC-22c | Every OVERRIDE skill carries a `decision_reason` | per-skill coverage, not per-row |
| QC-22d | The reason coverage is REPORTED, not assumed | `scts.reason_coverage(conn)` |
| QC-22e | The 11 original rows were NOT rewritten | `recorded_as='live' AND decision_reason='NA'` == 11 |
| QC-23 | Failures are split by CAUSE (this change vs pre-existing) | verdict counts `fail_caused_by_this_change` |

**Freeze token:** `capability_tag_qc_v1` = items as approved.

## 3b. QC-22 CLOSURE (2026-09-21)

QC-22 was UNVERIFIABLE because `skill_capability_tag_log` had **no reason
column** — the TSV `reason` column is the RENDERER's reason, so why `crud` was
rejected lived only in the TSV and in chat.

**Closed by:**

1. `decision_reason TEXT NOT NULL DEFAULT 'NA'` + `recorded_as TEXT NOT NULL
   DEFAULT 'live'` on the log (additive ALTER for the existing DB).
2. Gate **G7**: `decision_reason` is MANDATORY when the answer DIFFERS from the
   proposal. An acceptance needs none — the proposal's own reason stands.
3. The TSV header renamed `reason` -> `proposal_reason` and gained
   `decision_reason` as a 4th answer column (9 -> 10 columns).
4. `--migrate-tsv` rewrote the file, PRESERVING all 11 answers and appending
   `decision_reason` BLANK — a migration must not invent a human reason.
5. `--record-reason` appended **2 retroactive rows** (trace
   `TAG-20260921-073508-5749b0f8`) carrying the human's stated reason, and
   changed **NO tag** (`tags_written: 0`).

The 11 original rows can NEVER gain a reason: the log is append-only, and an
UPDATE would destroy the record that the reason was missing. They are closed by
APPENDING marked rows — which is why `recorded_as` exists. Without it a
retroactive row is indistinguishable from a live one.

**Result:** QC-22a-e all PASS, 0 UNVERIFIABLE.

## 3c. QC VERDICT LOGIC (2026-09-21)

The verdict answers **ONE** question: *is THIS submission sound?* It is **not** a
claim that the whole repo is green.

| cause_class | Meaning | Effect on verdict |
|---|---|---|
| `submission_defect` | The root cause is THIS change: data mismatch, bad citation, missing audit row, tag-contract violation, empty required field | **reject** — return to fix, then re-submit |
| `legacy_system_issue` | Pre-dates this change: a stale proof baseline, an unrelated module's historical bug, an unrelated red test | **accept** + record in `backlog_items`; does NOT block |

**Verdict output shape:**

```json
{
  "verdict": "accept|reject",
  "check_id": "capability_tag_qc_v1",
  "trace_id": "TAG-20260921-065543-31d1f602",
  "failures": [
    { "cause_class": "submission_defect|legacy_system_issue",
      "description": "...", "cite_ref": "...", "severity": "high|medium|low" }
  ],
  "backlog_items": [],
  "summary": "..."
}
```

**Rules:**

- "All repo tests pass" is **NOT** the admission gate. That standard makes an
  unrelated red test block a correct change, which trains everyone to edit the
  number instead of asking whether the change was right.
- The QC's job is to **isolate what THIS change introduced**.
- Every `legacy_system_issue` carries a `cite_ref` that passes
  `citation_discipline.is_citation`, so the claim can be **checked** rather than
  trusted. This is the guard against the failure mode of the rule itself: a
  legacy issue in a module the submission *depends on* would be a real blocker
  mislabelled as legacy.
- The exit code follows the verdict: non-zero only for a `submission_defect`, so
  CI fails on a real defect but not on a legacy one.

**Current result:** `verdict: accept` — 32 PASS, 0 `submission_defect`,
2 `legacy_system_issue` (backlogged), 0 UNVERIFIABLE.

## 4. Allowed File Allowlist

| Path | Action |
|------|--------|
| `docs/plan_capability_tag_qc.md` | create |
| `qc_evidence/plan_capability_tag_qc.json` | create |
| `_qc_capability_tag.py` | create |
| `qc_evidence/qc_report_capability_tag_qc.md` | create |
| `qc_evidence/qc_report_capability_tag_qc.json` | create |

## 5. Allowed Terminal Command List

```text
.\.venv\Scripts\python.exe _qc_capability_tag.py
.\.venv\Scripts\python.exe _proof_capability_tag.py
.\.venv\Scripts\python.exe _proof_skill_factor.py
.\.venv\Scripts\python.exe _proof_measure_skill.py
.\.venv\Scripts\python.exe _proof_skill_register_split.py
.\.venv\Scripts\python.exe _proof_null_backfill.py
.\.venv\Scripts\python.exe _proof_no_null.py
.\.venv\Scripts\python.exe _proof_skill_factor_generator.py
.\.venv\Scripts\python.exe _proof_factor_first_principle.py
.\.venv\Scripts\python.exe _proof_contract_ref.py
.\.venv\Scripts\python.exe -m pytest -q
```

## 6. Forbidden actions list

1. Editing `skill_register` data directly — all tag changes go through
   `skill_capability_tag_submit.py`.
2. Deleting or modifying rows in the append-only `skill_capability_tag_log`.
3. Removing the `skill_worker_code_builder` review flag.
4. Changing the `crud` / `code` definitions in `capability_tag_registry`.
5. Editing any path outside the allowlist.
6. Reporting a check as PASS when it could not be verified.

## 7. Stop. Wait for human approval.

```text
APPROVE plan
APPROVE plan with changes: <delta>
REJECT, re-plan
```

No code changes in PLAN MODE.

## 8. Machine-readable twin (JSON)

**Required every run:** `qc_evidence/plan_capability_tag_qc.json`
MD (this file) stays the human presentation. JSON is the backend-ingestible copy.
Do not disagree with the locked checklist or allowlists.