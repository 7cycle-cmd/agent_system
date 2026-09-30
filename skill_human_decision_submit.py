# -*- coding: utf-8 -*-
"""skill_human_decision_submit.py — receive a HUMAN decision, validate it,
commit it, and record a traceable origin.

THE TWO LAYERS THIS KEEPS APART
-------------------------------
    decision_list.py     RENDERS the worksheet     (evidence display)
    decision_answers.py  owns the INPUT file shape (human input)
    THIS MODULE          VALIDATES + COMMITS       (action execution)

A worksheet that decides is a bridge in disguise, and a file that writes itself
is a decision nobody made. So the display never decides, the input is never
written by the machine, and only this module commits — after every gate below.

THE GATES (all refuse; none warns)
----------------------------------
  G1  an answer cell that is EMPTY is not a decision and is skipped, never
      submitted as blank
  G2  `answer` must be an authoritative `skill_key` or the literal `NONE`.
      A typo is REFUSED, so a misspelling cannot enter the namespace as a
      mapping that resolves to nothing
  G3  `decided_by` is mandatory and may not be a pattern word
      (`pattern` / `guess` / `auto` / `unknown`): an attributed decision names
      who or what decided
  G4  `cite_ref` is mandatory AND must pass `citation_discipline.assert_cited`.
      An uncited decision is DISCARDED, never downgraded — the same rule the
      rest of the workset uses
  G5  the answer for a row with candidates must be ONE OF THE CANDIDATES.
      This is the enforced form of "cannot submit multiple candidates for an
      AMBIGUOUS item": the answer is exactly one value, and it must be one the
      rule actually offered. Naming a skill the rule never proposed would be an
      override; overrides are REFUSED here because the module that should carry
      an override cite does not exist yet
  G6  `NO_CANDIDATE` rows may name any authoritative key (the rule offered
      none, so there is nothing to check the answer against). `NONE` is always
      allowed — "this maps to no skill" is a real decision

WHAT IS WRITTEN, AND WHY BOTH PLACES
------------------------------------
  `skill_key_alias`          the RESOLVABLE mapping. Kept as current truth, so
                             `INSERT OR REPLACE` is correct here.
  `skill_key_decision_log`   APPEND-ONLY. Every submission is a NEW row, so a
                             re-decision ADDS history instead of destroying it.

That split is deliberate: one table answers "what does this alias mean now?",
the other answers "who decided that, when, on what evidence, and what did it
say before?". A single table cannot answer both — storing history in the
mapping table makes resolution ambiguous, and storing only the current value
loses the audit trail. `apply_proposed` alone cannot serve the human path
either: it refuses every non-PROPOSED state, so an AMBIGUOUS or NO_CANDIDATE
row could never be committed through it.

`NONE` writes NOTHING to `skill_key_alias`. Writing `skill_key='NONE'` would
place a non-answer inside the namespace of real skill keys, where a later join
would find a phantom skill. The refusal IS recorded — in the decision log.

LAW: this module never invents an answer, never fills a blank row, and never
writes to `skill_key_alias` for a row the log did not first accept.
"""
from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import citation_discipline as cd  # noqa: E402
import decision_answers as da  # noqa: E402
import skill_key_bridge as skb  # noqa: E402

DB = BASE / "agent.db"

NONE_ANSWER = "NONE"

# A decision must name who or what decided. These are the words that describe a
# PROCESS producing a value, not a decision being made — the same set
# `skill_key_bridge.apply_proposed` refuses.
PATTERN_WORDS = ("", "pattern", "guess", "auto", "unknown", "n/a", "none")

# Append-only. Module-owned DDL, following `skill_key_bridge.ALIAS_DDL`: the
# table exists to serve the module's contract, so the contract and its shape
# live together.
DECISION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS skill_key_decision_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT    NOT NULL,
    alias_key    TEXT    NOT NULL,
    rule_state   TEXT    NOT NULL,   -- the state the RULE reached, preserved
    answer       TEXT    NOT NULL,   -- a skill_key, or the literal NONE
    written_alias INTEGER NOT NULL DEFAULT 0,  -- 1 only for a real mapping
    candidates   TEXT,               -- what the rule OFFERED, comma-joined
    decided_by   TEXT    NOT NULL,
    cite_ref     TEXT    NOT NULL,
    source_md    TEXT    NOT NULL,   -- WHICH worksheet the human read
    md_line      INTEGER,            -- the line in that worksheet, if listed
    source_tsv   TEXT    NOT NULL,   -- WHICH input file carried the answer
    tsv_line     INTEGER NOT NULL,   -- ENUMERATED at parse time, never typed
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_skill_key_decision_alias
  ON skill_key_decision_log (alias_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skill_key_decision_trace
  ON skill_key_decision_log (trace_id);
"""


class DecisionRefused(RuntimeError):
    """Raised when a submission would persist something nobody decided."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("decision refused — not written: %s"
                         % "; ".join(self.reasons))


# Stable rejection CODES. A case asserts a CODE, never a sentence.
#
# WHY THIS EXISTS (measured, not preferred): the first version of the TDD
# fixtures matched SUBSTRINGS of the human-readable reason (`"names a pattern"`,
# `"cite_ref rejected"`). `_expected_matches` compares with `==`, so all four
# failed — and fixing them by pasting the FULL sentence would have made the
# cases assert DISPLAY TEXT, which is exactly the "assert a phrase the module
# contains" trap that already cost a check earlier in this workset. A sentence
# may be reworded at any time without the gate changing; a code is the contract,
# the sentence is the presentation.
CODE_ANSWER_NOT_AUTHORITATIVE = "ANSWER_NOT_AUTHORITATIVE"
CODE_DECIDER_IS_PATTERN = "DECIDER_IS_PATTERN"
CODE_CITE_REJECTED = "CITE_REJECTED"
CODE_ANSWER_NOT_OFFERED = "ANSWER_NOT_OFFERED"
CODE_STATE_STALE = "STATE_STALE"
ALL_CODES = (CODE_ANSWER_NOT_AUTHORITATIVE, CODE_DECIDER_IS_PATTERN,
             CODE_CITE_REJECTED, CODE_ANSWER_NOT_OFFERED, CODE_STATE_STALE)


def new_trace_id() -> str:
    """A trace id for ONE submission run: `DEC-<ts>-<8 hex>`.

    UTC so the id does not depend on the machine's clock offset, and random in
    the suffix so two runs in the same second cannot collide.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return "DEC-%s-%s" % (ts, uuid.uuid4().hex[:8])


def ensure_decision_log(conn: sqlite3.Connection) -> None:
    conn.executescript(DECISION_LOG_DDL)
    conn.commit()


def validate(
    conn: sqlite3.Connection,
    parsed: dict,
    *,
    source_md: Path | str = "",
    known_md_lines: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Apply the gates to every FILLED row. Returns accepted / rejected.

    A REJECTION of one row does not silently drop the others out of the report:
    both lists are returned, because "N of M accepted" is the number that tells
    a human whether their worksheet was understood.
    """
    auth = set(skb.authoritative(conn))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped_blank: list[str] = []
    md_lines = known_md_lines or {}

    for row in parsed["answers"]:
        cid = row.get("contract_id") or ""
        answer = (row.get("answer") or "").strip()

        # G1 — an empty cell is not a decision.
        if not answer:
            skipped_blank.append(cid)
            continue

        reasons: list[str] = []
        codes: list[str] = []

        # GATE PRECEDENCE — decided IN ORDER, and a later gate does not speak
        # about a question an earlier gate has already settled.
        #
        # DEFECT MEASURED BY RUNNING THE CASE, not by reading the code: `typo`
        # fired BOTH `ANSWER_NOT_AUTHORITATIVE` and `ANSWER_NOT_OFFERED`, and the
        # second message reads "An override is REFUSED". For a plain typo that is
        # not extra information, it is a WRONG EXPLANATION — the answer was not
        # an override, it was not a skill at all. A gate that reports unrelated
        # reasons trains a reader to ignore the list. Same family as the
        # `ISO`-style ordering defect fixed earlier in this workset: a general
        # condition must not swallow, or speak over, a specific one.
        answer_is_key = answer in auth

        # G2 — the answer must be a real skill_key or NONE.
        if answer != NONE_ANSWER and not answer_is_key:
            codes.append(CODE_ANSWER_NOT_AUTHORITATIVE)
            reasons.append("answer %r is not an authoritative skill_key and is "
                           "not NONE (a typo would enter the namespace as a "
                           "mapping resolving to nothing)" % answer)

        # G3 — attributed.
        decider = (row.get("decided_by") or "").strip()
        if decider.lower() in PATTERN_WORDS:
            codes.append(CODE_DECIDER_IS_PATTERN)
            reasons.append("decided_by %r names a pattern, not a decision"
                           % decider)

        # G4 — cited, through the SHARED gate so there is one copy of the rule.
        cite = (row.get("cite_ref") or "").strip()
        try:
            cd.assert_cited({"evidence_ref": cite})
        except cd.UncitedFinding as e:
            codes.append(CODE_CITE_REJECTED)
            reasons.append("cite_ref rejected: %s" % (e.reasons[0]
                           if e.reasons else "cite_ref is not a citation"))

        # G5 — the answer must be one the rule OFFERED (unless it is NONE).
        # Only asked when the answer IS a real key: for a typo there is no
        # meaningful "was it offered" question, and asking it produces the
        # misleading override message this precedence rule removes.
        cand_text = (row.get("candidates") or "").strip()
        cands = [c.strip() for c in cand_text.split(",")
                 if c.strip() and c.strip() != "(none)"]
        if answer_is_key and cands and answer not in cands:
            codes.append(CODE_ANSWER_NOT_OFFERED)
            reasons.append(
                "answer %r is not among the candidates the rule offered (%s). "
                "An override is REFUSED: the module that carries an override "
                "cite does not exist yet" % (answer, ", ".join(cands)))

        # G6 — a NO_CANDIDATE row may name any authoritative key; nothing to
        # check against. Recorded, not silently permitted.
        if answer != NONE_ANSWER and not cands and row.get("rule_state") \
                not in ("NO_CANDIDATE",):
            codes.append(CODE_STATE_STALE)
            reasons.append("no candidates listed but state is %r — the row may "
                           "be stale; re-render the worksheet"
                           % row.get("rule_state"))

        if reasons:
            rejected.append({"contract_id": cid, "answer": answer,
                             "codes": codes,
                             "line_no": row.get("line_no"),
                             "reasons": reasons})
            continue

        accepted.append({
            "contract_id": cid,
            "rule_state": row.get("rule_state") or "",
            "answer": answer,
            "decided_by": decider,
            "cite_ref": cite,
            "candidates": cand_text,
            "tsv_line": int(row.get("line_no") or 0),
            "md_line": md_lines.get(cid),
        })

    return {"accepted": accepted, "rejected": rejected,
            "skipped_blank": skipped_blank,
            "authoritative_skills": len(auth)}


def submit(
    conn: sqlite3.Connection,
    accepted: list[dict[str, Any]],
    *,
    source_md: Path | str,
    source_tsv: Path | str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Commit ACCEPTED decisions. Append-only log first, mapping second.

    Order matters: the log row is written before the alias so a crash between
    the two leaves an AUDITED decision missing its mapping, never a mapping
    with no record of who made it.
    """
    if not accepted:
        raise DecisionRefused(
            ["nothing was accepted — refusing to open a write path for zero "
             "decisions (that is how an unattributed mapping gets in)"])
    ensure_decision_log(conn)
    skb.ensure_alias_schema(conn)
    tid = trace_id or new_trace_id()
    written, logged, refused_none = 0, 0, 0

    for a in accepted:
        is_none = a["answer"] == NONE_ANSWER
        cur = conn.execute(
            """INSERT INTO skill_key_decision_log
                 (trace_id, alias_key, rule_state, answer, written_alias,
                  candidates, decided_by, cite_ref, source_md, md_line,
                  source_tsv, tsv_line)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, a["contract_id"], a["rule_state"], a["answer"],
             0 if is_none else 1, a.get("candidates") or "",
             a["decided_by"], a["cite_ref"], str(source_md),
             a.get("md_line"), str(source_tsv), int(a["tsv_line"])),
        )
        logged += 1
        if is_none:
            # A REFUSAL is recorded, never written as a phantom skill_key.
            refused_none += 1
            continue
        conn.execute(
            "INSERT OR REPLACE INTO skill_key_alias "
            "(alias_key, skill_key, source, cite_ref, decided_by, note) "
            "VALUES (?,?,?,?,?,?)",
            (a["contract_id"], a["answer"], "decision_log",
             a["cite_ref"], a["decided_by"],
             "trace=%s line=%s" % (tid, a["tsv_line"])),
        )
        written += 1

    conn.commit()
    return {"trace_id": tid, "logged": logged, "alias_written": written,
            "none_recorded": refused_none,
            "alias_rows": conn.execute(
                "SELECT COUNT(*) FROM skill_key_alias").fetchone()[0],
            "log_rows": conn.execute(
                "SELECT COUNT(*) FROM skill_key_decision_log").fetchone()[0]}


def history(conn: sqlite3.Connection, alias_key: str) -> list[dict[str, Any]]:
    """Every decision recorded for one alias, newest first. Append-only."""
    ensure_decision_log(conn)
    return [dict(r) for r in conn.execute(
        "SELECT trace_id, rule_state, answer, written_alias, decided_by, "
        "cite_ref, source_md, md_line, source_tsv, tsv_line, created_at "
        "FROM skill_key_decision_log WHERE alias_key=? "
        "ORDER BY id DESC", (alias_key,))]


def submit_from_files(
    conn: sqlite3.Connection,
    *,
    answers_path: Path | str,
    md_path: Path | str = "",
    apply: bool = False,
) -> dict[str, Any]:
    """The whole path: read the input, validate, optionally commit.

    `apply=False` is a DRY RUN and reports exactly what would be written. It is
    the default, because the dangerous direction of a mistake here is writing.
    """
    parsed = da.parse(answers_path)
    # Refuse an empty/unreadable result BEFORE validating — otherwise a
    # destroyed worksheet reports "0 accepted, 0 rejected", which reads as a
    # clean run.
    da.assert_answers_found(parsed, answers_path)

    md_lines: dict[str, int] = {}
    claim: dict[str, Any] = {"ok": None, "checked": 0}
    if md_path:
        mp = Path(md_path)
        if mp.is_file():
            md_text = mp.read_text(encoding="utf-8", errors="replace")
            # Map contract_id -> the PHYSICAL line of its heading in the
            # worksheet, so the trace can name it without trusting a typed value.
            for i, line in enumerate(md_text.splitlines(), start=1):
                for cid in list({a["contract_id"] for a in parsed["answers"]}):
                    if cid and ("`%s`" % cid) in line and cid not in md_lines:
                        md_lines[cid] = i
            claim = da.claim_matches_markdown(
                md_text, {a["contract_id"] for a in parsed["answers"]})

    result = validate(conn, parsed, source_md=md_path, known_md_lines=md_lines)
    result["md_claim"] = claim
    result["answers_file"] = str(answers_path)
    if apply:
        result["writes"] = submit(
            conn, result["accepted"],
            source_md=md_path, source_tsv=answers_path)
    else:
        result["writes"] = None
    return result


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--answers", required=True)
    ap.add_argument("--md", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.render:
            print(json.dumps(da.write_answers_file(conn, args.answers), indent=2))
            return
        out = submit_from_files(conn, answers_path=args.answers,
                                md_path=args.md, apply=args.apply)
        brief = {
            "answers_file": out["answers_file"],
            "accepted": len(out["accepted"]),
            "rejected": len(out["rejected"]),
            "skipped_blank": len(out["skipped_blank"]),
            "md_claim": out["md_claim"],
            "writes": out["writes"],
        }
        print(json.dumps(brief, indent=2, ensure_ascii=False))
        if out["rejected"]:
            print("\nREJECTED:")
            for r in out["rejected"]:
                print("  %s (line %s): %s"
                      % (r["contract_id"], r["line_no"], "; ".join(r["reasons"])))
    finally:
        conn.close()


if __name__ == "__main__":
    main()