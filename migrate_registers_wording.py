"""Migrate the registers to the wording_registry shape.

BEFORE: prompt_registry.wording was a single TEXT column.
AFTER:  wording lives in `wording_registry` (one row per dimension VALUE), and
        `prompt_wording` is the junction that says which value a prompt picked
        per dimension.

WHY: a prompt is a COMBINATION (context + criterion + negation + output), not
one blob of text. A single TEXT column cannot express that, so the 36-combination
capability from the retired prompt_dimension.py could not be stored.

Idempotent: safe to run repeatedly.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"


def migrate(db_path: Path | str | None = None) -> dict:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    out: dict = {"db": str(path), "actions": []}

    # 1. create the new tables
    # 🔴 `wording_registry_DDL` does not exist; the real name is
    # `WORDING_registry_DDL` (`db_schema.py`). MEASURED 2026-09-29.
    from db_schema import PROMPT_WORDING_DDL, WORDING_registry_DDL

    conn.executescript(WORDING_registry_DDL)
    conn.executescript(PROMPT_WORDING_DDL)
    out["actions"].append("ensured wording_registry + prompt_wording")

    # 2. drop the dead `wording` TEXT column from prompt_registry
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(prompt_registry)")}
    if "wording" in cols:
        # A column with a UNIQUE/FK cannot be dropped directly; `wording` has
        # neither, so a plain DROP COLUMN is safe here.
        conn.execute("ALTER TABLE prompt_registry DROP COLUMN wording")
        out["actions"].append("dropped prompt_registry.wording (dead TEXT column)")
    else:
        out["actions"].append("prompt_registry.wording already gone")

    conn.commit()
    conn.close()
    return out


def main() -> int:
    import json

    print(json.dumps(migrate(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
