"""
skill_task_format_worker_taxonomy_validator — deterministic pre-dispatch gate.

Validates Task | Channel | Module | Capability | API | Function | Table | Field
against project SSOT + code_registry. Does NOT execute business logic, mutate
ontology, or auto-fix payloads.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB = BASE_DIR / "agent.db"
SKILLS_DIR = BASE_DIR / "skills"

SKILL_KEY = "skill_task_format_worker_taxonomy_validator"
SKILL_VERSION = "v1_strict"
SKILL_PRODUCT_VERSION = "skill-1.0"
VALIDATOR_MODULE = "task_center"
VALIDATOR_FUNCTION = "task_center.validate_new_task"
VALIDATOR_REGISTER_HINT = "skill.task_format_worker_taxonomy_validator"
VALIDATOR_FILE = "src/task_center/skill_task_validate.py"
VALIDATOR_API = "POST /api/tasks/validate"
VALIDATOR_API_ALIAS = "GET /tasks/validate"
CHANNEL_CODE = "local_pc"
CHANNEL_NAME = "Local PC"
MODULE_CODE = "task_center"
MODULE_NAME = "Task Center"
VERSION_LABEL = "tc-skill-1.0"
TASK_LABEL = "T-SKILL01"
CAPABILITY_ID = "task_center.validate_new_task"
SOURCE = "skill_task_validate"

# The scope levels at which the hardcode question is ANSWERABLE. Deliberately a
# COPY of `hardcode_scan.PRECISE_LEVELS` as a literal-free tuple? No — it is read
# FROM that module below, at call time, so the two cannot drift. This constant
# exists only as the fallback when the scanner cannot be imported, and the
# fallback is the CONSERVATIVE one (fewer precise levels => more UNKNOWN => more
# review), never the permissive direction.
_HSC_PRECISE_LEVELS: tuple[str, ...] = ()


def _hardcode_precise_levels() -> tuple[str, ...]:
    """`hardcode_scan.PRECISE_LEVELS`, or an EMPTY tuple when unimportable.

    Empty is the safe direction: with no precise level, every candidate is
    treated as aggregate-only, i.e. as something a human must look at. A scanner
    that cannot load must never make the gate more permissive.
    """
    try:
        import hardcode_scan as _hsc

        return tuple(str(x) for x in _hsc.PRECISE_LEVELS)
    except Exception:
        return ()


def _changed_files_from_payload(payload: Any) -> list[str]:
    """The files this task changed, from the payload's OWN field.

    THE RULE (same as the QC gate's task id): read the declared field, NEVER
    guess. An absent list means the gate is SKIPPED AND SAID — a guessed file
    list would key the gate on files nobody declared, which is the `task`-label
    defect in a new place.

    Accepted keys (`files_changed` is the canonical one):
        files_changed / changed_files / files
    Value may be a list of relative paths, or a comma / newline separated string.
    """
    if not isinstance(payload, dict):
        return []
    for key in ("files_changed", "changed_files", "files"):
        if key not in payload:
            continue
        raw = payload.get(key)
        if raw is None:
            continue
        if isinstance(raw, (list, tuple, set)):
            out = [str(x).strip() for x in raw if str(x).strip()]
        else:
            out = [p.strip() for p in re.split(r"[,\n]", str(raw)) if p.strip()]
        # A relative path only: an absolute path would let a caller point the
        # gate at a file the task never touched.
        return [p for p in out if not Path(p).is_absolute()]
    return []

DIM_KEYS = (
    "task",
    "channel",
    "module",
    "capability",
    "api",
    "function",
    "table",
    "field",
)

# Allowed top-level dict keys for parse_task_payload (aliases + task compose helpers).
# Unknown keys are rejected (read-only gate) — not silently ignored.
ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "task",
        "task_id",
        "task_label",
        "title",
        "name",
        "id",
        "description",
        "channel",
        "channel_code",
        "module",
        "module_code",
        "module_name",
        "capability",
        "cap_id",
        "cap",
        "api",
        "endpoint",
        "http_api",
        "function",
        "function_name",
        "fn",
        "table",
        "table_name",
        "field",
        "fields",
        "field_list",
    }
)

# Capability -> module, as a STATIC SNAPSHOT.
#
# REMOVED 2026-09-21. This dict was the defect it looks like it prevents:
#   - it hard-coded the retired `CP-S-*` numbering (three files held three copies
#     of one capability set, and this was the copy nothing read)
#   - it was NEVER CALLED. A repo-wide search found exactly ONE occurrence: its
#     own definition. A reader would reasonably believe the validator enforced
#     it, while `validate_new_task` actually asked the DB via
#     `get_active_capability(...)` and compared `module_key` itself.
#
# It is NOT replaced by a corrected dict, because a corrected dict would drift
# again. It is replaced by `ontology_drift()` below, which `validate_new_task`
# CALLS, so code and registry cannot disagree in silence.

# The expected capability suffixes for the Mouse Spot module, in scope order.
# Declared here (rather than imported from the UI module) so the drift gate has
# no Flask-adjacent dependency and can run inside the validator.
EXPECTED_CAPABILITY_SUFFIXES: tuple[str, ...] = (
    "core",                # index 0
    "prompt_load",
    "prompt_render",
    "mouse_spot_verify",
    "prompt_regression",
    "prompt_promote",
    "skills_api",
    "prompt_ssot",
    "events",
    "task_format_validator",
)
EXPECTED_CAPABILITY_MODULE = "mouse_spot_helper"


def ontology_drift(
    conn: sqlite3.Connection,
    *,
    include_dead: bool = True,
) -> dict[str, Any]:
    """Compare the code's declared capability set against `capability_registry`.

    This is the ENFORCEMENT the removed snapshot only pretended to be. It is read
    only and it REFUSES rather than warns.

    Checks:
      C1  every expected capability exists in the registry and is active
      C2  every expected capability is bound to EXPECTED_CAPABILITY_MODULE
          (a set-membership test cannot make this one: `m.foo` could exist while
          registered under module `other`)
      C3  no legacy `CP-S-*` capability key is registered
      C4  every registry capability's key prefix names the module it is bound to
      C5  a never-called static snapshot is reported by name (dead code that
          looks like a check)
    """
    checks: list[dict[str, Any]] = []

    def add(cid: str, name: str, ok: bool, detail: str,
            items: list | None = None) -> None:
        checks.append({"id": cid, "name": name, "ok": bool(ok),
                       "detail": detail, "items": items or []})

    has_reg = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='capability_registry'").fetchone() is not None
    if not has_reg:
        add("C1", "capability_registry present", False,
            "capability_registry table is absent; drift cannot be checked")
        return {"ok": False, "checks": checks,
                "failed": [c["id"] for c in checks if not c["ok"]]}

    rows = [dict(r) for r in conn.execute(
        "SELECT c.capability_key, c.is_active, m.module_key AS module_key "
        "FROM capability_registry c "
        "LEFT JOIN module_registry m ON m.module_id = c.module_id")]
    reg = {r["capability_key"]: r for r in rows}
    expected = ["%s.%s" % (EXPECTED_CAPABILITY_MODULE, s)
                for s in EXPECTED_CAPABILITY_SUFFIXES]
    scope = ("Task | Channel | Module | Capability | API | Function | Table | "
             "Field")

    missing = [k for k in expected if k not in reg]
    inactive = [k for k in expected
                if k in reg and not int(reg[k].get("is_active") or 0)]
    add("C1", "expected capabilities exist and are active",
        not missing and not inactive,
        "%d expected capability(ies); %d absent, %d inactive"
        % (len(expected), len(missing), len(inactive)),
        items=missing + inactive)

    wrong = [{"capability": k, "registry_module": reg[k].get("module_key"),
              "expected_module": EXPECTED_CAPABILITY_MODULE}
             for k in expected
             if k in reg and reg[k].get("module_key") != EXPECTED_CAPABILITY_MODULE]
    add("C2", "expected capabilities are bound to their module",
        not wrong,
        "%d checked; %d bound to a different module "
        "(membership alone would not catch this)" % (len(expected), len(wrong)),
        items=wrong)

    # C3: no legacy numeric capability key remains ACTIVE.
    #
    # WHY `is_active` IS PART OF THE CHECK (measured 2026-09-21)
    # --------------------------------------------------------
    # The check used to scan EVERY row, so a RETIRED row counted as drift. That
    # made the check unsatisfiable in the only safe way to fix it: the 9 legacy
    # rows (`CP-S-00`..`CP-S-08`) are exact DUPLICATES of their semantic
    # replacements -- same `name`, same `module_id`, zero references from
    # `api_registry` / `capability_binding` / `function_registry` / `audit_trace`
    # / `task_lifecycle_log` / `task_prompt_trace` / `track_id_audit` /
    # `skill_registry.capability_tags`. Retiring them is correct; DELETING them
    # would destroy the trail that explains the rename.
    #
    # A retired row is not drift. Drift is a legacy key still in USE. So the
    # check now asks the question it was always meant to ask.
    legacy = sorted(k for k in reg
                    if re.match(r"^CP-S-\d+$", k or "")
                    and int(reg[k].get("is_active") or 0))
    retired = sorted(k for k in reg
                     if re.match(r"^CP-S-\d+$", k or "")
                     and not int(reg[k].get("is_active") or 0))
    add("C3", "no legacy numeric capability key remains active", not legacy,
        "%d legacy key(s) still ACTIVE%s"
        % (len(legacy),
           ("; %d retired (kept as history)" % len(retired)) if retired else ""),
        items=legacy)

    prefix_bad = []
    for k, info in sorted(reg.items()):
        if "." not in (k or ""):
            continue
        head = k.split(".", 1)[0]
        mod = info.get("module_key")
        if not mod:
            prefix_bad.append({"capability": k, "key_prefix": head,
                               "registry_module": None,
                               "why": "no module binding, so the parent is "
                                      "unprovable"})
            continue
        # Only enforce the {module}.{capability} convention when there IS a
        # module of that name. `capability.ssot` is bound to module
        # `openclaw_companion`; no module is called `capability`, so reading its
        # prefix as a module claim would be reading it wrong. Requiring a module
        # to exist for every key would flag a correctly-bound row as drift.
        has_module = conn.execute(
            "SELECT 1 FROM module_registry WHERE module_key = ? "
            "AND is_active = 1", (head,)).fetchone() is not None
        if has_module and head != mod:
            prefix_bad.append({"capability": k, "key_prefix": head,
                               "registry_module": mod,
                               "why": "a module named %r exists, and this key "
                                      "claims it while being bound to %r"
                                      % (head, mod)})
    add("C4", "a capability's module claim matches its binding",
        not prefix_bad, "%d registry capability(ies) checked; %d unprovable or "
        "contradicted" % (len(reg), len(prefix_bad)), items=prefix_bad)

    dead = []
    if include_dead:
        for name in ("CAPABILITY_MODULE", "_module_exists", "_channel_exists"):
            if count_call_sites(name) == 0 and _name_defined(name):
                dead.append({"name": name, "call_sites": 0})
    add("C5", "no static snapshot is dead code", not dead,
        "%d snapshot(s) look like a check but are never called"
        % len(dead), items=dead)

    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": not failed,
        "checks": checks,
        "failed": [c["id"] for c in failed],
        "expected": expected,
        "registry_rows": len(reg),
        "format": scope,
    }


def _name_defined(name: str) -> bool:
    """Is `name` still defined in this file? (its own def/assignment counts)"""
    try:
        src = Path(__file__).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return bool(re.search(r"^(?:def\s+%s\b|%s\s*[:=])"
                          % (re.escape(name), re.escape(name)),
                          src, re.M))


def count_call_sites(name: str) -> int:
    """References to `name` outside its own definition line, repo-wide.

    A definition, a comment, and this module's own checks do not count. Used so a
    snapshot nobody executes cannot masquerade as a live check.
    """
    skip_dirs = {".git", ".venv", "__pycache__", "node_modules", "dist",
                 "out", "chrome_cdp_profile", ".vite", ".pytest_cache",
                 "site-packages"}
    here = Path(__file__).name
    hits = 0
    for p in sorted(Path(__file__).resolve().parents[2].rglob("*.py")):
        if any(part in skip_dirs for part in p.parts):
            continue
        name_ok = p.name
        if name_ok == here or name_ok.startswith("_proof_"):
            # inside this file, ignore the definition and this counter itself
            pass
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            if name not in line:
                continue
            s = line.strip()
            if s.startswith("#"):
                continue
            if re.match(r"^(?:def|class)\s+%s\b" % re.escape(name), s):
                continue
            if re.match(r"^%s\s*[:=]" % re.escape(name), s):
                continue
            if "count_call_sites" in s or "_name_defined" in s:
                continue
            hits += 1
    return hits


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _norm(v: Any) -> str:
    return str(v or "").strip()


def _is_na(v: str) -> bool:
    return _norm(v).lower() in ("n/a", "na", "none", "-", "—")


def unknown_payload_keys(raw: Any) -> list[str]:
    """Return sorted unknown top-level keys when payload is a dict."""
    if not isinstance(raw, dict):
        return []
    unknown = [str(k) for k in raw.keys() if str(k) not in ALLOWED_PAYLOAD_KEYS]
    return sorted(unknown)


def extract_task_id(raw: Any) -> str:
    """Read the task id straight from the payload.

    The payload already carries it (`task_id` / `id`), so there is no need to
    guess by splitting the composed `task` label. Splitting was wrong: a task
    label like "109 QC gate proof" happens to start with the id, but a label
    like "T-SKILL01 — Register ..." does not, and a description containing a
    space would silently yield the wrong key.

    Returns "" when the payload has no explicit id (then the QC gate is skipped
    rather than run against a guessed key).
    """
    if isinstance(raw, dict):
        for k in ("task_id", "id"):
            v = _norm(raw.get(k))
            if v:
                return v
    return ""


def parse_task_payload(raw: Any) -> dict[str, str]:
    """Parse dict or multi-line / pipe text into the 8 dimensions."""
    out = {k: "" for k in DIM_KEYS}
    if raw is None:
        return out
    if isinstance(raw, dict):
        aliases = {
            "task": ("task", "task_id", "task_label", "title", "name"),
            "channel": ("channel", "channel_code"),
            "module": ("module", "module_code", "module_name"),
            "capability": ("capability", "cap_id", "cap"),
            "api": ("api", "endpoint", "http_api"),
            "function": ("function", "function_name", "fn"),
            "table": ("table", "table_name"),
            "field": ("field", "fields", "field_list"),
        }
        for dim, keys in aliases.items():
            for k in keys:
                if k in raw and _norm(raw.get(k)):
                    out[dim] = _norm(raw.get(k))
                    break
        # Compose task from id + description when split
        if not out["task"]:
            tid = _norm(raw.get("task_id") or raw.get("id"))
            desc = _norm(raw.get("description") or raw.get("title") or raw.get("name"))
            out["task"] = " — ".join(p for p in (tid, desc) if p)
        return out

    text = str(raw).strip()
    if not text:
        return out

    # Pipe row: Task|Channel|Module|Capability|API|Function|Table|Field
    if "|" in text and text.count("|") >= 7 and "\n" not in text.strip().split("|", 1)[0]:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) >= 8:
            for i, k in enumerate(DIM_KEYS):
                out[k] = parts[i]
            return out

    # Labeled lines: Task: ... / Channel: ...
    label_re = re.compile(
        r"^(task|channel|module|capability|api|function|table|field)\s*[:：\-]\s*(.*)$",
        re.I,
    )
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = label_re.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
            continue
        # bare first line as task if still empty
        if not out["task"] and not line.lower().startswith(("error", "result")):
            out["task"] = line
    return out


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (table,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r[1]) for r in rows}


def _lookup_registry(
    conn: sqlite3.Connection,
    *,
    function_name: str,
    module_name: str | None = None,
) -> dict[str, Any] | None:
    try:
        from managed_coding import get_code_registry

        if module_name:
            hit = get_code_registry(
                conn, module_name=module_name, function_name=function_name
            )
            if hit:
                return hit
        # fallback: any module with this function_name
        row = conn.execute(
            """
            SELECT register_id, module_name, function_name, file_path, status
            FROM code_registry
            WHERE function_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (function_name,),
        ).fetchone()
        if row:
            return dict(row)
    except Exception:
        row = conn.execute(
            """
            SELECT register_id, module_name, function_name, file_path, status
            FROM code_registry
            WHERE function_name = ?
               OR (module_name = ? AND function_name = ?)
            ORDER BY id DESC LIMIT 1
            """,
            (function_name, module_name or "", function_name),
        ).fetchone()
        if row:
            return dict(row)
    return None


def _hardcode_blocking_candidates(*, root: Path, changed_files: list[str],
                                  db_path: Path | str | None) -> list[dict]:
    """The LIVE (non-comment, non-message, non-bootstrap) candidates in the files.

    Delegates the SCAN to `hardcode_scan.scan_file` — one implementation of "is
    this a hard-coded value", used by the report AND the gate. Re-implementing
    the rules here would let the gate and the report disagree about the same
    file, which is the failure mode a single scanner exists to prevent.
    """
    import hardcode_scan as _hsc

    conn_h = _hsc._connect(db_path)
    try:
        out: list[dict] = []
        for rel in changed_files:
            f = root / rel
            if not f.is_file() or f.suffix != ".py":
                continue
            for cand in _hsc.scan_file(f, root, conn=conn_h, resolve=True):
                if (cand.get("is_comment") or cand.get("is_message")
                        or cand.get("excluded")):
                    continue
                out.append(cand)
        return out
    finally:
        conn_h.close()


def _shadowed_param_candidates(*, root: Path,
                               changed_files: list[str]) -> tuple[list[dict], list[dict]]:
    """Parameters REBOUND in their own function body, as BLOCKING candidates.

    WHY THIS IS A SECOND CANDIDATE KIND (the user, 2026-09-23):

        "output value in function can be hardcode easy by re-define $A = 0"
        "does we have checking to block such PK design"

    MEASURED: `hardcode_scan` is LINE-based and `A = 0` matches none of its seven
    rules, so the pattern was INVISIBLE. It is also the WORSE case: a literal is a
    value that could have been looked up, while a shadowed parameter makes the
    output TRUE BY CONSTRUCTION — no input can make it fail, so no test catches
    it, and the opposite proof is destroyed at the source.

    IT DELEGATES to `code_shape.redefined_params` — the ONE implementation of
    "is a parameter overwritten", exactly as the literal check delegates to
    `hardcode_scan`. Writing the rule here would be the second-truth defect this
    module's sibling function already records.

    Returns `(blocking, read_failures)`. A file that could NOT be read is
    returned SEPARATELY and never as an empty blocking list: "could not read" is
    not "clean", and collapsing them is how a broken detector reads as a pass
    (`independent_review`: an empty result needs a positive control).
    """
    import code_shape as _csh

    blocking: list[dict] = []
    failed: list[dict] = []
    for rel in changed_files:
        f = root / rel
        if not f.is_file() or f.suffix != ".py":
            continue
        r = _csh.redefined_params(f)
        if not r.get("ok"):
            failed.append({"file": rel, "code": r.get("code"),
                           "reason": r.get("reason")})
            continue
        for hit in r["findings"]:
            blocking.append({
                "rule": "PARAM_SHADOWED",
                "cite_ref": hit["cite_ref"],
                "text": ("%s(%s) rebinds the parameter `%s` at line %s"
                         % (hit["function"], "…", hit["param"], hit["line"])),
                "function": hit["function"],
                "param": hit["param"],
                "line": hit["line"],
                # A shadowed parameter CANNOT be resolved from a registry, so the
                # PRECISE/AGGREGATE grading does not apply: it is always a defect
                # of the shape. Marked so the reason text does not claim a scope
                # level it never measured.
                "scope_level": "function",
            })
    return blocking, failed


def validate_new_task(
    payload: Any = None,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Validate 8-dim task payload against ontology *_registry tables (read-only).

    Order: Task → Channel → Module → Capability → Function → API → Table → Field.
    Does NOT write SSOT / registry / code_registry.
    """
    dims = parse_task_payload(payload)
    errors: list[str] = []
    recs: list[str] = []

    # Reject unknown top-level keys (dict payloads only) — read-only, no writes
    unknown_keys = unknown_payload_keys(payload)
    if unknown_keys:
        errors.append(
            "Payload: unknown top-level field(s): " + ", ".join(unknown_keys)
        )
        recs.append(
            "Remove unknown keys; only 8-dim aliases are allowed "
            f"(e.g. {', '.join(DIM_KEYS)})"
        )

    own = False
    if conn is None:
        path = Path(db_path or DEFAULT_DB)
        if not path.is_file():
            return {
                "ok": False,
                "result": "FAIL",
                "result_yes_no": "NO",
                "errors": (["agent.db missing — cannot lookup SSOT"] + errors),
                "recommendations": ["Run create_db.py --migrate then seed ontology registry"],
                "dimensions": dims,
                "skill_key": SKILL_KEY,
                "version": SKILL_PRODUCT_VERSION,
                "dispatch_allowed": False,
            }
        conn = _connect(path)
        own = True

    try:
        try:
            from db_schema import ensure_task_center_schema

            ensure_task_center_schema(conn)
        except Exception:
            pass

        from src.task_center.ontology_store import (
            api_belongs_to_capability,
            fields_belong_to_table,
            function_belongs_to_capability,
            get_active_capability,
            get_active_channel,
            get_active_db_table,
            get_active_module,
            registry_tables_exist,
        )

        if not registry_tables_exist(conn):
            errors.append(
                "Ontology: registry tables missing — run create_db.py --migrate"
            )
            recs.append("Migrate ontology registry then re-validate")
            passed = False
            result = "FAIL"
            yes_no = "NO"
            return {
                "ok": passed,
                "result": result,
                "result_yes_no": yes_no,
                "errors": errors,
                "recommendations": recs,
                "dimensions": dims,
                "skill_key": SKILL_KEY,
                "version": SKILL_PRODUCT_VERSION,
                "dispatch_allowed": False,
                "format": "Task | Channel | Module | Capability | API | Function | Table | Field",
                "report": _format_report(result, yes_no, errors, recs),
            }

        # 0. Taxonomy drift gate.
        # The removed `CAPABILITY_MODULE` snapshot could disagree with
        # `capability_registry` and nothing would say so. This call is what makes
        # the check REAL: it runs on every validation, so the code's declared
        # capability set cannot drift from the registry in silence.
        # Read-only; reported, not written.
        drift = ontology_drift(conn)
        if not drift["ok"]:
            for c in drift["checks"]:
                if not c["ok"]:
                    errors.append("Taxonomy drift [%s] %s: %s"
                                  % (c["id"], c["name"], c["detail"]))
            recs.append("Re-run migrate_legacy_ids.py --apply, or align the "
                        "declared capability set with capability_registry")

        # 1. Task
        task = dims["task"]
        if not task:
            errors.append("Task: empty — need unique task id + description")
            recs.append("Set Task like 'T-SKILL01 — Register Task Format Validator Skill'")
        elif len(task) < 3:
            errors.append("Task: too short")
            recs.append("Provide a stable task id and short title")

        # 2. Channel — channel_registry active
        channel = dims["channel"]
        ch_row = None
        if not channel:
            errors.append("Channel: empty — required")
            recs.append(f"Use existing channel_key (e.g. {CHANNEL_CODE})")
        else:
            ch_row = get_active_channel(channel, conn)
            if not ch_row:
                errors.append(
                    f"Channel: unknown or inactive '{channel}' (channel_registry)"
                )
                recs.append(
                    f"Register active channel in channel_registry (seed uses {CHANNEL_CODE})"
                )

        # 3. Module — module_registry active; channel match when both set
        module = dims["module"]
        mod_row = None
        if not module:
            errors.append("Module: empty — required")
            recs.append(f"Use existing module_key (e.g. {MODULE_CODE})")
        else:
            mod_row = get_active_module(module, conn)
            if not mod_row:
                errors.append(
                    f"Module: unknown or inactive '{module}' (module_registry)"
                )
                recs.append("Register active module bound to an active channel")
            elif ch_row and _norm(mod_row.get("channel_key")) != _norm(channel):
                errors.append(
                    f"Module: '{module}' bound to channel "
                    f"'{mod_row.get('channel_key')}', not '{channel}'"
                )
                recs.append("Align Channel with module_registry.channel_id")

        # 4. Capability — active + correct module
        capability = dims["capability"]
        cap_row = None
        if not capability:
            errors.append("Capability: empty — required")
            recs.append(f"Set Capability belonging to Module (e.g. {CAPABILITY_ID})")
        else:
            cap_row = get_active_capability(capability, conn)
            if not cap_row:
                errors.append(
                    f"Capability: unknown or inactive '{capability}' "
                    "(not in capability_registry allowlist)"
                )
                recs.append(
                    "Pick an active capability_key from capability_registry "
                    f"(e.g. {CAPABILITY_ID})"
                )
            elif module and _norm(cap_row.get("module_key")) != _norm(module):
                errors.append(
                    f"Capability: '{capability}' belongs to module "
                    f"'{cap_row.get('module_key')}', not '{module}'"
                )
                recs.append("Align Module with capability_registry.module_id")
            elif not mod_row and module:
                # module already errored; still note capability needs active module
                pass

        # 5. Function — must belong to capability (function_registry)
        function = dims["function"]
        fn_row = None
        if not function:
            errors.append("Function: empty — required")
            recs.append(
                f"Register function under capability in function_registry "
                f"(e.g. {VALIDATOR_FUNCTION})"
            )
        elif not capability:
            errors.append("Function: cannot validate without capability")
        else:
            fn_row = function_belongs_to_capability(function, capability, conn)
            if not fn_row:
                errors.append(
                    f"Function: '{function}' not registered under capability "
                    f"'{capability}' (function_registry)"
                )
                recs.append(
                    "Human-approve function_registry row bound to active capability"
                )
            else:
                # Optional secondary: code_registry file_path only if linked
                cr_id = _norm(fn_row.get("code_registry_id"))
                fp = _norm(fn_row.get("file_path"))
                if cr_id:
                    reg = _lookup_registry(
                        conn, function_name=function, module_name=module or None
                    )
                    if not reg:
                        errors.append(
                            f"Function: code_registry_id '{cr_id}' set but "
                            f"'{function}' not found in code_registry"
                        )
                        recs.append("Enroll via managed_coding after human approval")
                    else:
                        rfp = _norm(reg.get("file_path")) or fp
                        if rfp:
                            p = Path(rfp)
                            if not p.is_file():
                                p2 = BASE_DIR / rfp.replace("\\", "/")
                                if not p2.is_file():
                                    errors.append(
                                        f"Function: file_path '{rfp}' does not exist on disk"
                                    )
                                    recs.append(
                                        "Point file_path at the real implementation file"
                                    )
                elif fp:
                    p = Path(fp)
                    if not p.is_file():
                        p2 = BASE_DIR / fp.replace("\\", "/")
                        if not p2.is_file():
                            errors.append(
                                f"Function: file_path '{fp}' does not exist on disk"
                            )
                            recs.append(
                                "Point function_registry.file_path at real source"
                            )

        # 6. API — must belong to capability (api_registry)
        api = dims["api"]
        if not api:
            errors.append("API: blank not allowed — use 'none' if no HTTP API")
            recs.append("Set API to a registered api_key or literal 'none'")
        elif not capability:
            errors.append("API: cannot validate without capability")
        else:
            api_row = api_belongs_to_capability(api, capability, conn)
            if not api_row:
                errors.append(
                    f"API: '{api}' not registered under capability "
                    f"'{capability}' (api_registry)"
                )
                recs.append(
                    "Human-approve api_registry row bound to active capability"
                )

        # 7–8. Table + Field — db_table_registry / db_field_registry
        table = dims["table"]
        field = dims["field"]
        if not table:
            errors.append("Table: blank not allowed — use 'n/a' if no table")
            recs.append("Set Table to a registered table_key or 'n/a'")
        elif _is_na(table):
            if not field:
                errors.append("Field: blank not allowed — use 'n/a' when Table is n/a")
                recs.append("Set Field to 'n/a'")
            elif not _is_na(field):
                errors.append("Field: must be 'n/a' when Table is 'n/a'")
                recs.append("Set Field to 'n/a' to match Table")
        else:
            tbl_row = get_active_db_table(table, conn)
            if not tbl_row:
                errors.append(
                    f"Table: unknown or inactive '{table}' (db_table_registry)"
                )
                recs.append("Register active table_key in db_table_registry")
            else:
                if not field:
                    errors.append(
                        "Field: blank not allowed when Table is set — use 'n/a' only if no fields"
                    )
                    recs.append(
                        "List comma-separated field_keys registered on the table"
                    )
                elif _is_na(field):
                    pass
                else:
                    wanted = [
                        f.strip()
                        for f in re.split(r"[,;]+", field)
                        if f.strip()
                    ]
                    # also split on whitespace-only lists carefully: prefer comma
                    if len(wanted) == 1 and " " in wanted[0] and "," not in field:
                        wanted = [f.strip() for f in field.split() if f.strip()]
                    ok_f, missing = fields_belong_to_table(table, wanted, conn)
                    if not ok_f:
                        errors.append(
                            f"Field: unknown on '{table}' (db_field_registry): "
                            f"{', '.join(missing) if missing else '(empty)'}"
                        )
                        recs.append(
                            "Register field_keys under db_field_registry for this table"
                        )

        passed = len(errors) == 0
        result = "PASS" if passed else "FAIL"
        yes_no = "YES" if passed else "NO"

        # ---- QC gate (read-only) --------------------------------------
        # Policy: UNKNOWN = FAIL. A task with any blocking QC verdict
        # (FAIL or UNKNOWN) must not be dispatched, even when the 8-dim
        # ontology check passes. We trust EVIDENCE, not self-reports.
        #
        # The task id comes straight from the payload (`task_id` / `id`), not
        # from splitting the composed `task` label — the label is free text and
        # splitting it would silently key the gate on the wrong value.
        qc_blocked = False
        qc_blocking: list[dict[str, Any]] = []
        qc_task_id = extract_task_id(payload)
        if passed and qc_task_id:
            try:
                import qc_contract as _qc

                if _qc.has_failed(qc_task_id, db_path=db_path):
                    qc_blocked = True
                    qc_blocking = _qc.blocking_runs(qc_task_id, db_path=db_path)
            except Exception:
                # QC layer unavailable must NOT silently allow dispatch.
                qc_blocked = True
                qc_blocking = [{
                    "verdict": "UNKNOWN",
                    "tool": "qc_contract",
                    "target": "qc_layer",
                    "reason": "qc_contract unavailable — cannot prove PASS",
                }]
        if qc_blocked:
            marks = ", ".join(
                f"{r.get('verdict')}:{r.get('tool')}/{r.get('target')}"
                for r in qc_blocking
            )
            errors.append(
                f"QC: blocking verdict(s) for task '{qc_task_id}' — {marks} "
                "(policy: UNKNOWN = FAIL)"
            )
            recs.append(
                "Resolve blocking QC runs (re-run with evidence) before dispatch"
            )
            passed = False
            result = "FAIL"

        # ---- HARDCODE gate (read-only) --------------------------------
        # THE USER'S REQUIREMENT (2026-09-23):
        #     "and hardcode is not allowed, hot can you fucking to have that!!!"
        #     "qc will fail for hardcode.... fix it too"
        #
        # MEASURED WHY THIS IS HERE: a model name was hard-coded in 8 files and
        # 11 places (`vision_analyze.py:45`, `f_model_vision.py:117,170,369`, ...)
        # and the hardcode scanner could not see a single one — its rules
        # covered screen sizes, pixels, paths, URLs and foreign keys, but not a
        # model. The missing rule is the root cause; this gate is what makes the
        # rule matter at dispatch time instead of in a report nobody reads.
        #
        # GRADING (the reason it is not a blanket FAIL):
        #   PRECISE level (db_field / db_table / function / api) -> FAIL.
        #     These levels can HOLD a value, so the question "was this value
        #     derivable?" is answerable.
        #   AGGREGATE-only (module / capability / channel) -> UNKNOWN.
        #     These levels name a FORUM, not a value source, so they cannot
        #     decide necessity. MEASURED: of 274 live candidates only a handful
        #     resolve precisely — failing on all of them would block every task.
        #   An OVER-INCLUSIVE rule is a REVIEW list, so a candidate is not
        #     automatically a defect; it is a thing a human must look at.
        #
        # NO `files_changed` -> SKIPPED AND SAID. Never a silent pass: the same
        # rule the QC gate already follows (an explicit task id is required, the
        # composed label is never split to guess one).
        hardcode_status = "SKIPPED_NO_FILES_CHANGED"
        hardcode_blocking: list[dict[str, Any]] = []
        shadow_blocking: list[dict[str, Any]] = []
        shadow_unreadable: list[dict[str, Any]] = []
        changed_files = _changed_files_from_payload(payload)
        # Read the scanner's PRECISE levels AT CALL TIME, so this gate and the
        # report cannot disagree about what "answerable" means.
        precise_levels = _hardcode_precise_levels()
        if passed and changed_files:
            try:
                root = (Path(db_path).resolve().parent if db_path
                        else Path.cwd())
                hardcode_blocking = _hardcode_blocking_candidates(
                    root=root, changed_files=changed_files, db_path=db_path)
                hardcode_status = ("BLOCKED" if hardcode_blocking else "CLEAN")
            except Exception as exc:
                # An unavailable scanner must NOT read as "no hardcode found".
                hardcode_status = "UNKNOWN_SCANNER_UNAVAILABLE"
                hardcode_blocking = [{
                    "rule": "hardcode_scan",
                    "cite_ref": "",
                    "text": "%s: %s" % (type(exc).__name__, exc),
                }]
            # ---- THE PARAMETER-SHADOW GATE (added 2026-09-23) --------------
            # The user: "output value in function can be hardcode easy by
            # re-define $A = 0". The literal scanner cannot see it, so it is a
            # SEPARATE candidate kind — and a file that could not be READ is
            # carried separately, because "could not read" is not "clean".
            try:
                shadow_blocking, shadow_unreadable = _shadowed_param_candidates(
                    root=root, changed_files=changed_files)
                shadow_status = ("BLOCKED" if shadow_blocking
                                 else "CLEAN" if not shadow_unreadable
                                 else "UNKNOWN_FILE_UNREADABLE")
            except Exception as exc:
                shadow_status = "UNKNOWN_SCANNER_UNAVAILABLE"
                shadow_unreadable = [{
                    "file": "<all>", "code": "IMPORT_ERROR",
                    "reason": "%s: %s" % (type(exc).__name__, exc)}]
        else:
            shadow_status = "SKIPPED_NO_FILES_CHANGED"
        if shadow_blocking:
            marks = ", ".join(
                "%s(%s)" % (c.get("rule"), c.get("cite_ref"))
                for c in shadow_blocking[:6])
            errors.append(
                "PARAM_SHADOWED: %d parameter(s) are REBOUND in their own "
                "function body — %s. A rebound parameter makes the output TRUE "
                "BY CONSTRUCTION: no input can make it fail, so no test can "
                "catch it and the opposite proof is destroyed at the source. "
                "(rule from `code_shape.redefined_params`; every candidate cites "
                "path:line)" % (len(shadow_blocking), marks)
            )
            recs.append(
                "Read the argument instead of overwriting it, or name the "
                "constant explicitly and declare why it is not an input"
            )
            passed = False
            result = "FAIL"
            yes_no = "NO"
        if shadow_unreadable:
            # AN UNREAD FILE IS NOT A PASS. Reported as UNKNOWN with the reason,
            # never folded into CLEAN — the same rule the literal scanner follows
            # for an unavailable scanner.
            marks = ", ".join("%s[%s]" % (u.get("file"), u.get("code"))
                              for u in shadow_unreadable[:4])
            errors.append(
                "PARAM_SHADOWED: %d changed file(s) could NOT be read, so their "
                "shape is UNKNOWN, not clean — %s" % (len(shadow_unreadable),
                                                      marks)
            )
            recs.append("Make the changed file readable, then re-run the gate")
            passed = False
            result = "FAIL"
            yes_no = "NO"
        if hardcode_blocking:
            marks = ", ".join(
                "%s(%s)%s" % (c.get("rule"), c.get("cite_ref"),
                              "" if c.get("scope_level") in precise_levels
                              else "=aggregate-only")
                for c in hardcode_blocking[:6])
            errors.append(
                "HARDCODE: %d live candidate(s) in the changed files — %s. "
                "A literal that could have been resolved from a registry is not "
                "allowed (rule from the hardcode scanner; every candidate cites "
                "path:line)." % (len(hardcode_blocking), marks)
            )
            recs.append(
                "Resolve each candidate from its source (a service route, a "
                "registry column, a config row) or cite why it cannot be derived"
            )
            passed = False
            result = "FAIL"
            yes_no = "NO"

        return {
            "ok": passed,
            "result": result,
            "result_yes_no": yes_no,
            "errors": errors,
            "recommendations": recs,
            "dimensions": dims,
            "skill_key": SKILL_KEY,
            "version": SKILL_PRODUCT_VERSION,
            "dispatch_allowed": passed,
            "qc_blocked": qc_blocked,
            "qc_blocking": qc_blocking,
            "hardcode_status": hardcode_status,
            "hardcode_blocking": hardcode_blocking,
            "hardcode_precise_levels": list(precise_levels),
            "param_shadow_status": shadow_status,
            "param_shadow_blocking": shadow_blocking,
            "param_shadow_unreadable": shadow_unreadable,
            "format": "Task | Channel | Module | Capability | API | Function | Table | Field",
            "report": _format_report(result, yes_no, errors, recs),
        }
    finally:
        if own and conn is not None:
            conn.close()


def _format_report(
    result: str,
    yes_no: str,
    errors: list[str],
    recs: list[str],
) -> str:
    err_lines = "\n".join(f"- {e}" for e in errors) if errors else "- (none)"
    rec_lines = "\n".join(recs) if recs else "(none)"
    reason = (
        "All 8 dimensions valid against SSOT; dispatch allowed."
        if result == "PASS"
        else f"{len(errors)} validation error(s); block worker dispatch."
    )
    return (
        f"Result: {yes_no}\n"
        f"VALIDATION_RESULT: {result}\n"
        f"ERROR_LIST:\n{err_lines}\n"
        f"RECOMMENDATION:\n{rec_lines}\n"
        f"Reason: {reason}\n"
    )


# ---------------------------------------------------------------------------
# Seed: skill prompt SSOT + T-SKILL01 + code_registry
# ---------------------------------------------------------------------------

def _get_or_create_dim(conn: sqlite3.Connection, table: str, code: str, name: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(f"INSERT INTO {table} (code, name) VALUES (?, ?)", (code, name))
    return int(cur.lastrowid)


def _resolve_version_id(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    module_id: int,
    version_label: str = VERSION_LABEL,
) -> int:
    row = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, module_id, version_label),
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO version_center
            (channel_id, module_id, version_label, title, notes, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            channel_id,
            module_id,
            version_label,
            "Task Center skill gates",
            "Pre-dispatch taxonomy validator skill-1.0",
        ),
    )
    return int(cur.lastrowid)


def _ensure_action(conn: sqlite3.Connection) -> int:
    code = "function.define"
    row = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", (code,)
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO task_action_name
            (element, action, code, name, requires_tdd, status)
        VALUES ('function', 'define', ?, 'Define function', 0, 'active')
        """,
        (code,),
    )
    return int(cur.lastrowid)


def seed_skill_prompt_file_and_db(
    db_path: Path | str | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Write skills JSON if missing and upsert skill_prompt_ssot."""
    from skill_prompt import (
        file_skill_path,
        load_file_skill,
        upsert_skill_prompt,
        _connect as skill_connect,
        ensure_skill_tables,
    )

    path = Path(db_path or DEFAULT_DB)
    fpath = file_skill_path(SKILL_KEY, SKILL_VERSION)
    bundled = BASE_DIR / "skills" / SKILL_KEY / f"{SKILL_VERSION}.json"
    if not fpath.is_file() and bundled.is_file():
        fpath.parent.mkdir(parents=True, exist_ok=True)
        if fpath.resolve() != bundled.resolve():
            fpath.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
    data = load_file_skill(SKILL_KEY, SKILL_VERSION)
    if not data:
        return {"ok": False, "error": f"skill file missing: {fpath}"}

    conn = skill_connect(path)
    try:
        ensure_skill_tables(conn)
        row = upsert_skill_prompt(
            conn,
            skill_key=data["skill_key"],
            version_label=data.get("version_label") or SKILL_VERSION,
            prompt_text=data.get("prompt_text") or "",
            prompt_key=data.get("prompt_key") or "main",
            status="active",
            parser=data.get("parser") or "result_yes_no",
            output_schema=data.get("output_schema") or "result_yes_no",
            model_default=data.get("model_default") or "qwen2.5:7b-instruct",
            hard_rules=data.get("hard_rules") or [],
            source=data.get("source") or SOURCE,
            notes=data.get("notes"),
            activate=True,
            commit=commit,
        )
        return {
            "ok": True,
            "skill_key": SKILL_KEY,
            "version_label": row.get("version_label") if isinstance(row, dict) else SKILL_VERSION,
            "file": str(fpath),
            "seeded": row,
        }
    finally:
        conn.close()


def seed_task_format_validator(
    db_path: Path | str | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Idempotent: skill prompt + T-SKILL01 task + code_registry enrollment."""
    from db_schema import ensure_task_center_schema, upsert_task_ssot
    from managed_coding import register_managed_function

    path = Path(db_path or DEFAULT_DB)
    out: dict[str, Any] = {
        "ok": True,
        "skill_key": SKILL_KEY,
        "task_label": TASK_LABEL,
        "module": MODULE_CODE,
        "channel": CHANNEL_CODE,
    }

    skill = seed_skill_prompt_file_and_db(path, commit=commit)
    out["skill"] = skill
    if not skill.get("ok"):
        out["ok"] = False
        out["error"] = skill.get("error")
        return out

    if not path.is_file():
        out["ok"] = False
        out["error"] = "agent.db missing"
        return out

    conn = _connect(path)
    try:
        ensure_task_center_schema(conn)
        channel_id = _get_or_create_dim(conn, "channel", CHANNEL_CODE, CHANNEL_NAME)
        module_id = _get_or_create_dim(conn, "module", MODULE_CODE, MODULE_NAME)
        # also ensure mouse_spot_helper exists for capability map samples
        _get_or_create_dim(conn, "module", "mouse_spot_helper", "Mouse Spot Helper")
        version_id = _resolve_version_id(
            conn, channel_id=channel_id, module_id=module_id, version_label=VERSION_LABEL
        )
        action_id = _ensure_action(conn)

        payload = {
            "pipeline": "task_format_validator",
            "skill_key": SKILL_KEY,
            "skill_version": SKILL_PRODUCT_VERSION,
            "format": "Task | Channel | Module | Capability | API | Function | Table | Field",
            "channel": CHANNEL_CODE,
            "module": MODULE_CODE,
            "capability": CAPABILITY_ID,
            "api": VALIDATOR_API,
            "api_alias": VALIDATOR_API_ALIAS,
            "function": VALIDATOR_FUNCTION,
            "table": "code_registry",
            "field": "register_id, module_name, function_name, file_path",
            "gate": "validate_before_dispatch",
        }
        title = "T-SKILL01 — Register Task Format Validator Skill"
        existing = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, TASK_LABEL),
        ).fetchone()
        if existing:
            task_id = int(existing[0])
            conn.execute(
                """
                UPDATE dev_task
                SET title = ?, payload_json = ?, module_id = ?, channel_id = ?,
                    action_name_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    title,
                    json.dumps(payload, ensure_ascii=False),
                    module_id,
                    channel_id,
                    action_id,
                    task_id,
                ),
            )
            out["task_action"] = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO dev_task
                    (parent_task_id, channel_id, module_id, action_name_id, version_id,
                     task_label, title, payload_json, status)
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    channel_id,
                    module_id,
                    action_id,
                    version_id,
                    TASK_LABEL,
                    title,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            task_id = int(cur.lastrowid)
            out["task_action"] = "created"
        out["task_id"] = task_id
        out["version_id"] = version_id

        dims = [
            (10, "system.key", "task_format_validator"),
            (20, "skill.key", SKILL_KEY),
            (30, "skill.version", SKILL_PRODUCT_VERSION),
            (40, "task.label", TASK_LABEL),
            (50, "channel.code", CHANNEL_CODE),
            (60, "module.code", MODULE_CODE),
            (70, "capability.id", CAPABILITY_ID),
            (80, "api.endpoint", VALIDATOR_API),
            (90, "function.name", VALIDATOR_FUNCTION),
            (100, "table.name", "code_registry"),
            (110, "field.list", "register_id, module_name, function_name, file_path"),
            (120, "gate.policy", "validate_before_dispatch"),
            (130, "format.law", "Task|Channel|Module|Capability|API|Function|Table|Field"),
        ]
        dim_n = 0
        for sort_order, dim_key, value_text in dims:
            upsert_task_ssot(
                conn,
                task_id=task_id,
                dim_key=dim_key,
                value_text=value_text,
                value_type="string",
                source=SOURCE,
                sort_order=sort_order,
                notes="task format validator",
                commit=False,
            )
            dim_n += 1
        out["dims"] = dim_n

        # Resolve line span for validate_new_task
        src_path = BASE_DIR / VALIDATOR_FILE.replace("/", "\\") if "\\" in str(BASE_DIR) else BASE_DIR / VALIDATOR_FILE
        src_path = BASE_DIR / Path(VALIDATOR_FILE)
        line_start = line_end = None
        if src_path.is_file():
            text = src_path.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if line.startswith("def validate_new_task"):
                    line_start = i
                if line_start and line.startswith("def ") and i > line_start:
                    line_end = i - 1
                    break
            if line_start and not line_end:
                line_end = line_start + 5

        reg = register_managed_function(
            conn,
            task_id=task_id,
            tacid=TASK_LABEL,
            module_name=MODULE_CODE,
            function_name=VALIDATOR_FUNCTION,
            system_key="task_format_validator",
            slice_key="validate_new_task",
            status="active",
            source=SOURCE,
            notes="Pre-dispatch 8-dim taxonomy validator (skill-1.0)",
            file_path=VALIDATOR_FILE,
            line_start=line_start,
            line_end=line_end,
            code_span="validate_new_task",
            commit=False,
        )
        out["register"] = {
            "register_id": reg.get("register_id"),
            "module_name": reg.get("module_name"),
            "function_name": reg.get("function_name"),
            "file_path": reg.get("file_path") or VALIDATOR_FILE,
            "action": reg.get("action") or reg.get("reg_action"),
        }

        if commit:
            conn.commit()

        # Self-check: T-SKILL01 payload should PASS after seed
        sample = {
            "task": title,
            "channel": CHANNEL_CODE,
            "module": MODULE_CODE,
            "capability": CAPABILITY_ID,
            "api": VALIDATOR_API,
            "function": VALIDATOR_FUNCTION,
            "table": "code_registry",
            "field": "register_id, module_name, function_name, file_path",
        }
        check = validate_new_task(sample, conn=conn)
        out["self_check"] = {
            "result": check.get("result"),
            "ok": check.get("ok"),
            "errors": check.get("errors"),
        }
        if not check.get("ok"):
            out["ok"] = False
            out["self_check_warn"] = check.get("errors")
        return out
    except Exception as e:
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        conn.close()


__all__ = [
    "SKILL_KEY",
    "VALIDATOR_FUNCTION",
    "VALIDATOR_MODULE",
    "parse_task_payload",
    "validate_new_task",
    "seed_task_format_validator",
    "seed_skill_prompt_file_and_db",
]


if __name__ == "__main__":
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Task format taxonomy validator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="Seed skill prompt + T-SKILL01 + code_registry")
    v = sub.add_parser("validate", help="Validate a payload JSON file or stdin JSON")
    v.add_argument("payload", nargs="?", help="JSON file path or inline JSON")
    args = p.parse_args()

    if args.cmd == "seed":
        print(json.dumps(seed_task_format_validator(), indent=2, ensure_ascii=False))
    elif args.cmd == "validate":
        raw: Any
        if not args.payload:
            raw = sys.stdin.read()
        else:
            pp = Path(args.payload)
            if pp.is_file():
                raw = json.loads(pp.read_text(encoding="utf-8"))
            else:
                raw = json.loads(args.payload)
        print(json.dumps(validate_new_task(raw), indent=2, ensure_ascii=False))
