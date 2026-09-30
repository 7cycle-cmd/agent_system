---
name: terminology_register
description: "A name must be a REGISTERED term before it is used. Use when naming a table, column, API route, file, module, factor or skill; when reusing a word that already names something else; when two systems share a word; or when a report is about to be marked done while using an unregistered name."
task_id: TERMINOLOGY.NAMING.SKILL
artifacts:
  - terminology_register.skill.md
---

# terminology_register — no name without a registered term

**Goal:** 一個名**未註冊**就唔可以用。名唔係字串，係一個**有定義、有引用、可分解**嘅結構。

Executable: `terminology_registry.py` · Table: `terminology_register`
Proof: `_proof_terminology_naming.py` · Pointer: `.github/skills/terminology-register/SKILL.md`

## The user's requirement (2026-09-23)

> "too easy to have name mis-understand problem, you need to register at
>  terminology_register!!!"
> "so wrong name can be applyed to qc skill to help proofed your mistake before
>  report done"
> "chat system is chat system, vscode > chat > conseraction is another system"
> "terminology_register is very import"
> "and how to it by skill to help worker auto to know they need to have this
>  action! not ask that by me again and again"

## Environment

- `terminology_register` exists and is the SSOT for a term + its definition
  (`db_schema.py:1364-1403`).
- MEASURED 2026-09-23: the register held **0 rows** — it existed and had never
  been used, so a name collision was invisible.
- `terminology_registry.add_term()` REFUSES a term with no `definition` or no
  `cite_ref`.
- `definition_sha256` is DERIVED by the writer, never accepted from the caller.
- The taxonomy is EMBEDDED (`taxonomy_level` / `taxonomy_path` /
  `entity_ref_key`) — no second catalog table.
- `is_active` DEFAULTS TO 0: a term is UNPROVEN until a 100-run proves it.

## Purpose

Make a name CHECKABLE before it is used, so a reader cannot pick the wrong
system's meaning. A term that was never registered is an INVENTED WORD.

## Flow

1. **Before naming anything**, ask: does a term for this concept already exist?
   `terminology_registry.list_terms(conn)` / `terms_for_entity(conn, key)`.
2. If it exists, USE IT. Do not invent a synonym.
3. If it does not, `add_term()` with a `definition` and a `cite_ref`.
4. If the concept is a COMPOSITE, register the composite first, then its parts
   with `parent_term_id` — the structure is STORED, not inferred.
5. If two systems share a word, register BOTH as top-level SIBLINGS, and make
   each definition STATE the difference.
6. Check with `assert_named(conn, term)` before the name reaches a report.

## Not To Do

- Do NOT name a table/column/route/file before registering the term.
- Do NOT register a term with no `definition` or no `cite_ref`.
- Do NOT make one system's term the PARENT of another's — that asserts they are
  one system.
- Do NOT invent a synonym for a registered term.
- Do NOT mark a report done while it uses an unregistered name.
- Do NOT rename a pre-existing table as a side effect of naming a new one.

## The worked example (the collision this skill exists to prevent)

`conversation_env_log.chat_main_id` FK -> `chat_main(id)`, and `chat_main` is the
**chat system's** table. The column name told a reader the value belonged to the
chat system, when it identifies a **VS Code conversation**.

Registered terms:

| term | kind | parent | definition says |
|---|---|---|---|
| `chat_system` | entity | (top-level) | the `chat_center_message` / `chat_identity_log` / `chat_register` set |
| `vscode_conversation` | entity | (top-level) | one `chatSessions/<id>.jsonl`; NOT the chat system |
| `vscode` | qualifier | `vscode_conversation` | the IDE that owns the conversation |
| `conversation` | part | `vscode_conversation` | one exchange thread in one session file |
| `vscode_session_id` | entity | (top-level) | VS Code's own id, not a chat-system id |
| `vscode_env_checklist` | entity | (top-level) | the per-conversation environment checklist |
| `vscode_conversation_id` | entity | (top-level) | the column that REPLACES `chat_main_id` |

`chat_system` and `vscode_conversation` are SIBLINGS — the register itself stores
that they are two systems.

## Refusals (each NAMES the fix)

| situation | code |
|---|---|
| no `definition` | `MISSING_DEFINITION` |
| no `cite_ref` | `MISSING_CITE_REF` |
| bad `term_kind` | `BAD_TERM_KIND` |
| undeclared `taxonomy_level` | `BAD_TAXONOMY_LEVEL` |
| unknown `parent_term_id` | `UNKNOWN_PARENT` |
| a name not in the register | `assert_named` -> `ok=False`, reason NAMES the term |

## Run

```
.\.venv\Scripts\python.exe terminology_registry.py --list
.\.venv\Scripts\python.exe terminology_registry.py --decompose vscode_conversation
.\.venv\Scripts\python.exe _registry_terminology_terms.py --apply
.\.venv\Scripts\python.exe _proof_terminology_naming.py
```
