# Prompt Analyze + Task Center IDs

## Surfaces

| Port | Path | Role |
|------|------|------|
| 18765 | `/llm-tasks`, `/prompt-analyze` | Task center picker + chat box + **Improve** / **Analyze** |
| 8766 | `/tasks?task_id=N` | Edit `writer` / `session_id`; **Open Prompt Analyze** deep-link |

## Required identity trio

Canonical markers in prompts:

```text
writer: <name>
task_id: <dev_task.id>
session_id: <session>
```

- **Improve** requires all three from the selected Task Center task (400 if missing).
- **Analyze** always checks the pasted text for the trio, reports Task Center context completeness, and adds LLM quality notes.

## APIs (18765)

- `GET /api/task-center/tasks`
- `GET /api/task-center/tasks/<id>`
- `POST /api/prompt/improve` `{ prompt, task_id, writer, session_id, model? }`
- `POST /api/prompt/analyze` `{ prompt, task_id?, writer?, session_id?, model? }`

History rows use `task=prompt_improve|prompt_analyze` in `mouse_spot_llm_tasks.json`.

## APIs (8766)

- `POST /api/tasks` accepts optional `writer`, `session_id`
- `POST /api/tasks/{id}/context` `{ writer?, session_id? }`

## Schema

`dev_task.writer`, `dev_task.session_id` (additive migrate via `ensure_task_center_schema`).

## Bridge

18765 reads `agent.db` server-side (no browser CORS to 8766).
