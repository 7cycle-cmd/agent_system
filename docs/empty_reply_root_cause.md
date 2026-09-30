# Empty-reply defect — root cause

**Date:** 2026-09-21
**Status:** root-caused, measured, reproduced live
**Scope:** Copilot chat harness / model — **NOT** `agent_system` repo code

---

## 1. The defect

A user sends a message and gets **nothing back**. The session store records
`assistant_response = ''`.

Measured live in this session (`576ee8ea-2e8d-47d3-979f-8bfa4c7cee25`): the
turns `Start implementation`, `bug!! happening, did you record that`,
`happen again!!!` and `keep happen` all produced an empty reply, while the UI
showed a stream of fragments — `Let me go. / Writing. / OK. / Let me do it. /
Let me create. / Creating.`

## 2. Root cause

**The model completed the turn without emitting a final assistant message.**
No text, no tool call. The harness recorded `toolCallRounds: []` and an empty
`assistant_response`.

### CORRECTION (2026-09-21): there are TWO patterns, not one

The authoritative per-turn record is
`GitHub.copilot-chat/transcripts/<session-id>.jsonl` — it carries
`assistant.message` records with `content`, `toolRequests` and `reasoningText`.
Grouping by user turn gives:

| turn | user message | assistant msgs | tool calls | reasoning chars | final content |
|---|---|---|---|---|---|
| 1 | Start implementation | **0** | 0 | 0 | — |
| 2 | bug!! happening, did you record that | 6 | 8 | 6,719 | **0** |
| 3 | happen again!!! | 5 | 6 | 8,005 | **0** |
| 4 | bug happening again | 2 | 2 | 8,407 | **0** |
| 5 | keep happen | 6 | 6 | 7,934 | **0** |
| 12 | yes | **0** | 0 | 0 | — |
| 14 | *(empty)* | **0** | 0 | 0 | — |

**Pattern A — ZERO assistant messages** (turns 1, 12, 14): the turn started and
ended with no assistant message at all. `reasoning = 0`, `tools = 0`. The model
produced **nothing**.

**Pattern B — tool activity but NO closing text** (turns 2, 3, 4, 5): the turn
ran 2–8 tool calls, then the **final** assistant message had `content = 0`. The
user saw tools run but got no answer.

Both look identical in the UI (fragments, then nothing), but they are different
failures.

### The earlier jsonl measurement CONFLATED these two

`requests[N].result.metadata.toolCallRounds` is the tool-call count of the
**final request of a turn**, not of the whole turn. A turn is several requests
(one per tool round). So `toolCallRounds: []` on the last request means only
"the last request made no tool call" — which is true for **both** patterns.

The jsonl-based figure (**35 empty / 1772 requests, 2.0%**) therefore mixed
Pattern A and Pattern B. **It is SUPERSEDED.**

### Corrected measurement (per TURN, from transcripts)

`_measure_empty_replies_v2.py` — 21 transcripts, **619 turns**:

| Class | Turns | Rate |
|---|---|---|
| answered | 529 | 85.5% |
| **Pattern A** (0 assistant messages) | **50** | **8.1%** |
| **Pattern B** (tool calls, no closing text) | **40** | **6.5%** |
| **FAILED total** | **90** | **14.5%** |

Discriminators both PASS:

```
[PASS] every Pattern-A turn has reasoning == 0
[PASS] every Pattern-B turn ran at least one tool call
```

Median reasoning size separates them cleanly:

| Class | median reasoning chars | median tool calls |
|---|---|---|
| Pattern A | **0** | 0 |
| Pattern B | **7,934** | **6** |
| answered | 5,093 | — |

**Pattern A is not a truncation of Pattern B.** Pattern A produced *nothing at
all* — no reasoning, no tool call. Pattern B reasoned normally (7,934 chars,
comparable to the 5,093 of answered turns) and ran ~6 tools, then failed to
close with text. They are different failures and need separate causes.

**Caveat:** only 21 transcripts exist versus 62 `chatSessions` files, so the
transcript store is partial. The 14.5% is the rate over the turns the transcript
store covers, not over all history.

### Pattern A characterised — full measurement

`_diag_pattern_a.py`, all 21 transcripts, **50 Pattern A turns**:

| Question | Result |
|---|---|
| turn_start present? | **yes** — 49 turns have exactly 1, 1 turn has 2 |
| turn_end present? | **41 have 1; 9 have ZERO** |
| duration (user msg → last record) | min 0.0 s, **median 38.0 s**, max 1,498 s |
| non-A duration, for contrast | median **153.9 s** |
| preceded by | `ok` 32, `A` 10, `B` 4, first-turn 4 |
| position | later 46, first 4 |
| attachments | **0 on all 50** |
| user message empty? | non-empty 49, empty 1 |
| idle gap before | median 43.1 s (non-A: 93.8 s) |

The single-turn evidence (session `576ee8ea`, `Start implementation`):

```
user.message          "Start implementation"   15:34:40.448
assistant.turn_start  turnId=0                 15:34:40.448
assistant.turn_end    turnId=0                 15:40:33.639
```

353 s apart, zero `assistant.message` in between.

**Pattern A is NOT "the request was never sent"** — that was my earlier guess
and it is wrong. The turn ran (median 38 s) and committed nothing.

**Notable:** Pattern A turns are **shorter** than normal turns (median 38 s vs
153.9 s). They are not long turns that ran out of budget — they end early.

### CORRECTION: the "A1 / A2" split was a measurement bug

An earlier version of this document split Pattern A into:

- A1 — `turn_end` present (41) = commit failure
- A2 — `turn_end` ABSENT (9) = abandoned / aborted turn

**The A2 half is wrong, and it is a measurement bug in `_diag_pattern_a.py`.**

`turnId` in the transcript is a **tool round**, not a user turn. Within ONE user
turn the transcript emits many `turn_start` / `turn_end` pairs, one per tool
round, and `turnId` restarts at `0` for each new user message:

```
user.message "Skill Contract 系統｜…"   -> turn_start turnId=0
user.message "1. 批准要唔要綁人？…"      -> turn_start turnId=0   <- restarts
```

`_diag_pattern_a.py` counted `turn_start` / `turn_end` between `user.message`
records. So "9 turns with no `turn_end`" means only that the **last tool round
of that user turn** had no `turn_end` — because the next user message arrived.
That is a **user interruption**, not an abandoned turn.

**Therefore: do NOT split Pattern A into A1/A2.** The 50-turn count stands; the
sub-classification does not.

**Caveat:** the "thinking was streamed to the UI" step is inferred from the
jsonl `response[]` snapshots (4 `thinking` entries for the 353 s turn), not from
the transcript, which carries no `reasoningText` for these turns.

### What the cause is NOT (measured, and it corrects an earlier claim)

An earlier version of this document said "the thinking block consumed the whole
turn". **The duration evidence contradicts that.**

| Measure | EMPTY (n=35) | NON-EMPTY (n=1673) |
|---|---|---|
| elapsedMs median | **80,102** | **78,549** |
| elapsedMs min | 3,152 | 1,538 |
| elapsedMs max | 1,498,527 | 3,494,331 |

Empty turns take the **same** time as normal ones. A turn cut off by an
exhausted budget would be *longer*, not equal. So the turn was **not**
truncated — it **finished** and simply carried no final message.

Two further knobs were tested and are **not** the cause:

- `reasoningEffort` is **not recorded** in the jsonl at all, so it cannot be
  correlated. Any claim that lowering it fixes this is **unmeasured**.
- `outputBuffer` is a **streaming artefact**: it is patched at line 69 while the
  request is appended at line 47 (`_diag_req_shape.py`). Its absence in empty
  turns is a **consequence**, not a cause.

`permissionLevel` is likewise not a factor (request 42 ran under `autopilot`,
request 44 under `default`; both empty).

The fragments the user sees (`Let me write. / OK. / Writing.`) **are the
thinking block**, streamed to the UI. They are not assistant messages and are
not persisted as such — which is why grepping every Copilot log for
`Let me write` returns 0 matches (see `HANDOFF_p2_gate_and_loop.md` Part 1a).

### Evidence

| Check | Command | Result |
|---|---|---|
| Empty replies have no tool call | `.\.venv\Scripts\python.exe _measure_empty_replies.py` | `[PASS] all empty replies have toolCallRounds == []` |
| Raw record for a live empty turn | `_diag_jsonl_patch.py 576ee8ea-… 0` | final snapshot carries `text(741), text(626), …`; the empty turns carry only `thinking` |
| Requests accumulate by append | `_diag_jsonl_requests.py 576ee8ea-…` | `header_n=1` + 5 append patches = 6 requests = 6 store turns |

Raw record shape for an empty turn (session `576ee8ea`, request 1):

```json
{"timings":{"totalElapsed":353196},
 "metadata":{"toolCallRounds":[], "modelMessageId":"…", "responseId":"…"},
 "details":"DeepSeek: DeepSeek V4.1 Flash"}
```

`toolCallRounds: []` + no text chunk in `response[]` = the user got nothing.

### Discriminator

```
empty reply  <=>  (no text chunk in response[])  AND  (toolCallRounds == [])
```

This is the checkable rule. It is asserted by the script and currently PASSES
for all 35 empty replies found.

## 3. Two definitions — do not conflate them

The handoff's "11 of 50" and this script's "9" are **both correct**; they
measure different things.

| Definition | Meaning | f220fb14 | All sessions |
|---|---|---|---|
| `no_final_text` | no final **text** (the store's definition; a turn that ran tools then said nothing also counts) | **11** | 132 (7.4%) |
| `empty_reply` | **the user got nothing** — no text AND no tool call | **9** | 35 (2.0%) |

`no_final_text` for `f220fb14` = **11**, which matches
`SELECT COUNT(*) FROM turns WHERE COALESCE(assistant_response,'')=''`
exactly. That match is the validation anchor for the parser.

A tool-only turn is **not** the defect: the user saw the tool run.

## 4. The measurement bug this file exists to record

Three wrong numbers were produced before the parser was correct. Each was a
**parser artefact**, not a finding:

| Attempt | Rate | Cause |
|---|---|---|
| 1 | 98.1% | read only the `kind:0` header; never applied the patch log |
| 2 | 78.1% | treated `kind:2` as SET, so each response snapshot overwrote the previous one |
| 3 | 78.0% | "keep the longest array" heuristic — the longest snapshot carried **no text** while the final one did |
| 4 | **2.0%** | correct: `kind:2` on `["requests"]` = APPEND; on `["requests",N,"response"]` = last-write-wins snapshot |

The lesson: **the jsonl is a patch log, and its semantics must be measured, not
guessed.** `_diag_jsonl_patch.py` and `_diag_jsonl_requests.py` exist to read
the semantics off the data. A 78–98% "empty rate" should have been rejected on
sight as not credible — it was, which is why the parser was fixed instead of
the number being reported.

## 5. What this is NOT

- **Not** a loop in `skill_tdd_runner.py` / `skill_contract_store.py`.
- **Not** correlated with permission level: request 42 of `f220fb14` ran under
  `autopilot` and request 44 under `default`; both were empty.
- **Not** a repo-code defect. No `agent_system` code path is involved.

## 5b. Pattern C — degenerate reasoning loop (NEW, 2026-09-21)

The screenshot the user sent (`Let me write. / OK. / Go. / Producing. / Now.`
repeating, then `Working`) is a **third** failure mode, and it is the one that
produces the *longest* empty turns.

### What it is

The model's **reasoning block** degenerates into a loop over a tiny vocabulary
of short phrases and never terminates. The turn ends with no text and no tool
call, so the user sees the fragments stream past and then nothing.

### Evidence — session `576ee8ea`, request 33

Reproduce with:

```powershell
.\.venv\Scripts\python.exe _diag_loop_numbers.py
```

| Measure | Value |
|---|---|
| thinking chars | **334,625** |
| thinking lines | **38,027** |
| distinct lines | 153 |
| top-10 lines cover | **99.6%** of all lines |
| `Go.` | 8,119 |
| `Let me write.` | 8,112 |
| `OK.` | 8,110 |
| `Now.` | 5,413 |
| `Writing.` | 4,053 |
| `Producing.` | 4,052 |
| text chars | **0** |
| tool calls | **0** |

The first 12 lines are a normal, coherent analysis. Line 13 onward is the loop.
The streamed snapshots grow monotonically to 334,625 chars
(`_diag_loop_growth.py 576ee8ea 33`: 20 patches, 12,322 → 334,625).

### It is not unique to this session

`_diag_loop_summary.py` — all 62 `chatSessions` files, 325 requests with output:

| session | req | thinking chars | lines | distinct | top-10 share | empty |
|---|---|---|---|---|---|---|
| `576ee8ea` | 33 | 356,347 | 40,561 | 152 | 99.6% | **yes** |
| `34bae87b` | 2 | 209,745 | 2,305 | 3 | 100.0% | **yes** |
| `2e07543a` | 6 | 154,635 | 201 | 7 | 100.0% | **yes** |
| `c0005b0a` | 13 | 119,106 | 9,891 | 298 | 96.9% | **yes** |

**All 4 degenerate loops are empty, and all 4 are the LAST request of their
session.** The user stops the session because the turn never ends.

`34bae87b` is the extreme case: **3 distinct lines** repeated 2,305 times.

### Contingency

| | empty | ok |
|---|---|---|
| loop (top-10 ≥ 90% of lines) | **4** | 0 |
| no loop | 14 | 307 |

A degenerate loop is **sufficient** for an empty reply (4/4) but not
**necessary** (14 of the 18 empty requests have no loop). So Pattern C is a
distinct sub-case, not the whole defect.

### Why this matters for the #324335 hypothesis

Pattern C is **not** explained by `{"choices":[],"usage":{...}}`. In that
hypothesis the model produced a *short* final message that the client discarded.
Here the model produced **334 KB of reasoning and never stopped** — the stream
was still growing when the turn ended. These are different failures and must be
reported separately.

### Caveat

`_diag_loop_summary.py` reads `chatSessions/*.jsonl` (the patch log), not the
transcripts. The 4 loop requests are the last request of their session, so their
`result` is `null` and `modelId` is absent — the model cannot be attributed from
this file. Do not claim a model for Pattern C without a transcript.

## 5c. The installed extension REFUTES #324335 (2026-09-21)

The bundled Copilot Chat extension is **0.66.0** (VS Code **1.138.0**), at
`…\Microsoft VS Code\7debcd0e2a\resources\app\extensions\copilot\dist\extension.js`.

### The `choices: []` chunk cannot drop content

`extension.js:1471` — the SSE chunk handler:

```js
if (p.usage && …) yield p.usage,
…
!p.choices) {                       // <-- empty array is FALSY
  !p.copilot_references && !p.copilot_confirmation && (
    p.error !== void 0
      ? (… yield {reason:"error", error:p.error, …})
      : (this.logService.error(`Unexpected response with no choices or error for request id …`),
         a9(this.telemetryService, `Unexpected response with no choices or error …`))
  ),
  p.copilot_errors && await e("",0,{text:"",copilotErrors:p.copilot_errors}),
  p.copilot_references && await e("",0,{text:"",copilotReferences:p.copilot_references});
  continue
}
```

`!p.choices` is **true** for `[]`. So a `{"choices":[],"usage":{…}}` chunk takes
the no-choices branch, logs an error, and `continue`s. It **never touches**
`this.solutions`, so accumulated content is **not** discarded.

`finishSolutions()` (`extension.js:1471`) is called **only** on `[DONE]`:

```js
if (d === "[DONE]") { yield* this.finishSolutions(); return }
```

and it yields whatever is in `this.solutions`. A usage-only chunk therefore
cannot suppress the final message.

**Conclusion: the #324335 mechanism does not exist in this build.** The
hypothesis is **REFUTED by code reading**, not merely unproven. Do not post the
draft comment.

### #329963 is already fixed in this build

`extension.js:1471` carries the **fixed** guard:

```js
if (A.delta?.tool_calls?.length) { … } else if (…) { … }
```

The buggy form was `if (choice.delta?.tool_calls)` — truthy on `[]`. The
`.length` guard is present, so the #329963 defect (empty `tool_calls: []`
dropping content) is **already fixed** here. It is **not** our cause.

### There is no reasoning-loop guard

Searched the bundle for a cap or a "reasoning-only" guard:

| Pattern | Matches |
|---|---|
| `only reasoning` | **0** |
| `no content` | **0** |
| `thinkingBudget` | **0** |

`maxThinkingBudget` / `minThinkingBudget` exist only as **model capability
metadata** used to build the request (`budget_tokens`), not as a client-side
stop condition. Nothing in the extension stops a model that keeps emitting
reasoning.

**So Pattern C is unfixed and unguarded in 1.138.0.**

## 6. Secondary finding (not root-caused)

28 sessions show `jsonl_no_text < store_empty` — e.g. `0e70ec00` has 4 in the
jsonl but 13 in the store, and `3794bef3` has 0 in the jsonl but 11 in the
store. The store therefore records empty turns that the jsonl does not carry.
That is a **separate** persistence question and is **not** explained here.

## 7. Can it be fixed?

**Not from this repository.** No `agent_system` code path is involved, so there
is nothing here to change.

### Is it a DeepSeek bug? — NO (measured)

`_diag_empty_by_model.py` groups every request by `result.details`:

| Model | reqs | empty | rate |
|---|---|---|---|
| SpaceXAI: Grok 4.5 | 322 | 1 | **0.3%** |
| DeepSeek: DeepSeek V4 Flash 0731 | 221 | 1 | **0.5%** |
| MoonshotAI: Kimi K2.7 Code | 395 | 4 | **1.0%** |
| Qwen: Qwen3.8 27B | 378 | 11 | **2.9%** |
| DeepSeek: DeepSeek V4.1 Flash | 368 | 15 | **4.1%** |
| MoonshotAI: Kimi K2.5 | 19 | 3 | 15.8% (n too small) |

**Every model shows the defect.** It is therefore **harness-wide**, not
DeepSeek-specific. DeepSeek V4.1 Flash sits at 4.1% — above Grok (0.3%) but
below Kimi K2.5 (15.8%), so model *susceptibility* varies while the defect
class does not.

Consequence: **switching model is not a reliable fix.** Moving to Grok would
lower the rate (0.3%) but not eliminate it.

### What the measurement does and does not support

| Option | Supported by evidence? |
|---|---|
| Retry a `toolCallRounds == []` + no-text turn in the harness | **Yes** — the turn is a completed no-op, so retrying is safe and directly targets it |
| Switch model or provider | **Weak** — reduces the rate, does not remove the defect (all models affected) |
| Lower `reasoningEffort` | **No** — not recorded in the jsonl; the duration evidence contradicts the budget story |
| Raise `outputBuffer` | **No** — it is a streaming artefact, not a setting |

Only the first is a real fix. The last two were the original guess and the
measurement rejected them.

### Unmeasured fields (do not claim these)

- `reasoningEffort` — not present in the jsonl.
- `sendOptions.userSelectedModelId` — `(none)` for all 1777 requests.

Neither can be correlated, so no claim about them is supported.

## 9. Upstream: is there a known issue?

**Yes — and one matches this defect closely.**

| Issue | Match | Status |
|---|---|---|
| [microsoft/vscode#324335](https://github.com/microsoft/vscode/issues/324335) — "Response Contained No Choices" (BYOK / Ollama / OpenRouter) | **Strong.** Silent empty reply, no error, BYOK via OpenRouter | Open, `bug` + `model-byok`, assigned @vritant24 |
| [microsoft/vscode#332870](https://github.com/microsoft/vscode/issues/332870) — cross-turn `reasoning_content` dropped | **Weak.** Produces an **HTTP 400**, not a silent empty reply | Open, has a `dist/extension.js` workaround |
| [microsoft/vscode#273723](https://github.com/microsoft/vscode/issues/273723) — "Thinking then incomplete response" | Related. roblourens: the model dumped its reply into the `think` tool | Closed, fixed in 1.107 |
| [microsoft/vscode#278816](https://github.com/microsoft/vscode/issues/278816) — empty chat UI, text appears after reload | Related but different (render path) | Closed, fixed in 1.108 |

### The #324335 hypothesis

@TjWheeler traced it with Chat Debug View: under
`stream_options: {"include_usage": true}`, the server sends a final
**spec-compliant** SSE chunk carrying only usage:

```json
{"id":"...","choices":[],"usage":{"prompt_tokens":28987,"completion_tokens":21}}
```

The client indexes `choices[0]` without checking for an empty array, and
**discards the already-accumulated message content**. The debug view showed
`completion_tokens: 21` (the model *did* generate content) while the recorded
assistant message was empty.

### Why this fits our measurement

| Our evidence | #324335 explains it |
|---|---|
| `toolCallRounds: []` | the whole message was discarded |
| no text chunk | same |
| elapsedMs normal (~80s) | the model **completed**; not a timeout |
| **every model affected** | it is a **client parser** bug, not a model bug |
| BYOK via OpenRouter | the issue is specifically OpenRouter/Ollama BYOK |

The model-agnostic rate is the strongest signal: a per-model cause cannot
produce a defect that every model shares.

### What is NOT established

We have **no HTTP-level capture**, so the trailing `choices: []` chunk is
**not confirmed** in our failing requests. The evidence is *consistent with* it
but does not prove it. Confirming it needs one reproduction with Chat Debug View
open.

**Do not conflate #332870 with this defect** — that one raises an HTTP 400.

Draft comment for the issue: `docs/gh_324335_comment_draft.md`.

## 10. Reproduce

```powershell
.\.venv\Scripts\python.exe _measure_empty_replies.py
.\.venv\Scripts\python.exe _diag_jsonl_patch.py 576ee8ea-2e8d-47d3-979f-8bfa4c7cee25 0
.\.venv\Scripts\python.exe _diag_jsonl_requests.py 576ee8ea-2e8d-47d3-979f-8bfa4c7cee25
```
