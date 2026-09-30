# Draft comment for microsoft/vscode#324335

**Issue:** "GitHub Copilot 'Response Contained No Choices' Using BYOK - Ollama, OpenRouter.ai"
**URL:** https://github.com/microsoft/vscode/issues/324335
**Status:** Open, assigned to @vritant24, labelled `bug` + `model-byok`

---

## Paste this

---

Independent confirmation + a measurement that may narrow the cause.

**Setup:** VS Code 1.138.0, built-in Copilot Chat 0.66.0, Windows 11.
All models are BYOK via **OpenRouter** (`openrouter/OpenRouter/...`).

**Symptom:** the turn completes and the chat shows **nothing** — no text, no
tool call. No error is surfaced. The UI streams short fragments
(`Let me write. / OK. / Writing.`) which are the *thinking* block, then stops.

### What I measured

I parsed the per-request records in
`%APPDATA%\Code\User\workspaceStorage\<hash>\chatSessions\<session>.jsonl`
across **62 sessions / 1772 requests** and classified each request by whether it
produced a final text chunk and whether it produced a tool call.

An **empty reply** = no text chunk **AND** `metadata.toolCallRounds == []`.

```
requests parsed : 1772
EMPTY replies   : 35  (2.0%)
```

Every one of the 35 empty replies has `toolCallRounds: []` — i.e. the turn
produced **no tool call and no text**, yet the request completed normally.

### The finding that may matter most: it is NOT model-specific

Empty-reply rate grouped by `result.details`:

| Model | reqs | empty | rate |
|---|---|---|---|
| SpaceXAI: Grok 4.5 | 322 | 1 | **0.3%** |
| DeepSeek: DeepSeek V4 Flash 0731 | 221 | 1 | **0.5%** |
| MoonshotAI: Kimi K2.7 Code | 395 | 4 | **1.0%** |
| Qwen: Qwen3.8 27B | 378 | 11 | **2.9%** |
| DeepSeek: DeepSeek V4.1 Flash | 368 | 15 | **4.1%** |
| MoonshotAI: Kimi K2.5 | 19 | 3 | 15.8% (n too small) |

**Every model is affected.** The rate varies by model, but the defect class does
not. That is consistent with a **client-side stream-parsing** cause rather than
a per-model/provider cause — which is what @TjWheeler's analysis above
(`choices: []` trailing chunk under `stream_options.include_usage`) predicts.

### Two things that rule out other explanations

1. **It is not a timeout or an exhausted budget.** Median elapsed time is the
   same for empty and non-empty turns:

   | | EMPTY (n=35) | NON-EMPTY (n=1673) |
   |---|---|---|
   | elapsedMs median | 80,102 | 78,549 |

   A turn cut short by a budget would be *longer*, not equal. The turn
   **finished**; it just carried no message.

2. **It is not the permission level.** Request 42 of one session ran under
   `autopilot` and request 44 under `default`; both were empty.

### Raw record for an empty turn

```json
{"timings":{"totalElapsed":353196},
 "metadata":{"toolCallRounds":[], "modelMessageId":"…", "responseId":"…"},
 "details":"DeepSeek: DeepSeek V4.1 Flash"}
```

### What I have NOT established

I do **not** have an HTTP-level capture, so I cannot yet confirm the trailing
`{"choices":[],"usage":{...}}` chunk is present in my failing requests. My
evidence is consistent with it but does not prove it. If a maintainer wants it,
I can attach a Chat Debug View capture of a failing request.

### Suggested fix direction (agreeing with @TjWheeler)

The stream-chunk handler for custom-endpoint/BYOK responses should tolerate a
chunk with `"choices": []` (spec-compliant when `stream_options.include_usage`
is set) instead of indexing `choices[0]` and discarding the accumulated message.

---

## Notes for us (do not paste)

- **Do not claim** we proved the `choices: []` cause. We have a *consistent*
  measurement, not an HTTP capture. The draft says so explicitly.
- **Do not paste** the model table as if it were a controlled experiment — it is
  observational across 62 local sessions, and Kimi K2.5 has n=19.
- **Before posting:** reproduce once with Chat Debug View open and check for the
  trailing `choices: []` chunk. If present, upgrade the wording from
  "consistent with" to "confirmed".
- Related but **different** issue: #332870 (cross-turn `reasoning_content`
  dropped) produces an **HTTP 400**, not a silent empty reply. Do not conflate.
