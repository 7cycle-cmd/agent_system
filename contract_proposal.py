# -*- coding: utf-8 -*-
"""contract_proposal.py — PROPOSE a contract for a skill; never invent one.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "1"   (the 45 skills with no contract, from the graph gap list)

`skill_taxonomy_backfill.py` refuses 45 of 55 skills with
`no skill_contract_template row for this skill_key`. A skill's graph PARENT is
declared by its contract's `taxonomy_path`; with no contract there is no declared
parent, so the gap is real and NAMED.

WHY THE CONTRACTS CANNOT BE GENERATED
-------------------------------------
A contract requires `skill_key, contract_id, taxonomy_path, purpose`
(`skill_registrar.py:84`). Measured:
  * a `.skill.md` frontmatter carries `name`, `reason`, `qc_summary`, `schema`,
    `task_id` — NOT `contract_id` / `taxonomy_path` / `purpose`
    (`skill_scanner.py:172-179`).

THREE SEPARATE AXES (the user's correction, 2026-09-23)
-------------------------------------------------------
    "location is for entity / catalog is the thinking for skill library / they
     are totally different concept"

  * `catalog`       — the skill LIBRARY's shelf (core / db_schema / ui / agent /
                      qa), derived from `skills/<folder>/`. A THINKING axis for
                      FINDING a skill. (`terminology_registry:'catalog'`)
  * `entity_location` — an ENTITY's WHERE: file path + line in
                      `code_location_registry`. Points at CODE.
                      (`terminology_registry:'entity_location'`)
  * `taxonomy_path` — the ONTOLOGY LEVEL a thing IS (channel / module /
                      capability / api / function / db_table / db_field),
                      validated against the registry.

A catalog says WHICH SHELF, a location says WHERE THE CODE IS, a taxonomy_path
says WHAT THE THING IS. Deriving one from another is the mixing this module must
NOT do — so `taxonomy_path` is left BLANK, with the catalog REPORTED beside it as
context for the human, not as evidence for the level.

WHAT THIS MODULE DOES INSTEAD
-----------------------------
It renders a PROPOSAL a human can answer — the SAME pattern
`skill_capability_tag.py` uses for an unanswerable field:
  * IDENTITY columns  — written by the renderer
  * PROPOSED column   — the DERIVABLE part only (the catalog); a semantic field
                        is left BLANK with a named reason
  * ANSWER columns    — LEFT BLANK for the human

It REFUSES to overwrite a file that carries human answers, so re-rendering
cannot destroy work.

Run:
    .\\.venv\\Scripts\\python.exe contract_proposal.py --render
    .\\.venv\\Scripts\\python.exe contract_proposal.py --render --force
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = BASE / "agent.db"
SKILLS_DIR = BASE / "skills"
PROPOSAL_TSV = BASE / "contract_proposal.tsv"

# Folder name -> catalog label. COPIED from `skill_library_api.SKILL_CATALOG_FOLDERS`
# (the module that derives it), because importing that module pulls in the whole
# web API for one dict. `_proof_contract_proposal.py` asserts the two agree.
#
# THREE SEPARATE AXES (user, 2026-09-23 — "location is for entity / catalog is
# the thinking for skill library / they are totally different concept"):
#
#   catalog        a skill LIBRARY shelf (core / db_schema / ui / agent / qa) —
#                  which shelf a skill sits on FOR FINDING IT. A THINKING axis
#                  of the library. Derived from the `skills/<folder>/` path.
#                  Registered: `terminology_registry.term_key='catalog'`.
#   entity_location an ENTITY's WHERE: file path + line, in
#                  `code_location_registry`, keyed by (entity_type,
#                  entity_ref_id, version). Points at CODE.
#                  Registered: `terminology_registry.term_key='entity_location'`.
#   taxonomy_path  the ONTOLOGY LEVEL a thing IS (channel/module/capability/
#                  api/function/db_table/db_field), validated against the
#                  registry.
#
# They are NOT interchangeable: a catalog says WHICH SHELF, a location says WHERE
# THE CODE IS, a taxonomy_path says WHAT THE THING IS. Deriving a taxonomy_path
# from a catalog would be mixing two of the three.
CATALOG_FOLDERS: dict[str, str] = {
    "1_core": "core",
    "2_db_schema": "db_schema",
    "3_ui": "ui",
    "4_agent": "agent",
    "5_qa": "qa",
}

# Identity + context, written by the renderer.
ID_COLS = ("skill_key", "skill_dir", "catalog", "existing_reason")
# The DERIVABLE proposal. `taxonomy_path` is left BLANK on purpose: a catalog is
# a LOCATION, not an ontology level, so there is NO evidence for a level. A
# proposal with no evidence is a guess, and a guess written into the register is
# the defect. The column exists so a human can see WHERE to answer.
PROPOSED_COLS = ("proposed_taxonomy_path", "proposal_reason")
# The columns a human fills. Everything else is NOT the human's to edit.
ANSWER_COLS = ("answer_taxonomy_path", "answer_purpose", "answer_contract_id",
               "decided_by", "cite_ref", "decision_reason")
HEADER = "\t".join(ID_COLS + PROPOSED_COLS + ANSWER_COLS)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def catalog_of(skill_dir: str) -> str:
    """The catalog label from a `skills/<folder>/...` path, or ''.

    A CATALOG is the skill LIBRARY's thinking axis — which shelf a skill sits on
    for FINDING it (core / db_schema / ui / agent / qa). It is NOT a location
    (that is an ENTITY's file+line) and NOT a taxonomy_path (that is the ontology
    level a thing IS). Three separate axes; this function answers only the first.
    """
    parts = Path(str(skill_dir or "")).parts
    for p in parts:
        if p in CATALOG_FOLDERS:
            return CATALOG_FOLDERS[p]
    return ""


def _skill_dirs() -> dict[str, Path]:
    """skill_key (the dir name, or the frontmatter `name`) -> its directory."""
    out: dict[str, Path] = {}
    if not SKILLS_DIR.is_dir():
        return out
    for p in sorted(SKILLS_DIR.rglob("*.skill.md")):
        key = p.name.replace(".skill.md", "")
        out[key] = p.parent
    return out


def _frontmatter_reason(skill_md: Path) -> str:
    """The `reason:` line from a `.skill.md` frontmatter, or ''."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")[:2000]
    except Exception:
        return ""
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("reason:"):
            return s.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def propose(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """One row per skill with NO contract. The derivable part is derived; the
    semantic part is BLANK with a named reason. Writes NOTHING."""
    rows: list[dict[str, str]] = []
    if not _table_exists(conn, "skill_registry"):
        return rows
    dirs = _skill_dirs()
    for r in conn.execute(
            "SELECT skill_id, skill_key FROM skill_registry ORDER BY skill_key"):
        sk = str(r["skill_key"])
        has = conn.execute(
            "SELECT 1 FROM skill_contract_template WHERE skill_key = ?",
            (sk,)).fetchone()
        if has:
            continue
        d = dirs.get(sk)
        rel = (d.relative_to(BASE).as_posix() if d else "")
        cat = catalog_of(rel)
        reason = ""
        if d:
            md = d / (sk + ".skill.md")
            if not md.is_file():
                cands = list(d.glob("*.skill.md"))
                md = cands[0] if cands else md
            reason = _frontmatter_reason(md)
        rows.append({
            "skill_key": sk,
            "skill_dir": rel or "(no dir found)",
            "catalog": cat,
            # BLANK ON PURPOSE. A catalog is the LIBRARY's thinking axis (which
            # shelf); a taxonomy_path is the ONTOLOGY level (what the thing IS).
            # They are different concepts (user 2026-09-23), so a catalog gives
            # NO evidence for a level and the field is left for a human.
            "proposed_taxonomy_path": "",
            "proposal_reason": (
                "NO evidence for a taxonomy level: the catalog (%s) is the skill "
                "LIBRARY's thinking axis (which shelf), NOT the ontology level "
                "the skill IS. A human must set it."
                % (cat or "unknown")),
            "existing_reason": reason,
            "answer_taxonomy_path": "",
            "answer_purpose": "",
            "answer_contract_id": "",
            "decided_by": "",
            "cite_ref": "",
            "decision_reason": "",
        })
    return rows


def _answered(path: Path) -> bool:
    """True when the file carries a HUMAN answer (any ANSWER column non-blank)."""
    if not path.is_file():
        return False
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh, delimiter="\t")
            for row in rd:
                for c in ANSWER_COLS:
                    if str(row.get(c) or "").strip():
                        return True
    except Exception:
        return False
    return False


def render(conn: sqlite3.Connection, *, force: bool = False) -> dict[str, Any]:
    rows = propose(conn)
    if PROPOSAL_TSV.is_file() and _answered(PROPOSAL_TSV) and not force:
        return {"ok": False, "code": "REFUSED_HUMAN_ANSWERS",
                "message": ("%s carries human answers; re-rendering would "
                            "destroy them. Pass --force to overwrite."
                            % PROPOSAL_TSV.name),
                "rows": len(rows)}
    with PROPOSAL_TSV.open("w", encoding="utf-8", newline="") as fh:
        fh.write(HEADER + "\n")
        for row in rows:
            fh.write("\t".join(str(row.get(c, "")) for c in
                               ID_COLS + PROPOSED_COLS + ANSWER_COLS) + "\n")
    return {"ok": True, "rows": len(rows), "path": str(PROPOSAL_TSV),
            "header": HEADER}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.render or not args.render:
            res = render(conn, force=args.force)
            if not res.get("ok"):
                print("REFUSED: %s" % res["message"])
                return 1
            print("rendered %d row(s) to %s" % (res["rows"], res["path"]))
            print("HEADER: %s" % res["header"])
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
