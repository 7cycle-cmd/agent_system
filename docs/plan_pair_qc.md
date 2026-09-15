# Pair QC — dual-path detect (MCP SSOT 🆚 UI evidence)

**Status:** Level-1 DONE (detect + case + normalize)  
**Pipeline:** `E_pair_qc` (orchestrator business QC)  
**Gate:** **never** (does not flip Pipeline A `match_ok`)  
**DB:** `agent.db` only  

## Why

API-only success is a blind spot: backend JSON can be correct while UI shows wrong data (render/cache/bug).

```text
MCP / backend SSOT  🆚  OpenClaw screen-frame UI extract
        ↓ normalize (TDD-aware)
     compare → pass | fail case
```

- Both agree → pass (stored == displayed)  
- Disagree → **case** with both raw evidences + `register_id` + `tdd_rule_id`  
- Detect is guaranteed when at least one path returns; **auto-fix is not**

## Law

```text
detect ≠ fix
never gate schema_qc
never auto-delete source
normalize before compare (anti false-positive: +86138… vs 138-0013-8000)
no register_id → not managed pair target (prefer bound register)
```

## Layer split

| Layer | Owner |
|-------|--------|
| Expected / TDD / register | MCS (D) — `field_tdd_rule`, `code_register` |
| Dual fetch + normalize + compare + case | **Pair QC (E)** this plan |
| Live MCP tool / full Screen-Frame pack | later orchestrator L2+ |

## Tables

### `pair_qc_run`
One compare run: mcp_raw/norm, ui_raw/norm, match_ok, both vision ids, register_id, tdd_rule_id, case_id.

### `pair_value_ssot`
Optional backend value cache (seed/sql/mcp/api) keyed by register_id + field.

### Reuse
`vision_asset`, `fault_event` (+ facts), `code_register`, `field_tdd_rule`, `dev_task` action `qc.pair_verify`.

## Flow (L1)

1. Resolve handoff: `register_id` + field/slice + optional `tdd_rule_id`  
2. **Normalize** both sides (phone: strip spaces/dashes, unify +CC)  
3. Fetch MCP SSOT (L1: explicit value or `pair_value_ssot` seed)  
4. Fetch UI extract (L1: explicit value; optional MCP screenshot → `vision_asset`)  
5. Compare norms (+ optional TDD pattern check)  
6. Write `pair_qc_run`  
7. On fail → `fault_event` (`pair_qc_mismatch`) + facts + optional `qc.pair_verify` task  

## False-positive guard (L1.2 split)

Before equality:

- **region** = `+CC` primary (e.g. `+86`); bare `86` / ISO2 `CN` → `+86`
- **phone** = **local digits only** (e.g. `13800138000`); strip `-` ` ` `( )`; **not INT**; no `+` in storage
- if phone raw embeds `+CC`, **split** → region + local; phone.norm stays local
- **e164** = compose `region + phone` for pack/compare only — never master phone storage
- ISO2 `CN`/`HK` = display lookup only
- normalize is a **pure function** (same raw → same norm always); raw never rewritten
- fail_class: `business_defect` (formal case) | `transient_execution` (retry, no formal case)

SSOT table: `docs/ssot_member_phone_region.md`

```powershell
python pair_qc.py contracts
python pair_qc.py selftest
python pair_qc.py run --field phone --region +86 --mcp-value "+86 138-0013-8000" --ui-value "138-0013-8000"
python pair_qc.py run --field phone --region +86 --mcp-value "13800138000" --ui-value "1380013800"
```

## Reject

- Pair QC as schema hard gate  
- Auto-fix UI/backend from pair fail  
- Compare raw strings without normalize  
- Store full E.164 in the phone field / use INT for phone  
- Rebuild register/TDD tables inside pair_qc  

## Done when

1. Selftest: region `+86`/`86`/`CN` → same; phone local norms equal → **pass**  
2. Selftest: wrong local length → **fail** + case with register/tdd facts  
3. UI/API can trigger offline run and list recent runs  
4. MCS handoff unchanged; pair consumes `register_id`  
5. `/managed` shows Task header + 8 action cards + region/phone/country contracts  
