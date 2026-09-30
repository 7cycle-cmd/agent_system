---
task_id: SKILL.ENTITY.ID.MINT
name: skill_entity_id_mint
skill_key: skill_entity_id_mint
version_label: skill-1.0
parser: entity_id_mint
schema: entity_id_mint
qc_summary: Entity ID mint - one call produces a VERIFIED 4-part entity id
---

# skill_entity_id_mint

Your job: produce ONE entity id, in the only shape that exists, and prove it
verifies. You do NOT invent ids, do NOT write code, do NOT register entities.

## The id shape (the ONLY shape)

```
{LETTER}-{table_id}-{row_id}-{version}          e.g.  F-38-11-1
```

| part | meaning | checked against |
|------|---------|-----------------|
| `LETTER` | which KIND of thing | `entity_type_register` |
| `table_id` | the TABLE the entity lives in | `db_table_registry.db_table_id` of the letter's register table |
| `row_id` | the ROW of that table | the register table's OWN primary key |
| `version` | a revision | `version_register` |

**The 3-part form `T-1-1` is the OLD one (2026-09-27).** It is MALFORMED, not
"an older form". One shape only, so the id is unambiguous.

WHY THE 3-PART FORM IS WRONG, MEASURED: with ONE trailing number, `T-1-5` reads
as *version 5* OR *row 5* - the reader cannot tell which. The version is the key
that creates the mis-understanding, so it must be its own part.

## There is NO row register

There is NO `db_row_registry`. The row id IS the register table's own primary
key. A second register to name a row that already has a primary key is the old
and wrong design.

## How to get an id

Call the API. Do NOT build the string yourself.

```
GET  /api/entity/id-for?path=<file>                          -> the id AND the line to paste into a plan
GET  /api/entity/format?letter=&table_id=&row_id=&version=   -> build + verify
POST /api/entity/mint    {letter, row_id, version?}          -> {entity_id}
GET  /api/entity/<id>                                        -> verify
```

`table_id` is LOOKED UP from the letter - you do not supply it, so it cannot be
wrong.

## Required output

```
ENTITY_ID: [the id]
PARTS:
- letter:   ...
- table_id: ...
- row_id:   ...  (entity_key: ...)
- version:  ...
VERIFIED: [YES | NO]
REFUSAL:  [reason, or (none)]
```

## Hard Rules

1. **Never hand-build an id.** Call the API.
2. **Never omit a part.** All FOUR are required; a 3-part id is malformed.
3. **Never invent a `row_id`.** It must be a real row in the letter's register table.
4. **Never invent a `table_id`.** It is looked up from the letter.
5. **A refusal is a valid answer.** Report it; do not retry with a guessed value.
6. **Never claim VERIFIED without calling `GET /api/entity/<id>`.**

## Examples

### Example 1 - a function

Request: mint an id for the function registered as row 11 of `function_registry`

```
GET  /api/entity/format?letter=F&table_id=38&row_id=11&version=1  -> F-38-11-1
GET  /api/entity/F-38-11-1                                        -> exists: true
```

```
ENTITY_ID: F-38-11-1
PARTS:
- letter:   F  (function)
- table_id: 38 (function_registry)
- row_id:   11 (entity_key: ...)
- version:  1
VERIFIED: YES
REFUSAL:  (none)
```

### Example 2 - a file

```
GET /api/entity/id-for?path=entity_id.py
  -> entity_id: R-1-75-1
  -> line:      **Entities:** R-1-75-1
```

### Example 3 - a refusal

Request: mint `F-38-999999-1`

```
REFUSAL:  no function_registry row with function_id = 999999
```
