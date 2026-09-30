---
description: "Use when: the user sends the F1 command. Emits the SESSION_ID / TASK_ID / CHAT_ID / MODEL header. Do NOT auto-prepend on every reply."
applyTo: "**"
---

# Reply Metadata Header (F1 command only)

## Trigger

Emit the metadata header ONLY when the user sends the **F1** command (e.g. types `F1` in chat). Do NOT prepend it automatically to any other reply.

```
SESSION_ID: {auto-generated UUID}
TASK_ID: {from previous message if present, otherwise leave empty}
CHAT_ID: {from previous message if present, otherwise leave empty}
MODEL: {current model name}
```

## Field Rules

| Field | Rule |
|-------|------|
| SESSION_ID | Generate a new UUID for every reply. Do not reuse previous values. |
| TASK_ID | Copy from the user's previous message if provided. If absent, leave blank. |
| CHAT_ID | Copy from the user's previous message if provided. If absent, leave blank. |
| MODEL | Use the EXACT model name currently serving this chat (shown in the chat input model selector, e.g. `Qwen: Qwen3.8 27B`). NEVER copy the example value — always read the real model from the current session. |

## Format

- Use the exact field names and colon separator.
- One field per line.
- No extra labels, prefixes, or markdown around the header.
- When emitted (on F1), the header appears at the very beginning of the reply.

## Example

```
SESSION_ID: 7f8d9e0a-1b2c-4d3e-8f4a-5b6c7d8e9f0a
TASK_ID:
CHAT_ID:
MODEL: Qwen: Qwen3.8 27B
```
