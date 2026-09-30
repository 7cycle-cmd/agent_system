---
name: task-done-verification
description: "Use when: about to report a task DONE, about to write a `done` / `success` status, about to conclude without evidence, or about to say a job is finished. Enforces that DONE means a VERDICT was READ from the run — never that the run looked fine — and that the five rings (files, reason, lesson, task link, proposal) are all reported with their units. Also use when you catch yourself concluding from a neighbour proof's health, from a pinned count, or from a run whose exit code you never read."
---

# Task Done Verification — DONE means a verdict was READ

## The rule

```
Rule 1: DONE means a VERDICT was READ from the run, never that it looked fine.
Rule 2: Every ring reports its own unit; a ring with no unit cannot be audited.
Rule 3: A refusal becomes a FACTOR and an EXAMPLE, not a silent skip.
Rule 4: A check asserts the property it NAMES, never a neighbour's health.
Rule 5: An empty result is refused unless a positive control proves the detector works.
```

**One sentence:** a `done` with no verdict read is a claim, not a result.

## Why this exists

THE USER (2026-09-26):

> "worker report job is done without evidence, now we can have the work done is
> done by 100% tracable and proofed evidence chain, how to it be skill for verify
> each task before worker tell job is done"

**MEASURED before it existed:** `task_instances` (1444 rows, 496 `success`),
`dev_task` (165) and `skill_task_queue` (263) each carry a `status` and **NONE**
carries an evidence column — and **NO** gate refused a `done` write.

## The five factors, and the measured failure behind each

| factor_key | rule | measured failure |
|---|---|---|
| `verdict_is_read` | assert the property you NAME, never a neighbour's health | `_proof_proof_gate_skip_and_timeout.py` QC-12 required `_proof_identity_llm.py` to be GREEN, so it went 63/1 whenever an UNRELATED proof regressed |
| `relation_not_count` | assert a RELATION, never a pinned live-population count | `_proof_binding_cite_source.py` pinned `checkable == rows == 62`, true once, and went red when the register grew — while the data was MORE correct |
| `prose_is_not_code` | strip a docstring as an AST NODE, never by deleting quotes | `_proof_identity_llm.py` L-07b read the module's own MEASUREMENT EVIDENCE as a hardcoded model name — 49/1 on a module that hardcodes nothing |
| `control_present` | every "detects X" check needs a POSITIVE CONTROL | `found nothing` and `the detector is dead` are indistinguishable without one |
| `green_is_measured` | DONE means a verdict was READ | `python X.py \| Select-String ...` reported an EMPTY result on a cp950 console while the process had exited 0 and the proof was GREEN |
| `punctuation_is_not_code` | assert a PROPERTY, never a punctuation in the text | `_proof_proof_run_layers.py` asserted `"layer_key)" in hsrc`; the INSERT is `... layer_key, value_type, ...)` so it NEVER appears |
| `control_constructs_its_precondition` | a control CONSTRUCTS its precondition | `_proof_evidence_generator.py` borrowed the live register and went RED when 7 kinds were legitimately activated |

## How to check it

```text
.\.venv\Scripts\python.exe skill_registrar.py --skill skill_task_done_verification
.\.venv\Scripts\python.exe skill_tdd_runner.py --skill skill_task_done_verification
```

## The contract

`skills/4_agent/skill_task_done_verification/contract.yaml` —
`contract_id: SKILL.TASK.DONE.VERIFY`, `probe_token: "TDV."`, 9 behaviour cases.
