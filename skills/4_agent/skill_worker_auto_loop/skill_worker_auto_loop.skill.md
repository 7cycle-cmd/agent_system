# Skill: skill_worker_auto_loop

**Let a worker loop WITHOUT a stop condition and still be safe.**

## Why this skill exists

The human asked (2026-09-27):

> "yes! and be skill help worker can auto forever"

"auto forever" is a request for a loop with no stop condition, and that is the
dangerous reading. The measured answer is that **safety and termination are the
SAME property**:

```
each pass must produce a NEW DISTINCT CODE STATE.
```

Then:

| property | why it holds |
|----------|--------------|
| **safety** | infinite passes cannot promote work with no new state |
| **termination** | when no new state is producible, the loop STOPS |

A count-based loop would promote by **repetition**. That is why there is no
`run_forever()` anywhere in this repo, and why this skill **forbids** writing one.

## The loop, with a gate at every step

```
1 PROPOSE   generate a case for one of the failure classes; write NOTHING yet
2 DERIVE    classify the failure -> REFUSES no-match / same-tier / unknown signal
3 MEASURE   two independent methods -> write ONLY on agreement
4 PROVE     streak over DISTINCT CODE STATES (a repeated state is not progress)
5 DISCOVER  which sibling could be proven next
6 STOP      state-based, never a counter
```

## Step 5: the discriminator is FAMILY MEMBERSHIP, not similarity

`independent_review` forbids overlap scores, so nothing scores similarity. The
question asked is: **given a family, how many unproven members does it have?**

| count | verdict |
|-------|---------|
| 1 | `CONFIRMABLE` — the rule decided |
| >1 | `AMBIGUOUS` — reported, never resolved by picking the largest |
| 0 | `FULLY_PROVEN` |

The family comes from `contract_id`'s entity-type **LETTER** (schema-defined),
**NOT** from `assertion`, which is prose.

## The stop reasons

| reason | meaning |
|--------|---------|
| `SATURATED` | no family has an unproven member left |
| `UNSATURABLE` | a family cannot reach `evidence` however often it runs; the missing input is a **DEDICATED PROBE** (a code change), not more iterations |

A saturation stop is a **RESULT to report**, not an obstacle to widen around.

## Refused by construction

```
assert_not_loosened(gate, before, after)  ->  RAISES
```

The rule:

> a gate may be changed by a **MEASUREMENT**, never by a **DESIRE TO CONTINUE**.

## Not to do

- DO NOT write a `run_forever()` or any count-based loop — a count promotes by repetition
- DO NOT count an iteration as progress; only a NEW DISTINCT CODE STATE is progress
- DO NOT widen a gate to keep the loop turning
- DO NOT treat a saturation stop as an obstacle
- DO NOT resolve an ambiguity by picking the largest or the most confident candidate
- DO NOT restate the loop rules in a second place; `discover_cases` is the ONE copy

## Where the rules live

| rule | source |
|------|--------|
| the loop, the gates, the stop | `discover_cases.py` |
| the streak over distinct states | `skill_contract_store.py` |
| the family letter | `contract_id` (schema-defined) |
