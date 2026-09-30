# skill_terminology_autopilot

**skill_key:** `skill_terminology_autopilot`
**taxonomy_path:** `module/task_center/skill_terminology_autopilot`
**capability:** `task_center.terminology_autopilot` (`capability_kind='code'`)
**contract:** `CAP.TASK_CENTER.TERMINOLOGY_AUTOPILOT`
**writes:** `terminology_autopilot.py`

## 5W1H

| | |
|---|---|
| **What** | Run the terminology CHECKLIST and drive the items a loop may legally fix, BOUNDED, with a NAMED stop reason. |
| **Why** | The user: *"be the coding writing and verify skill too, so it can auto forever"* and *"non stop until evidence proof for all terminology checklist can proof your work has done"*. |
| **Who** | A worker/agent with write access to the terminology register. |
| **Where** | `terminology_autopilot.py`; reads `terminology_register` through `terminology_alias` (the one door). |
| **When** | After any terminology write, and as the kicker's per-round step. |
| **How** | `--checklist` → `--run-once` → `--until-all-proven` (bounded) → `--json` for the proof. |

## NOT RESPONSIBLE FOR
* Writing code — that is `skill_worker_code_builder` (id 25).
* The done verdict — that is `skill_task_done_verification` (id 57).
* C1b / C6 / C7 — they are **REPORT** items; a loop must NOT touch them.

## HARD RULES
1. **A loop is BOUNDED.** `until_all_proven` cannot run forever; the stop reason is
   one of `ALL_CHECKLIST_ITEMS_PROVEN`, `NO_PROGRESS`, `MAX_ROUNDS_REACHED`,
   `BLOCKED_REPORT_ITEMS`.
2. **A REPORT item is never auto-fixed.** A worker decides it.
3. **An item must state what would make it RED** (`would_be_red_if`). A checklist
   containing an unprovable item is REFUSED, never reported ALL PROVEN.
4. **A crash is RED, not a pass.** A check that raises is reported with the exception
   named — a crash silently read as "ok" is how a detector goes blind.
5. **A rename is ONE `update_term` write**, moving the LABEL and keeping the old
   label as an alias. Never mint a second name-object or a compatibility view.
6. **A name collision is never resolved by deleting a meaning.** It needs a
   QUALIFIER named by a worker (measured: `route` = a declared CONNECTION AND an
   HTTP ENDPOINT, both correct).

## FLOW
1. `run_once()` measures every item; nothing is written unless `fix=True`.
2. The AUTO items are driven; the REPORT items are only observed.
3. Stop is named. `evidence_for()` is the ONE dict a proof reads, so the loop and
   the proof cannot disagree.
