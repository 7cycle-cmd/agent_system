# Member region + phone SSOT (split model)

**Status:** locked L1.2  
**Law:** `region = +CC` (e.g. `+86`) · `phone = local only` (e.g. `13800138000`)  
**Compose (detect only):** `e164 = region + phone` → `+8613800138000`  
**ISO2 CN/HK:** optional display via lookup — **not** the phone field  

## Field split

| field | example raw | norm | role |
|-------|-------------|------|------|
| **region** | `+86`, `86`, `＋86` | `+86` | **primary** country dial policy / length map |
| **phone** | `13800138000`, `138-0013-8000` | `13800138000` | local digits only · **no** `+` · not INT |
| **country** (opt) | China | `China` | text lookup from region |
| **e164** | (composed) | `+8613800138000` | Pair QC / dial compose only · not master storage |

```text
region  = +86              <- NOT the phone number
phone   = 13800138000      <- local national number
e164    = region + phone   <- pure compose for compare/TDD pack
```

## Multi-dim SSOT table

| slice_key | field | source | dependency | business_rule | edge_case | fail_class |
|-----------|-------|--------|------------|---------------|-----------|------------|
| region | region | MCP/UI | none | must be `+` + digits (calling code) | `86`→`+86`; ISO2 `CN`→`+86` map | business_defect |
| phone | phone | MCP/UI | **region** (for length policy) | digits only; length by region policy | dashes/spaces stripped; reject letters/emoji | business_defect |
| country | country | MCP/lookup | region | text name from region→country table | empty if region empty | business_defect |
| (extract) | * | UI | — | null frame / loading | blank screenshot | transient_execution |

## Pair QC normalize (pure)

1. **region:** strip; bare `86`→`+86`; ISO2 `CN`→`+86`; output `+digits`.
2. **phone:** strip `-` ` ` `()`; if raw contains `+CC`, **split** → region + local; phone.norm = **local digits only**.
3. **e164 compose:** `region.norm + phone.norm`.
4. **compare phone:** local norms equal (optional e164 if both have region).
5. **raw never rewritten.** Same raw → same norm always.

## Builder / MCS

- Function Builder cards: region / phone / country contracts
- Actions: create|update × function|API|table|field → **one child task each**
- MCS declares; Pair QC detects; never auto-fix business data
