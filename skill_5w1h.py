# -*- coding: utf-8 -*-
"""skill_5w1h.py — the GENERAL 5W1H template, as DATA.

WHY THIS EXISTS (user, 2026-09-22):

    "skill = 5W1H for plan.md"
    "skill = multi dismenional SSOT, fuck...."

and, asked whether 5W1H is a document format or a registration dimension:

    "-> both"

So this module supplies BOTH halves, from ONE source:

  1. the SIX DIMENSIONS as data (`DIMENSIONS`), so a document template and a
     contract field set cannot drift apart; and
  2. `seed_contract_5w1h(conn, contract_id)`, which writes the six
     `skill_contract_field` rows for a contract.

WHY ONE SOURCE AND NOT TWO
--------------------------
A `.md` template and a set of DB fields that are maintained separately WILL
disagree — this repo has measured that exact drift before (46 `.skill.md` files
vs 28 ssot rows: 12 disagreed, 20 had no row at all). So the six dimensions are
declared ONCE here, and both the document and the fields are derived from them.

THE SIX DIMENSIONS, and why each is a HARD RULE rather than a heading
--------------------------------------------------------------------
A heading can be left empty. A `skill_contract_field` with `mandatory=1` and a
`hard_rule` cannot. The point of the template is that a plan which cannot answer
one of the six is INCOMPLETE, and that has to be checkable, not merely stated.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"

# (field_name, question, hard_rule, mandatory)
#
# `mandatory=1` for all six: a plan missing any one of them is the defect the
# template exists to catch. `hard_rule` is the checkable form of the question —
# `skill_contract_store.upsert_field` HARD-REJECTS an empty `hard_rule`, so a
# dimension cannot be registered as a bare heading.
DIMENSIONS: tuple[tuple[str, str, str, int], ...] = (
    ("what",
     "What is being changed, named concretely?",
     "non_blank: names the artifact (a file, a table, a function), not a topic",
     1),
    ("why",
     "Why is it needed — what breaks or is missing without it?",
     "non_blank: states the defect or the gap, not a benefit",
     1),
    ("who",
     "Who writes it, who approves it, who is affected?",
     "non_blank: names the writer AND the approver; 'the agent' alone is not "
     "an answer",
     1),
    ("when",
     "When does it happen — before what, after what?",
     "non_blank: states the ORDER relative to another act (e.g. 'before any "
     "code is written'), not a date",
     1),
    ("where",
     "Where does the artifact live — the exact path?",
     "non_blank: a real path, not a directory name",
     1),
    ("how",
     "How is it verified — which command or which check?",
     "non_blank: a command that can be RUN or a check that can FAIL, not "
     "'review it'",
     1),
)

DIMENSION_NAMES: tuple[str, ...] = tuple(d[0] for d in DIMENSIONS)


class FiveWOneHError(ValueError):
    """Raised when the template would be registered without its rules."""


def seed_contract_5w1h(conn: sqlite3.Connection, contract_id: str, *,
                       taxonomy_path: str | None = None) -> dict[str, Any]:
    """Write the six 5W1H fields for a contract. Idempotent.

    Goes through `skill_contract_store.upsert_field`, so the SAME gate applies
    as for any other field: an empty `hard_rule` is refused there, and a
    duplicate is updated rather than silently doubled.
    """
    import skill_contract_store as scs

    cid = str(contract_id or "").strip()
    if not cid:
        raise FiveWOneHError("contract_id is required")
    if not conn.execute("SELECT 1 FROM skill_contract_template WHERE "
                        "contract_id = ?", (cid,)).fetchone():
        raise FiveWOneHError(
            "unknown contract_id %r — the contract must exist before its fields "
            "(a field with no contract is an orphan)" % cid)

    written: list[str] = []
    for name, question, hard_rule, mandatory in DIMENSIONS:
        # `upsert_field` RETURNS a refusal dict rather than raising, so the
        # result must be CHECKED. Ignoring it would let a refused field look
        # like a written one — the "silent no-op" defect this repo has recorded.
        res = scs.upsert_field(
            cid, name, "TEXT", hard_rule,
            mandatory=bool(mandatory),
            taxonomy_path=taxonomy_path,
            conn=conn,
        )
        if not res.get("ok"):
            raise FiveWOneHError(
                "field %r was REFUSED by the contract store: %s"
                % (name, res.get("message") or res))
        written.append(name)
    conn.commit()
    return {"ok": True, "contract_id": cid, "fields": written,
            "count": len(written)}


def fields_for(conn: sqlite3.Connection, contract_id: str) -> list[dict[str, Any]]:
    """The 5W1H fields of a contract, in dimension order."""
    rows = conn.execute(
        "SELECT field_name, data_type, mandatory, hard_rule FROM "
        "skill_contract_field WHERE contract_id = ?", (str(contract_id),))
    got = {r["field_name"]: dict(r) for r in rows}
    return [got[n] for n in DIMENSION_NAMES if n in got]


def missing_for(conn: sqlite3.Connection, contract_id: str) -> list[str]:
    """Which of the six dimensions a contract does NOT declare.

    Reported rather than raised: a contract that predates the template is not an
    error, it is INCOMPLETE, and the difference matters to a reader.
    """
    have = {r["field_name"] for r in conn.execute(
        "SELECT field_name FROM skill_contract_field WHERE contract_id = ?",
        (str(contract_id),))}
    return [n for n in DIMENSION_NAMES if n not in have]


def render_template() -> str:
    """The 5W1H document template, DERIVED from `DIMENSIONS`.

    Derived, not hand-written: a hand-written copy is the drift this module
    exists to prevent. Every heading carries its own hard rule, so a reader
    cannot fill a heading without seeing what would make it acceptable.
    """
    lines = ["## 5W1H", ""]
    for name, question, hard_rule, mandatory in DIMENSIONS:
        lines.append("### %s — %s" % (name.upper(), question))
        lines.append("")
        lines.append("_Rule: %s_" % hard_rule)
        lines.append("")
        lines.append("<!-- %s -->" % ("REQUIRED" if mandatory else "optional"))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="the general 5W1H template")
    ap.add_argument("--template", action="store_true",
                    help="print the document template")
    ap.add_argument("--seed", metavar="CONTRACT_ID")
    ap.add_argument("--missing", metavar="CONTRACT_ID")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    if args.template:
        print(render_template())
        return

    conn = sqlite3.connect(str(args.db or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.seed:
            print(json.dumps(seed_contract_5w1h(conn, args.seed), indent=2))
        if args.missing:
            print(json.dumps(missing_for(conn, args.missing), indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
