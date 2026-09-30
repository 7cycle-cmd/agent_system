---
name: env-task-proof
description: "Use when: about to perform UI automation (vision detect, mouse click, hotkey, screenshot-based action) OR about to act on a task/state (mode switch, queue item, registry entry). Enforces two pre-action rules: (1) PROVE the environment state (window maximized/foreground/screen size) instead of assuming it; (2) PROVE the task/target exists before acting. NEVER skip proof and NEVER assume."
---

# Env + Task Proof (pre-action rules)

**Goal:** before ANY UI automation or state change, **prove** the environment and
the target. Assume nothing.

Full skill: `skills/1_core/env_task_proof/env_task_proof.skill.md`

## When to use

- About to do UI automation: vision detect, mouse click, hotkey, screenshot action
- About to act on a task/state: mode switch, queue item, registry entry
- About to trust a vision-detected coordinate, box, or region
- A check reported success but nothing actually changed (false success)
- A verdict was produced from a screenshot whose origin is unknown

## The four rules (detail in the full skill)

1. **Environment proof** — before any UI automation, prove window
   maximized / foreground / screen size. Log the measured values. A wrong
   layout shifts every pixel → wrong click.
2. **Task proof** — prove the task/target EXISTS (state file, element on screen,
   registry/queue row). Never click without a proven coordinate.
3. **Container proof** — prove the target's container is on screen before
   judging geometry. A same-size image is **not** a same-content image: a
   capture of Chrome at 1920x1080 passed every size/hash provenance check and
   still produced a confident, meaningless `geometry_fail`.
4. **Classify absence FIRST** — `picker_not_open > source_suspect >
   box_not_seen > geometry_fail > text_mismatch`. A container-absent result is
   **UNKNOWN, not FAIL**: no judgement was actually made.
5. **Capture while the container is OPEN** — a separate verify invocation takes
   its own screenshot after the container has dismissed, giving a correct but
   useless `picker_not_open`. Open → measure → verify must happen in **ONE
   process** for a transient container.
6. **Never locate a control by a state-encoded signal** — the permission pill is
   yellow only while that permission is SELECTED, so a colour search measures the
   current setting, not the control. Use a fixed measured coordinate, or let a
   human point at it. Cluster by contiguous dense runs, never min/max.
7. **Treat struck-off findings as dated claims** — a recorded "this does not
   work" can become false. `Ctrl+Alt+K` was recorded as abandoned (1/6); on
   re-test it worked reliably, the old failures having lacked WinActivate +
   a short wait. Re-test with before/after captures before trusting a negative,
   and check toggle state or a retry will undo the previous success.

**Never report success without a proven post-action read.**

## Note on this file

This is a **pointer**, not a second copy. The canonical skill lives at
`skills/1_core/env_task_proof/` so it is visible to the Skill Library scanner,
has a contract and can accumulate a streak. This file exists only so the agent
loads the summary automatically at task start; editing it does not change the
enforced rule.

