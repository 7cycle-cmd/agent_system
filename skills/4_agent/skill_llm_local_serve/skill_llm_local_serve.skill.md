---
task_id: "SKILL.LLM.LOCAL.SERVE"
name: "skill_llm_local_serve"
catalog_id: 4
subcatalog_id: 0
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: "Serve an LLM route from an on-machine provider"
reason: "The edge of a local provider is zero token cost, 24/7 availability, a small model, and an interactive latency budget."
artifacts: ["skill_llm_local_serve.skill.md"]
schema: "llm_service_provider (service_id, model_id, local, priority)"
---
# Skill: skill_llm_local_serve

Serve an LLM route from a provider that runs ON THIS MACHINE (ollama).

## The edge this skill defines

The user's rule (2026-09-21): *"condition is LLM ability * services factor"*.
`local` is INFRASTRUCTURE (where the model runs), not ABILITY. This skill states
the EDGE of a local provider, so dispatch can be decided by ability rather than
by location.

| property | value |
|---|---|
| token cost | zero |
| availability | 24/7, no human needed |
| model size | small (7B) |
| latency | interactive — measured median ~108ms after the first call; the first call is COLD and may take seconds |
| failure mode | schema miss, not a reasoning failure |

## What it does

1. Confirm the provider is `local=1` and installed in ollama.
2. Call it over the EXISTING channel (`skill_task_queue._llm_call`).
3. Measure the round trip against the latency budget.

## Not to do

- DO NOT route a task here that needs a capability the local model lacks. A
  wrong route is worse than a ticket.
- DO NOT treat the first (cold) call as representative of the latency.
- DO NOT present a 7B-to-7B model swap as a capability upgrade.
