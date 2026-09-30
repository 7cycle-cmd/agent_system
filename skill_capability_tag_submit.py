# -*- coding: utf-8 -*-
"""skill_capability_tag_submit.py — receive a HUMAN decision about a capability
tag, validate it, commit it, and record a traceable origin.

WHY THIS IS A SEPARATE MODULE AND NOT `skill_human_decision_submit`
------------------------------------------------------------------
That module is shaped around `contract_id` / `alias_key` / `skill_key_alias`:
its G2 checks the answer is an authoritative **skill_key**, and its G5 checks the
answer is one of the candidates the rule OFFERED. A capability tag is neither —
it is a tag, and the thing being decided is a different object. Reusing it would
mean weakening G2/G5 for the alias path, which has 20 contracts and a passing
proof. So the GATE PATTERN is copied, not the module.

THE TWO LAYERS THIS KEEPS APART
-------------------------------
    skill_capability_tag.py          RENDERS the proposal   (evidence display)
    THIS MODULE                      VALIDATES + COMMITS    (action execution)

A worksheet that decides is a bridge in disguise, and a file that writes itself
is a decision nobody made. So the display never decides, and only this module
commits — after every gate below.

THE GATES (all refuse; none warns)
----------------------------------
  G1  an answer cell that is EMPTY is not a decision and is skipped, never
      submitted as blank
  G2  `answer` must be a tag in `capability_tag_registry`, or the literal `NA`.
      A typo is REFUSED — an undefined tag matches ZERO skills SILENTLY, which
      is the exact defect this workset removes
  G3  `decided_by` is mandatory and may not be a pattern word
      (`pattern` / `guess` / `auto` / `unknown`): an attributed decision names
      who or what decided
  G4  `cite_ref` is mandatory AND must pass `citation_discipline.assert_cited`.
      An uncited decision is DISCARDED, never downgraded
  G5  the answer for a row that CARRIED a proposal must be that proposal or
      `NA`. Naming a tag the rule never proposed would be an override; overrides
      are REFUSED here because the module that should carry an override cite does
      not exist yet
  G6  a row with NO proposal (`no_evidence` / `no_file`) may name any registered
      tag — the rule offered none, so there is nothing to check against. `NA` is
      always allowed: "this skill has no capability tag" is a real decision

WHAT IS WRITTEN, AND WHY BOTH PLACES
------------------------------------
  `skill_registry.capability_tags`   the RESOLVABLE current truth. `UPDATE` is
                                     correct here: it answers "what does this
                                     skill's tag mean NOW?"
  `skill_capability_tag_log`         APPEND-ONLY. Every submission is a NEW row,
                                     so a re-decision ADDS history instead of
                                     destroying it.

That split is deliberate: one table answers "what is the tag now?", the other
answers "who decided that, when, on what evidence, and what did it say before?".

`NA` writes the literal `NA` to the column — NOT NULL, following the no_null
standard. Writing NULL would make "decided: no tag" indistinguishable from "the
standardiser did not run", which is a defect.

LAW: this module never invents an answer, never fills a blank row, and never
writes to `skill_registry` for a row the log did not first accept.
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
import skill_capability_tag as sct  # noqa: E402
import skill_factor as sf  # noqa: E402

DB = BASE / "agent.db"

NA_ANSWER = sf.NA_TAG

# A decision must name who or what decided. These are the words that describe a
# PROCESS producing a value, not a decision being made — the same set
# `skill_human_decision_submit` refuses.
PATTERN_WORDS = ("", "pattern", "guess", "auto", "unknown", "n/a", "none")

# Append-only. Module-owned DDL, following `skill_human_decision_submit`'s
# DECISION_LOG_DDL: the table exists to serve the module's contract, so the
# contract and its shape live together.
#
# `recorded_as` VOCABULARY (two values, and nothing else):
#   live        the row was written by the submit that MADE the decision
#   retroactive the row was written LATER, to record a reason the original
#               submit did not capture
# The distinction is not decoration. An append-only log can never be back-filled
# by an UPDATE, so a missing reason is closed by APPENDING a marked row — and
# without the marker a retroactive row is indistinguishable from a live one,
# which is a claim dressed as an observation.
TAG_LOG_DDL = """
CREATE TABLE IF NOT EXISTS skill_capability_tag_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT    NOT NULL,
    skill_key    TEXT    NOT NULL,
    rule_state   TEXT    NOT NULL,   -- the state the PROPOSAL reached
    proposed_tag TEXT    NOT NULL,   -- what the rule OFFERED (NA if none)
    answer       TEXT    NOT NULL,   -- a registered tag, or the literal NA
    tags_before  TEXT    NOT NULL,   -- the value the column held BEFORE
    tags_after   TEXT    NOT NULL,   -- the value written
    decided_by   TEXT    NOT NULL,
    cite_ref     TEXT    NOT NULL,
    -- THE HUMAN'S REASON. `NA` (never NULL) follows the no_null standard, so a
    -- surviving NULL means the standardiser did not run — a defect. It is
    -- MANDATORY when the answer DIFFERS from the proposal (gate G7): the reason
    -- matters most exactly when a human overrode the machine.
    decision_reason TEXT NOT NULL DEFAULT 'NA',
    recorded_as  TEXT    NOT NULL DEFAULT 'live'
                 CHECK (recorded_as IN ('live','retroactive')),
    source_tsv   TEXT    NOT NULL,   -- WHICH input file carried the answer
    tsv_line     INTEGER NOT NULL,   -- ENUMERATED at parse time, never typed
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_skill_capability_tag_log_skill
  ON skill_capability_tag_log (skill_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_skill_capability_tag_log_trace
  ON skill_capability_tag_log (trace_id);
"""

# Stable rejection CODES. A case asserts a CODE, never a sentence: a sentence may
# be reworded at any time without the gate changing; a code is the contract.
CODE_ANSWER_NOT_A_TAG = "ANSWER_NOT_A_TAG"
CODE_DECIDER_IS_PATTERN = "DECIDER_IS_PATTERN"
CODE_CITE_REJECTED = "CITE_REJECTED"
CODE_ANSWER_NOT_OFFERED = "ANSWER_NOT_OFFERED"
CODE_STATE_STALE = "STATE_STALE"
CODE_REASON_MISSING = "REASON_MISSING"
ALL_CODES = (CODE_ANSWER_NOT_A_TAG, CODE_DECIDER_IS_PATTERN, CODE_CITE_REJECTED,
             CODE_ANSWER_NOT_OFFERED, CODE_STATE_STALE, CODE_REASON_MISSING)


class TagDecisionRefused(RuntimeError):
    """Raised when a submission would persist something nobody decided."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("tag decision refused — not written: %s"
                         % "; ".join(self.reasons))


def new_trace_id() -> str:
    """A trace id for ONE submission run: `TAG-<ts>-<8 hex>`.

    UTC so the id does not depend on the machine's clock offset, and random in
    the suffix so two runs in the same second cannot collide.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return "TAG-%s-%s" % (ts, uuid.uuid4().hex[:8])


def ensure_tag_log(conn: sqlite3.Connection) -> None:
    conn.executescript(TAG_LOG_DDL)
    # ADDITIVE migration for a DB created before the reason existed. A column
    # added here is NOT NULL DEFAULT, so an existing row reads as "no reason
    # recorded" without a rewrite — the same shape as the no_null standard.
    # The 11 rows written before this column existed can NEVER gain a reason:
    # the log is append-only, and an UPDATE would destroy the record that the
    # reason was missing. They are closed by APPENDING marked retroactive rows.
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(skill_capability_tag_log)")}
    if "decision_reason" not in cols:
        conn.execute("ALTER TABLE skill_capability_tag_log ADD COLUMN "
                     "decision_reason TEXT NOT NULL DEFAULT 'NA'")
    if "recorded_as" not in cols:
        conn.execute("ALTER TABLE skill_capability_tag_log ADD COLUMN "
                     "recorded_as TEXT NOT NULL DEFAULT 'live'")
    conn.commit()


def parse(path: Path | str) -> dict[str, Any]:
    """Read the proposal TSV back. Header-driven, so column order may change.

    The line number is ENUMERATED from the physical file, never carried in the
    data: a self-reported line number is a claim, an enumerated one is an
    observation.
    """
    p = Path(path)
    if not p.is_file():
        return {"path": str(p), "header_ok": False, "answers": [],
                "errors": ["file not found: %s" % p]}
    errors: list[str] = []
    header: list[str] | None = None
    answers: list[dict[str, Any]] = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        cells = line.split("\t")
        if header is None:
            header = [c.strip().lower() for c in cells]
            missing = [c for c in sct.ID_COLS + sct.ANSWER_COLS
                       if c not in header]
            if missing:
                errors.append("header (line %d) missing column(s): %s"
                              % (i, ", ".join(missing)))
                return {"path": str(p), "header_ok": False, "answers": [],
                        "errors": errors}
            continue
        if len(cells) != len(header):
            errors.append("line %d: %d cells, header has %d"
                          % (i, len(cells), len(header)))
            continue
        row = dict(zip(header, cells))
        row["line_no"] = i
        answers.append(row)
    return {"path": str(p), "header_ok": not errors, "answers": answers,
            "errors": errors}


def assert_answers_found(parsed: dict, path: Path | str) -> dict:
    """A file that yields ZERO rows is an ERROR, never an empty success.

    A parser that returns [] for both "nothing to do" and "I could not read the
    file" cannot tell a completed worksheet from a destroyed one.
    """
    if not parsed["answers"]:
        raise TagDecisionRefused(
            ["%s yielded ZERO answer rows. A header with no data rows is not an "
             "empty success — it is a file that could not be read." % path])
    return parsed


def validate(conn: sqlite3.Connection, parsed: dict) -> dict[str, Any]:
    """Apply the gates to every FILLED row. Returns accepted / rejected.

    A REJECTION of one row does not silently drop the others out of the report:
    both lists are returned, because "N of M accepted" is the number that tells
    a human whether their worksheet was understood.
    """
    known = set(sf.registered_tags(conn))
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped_blank: list[str] = []

    for row in parsed["answers"]:
        key = (row.get("skill_key") or "").strip()
        answer = (row.get("answer") or "").strip()

        # G1 — an empty cell is not a decision.
        if not answer:
            skipped_blank.append(key)
            continue

        reasons: list[str] = []
        codes: list[str] = []

        # GATE PRECEDENCE — decided IN ORDER, and a later gate does not speak
        # about a question an earlier gate has already settled. A typo must not
        # also be reported as "an override", which is a WRONG EXPLANATION.
        answer_is_tag = answer in known

        # G2 — the answer must be a registered tag or NA.
        if answer != NA_ANSWER and not answer_is_tag:
            codes.append(CODE_ANSWER_NOT_A_TAG)
            reasons.append(
                "answer %r is not in capability_tag_registry (known: %s) and is "
                "not NA. An undefined tag matches ZERO skills SILENTLY — the "
                "same defect as reading prose as a tag."
                % (answer, ", ".join(sorted(known)) or "none"))

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
            reasons.append("cite_ref rejected: %s"
                           % (e.reasons[0] if e.reasons
                              else "cite_ref is not a citation"))

        # G5 — the answer must be what the rule OFFERED (unless it is NA).
        # Only asked when the answer IS a real tag: for a typo there is no
        # meaningful "was it offered" question.
        proposed = (row.get("proposed_tag") or "").strip()
        if answer_is_tag and proposed and proposed != NA_ANSWER \
                and answer != proposed:
            codes.append(CODE_ANSWER_NOT_OFFERED)
            reasons.append(
                "answer %r is not the tag the rule proposed (%s). An override is "
                "REFUSED: the module that carries an override cite does not "
                "exist yet" % (answer, proposed))

        # G6 — a row with NO proposal may name any registered tag; nothing to
        # check against. Recorded, not silently permitted.
        if answer_is_tag and (not proposed or proposed == NA_ANSWER) \
                and not (row.get("proposal_reason") or "").strip():
            codes.append(CODE_STATE_STALE)
            reasons.append("no proposal and no reason recorded — the row may be "
                           "stale; re-render the proposal")

        # G7 — THE HUMAN'S REASON. Mandatory when the answer DIFFERS from what
        # the rule proposed, because that is exactly when a human overrode the
        # machine and the WHY is the only thing that makes the override
        # auditable. When the answer ACCEPTS the proposal the proposal's own
        # reason stands, so a blank is honest rather than missing.
        #
        # Measured need: the first submission recorded 11 decisions with NO
        # reason column at all, so why `crud` was rejected lived only in the TSV
        # and in chat — the audit trail could not answer "why".
        decision_reason = (row.get("decision_reason") or "").strip()
        differs = bool(proposed) and proposed != NA_ANSWER and answer != proposed
        if differs and not decision_reason:
            codes.append(CODE_REASON_MISSING)
            reasons.append(
                "answer %r DIFFERS from the proposed %r but decision_reason is "
                "empty. An override with no reason is an unattributable change "
                "of the applicable set." % (answer, proposed))

        if reasons:
            rejected.append({"skill_key": key, "answer": answer, "codes": codes,
                             "line_no": row.get("line_no"), "reasons": reasons})
            continue

        accepted.append({
            "skill_key": key,
            "rule_state": (row.get("proposal_reason") or "").strip()[:60],
            "proposed_tag": proposed or NA_ANSWER,
            "answer": answer,
            "decided_by": decider,
            "cite_ref": cite,
            "decision_reason": decision_reason or NA_ANSWER,
            "tsv_line": int(row.get("line_no") or 0),
        })

    return {"accepted": accepted, "rejected": rejected,
            "skipped_blank": skipped_blank, "registered_tags": sorted(known)}


def submit(conn: sqlite3.Connection, accepted: list[dict[str, Any]], *,
           source_tsv: Path | str, trace_id: str | None = None,
           recorded_as: str = "live",
           write_tags: bool = True) -> dict[str, Any]:
    """Commit ACCEPTED decisions. Append-only log first, the column second.

    Order matters: the log row is written before the column so a crash between
    the two leaves an AUDITED decision missing its effect, never a changed tag
    with no record of who made it.

    `recorded_as='retroactive'` + `write_tags=False` is the RETROACTIVE path: it
    appends a marked log row for a reason the original submit did not capture,
    and does NOT touch `skill_registry` — the tags are already correct, so
    re-writing them would add noise, not information.
    """
    if recorded_as not in ("live", "retroactive"):
        raise TagDecisionRefused(
            ["recorded_as %r is not 'live' or 'retroactive' — an unmarked row "
             "cannot be told apart from a live one" % recorded_as])
    if not accepted:
        raise TagDecisionRefused(
            ["nothing was accepted — refusing to open a write path for zero "
             "decisions (that is how an unattributed tag gets in)"])
    ensure_tag_log(conn)
    sf.ensure_schema(conn)
    tid = trace_id or new_trace_id()
    written, logged = 0, 0

    for a in accepted:
        row = conn.execute("SELECT capability_tags FROM skill_registry WHERE "
                           "skill_key=?", (a["skill_key"],)).fetchone()
        if not row:
            raise TagDecisionRefused(
                ["skill %r is not in skill_registry — a tag for an unregistered "
                 "skill resolves to nothing" % a["skill_key"]])
        before = str(row[0] or sf.NA_TAG)
        after = a["answer"]
        conn.execute(
            """INSERT INTO skill_capability_tag_log
                 (trace_id, skill_key, rule_state, proposed_tag, answer,
                  tags_before, tags_after, decided_by, cite_ref,
                  decision_reason, recorded_as, source_tsv, tsv_line)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, a["skill_key"], a["rule_state"], a["proposed_tag"], after,
             before, after, a["decided_by"], a["cite_ref"],
             a.get("decision_reason") or sf.NA_TAG, recorded_as,
             str(source_tsv), int(a["tsv_line"])),
        )
        logged += 1
        if write_tags:
            conn.execute("UPDATE skill_registry SET capability_tags=?, "
                         "updated_at=datetime('now') WHERE skill_key=?",
                         (after, a["skill_key"]))
            written += 1

    conn.commit()
    return {"trace_id": tid, "logged": logged, "tags_written": written,
            "recorded_as": recorded_as,
            "skills_with_a_tag": sf.tag_source_empty(conn)["skills_with_a_tag"],
            "log_rows": conn.execute(
                "SELECT COUNT(*) FROM skill_capability_tag_log").fetchone()[0]}


def history(conn: sqlite3.Connection, skill_key: str) -> list[dict[str, Any]]:
    """Every decision recorded for one skill, newest first. Append-only."""
    ensure_tag_log(conn)
    return [dict(r) for r in conn.execute(
        "SELECT trace_id, rule_state, proposed_tag, answer, tags_before, "
        "tags_after, decided_by, cite_ref, decision_reason, recorded_as, "
        "source_tsv, tsv_line, created_at "
        "FROM skill_capability_tag_log WHERE skill_key=? ORDER BY id DESC",
        (skill_key,))]


def reason_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which skills in the log still have NO reason recorded.

    A skill is COVERED when it has at least one log row whose
    `decision_reason` is not NA. This is the measurement QC-22 needs: the
    question is not "does the column exist" but "can the audit trail answer
    WHY for every decision it holds".
    """
    ensure_tag_log(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT skill_key, decision_reason, recorded_as FROM "
        "skill_capability_tag_log ORDER BY id")]
    covered = {r["skill_key"] for r in rows
               if str(r["decision_reason"] or "").strip()
               and str(r["decision_reason"]).strip() != sf.NA_TAG}
    all_skills = {r["skill_key"] for r in rows}
    return {"skills_in_log": len(all_skills),
            "skills_with_a_reason": len(covered),
            "skills_without_a_reason": sorted(all_skills - covered),
            "retroactive_rows": sum(1 for r in rows
                                    if r["recorded_as"] == "retroactive"),
            "live_rows": sum(1 for r in rows if r["recorded_as"] == "live"),
            "complete": not (all_skills - covered)}


def record_reasons(conn: sqlite3.Connection, *, answers_path: Path | str,
                   apply: bool = False) -> dict[str, Any]:
    """THE RETROACTIVE PATH: append a marked reason row, change NO tag.

    The log is append-only, so the rows written before `decision_reason`
    existed can never gain one — an UPDATE would destroy the record that the
    reason was missing. This appends a NEW row marked `retroactive`, which is
    what keeps it distinguishable from a live decision.

    It does NOT touch `skill_registry`: the tags are already correct, so
    re-writing them would add noise, not information.
    """
    parsed = parse(answers_path)
    assert_answers_found(parsed, answers_path)
    res = validate(conn, parsed)
    # Only rows that CARRIED a reason are recorded; a blank reason is not a
    # retroactive record, it is the absence this path exists to close.
    with_reason = [a for a in res["accepted"]
                   if a["decision_reason"] != sf.NA_TAG]
    out: dict[str, Any] = {
        "answers_file": str(answers_path),
        "accepted": len(res["accepted"]),
        "with_a_reason": len(with_reason),
        "rejected": res["rejected"],
        "skipped_blank": res["skipped_blank"],
        "writes": None,
    }
    if not with_reason:
        raise TagDecisionRefused(
            ["no row carries a decision_reason — a retroactive record with no "
             "reason is the absence this path exists to close"])
    if apply:
        out["writes"] = submit(conn, with_reason, source_tsv=answers_path,
                               recorded_as="retroactive", write_tags=False)
    return out


def submit_from_files(conn: sqlite3.Connection, *, answers_path: Path | str,
                      apply: bool = False) -> dict[str, Any]:
    """The whole path: read the input, validate, optionally commit.

    `apply=False` is a DRY RUN and reports exactly what would be written. It is
    the default, because the dangerous direction of a mistake here is writing.
    """
    parsed = parse(answers_path)
    # Refuse an empty/unreadable result BEFORE validating — otherwise a
    # destroyed worksheet reports "0 accepted, 0 rejected", which reads as a
    # clean run.
    assert_answers_found(parsed, answers_path)
    result = validate(conn, parsed)
    result["answers_file"] = str(answers_path)
    result["parse_errors"] = parsed["errors"]
    if apply:
        result["writes"] = submit(conn, result["accepted"],
                                  source_tsv=answers_path)
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
    ap.add_argument("--answers", default=str(sct.PROPOSAL_TSV))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--record-reason", action="store_true",
                    help="RETROACTIVE: append a marked reason row for a decision "
                         "already made. Changes NO tag.")
    ap.add_argument("--coverage", action="store_true",
                    help="report which skills in the log still have no reason")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.coverage:
            print(json.dumps(reason_coverage(conn), indent=2,
                             ensure_ascii=False))
            return
        if args.render:
            print(json.dumps(sct.write_proposal(conn, args.answers), indent=2,
                             ensure_ascii=False))
            return
        if args.record_reason:
            out = record_reasons(conn, answers_path=args.answers,
                                 apply=args.apply)
            print(json.dumps({
                "answers_file": out["answers_file"],
                "accepted": out["accepted"],
                "with_a_reason": out["with_a_reason"],
                "rejected": len(out["rejected"]),
                "skipped_blank": len(out["skipped_blank"]),
                "writes": out["writes"],
            }, indent=2, ensure_ascii=False))
            for r in out["rejected"]:
                print("  REJECTED %s (line %s): %s"
                      % (r["skill_key"], r["line_no"],
                         "; ".join(r["reasons"])))
            return
        out = submit_from_files(conn, answers_path=args.answers, apply=args.apply)
        brief = {
            "answers_file": out["answers_file"],
            "accepted": len(out["accepted"]),
            "rejected": len(out["rejected"]),
            "skipped_blank": len(out["skipped_blank"]),
            "parse_errors": out["parse_errors"],
            "writes": out["writes"],
        }
        print(json.dumps(brief, indent=2, ensure_ascii=False))
        if out["rejected"]:
            print("\nREJECTED:")
            for r in out["rejected"]:
                print("  %s (line %s): %s"
                      % (r["skill_key"], r["line_no"], "; ".join(r["reasons"])))
    finally:
        conn.close()


if __name__ == "__main__":
    main()