# Source location + dead-function cleanup (CH7 / W11–W12)

**Locked:** 2026-09-14  
**Pipelines:** C (Code Health) + D (Managed Coding register) + watchdog side-job  
**Gate:** never · **Delete source:** never (mark only)

## Goal

Every managed function can answer:

1. **Where** is the code? → `file_path` + `line_start`–`line_end` (+ optional `code_span`)
2. **How often** used? → `usage` = `total_invocations`
3. **Pass / fail times?** → `pass_count` / `fail_count`
4. **Score?** → `pass / usage * 100` (null if usage=0)
5. **Dead / dirty?** → `status` + `usage==0` → easy cleanup list
6. **Who opens work?** → watchdog / health spawns `code.cleanup` `dev_task`

Example:

| field | value |
|-------|--------|
| file | `test.py` |
| lines | 1–3 |
| code_span | `csgvuyfbvfhdbvljdsfkvbdfvb` |
| status | `active` |
| usage | 3 |
| pass | 2 |
| fail | 1 |
| score | `66.6667` |

## Tables

| table | role |
|-------|------|
| `code_register` | identity + **file/line/code_span** + register status |
| `function_scoring` | usage / pass / fail / score / health status (+ mirrored file/line) |
| `function_invoke_trace` | each run ok/fail under TACID |
| `dev_task` + action `code.cleanup` | cleanup work items (pending) |

## Status rules (unchanged)

- `zombie` = declared static, usage=0  
- `dead_candidate` = no static, usage=0  
- `active` = usage>0 recent  
- `inactive` = usage>0 stale  
- rubbish/zombie **mark only** — never auto-delete `.py`

## APIs / CLI

```text
POST /api/health/bind     {register_id|module+function, file, line_start, line_end, code?}
POST /api/health/cleanup  {limit?}
GET  /health              bind form + usage/pass/fail/score + file:line columns

python code_health.py bind --module M --function F --file test.py --line-start 1 --line-end 3
python code_health.py cleanup --migrate
python code_health.py report
```

## Watchdog

Every N scans (`DEAD_FN_CLEANUP_EVERY_N_SCANS=5`):  
`spawn_dead_function_cleanup_tasks` → pending `ch.cleanup.<mod>.<fn>` tasks.  
Heartbeat trunk never blocked; side-job failures are warnings only.

## Reject

- score as QC gate  
- auto-delete source  
- parallel experiment.db  
- cleanup without register/scoring trail  

## Done when

- bind stores file+line+optional code on register  
- report shows usage/pass/fail/score + file:line  
- dead list → cleanup tasks (UI button + watchdog)  
- selftest includes W11/W12  
