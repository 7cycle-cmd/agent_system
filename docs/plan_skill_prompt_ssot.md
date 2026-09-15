# Skill Prompt SSOT — plan

**Status:** Phase 0–5 implemented (parser, file+DB SSOT, 100-run harness, `/llm-tasks` panel, gold cases, Task Center root 10, improve→draft)  
**Gate:** never  
**DB:** `agent.db` only  

## Goal

Versioned LLM prompts as multi-dim SSOT skills (not hardcoded). Task Center + `/llm-tasks` load / improve / test / promote. Mouse Spot verify uses `Result: YES/NO`.

## Law

```text
skill_key + version_label = one prompt document
one active version per (skill_key, prompt_key)
promote only after proof test (pass_gate) unless force
inference always records skill version
100-run = stability; gold cases = correctness
```

## Task ID coding

`{Root}.{GlobalSeq}` — one continuous counter per root across F/A/T/D/J/E. Never renumber on delete. Type metadata separate.

## Tables

- `skill_prompt_ssot`
- `skill_prompt_case`
- `skill_prompt_test_run`
- `skill_prompt_inference`

## Files

| Path | Role |
|------|------|
| `skill_prompt.py` | load/render/seed/test CLI+lib |
| `skill_prompt_ext.py` | Phase 5: gold cases, TC root 10, improve→draft, gold suite |
| `skills/mouse_spot_verify/v1_strict.json` | file bootstrap |
| `vision_analyze.py` | `parse_verify_response`, `format_json` off for verify |
| `mouse_spot_helper.py` | `/api/skills*`, `api_analyze` SSOT, Skill panel UI |
| `db_schema.py` | DDL + ensure |

## CLI

```powershell
python create_db.py --migrate
python skill_prompt.py seed
python skill_prompt.py list --skill mouse_spot_verify
python skill_prompt.py test --skill mouse_spot_verify --expected NO --runs 10 --target "Visual Studio Code"
python skill_prompt_ext.py seed-all
python skill_prompt_ext.py lines
python skill_prompt_ext.py list-cases
python skill_prompt_ext.py test-gold --runs 1
python skill_prompt_ext.py improve-draft
```

## UI

`http://127.0.0.1:18765/llm-tasks` → **Skill Prompt SSOT** panel:

- Reload / Seed v1 / Seed all / Task IDs
- Save draft / Improve→draft / Run proof test / Test gold / Promote active
- Yes/No % + pass_gate
- To chat / From chat with Prompt analyze box

## APIs (Phase 5)

- `POST /api/skills/seed-all` · `POST /api/skills/seed-tasks` · `GET /api/skills/task-lines`
- `GET /api/skills/<key>/cases` · `POST .../cases/seed`
- `POST /api/skills/<key>/test-gold` · `POST .../improve-draft` (draft only; never activate)

## pass_gate

`accuracy_pct >= 95` AND `other == 0` AND `errors == 0`

## Gold cases (active)

- `msv_shot_vscode_no` · `msv_shot_generic_target_no` · `msv_shot_doubao_no` (screenshot NO fixtures)
- Icon crops under `mouse_spot_targets/` seeded as **draft** reference only

## Task Center

Root label `10` under module `mouse_spot_helper` / version `skill-1.0` · items `10.1`–`10.20` continuous F/A/T/D/J/E

## Next (follow-on)

- YES-labeled gold when a true hit screenshot exists
- Attach `case_id` on `skill_prompt_test_run` from gold suite
- Inference ledger from suite path (analyze already records version)
