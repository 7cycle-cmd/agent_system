# skill_role_derive

**skill_key:** `skill_role_derive`
**taxonomy_path:** `module/task_center/skill_role_derive`
**capability:** `task_center.role_derive` (`capability_kind='code'`)
**contract:** `CAP.TASK_CENTER.ROLE_DERIVE`
**writes:** `role_derive.py`

## 5W1H

| | |
|---|---|
| **What** | Derive a worker's ROLE from DECLARED evidence, or REFUSE with a named reason. Never guess. |
| **Why** | The user: *"6 個 worker 全部 NO_ROLE — can by evidence to get it by logic generation?"* and *"be skill, when new role happen can be auto forever by API"*. |
| **Who** | A worker/agent registering a NEW worker, or auditing the existing ones. |
| **Where** | `role_derive.py`; reads `worker_register`, `capability_registry`, `capability_kind_registry`. |
| **When** | On every new worker registration, and as the kicker's per-round step. |
| **How** | `--derive-all` → `--needs-evidence` → `--run --assign` (bounded) → `--json` for a proof. |

## NOT RESPONSIBLE FOR
* The role VOCABULARY — that is `skill_role_right_register`.
* Writing a role: it CALLS `role_right_register.declare_role`, the ONE write path.
* Deciding policy: it derives from evidence and REPORTS what evidence is missing.

## HARD RULES
1. **DERIVE from the REGISTER, never from a description.** MEASURED: the user's own
   example, `vscode is the code writing software`, is NOT what the register says —
   the term is *"The IDE that owns the conversation … it qualifies the conversation
   so it cannot be read as the chat system's"* (`mode_attest.py:258`), and its
   `physical_path` is `'vscode'`, not a file. A description-following rule would
   have produced `writer` for a term about CONVERSATION IDENTITY.
2. **A derivation needs a RULE and a CITATION.** No rule, no role.
3. **Refuse when evidence is absent.** An unresolved `capability_ref` is
   `NO_CAPABILITY_EVIDENCE`, not an assumption.
4. **`verifier` is NOT derivable yet.** MEASURED: `gate_ref` is NA on every
   capability row, so its evidence ("its output GATES another worker") does not
   exist. It is REPORTED as missing evidence.
5. **Fail CLOSED.** An undecidable worker stays `NO_ROLE`; `needs_evidence()` names
   what would settle it, so a gap is a WORK ITEM.
6. **The runner is BOUNDED** with a NAMED stop: `ALL_DERIVED`, `NO_PROGRESS`,
   `MAX_ROUNDS_REACHED`, `BLOCKED_NEEDS_EVIDENCE`.

## FLOW
1. Read the DECLARED kind vocabulary from `capability_kind_registry`.
2. Map the worker's `capability_kind` through `KIND_TO_ROLE` (each rule names its
   evidence).
3. If nothing decides, REFUSE and record what evidence is needed.
4. Assign ONLY a derived role, through the cited ONE write path.
5. Stop with a named reason.

## API
`register(app)` mounts `GET /api/role/derive-all`, `/api/role/derive`,
`/api/role/needs-evidence`, `/api/role/run` on the same `APIRouter` shape
`terminology_api.py` and `object_door.py` already use.
