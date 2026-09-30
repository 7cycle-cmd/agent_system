# -*- coding: utf-8 -*-
"""skill_taxonomy_evidence.py — derive a skill's `taxonomy_path` from EVIDENCE.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
    "45 個 skill `taxonomy_path` —— 冇可推導來源，要人手"
    "要人手??? no lagic generator can help with evidence to get answer / do it now"
    "task ID -> coding update = YES -> entity_id / you found that finally, good"

I WAS WRONG. I had said "no derivable source" after measuring only that
`skill_registry` declares no FK — I never followed the `.skill.md`
frontmatter's `task_id`. That was my blind spot, not missing data.

THE EVIDENCE CHAIN (the user's derivation, MEASURED and confirmed)
------------------------------------------------------------------
    skill_key
      -> `.skill.md` frontmatter `task_id`     (SKILL.EVIDENCE.CLASSIFY, DB.FIELD.PHONE, ...)
      -> `dev_task.task_label` = that task_id  (39 of 45 resolve)
      -> `dev_task.module_id`
      -> the module the task was filed under
      -> `module_registry.module_key`          (the REGISTRY, not the legacy table)
    => taxonomy_path = "module/<module_key>"

This is an EVIDENCE chain, not a guess: every hop is a stored row, and the
`cite_ref` it returns is the chain itself, so the answer is CHECKABLE.

WHAT IT REFUSES
---------------
A skill whose chain does not resolve is REFUSED with the BROKEN HOP NAMED —
"no .skill.md file" / "no task_id in the frontmatter" / "no dev_task row for
task_id X" / "module Y is not in module_registry". A refusal that names the hop
is actionable; a blanket "cannot derive" is a wall.

THE LEGACY `module` TABLE (a real defect, measured)
---------------------------------------------------
`dev_task.module_id` is a DANGLING FK: it points at the LEGACY `module` table
(13 rows: `agent_db`, `code_health`, `membership`, ...), NOT `module_registry`
(1,2,3,15115). So the chain has TWO module sources and they DISAGREE. This
module resolves the legacy `code` to a `module_registry.module_key` through an
explicit map, and REPORTS a code it cannot map rather than silently accepting the
legacy table.

Run:
    .\\.venv\\Scripts\\python.exe skill_taxonomy_evidence.py --measure
    .\\.venv\\Scripts\\python.exe skill_taxonomy_evidence.py            # dry run
    .\\.venv\\Scripts\\python.exe skill_taxonomy_evidence.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
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
PACK_DIR = BASE / ".github" / "skills"

# THE LEGACY -> REGISTRY MODULE MAP, and it is DELIBERATELY EXPLICIT.
#
# `dev_task.module_id` references the legacy `module` table (13 rows), while the
# graph uses `module_registry` (4 rows). MEASURED: `task_center` is id 13 in the
# legacy table and module_id 1 in the registry, so the SAME name appears in both
# and the ids disagree. A silent "join on the name" would hide a missing row; an
# explicit map makes an unmapped code a REPORTED fact.
LEGACY_MODULE_TO_REGISTRY: dict[str, str] = {
    "task_center": "task_center",
    "mouse_spot_helper": "mouse_spot_helper",
    "openclaw_companion": "openclaw_companion",
    "ollama": "llm_runtime",            # the LLM runtime, renamed in the registry
    # `openclaw_gateway` WAS mapped here to `openclaw_companion` and that entry was
    # REMOVED (2026-09-24) because it had NO EVIDENCE. Measured: `openclaw_gateway`
    # exists ONLY as `module.code` — it is not an `app.app_key`, not an
    # `onto_binding.bind_key`, not a `module_registry.module_key`, not an
    # `onto_concept.title`, and not a `code_registry.system_key`. The app is
    # `app_key='openclaw'` (kind='desktop'), and NO row anywhere links the two.
    # A name similarity is not a citation, and an unsupported mapping is worse than
    # an unmapped code: it makes a refusal that should happen silently NOT happen.
    # Every other legacy code has NO counterap in `module_registry`, so a task filed
    # under one is REFUSED with the code named rather than silently resolved.
}

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---", re.S)
_TASK_ID_RE = re.compile(r'^task_id\s*:\s*"?([^"\n]+?)"?\s*$', re.M)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def skill_md(skill_key: str) -> Path | None:
    """The skill's canonical file, or None. Searched by NAME, not by a stored path.

    MEASURED 2026-09-24 — a skill has FIVE file forms and I first searched only
    one, so my refusal said "no .skill.md file" about skills that HAVE a file:
        1. skills/<n>/<key>/SKILL.md          (the pack form)
        2. skills/<n>/<key>.skill.md          (flat, frontmatter with task_id)
        3. skills/<key>.skill.md
        4. skills_generated/<key>.skill.md    (GENERATED from the register)
        5. .github/skills/<kebab-key>/SKILL.md
    A "no file" refusal about form 4 was a FALSE RED. This returns the CANONICAL
    file (forms 1/2/3/5); form 4 is reported separately by `generated_md` because
    a file generated FROM the register cannot be the evidence FOR the register.
    """
    if not SKILLS_DIR.is_dir():
        return None
    hits: list[Path] = []
    hits += sorted(SKILLS_DIR.rglob("%s.skill.md" % skill_key))
    hits += sorted(SKILLS_DIR.rglob("%s/SKILL.md" % skill_key))
    hits += sorted(SKILLS_DIR.rglob("%s/SKILL.md" % skill_key.replace("_", "-")))
    if PACK_DIR.is_dir():
        hits += sorted(PACK_DIR.rglob("%s/SKILL.md" % skill_key))
        hits += sorted(PACK_DIR.rglob("%s/SKILL.md" % skill_key.replace("_", "-")))
    return hits[0] if hits else None


def generated_md(skill_key: str) -> Path | None:
    """A file GENERATED from the register (`skills_generated/`), or None.

    It exists, so "no file" would be false — but it carries NO frontmatter
    (`<!-- GENERATED FILE -->`, no `task_id`), so it cannot supply the task
    evidence either. Naming it is what makes the refusal honest.
    """
    g = BASE / "skills_generated"
    if not g.is_dir():
        return None
    hits = sorted(g.rglob("%s.skill.md" % skill_key))
    return hits[0] if hits else None


def frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    m = _FM_RE.match(text)
    if not m:
        return {}
    return dict(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", m.group(1),
                           re.M))


def task_id_of(skill_key: str) -> tuple[str | None, str | None, str | None]:
    """The skill's `task_id`, its file, and the REFUSAL CODE if absent.

    `(task_id, why, code)`. The code is per-REASON, not one blanket code, because
    "no file at all" and "a file with no task_id" and "a generated file" need three
    different fixes — collapsing them to `NO_TASK_ID` makes a refusal that names
    the wrong hop.
    """
    p = skill_md(skill_key)
    if p is not None:
        d = frontmatter(p)
        tid = str(d.get("task_id") or "").strip().strip('"').strip("'")
        if tid:
            return tid, None, None
        return None, ("the canonical file %s exists but has NO `task_id` in its "
                      "frontmatter" % p.relative_to(BASE).as_posix()), \
            "NO_TASK_ID_IN_FRONTMATTER"
    gen = generated_md(skill_key)
    if gen is not None:
        return None, ("only a GENERATED file exists (%s): it is produced FROM the "
                      "register, carries no frontmatter/task_id, and cannot be the "
                      "evidence FOR the register — derive from the skill's task"
                      % gen.relative_to(BASE).as_posix()), "ONLY_GENERATED_FILE"
    return None, "no skill file exists in any known form for this skill_key", \
        "NO_SKILL_FILE"


def resolve_module(conn: sqlite3.Connection, legacy_code: str) -> tuple[str | None, str]:
    """A legacy `module.code` -> the `module_registry.module_key`. (None, why) else."""
    key = LEGACY_MODULE_TO_REGISTRY.get(str(legacy_code or "").strip())
    if not key:
        return None, ("legacy module code %r has NO mapping to module_registry "
                      "(the legacy `module` table is a different population)"
                      % legacy_code)
    row = conn.execute("SELECT module_key FROM module_registry WHERE module_key = ?",
                       (key,)).fetchone()
    if not row:
        return None, ("mapped key %r is not in module_registry" % key)
    return str(row["module_key"]), ""


def evidence_for(conn: sqlite3.Connection, skill_key: str) -> dict[str, Any]:
    """The EVIDENCE for a skill's taxonomy_path. REFUSES with the broken hop named."""
    chain: list[str] = []
    tid, why, code = task_id_of(skill_key)
    if tid is None:
        return {"ok": False, "skill_key": skill_key, "code": code or "NO_TASK_ID",
                "why": why, "chain": chain}
    chain.append("frontmatter task_id=%s" % tid)

    if not _table_exists(conn, "dev_task"):
        return {"ok": False, "skill_key": skill_key, "code": "NO_DEV_TASK_TABLE",
                "why": "dev_task is absent", "chain": chain}
    # `task_label` is the task's human label; `id` is the AUTO-INCREMENT task id.
    # THE RULING: the task id IS the auto-increment id, and `task_label` is how a
    # legacy tracking label still REACHES it.
    rows = [dict(r) for r in conn.execute(
        "SELECT id, task_label, module_id FROM dev_task WHERE task_label = ?",
        (tid,))]
    if not rows:
        return {"ok": False, "skill_key": skill_key, "code": "NO_DEV_TASK_ROW",
                "why": ("no dev_task row has task_label=%r, so the task id that "
                        "would connect this skill to a chat/workflow does not "
                        "exist" % tid), "chain": chain}
    if len(rows) > 1:
        return {"ok": False, "skill_key": skill_key, "code": "AMBIGUOUS_DEV_TASK",
                "why": ("%d dev_task rows share task_label=%r, so the module is "
                        "not determined" % (len(rows), tid)), "chain": chain}
    task = rows[0]
    chain.append("dev_task.id=%s task_label=%s" % (task["id"], task["task_label"]))

    mid = task.get("module_id")
    if mid is None:
        return {"ok": False, "skill_key": skill_key, "code": "NO_MODULE_ON_TASK",
                "why": "dev_task.module_id is NULL", "chain": chain}
    if not _table_exists(conn, "module"):
        return {"ok": False, "skill_key": skill_key, "code": "NO_LEGACY_MODULE_TABLE",
                "why": "the legacy `module` table is absent", "chain": chain}
    m = conn.execute("SELECT code FROM module WHERE id = ?", (int(mid),)).fetchone()
    if not m:
        return {"ok": False, "skill_key": skill_key, "code": "NO_LEGACY_MODULE_ROW",
                "why": "no legacy `module` row has id=%s" % mid, "chain": chain}
    code = str(m["code"])
    chain.append("legacy module code=%s" % code)

    reg_key, why2 = resolve_module(conn, code)
    if reg_key is None:
        return {"ok": False, "skill_key": skill_key, "code": "MODULE_NOT_IN_REGISTRY",
                "why": why2, "chain": chain}
    chain.append("module_registry.module_key=%s" % reg_key)

    path = "module/%s" % reg_key
    return {
        "ok": True, "skill_key": skill_key, "taxonomy_path": path,
        "task_id_label": tid, "task_id": int(task["id"]),
        "legacy_module_code": code, "module_key": reg_key,
        "chain": chain,
        # THE CITATION IS THE CHAIN. A value without its hops cannot be checked.
        "cite_ref": " -> ".join(chain),
    }


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM skill_registry").fetchone()[0]
    with_c = conn.execute(
        "SELECT COUNT(*) FROM skill_registry s WHERE EXISTS("
        "SELECT 1 FROM skill_contract_template t WHERE t.skill_key=s.skill_key)"
    ).fetchone()[0]
    derivable = 0
    refused: dict[str, int] = {}
    for r in conn.execute(
            "SELECT skill_key FROM skill_registry s WHERE NOT EXISTS("
            "SELECT 1 FROM skill_contract_template t WHERE t.skill_key=s.skill_key)"
            " ORDER BY s.skill_key"):
        ev = evidence_for(conn, str(r["skill_key"]))
        if ev["ok"]:
            derivable += 1
        else:
            refused[ev["code"]] = refused.get(ev["code"], 0) + 1
    return {"skill_total": total, "skill_with_contract": with_c,
            "contractless": total - with_c, "derivable": derivable,
            "refused_by_code": refused}


def propose(conn: sqlite3.Connection) -> dict[str, Any]:
    """What WOULD be written, and every refusal with its named reason."""
    proposed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for r in conn.execute(
            "SELECT skill_id, skill_key, taxonomy_path FROM skill_registry "
            "WHERE taxonomy_path IS NULL OR taxonomy_path = '' ORDER BY skill_id"):
        sk = str(r["skill_key"])
        ev = evidence_for(conn, sk)
        if not ev["ok"]:
            refused.append({"skill_key": sk, "code": ev["code"], "why": ev["why"],
                            "chain": ev["chain"]})
            continue
        proposed.append({"skill_id": int(r["skill_id"]), "skill_key": sk,
                         "taxonomy_path": ev["taxonomy_path"],
                         "task_id": ev["task_id"], "cite_ref": ev["cite_ref"]})
    return {"ok": True, "total_empty": len(proposed) + len(refused),
            "would_write": len(proposed), "refused": len(refused),
            "proposed": proposed, "refused_rows": refused}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    p = propose(conn)
    written = 0
    for row in p["proposed"]:
        conn.execute(
            "UPDATE skill_registry SET taxonomy_path = ?, "
            "updated_at = datetime('now') WHERE skill_id = ?",
            (row["taxonomy_path"], row["skill_id"]))
        written += 1
    conn.commit()
    return {"ok": True, "would_write": p["would_write"], "written": written,
            "refused": p["refused"], "refused_rows": p["refused_rows"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.measure:
            res = measure(conn)
        elif args.apply:
            res = apply(conn)
        else:
            res = propose(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.measure:
            print("skill_registry        : %d" % res["skill_total"])
            print("  with a contract     : %d" % res["skill_with_contract"])
            print("  contractless        : %d" % res["contractless"])
            print("  DERIVABLE by evidence: %d" % res["derivable"])
            print("  refused by code     : %s" % res["refused_by_code"])
        else:
            print("%s: would_write=%d written=%d refused=%d"
                  % ("APPLIED" if args.apply else "DRY RUN",
                     res.get("would_write"), res.get("written", 0),
                     res["refused"]))
            for r in res["refused_rows"]:
                print("  REFUSED %-40s %-24s %s"
                      % (r["skill_key"], r["code"], r["why"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
