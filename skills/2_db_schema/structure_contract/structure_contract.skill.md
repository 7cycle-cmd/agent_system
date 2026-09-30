# structure_contract

Derive the folder name, file name, and location for **all seven levels** —
channel, module, capability, api, function, table, field — from the entity's
registered key, so a path is never hand-typed.

## Why this skill exists

Before this skill, `structure_contract.py` derived a path for four levels and
nothing for the other three. `code_location_register` had 298 rows, only 37
active, and 5 active rows whose path did not equal the derivation. 261 code
rows sat at flat root paths — the "801 root files" damage — with no rule saying
a code location is structured iff it sits under a derived capability folder.

## The seven levels

| level      | derived from                                   | shape                                   |
|------------|------------------------------------------------|-----------------------------------------|
| channel    | `channel_registry.channel_key`                 | `<channel_key>/`                        |
| module     | `module_registry.module_key` + channel         | `<channel_key>/<module_key>/`           |
| capability | `capability_registry.capability_key`           | `<channel>/<module>/<cap>/`             |
| api        | `api_registry.capability_id` → capability      | `<...>/<mod>_<cap>_api.py`              |
| function   | `function_registry.capability_id` → capability | the capability file itself (line = loc) |
| table      | `db_table_registry.table_key`                  | `<...>/<mod>_<cap>_schema.py`           |
| field      | `db_field_registry.db_table_id` → table        | same file as table (line = loc)         |

## The rule

A location row is activated **only** when its path **equals** the derivation.
A row whose path does not equal the derivation is **refused** with a named
code — reported, never forced:

- `PATH_MISMATCH` — the row's path is not the derivation.
- `UNSTRUCTURED_CODE` — a code row at a flat root path, under no capability.
- `UNRESOLVABLE_ENTITY` — the key resolves to no registered row.

## Not to do

- Do not hand-type a path — derive it from the registered key.
- Do not activate a row whose path does not equal the derivation.
- Do not move files on disk (this skill derives and reports, it does not move).
- Do not bless a flat root path as a structured location.
- Do not invent a level's rule without a citation to the register it reads.

## Source

`structure_contract.py` — `STRUCTURE_TERMS`, `structure_of`,
`api_structure_of`, `function_structure_of`, `table_structure_of`,
`field_structure_of`, `location_mismatches`, `activate_locations`.
