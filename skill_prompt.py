"""Skill Prompt SSOT — load/render/test/promote versioned LLM prompts.

Usage:
  python skill_prompt.py seed
  python skill_prompt.py list --skill mouse_spot_verify
  python skill_prompt.py show --skill mouse_spot_verify
  python skill_prompt.py set-active --skill mouse_spot_verify --version v1_strict
  python skill_prompt.py test --skill mouse_spot_verify --expected NO --runs 10 --target "Visual Studio Code"
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
SKILLS_DIR = BASE_DIR / "skills"
DEFAULT_DB = BASE_DIR / "agent.db"
DEFAULT_SCREENSHOT = BASE_DIR / "mouse_spot_screenshot.png"

# The write-site standardiser. `error_reason` is a DETAIL OF A CONDITION recorded
# in the same row (`status`), so its empty state is `NA`, never NULL — and the
# DDL already says `NOT NULL DEFAULT 'NA'`. Measured defect 2026-09-22: this
# module passed `error_reason=None` EXPLICITLY, which overrides the DDL default,
# so 685 backfilled NULLs came back as 1578.
import no_null as _nn  # noqa: E402
DEFAULT_SKILL = "mouse_spot_verify"
DEFAULT_VERSION = "v1_strict"

MOUSE_SPOT_V1_PROMPT = (
    "You are a visual inspector for Mouse Spot Helper. Strict rules must be followed:\n"
    "1. Red crosshair (+) = captured mouse point.\n"
    "2. Target = {{target_name}}.\n"
    "3. PASS condition ONLY: The red crosshair must lie within the physical pixel boundary of the target icon itself.\n"
    "4. Hard rule: Being close, pointing toward, or inside the large blue preview circle DOES NOT count as PASS.\n"
    "5. If crosshair lands on any other icon (even adjacent), return FAIL. Proximity is never accepted.\n"
    "6. Ignore the blue circle entirely; it is only a UI hint for human user, NOT a detection boundary.\n"
    "7. Intended action (context only, not a pass condition): {{target_action}}\n"
    "8. Output fixed format exactly:\n"
    "Result: [YES / NO]\n"
    "Reason: 1 short sentence, state which icon the crosshair is on.\n"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_skill_tables(conn: sqlite3.Connection) -> None:
    """Create skill prompt SSOT tables if missing (safe to call repeatedly)."""
    try:
        from db_schema import (
            SKILL_LESSON_DDL,
            SKILL_MISMATCH_LOG_DDL,
            SKILL_PROMPT_CASE_DDL,
            SKILL_PROMPT_INFERENCE_DDL,
            SKILL_PROMPT_SSOT_DDL,
            SKILL_PROMPT_TEST_RUN_DDL,
            SKILL_TASK_QUEUE_DDL,
        )

        for ddl in (
            SKILL_PROMPT_SSOT_DDL,
            SKILL_PROMPT_CASE_DDL,
            SKILL_PROMPT_TEST_RUN_DDL,
            SKILL_PROMPT_INFERENCE_DDL,
            SKILL_LESSON_DDL,
            SKILL_MISMATCH_LOG_DDL,
            SKILL_TASK_QUEUE_DDL,
        ):
            conn.executescript(ddl)
        # Idempotent additive migrations for existing tables (CREATE IF NOT
        # EXISTS won't add columns to an already-created table).
        _ensure_column(conn, "skill_task_queue", "handoff_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "skill_mismatch_log", "error_reason", "TEXT")
    except ImportError:
        # Minimal fallback if db_schema not yet updated in odd import paths.
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skill_prompt_ssot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_key TEXT NOT NULL,
                prompt_key TEXT NOT NULL DEFAULT 'main',
                version_label TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                hard_rules_json TEXT NOT NULL DEFAULT '[]',
                output_schema TEXT NOT NULL DEFAULT 'result_yes_no',
                parser TEXT NOT NULL DEFAULT 'result_yes_no',
                model_default TEXT,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','testing','active','deprecated')),
                task_id INTEGER,
                parent_id INTEGER,
                sort_order INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'skill_prompt',
                notes TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (skill_key, prompt_key, version_label)
            );
            """
        )


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> None:
    """Add a column to an existing table if it is missing (idempotent)."""
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.commit()
    except Exception:
        pass


def file_skill_path(skill_key: str, version_label: str) -> Path:
    """Canonical (flat) write location: skills/<skill_key>/<version>.json."""
    return SKILLS_DIR / skill_key / f"{version_label}.json"


def resolve_skill_json(
    skill_key: str,
    version_label: str | None = None,
) -> Path | None:
    """Find an existing skill JSON twin anywhere under skills/.

    Skill packages live either flat (skills/<key>/<ver>.json) or under a
    category folder (skills/<catalog>/<key>/<ver>.json). Scan both so a
    package placed in a category is not silently treated as missing.

    Order:
      1. exact flat path
      2. exact nested path anywhere (skills/**/<key>/<ver>.json)
      3. any nested json for that key (skills/**/<key>/<other>.json)
      4. any flat json for that key
    Returns None when nothing matches.
    """
    key = (skill_key or "").strip()
    if not key:
        return None
    ver = (version_label or "").strip()

    flat = file_skill_path(key, ver or "v1_strict")
    if ver and flat.is_file():
        return flat

    nested = sorted(SKILLS_DIR.rglob(f"{key}/{ver}.json")) if ver else []
    if nested:
        return nested[0]

    nested_any = sorted(SKILLS_DIR.rglob(f"{key}/*.json"))
    if nested_any:
        if ver:
            exact = [p for p in nested_any if p.stem == ver]
            if exact:
                return exact[0]
        return nested_any[0]

    flat_any = sorted((SKILLS_DIR / key).glob("*.json")) if (SKILLS_DIR / key).is_dir() else []
    if flat_any:
        if ver:
            exact = [p for p in flat_any if p.stem == ver]
            if exact:
                return exact[0]
        return flat_any[0]
    return None


def discover_skill_json_files() -> list[Path]:
    """All skill JSON twins under skills/ (flat + category), version-sorted.

    Excludes non-skill json such as 'v1_draft.json' owned by other catalogs
    only in the sense that callers filter by ``skill_key`` field; here we
    simply return every *.json that parses to an object with a skill_key.
    """
    found: list[Path] = []
    if not SKILLS_DIR.is_dir():
        return found
    for p in sorted(SKILLS_DIR.rglob("*.json")):
        if "_tmp" in p.name:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and str(data.get("skill_key") or "").strip():
            found.append(p)
    return found


def load_file_skill(
    skill_key: str = DEFAULT_SKILL,
    version_label: str = DEFAULT_VERSION,
) -> dict[str, Any] | None:
    path = resolve_skill_json(skill_key, version_label)
    if path is None:
        # last resort: flat path even if missing (caller may overwrite)
        path = file_skill_path(skill_key, version_label)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("skill_key", skill_key)
    data.setdefault("prompt_key", "main")
    data.setdefault("version_label", path.stem)
    data.setdefault("parser", "result_yes_no")
    data.setdefault("status", "active")
    data["_source_path"] = str(path)
    return data


def builtin_mouse_spot_skill() -> dict[str, Any]:
    return {
        "skill_key": DEFAULT_SKILL,
        "prompt_key": "main",
        "version_label": DEFAULT_VERSION,
        "status": "active",
        "parser": "result_yes_no",
        "output_schema": "result_yes_no",
        "model_default": "qwen2.5vl:7b",
        "source": "builtin",
        "notes": "Builtin fallback",
        "hard_rules": [],
        "prompt_text": MOUSE_SPOT_V1_PROMPT,
    }


def row_to_skill(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else dict(row)
    rules = d.get("hard_rules_json") or d.get("hard_rules") or "[]"
    if isinstance(rules, str):
        try:
            d["hard_rules"] = json.loads(rules)
        except json.JSONDecodeError:
            d["hard_rules"] = []
    d.setdefault("prompt_key", "main")
    return d


def list_skills(
    conn: sqlite3.Connection | None = None,
    *,
    skill_key: str | None = None,
) -> list[dict[str, Any]]:
    own = False
    if conn is None:
        if not DEFAULT_DB.is_file():
            f = load_file_skill(skill_key or DEFAULT_SKILL)
            return [f] if f else [builtin_mouse_spot_skill()]
        conn = _connect()
        own = True
        ensure_skill_tables(conn)
    try:
        if skill_key:
            rows = conn.execute(
                """
                SELECT * FROM skill_prompt_ssot
                WHERE skill_key = ?
                ORDER BY
                  CASE status WHEN 'active' THEN 0 WHEN 'testing' THEN 1
                       WHEN 'draft' THEN 2 ELSE 3 END,
                  id DESC
                """,
                (skill_key,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM skill_prompt_ssot
                ORDER BY skill_key,
                  CASE status WHEN 'active' THEN 0 ELSE 1 END,
                  id DESC
                """
            ).fetchall()
        out = [row_to_skill(r) for r in rows]
        if not out and skill_key:
            f = load_file_skill(skill_key)
            if f:
                out = [f]
        if not out and (not skill_key or skill_key == DEFAULT_SKILL):
            out = [builtin_mouse_spot_skill()]
        return out
    finally:
        if own:
            conn.close()


def get_active_skill(
    skill_key: str = DEFAULT_SKILL,
    *,
    prompt_key: str = "main",
    conn: sqlite3.Connection | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    own = False
    if conn is None:
        path = Path(db_path or DEFAULT_DB)
        if path.is_file():
            conn = _connect(path)
            own = True
            ensure_skill_tables(conn)
        else:
            return load_file_skill(skill_key) or builtin_mouse_spot_skill()
    try:
        row = conn.execute(
            """
            SELECT * FROM skill_prompt_ssot
            WHERE skill_key = ? AND prompt_key = ? AND status = 'active'
            ORDER BY id DESC LIMIT 1
            """,
            (skill_key, prompt_key),
        ).fetchone()
        if row:
            return row_to_skill(row)
        # any version
        row = conn.execute(
            """
            SELECT * FROM skill_prompt_ssot
            WHERE skill_key = ? AND prompt_key = ?
            ORDER BY id DESC LIMIT 1
            """,
            (skill_key, prompt_key),
        ).fetchone()
        if row:
            return row_to_skill(row)
    finally:
        if own:
            conn.close()
    return load_file_skill(skill_key) or (
        builtin_mouse_spot_skill() if skill_key == DEFAULT_SKILL else {
            "skill_key": skill_key,
            "prompt_key": prompt_key,
            "version_label": "missing",
            "status": "draft",
            "parser": "result_yes_no",
            "prompt_text": "Target={{target_name}} Action={{target_action}}\nResult: [YES / NO]\nReason: ...\n",
            "error": "skill_not_found",
        }
    )


def get_skill_version(
    skill_key: str,
    version_label: str,
    *,
    prompt_key: str = "main",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = False
    if conn is None and DEFAULT_DB.is_file():
        conn = _connect()
        own = True
        ensure_skill_tables(conn)
    try:
        if conn is not None:
            row = conn.execute(
                """
                SELECT * FROM skill_prompt_ssot
                WHERE skill_key = ? AND prompt_key = ? AND version_label = ?
                """,
                (skill_key, prompt_key, version_label),
            ).fetchone()
            if row:
                return row_to_skill(row)
    finally:
        if own and conn is not None:
            conn.close()
    return load_file_skill(skill_key, version_label)


def render_prompt(
    template: str,
    *,
    target_name: str = "target",
    target_action: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    text = template or ""
    mapping = {
        "target_name": target_name or "target",
        "target_action": target_action or "(none)",
    }
    if extra:
        mapping.update({str(k): str(v) for k, v in extra.items()})
    for key, val in mapping.items():
        text = text.replace("{{" + key + "}}", val)
        text = text.replace("{" + key + "}", val)
    return text


def upsert_skill_prompt(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    version_label: str,
    prompt_text: str,
    prompt_key: str = "main",
    status: str = "draft",
    parser: str = "result_yes_no",
    output_schema: str = "result_yes_no",
    model_default: str | None = "qwen2.5vl:7b",
    hard_rules: list[Any] | None = None,
    task_id: int | None = None,
    source: str = "skill_prompt",
    notes: str | None = None,
    activate: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    ensure_skill_tables(conn)
    skill_key = (skill_key or "").strip()
    version_label = (version_label or "").strip()
    prompt_key = (prompt_key or "main").strip() or "main"
    if not skill_key or not version_label:
        raise ValueError("skill_key and version_label required")
    if not (prompt_text or "").strip():
        raise ValueError("prompt_text required")
    status = (status or "draft").strip()
    if activate:
        status = "active"
    rules_json = json.dumps(hard_rules or [], ensure_ascii=False)
    existing = conn.execute(
        """
        SELECT id FROM skill_prompt_ssot
        WHERE skill_key = ? AND prompt_key = ? AND version_label = ?
        """,
        (skill_key, prompt_key, version_label),
    ).fetchone()
    if activate:
        conn.execute(
            """
            UPDATE skill_prompt_ssot
            SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP
            WHERE skill_key = ? AND prompt_key = ? AND status = 'active'
              AND NOT (version_label = ?)
            """,
            (skill_key, prompt_key, version_label),
        )
    if existing:
        sid = int(existing[0])
        conn.execute(
            """
            UPDATE skill_prompt_ssot
            SET prompt_text = ?, hard_rules_json = ?, output_schema = ?,
                parser = ?, model_default = ?, status = ?, task_id = ?,
                source = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                prompt_text,
                rules_json,
                output_schema,
                parser,
                model_default,
                status,
                task_id,
                source,
                notes,
                sid,
            ),
        )
        action = "updated"
    else:
        cur = conn.execute(
            """
            INSERT INTO skill_prompt_ssot (
                skill_key, prompt_key, version_label, prompt_text,
                hard_rules_json, output_schema, parser, model_default,
                status, task_id, source, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill_key,
                prompt_key,
                version_label,
                prompt_text,
                rules_json,
                output_schema,
                parser,
                model_default,
                status,
                task_id,
                source,
                notes,
            ),
        )
        sid = int(cur.lastrowid)
        action = "inserted"
    if commit:
        conn.commit()
    row = conn.execute("SELECT * FROM skill_prompt_ssot WHERE id = ?", (sid,)).fetchone()
    out = row_to_skill(row)
    out["action"] = action
    return out


def set_active_version(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    version_label: str,
    prompt_key: str = "main",
    commit: bool = True,
) -> dict[str, Any]:
    ensure_skill_tables(conn)
    row = conn.execute(
        """
        SELECT id FROM skill_prompt_ssot
        WHERE skill_key = ? AND prompt_key = ? AND version_label = ?
        """,
        (skill_key, prompt_key, version_label),
    ).fetchone()
    if not row:
        raise ValueError(f"version not found: {skill_key}/{version_label}")
    conn.execute(
        """
        UPDATE skill_prompt_ssot
        SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP
        WHERE skill_key = ? AND prompt_key = ? AND status = 'active'
        """,
        (skill_key, prompt_key),
    )
    conn.execute(
        """
        UPDATE skill_prompt_ssot
        SET status = 'active', updated_at = CURRENT_TIMESTAMP
        WHERE skill_key = ? AND prompt_key = ? AND version_label = ?
        """,
        (skill_key, prompt_key, version_label),
    )
    if commit:
        conn.commit()
    return get_active_skill(skill_key, prompt_key=prompt_key, conn=conn)


def seed_default_skills(
    db_path: Path | str | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    try:
        ensure_skill_tables(conn)
        # Prefer file seed
        data = load_file_skill(DEFAULT_SKILL, DEFAULT_VERSION) or builtin_mouse_spot_skill()
        # Write file if missing
        fpath = file_skill_path(DEFAULT_SKILL, DEFAULT_VERSION)
        if not fpath.is_file():
            fpath.parent.mkdir(parents=True, exist_ok=True)
            dump = {k: v for k, v in data.items() if not str(k).startswith("_")}
            fpath.write_text(json.dumps(dump, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        row = upsert_skill_prompt(
            conn,
            skill_key=data["skill_key"],
            version_label=data.get("version_label") or DEFAULT_VERSION,
            prompt_text=data.get("prompt_text") or MOUSE_SPOT_V1_PROMPT,
            prompt_key=data.get("prompt_key") or "main",
            status="active",
            parser=data.get("parser") or "result_yes_no",
            output_schema=data.get("output_schema") or "result_yes_no",
            model_default=data.get("model_default") or "qwen2.5vl:7b",
            hard_rules=data.get("hard_rules") or [],
            source=data.get("source") or "seed",
            notes=data.get("notes"),
            activate=True,
            commit=commit,
        )
        out: dict[str, Any] = {"ok": True, "seeded": row, "file": str(fpath)}
        # Phase 5: gold cases + Task Center root-10 (lazy import; never fail seed)
        try:
            from skill_prompt_ext import seed_gold_cases, seed_task_center_skill_root

            try:
                out["gold"] = seed_gold_cases(path, commit=commit)
            except Exception as e:
                out["gold_warn"] = f"{type(e).__name__}: {e}"
            try:
                out["task_center"] = seed_task_center_skill_root(path, commit=commit)
            except Exception as e:
                out["task_center_warn"] = f"{type(e).__name__}: {e}"
        except ImportError as e:
            out["ext_warn"] = f"skill_prompt_ext missing: {e}"
        # Task format validator skill (8-dim pre-dispatch gate) — best-effort
        try:
            from src.task_center.skill_task_validate import seed_task_format_validator

            try:
                out["task_format_validator"] = seed_task_format_validator(
                    path, commit=commit
                )
            except Exception as e:
                out["task_format_validator_warn"] = f"{type(e).__name__}: {e}"
        except ImportError as e:
            out["task_format_validator_import_warn"] = str(e)
        # Lifecycle recorder + draft pipeline skills + T-SKILL02 — best-effort
        try:
            from src.task_center.lifecycle_log import seed_lifecycle_skills

            try:
                out["lifecycle_skills"] = seed_lifecycle_skills(path, commit=commit)
            except Exception as e:
                out["lifecycle_skills_warn"] = f"{type(e).__name__}: {e}"
        except ImportError as e:
            out["lifecycle_skills_import_warn"] = str(e)
        return out
    finally:
        conn.close()


def parse_yes_no(detail: dict[str, Any], raw: str = "") -> str:
    """Return YES, NO, or OTHER."""
    if detail.get("correct") is True:
        return "YES"
    if detail.get("correct") is False:
        return "NO"
    rf = str(detail.get("result") or "").strip().upper()
    if rf in ("YES", "PASS", "SUCCESS", "TRUE"):
        return "YES"
    if rf in ("NO", "FAIL", "FALSE"):
        return "NO"
    text = (raw or detail.get("reason") or "").strip().lower()
    m = re.match(r"^(yes|no)\b", text)
    if m:
        return m.group(1).upper()
    return "OTHER"


def test_prompt_runs(
    *,
    skill_key: str = DEFAULT_SKILL,
    version_label: str | None = None,
    image_path: str | Path | None = None,
    target_name: str = "Visual Studio Code",
    target_action: str = "",
    expected: str = "NO",
    runs: int = 100,
    model: str | None = None,
    db_path: Path | str | None = None,
    persist: bool = True,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Run the skill prompt N times against one image; count YES/NO."""
    from vision_analyze import analyze_evidence

    expected_u = (expected or "NO").strip().upper()
    if expected_u not in ("YES", "NO"):
        raise ValueError("expected must be YES or NO")
    img = Path(image_path or DEFAULT_SCREENSHOT)
    if not img.is_file():
        raise FileNotFoundError(f"screenshot not found: {img}")

    conn = None
    if persist and Path(db_path or DEFAULT_DB).is_file():
        conn = _connect(db_path)
        ensure_skill_tables(conn)

    try:
        if version_label:
            skill = get_skill_version(skill_key, version_label, conn=conn) or get_active_skill(
                skill_key, conn=conn
            )
        else:
            skill = get_active_skill(skill_key, conn=conn)
        version_label = str(skill.get("version_label") or version_label or "unknown")
        template = skill.get("prompt_text") or MOUSE_SPOT_V1_PROMPT
        prompt = render_prompt(
            template, target_name=target_name, target_action=target_action
        )
        model = model or skill.get("model_default") or "qwen2.5vl:7b"
        parser = skill.get("parser") or "result_yes_no"

        yes = no = other = errors = 0
        durations: list[int] = []
        samples: list[dict[str, Any]] = []
        t0 = time.perf_counter()
        for i in range(max(1, int(runs))):
            try:
                result = analyze_evidence(
                    img,
                    fault_type=skill_key,
                    model=model,
                    prompt=prompt,
                    format_json=False,
                    parse_mode="result_yes_no" if parser == "result_yes_no" else "auto",
                    timeout=180,
                )
                if result.error:
                    errors += 1
                    ans = "ERROR"
                    reason = result.error
                    ms = result.duration_ms or 0
                else:
                    ans = parse_yes_no(result.detail or {}, result.raw_text or "")
                    reason = (result.detail or {}).get("reason") or result.summary or ""
                    ms = result.duration_ms or 0
                    if ans == "YES":
                        yes += 1
                    elif ans == "NO":
                        no += 1
                    else:
                        other += 1
                durations.append(int(ms))
                if len(samples) < 8 or ans not in ("YES", "NO"):
                    samples.append(
                        {
                            "i": i + 1,
                            "answer": ans,
                            "reason": str(reason)[:160],
                            "ms": ms,
                        }
                    )
                if (
                    persist
                    and conn is not None
                    and ans in ("YES", "NO")
                    and ans != expected_u
                ):
                    log_mismatch(
                        conn,
                        skill_key=skill_key,
                        version_label=version_label,
                        image_path=str(img),
                        target_name=target_name,
                        ask_output=ans,
                        expected=expected_u,
                        raw_response=result.raw_text or "",
                        model=model,
                        run_id=None,
                    )
            except Exception as e:
                errors += 1
                samples.append({"i": i + 1, "answer": "ERROR", "reason": str(e)[:160]})
            if progress_cb:
                progress_cb(i + 1, yes, no, other, errors)

        total = yes + no + other + errors
        wall_ms = int((time.perf_counter() - t0) * 1000)
        match = yes if expected_u == "YES" else no
        # accuracy among non-error runs that are YES/NO; errors count against accuracy
        accuracy = round(100.0 * match / total, 2) if total else 0.0
        yes_pct = round(100.0 * yes / total, 2) if total else 0.0
        no_pct = round(100.0 * no / total, 2) if total else 0.0
        avg_ms = int(sum(durations) / len(durations)) if durations else 0
        pass_gate = accuracy >= 95.0 and other == 0 and errors == 0

        out: dict[str, Any] = {
            "ok": True,
            "run_id": f"tr_{uuid.uuid4().hex[:12]}",
            "skill_key": skill_key,
            "version_label": version_label,
            "prompt_key": skill.get("prompt_key") or "main",
            "image_path": str(img),
            "target_name": target_name,
            "target_action": target_action,
            "expected": expected_u,
            "model": model,
            "runs": total,
            "yes": yes,
            "no": no,
            "other": other,
            "errors": errors,
            "yes_pct": yes_pct,
            "no_pct": no_pct,
            "accuracy_pct": accuracy,
            "pass_gate": pass_gate,
            "avg_duration_ms": avg_ms,
            "wall_ms": wall_ms,
            "samples": samples,
            "prompt_preview": prompt[:400],
            "started_at": _utc_now(),
        }

        if persist and conn is not None:
            conn.execute(
                """
                INSERT INTO skill_prompt_test_run (
                    run_id, skill_key, version_label, prompt_key,
                    image_path, target_name, target_action, expected,
                    n_runs, yes_count, no_count, other_count, error_count,
                    yes_pct, no_pct, accuracy_pct, pass_gate,
                    model, avg_duration_ms, wall_ms, raw_json, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    out["run_id"],
                    skill_key,
                    version_label,
                    skill.get("prompt_key") or "main",
                    str(img),
                    target_name,
                    target_action,
                    expected_u,
                    total,
                    yes,
                    no,
                    other,
                    errors,
                    yes_pct,
                    no_pct,
                    accuracy,
                    1 if pass_gate else 0,
                    model,
                    avg_ms,
                    wall_ms,
                    json.dumps(out, ensure_ascii=False),
                    "skill_prompt_test",
                ),
            )
            conn.commit()
        return out
    finally:
        if conn is not None:
            conn.close()


def log_mismatch(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    version_label: str | None = None,
    image_path: str | None = None,
    target_name: str | None = None,
    ask_output: str | None = None,
    expected: str | None = None,
    raw_response: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
    error_reason: str | None = None,
) -> str:
    """Record an ask<->confirm mismatch into the Skill Learning Center log."""
    key = f"mm_{uuid.uuid4().hex[:12]}"
    # THE WRITE-SITE STANDARDISER. `error_reason=None` used to be passed
    # explicitly, which OVERRIDES the DDL's `NOT NULL DEFAULT 'NA'` — so the
    # default never applied and every mismatch with no error wrote a NULL.
    # `insert_row` standardises the empty value to `NA` and then GATES the row,
    # so a NULL cannot reach the table from here.
    _nn.insert_row(conn, "skill_mismatch_log", {
        "mismatch_key": key,
        "skill_key": skill_key,
        "version_label": version_label,
        "image_path": image_path,
        "target_name": target_name,
        "ask_output": ask_output,
        "expected": expected,
        "raw_response": (raw_response or "")[:2000] or None,
        "model": model,
        "run_id": run_id,
        "error_reason": error_reason,
        "status": "to_review",
    })
    conn.commit()
    return key


def streak_proof(
    *,
    skill_key: str = "captcha_cell_detect",
    version_label: str | None = None,
    prompt_key: str = "main",
    case_keys: list[str] | None = None,
    target_streak: int = 100,
    max_asks: int | None = None,
    gate_mode: str = "streak",
    min_accuracy_pct: float = 90.0,
    min_samples: int = 100,
    model: str | None = None,
    db_path: Path | str | None = None,
    persist: bool = True,
    seed: int | None = None,
    progress_cb: Any | None = None,
    combo_key: str | None = None,
    dim_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Streak-proof mode: N consecutive correct ask↔confirm rounds.

    Each round uses a DIFFERENT gold case from skill_prompt_case (shuffled).
    A wrong answer (or error) resets the streak to 0. Every round is persisted to
    skill_prompt_inference; the summary goes to skill_prompt_test_run.

    `prompt_key` selects WHICH prompt document to test. Together with
    `version_label` it identifies the combination in the multi-dimensional SSOT
    (see prompt_dimension.py), so different prompt variants can be measured
    without replacing each other.

    `case_keys` restricts the run to a subset of gold cases. This is what makes
    a TRAIN/HOLDOUT split possible: measuring a variant on the same cases used
    to choose it is how a prompt gets tuned to the fixtures instead of the task.

    GATE SHAPE (`gate_mode`) — pick one deliberately:
      "streak" (default) - pass_gate = best_streak >= target_streak.
          N CONSECUTIVE correct answers.
      "rate"             - pass_gate = accuracy >= min_accuracy_pct over
          >= min_samples answers.

    The "streak" shape is kept as the default so existing behaviour is unchanged.
    Do not switch shape to make a prompt pass; switch it on principle and then
    HOLD the threshold. See the note at the gate computation for the measured
    reason the two shapes differ so much in practice.

    `combo_key` / `dim_values` (added 2026-09-20): when supplied, the prompt is
    COMPOSED per case via `prompt_dimension.compose_prompt()` instead of being
    rendered from the single stored `prompt_text`. Before this, the generator
    center could not reach this path at all — the 100 proof ran on one fixed
    string, so a variant could not be measured here. The composed
    `composition_key` is recorded on the run, so the result states WHICH
    composition it measured.
    """
    import random

    from vision_analyze import analyze_evidence

    target_streak = max(1, int(target_streak))
    conn = None
    if Path(db_path or DEFAULT_DB).is_file():
        conn = _connect(db_path)
        ensure_skill_tables(conn)

    try:
        if version_label:
            skill = get_skill_version(
                skill_key, version_label, prompt_key=prompt_key, conn=conn
            ) or get_active_skill(skill_key, prompt_key=prompt_key, conn=conn)
        else:
            skill = get_active_skill(skill_key, prompt_key=prompt_key, conn=conn)
        if not skill:
            raise ValueError(f"no skill prompt found for skill_key={skill_key}")
        version_label = str(skill.get("version_label") or "unknown")
        template = skill.get("prompt_text") or ""
        model = model or skill.get("model_default") or "qwen2.5vl:7b"
        parser = skill.get("parser") or "result_yes_no"

        if conn is None:
            raise ValueError("streak mode requires the DB (gold cases live in skill_prompt_case)")
        sql = (
            """
            SELECT case_key, image_path, target_name, target_action, expected
            FROM skill_prompt_case
            WHERE skill_key = ? AND status = 'active' AND image_path IS NOT NULL
            """
        )
        params: list[Any] = [skill_key]
        if case_keys is not None:
            if not case_keys:
                raise ValueError("case_keys was given but is empty")
            sql += " AND case_key IN (%s)" % ",".join("?" * len(case_keys))
            params.extend(case_keys)
        cases = conn.execute(sql, tuple(params)).fetchall()
        if not cases:
            raise ValueError(f"no active gold cases for skill_key={skill_key}")
        cases = [dict(r) for r in cases]
        rng = random.Random(seed)
        rng.shuffle(cases)

        run_id = f"st_{uuid.uuid4().hex[:12]}"
        streak = best_streak = 0
        total = correct = wrong = errors = 0
        yes = no = other = 0
        tallies: dict[str, list[int]] = {}
        durations: list[int] = []
        t0 = time.perf_counter()
        idx = 0
        while streak < target_streak:
            if max_asks is not None and total >= max_asks:
                break
            case = cases[idx % len(cases)]
            idx += 1
            total += 1
            img = Path(case["image_path"])
            if not img.is_file():
                errors += 1
                streak = 0
                continue
            prompt = render_prompt(
                template,
                target_name=case.get("target_name") or "",
                target_action=case.get("target_action") or "",
            )
            # COMPOSED PROMPT (2026-09-20): when a combo is supplied, compose
            # per case instead of rendering the one stored template. The
            # dimension composer is REUSED, never reimplemented — a second
            # composer would drift, and the drift would be invisible until a
            # measurement disagreed with itself.
            if combo_key or dim_values:
                try:
                    # SSOT: registers, not the retired prompt_dimension.
                    # `prompt_generator` provides the SAME compose_prompt API.
                    import prompt_generator as _pd

                    prompt = _pd.compose_prompt(
                        conn, skill_key, dim_values or {}, template=template,
                    )
                except Exception as e:
                    errors += 1
                    streak = 0
                    if progress_cb:
                        progress_cb(total, streak,
                                    "compose failed: %s: %s"
                                    % (type(e).__name__, e))
                    continue
            ans = "ERROR"
            reason = ""
            ms = 0
            raw = ""
            try:
                result = analyze_evidence(
                    img,
                    fault_type=skill_key,
                    model=model,
                    prompt=prompt,
                    format_json=False,
                    parse_mode="result_yes_no" if parser == "result_yes_no" else "auto",
                    timeout=180,
                )
                raw = result.raw_text or ""
                ms = result.duration_ms or 0
                if result.error:
                    errors += 1
                    reason = result.error
                else:
                    ans = parse_yes_no(result.detail or {}, raw)
                    reason = (result.detail or {}).get("reason") or result.summary or ""
                    if ans == "YES":
                        yes += 1
                    elif ans == "NO":
                        no += 1
                    else:
                        other += 1
            except Exception as e:
                errors += 1
                reason = str(e)[:200]
            durations.append(int(ms))

            expected = (case.get("expected") or "").upper()
            is_correct = ans == expected
            # PER-CLASS TALLY. Plain accuracy on an imbalanced set rewards the
            # strategy of always answering the majority class. MEASURED: on
            # mouse_spot_verify (14 NO : 6 YES) the always-NO baseline scored
            # 70% accuracy while getting 0/12 YES cases right — i.e. 70% for a
            # prompt that cannot detect a hit at all. Per-class recall is what
            # exposes that, so it is recorded per round rather than reconstructed
            # later.
            tallies.setdefault(expected, [0, 0])
            tallies[expected][0] += 1              # n of this expected class
            if is_correct:
                tallies[expected][1] += 1          # correct of this class
            if is_correct:
                correct += 1
                streak += 1
                best_streak = max(best_streak, streak)
            else:
                wrong += 1
                streak = 0
                if persist and conn is not None:
                    log_mismatch(
                        conn,
                        skill_key=skill_key,
                        version_label=version_label,
                        image_path=str(img),
                        target_name=case.get("target_name") or "",
                        ask_output=ans,
                        expected=expected,
                        raw_response=raw,
                        model=model,
                        run_id=run_id,
                    )

            if persist and conn is not None:
                # THE WRITE-SITE STANDARDISER. `run_id` can be None, and this
                # INSERT wrote it straight into `task_run_id` — the NULL that
                # `_proof_skill_ref_migration` reports as "a resolvable key still
                # has a NULL skill_ref". `insert_row` standardises it to `NA`.
                _nn.insert_row(conn, "skill_prompt_inference", {
                    "inference_id": f"inf_{uuid.uuid4().hex[:12]}",
                    "skill_key": skill_key,
                    "version_label": version_label,
                    "prompt_key": skill.get("prompt_key") or "main",
                    "task_run_id": run_id,
                    "target_name": case.get("target_name") or "",
                    "target_action": case.get("target_action") or "",
                    "image_path": str(img),
                    "raw_response": raw[:2000],
                    "parsed_result": ans,
                    "final_result": expected,
                    "reason": str(reason)[:300],
                    "model": model,
                    "is_wrong": 0 if is_correct else 1,
                    "meta_json": json.dumps(
                        {
                            "case_key": case.get("case_key"),
                            "streak_after": streak,
                            "best_streak": best_streak,
                            "ms": ms,
                        },
                        ensure_ascii=False,
                    ),
                })
                conn.commit()

            if progress_cb:
                progress_cb(total, streak, best_streak, correct, wrong, errors)

        wall_ms = int((time.perf_counter() - t0) * 1000)
        accuracy = round(100.0 * correct / total, 2) if total else 0.0

        # GATE SHAPE. Two shapes; the caller picks one explicitly.
        #
        # MEASURED reason the shapes differ so much: "N consecutive" is
        # EXPONENTIALLY sensitive to the per-case error rate. On
        # mouse_spot_verify the observed rate was 18.1% (p=0.819), giving a
        # 20-run an expected wait of ~125,000 rounds; that attempt had to be
        # killed at 1192 rounds with best_streak=9. Even at p=0.95 a 20-run
        # happens only 36% of the time. A pass-rate gate on the same data is
        # stable and still fails a bad prompt.
        #
        # Changing the SHAPE is not the same as lowering the bar. Lowering the
        # bar (e.g. 90% -> 60% so a candidate passes) is the false confidence
        # this project exists to remove.
        if gate_mode not in ("streak", "rate"):
            raise ValueError(
                f"gate_mode must be 'streak' or 'rate', got {gate_mode!r}"
            )
        if gate_mode == "streak":
            pass_gate = best_streak >= target_streak
        else:
            pass_gate = (
                total >= max(1, int(min_samples))
                and accuracy >= float(min_accuracy_pct)
            )

        # DISCRIMINATION. A run in which the model answered only ONE class is
        # not evidence about the prompt — it is a restatement of the gold set's
        # class mix. Measured case: two different prompts on mouse_spot_verify
        # both answered "NO" 60/60, scoring a bit-identical 81.67%, because the
        # score was arithmetic over an 9:2 NO:YES case mix and had nothing to do
        # with either prompt. That was reported as "no significant difference"
        # (p=1.000), which is a tautology, not a finding.
        #
        # So the run records how many answer classes it actually produced. A
        # single-class run cannot support any claim about which prompt is better.
        answer_classes = {a for a, n in (("YES", yes), ("NO", no), ("OTHER", other)) if n}
        discriminating = len(answer_classes) >= 2

        # BALANCED ACCURACY = mean per-class recall. Plain accuracy rewards
        # always answering the majority class; balanced accuracy does not. On a
        # 14 NO : 6 YES set the always-NO prompt above scored 70% accuracy but
        # 0/6 = 0% YES recall, giving balanced accuracy 50% — the honest number
        # for a coin that always lands the same way.
        recall: dict[str, float] = {
            cls: round(100.0 * c / n, 2) for cls, (n, c) in tallies.items() if n
        }
        balanced_accuracy = (
            round(sum(recall.values()) / len(recall), 2) if recall else 0.0
        )

        out: dict[str, Any] = {
            "ok": True,
            "run_id": run_id,
            "skill_key": skill_key,
            "version_label": version_label,
            "prompt_key": skill.get("prompt_key") or prompt_key,
            "mode": "streak",
            "gate_mode": gate_mode,
            "min_accuracy_pct": min_accuracy_pct,
            "min_samples": min_samples,
            "target_streak": target_streak,
            "model": model,
            "n_cases": len(cases),
            "case_filter": sorted(case_keys) if case_keys is not None else None,
            "asks": total,
            "correct": correct,
            "wrong": wrong,
            "errors": errors,
            "yes": yes,
            "no": no,
            "other": other,
            "answer_classes": sorted(answer_classes),
            "discriminating": discriminating,
            "per_class": {cls: {"n": n, "correct": c, "recall_pct": recall.get(cls, 0.0)}
                          for cls, (n, c) in tallies.items()},
            "balanced_accuracy_pct": balanced_accuracy,
            "final_streak": streak,
            "best_streak": best_streak,
            "accuracy_pct": accuracy,
            "pass_gate": pass_gate,
            "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            "wall_ms": wall_ms,
            "seed": seed,
            "started_at": _utc_now(),
            # WHICH composition was measured (2026-09-20). Without this a run
            # states a number but not what produced it, so two runs of different
            # prompts are indistinguishable in the ledger.
            "combo_key": combo_key,
            "dim_values": dict(sorted((dim_values or {}).items())),
            "prompt_source": "composed" if (combo_key or dim_values)
                             else "stored_prompt_text",
        }

        if persist and conn is not None:
            conn.execute(
                """
                INSERT INTO skill_prompt_test_run (
                    run_id, skill_key, version_label, prompt_key,
                    image_path, target_name, target_action, expected,
                    n_runs, yes_count, no_count, other_count, error_count,
                    yes_pct, no_pct, accuracy_pct, pass_gate,
                    model, avg_duration_ms, wall_ms, raw_json, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    skill_key,
                    version_label,
                    skill.get("prompt_key") or prompt_key,
                    f"streak:{len(cases)}_cases",
                    "streak",
                    "",
                    "MIXED",
                    total,
                    yes,
                    no,
                    other,
                    errors,
                    round(100.0 * yes / total, 2) if total else 0.0,
                    round(100.0 * no / total, 2) if total else 0.0,
                    accuracy,
                    1 if pass_gate else 0,
                    model,
                    out["avg_duration_ms"],
                    wall_ms,
                    json.dumps(out, ensure_ascii=False),
                    "skill_prompt_streak",
                ),
            )
            conn.commit()
        return out
    finally:
        if conn is not None:
            conn.close()


def latest_test_run(
    skill_key: str = DEFAULT_SKILL,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any] | None:
    path = Path(db_path or DEFAULT_DB)
    if not path.is_file():
        return None
    conn = _connect(path)
    try:
        ensure_skill_tables(conn)
        row = conn.execute(
            """
            SELECT * FROM skill_prompt_test_run
            WHERE skill_key = ?
            ORDER BY id DESC LIMIT 1
            """,
            (skill_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Skill Prompt SSOT tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="Seed mouse_spot_verify v1 into DB + file")

    pl = sub.add_parser("list", help="List skill prompt versions")
    pl.add_argument("--skill", default=None)
    pl.add_argument("--db", default=str(DEFAULT_DB))

    ps = sub.add_parser("show", help="Show active or specific version")
    ps.add_argument("--skill", default=DEFAULT_SKILL)
    ps.add_argument("--version", default=None)
    ps.add_argument("--db", default=str(DEFAULT_DB))

    pa = sub.add_parser("set-active", help="Activate a version")
    pa.add_argument("--skill", default=DEFAULT_SKILL)
    pa.add_argument("--version", required=True)
    pa.add_argument("--db", default=str(DEFAULT_DB))

    pt = sub.add_parser("test", help="Run N-times proof test")
    pt.add_argument("--skill", default=DEFAULT_SKILL)
    pt.add_argument("--version", default=None)
    pt.add_argument("--expected", default="NO", choices=["YES", "NO", "yes", "no"])
    pt.add_argument("--runs", type=int, default=10)
    pt.add_argument("--target", default="Visual Studio Code")
    pt.add_argument("--action", default="")
    pt.add_argument("--image", default=str(DEFAULT_SCREENSHOT))
    pt.add_argument("--model", default=None)
    pt.add_argument("--db", default=str(DEFAULT_DB))
    pt.add_argument("--no-persist", action="store_true")

    pst = sub.add_parser("streak", help="N-consecutive-success streak proof")
    pst.add_argument("--skill", default="captcha_cell_detect")
    pst.add_argument("--version", default=None)
    pst.add_argument("--streak", type=int, default=100)
    pst.add_argument("--max-asks", type=int, default=None)
    pst.add_argument("--model", default=None)
    pst.add_argument("--seed", type=int, default=None)
    pst.add_argument("--db", default=str(DEFAULT_DB))
    pst.add_argument("--no-persist", action="store_true")

    args = p.parse_args(argv)

    if args.cmd == "seed":
        out = seed_default_skills(getattr(args, "db", None) or DEFAULT_DB)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "list":
        conn = _connect(args.db) if Path(args.db).is_file() else None
        try:
            if conn:
                ensure_skill_tables(conn)
            rows = list_skills(conn, skill_key=args.skill)
        finally:
            if conn:
                conn.close()
        slim = [
            {
                "id": r.get("id"),
                "skill_key": r.get("skill_key"),
                "version_label": r.get("version_label"),
                "status": r.get("status"),
                "parser": r.get("parser"),
                "source": r.get("source"),
            }
            for r in rows
        ]
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "show":
        if args.version:
            row = get_skill_version(args.skill, args.version)
        else:
            row = get_active_skill(args.skill, db_path=args.db)
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.cmd == "set-active":
        conn = _connect(args.db)
        try:
            ensure_skill_tables(conn)
            row = set_active_version(
                conn, skill_key=args.skill, version_label=args.version
            )
            print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
        finally:
            conn.close()
        return 0

    if args.cmd == "test":
        def _prog(i, y, n, o, e):
            print(f"{i}: yes={y} no={n} other={o} err={e}", flush=True)

        out = test_prompt_runs(
            skill_key=args.skill,
            version_label=args.version,
            image_path=args.image,
            target_name=args.target,
            target_action=args.action,
            expected=args.expected.upper(),
            runs=args.runs,
            model=args.model,
            db_path=args.db,
            persist=not args.no_persist,
            progress_cb=_prog,
        )
        print("--- SUMMARY ---")
        print(json.dumps({k: out[k] for k in out if k != "samples"}, ensure_ascii=False, indent=2))
        return 0 if out.get("pass_gate") or out.get("ok") else 1

    if args.cmd == "streak":
        def _prog(i, s, bs, c, w, e):
            print(f"{i}: streak={s} best={bs} ok={c} wrong={w} err={e}", flush=True)

        out = streak_proof(
            skill_key=args.skill,
            version_label=args.version,
            target_streak=args.streak,
            max_asks=args.max_asks,
            model=args.model,
            db_path=args.db,
            persist=not args.no_persist,
            seed=args.seed,
            progress_cb=_prog,
        )
        print("--- SUMMARY ---")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("pass_gate") else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
