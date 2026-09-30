---
name: terminology-register
description: "Use when: about to NAME anything new — a table, a column, an API route, a file, a module, a factor, a skill, a variable that carries a domain concept — or when you catch yourself reusing a word that already names something else. Enforces 'no name without a registered term': the term must exist in terminology_register with a definition and a citation BEFORE the name is used, because a name that collides with another system's word makes every later reader pick the wrong one. Also use when two systems share a word (e.g. 'chat' naming both the chat system and a VS Code conversation), when you are about to invent a synonym, or when a report is about to be marked done while using an unregistered name."
---

# Terminology Register (no name without a registered term)

**Goal:** 一個名**未註冊**就唔可以用。名唔係字串，係一個**有定義、有引用、可分解**嘅結構。

Full skill: `skills/1_core/terminology_register/terminology_register.skill.md`
Executable: `terminology_registry.py` · Proof: `_proof_terminology_naming.py`

## When to use

- 你**即將命名**任何新嘢：table、column、API route、file、module、factor、skill
- 你**重用**一個已經命名咗其他嘢嘅字
- **兩個系統共用一個字**（例：`chat` 同時指 chat system 同 VS Code conversation）
- 你打算**發明同義詞**（「呢個同嗰個差唔多」）
- 報告**即將當完成**，但用咗未註冊嘅名

## 點解要註冊，唔係改名咁簡單

用戶（2026-09-23）：

> "too easy to have name mis-understand problem, you need to register at
>  terminology_register!!!"
> "chat system is chat system, vscode > chat > conseraction is another system"

實測例子：`conversation_env_log.chat_main_id` FK → `chat_main(id)`，而
`chat_main` 係 **chat system** 嘅表。個 column 名令讀者以為個值屬於 chat
system，但佢其實識別一個 **VS Code conversation**。一個字，兩個系統。

## 一個 term 係結構，唔係字串

```
100_run_service
  -> 100_run        (the capability)
  -> service        (what it IS)
```

`parent_term_id` **儲存**個結構，所以「呢個 term 由邊幾部分組成」係表裡面嘅
事實，唔係註解裡面嘅一句話。

## 註冊一個 term

```python
import terminology_registry as tr
tr.add_term(conn, "vscode_conversation",
            definition="A conversation INSIDE VS Code's chat: one "
                       "chatSessions/<session-id>.jsonl file. It is NOT the "
                       "chat system.",
            cite_ref="scripts/mode_attest.py find_session_file",
            term_kind="entity",
            entity_ref_key="vscode_conversation")
```

**會被拒嘅情況**（`add_term` 自己拒，唔係靠人記得）：

| 情況 | code |
|---|---|
| 冇 `definition` | `MISSING_DEFINITION` |
| 冇 `cite_ref` | `MISSING_CITE_REF` |
| `term_kind` 唔合法 | `BAD_TERM_KIND` |
| `taxonomy_level` 未宣告 | `BAD_TAXONOMY_LEVEL` |
| `parent_term_id` 唔存在 | `UNKNOWN_PARENT` |

## 兩個系統要係 SIBLINGS

`chat_system` 同 `vscode_conversation` **兩個都係 top-level**（`parent_term_id`
= NULL）。呢個就係重點：register **儲存**「佢哋係兩個系統」呢個事實。

```python
# 錯：令一個做另一個嘅 parent，等於話佢哋係同一個系統
# 對：兩個都 top-level
```

## 檢查一個名

```python
ok, reason = tr.assert_named(conn, "vscode_conversation")
# ok=False 時，reason 會**指名**邊個字未註冊
```

拒絕要**指名**：修法係「註冊呢個字」，唔係「再試一次」。一個唔講邊個字缺失嘅
拒絕，會令讀者返去猜。

## 紅旗

- 「個名好明顯」→ 明顯唔係註冊
- 「同之前嗰個差唔多」→ 差唔多就係碰撞
- 「先寫 code，之後補註冊」→ **之後永遠唔會補**
- 「兩個系統用同一個字冇問題」→ 就係呢個問題
- 「報告做完先算」→ 用未註冊嘅名，報告唔算完成
