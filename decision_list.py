# -*- coding: utf-8 -*-
"""decision_list.py — A) the 11 items that need a human, in reviewable form.

WHAT THIS PRODUCES, AND WHAT IT REFUSES TO PRODUCE
--------------------------------------------------
It lists every contract the RULE could not settle, with everything a reviewer
needs to decide in one place:
    contract_id, its entity letter, its name
    every candidate, the shared tokens, the GENERIC tokens dropped
    why the state was reached (collision count, or no candidate at all)
    the entity-type mismatch flag (a TBL contract mapped to a SKILL skill)

It does NOT output a mapping for those rows. A decision list that supplies a
suggested answer for undecidable rows is a bridge wearing a list's clothes, and
`skill_key_bridge` already measured that a single shared token (`TBL.CODE_REGISTER
-> skill_worker_code_builder`) survives every automatic test and is still wrong.

INPUT is `skill_key_bridge.build_list()`, so the states come from the one place
that computes them. This module adds PRESENTATION and a WRITABLE worksheet.

    --md      markdown worksheet, one section per item
    --json    machine form
    --tsv     one row per candidate, for a spreadsheet
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import code_introspect as ci  # noqa: E402
import skill_key_bridge as skb  # noqa: E402

DB = BASE / "agent.db"

# Entity letters that are NOT skills. A contract with one of these letters that
# resolves to a skill needs semantic review: the WORD matched, the MEANING is
# unverified. This set is the same one `skill_key_bridge` uses.
NON_SKILL_LETTERS = ("TBL", "FLD", "CH", "MOD", "CAP", "FN", "API")


def needs_human(state: str) -> bool:
    return state in ("REVIEW", "AMBIGUOUS", "NO_CANDIDATE")


def collect(conn: sqlite3.Connection) -> dict[str, Any]:
    res = skb.build_list(conn)
    items = []
    for r in res["rows"]:
        if not needs_human(r["state"]):
            continue
        parts = skb.entity_parts(r["contract_id"])
        cands = r.get("candidates") or []
        items.append({
            "contract_id": r["contract_id"],
            "state": r["state"],
            "entity_letter": parts["letter"],
            "entity_name": parts["name"],
            "entity_is_non_skill": parts["letter"] in NON_SKILL_LETTERS,
            "candidate_count": len(cands),
            "candidates": [
                {"skill_key": c["skill_key"],
                 "shared_tokens": c.get("shared_tokens", []),
                 "generic_tokens_dropped": c.get("generic_tokens_dropped", []),
                 "rule": c.get("rule", "")}
                for c in cands],
            "collision": r.get("collision"),
            "weakness": r.get("weakness"),
            "question": _question(r, parts, cands),
        })
    by = {}
    for it in items:
        by[it["state"]] = by.get(it["state"], 0) + 1
    return {"items": items, "by_state": by, "count": len(items),
            "settled": res["by_state"].get("PROPOSED", 0),
            "note": ("No mapping is proposed for these rows. The worksheet asks a "
                     "question per row; the answer is a human decision recorded "
                     "via skill_key_bridge --apply --decided-by/--cite-ref.")}


def _question(row: dict[str, Any], parts: dict[str, str],
               cands: list[dict[str, Any]]) -> str:
    """ONE reviewable question per item. Wording derives from the STATE."""
    if row["state"] == "REVIEW":
        return ("This is a %s contract resolving to a skill on ONE token (%s). Is "
                "it really the same thing, or is the word a coincidence?"
                % (parts["letter"] or "?", cands[0]["shared_tokens"] if cands
                   else "?"))
    if row["state"] == "AMBIGUOUS":
        if row.get("collision"):
            return ("%s Which one skill is correct, or is none? The rule could not "
                    "separate them." % row["collision"])
        return ("%d candidates matched. Which is correct, or is none?"
                % len(cands))
    return ("No candidate matched. Which skill does this contract belong to, or "
            "should it have a contract of its own?")


def to_markdown(res: dict[str, Any]) -> str:
    lines = ["# Decision list — contracts the rule could not settle",
             "",
             "**%d items** (%s). %d settled by the rule and NOT listed here."
             % (res["count"],
                ", ".join("%s %d" % (k, v) for k, v in sorted(res["by_state"].items())),
                res["settled"]),
             "",
             res["note"],
             ""]
    for it in res["items"]:
        lines.append("## %s  —  `%s`" % (it["state"], it["contract_id"]))
        lines.append("")
        lines.append("- entity: `%s` / name `%s`%s"
                     % (it["entity_letter"], it["entity_name"] or "(none)",
                        "  **(NON-SKILL TYPE)**" if it["entity_is_non_skill"]
                        else ""))
        lines.append("- candidates: %d" % it["candidate_count"])
        for c in it["candidates"]:
            lines.append("  - `%s`  shared=%s%s"
                         % (c["skill_key"], c["shared_tokens"],
                            ("  dropped=%s" % c["generic_tokens_dropped"])
                            if c["generic_tokens_dropped"] else ""))
        if it["weakness"]:
            lines.append("- **weakness**: %s" % it["weakness"])
        lines.append("")
        lines.append("**Question:** %s" % it["question"])
        lines.append("")
        lines.append("**Answer** (skill_key, or `NONE`): "
                     "`____________________`")
        lines.append("")
    return "\n".join(lines)


def to_tsv(res: dict[str, Any]) -> str:
    rows = ["state\tcontract_id\tentity\tcandidate\tshared_tokens\t"
            "generic_dropped\tsame_entity"]
    for it in res["items"]:
        if not it["candidates"]:
            rows.append("%s\t%s\t%s\t(none)\t\t\t%s"
                        % (it["state"], it["contract_id"], it["entity_letter"],
                           "no" if it["entity_is_non_skill"] else "yes"))
            continue
        for c in it["candidates"]:
            rows.append("%s\t%s\t%s\t%s\t%s\t%s\t%s"
                        % (it["state"], it["contract_id"], it["entity_letter"],
                           c["skill_key"], ",".join(c["shared_tokens"]),
                           ",".join(c["generic_tokens_dropped"]),
                           "no" if it["entity_is_non_skill"] else "yes"))
    return "\n".join(rows)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--tsv", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        res = collect(conn)
        if args.md:
            print(to_markdown(res))
        elif args.tsv:
            print(to_tsv(res))
        else:
            print(json.dumps(res, indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()