"""Skill Prompt SSOT — Phase 5 extensions.

Gold cases, Task Center root-10 seed, improve→draft only.

Importable library (no side effects on import).

Usage examples:
  from skill_prompt_ext import (
      SKILL_TC_ITEMS,
      seed_gold_cases,
      seed_task_center_skill_root,
      improve_prompt_to_draft,
      list_skill_cases,
      upsert_skill_case,
      test_gold_suite,
      format_task_id,
  )
  python -c "from skill_prompt_ext import seed_task_center_skill_root; print(seed_task_center_skill_root())"
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill_prompt import (
    BASE_DIR,
    DEFAULT_DB,
    DEFAULT_SCREENSHOT,
    DEFAULT_SKILL,
    DEFAULT_VERSION,
    MOUSE_SPOT_V1_PROMPT,
    _connect,
    ensure_skill_tables,
    get_active_skill,
    get_skill_version,
    render_prompt,
    test_prompt_runs,
    upsert_skill_prompt,
)

# ---------------------------------------------------------------------------
# Constants — Root 10 / mouse_spot_helper / skill-1.0
# ---------------------------------------------------------------------------

ROOT_TASK_ID = 10
SKILL_MODULE_CODE = "mouse_spot_helper"
SKILL_MODULE_NAME = "Mouse Spot Helper"
SKILL_CHANNEL_CODE = "local_pc"
SKILL_CHANNEL_NAME = "Local PC"
SKILL_VERSION_LABEL = "skill-1.0"  # alias accepted: sp-1.0
SKILL_VERSION_ALIASES = ("skill-1.0", "sp-1.0")
SKILL_SYSTEM_KEY = "skill_prompt_ssot"
SKILL_ROOT_LABEL = str(ROOT_TASK_ID)
DEFAULT_IMPROVE_MODEL = "qwen2.5:7b-instruct"
GATE_POLICY = "never"
SOURCE_SEED = "skill_prompt_ext"

TARGETS_DIR = BASE_DIR / "mouse_spot_targets"
CASES_DIR = BASE_DIR / "skills" / DEFAULT_SKILL / "cases"

# Continuous global sequence 10.1–10.20 (F/A/T/D/J/E share one counter).
# item_type is metadata only; ID = {root}.{seq}
SKILL_TC_ITEMS: list[dict[str, Any]] = [
    # Functions
    {"seq": 1, "item_type": "F", "name": "skill_prompt_load", "title": "Load skill prompt SSOT"},
    {"seq": 2, "item_type": "F", "name": "skill_prompt_render", "title": "Render skill prompt placeholders"},
    {"seq": 3, "item_type": "F", "name": "mouse_spot_verify", "title": "Mouse spot visual verify"},
    {"seq": 4, "item_type": "F", "name": "skill_prompt_test_100", "title": "Skill prompt 100-run harness"},
    {"seq": 5, "item_type": "F", "name": "skill_prompt_promote", "title": "Promote skill prompt version"},
    # APIs
    {"seq": 6, "item_type": "A", "name": "GET /api/skills", "title": "List skills/versions"},
    {"seq": 7, "item_type": "A", "name": "GET /api/skills/:id", "title": "Get skill active + versions"},
    {"seq": 8, "item_type": "A", "name": "POST /api/skills/:id/test", "title": "Run skill proof test"},
    {"seq": 9, "item_type": "A", "name": "POST /api/skills/:id/activate", "title": "Activate skill version"},
    {"seq": 10, "item_type": "A", "name": "POST /api/analyze", "title": "Analyze bind skill SSOT"},
    # Tables
    {"seq": 11, "item_type": "T", "name": "skill_prompt_ssot", "title": "Table skill_prompt_ssot"},
    {"seq": 12, "item_type": "T", "name": "skill_prompt_case", "title": "Table skill_prompt_case"},
    {"seq": 13, "item_type": "T", "name": "skill_prompt_test_run", "title": "Table skill_prompt_test_run"},
    {"seq": 14, "item_type": "T", "name": "skill_prompt_inference", "title": "Table skill_prompt_inference"},
    # Fields
    {"seq": 15, "item_type": "D", "name": "skill_key", "title": "Field skill_key"},
    {"seq": 16, "item_type": "D", "name": "version_label", "title": "Field version_label"},
    {"seq": 17, "item_type": "D", "name": "prompt_text", "title": "Field prompt_text"},
    # Jobs / Events
    {"seq": 18, "item_type": "J", "name": "skill_prompt_regression_100", "title": "Job skill prompt regression 100"},
    {"seq": 19, "item_type": "E", "name": "skill_prompt_promoted", "title": "Event skill prompt promoted"},
    {"seq": 20, "item_type": "E", "name": "mouse_spot_verify_done", "title": "Event mouse_spot_verify done"},
]

# Default gold cases (screenshot = NO on VS Code; icons optional YES/NO fixtures).
DEFAULT_GOLD_CASES: list[dict[str, Any]] = [
    {
        "case_key": "msv_shot_vscode_no",
        "skill_key": DEFAULT_SKILL,
        "image_path": str(DEFAULT_SCREENSHOT),
        "target_name": "Visual Studio Code",
        "target_action": "open",
        "expected": "NO",
        "expected_reason": "Crosshair not on VS Code icon (current desktop shot).",
        "notes": "Primary stability/correctness fixture from mouse_spot_screenshot.png",
        "source": "seed_gold",
        "status": "active",
    },
    {
        "case_key": "msv_shot_generic_target_no",
        "skill_key": DEFAULT_SKILL,
        "image_path": str(DEFAULT_SCREENSHOT),
        "target_name": "target",
        "target_action": "",
        "expected": "NO",
        "expected_reason": "Generic target name; shot is fail fixture.",
        "notes": "Secondary NO case on same screenshot",
        "source": "seed_gold",
        "status": "active",
    },
    {
        "case_key": "msv_shot_doubao_no",
        "skill_key": DEFAULT_SKILL,
        "image_path": str(DEFAULT_SCREENSHOT),
        "target_name": "豆包 AI",
        "target_action": "open",
        "expected": "NO",
        "expected_reason": "Crosshair not on 豆包 AI icon (current desktop shot).",
        "notes": "Third active NO fixture; target name from mouse_spot_targets.json",
        "source": "seed_gold",
        "status": "active",
    },
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def format_task_id(root: int | str = ROOT_TASK_ID, seq: int | str = 1) -> str:
    """Format Task Center id as ``{Root}.{GlobalSequenceNumber}``."""
    return f"{int(root)}.{int(seq)}"


def skill_tc_item_lines(root: int | str = ROOT_TASK_ID) -> list[str]:
    """Human lines: ``10.1 skill_prompt_load`` …"""
    return [
        f"{format_task_id(root, it['seq'])} {it['name']}"
        for it in SKILL_TC_ITEMS
    ]


def list_skill_task_records(
    db_path: Path | str | None = None,
    *,
    root: int | str = ROOT_TASK_ID,
) -> dict[str, Any]:
    """List seeded Task Center rows for a root (``10``, ``10.1`` …) with channel/module.

    Returns table-ready records:
    Date | Task ID | channel | module | task name | status
    """
    path = Path(db_path or DEFAULT_DB)
    root_s = str(int(root))
    out: dict[str, Any] = {
        "ok": True,
        "root": int(root_s),
        "count": 0,
        "records": [],
        "db": str(path),
    }
    if not path.is_file():
        out["ok"] = False
        out["error"] = "agent.db missing"
        return out

    conn = _connect(path)
    try:
        ensure_skill_tables(conn)
        rows = conn.execute(
            """
            SELECT d.id AS db_id,
                   d.task_label,
                   d.title,
                   d.status,
                   d.created_at,
                   d.updated_at,
                   d.parent_task_id,
                   d.payload_json,
                   d.channel_id,
                   d.module_id,
                   d.version_id,
                   ch.code AS channel_code,
                   ch.name AS channel_name,
                   m.code AS module_code,
                   m.name AS module_name
            FROM dev_task d
            LEFT JOIN channel ch ON ch.id = d.channel_id
            LEFT JOIN module m ON m.id = d.module_id
            WHERE d.task_label = ?
               OR d.task_label LIKE (? || '.%')
            ORDER BY CASE WHEN d.task_label = ? THEN 0 ELSE 1 END,
                     CAST(
                       CASE
                         WHEN instr(d.task_label, '.') > 0
                         THEN substr(d.task_label, instr(d.task_label, '.') + 1)
                         ELSE '0'
                       END AS INTEGER
                     )
            """,
            (root_s, root_s, root_s),
        ).fetchall()

        meta_by_seq = {int(it["seq"]): it for it in SKILL_TC_ITEMS}
        records: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            label = str(d.get("task_label") or "")
            payload: dict[str, Any] = {}
            raw = d.get("payload_json")
            if isinstance(raw, str) and raw.strip():
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"_raw": raw}
            elif isinstance(raw, dict):
                payload = raw

            item_type = payload.get("item_type")
            item_name = payload.get("name")
            seq = payload.get("seq")
            if seq is None and "." in label:
                try:
                    seq = int(label.split(".", 1)[1])
                except Exception:
                    seq = None
            if seq is not None and int(seq) in meta_by_seq:
                meta = meta_by_seq[int(seq)]
                item_type = item_type or meta.get("item_type")
                item_name = item_name or meta.get("name")

            task_name = (
                item_name
                or (d.get("title") or "").replace(label, "", 1).strip(" -:")
                or d.get("title")
                or label
            )
            date_s = str(d.get("updated_at") or d.get("created_at") or "")[:19]
            records.append(
                {
                    "db_id": d.get("db_id"),
                    "date": date_s,
                    "task_id": label,
                    "channel": d.get("channel_name") or d.get("channel_code") or "",
                    "channel_code": d.get("channel_code") or "",
                    "module": d.get("module_name") or d.get("module_code") or "",
                    "module_code": d.get("module_code") or "",
                    "task_name": task_name,
                    "title": d.get("title") or "",
                    "status": d.get("status") or "",
                    "item_type": item_type or ("ROOT" if label == root_s else ""),
                    "seq": seq,
                    "parent_task_id": d.get("parent_task_id"),
                    "created_at": d.get("created_at"),
                    "updated_at": d.get("updated_at"),
                    "channel_id": d.get("channel_id"),
                    "module_id": d.get("module_id"),
                    "version_id": d.get("version_id"),
                    "payload": payload,
                }
            )

        out["count"] = len(records)
        out["records"] = records
        return out
    except Exception as e:
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        conn.close()


def get_skill_task_record(
    task_id: str | int,
    db_path: Path | str | None = None,
    *,
    root: int | str = ROOT_TASK_ID,
) -> dict[str, Any]:
    """Fetch one Task Center record by ``task_label`` (e.g. ``10.3``) or db id."""
    listing = list_skill_task_records(db_path, root=root)
    if not listing.get("ok"):
        return listing
    key = str(task_id).strip()
    for rec in listing.get("records") or []:
        if str(rec.get("task_id")) == key or str(rec.get("db_id")) == key:
            # attach task_ssot dims when possible
            path = Path(db_path or DEFAULT_DB)
            dims: list[dict[str, Any]] = []
            try:
                conn = _connect(path)
                try:
                    db_id = rec.get("db_id")
                    if db_id is not None:
                        rows = conn.execute(
                            """
                            SELECT id, dim_key, value_text, value_type, source,
                                   sort_order, notes, updated_at
                            FROM task_ssot
                            WHERE task_id = ?
                            ORDER BY sort_order, id
                            """,
                            (int(db_id),),
                        ).fetchall()
                        dims = [dict(r) for r in rows]
                finally:
                    conn.close()
            except Exception as e:
                return {
                    "ok": True,
                    "record": rec,
                    "dims": [],
                    "dims_warn": f"{type(e).__name__}: {e}",
                }
            return {"ok": True, "record": rec, "dims": dims, "root": listing.get("root")}
    return {"ok": False, "error": f"task not found: {key}", "root": listing.get("root")}


def _get_or_create_dim(
    conn: sqlite3.Connection,
    table: str,
    code: str,
    name: str,
) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        f"INSERT INTO {table} (code, name) VALUES (?, ?)",
        (code, name),
    )
    return int(cur.lastrowid)


def _ensure_action(
    conn: sqlite3.Connection,
    *,
    element: str,
    action: str,
    code: str,
    name: str,
    requires_tdd: int = 0,
) -> int:
    row = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", (code,)
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO task_action_name
            (element, action, code, name, requires_tdd, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (element, action, code, name, int(requires_tdd)),
    )
    return int(cur.lastrowid)


def _action_code_for_item_type(item_type: str) -> tuple[str, str, str, str]:
    """Return (element, action, code, name) for task_action_name."""
    t = (item_type or "F").upper()
    mapping = {
        "F": ("function", "define", "function.define", "Define function"),
        "A": ("api", "define", "api.define", "Define API"),
        "T": ("table", "create", "table.create", "Create table"),
        "D": ("field", "create", "field.create", "Create field"),
        "J": ("job", "define", "job.define", "Define job"),
        "E": ("event", "define", "event.define", "Define event"),
    }
    return mapping.get(t, mapping["F"])


def _resolve_version_id(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    module_id: int,
    version_label: str = SKILL_VERSION_LABEL,
) -> int:
    """Get or create version_center row; accept skill-1.0 / sp-1.0 aliases."""
    labels = []
    for lab in (version_label, *SKILL_VERSION_ALIASES):
        lab = (lab or "").strip()
        if lab and lab not in labels:
            labels.append(lab)

    for lab in labels:
        ver = conn.execute(
            """
            SELECT id FROM version_center
            WHERE channel_id = ? AND module_id = ? AND version_label = ?
            """,
            (channel_id, module_id, lab),
        ).fetchone()
        if ver:
            return int(ver[0])

    primary = labels[0] if labels else SKILL_VERSION_LABEL
    cur = conn.execute(
        """
        INSERT INTO version_center
            (channel_id, module_id, version_label, title, notes, status)
        VALUES (?, ?, ?, ?, ?, 'active')
        """,
        (
            channel_id,
            module_id,
            primary,
            "Skill Prompt SSOT spine",
            "Root 10 continuous F/A/T/D/J/E; gate=never; promote after proof",
        ),
    )
    return int(cur.lastrowid)


def _open_db(db_path: Path | str | None = None) -> tuple[sqlite3.Connection, bool]:
    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    ensure_skill_tables(conn)
    try:
        from db_schema import ensure_task_center_schema

        ensure_task_center_schema(conn)
    except Exception:
        pass
    return conn, True


def _discover_target_icon_cases() -> list[dict[str, Any]]:
    """Optional gold cases from mouse_spot_targets/*.png if present."""
    out: list[dict[str, Any]] = []
    if not TARGETS_DIR.is_dir():
        return out
    # Icon crops alone are not full desktop+crosshair shots — seed as draft
    # reference fixtures (expected NO unless filename suggests hit).
    for p in sorted(TARGETS_DIR.glob("*.png")):
        stem = p.stem.lower()
        # Prefer readable target names from known icons
        if "doubao" in stem:
            target_name = "Doubao"
        elif "vscode" in stem or "code" in stem:
            target_name = "Visual Studio Code"
        else:
            target_name = p.stem.replace("_", " ").strip() or "target"
        case_key = f"msv_icon_{_safe_key(p.stem)}"
        out.append(
            {
                "case_key": case_key,
                "skill_key": DEFAULT_SKILL,
                "image_path": str(p.resolve()),
                "target_name": target_name,
                "target_action": "open",
                "expected": "NO",
                "expected_reason": (
                    "Target icon crop only (no crosshair context); "
                    "treat as reference/draft until labeled."
                ),
                "notes": f"Auto from {p.name}",
                "source": "seed_gold_icon",
                "status": "draft",
            }
        )
    return out


def _safe_key(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", (text or "").strip()).strip("_").lower()
    return s[:64] or "case"


# ---------------------------------------------------------------------------
# skill_prompt_case CRUD
# ---------------------------------------------------------------------------

def upsert_skill_case(
    conn: sqlite3.Connection | None = None,
    *,
    case_key: str,
    skill_key: str = DEFAULT_SKILL,
    image_path: str | Path | None = None,
    vision_ref: str | None = None,
    target_name: str | None = None,
    target_action: str | None = None,
    expected: str = "NO",
    expected_reason: str | None = None,
    notes: str | None = None,
    source: str = "manual",
    labeler: str | None = None,
    status: str = "active",
    db_path: Path | str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Insert or update one gold/regression case (UNIQUE case_key)."""
    own = False
    if conn is None:
        conn, own = _open_db(db_path)
    assert conn is not None
    try:
        ensure_skill_tables(conn)
        case_key = (case_key or "").strip()
        skill_key = (skill_key or DEFAULT_SKILL).strip() or DEFAULT_SKILL
        if not case_key:
            raise ValueError("case_key required")
        expected_u = (expected or "NO").strip().upper()
        if expected_u not in ("YES", "NO"):
            raise ValueError("expected must be YES or NO")
        status_u = (status or "active").strip().lower()
        if status_u not in ("active", "draft", "deprecated"):
            status_u = "active"
        img = str(image_path) if image_path is not None else None

        existing = conn.execute(
            "SELECT id FROM skill_prompt_case WHERE case_key = ?",
            (case_key,),
        ).fetchone()
        if existing:
            cid = int(existing[0])
            conn.execute(
                """
                UPDATE skill_prompt_case
                SET skill_key = ?, image_path = ?, vision_ref = ?,
                    target_name = ?, target_action = ?, expected = ?,
                    expected_reason = ?, notes = ?, source = ?,
                    labeler = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    skill_key,
                    img,
                    vision_ref,
                    target_name,
                    target_action,
                    expected_u,
                    expected_reason,
                    notes,
                    source,
                    labeler,
                    status_u,
                    cid,
                ),
            )
            action = "updated"
        else:
            cur = conn.execute(
                """
                INSERT INTO skill_prompt_case (
                    case_key, skill_key, image_path, vision_ref,
                    target_name, target_action, expected, expected_reason,
                    notes, source, labeler, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_key,
                    skill_key,
                    img,
                    vision_ref,
                    target_name,
                    target_action,
                    expected_u,
                    expected_reason,
                    notes,
                    source,
                    labeler,
                    status_u,
                ),
            )
            cid = int(cur.lastrowid)
            action = "inserted"
        if commit:
            conn.commit()
        row = conn.execute(
            "SELECT * FROM skill_prompt_case WHERE id = ?", (cid,)
        ).fetchone()
        out = dict(row) if row else {"id": cid, "case_key": case_key}
        out["action"] = action
        return out
    finally:
        if own:
            conn.close()


def list_skill_cases(
    conn: sqlite3.Connection | None = None,
    *,
    skill_key: str | None = DEFAULT_SKILL,
    status: str | None = "active",
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List gold cases, newest first."""
    own = False
    if conn is None:
        path = Path(db_path or DEFAULT_DB)
        if not path.is_file():
            return []
        conn, own = _open_db(path)
    assert conn is not None
    try:
        ensure_skill_tables(conn)
        sql = "SELECT * FROM skill_prompt_case WHERE 1=1"
        params: list[Any] = []
        if skill_key:
            sql += " AND skill_key = ?"
            params.append(skill_key)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def seed_gold_cases(
    db_path: Path | str | None = None,
    *,
    include_icons: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    """Seed default gold cases from screenshot (+ optional target icons)."""
    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    try:
        ensure_skill_tables(conn)
        specs = list(DEFAULT_GOLD_CASES)
        if include_icons:
            specs.extend(_discover_target_icon_cases())

        written: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for spec in specs:
            img = Path(str(spec.get("image_path") or ""))
            if not img.is_file():
                skipped.append(
                    {
                        "case_key": spec.get("case_key"),
                        "reason": f"image missing: {img}",
                    }
                )
                continue
            # Prefer relative-ish stable path string under BASE_DIR when possible
            try:
                rel = img.resolve().relative_to(BASE_DIR.resolve())
                image_path = str(rel).replace("\\", "/")
            except Exception:
                image_path = str(img)

            row = upsert_skill_case(
                conn,
                case_key=str(spec["case_key"]),
                skill_key=str(spec.get("skill_key") or DEFAULT_SKILL),
                image_path=image_path,
                target_name=spec.get("target_name"),
                target_action=spec.get("target_action") or "",
                expected=str(spec.get("expected") or "NO"),
                expected_reason=spec.get("expected_reason"),
                notes=spec.get("notes"),
                source=str(spec.get("source") or "seed_gold"),
                status=str(spec.get("status") or "active"),
                commit=False,
            )
            written.append(row)

        if commit:
            conn.commit()
        return {
            "ok": True,
            "seeded": len(written),
            "skipped": skipped,
            "cases": written,
            "screenshot": str(DEFAULT_SCREENSHOT),
            "targets_dir": str(TARGETS_DIR),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Task Center seed — Root 10 continuous items
# ---------------------------------------------------------------------------

def seed_task_center_skill_root(
    db_path: Path | str | None = None,
    *,
    version_label: str = SKILL_VERSION_LABEL,
    commit: bool = True,
) -> dict[str, Any]:
    """Idempotent seed: module mouse_spot_helper, channel local_pc, root 10 + 10.1–10.20.

    Uses ``db_schema.upsert_task_ssot`` and managed-style version_center/dev_task rows.
    Never renumbers; existing labels are updated in place.
    """
    from db_schema import upsert_task_ssot

    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    out: dict[str, Any] = {
        "ok": True,
        "root": ROOT_TASK_ID,
        "version_label": version_label,
        "module": SKILL_MODULE_CODE,
        "channel": SKILL_CHANNEL_CODE,
        "created_tasks": 0,
        "updated_tasks": 0,
        "items": [],
        "dims": 0,
        "lines": skill_tc_item_lines(ROOT_TASK_ID),
    }
    try:
        ensure_skill_tables(conn)
        try:
            from db_schema import ensure_task_center_schema

            ensure_task_center_schema(conn)
        except Exception as e:
            out["schema_warn"] = f"{type(e).__name__}: {e}"

        channel_id = _get_or_create_dim(
            conn, "channel", SKILL_CHANNEL_CODE, SKILL_CHANNEL_NAME
        )
        module_id = _get_or_create_dim(
            conn, "module", SKILL_MODULE_CODE, SKILL_MODULE_NAME
        )
        version_id = _resolve_version_id(
            conn,
            channel_id=channel_id,
            module_id=module_id,
            version_label=version_label,
        )
        out["channel_id"] = channel_id
        out["module_id"] = module_id
        out["version_id"] = version_id

        act_sys = _ensure_action(
            conn,
            element="system",
            action="skill",
            code="system.skill_prompt",
            name="Skill prompt SSOT system",
        )

        root_payload = {
            "pipeline": "skill_prompt_ssot",
            "gate": GATE_POLICY,
            "system_key": SKILL_SYSTEM_KEY,
            "skill_key": DEFAULT_SKILL,
            "root_task_id": ROOT_TASK_ID,
            "version_label": version_label,
            "module": SKILL_MODULE_CODE,
            "channel": SKILL_CHANNEL_CODE,
            "coding_rule": "{Root}.{GlobalSeq} continuous F/A/T/D/J/E",
            "items": [
                {
                    "id": format_task_id(ROOT_TASK_ID, it["seq"]),
                    "item_type": it["item_type"],
                    "name": it["name"],
                }
                for it in SKILL_TC_ITEMS
            ],
        }

        root = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, SKILL_ROOT_LABEL),
        ).fetchone()
        if root:
            root_id = int(root[0])
            conn.execute(
                """
                UPDATE dev_task
                SET title = ?, payload_json = ?, module_id = ?, channel_id = ?,
                    action_name_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    "Skill Prompt SSOT (Root 10)",
                    json.dumps(root_payload, ensure_ascii=False),
                    module_id,
                    channel_id,
                    act_sys,
                    root_id,
                ),
            )
            out["updated_tasks"] += 1
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
                    act_sys,
                    version_id,
                    SKILL_ROOT_LABEL,
                    "Skill Prompt SSOT (Root 10)",
                    json.dumps(root_payload, ensure_ascii=False),
                ),
            )
            root_id = int(cur.lastrowid)
            out["created_tasks"] += 1
        out["root_db_id"] = root_id

        root_dims = [
            (10, "system.key", SKILL_SYSTEM_KEY),
            (20, "skill.key", DEFAULT_SKILL),
            (30, "skill.prompt_key", "main"),
            (40, "skill.version", version_label),
            (50, "skill.parser", "result_yes_no"),
            (60, "skill.output_schema", "result_yes_no"),
            (70, "module.code", SKILL_MODULE_CODE),
            (80, "channel.code", SKILL_CHANNEL_CODE),
            (90, "task.root", str(ROOT_TASK_ID)),
            (100, "task.id_rule", "{Root}.{GlobalSeq}"),
            (110, "gate.policy", GATE_POLICY),
            (120, "promote.law", "draft_only_until_pass_gate"),
        ]
        for sort_order, dim_key, value_text in root_dims:
            upsert_task_ssot(
                conn,
                task_id=root_id,
                dim_key=dim_key,
                value_text=value_text,
                value_type="string",
                source=SOURCE_SEED,
                sort_order=sort_order,
                notes="skill prompt root",
                commit=False,
            )
            out["dims"] += 1

        for it in SKILL_TC_ITEMS:
            seq = int(it["seq"])
            label = format_task_id(ROOT_TASK_ID, seq)
            item_type = str(it["item_type"]).upper()
            name = str(it["name"])
            title = str(it.get("title") or name)
            el, act, code, aname = _action_code_for_item_type(item_type)
            action_id = _ensure_action(
                conn, element=el, action=act, code=code, name=aname
            )
            payload = {
                "pipeline": "skill_prompt_ssot",
                "gate": GATE_POLICY,
                "root": ROOT_TASK_ID,
                "seq": seq,
                "task_id_code": label,
                "item_type": item_type,
                "name": name,
                "skill_key": DEFAULT_SKILL,
                "parent_label": SKILL_ROOT_LABEL,
            }
            existing = conn.execute(
                "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
                (version_id, label),
            ).fetchone()
            if existing:
                tid = int(existing[0])
                conn.execute(
                    """
                    UPDATE dev_task
                    SET title = ?, payload_json = ?, parent_task_id = ?,
                        module_id = ?, channel_id = ?, action_name_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        f"{label} {title}",
                        json.dumps(payload, ensure_ascii=False),
                        root_id,
                        module_id,
                        channel_id,
                        action_id,
                        tid,
                    ),
                )
                out["updated_tasks"] += 1
                action = "updated"
            else:
                cur = conn.execute(
                    """
                    INSERT INTO dev_task
                        (parent_task_id, channel_id, module_id, action_name_id,
                         version_id, task_label, title, payload_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        root_id,
                        channel_id,
                        module_id,
                        action_id,
                        version_id,
                        label,
                        f"{label} {title}",
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                tid = int(cur.lastrowid)
                out["created_tasks"] += 1
                action = "inserted"

            child_dims = [
                (10, "item.type", item_type),
                (20, "item.name", name),
                (30, "task.id_code", label),
                (40, "task.seq", str(seq)),
                (50, "skill.key", DEFAULT_SKILL),
                (60, "skill.prompt_key", "main"),
                (70, "skill.version", version_label),
                (80, "skill.parser", "result_yes_no"),
                (90, "parent.root", SKILL_ROOT_LABEL),
            ]
            for sort_order, dim_key, value_text in child_dims:
                upsert_task_ssot(
                    conn,
                    task_id=tid,
                    dim_key=dim_key,
                    value_text=value_text,
                    value_type="string",
                    source=SOURCE_SEED,
                    sort_order=sort_order,
                    notes=f"skill tc {label}",
                    commit=False,
                )
                out["dims"] += 1

            out["items"].append(
                {
                    "task_id_code": label,
                    "db_id": tid,
                    "item_type": item_type,
                    "name": name,
                    "action": action,
                }
            )

        # Bind active skill row task_id → root when present
        try:
            active = get_active_skill(DEFAULT_SKILL, conn=conn)
            if active and active.get("id"):
                conn.execute(
                    """
                    UPDATE skill_prompt_ssot
                    SET task_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (root_id, int(active["id"])),
                )
                out["bound_skill_ssot_id"] = int(active["id"])
        except Exception:
            pass

        if commit:
            conn.commit()
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Improve → draft only (never auto-promote)
# ---------------------------------------------------------------------------

def improve_prompt_to_draft(
    *,
    skill_key: str = DEFAULT_SKILL,
    prompt_text: str | None = None,
    version_label: str | None = None,
    model: str | None = None,
    notes: str | None = None,
    db_path: Path | str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Improve prompt via text LLM and save as **draft** only (no activate).

    - Loads active prompt when ``prompt_text`` omitted.
    - Writes new version_label (default ``draft_YYYYmmdd_HHMMSS`` or caller).
    - status forced to ``draft``; activate=False always.
    """
    from vision_analyze import complete_text

    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    try:
        ensure_skill_tables(conn)
        active = get_active_skill(skill_key, conn=conn)
        base_prompt = (prompt_text if prompt_text is not None else active.get("prompt_text")) or MOUSE_SPOT_V1_PROMPT
        parent_version = str(active.get("version_label") or DEFAULT_VERSION)
        draft_label = (version_label or "").strip() or (
            f"draft_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        model_id = (model or DEFAULT_IMPROVE_MODEL).strip() or DEFAULT_IMPROVE_MODEL

        system = (
            "You improve vision-LLM prompts for Mouse Spot Helper icon hit-testing. "
            "Keep strict PASS/FAIL rules. Preserve {{target_name}} and {{target_action}} "
            "placeholders exactly. Keep output format:\n"
            "Result: [YES / NO]\n"
            "Reason: 1 short sentence...\n"
            "Return ONLY the improved prompt text. No markdown fences. No commentary."
        )
        user_msg = (
            "Improve the following skill prompt for clarity and stricter grounding. "
            "Do not weaken the hard rules about red crosshair vs blue circle.\n\n"
            f"--- ORIGINAL PROMPT ---\n{base_prompt}\n--- END ---"
        )
        result = complete_text(
            user_msg, model=model_id, system=system, format_json=False
        )
        if result.error:
            return {
                "ok": False,
                "error": result.error,
                "reason": result.summary,
                "model": model_id,
                "parent_version": parent_version,
            }

        improved = (result.raw_text or "").strip()
        if improved.startswith("```"):
            improved = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", improved)
            improved = re.sub(r"\n?```$", "", improved).strip()
        if not improved:
            return {
                "ok": False,
                "error": "empty_improved_prompt",
                "model": model_id,
                "parent_version": parent_version,
            }
        # Ensure placeholders survive improve
        if "{{target_name}}" not in improved and "{target_name}" not in improved:
            improved = base_prompt  # fall back rather than break render contract
            notes_merge = (notes or "") + " | improve_fallback_placeholders"
        else:
            notes_merge = notes

        row = upsert_skill_prompt(
            conn,
            skill_key=skill_key,
            version_label=draft_label,
            prompt_text=improved,
            prompt_key=str(active.get("prompt_key") or "main"),
            status="draft",
            parser=str(active.get("parser") or "result_yes_no"),
            output_schema=str(active.get("output_schema") or "result_yes_no"),
            model_default=active.get("model_default") or "qwen2.5vl:7b",
            hard_rules=active.get("hard_rules") if isinstance(active.get("hard_rules"), list) else [],
            task_id=active.get("task_id"),
            source="improve_draft",
            notes=(notes_merge or f"improved from {parent_version}; promote gated"),
            activate=False,
            commit=commit,
        )
        return {
            "ok": True,
            "draft_only": True,
            "activated": False,
            "skill_key": skill_key,
            "version_label": draft_label,
            "parent_version": parent_version,
            "model": model_id,
            "prompt_tokens": getattr(result, "prompt_tokens", None),
            "completion_tokens": getattr(result, "completion_tokens", None),
            "duration_ms": getattr(result, "duration_ms", None),
            "row": row,
            "prompt_preview": improved[:400],
            "law": "improve saves draft only; promote requires pass_gate",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Gold suite runner
# ---------------------------------------------------------------------------

def test_gold_suite(
    *,
    skill_key: str = DEFAULT_SKILL,
    version_label: str | None = None,
    runs_per_case: int = 1,
    status: str = "active",
    model: str | None = None,
    db_path: Path | str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run proof harness across all gold cases (correctness suite).

    Each case uses its own image/target/expected. ``runs_per_case`` defaults to 1
    for suite speed; use 100 for stability on a single case via ``test_prompt_runs``.
    """
    cases = list_skill_cases(
        skill_key=skill_key, status=status, db_path=db_path
    )
    if not cases:
        # try seed once if empty
        seed_gold_cases(db_path=db_path, commit=True)
        cases = list_skill_cases(
            skill_key=skill_key, status=status, db_path=db_path
        )

    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    errors = 0
    for case in cases:
        img = case.get("image_path") or DEFAULT_SCREENSHOT
        img_path = Path(str(img))
        if not img_path.is_file():
            # allow paths relative to BASE_DIR
            alt = BASE_DIR / str(img)
            img_path = alt if alt.is_file() else img_path
        if not img_path.is_file():
            errors += 1
            results.append(
                {
                    "ok": False,
                    "case_key": case.get("case_key"),
                    "error": f"image missing: {img}",
                }
            )
            continue
        try:
            run = test_prompt_runs(
                skill_key=skill_key,
                version_label=version_label,
                image_path=img_path,
                target_name=str(case.get("target_name") or "target"),
                target_action=str(case.get("target_action") or ""),
                expected=str(case.get("expected") or "NO"),
                runs=max(1, int(runs_per_case)),
                model=model,
                db_path=db_path,
                persist=persist,
            )
            run["case_key"] = case.get("case_key")
            run["case_id"] = case.get("id")
            if run.get("pass_gate"):
                passed += 1
            else:
                failed += 1
            results.append(run)
        except Exception as e:
            errors += 1
            results.append(
                {
                    "ok": False,
                    "case_key": case.get("case_key"),
                    "error": f"{type(e).__name__}: {e}",
                }
            )

    total = len(cases)
    return {
        "ok": errors == 0 and failed == 0 and total > 0,
        "skill_key": skill_key,
        "version_label": version_label,
        "runs_per_case": runs_per_case,
        "cases_total": total,
        "cases_passed": passed,
        "cases_failed": failed,
        "cases_errors": errors,
        "results": results,
        "started_at": _utc_now(),
        "note": "100-run = stability on one image; gold suite = correctness across cases",
    }


# ---------------------------------------------------------------------------
# Convenience: seed all Phase 5 artifacts
# ---------------------------------------------------------------------------

def seed_phase5_all(
    db_path: Path | str | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Seed skill prompts (if needed) + gold cases + Task Center root 10."""
    from skill_prompt import seed_default_skills

    skill = seed_default_skills(db_path, commit=commit)
    gold = seed_gold_cases(db_path, commit=commit)
    tc = seed_task_center_skill_root(db_path, commit=commit)
    return {
        "ok": bool(skill.get("ok") and gold.get("ok") and tc.get("ok")),
        "skill": skill,
        "gold": gold,
        "task_center": tc,
        "lines": tc.get("lines") or skill_tc_item_lines(),
    }


__all__ = [
    "BASE_DIR",
    "ROOT_TASK_ID",
    "SKILL_TC_ITEMS",
    "SKILL_MODULE_CODE",
    "SKILL_VERSION_LABEL",
    "DEFAULT_GOLD_CASES",
    "format_task_id",
    "skill_tc_item_lines",
    "list_skill_cases",
    "upsert_skill_case",
    "seed_gold_cases",
    "seed_task_center_skill_root",
    "improve_prompt_to_draft",
    "test_gold_suite",
    "seed_phase5_all",
]


if __name__ == "__main__":
    import argparse
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Skill Prompt Phase 5 extensions")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed-tc", help="Seed Task Center root 10 + 10.1-10.20")
    sub.add_parser("seed-gold", help="Seed gold cases")
    sub.add_parser("seed-all", help="Seed skill + gold + task center")
    sub.add_parser("list-cases", help="List gold cases")
    sub.add_parser("lines", help="Print task id lines")

    pt = sub.add_parser("test-gold", help="Run gold suite")
    pt.add_argument("--runs", type=int, default=1)
    pt.add_argument("--version", default=None)
    pt.add_argument("--skill", default=DEFAULT_SKILL)

    pi = sub.add_parser("improve-draft", help="Improve active prompt → draft only")
    pi.add_argument("--skill", default=DEFAULT_SKILL)
    pi.add_argument("--version", default=None)
    pi.add_argument("--model", default=None)

    for sp in (sub.choices[k] for k in list(sub.choices)):
        if hasattr(sp, "add_argument"):
            try:
                sp.add_argument("--db", default=str(DEFAULT_DB))
            except argparse.ArgumentError:
                pass

    args = p.parse_args()
    db = getattr(args, "db", None) or str(DEFAULT_DB)

    if args.cmd == "lines":
        print("\n".join(skill_tc_item_lines()))
        raise SystemExit(0)
    if args.cmd == "seed-tc":
        print(json.dumps(seed_task_center_skill_root(db), ensure_ascii=False, indent=2, default=str))
        raise SystemExit(0)
    if args.cmd == "seed-gold":
        print(json.dumps(seed_gold_cases(db), ensure_ascii=False, indent=2, default=str))
        raise SystemExit(0)
    if args.cmd == "seed-all":
        print(json.dumps(seed_phase5_all(db), ensure_ascii=False, indent=2, default=str))
        raise SystemExit(0)
    if args.cmd == "list-cases":
        print(json.dumps(list_skill_cases(db_path=db), ensure_ascii=False, indent=2, default=str))
        raise SystemExit(0)
    if args.cmd == "test-gold":
        print(
            json.dumps(
                test_gold_suite(
                    skill_key=args.skill,
                    version_label=args.version,
                    runs_per_case=args.runs,
                    db_path=db,
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        raise SystemExit(0)
    if args.cmd == "improve-draft":
        print(
            json.dumps(
                improve_prompt_to_draft(
                    skill_key=args.skill,
                    version_label=args.version,
                    model=args.model,
                    db_path=db,
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        raise SystemExit(0)
    raise SystemExit(1)