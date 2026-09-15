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
            SKILL_PROMPT_CASE_DDL,
            SKILL_PROMPT_INFERENCE_DDL,
            SKILL_PROMPT_SSOT_DDL,
            SKILL_PROMPT_TEST_RUN_DDL,
        )

        for ddl in (
            SKILL_PROMPT_SSOT_DDL,
            SKILL_PROMPT_CASE_DDL,
            SKILL_PROMPT_TEST_RUN_DDL,
            SKILL_PROMPT_INFERENCE_DDL,
        ):
            conn.executescript(ddl)
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


def file_skill_path(skill_key: str, version_label: str) -> Path:
    return SKILLS_DIR / skill_key / f"{version_label}.json"


def load_file_skill(
    skill_key: str = DEFAULT_SKILL,
    version_label: str = DEFAULT_VERSION,
) -> dict[str, Any] | None:
    path = file_skill_path(skill_key, version_label)
    if not path.is_file():
        # try any json in folder
        folder = SKILLS_DIR / skill_key
        if folder.is_dir():
            for p in sorted(folder.glob("*.json")):
                path = p
                break
        else:
            return None
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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
