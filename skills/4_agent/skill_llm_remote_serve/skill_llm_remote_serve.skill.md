---
task_id: "SKILL.LLM.REMOTE.SERVE"
name: "skill_llm_remote_serve"
catalog_id: 4
subcatalog_id: 0
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: "Serve an LLM route from an IDE-reached provider"
reason: "The edge of a remote provider is token cost, availability only while a human/IDE is present, and a larger model."
artifacts: ["skill_llm_remote_serve.skill.md"]
schema: "llm_service_provider (service_id, model_id, local, priority)"
---
# Skill: skill_llm_remote_serve

Serve an LLM route from a provider reached through the IDE.

## The edge this skill defines

The user's rule (2026-09-21): *"condition is LLM ability * services factor"*.
`local` is INFRASTRUCTURE (where the model runs), not ABILITY. This skill states
the EDGE of a remote provider, so dispatch can be decided by ability rather than
by location.

| property | value |
|---|---|
| token cost | applies — cost is a ROUTING input, not a post-hoc report |
| availability | only while a human / IDE is present; unavailability is a NORMAL state, not a fault |
| model size | larger |
| latency | not interactive — a human is in the loop |
| failure mode | unreachable, which is handled by a TICKET, not a task failure |

## What it does

1. Confirm the provider is registered in `llm_model` (it is not installed here
   by definition — it is the IDE's LLM).
2. Check reachability BEFORE routing.
3. When reachable, raise a SERVICE TICKET. The ticket IS the handoff — no
   task_id/chat_id bridge is involved.

## Not to do

- DO NOT route a task here when a local provider can serve it. Cost is a routing
  input.
- DO NOT treat an unreachable remote provider as a task failure. It is a normal
  state; raise a ticket.
- DO NOT build a task_id/chat_id bridge to reach it. The ticket already is the
  handoff.
