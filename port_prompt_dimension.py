"""Port prompt_dimension -> wording_registry (the capability must not be lost).

The retired `prompt_dimension.py` held 10 values across 4 dimensions for skill
`mouse_spot_verify` (context 2 x criterion 3 x negation 3 x output 2 = 36
combinations). This moves that DATA into `wording_registry` so the same
capability is driven by the registers instead of a bespoke table.

Idempotent: re-running updates the template text rather than duplicating.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# The COMPONENT the old dimensions belonged to. It was never in `skill_registry`,
# so it is created here — otherwise the ported wording would have no parent.
#
# POST-SPLIT (2026-09-21): this parent is a `component_registry` row, not a
# `skill_registry` row. `wording_registry.skill_id` FKs to component_registry, and
# `mouse_spot_verify` is measured to be the ONE key present in BOTH populations
# (it is a skill AND a composition target) — which is exactly the case
# `component_registry.skill_ref` exists to link BY ID.
PORT_SKILL_KEY = "mouse_spot_verify"
PORT_SKILL_NAME = "Mouse spot verify"
PORT_SKILL_DESC = (
    "You are shown a screenshot with a red crosshair (+). The crosshair marks "
    "where the mouse was captured."
)
PORT_SKILL_OUTPUT = (
    "Answer in this exact format:\nResult: [YES / NO]\n"
    "Reason: one short sentence naming what the crosshair is on."
)


def port(db_path: Path | str | None = None) -> dict:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    out: dict = {"db": str(path), "skill": None, "ported": [], "skipped": []}

    # 1. ensure the parent COMPONENT exists
    row = conn.execute(
        "SELECT skill_id FROM component_registry WHERE skill_key = ?", (PORT_SKILL_KEY,)
    ).fetchone()
    if row:
        skill_id = int(row["skill_id"])
        out["skill"] = f"{PORT_SKILL_KEY} (existing skill_id={skill_id})"
    else:
        cur = conn.execute(
            "INSERT INTO component_registry (skill_key, name, description, "
            "output_schema, parser) VALUES (?, ?, ?, ?, ?)",
            (PORT_SKILL_KEY, PORT_SKILL_NAME, PORT_SKILL_DESC, PORT_SKILL_OUTPUT, "result_yes_no"),
        )
        skill_id = int(cur.lastrowid)
        out["skill"] = f"{PORT_SKILL_KEY} (created skill_id={skill_id})"

    # 2. read the old dimension values
    try:
        old = conn.execute(
            "SELECT dim_key, value_key, value_text, sort_order FROM prompt_dimension "
            "WHERE skill_key = ? ORDER BY dim_key, sort_order",
            (PORT_SKILL_KEY,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        conn.close()
        return {"error": f"prompt_dimension not readable: {e}"}

    if not old:
        conn.close()
        return {"error": "prompt_dimension has no rows for " + PORT_SKILL_KEY}

    # 3. upsert each value into wording_registry
    for r in old:
        dim = r["dim_key"]
        val = r["value_key"]
        text = r["value_text"] or ""
        existing = conn.execute(
            "SELECT wording_id FROM wording_registry "
            "WHERE skill_id = ? AND dim_key = ? AND wording_key = ?",
            (skill_id, dim, val),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE wording_registry SET template = ?, sort_order = ?, "
                "updated_at = datetime('now') WHERE wording_id = ?",
                (text, int(r["sort_order"]), int(existing["wording_id"])),
            )
            out["skipped"].append(f"{dim}={val} (updated)")
        else:
            conn.execute(
                "INSERT INTO wording_registry "
                "(wording_key, name, skill_id, dim_key, template, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (val, f"{dim}: {val}", skill_id, dim, text, int(r["sort_order"])),
            )
            out["ported"].append(f"{dim}={val}")

    conn.commit()

    # 4. report the combination count so the capability is PROVEN, not assumed
    dims = conn.execute(
        "SELECT dim_key, COUNT(*) n FROM wording_registry "
        "WHERE skill_id = ? AND is_active = 1 GROUP BY dim_key ORDER BY dim_key",
        (skill_id,),
    ).fetchall()
    total = 1
    for d in dims:
        total *= int(d["n"])
    out["dimensions"] = {d["dim_key"]: int(d["n"]) for d in dims}
    out["combinations"] = total
    conn.close()
    return out


def main() -> int:
    print(json.dumps(port(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
