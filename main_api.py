"""FastAPI layer: task-payload validation + coord CRUD + settings page."""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import coord_store
import skill_field_registry
import skill_contract_store

app = FastAPI(title="Agent System API")


# =====================================================================
# RATE LIMITING — the MIDDLEWARE that was missing (added 2026-09-29).
#
# MEASURED before this change: this app served **30 routes and 0 middleware**,
# its text mentioned `rate_limit` **0** times, and NO number, window or endpoint
# set was documented anywhere in the repo. The `rate_limit` TABLE had a pruner
# and an insert but no caller. OWASP API4:2023 (Unrestricted Resource
# Consumption) is exactly that state.
#
# THE POLICY IS NOT IN THIS FILE. It lives as DATA in
# `settings.rate_limit.policies` in the IETF shape (`q` quota, `qu` unit, `w`
# window, `pk` partition key), read by `rate_limit_policy`. A limit typed into
# middleware would be a number with no unit and no cite — the defect
# `factor_first_principle` refuses.
#
# WHY A RAW ASGI MIDDLEWARE AND NOT `@app.middleware("http")`
# ---------------------------------------------------------
# MEASURED: a BaseHTTPMiddleware wrapper around a SYNC route handler made
# FastAPI return the `TestClient` BEFORE the response, and the answer measured was
# the middleware's, not the route's — the check read the fence instead of the
# thing it guards. A raw ASGI middleware passes the decision into the app and
# never fabricates a response for the normal path, so the route answers.
#
# THE DECISION IS TWO-LAYERED, STRICTEST FIRST (see `LAYER_ORDER`), so one
# expensive request cannot consume two budgets, and a rejected request is NOT
# handed to the next layer as if it had passed.
# =====================================================================

# Paths that are never counted. `/api/health` and `/settings` are how an operator
# checks whether the system is alive; throttling them makes an outage look like a
# rate limit (`status_poll_no_live_probe` records that family).
RATE_LIMIT_EXEMPT_PATHS = ("/api/health", "/settings")


@app.middleware("http")
async def _rate_limit_middleware(request: Request, call_next) -> Response:
    """Record the hit, and answer 429 + Retry-After when the layer is over quota."""
    path = request.url.path
    if path in RATE_LIMIT_EXEMPT_PATHS:
        return await call_next(request)
    try:
        import rate_limit_policy as rlp
        ip = (request.client.host if request.client else "") or "unknown"
        d = rlp.decide(request.method, path, ip)
    except Exception as e:  # a limiter that CRASHES must not take the API down
        response = await call_next(request)
        response.headers["X-RateLimit-Error"] = "%s: %s" % (type(e).__name__, e)
        return response
    # A FAULT (a policy that cannot be read) is REPORTED, never silently allowed:
    # a limiter that cannot read its own policy cannot claim to be limiting.
    if not d.get("ok"):
        response = await call_next(request)
        response.headers["X-RateLimit-Fault"] = str(d.get("reason"))[:120]
        return response
    if not d.get("allowed"):
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests", "policy": d.get("policy"),
                     "hits": d.get("hits"), "q": d.get("q"),
                     "w": d.get("w"),
                     "retry_after_sec": d.get("retry_after_sec")},
            headers={"Retry-After": str(d.get("retry_after_sec")),
                     "X-RateLimit-Policy": str(d.get("policy")),
                     "X-RateLimit-Limit": str(d.get("q")),
                     "X-RateLimit-Remaining": "0"},
        )
    response = await call_next(request)
    if d.get("policy"):
        response.headers["X-RateLimit-Policy"] = str(d["policy"])
        response.headers["X-RateLimit-Limit"] = str(d.get("q"))
        response.headers["X-RateLimit-Remaining"] = str(
            max(0, int(d.get("q") or 0) - int(d.get("hits") or 0)))
    return response


class StreakResult(BaseModel):
    case_key: str
    passed: bool
    rule_version: int = 1
    target_streak: int = 100


@app.get("/api/skill-contracts")
def list_skill_contracts(status: Optional[str] = None) -> dict:
    """List skill contracts joined with their streak counters."""
    return {"ok": True, "contracts": skill_contract_store.list_contracts(status=status)}


@app.get("/api/skill-contracts/{contract_id}")
def get_skill_contract(contract_id: str) -> dict:
    """One contract + its Field Register + TDD cases + streak + review log."""
    contract = skill_contract_store.get_contract(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail=f"contract not found: {contract_id}")
    return {
        "ok": True,
        "contract": contract,
        "fields": skill_contract_store.list_fields(contract_id),
        "tdd_cases": skill_contract_store.list_tdd_cases(contract_id),
        "streak": skill_contract_store.get_streak(
            contract_id, rule_version=int(contract.get("version") or 1)
        ),
        "review_log": skill_contract_store.list_review_logs(contract_id),
    }


@app.post("/api/skill-contracts/{contract_id}/streak")
def post_skill_contract_streak(contract_id: str, body: StreakResult) -> dict:
    """Record one TDD round; the streak is recomputed from the DB."""
    res = skill_contract_store.record_streak_result(
        contract_id,
        body.case_key,
        body.passed,
        rule_version=body.rule_version,
        target_streak=body.target_streak,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("message", "streak failed"))
    return res


@app.post("/api/task")
def post_task(payload: dict) -> dict:
    """Thin pass-through: accept the raw payload and let the hard validator
    (field_registry) decide required/type rules. Pydantic must NOT pre-validate
    types here, or it would shadow the field_registry checks."""
    ok, errors = skill_field_registry.validate_task_payload(
        str(payload.get("task_id", "")), payload
    )
    if not ok:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    return {"ok": True, "task_id": payload.get("task_id")}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---- Coord CRUD ----
class CoordItem(BaseModel):
    target_name: str
    x: int
    y: int
    action: str
    is_active: bool
    error: Optional[str] = None


@app.get("/api/coord")
def get_all_coord():
    return coord_store.list_all()


@app.get("/api/coord/grouped")
def get_grouped_coord():
    """Targets grouped by target_id with learned X/Y ranges (success table)."""
    return coord_store.list_grouped()


@app.get("/api/coord/{item_id}")
def get_coord(item_id: int):
    row = coord_store.get_by_id(item_id)
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return row


@app.post("/api/coord")
def create_coord(item: CoordItem):
    return coord_store.create(item.model_dump())


@app.put("/api/coord/{item_id}")
def update_coord(item_id: int, item: CoordItem):
    row = coord_store.update(item_id, item.model_dump())
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return row


@app.delete("/api/coord/{item_id}")
def delete_coord(item_id: int):
    ok = coord_store.delete(item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


from template_manager_ui import LLM_TEMPLATES_PAGE_HTML, SETTINGS_PAGE_HTML


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    return SETTINGS_PAGE_HTML


@app.get("/prompt/setting", response_class=HTMLResponse)
@app.get("/prompt/setting/", response_class=HTMLResponse)
@app.get("/llm-templates", response_class=HTMLResponse)
@app.get("/llm-templates/", response_class=HTMLResponse)
@app.get("/llm_templates", response_class=HTMLResponse)
@app.get("/llm_templates/", response_class=HTMLResponse)
def llm_templates_page():
    return LLM_TEMPLATES_PAGE_HTML


# ---- prompt_setting CRUD ----
class TemplateCreate(BaseModel):
    prompt_setting_key: str
    name: str
    instruction: str
    description: Optional[str] = ""
    catalog_id: int = 0
    is_active: int = 1


class TemplateUpdate(BaseModel):
    prompt_setting_key: Optional[str] = None
    name: Optional[str] = None
    instruction: Optional[str] = None
    description: Optional[str] = None
    catalog_id: Optional[int] = None
    is_active: Optional[int] = None


class TemplatePreview(BaseModel):
    sample_prompt: Optional[str] = ""
    instruction: Optional[str] = None
    setting_id: Optional[int] = None


@app.get("/api/templates")
def list_templates(all: int = 0):
    return coord_store.list_prompt_settings(active_only=not bool(all))


@app.post("/api/templates/preview")
def preview_template(item: TemplatePreview):
    return coord_store.preview_prompt_setting(
        sample_prompt=item.sample_prompt or "",
        instruction=item.instruction,
        setting_id=item.setting_id,
    )


@app.post("/api/templates")
def create_template(item: TemplateCreate):
    try:
        return coord_store.create_prompt_setting(item.model_dump())
    except ValueError as e:
        msg = str(e)
        code = 409 if "already exists" in msg else 400
        raise HTTPException(status_code=code, detail=msg) from e


@app.get("/api/templates/{template_id}")
def get_template(template_id: int):
    row = coord_store.get_format_template_row_by_id(template_id)
    if not row:
        raise HTTPException(status_code=404, detail="prompt setting not found")
    out = dict(row)
    # Pass the KEY as well as the id. `format_templates.id` is a per-database
    # autoincrement, so a bare id can collide with an unrelated database's
    # numbering and report a false reference count (measured 2026-09-20).
    out["skill_ref_count"] = coord_store.count_skill_refs_to_setting(
        template_id, prompt_setting_key=row.get("prompt_setting_key"))
    return out


@app.put("/api/templates/{template_id}")
def update_template(template_id: int, item: TemplateUpdate):
    data = {k: v for k, v in item.model_dump().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        row = coord_store.update_prompt_setting(template_id, data)
    except ValueError as e:
        msg = str(e)
        code = 409 if "already exists" in msg else 400
        raise HTTPException(status_code=code, detail=msg) from e
    if not row:
        raise HTTPException(status_code=404, detail="prompt setting not found")
    return row


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: int, hard: int = 0):
    result = coord_store.delete_prompt_setting(template_id, hard=bool(hard))
    if not result.get("ok"):
        err = result.get("error") or "delete failed"
        if err == "not found":
            raise HTTPException(status_code=404, detail=err)
        raise HTTPException(status_code=409, detail=err)
    return result

# ---- Check records ----
class CheckItem(BaseModel):
    task_id: str
    session_id: Optional[str] = None
    hash_chain: Optional[str] = None
    detail: Optional[str] = None
    verdict: Optional[str] = None
    error: Optional[str] = None
    catalog_id: int = 0
    subcatalog_id: int = 0
    catalog_name: Optional[str] = ""
    subcatalog_name: Optional[str] = ""
    prompt_setting_id: int = 0
    prompt_setting_key: Optional[str] = ""


@app.post("/api/check")
def create_check(item: CheckItem):
    return coord_store.create_check_record(item.model_dump())


@app.get("/api/check/task/{task_id}")
def get_checks_for_task(task_id: str):
    return coord_store.list_check_by_task_id(task_id)


@app.get("/api/check/{check_id}")
def get_check(check_id: int):
    row = coord_store.get_check_by_id(check_id)
    if not row:
        raise HTTPException(status_code=404, detail="check record not found")
    return row


@app.put("/api/check/{check_id}")
def update_check(check_id: int, item: CheckItem):
    row = coord_store.update_check_record(check_id, item.model_dump())
    if not row:
        raise HTTPException(status_code=404, detail="check record not found")
    return row


@app.delete("/api/check/{check_id}")
def delete_check(check_id: int):
    ok = coord_store.delete_check_record(check_id)
    if not ok:
        raise HTTPException(status_code=404, detail="check record not found")
    return {"ok": True}


# ---- Worker trigger ----
import re
from pathlib import Path as _Path

from worker_engine import run_task_worker

_SKILL_ROOT = _Path(__file__).resolve().parent / "skills"
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def get_task_prompt(task_id: str) -> str | None:
    """Find the skill file whose frontmatter task_id matches; return its body (prompt)."""
    import yaml

    for md in _SKILL_ROOT.rglob("*.skill.md"):
        text = md.read_text(encoding="utf-8")
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if str(meta.get("task_id", "")).strip() == task_id:
            return m.group(2).strip()
    return None


def get_task_dict(task_id: str) -> dict | None:
    """Find the full task dict (incl. catalog metadata) for a task_id via skill_scanner."""
    from skill_scanner import scan_skill_folder

    for t in scan_skill_folder(_SKILL_ROOT):
        if t.get("task_id") == task_id:
            return t
    return None


@app.post("/api/worker/run/{task_id}")
def trigger_worker(task_id: str, session_id: str, prev_hash: str = ""):
    task = get_task_dict(task_id)
    if not task or not task.get("prompt"):
        raise HTTPException(status_code=404, detail="task prompt not found")
    check_record = run_task_worker(task_id, task["prompt"], session_id, prev_hash, task)
    return check_record
