# SKILL — name classify (the old->new name gate)

**skill_key:** `skill_name_classify`
**taxonomy_path:** `module/task_center/skill_name_classify`
**owner:** `name_classify.py`
**module:** `task_center`
**capability:** `task_center.name_classify`

## WHAT

Classify EVERY registered name as **RETIRED** or **CURRENT**, from **TWO**
signals, and refuse to guess.

## WHY (the defect this exists to stop)

```
terminology_register.is_active:   0 -> 43 rows,   1 -> 9 rows
those 43 inactive rows INCLUDE:   chat_system, vscode, conversation, app,
                                  channel, capability_tool, ...
```

`is_active = 0` **alone does NOT mean "retired name"**. `name_classify.py
--blind` MEASURES the damage: a blanket `is_active = 0` would have marked **42 of
43** terms RETIRED with NO rename evidence, and **3 of them ARE current registry
keys**. So the rule needs BOTH signals, and a disagreement is REPORTED.

## THE RULE (checkable)

| `is_active` | rename evidence | class | action |
|-------------|-----------------|-------|--------|
| 0 | YES | `RETIRED` | keep the flag, keep the alias to the current name |
| 1 | NO | `CURRENT` | nothing to do |
| 0 | NO | `NOT_CLASSIFIED` | **REPORTED**, never guessed |
| 1 | YES | `CONFLICT` | **REPORTED**, never resolved by picking a side |

## HOW (the command that can FAIL)

```
.\.venv\Scripts\python.exe name_classify.py --census
.\.venv\Scripts\python.exe name_classify.py --mislabelled
.\.venv\Scripts\python.exe name_classify.py --blind
.\.venv\Scripts\python.exe name_classify.py --until-converged
```

**PASS** is `mislabelled == 0` AND every class assignment NAMES its carrier row.
**FAIL** is any `NOT_CLASSIFIED` or `CONFLICT` that was silently resolved.

## HARD RULES

1. **A CALL IS NOT "CURRENT" UNLESS `classify_one` SAYS `CURRENT`.** A name is
   never assumed live from a comment, a file name, or a memory.
2. **NO BLANKET `is_active = 0`.** Measured to mislabel 42 terms.
3. **EVERY WRITE NAMES ITS CARRIER.** `is_active` is set only with the
   `measured:` cite of the row that decided the class.
4. **A DISAGREEMENT IS NOT A VOTE.** `CONFLICT` and `NOT_CLASSIFIED` are REPORTED.
5. **`until_converged` IS BOUNDED.** "non stop" means the process keeps working
   until the state is FIXED, with a hard cap and a NAMED stop reason — never an
   unbounded loop (this repo has recorded loop incidents).

## WHERE the evidence lives (five carriers, already built)

`terminology_alias.resolve_name` asks all five and returns WHICH one answered:
`terminology_register` / `alias_list` / `legacy_id_map` / a SQL VIEW /
`schema_migration_log` / the declared `CODE_ONLY_RENAMES`.

## THE RESULT AFTER APPLYING IT

```
census       : RETIRED=1  CURRENT=68  NOT_CLASSIFIED=0  CONFLICT=0
mislabelled  : 0
blind damage : 0 of 1  (the rule was applied WITH the evidence, not instead of it)
door gaps    : 0
```
