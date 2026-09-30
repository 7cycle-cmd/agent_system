# Skill — terminology_spelling

**skill_key:** `terminology_spelling`
**layer:** 1_core
**status:** active

---

## WHY THIS SKILL EXISTS

The human (2026-09-27):

> "alias must = terminonotology, all in same language, do it now"
> "fucking mis-undersatnd name never happen again, all the worker can have
>  happy and easy work"
> "`environment` this is wrong spelling, is it?"
> "terminontology_registered has splleing proof?"

**MEASURED, and the answer to the last question is NO:**

| question | answer | evidence |
|----------|--------|----------|
| is `environment` wrong? | **NO** — `environment` is CORRECT; `enviornment` is the typo | `ron` -> `iro` transposition at index 4 |
| does the register have a spelling proof? | **NO** | zero modules import a spell-checker; `terminology_registry` had no `spell` function |

`_proof_terminology_ko_spelling.py` has "spelling" in its NAME, but it proves
**alias identity** (two spellings of one concept resolve to one canonical key),
NOT **spelling correctness**. There was no dictionary anywhere, so a typo like
`enviornment` was NEVER caught.

**MEASURED POPULATION:** `environment` 12,423 occurrences / `enviornment` 3,970.
Of the 3,970, **3,584 are QUOTES of the human's own words** (evidence — must NOT
be changed) and **357 are real identifiers** (the defect).

---

## WHEN TO USE

- You are about to **register a NEW term** (`terminology_registry.add_term`)
- You are about to **add a NEW alias** (`terminology_alias.add_alias`)
- You are about to **name anything** — a table, a column, a route, a file, a
  module, a factor, a skill
- You catch yourself writing a word you are not sure how to spell

---

## THE RULE

```
Rule 1: A NEW term or alias MUST NOT contain a declared misspelling.
Rule 2: The check is a DECLARED TABLE (MISSPELLINGS), not a general dictionary,
        because a general dictionary would refuse the repo's OWN vocabulary
        (`5w1h`, `db_row`, `llm_100_run`, `CP-S-00`) — a false positive that
        blocks real work.
Rule 3: The rule is NARROW: it applies to a NEW term/alias only. Existing rows
        are NOT retro-refused (that would be a mass refusal of live data).
Rule 4: A legacy typo that is ALREADY an alias is KEPT for resolution, and the
        CORRECT spelling is ADDED as a second alias. Both resolve.
Rule 5: A QUOTE of the human's own words is EVIDENCE, not a defect. It is never
        rewritten.
Rule 6: A NEW term_key MUST be lowercase snake_case or dotted (TERM_KEY_RE).
        An uppercase constant (e.g. `LLM_OFF_FORM`) is a CARRIER — register it
        as an ALIAS of the lowercase term, never as a term. MEASURED 2026-09-27:
        all 1452 active terms already comply, so the rule refuses only NEW
        violations. The defect it stops: `LLM_OFF_FORM` (id=87) entered as a
        term_key when it is a Python constant in `stepwise_ask.py:127`.
```

**One sentence:** a new name must be spelled correctly AND in the right form;
a typo or an uppercase constant is refused at the write site, so it can never
enter the register as a term again.

---

## HOW IT IS ENFORCED

| write site | refusal code | when |
|------------|--------------|------|
| `terminology_registry.add_term` | `MISSPELLED_NAME` | a NEW term contains a declared typo |
| `terminology_registry.add_term` | `BAD_TERM_KEY_FORM` | a NEW term_key is not lowercase snake_case or dotted |
| `terminology_alias.add_alias` | `MISSPELLED_NAME` | a NEW alias contains a declared typo |

The checks are `terminology_registry.check_spelling(name)` — a SUBSTRING match
(so `enviornment` inside `enviornment_playwright` is caught), case-insensitive —
and `terminology_registry.check_naming(key)` — a FORM match against
`TERM_KEY_RE` (`^[a-z0-9]+(?:[._][a-z0-9]+)*$`).

---

## THE DECLARED TABLE

`terminology_registry.MISSPELLINGS` maps `typo -> correct`. It is deliberately
SMALL and PROVABLE: each entry is a known transposition, not a guess. Adding an
entry is a DECISION with a citation, not a heuristic.

Examples: `enviornment -> environment`, `recieve -> receive`,
`seperate -> separate`, `occured -> occurred`, `definately -> definitely`.

---

## REFUSALS (as data)

| code | when | because |
|------|------|---------|
| `MISSPELLED_NAME` | a NEW term/alias contains a declared typo | a name a reader must decode is a name that will be mis-understood |
| `BAD_TERM_KEY_FORM` | a NEW term_key is not lowercase snake_case or dotted | an uppercase constant is a carrier (an alias), not a term |

---

## WHAT THIS SKILL IS NOT RESPONSIBLE FOR

- NOT a general English spell-checker (it refuses only DECLARED typos)
- NOT rewriting existing rows (the rule is narrow, on purpose)
- NOT rewriting QUOTES of the human's words (they are evidence)
- NOT deciding what a term MEANS (that is `terminology_register`)

---

## CITE

- `terminology_registry.py:MISSPELLINGS`
- `terminology_registry.py:check_spelling`
- `terminology_registry.py:TERM_KEY_RE`
- `terminology_registry.py:check_naming`
- `terminology_registry.py:add_term`
- `terminology_alias.py:add_alias`
- `_proof_alias_must_be_terminology.py`
