# Function API Docs (Swagger-like) — plan

**Status:** planned (MCS9)  
**Layer:** Pipeline D · Managed Coding / Function Builder  
**Gate:** never  
**DB:** existing `agent.db` only — **no** parallel schema, **no** FastAPI as TDD SSOT  
**UI target:** same family as FastAPI `/docs` (try-it + schema + mapping), **per managed function**

---

## 0. Why

FastAPI Swagger shows:

- path + method
- parameters / body schema
- try-it
- short description

We already build **functions** under one table spine:

```text
code_register.register_id   = Function ID
field_tdd_rule              = field contract (op / pattern / depends_on)
task_ssot profile A–H       = field · table · api · function · tdd · trace · module · register
fn_request / fn_research    = human ask + research path
```

**Gap:** `/managed` has Function Builder + a few hard-coded contract cards (region/phone/country), but **no** generic per-function API doc page with clear **mapping + explanation**, generated on build.

**Goal:** when Function Builder generates / refreshes a function, it also materializes a **doc envelope** (same table format) so UI can render Swagger-like pages for **every** `register_id`.

---

## 1. Law (locked)

```text
1. Function ID     = code_register.register_id only
2. Task ID         = dev_task.task_label / tacid (1.1, 1.2…) — NOT Function ID
3. TDD SSOT        = field_tdd_rule via field_tdd.load_field_tdd (not Pydantic models)
4. Doc is declare  = MCS owns mapping + explanation; runtime call is optional downstream
5. Same recipe     = API doc and SQL/table map are the same multi-dim row set; only dim values change
6. No rubbish      = one doc envelope per register_id (upsert), not N markdown forks
7. Gate            = never (docs do not flip match_ok / do not auto-fix business data)
```

**Reject**

- New `member_db.sqlite` or second `field_tdd_rule`
- Hardcoding OpenAPI only in Python FastAPI models as SSOT
- Treating Task ID `1.2` as Function ID in paths or UI labels
- Auto-creating live ERP write endpoints without register + TDD bind
- Per-function free-text README that drifts from DB

---

## 2. What “similar UI” means (product)

Per **Function ID**, a page that looks like FastAPI docs:

| Swagger block | Our source |
|---------------|------------|
| Tag / group | `system_key` + `module_name` |
| Operation id | `function_name` |
| Summary | `code_register.notes` or research equation line |
| Description | TDD `note` + slice explanation + depends_on |
| Path | `profile.C.api` (declared path string) |
| Method | default `GET` read / `PUT` write **as declare dims** (not auto-wired runtime) |
| Path params | e.g. `item_id` ↔ table PK when profile says so |
| Body / field schema | `field_tdd_rule.rule_json` + `field_name` / `slice_key` |
| Example value | TDD `example` |
| Try it | optional: call **validate-only** or **managed_invoke** — never silent business fix |
| Mapping panel | Function ID ↔ Task ID ↔ table.field ↔ TDD id ↔ register status |

**Two surfaces (same data):**

1. **Catalog** — `/api-docs` or `/managed/docs?system=membership`  
   list operations like Swagger “default” group  
2. **One function** — `/managed/docs?register_id=reg_…`  
   expanded operation + mapping + try-validate

Keep `/managed` as **builder**; docs page is **consumer view** of what builder already wrote.

---

## 3. Mapping model (clear explanation per function)

Every managed function exposes a stable **mapping card**:

```text
Function
  function_name     member_phone
  register_id       reg_membership_member_phone_…
  status            active|draft|…

Task coordinate (not Function ID)
  task_label / tacid   1.2
  slice_task_id        …
  root_task_id         …

Data map
  system_key        membership
  module_name       membership
  slice_key         phone
  table             <profile.B.table / plan.table>
  field / column    <profile.A.field / field_name>

API declare
  path              /api/membership/phone   (profile.C.api)
  methods           GET (read) · PUT (write declare)
  operation_id      member_phone

TDD contract
  tdd_rule_id       field_tdd_rule.id
  op / pattern      local_digits_by_region / …
  depends_on        ["region"]
  fail_class        business_defect
  example           13800138000
  note              phone = LOCAL only …

Trace
  profile.F.trace   required?
  handoff keys      register_id · tdd_rule_id · slice_key · plan.table
```

This is the “clearly mapping and explanation” layer — **one card per function**, filled from tables, not hand-written HTML.

---

## 4. Same table format (storage)

### 4.1 Prefer existing columns first

| Need | Existing home |
|------|----------------|
| Function identity | `code_register.*` |
| Field rules | `field_tdd_rule.*` |
| API path string | `task_ssot` dim `profile.C.api` |
| Field / table / fn dims | `profile.A.field` · `profile.B.table` · `profile.D.function` · `profile.H.register_id` |
| Human request context | `fn_request` · `fn_research` |
| Machine contracts | `managed_coding.contracts_doc()` / `layer_architecture()` |

### 4.2 Add **one** envelope table (only if needed)

If packing OpenAPI-ish JSON into scattered dims is too weak for UI, add **one** table on agent.db:

```text
function_api_doc
  id
  register_id          UNIQUE  → code_register.register_id
  system_key
  module_name
  function_name
  slice_key
  task_id              (slice task pk; nullable)
  operation_json       -- OpenAPI Operation-like object (summary, description, parameters, requestBody, responses)
  mapping_json         -- Function/Task/Table/Field/TDD/API map (section 3)
  openapi_fragment     -- optional PathItem fragment for this op only
  status               active|draft|stale
  source               managed_coding.build | import | manual
  updated_at / created_at
```

**Law:** `operation_json` / `mapping_json` are **projections** of register + TDD + SSOT.  
Rebuild on every successful `build_fn_request` / `import_membership_field_tdd` / seed.  
If projection drifts → mark `stale`; never treat doc JSON as TDD SSOT.

### 4.3 OpenAPI catalog (derived, not SSOT)

```text
GET /openapi.json?system=membership
  → assemble paths from function_api_doc (or live join if table deferred)
GET /api-docs
  → Swagger-like HTML (can be minimal static UI; need not embed full Swagger UI bundle v1)
```

Whole-spec document is **generated read model**. Source of truth remains register + TDD + SSOT (+ envelope cache).

---

## 5. Generation on Function Builder build

Hook points (same spine, no side DB):

```text
build_fn_request / seed_membership_system / import-tdd
  → ensure code_register row
  → ensure field_tdd_rule row(s)
  → ensure task_ssot profile A–H (especially C.api)
  → upsert function_api_doc (mapping_json + operation_json)
```

### 5.1 Generator sketch

```text
build_function_api_doc(conn, register_id) -> dict
  load code_register
  load field_tdd via load_field_tdd(... register_id or slice_key)
  load task_ssot dims for slice_task_id
  load optional fn_request/research notes
  mapping = { function, task, data, api, tdd, trace }
  operation = {
    operationId, summary, description,
    tags: [system_key],
    parameters: path/query from mapping,
    requestBody: schema from TDD (type, pattern, min/max, enum, example),
    responses: { 200: ok schema, 400: tdd fail envelope }
  }
  upsert function_api_doc
```

### 5.2 Schema from TDD (not Pydantic SSOT)

| TDD op | OpenAPI-ish schema fragment |
|--------|------------------------------|
| `match` + pattern | `string` + `pattern` |
| `local_digits_by_region` | `string` + pattern by region context; document `depends_on: region` |
| `range_len` / `min_len` / `max_len` | `minLength` / `maxLength` |
| `in_set` | `enum` |
| `text_length_clean` | string + min/max + description |

Validation at try-it time: **`validate_value_against_tdd`** (shared loader).

### 5.3 Default declared paths

```text
profile.C.api default:
  GET  /api/{system_key}/{slice_key}
  PUT  /api/{system_key}/{slice_key}      # write declare — enforce TDD when implemented
  GET  /api/functions/{register_id}       # mapping + doc only
  POST /api/functions/{register_id}/validate  # body value → TDD result (safe first runtime)
```

**Phase split**

| Phase | Ship |
|-------|------|
| **MCS9a** | mapping + operation projection + docs UI (read-only try = validate) |
| **MCS9b** | optional real GET/PUT handlers bound to register (still TDD-gated) |
| **MCS9c** | full OpenAPI aggregate + nicer Swagger UI skin |

Do **not** block 9a on live ERP writes.

---

## 6. UI IA

### 6.1 Builder (`/managed`) — extend, don’t replace

Keep current:

- Task header · Function ID picker · 8 action cards · field contracts · research/build

Add:

- Link: **API docs for this Function ID** → `/managed/docs?register_id=…`
- After build success: show “doc upserted” + operation path
- Field contracts section becomes **generic**: all slices with TDD for system (not only region/phone/country)

### 6.2 Docs page (`/managed/docs`) — FastAPI-like

```text
Header: system · module · OpenAPI json link
Left/list: operations grouped by tag (slice/function)
Main:
  METHOD  path
  summary
  description (explanation)
  Mapping card (section 3)
  Parameters
  Request body example (from TDD example)
  Responses
  Try it: validate value [ + region context if depends_on ]
```

Visual language can mirror FastAPI (method color chips, path, schema tabs) without requiring FastAPI runtime.

### 6.3 JSON APIs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/managed/docs?system=` | catalog list |
| GET | `/api/managed/docs?register_id=` | one function doc + mapping |
| GET | `/openapi.json?system=` | assembled OpenAPI 3 fragment |
| POST | `/api/functions/{register_id}/validate` | TDD validate-only |
| POST | `/api/managed/docs/rebuild` | reproject all docs for system |

---

## 7. Relation to 8 action cards

Today: create|update × function|API|table|field = **declare-only scaffolds**.

Under this plan:

| Action | Doc effect |
|--------|------------|
| create/update **function** | refresh `code_register` + `function_api_doc` mapping |
| create/update **API** | refresh `profile.C.api` + operation path/method in envelope |
| create/update **table** | refresh `profile.B.table` in mapping |
| create/update **field** | refresh `profile.A.field` + `field_tdd_rule` + requestBody schema |

Still **no auto DDL** unless a later explicit phase says so. Docs always follow declarations.

---

## 8. Work packages

| ID | Work | Done when |
|----|------|-----------|
| **MCS9.0** | This plan locked in `docs/plan_function_api_docs.md` + TODO pointer | law + reject list agreed |
| **MCS9.1** | `build_function_api_doc()` pure builder from register+TDD+SSOT | unit/selftest mapping for phone/region |
| **MCS9.2** | DDL `function_api_doc` (or proven join-only v1) + migrate | schema verify |
| **MCS9.3** | Hook build/seed/import-tdd → upsert doc | build returns `api_doc` block |
| **MCS9.4** | GET `/api/managed/docs` + `/openapi.json` | JSON catalog + one-op |
| **MCS9.5** | HTML `/managed/docs` Swagger-like | list + detail + mapping card |
| **MCS9.6** | POST validate-only endpoint | uses `validate_value_against_tdd` |
| **MCS9.7** | `/managed` links + generic contract cards from DB | no hard-code-only trio |
| **MCS9.8** | selftest + contracts_doc() entry | `python managed_coding.py selftest` green |

**Out of scope unless asked**

- Full FastAPI app replacement of `db_browser.py`
- Auto business data repair from try-it
- Pair QC gate change

---

## 9. Selftest checklist

```text
1. build/import membership → ≥6 functions have mapping with register_id
2. phone mapping: Task 1.2 ≠ Function ID; depends_on includes region
3. operation requestBody example phone = 13800138000; region example = +86
4. GET docs by register_id returns mapping_json + operation_json
5. validate 13800138000 + region +86 → ok; bad length → business_defect
6. openapi.json paths include profile.C.api values
7. rebuild is idempotent (UNIQUE register_id upsert)
```

---

## 10. CLI / UX commands (target)

```powershell
python managed_coding.py docs-rebuild --system membership
python managed_coding.py docs --register-id reg_membership_member_phone_...
python field_tdd.py load --field phone
python db_browser.py --port 8768
# http://127.0.0.1:8768/managed?system=membership
# http://127.0.0.1:8768/managed/docs?system=membership
# http://127.0.0.1:8768/managed/docs?register_id=reg_...
# http://127.0.0.1:8768/openapi.json?system=membership
```

---

## 11. One-line summary

**Function Builder already writes register + TDD + profile dims; MCS9 projects each `register_id` into a Swagger-like doc+mapping envelope (same DB spine) so every function has a clear API explanation UI — validate-first, runtime write later, never a second SSOT.**
