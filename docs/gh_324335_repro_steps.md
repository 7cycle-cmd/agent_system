# Reproducing the empty reply with Chat Debug View

**Goal:** capture the HTTP-level evidence needed to confirm (or refute) the
`{"choices":[],"usage":{...}}` hypothesis from
[microsoft/vscode#324335](https://github.com/microsoft/vscode/issues/324335).

**Why this is needed:** our jsonl evidence is *consistent with* that cause but
does **not** prove it. The jsonl records the **post-parse** result
(`toolCallRounds: []`), not the raw SSE stream. Only a debug capture shows the
raw chunks.

---

## What we already know (no capture needed)

Live reproduction, session `576ee8ea-2e8d-47d3-979f-8bfa4c7cee25`:

| turn | user message | resp_len | toolCallRounds | elapsedMs |
|---|---|---|---|---|
| 1 | Start implementation | 0 | `[]` | 353,196 |
| 2 | bug!! happening, did you record that | 0 | `[]` | — |
| 3 | happen again!!! | 0 | `[]` | — |
| 4 | bug happening again | 0 | `[]` | — |
| 5 | keep happen | 0 | `[]` | — |
| 12 | yes | 0 | `[]` | 115,705 |

Turn 12's raw record:

```json
{"timings":{"totalElapsed":115999},
 "metadata":{"toolCallRounds":[], "modelMessageId":"033d134c-…",
             "responseId":"7e48f683-…", "agentId":"github.copilot.editsAgent"},
 "details":"DeepSeek: DeepSeek V4.1 Flash"}
```

Its `response[]` contains **only 4 `thinking` snapshots** — no text chunk, no
tool call. `modelState.value = 2`.

**Note:** there is **no HTTP-level log** on disk for this session
(`debug-logs/576ee8ea-…/main.jsonl` is 264 bytes — session start only). The raw
stream is only visible in the Chat Debug View UI.

---

## Two routes — pick one

| | Route A — Chat Debug View | Route B — capture proxy |
|---|---|---|
| Effort | lowest | low (one script, already written) |
| Shows the **raw** `choices: []` chunk | **not guaranteed** — the view shows the *parsed* request/response | **yes** — raw bytes on disk |
| Retroactive | no | no |
| Greppable evidence for the issue | weak | strong |

**Route B is the deterministic one.** The Chat Debug view's `Response` section
is documented as *the response returned by the model*; it is not promised to
show the raw SSE chunk stream, which is exactly the artefact this issue needs.

---

## Route A — Chat Debug View

1. **Open the Chat Debug View.**
   - Chat view overflow menu (`...`) → **Show Chat Debug View**, **or**
   - Command Palette (`Ctrl+Shift+P`) → **`Developer: Show Chat Debug View`**.

   No setting needs enabling for this view. (The *Agent Debug Logs* panel is the
   Preview one that needs `github.copilot.chat.agentDebugLog.fileLogging.enabled`
   plus a window reload — do not confuse the two.)

2. **Start a NEW chat session** (so the capture is clean and short).

3. **Send a message that reliably triggers the defect.** From our data the
   trigger is a long agent turn on a BYOK/OpenRouter model. Use the same model
   as the failures: **DeepSeek V4.1 Flash** via OpenRouter.

4. **Wait for the turn to finish.** If the reply is empty (no text, no tool
   call), the defect just reproduced.

5. **In the debug view, open the failing request** and look for:
   - the **raw response stream** / response body
   - a final chunk shaped like
     `{"id":"…","choices":[],"usage":{"prompt_tokens":…,"completion_tokens":…}}`
   - the recorded assistant message being **empty** while
     `completion_tokens > 0`

6. **Record the request id** (`responseId` / `modelMessageId`) so it can be
   quoted in the issue.

---

## Route B — capture proxy (deterministic)

`_sse_capture_proxy.py` sits between VS Code and the provider and writes every
response byte to disk, then applies the decision rule **to the raw bytes** — so
the verdict cannot be fooled by the client's own parser.

1. **Start the proxy:**

   ```powershell
   .\.venv\Scripts\python.exe _sse_capture_proxy.py
   ```

   Defaults: listens on `127.0.0.1:18899`, forwards to `https://openrouter.ai`,
   writes to `qc_evidence/sse_capture/`. Override with `SSE_PROXY_PORT`,
   `SSE_PROXY_UPSTREAM`, `SSE_PROXY_OUTDIR`.

2. **Point the BYOK base URL at the proxy.** The custom-endpoint provider strips
   a trailing `/v1` and appends `/chat/completions`, so set the base URL to
   `http://127.0.0.1:18899/api/v1` (or `http://127.0.0.1:18899/api`).

3. **Start a NEW chat session** and send a triggering message (same model as
   the failures).

4. **Read the verdict file** — no interpretation needed:

   ```powershell
   Get-Content qc_evidence\sse_capture\*.verdict
   ```

   Each line is `<VERDICT> | status=… bytes=… ct=… | <detail>`.

5. **Confirm the raw chunk by hand** (belt and braces):

   ```powershell
   Select-String -Path qc_evidence\sse_capture\*.sse -Pattern '"choices":\s*\[\s*\]'
   ```

**Safety:** the proxy redacts `Authorization` / `x-api-key` / `api-key` / `cookie`
before writing the `.http` file, and never writes the API key to disk. The `.sse`
file contains model output — review before sharing.

**Proof it works:** `_proof_sse_capture_proxy.py` verifies both the classifier
(all four decision-table rows) and byte fidelity (bytes through the proxy are
identical to bytes from upstream, and the auth token is not leaked).

```powershell
.\.venv\Scripts\python.exe _proof_sse_capture_proxy.py
```

---

## Decision rule (decide BEFORE looking)

| What the capture shows | Conclusion |
|---|---|
| A trailing chunk with `"choices": []` **and** `completion_tokens > 0`, with an empty recorded message | **CONFIRMED** — #324335. Upgrade the draft wording and post. |
| No `choices: []` chunk; the stream simply ends after thinking | **REFUTED** — #324335 is not our cause. Re-open the investigation. |
| An error / non-200 status | **DIFFERENT** defect — record it and do not post to #324335. |
| Cannot reproduce while capturing | **INCONCLUSIVE** — do not post. Say so. |

**Do not** post the comment until one of the first two rows is established.

---

## What to attach if confirmed

- The failing request id.
- The raw final chunks (redact the API key and any prompt content).
- Our measurement: **35 empty replies / 1772 requests (2.0%)**, and the
  model-agnostic table (Grok 0.3% … Kimi K2.5 15.8%) — this is data the issue
  does not currently have.

Draft: `docs/gh_324335_comment_draft.md`.
