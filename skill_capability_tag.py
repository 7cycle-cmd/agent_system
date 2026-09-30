# -*- coding: utf-8 -*-
"""skill_capability_tag.py — PROPOSE a capability tag from the catalog, and
report what the proposal would CHANGE.

THE PROBLEM THIS SOLVES
-----------------------
`skill_factor.applies_to` is matched against a skill's `capability_tags`. Measured
2026-09-21: **0 of 48** skills declare a tag, so `applies_to='crud'` and
`applies_to='code'` match NOTHING and **8 of the 19 factors apply to zero
skills**. The mechanism is dead.

The fix is NOT to fill the column automatically. A wrong tag SILENTLY changes
which factors apply — a factor quietly starts or stops applying, and nothing
says so. That is the exact defect family this workset removes (a rule stated but
not enforced, a match that is dead and silent). So this module PROPOSES, and a
human decides.

WHY THE CATALOG IS ONLY A PROPOSAL SOURCE
-----------------------------------------
`skill_library_api.SKILL_CATALOG_FOLDERS` derives `core` / `db_schema` / `ui` /
`agent` / `qa` from the `skills/<folder>/` path, and its comment says the folder
structure is "the authoritative catalog".

**But a catalog is a LOCATION, and `applies_to` is a CAPABILITY.** The two
vocabularies do not map. A `2_db_schema` skill may generate CRUD; a `4_agent`
skill may produce code. Location is not capability — so the catalog is used to
PROPOSE, never to decide.

THE CONSEQUENCE IS THE POINT
----------------------------
For every proposal this module lists the factors that would NEWLY APPLY. A
proposal shown without its consequence is a proposal a human cannot judge: the
whole risk is that a tag changes the applicable set, and that change is invisible
unless it is printed.

WHAT IT REFUSES
---------------
  * writing the proposal over a file that already carries human answers — the
    same guard `decision_answers.write_answers_file` uses, because re-rendering
    would destroy human work
  * proposing a tag that is not in `capability_tag_registry` — an undefined tag
    matches zero skills silently, so it is refused at the source
  * writing to `skill_registry` AT ALL. This module is read-only against the
    register; the write path is `skill_capability_tag_submit.py`, behind the
    human gate.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import skill_factor as sf  # noqa: E402

DB = BASE / "agent.db"
SKILLS_DIR = BASE / "skills"

# The proposal file. Same shape as `decision_answers.tsv`: identity columns are
# written by the renderer, the answer columns are LEFT BLANK for a human.
PROPOSAL_TSV = BASE / "capability_tag_proposal.tsv"

# Identity + context, written by the renderer.
#
# `proposal_reason` is the RULE's reason, written by the renderer — it is NOT
# the human's. The human's reason is `decision_reason`, an ANSWER column, and it
# is MANDATORY when the answer differs from the proposal (gate G7). The two were
# one column named `reason` until it was measured that the audit trail could not
# answer "why did the human reject this" — the renderer's reason had been
# mistaken for the human's.
ID_COLS = ("skill_key", "catalog", "current_tags", "proposed_tag",
           "factors_unlocked", "proposal_reason")
# The columns a human fills. Everything else is NOT the human's to edit.
ANSWER_COLS = ("answer", "decided_by", "cite_ref", "decision_reason")
HEADER = "\t".join(ID_COLS + ANSWER_COLS)

# Catalog label -> proposed tag. ONLY these two have evidence:
#   db_schema — the field/table register skills, closest to CRUD generation
#   agent     — `skill_worker_code_builder` lives here, which produces code
# Everything else proposes NA, because there is NO evidence. A proposal with no
# evidence is a guess, and a guess written into the register is the defect.
CATALOG_TAG_PROPOSAL: dict[str, str] = {
    "db_schema": "crud",
    "agent": "code",
}

# Folder name -> catalog label. COPIED from `skill_library_api.SKILL_CATALOG_FOLDERS`
# rather than imported, because importing that module pulls in the whole web API
# (Flask routes, DB handles) for one dict. The copy is asserted against the
# original by `_proof_capability_tag.py`, so the two cannot drift silently.
CATALOG_FOLDERS: dict[str, str] = {
    "1_core": "core",
    "2_db_schema": "db_schema",
    "3_ui": "ui",
    "4_agent": "agent",
    "5_qa": "qa",
}


class ProposalRefused(RuntimeError):
    """Raised when a proposal would be written over human work."""


def derive_catalog(skill_path: Path | str) -> str:
    """The catalog label from a `skills/<folder>/...` path, or '' if none.

    Derived from the PATH, not from a frontmatter field: the folder structure is
    what `sync_skill_library` treats as authoritative, and a frontmatter
    `catalog_id` may point at an unrelated sample catalog.
    """
    parts = Path(str(skill_path)).parts
    for part in parts:
        if part in CATALOG_FOLDERS:
            return CATALOG_FOLDERS[part]
    return ""


def skill_files() -> dict[str, Path]:
    """{skill_key: path} for every `.skill.md`, keyed by the FILE STEM.

    The stem is the key `skill_factor.drift_report` already uses, so the two
    modules agree on what a file is called.
    """
    out: dict[str, Path] = {}
    if not SKILLS_DIR.is_dir():
        return out
    for p in sorted(SKILLS_DIR.rglob("*.skill.md")):
        out[p.name[: -len(".skill.md")]] = p
    return out


def _applicable_with(conn: sqlite3.Connection, tags: set[str]) -> set[str]:
    """The applicable factor keys for a HYPOTHETICAL tag set.

    Read-only: it computes the set without writing a tag, so a proposal can be
    shown with its consequence before anything is decided.
    """
    want = set(tags) | {sf.APPLIES_ALL}
    return {str(r[0]) for r in conn.execute(
        "SELECT factor_key, applies_to FROM skill_factor_registry "
        "WHERE is_active=1")
        if str(r[1]) == sf.APPLIES_ALL or str(r[1]) in want}


def current_tags(conn: sqlite3.Connection, skill_key: str) -> set[str]:
    """The tags a skill declares NOW. `NA` is not a tag."""
    row = conn.execute("SELECT capability_tags FROM skill_registry WHERE "
                       "skill_key=?", (skill_key,)).fetchone()
    if not row or not row[0]:
        return set()
    return {p.strip().lower() for p in str(row[0]).split(",")
            if p.strip() and p.strip() != sf.NA_TAG}


def propose(conn: sqlite3.Connection) -> dict[str, Any]:
    """One row per skill: the catalog, the proposed tag, and the CONSEQUENCE.

    A skill that already declares a tag is reported as `already_tagged` and gets
    NO proposal — re-proposing a decided value would invite a human to overwrite
    a decision with a guess.
    """
    sf.ensure_schema(conn)
    known = set(sf.registered_tags(conn))
    files = skill_files()
    rows: list[dict[str, Any]] = []
    for r in conn.execute("SELECT skill_key FROM skill_registry "
                          "ORDER BY skill_key"):
        key = str(r[0])
        have = current_tags(conn, key)
        path = files.get(key)
        catalog = derive_catalog(path) if path else ""
        base = _applicable_with(conn, have)

        if have:
            rows.append({
                "skill_key": key, "catalog": catalog,
                "current_tags": ",".join(sorted(have)),
                "proposed_tag": sf.NA_TAG, "factors_unlocked": "",
                "reason": "already tagged — a decided value is not re-proposed",
                "state": "already_tagged", "unlocked": [],
            })
            continue

        if not path:
            rows.append({
                "skill_key": key, "catalog": "", "current_tags": sf.NA_TAG,
                "proposed_tag": sf.NA_TAG, "factors_unlocked": "",
                "reason": "no .skill.md file, so there is no catalog to derive "
                          "from — no evidence, so no proposal",
                "state": "no_file", "unlocked": [],
            })
            continue

        tag = CATALOG_TAG_PROPOSAL.get(catalog, sf.NA_TAG)
        if tag == sf.NA_TAG:
            rows.append({
                "skill_key": key, "catalog": catalog or "(none)",
                "current_tags": sf.NA_TAG, "proposed_tag": sf.NA_TAG,
                "factors_unlocked": "",
                "reason": "catalog %r has no evidenced capability tag — "
                          "location is not capability, so nothing is proposed"
                          % (catalog or "(none)"),
                "state": "no_evidence", "unlocked": [],
            })
            continue

        # A proposal must name a DEFINED tag, or it would match zero skills
        # silently — the defect this module exists to avoid.
        if tag not in known:
            raise ProposalRefused(
                "catalog %r proposes tag %r, which is not in "
                "capability_tag_registry (known: %s). An undefined tag matches "
                "zero skills SILENTLY." % (catalog, tag, ", ".join(sorted(known))))

        unlocked = sorted(_applicable_with(conn, have | {tag}) - base)
        rows.append({
            "skill_key": key, "catalog": catalog, "current_tags": sf.NA_TAG,
            "proposed_tag": tag, "factors_unlocked": ",".join(unlocked),
            "reason": "catalog %r -> %r (proposal only; a wrong tag silently "
                      "changes the applicable set)" % (catalog, tag),
            "state": "proposed", "unlocked": unlocked,
        })
    return {"rows": rows,
            "proposed": sum(1 for r in rows if r["state"] == "proposed"),
            "already_tagged": sum(1 for r in rows if r["state"] == "already_tagged"),
            "no_evidence": sum(1 for r in rows if r["state"] == "no_evidence"),
            "no_file": sum(1 for r in rows if r["state"] == "no_file"),
            "skills_total": len(rows),
            "registered_tags": sorted(known)}


def render(conn: sqlite3.Connection) -> str:
    """The proposal file: every skill, with the three answer cells BLANK."""
    res = propose(conn)
    lines = [HEADER]
    for r in res["rows"]:
        lines.append("\t".join([
            r["skill_key"], r["catalog"], r["current_tags"], r["proposed_tag"],
            r["factors_unlocked"], r["reason"], "", "", "", "",
        ]))
    return "\n".join(lines) + "\n"


def migrate_tsv(path: Path | str = PROPOSAL_TSV) -> dict[str, Any]:
    """Rewrite an OLD proposal file to the new header, PRESERVING every answer.

    The old header had one `reason` column (the renderer's) and three answer
    columns. The new header renames it `proposal_reason` and adds
    `decision_reason` as a fourth answer column.

    It does NOT invent a reason: the new column is appended BLANK. A migration
    that filled it would be fabricating a human decision, which is the exact
    defect this workset removes.
    """
    p = Path(path)
    if not p.is_file():
        raise ProposalRefused("no file to migrate: %s" % p)
    lines = p.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ProposalRefused("%s is empty" % p.name)
    old_header = [c.strip().lower() for c in lines[0].split("\t")]
    new_header = [c.strip().lower() for c in HEADER.split("\t")]
    if old_header == new_header:
        return {"path": str(p), "action": "already_migrated",
                "rows": len(lines) - 1}
    if "decision_reason" in old_header:
        raise ProposalRefused(
            "%s already carries decision_reason but its header differs from "
            "the current shape — refusing to guess which is right" % p.name)
    if "reason" not in old_header:
        raise ProposalRefused(
            "%s has neither the old `reason` column nor `decision_reason` — "
            "this is not a file this migration understands" % p.name)
    out = [HEADER]
    preserved = 0
    for ln in lines[1:]:
        if not ln.strip():
            continue
        cells = ln.split("\t")
        if len(cells) != len(old_header):
            raise ProposalRefused(
                "a data row has %d cells but the old header has %d — refusing "
                "to migrate a file whose shape is already inconsistent"
                % (len(cells), len(old_header)))
        row = dict(zip(old_header, cells))
        if (row.get("answer") or "").strip():
            preserved += 1
        out.append("\t".join([
            row.get("skill_key", ""), row.get("catalog", ""),
            row.get("current_tags", ""), row.get("proposed_tag", ""),
            row.get("factors_unlocked", ""), row.get("reason", ""),
            row.get("answer", ""), row.get("decided_by", ""),
            row.get("cite_ref", ""), "",
        ]))
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"path": str(p), "action": "migrated", "rows": len(out) - 1,
            "answers_preserved": preserved,
            "decision_reason": "appended BLANK — a migration must not invent a "
                               "human reason"}


def write_proposal(conn: sqlite3.Connection, path: Path | str = PROPOSAL_TSV
                   ) -> dict[str, Any]:
    """Write the proposal. NEVER overwrites a file that already has answers."""
    p = Path(path)
    if p.exists():
        filled = [ln for ln in p.read_text(encoding="utf-8").splitlines()[1:]
                  if ln.strip() and len(ln.split("\t")) >= 7
                  and ln.split("\t")[6].strip()]
        if filled:
            raise ProposalRefused(
                "refusing to overwrite %s: it already carries %d answer(s). "
                "Re-rendering would destroy human work; move or edit it instead."
                % (p.name, len(filled)))
    p.write_text(render(conn), encoding="utf-8")
    return {"path": str(p), "bytes": p.stat().st_size}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--write", action="store_true",
                    help="write capability_tag_proposal.tsv (never overwrites "
                         "a file that already carries answers)")
    ap.add_argument("--migrate-tsv", action="store_true",
                    help="rewrite an OLD proposal file to the new header, "
                         "preserving every answer and inventing no reason")
    ap.add_argument("--out", default=str(PROPOSAL_TSV))
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.migrate_tsv:
            print(json.dumps(migrate_tsv(args.out), indent=2,
                             ensure_ascii=False))
            return
        if args.write:
            print(json.dumps(write_proposal(conn, args.out), indent=2,
                             ensure_ascii=False))
            return
        res = propose(conn)
        print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                         indent=2, ensure_ascii=False))
        print("\n%-42s %-10s %-6s %s" % ("skill_key", "catalog", "tag",
                                         "factors it would unlock"))
        for r in res["rows"]:
            if r["state"] == "proposed":
                print("%-42s %-10s %-6s %s"
                      % (r["skill_key"], r["catalog"], r["proposed_tag"],
                         ", ".join(r["unlocked"]) or "(none)"))
    finally:
        conn.close()


if __name__ == "__main__":
    main()