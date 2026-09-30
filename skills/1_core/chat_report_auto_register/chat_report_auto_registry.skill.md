# Skill — chat_report_auto_register

**skill_key:** `chat_report_auto_register`
**layer:** 1_core
**status:** active

---

## WHY THIS SKILL EXISTS

The human (2026-09-28), verbatim:

> "why coding quality report task, and task still not show at
> http://127.0.0.1:18765/llm-tasks/conversation/list"
> "yes, do it now"
> "corn report need to auto with session ID（ｉｄｅｎｔｉｔｙ）ｔｏｏ"
> "ｉｔ　ｓｈｏｕｌｄ　ｂｅ　ｓｋｉｌｌ　ｆｏｒ　ａｕｔｏ　ｆｏｒｅｖｅｒ"

**MEASURED, and the human is right:** a REPORT is written to a **FILE**
(`qc_evidence/agent_log_<task_id>.md`) and the conversation list reads a
**TABLE** (`chat`, `mouse_spot_helper.py:13964`). **Nothing connects the two.**

MEASURED for the CODE.QUALITY session `d01a8339-a222-45d5-8c9c-f68f91c241b6`:

| endpoint | result |
|---|---|
| `GET /api/chat_center/history?session_id=d01a8339-…` | **`total: 0`** |
| `GET /api/chat_identity/recent` | **0 rows** |
| `GET /api/conversation_center/chats?q=d01a8339` | **0 chats** |

So the report existed on disk and the list could not see it.

---

## WHEN TO USE IT

After a task has produced its report (`qc_evidence/agent_log_<task_id>.md`).
The report is the AGENT deliverable (`scripts/plan_gate.py:832`), so this runs
**once per task**, not once per turn.

---

## HOW — ONE COMMAND

```text
.\.venv\Scripts\python.exe chat_report_register.py --task CODE.QUALITY
.\.venv\Scripts\python.exe chat_report_register.py --task CODE.QUALITY --apply
.\.venv\Scripts\python.exe chat_report_register.py --all --apply
```

Without `--apply` it only REPORTS what it would write, so a caller can look
before it writes.

---

## THE THREE THINGS IT REFUSES TO DO

1. **It does not TYPE a session id.** The id is READ from the plan's own
   `**Session:**` line, using the SAME regex the gate uses
   (`scripts/plan_gate.py:365`). A typed id is a claim; a read id is a fact.
2. **It does not AUTHOR a turn.** The turns are the report's own `## ` sections,
   in order. A summary written here would be a second, drifting copy.
3. **It does not write `chat` or `chat_center_message`.** Both have exactly ONE
   writer (`chat_level.ensure_chat` at `chat_level.py:150`,
   `skill_library_api.create_chat_center_message` at `skill_library_api.py:1729`),
   and this skill DELEGATES to them.

---

## THE ORDER IS FORCED BY THE DATA

`chat_center_message.chat_id` holds the **CONVERSATION** id (`chat_main.id`),
**not** the chat id. MEASURED: session `b1664979-…` has `chat_main.id=71` and its
turn carries `chat_id=71`, while its CHAT is `chat_id=68`.

So the order is:

1. **the conversation + the turns** — `register_conversation` (this also mints
   the `chat_main` row the link needs);
2. **the chat** — `chat_level.ensure_chat` (it REFUSES a blank title, and the
   task id IS the title);
3. **the link** — `chat_level.link_conversation` (it REFUSES a conversation that
   does not exist, which step 1 has just guaranteed).

---

## THE REFUSALS

A caller that hits one gets a NAMED code, never a silent skip:

| refusal | code | when |
|---|---|---|
| no report | `REPORT_MISSING` | `qc_evidence/agent_log_<task_id>.md` does not exist |
| no plan | `PLAN_MISSING` | `qc_evidence/plan_<task_id>.md` does not exist |
| no session line | `NO_SESSION` | the plan declares no `**Session:**` line |
| bad session | `BAD_SESSION` | the declared session is not a canonical UUID |
| no sections | `NO_SECTIONS` | the report has no `## ` section |

**A silent skip would make "nothing to register" and "the reader is broken"
indistinguishable** — the defect this repo keeps paying for.

---

## AUTO FOREVER IS A STATE TEST, NOT A COUNTER

The rule already exists in this repo
(`skills/4_agent/skill_worker_auto_loop/contract.yaml`):

> each pass must produce a **NEW DISTINCT CODE STATE**; when no new state is
> producible, the loop **STOPS**.

So `--all` registers every report that has no conversation row and then **STOPS**,
naming how many remained. A re-run is a no-op because the identity is
`(session_id, task_id)`.

**There is no `run_forever()` here, deliberately.** A count-based loop would
promote by repetition, which is why this repo has no `run_forever()` anywhere.

---

## WHAT NOT TO DO

1. **Do NOT write `chat` directly** — `chat_level.ensure_chat` is the ONE writer.
2. **Do NOT write `chat_center_message` directly** — `create_chat_center_message`
   is the ONE writer.
3. **Do NOT type a session id** — it is READ from the plan.
4. **Do NOT author a turn** — the turns are the report's own sections.
5. **Do NOT register a session whose report does not exist.**
6. **Do NOT write a `run_forever()`** — the loop stops on a STATE test.

---

## HOW TO VERIFY IT WORKED

```text
GET /api/conversation_center/chats?q=<task_id>          -> the chat appears
GET /api/chat_center/history?session_id=<session_id>    -> the turns appear
```

MEASURED after the first run (task `CODE.QUALITY`):

```text
chat_id=70  chat_key=chat:report:CODE.QUALITY  title=CODE.QUALITY  turns=36
```

---

## A DEFECT THIS SKILL FOUND (and the fix it forced)

The list counted turns with `m.chat_id = c.chat_id`, which is the **wrong
population** — `chat_center_message.chat_id` is the CONVERSATION id. MEASURED:
the report showed `turns: 1` for a chat whose session holds **36** turns, because
one unrelated row merely SHARED THE NUMBER 70.

The fix is in `mouse_spot_helper.py:13910` — the count is now by **SESSION**,
through the chat's own conversations:

```sql
(SELECT COUNT(*) FROM chat_center_message m
  WHERE m.session_id IN (
        SELECT cm2.session_id FROM chat_main cm2
         WHERE cm2.chat_id = c.chat_id))  AS turns
```

A count of a TABLE is not a count of a LIST; a count by an AMBIGUOUS id is not a
count of anything.
