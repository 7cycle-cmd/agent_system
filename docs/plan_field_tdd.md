# Field TDD (shared loader) — plan

**Status:** done  
**Table:** existing `field_tdd_rule` on agent.db  
**Module:** `field_tdd.py`

## Law

- MCS **declares** rules (`register_id` + `rule_json` + `depends_on_json`)
- Pair QC / API / UI **consume** the same loader
- Pair QC gate stays **never** (detect ≠ fix)
- API write path **may enforce** via `validate_value_against_tdd`
- **No** parallel `member_db.sqlite` / no second `CREATE TABLE field_tdd_rule`

## Column map (import payload → live table)

| payload | live column |
|---------|-------------|
| system | `system_key` |
| field | `slice_key` + `field_name` |
| depends | `depends_on_json` (JSON array) |
| op / fail_class / patterns | inside `rule_json` |
| register_id | `register_id` (= Function ID) |

## Canonical membership fields

1. region — `match` `^\+\d{1,4}$`
2. phone — `local_digits_by_region` depends `region`
3. address — `text_length_clean` 3..500
4. name — `range_len` 1..80
5. gender — `in_set` M/F/X/U
6. contact_method — `min_len` 2

## CLI

```powershell
python field_tdd.py import
python field_tdd.py selftest
python field_tdd.py load --field phone
python managed_coding.py import-tdd
python pair_qc.py selftest
```

## Reject

- New DDL that renames columns away from MCS spine
- Hardcoding validators only in FastAPI/Pydantic as SSOT
- Storing Task ID (`1.2`) as Function ID
