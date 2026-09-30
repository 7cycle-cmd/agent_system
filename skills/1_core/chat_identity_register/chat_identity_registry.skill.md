# Skill — chat_identity_register

**skill_key:** `chat_identity_register`
**layer:** 1_core
**status:** active

---

## WHY THIS SKILL EXISTS

The human (2026-09-25), on `http://127.0.0.1:18765/llm-tasks/chat_identity/recent`:

> "not all the VScode chat will submit to that auto?"
> "skill missing to help the automactic!! have the plan now"

**MEASURED, and the human is right on both counts:**

| | count |
|---|---|
| VS Code chat session files (`chatSessions/*.jsonl`) | **80** |
| `chat_identity_log` rows | **344** |
| `skill_register` rows | **61** |
| `skill_register` rows about chat identity | **0** |
| `*.skill.md` files | **57** |
| `*.skill.md` files about chat identity | **0** |

**Nothing submits a VS Code chat automatically.** MEASURED, every writer of
`chat_identity_log` is a CALLER:

```
register  chat_center          263
resolve   chat_center           52
resolve   api                   10
resolve   chat_center_backfill   9
register  api                    6
miss      verify                 1
register  session_register       1
register  verify                 1
resolve   verify                 1
```

There is NO watcher, NO poller, NO scheduled job. So a chat is registered ONLY
when something explicitly calls one of the two routes below — and until this
skill existed, no document said which routes those were.

---

## WHEN TO REGISTER

Register a conversation **after a turn that produced something worth keeping**:

- a DECISION the human made,
- a LESSON or a root cause,
- a DELIVERABLE (a file, a plan, a proof).

**Do NOT register every turn.** MEASURED: 196 of 344 rows were written in ONE
hour by a bulk backfill, which is why the table is mostly one session. A row per
turn makes the table unreadable and hides the turns that mattered.

---

## HOW — THE TWO ROUTES

### 1. Register the conversation (ONE row per turn)

```
POST /api/chat_center/register_conversation
{
  "session_id": "<the VS Code session UUID>",
  "turns": [
    {"role": "Question", "content": "..."},
    {"role": "Answer",   "content": "..."}
  ],
  "status": "draft"
}
```

MEASURED: this writes ONE `chat_center_message` row per turn and reuses
`create_chat_center_message`, so the one-row-per-turn rule stays in ONE place.

### 2. Link the environment (the `kind > product > surface` string)

```
POST /api/chat_identity/link_environment
{"session_id": "<uuid>", "apply": true}
```

MEASURED: the IDE is tried FIRST and the channel SECOND. For this session the
channel is `local_pc` (-> `Local PC`, `is_active=0`) while the IDE is `VS Code`
(-> `VS Code`, `is_active=1`). **A chat happens IN an IDE; the channel is only
where it was filed**, so the IDE is the more specific fact.

Without `apply:true` the route only REPORTS what it would resolve, so a caller
can look before it writes.

---

## THE STATUS VOCABULARY

```
draft        the DEFAULT. Written down, not yet sent anywhere.
pending      SENT to the logic generator, not yet answered.
progressing  the generator is RUNNING.
completed    the generator returned a format AND a status.
```

The four EXISTING values (`done`, `ask`, `progressive`, `QC`) stay: MEASURED,
`chat_center_message.status` already holds `done` 161 and `ask` 3, so dropping
them would orphan 164 rows.

---

## THE THREE REFUSALS

A caller that hits one of these gets a NAMED error code, never a silent pass:

| refusal | code | when |
|---|---|---|
| unknown status | `INVALID_STATUS` | the status is not in `WORKFLOW_STATUSES` |
| unknown LLM alias | (from `resolve_alias`) | the alias is not in `llm_model_alias` |
| no exact environment | `NO_EXACT_ENVIRONMENT` | no `working_environment.product` matches EXACTLY |

**The environment match is EXACT** (case-insensitive, whitespace-trimmed). No
substring, no prefix, no similarity score. A near match is REFUSED, because a
name join is this repo's recurring defect #4 ("a fuzzy match joins by luck").

---

## THE LLM COMES FROM THE REGISTER, NEVER FROM FREE TEXT

MEASURED, and this was a real defect: the table showed the free text `copilot`
while the register held `DeepSeek V4.1 Flash 0731`, and
`identity_register.llm_id` was NULL in ALL 55 rows.

The link is `identity_register.llm_id` -> `llm_model.name`, bridged by
`llm_model_alias` (MEASURED: VS Code reports `deepseek/deepseek-v4.1-flash`
while `llm_model` stores `deepseek/deepseek-v4-flash-0731` — NO exact match, so
the alias table is the bridge).

**NEVER read `chatSessions/*.jsonl` for the model.** MEASURED and recorded in
`identity_llm.py`: the human rejected that design — *"chatsession is totally
wrong design, will remove"*. A model must come from a REGISTER, not from another
program's log file.

---

## WHAT NOT TO DO

1. **Do NOT read `chatSessions/*.jsonl`** for the model or the content.
2. **Do NOT register every turn** — only turns that produced a decision, a
   lesson, or a deliverable.
3. **Do NOT pass a free-text model name.** Assign `llm_id` through the alias.
4. **Do NOT guess an environment.** A non-exact match is refused; store NULL.
5. **Do NOT write `chat_identity_log` directly.** The two routes own that.

---

## HOW TO VERIFY IT WORKED

```
GET /api/chat_identity/recent?limit=5
```

Each row must carry:

- `environment_display` = `kind > product > surface` (e.g. `IDE > VS Code > chat`)
- `llm` = the REGISTER's name (or `""` when unassigned — never the free text)
- `status` = one of the vocabulary above

MEASURED, the live payload after this skill's routes were used:

```
env=IDE > VS Code > chat   llm=DeepSeek V4.1 Flash 0731   status=draft
```

---

## CITE

- `skill_library_api.py:register_conversation` — the one-row-per-turn writer
- `skill_library_api.py:resolve_environment_id` — the EXACT-only resolver
- `skill_library_api.py:WORKFLOW_STATUSES` / `TURN_STATUSES` — the vocabulary
- `mouse_spot_helper.py:api_chat_center_register_conversation` — the route
- `mouse_spot_helper.py:api_chat_identity_link_environment` — the route
- `identity_llm.py` — why `chatSessions` is NOT read
