# -*- coding: utf-8 -*-
"""decision_answers.py — the HUMAN INPUT side of the decision worksheet.

WHY THIS MODULE EXISTS (and why it is NOT part of decision_list.py)
------------------------------------------------------------------
`decision_list.py` RENDERS a worksheet. A rendering is for eyes: column widths,
headings and wording may change at any time, and none of that is a contract.

So the answers may NOT be read back out of the markdown. Parsing a rendering
means a formatting change silently produces ZERO answers — and "found no
answers" reads exactly like "there was nothing to submit". That is the same
defect family as the text searches earlier in this workset that matched their
own docstrings: a reader aimed at the wrong object, failing quietly.

The separation is therefore by ROLE, not by file format:

    decision_list.py    ->  the EVIDENCE it needs, and the display (.md)
    decision_answers.py ->  the human INPUT file, machine-readable by design
    skill_human_decision_submit.py ->  the VALIDATOR + the WRITE

This module owns ONE thing: the shape of the input file, in both directions.
Render it, and parse it back. Nothing here decides anything, and nothing here
writes to a database.

THE FILE
--------
A TSV with a header row. The identity columns come from the rule; the three
last columns are LEFT BLANK for a human to fill:

    contract_id  rule_state  candidates  entity  answer  decided_by  cite_ref

`answer` is `skill_key` or `NONE`. `decided_by` names the human or process.
`cite_ref` is a checkable citation (path:line, a command, an EVID id, or
`register:<table>:<pk>`), enforced downstream by `citation_discipline`.

WHY `line` IS NOT A COLUMN BUT IS STILL RECORDED
------------------------------------------------
The trace must say WHICH line of WHICH file carried the decision. That number
is computed at PARSE time from the physical file, not carried in the data, so
it cannot be stale or hand-edited. A self-reported line number is a claim; an
enumerated one is an observation.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import skill_key_bridge as skb  # noqa: E402

import re  # noqa: E402

DB = BASE / "agent.db"

# The worksheet writes each item heading as "## STATE  —  `CONTRACT_ID`", so the
# id is found inside BACKTICKS. Anchoring on that shape means a contract id that
# merely appears in prose (e.g. inside a question) is not mistaken for a heading.
_RX_MD_CONTRACT = re.compile(r"^##\s+\S+\s+—\s+`([^`]+)`\s*$", re.M)

# The three columns a human fills. Everything else is written by the renderer
# and is NOT the human's to edit — the reader ignores any other column.
ANSWER_COLS = ("answer", "decided_by", "cite_ref")

# Identity + context, written by the renderer. `candidates` and `entity` are
# present so the human can decide WITHOUT going back to the markdown.
ID_COLS = ("contract_id", "rule_state", "candidates", "entity")

HEADER = "\t".join(ID_COLS + ANSWER_COLS)

# A row whose answer cell is empty has NOT been decided. It is not `NONE`:
# `NONE` is a decision ("this maps to no skill") and must be typed.
NONE_ANSWER = "NONE"


class NoAnswersFound(RuntimeError):
    """Raised when an input file yields zero answer rows.

    This is an ERROR, never an empty success. A parser that returns [] for both
    "nothing to do" and "I could not read the file" cannot tell a completed
    worksheet from a destroyed one.
    """


def _candidates_cell(row: dict[str, Any]) -> str:
    cands = row.get("candidates") or []
    return ",".join(c["skill_key"] for c in cands) if cands else "(none)"


def render(conn: sqlite3.Connection) -> str:
    """The human input file: every contract, with the three answer cells blank.

    Rendered from `skill_key_bridge.build_list`, so the identity and the state
    come from the ONE module that computes them. A second computation here
    could disagree with the rule while looking authoritative.
    """
    res = skb.build_list(conn)
    lines = [HEADER]
    for r in sorted(res["rows"], key=lambda x: str(x["contract_id"])):
        parts = skb.entity_parts(str(r["contract_id"]))
        lines.append("\t".join([
            str(r["contract_id"]),
            str(r["state"]),
            _candidates_cell(r),
            parts["letter"] or "?",
            "", "", "",
        ]))
    return "\n".join(lines) + "\n"


def write_answers_file(conn: sqlite3.Connection, path: Path | str) -> dict:
    """Write the input file. NEVER overwrites a file that already has answers."""
    p = Path(path)
    if p.exists():
        existing = parse(p)
        filled = [a for a in existing["answers"] if a["answer"]]
        if filled:
            raise NoAnswersFound(
                "refusing to overwrite %s: it already carries %d answer(s). "
                "Re-rendering would destroy human work; move or edit it "
                "instead." % (p.name, len(filled))
            )
    p.write_text(render(conn), encoding="utf-8")
    return {"path": str(p), "bytes": p.stat().st_size}


def parse(path: Path | str) -> dict:
    """Read the input file. Returns {"answers": [...], "errors": [...], ...}.

    Header-driven: the columns are located BY NAME, so column order may change
    without breaking the reader. An answer row records the PHYSICAL line it came
    from, computed here.
    """
    p = Path(path)
    if not p.is_file():
        return {"answers": [], "errors": ["file not found: %s" % p],
                "path": str(p), "header_ok": False}

    text = p.read_text(encoding="utf-8-sig")
    answers: list[dict[str, Any]] = []
    errors: list[str] = []
    header: list[str] | None = None
    header_line = 0

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in line.split("\t")]
        if header is None:
            header = [c.lower() for c in cells]
            header_line = i
            missing = [c for c in ID_COLS + ANSWER_COLS if c not in header]
            if missing:
                errors.append("header (line %d) missing column(s): %s"
                              % (i, ", ".join(missing)))
            continue
        if len(cells) != len(header):
            errors.append("line %d: %d cells, header has %d"
                          % (i, len(cells), len(header)))
            continue
        row = dict(zip(header, cells))
        answers.append({
            "line_no": i,
            "contract_id": row.get("contract_id", ""),
            "rule_state": row.get("rule_state", ""),
            "candidates": row.get("candidates", ""),
            "entity": row.get("entity", ""),
            "answer": row.get("answer", ""),
            "decided_by": row.get("decided_by", ""),
            "cite_ref": row.get("cite_ref", ""),
        })

    return {"answers": answers, "errors": errors, "path": str(p),
            "header_ok": not errors, "header_line": header_line}


def assert_answers_found(parsed: dict, path: Path | str) -> dict:
    """Refuse an EMPTY result. "I found nothing" is never "nothing to do"."""
    if parsed.get("errors"):
        raise NoAnswersFound(
            "cannot read %s: %s" % (path, "; ".join(parsed["errors"][:4])))
    if not parsed.get("answers"):
        raise NoAnswersFound(
            "%s yielded ZERO answer rows. A header with no data rows is not an "
            "empty decision set — it is an unreadable or truncated file, and "
            "treating it as 'nothing to submit' is the failure this gate "
            "exists to prevent." % path)
    return parsed


def filled(parsed: dict) -> list[dict[str, Any]]:
    """Only the rows a human actually decided. Blank rows are NOT decisions."""
    return [a for a in parsed["answers"] if a["answer"]]


def claim_matches_markdown(md_text: str, tsv_contract_ids: set[str]) -> dict:
    """Prove the WORKSHEET's questions are all answerable in the INPUT file.

    THE DIRECTION MATTERS, and my first version had it backwards.
    -----------------------------------------------------------------
    I originally checked "every contract in the input file appears in the
    markdown" — which FAILED, and correctly so, because the two artifacts are
    meant to hold DIFFERENT sets:

        decision_list.md        only the rows that NEED a human (12)
        decision_answers.tsv    EVERY contract, so a human may also confirm a
                                PROPOSED row, or answer one that later becomes
                                ambiguous

    So the two sets are not equal and must not be asserted equal. The claim that
    actually has to hold is one-directional:

        every contract ASKED ABOUT in the worksheet
            must have a row IN the input file.

    If the display asks a question the input cannot carry, the human's answer has
    nowhere to go — and that is the failure this check exists to catch. Comparing
    whole sets would have been a check that could only ever fail.

    The worksheet is never parsed FOR ANSWERS; only its contract IDs are read,
    because the question and the answer must refer to the same object.
    """
    md_ids = {m.group(1) for m in _RX_MD_CONTRACT.finditer(md_text)}
    missing = sorted(c for c in md_ids if c not in tsv_contract_ids)
    return {"ok": not missing, "missing_from_input": missing,
            "questions_in_display": len(md_ids),
            "ids_in_input": len(tsv_contract_ids)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--write", default="")
    ap.add_argument("--check", default="")
    args = ap.parse_args()

    if args.check:
        print(parse(args.check))
        return
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.write:
            print(write_answers_file(conn, args.write))
        else:
            print(render(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()