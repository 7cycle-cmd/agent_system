"""llm_model_registry.py -- the ADD path for `llm_model`.

THE USER (2026-09-25): "same function for step 2, + button to + LLM".

MEASURED (db_schema.py:5244): the 4 live `llm_model` rows were inserted BY
HAND. There was a SEED (`LLM_MODEL_SEED`) but NO registration path at all --
a popup could not add a model, so the register could only grow by editing the
database directly. This module is that path.

THE RULES (the same shape as `working_environment.declare`):
  * `name` and `model_id` are REQUIRED and UNIQUE (the table enforces both)
  * `model_id` is the STABLE EXTERNAL NAME (e.g. `deepseek/deepseek-v4-
    flash-0731`); `name` is the human label. Same rule as
    `setting_ref_key_not_id`.
  * `cite_ref` is REQUIRED -- no citation, no model.
  * a REFUSAL is RAISED, not returned as a soft failure: a caller that
    ignores a soft failure would believe the model exists when it does not.

NEVER RAISES ON A READ
----------------------
`list_models()` returns `ok: False` with a `why` rather than raising, because
a status read that can raise turns a page into an outage.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "agent.db"

TABLE = "llm_model"
SOURCE = "llm_model_registry:llm_model"


class ModelRefused(Exception):
    """Raised when a model would be stored without a real name/cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("llm_model refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent."""
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `llm_model` if absent. Idempotent."""
    import db_schema

    conn.executescript(db_schema.LLM_MODEL_DDL)
    conn.commit()
    return {"ok": True, "table": TABLE}


def add_model(conn: sqlite3.Connection, *, name: str, model_id: str,
              local: bool = False, visual: bool = False,
              text: bool = True, description: str = "",
              cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """Add ONE model. REFUSES an empty or duplicate name / model_id.

    A REFUSAL is raised, not returned as a soft failure: a caller that ignores
    a soft failure would believe the model exists when it does not.
    """
    _as_rows(conn)
    nm = str(name or "").strip()
    mid = str(model_id or "").strip()
    reasons: list[str] = []
    if not nm:
        reasons.append("name is required")
    if not mid:
        reasons.append("model_id is required (the stable external name)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no model)")
    if nm:
        dup = conn.execute(
            "SELECT id FROM %s WHERE name=?" % TABLE, (nm,)).fetchone()
        if dup is not None:
            reasons.append("name %r already exists (id=%s)"
                           % (nm, dup["id"]))
    if mid:
        dup = conn.execute(
            "SELECT id FROM %s WHERE model_id=?" % TABLE, (mid,)).fetchone()
        if dup is not None:
            reasons.append("model_id %r already exists (id=%s)"
                           % (mid, dup["id"]))
    if reasons:
        raise ModelRefused(reasons)

    cur = conn.execute(
        "INSERT INTO %s (name, model_id, visual, text, local, description) "
        "VALUES (?, ?, ?, ?, ?, ?)" % TABLE,
        (nm, mid, 1 if visual else 0, 1 if text else 0,
         1 if local else 0, str(description or "")))
    if commit:
        conn.commit()
    return {"ok": True, "action": "created", "id": int(cur.lastrowid),
            "name": nm, "model_id": mid,
            "local": 1 if local else 0}


def update_model(conn: sqlite3.Connection, *, llm_id: int, name: str,
                 model_id: str, local: bool = False, visual: bool = False,
                 text: bool = True, description: str = "",
                 cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """EDIT an existing model. The user (2026-09-25): "same function for
    step 2" -- the step-2 popup edits the row, the same way the step-1 popup
    edits its environment.

    REFUSES (raises `ModelRefused`): an unknown `llm_id`, an empty `name` /
    `model_id`, a duplicate `name` / `model_id` on ANOTHER row, or a missing
    `cite_ref`.
    """
    _as_rows(conn)
    nm = str(name or "").strip()
    mid = str(model_id or "").strip()
    reasons: list[str] = []
    if not nm:
        reasons.append("name is required")
    if not mid:
        reasons.append("model_id is required (the stable external name)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no edit)")
    try:
        lid = int(llm_id)
    except Exception:
        reasons.append("llm_id must be an integer, got %r" % (llm_id,))
        lid = None
    row = None
    if lid is not None:
        row = conn.execute(
            "SELECT id, name, model_id FROM %s WHERE id=? AND is_active=1"
            % TABLE, (lid,)).fetchone()
        if row is None:
            reasons.append("llm_id %s is not in `%s`" % (lid, TABLE))
    if nm:
        dup = conn.execute(
            "SELECT id FROM %s WHERE name=? AND id!=?" % TABLE,
            (nm, lid or -1)).fetchone()
        if dup is not None:
            reasons.append("name %r already exists (id=%s)"
                           % (nm, dup["id"]))
    if mid:
        dup = conn.execute(
            "SELECT id FROM %s WHERE model_id=? AND id!=?" % TABLE,
            (mid, lid or -1)).fetchone()
        if dup is not None:
            reasons.append("model_id %r already exists (id=%s)"
                           % (mid, dup["id"]))
    if reasons:
        raise ModelRefused(reasons)

    conn.execute(
        "UPDATE %s SET name=?, model_id=?, visual=?, text=?, local=?, "
        "description=?, updated_at=datetime('now') WHERE id=?" % TABLE,
        (nm, mid, 1 if visual else 0, 1 if text else 0,
         1 if local else 0, str(description or ""), lid))
    if commit:
        conn.commit()
    return {"ok": True, "action": "updated", "id": lid, "name": nm,
            "model_id": mid, "local": 1 if local else 0}


def list_models(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every active model, READ from the table. Never raises."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, name, model_id, visual, text, local, description "
            "FROM %s WHERE is_active=1 ORDER BY id" % TABLE)]
    except Exception as exc:
        return {"ok": False, "models": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "models": rows, "count": len(rows),
            "source": SOURCE}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--add" in args:
            i = args.index("--add")
            out = add_model(
                conn, name=args[i + 1], model_id=args[i + 2],
                cite_ref=args[i + 3] if len(args) > i + 3
                else "cli:llm_model_registry")
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(list_models(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
