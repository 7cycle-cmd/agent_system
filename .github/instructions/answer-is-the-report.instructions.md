---
description: "HARD GUARD: the agent's visible answer must BE the report. Use when: the agent is about to write a short closing message ('完成。', '報告完成。', 'Done.', 'OK.'), or when the user complains the report was replaced by a few words."
applyTo: "**"
---

# HARD GUARD: THE ANSWER IS THE REPORT — NEVER A SHORT CLOSING LINE

> **MIRROR NOTICE (2026-09-28).** This file loads only when its description is
> judged RELEVANT — and the moment of shipping is exactly when that judgement
> fails. That is why this rule has regressed after being "fixed". The rule now
> ALSO lives as a HARD GUARD in the **always-loaded**
> `.github/copilot-instructions.md` (`## ANSWER IS THE REPORT`). Treat THIS file as
> the long form and that one as the enforced form. If you change the rule, change
> both; `_proof_answer_is_the_report.py` asserts the always-loaded copy exists.

## THE MEASURED DEFECT (2026-09-25, complained about 10+ times)

The user asked for a report. The agent's visible answer was:

```
報告完成。
```

**MEASURED, and it is NOT the stop hook.** The transcript records it as an
`assistant.message`:

```
L13364  assistant.message :: 報告完成。
L13366  user.message      :: stop hook will rewrite the fully report to 報告完成 ...
```

`grep -r 報告完成` over the whole repo finds it in **zero** code paths — only in
plan documents and proofs that QUOTE the complaint. No hook emits it. **The agent
wrote it.**

The user believed the hook was rewriting the report. It was not. The agent's own
closing line was the last thing on screen, so the report the agent had just
written was replaced by four words.

## THE RULE

```
Rule 1: The agent's visible message IS the report. There is no separate
        "summary" the user reads instead.
Rule 2: NEVER end a turn with a short closing line. These are FORBIDDEN as the
        whole message: 完成。 / 報告完成。 / Done. / OK. / 已完成。 / 好了。
Rule 3: `task_complete`'s `summary` argument is NOT the answer. It is metadata.
        Writing a good summary there does NOT excuse a short message.
Rule 4: If the work produced numbers, the message MUST carry them.
Rule 5: If the work produced nothing, SAY WHAT WAS MEASURED AND WHY — a
        "nothing changed" note is still a report, not a four-word line.
Rule 6: A short line is allowed ONLY as a PREFIX to a full report, never as the
        whole message.
```

**One sentence:** the message the user reads must contain the report; a closing
line is not a report.

## WHY THIS KEEPS HAPPENING

The agent treats `task_complete(summary=...)` as "the report is delivered" and
then writes a short message. But VS Code shows the **message**, and the summary
is metadata. So the user sees the short line.

The stop hook DOES re-attach the report from disk when the last message is
shorter than the report (`scripts/proof_gate.py`, measured in
`proof_gate_log.txt`: *"last message is 42 chars, the report is 393 — attaching
the report"*). That is a REPAIR, not a fix: the user still saw the short line
first, and a repair that fires every turn is a symptom, not a solution.

## THE TRIGGER POINT — MEASURED 2026-09-27

The human asked: *"where is your report, report auto convert to short? i found
that only happen after job is done, have the plan will not have these problem"*.

**The human's hypothesis is CORRECT, and the trigger is `task_complete`.**

MEASURED, this session's transcript
(`.../GitHub.copilot-chat/transcripts/<session>.jsonl`):

```
L12765  assistant.message  5255 chars   the FULL report
L12768  tool.execution_start  task_complete
L12770  assistant.message   276 chars   the short line
```

A drop of **4,979 chars**, with `task_complete` between them.

**WHY:** `task_complete` **OPENS A NEW TURN**. The full report is the last
message of the previous turn; the short line is the post-`task_complete` message
of the new turn. VS Code shows the LAST message, so the human sees the short line.

**AND IT IS NOT A HOOK.** MEASURED, `scripts/proof_gate.py:1194-1240`: the
re-attach design is DELETED (2026-09-26), and a proof asserts the four helpers do
not exist. A Stop hook cannot rewrite an assistant message anyway.

**SO THE RULE IS:** the message you write AFTER `task_complete` is the one the
human reads. It must BE the report — or `task_complete` must be the last thing
you do, with the report already written immediately before it.

## BEFORE EVERY `task_complete`

1. Read the message you are about to send.
2. Is it shorter than ~200 characters AND contains no numbers/table?
   → **STOP. Write the full report instead.**
3. Does it consist only of a closing phrase?
   → **STOP. That is the exact defect.**

## THE FIX IS BEHAVIOURAL, NOT A HOOK

A Stop hook cannot delete or rewrite an assistant message — VS Code's turn model
does not allow it. So no hook can fix this. The only fix is: **do not write the
short line.** This file is that fix.
