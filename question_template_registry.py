# -*- coding: utf-8 -*-
"""question_template_registry.py — THE QUESTION TEMPLATE, keyed by FACTOR.

SCOPE B of `qc_evidence/plan_GITHUB.CONSULTANT.UPGRADE.md` (APPROVED 2026-09-28).

THE HUMAN, verbatim:

    "what is the key for question to find at github? = factor = what?"
    "can have table to record this key / factor will find best 3 at github = ?"
    "this is question template!!! and it can help for 80% case"

WHY A NEW TABLE AND NOT AN EXTENSION OF `question_template_factor`
-----------------------------------------------------------------
MEASURED: `question_template_factor` (6 rows) is a **SCORING JOIN** —
`(combo_id, factor_id, scoring)` — and its `combo_id` resolves in `prompt_combo`
(36 rows), NOT in a missing parent. It carries NO question text, which is exactly
the gap the human named.

Adding `question_text` to a JOIN table would put a question on a PAIR
(combo × factor), so the same question would be re-stated once per combo — 36
copies of one sentence, drifting apart. **A question belongs to the FACTOR**
(D2: the factor IS the key), so it gets its own register keyed by `factor_key`,
and the existing 6 rows are left UNTOUCHED.

THE FOUR COLUMNS, and what each is FOR
--------------------------------------
* `question_text`  — the question, in words.
* `answer_key`     — what a GOOD answer looks like, so the answer can be judged.
* `asks_about`     — **the FACT the answer reveals.** This is the load-bearing
  column: a question that reveals no fact is not a question, it is a topic.
* `covers_pct`     — how much of the case the question covers. The human said
  "80% case"; that number is MEASURED and REPORTED, never asserted.

READ-ONLY BY DEFAULT. `upsert_template` writes; nothing else does.
"""
from __future__ import annotations

import argparse
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

DEFAULT_DB = BASE / "agent.db"
NA = "NA"

# ---------------------------------------------------------------------------
# SCOPE Q — THE QUESTION LEARNS laya's SHAPE.
#
# THE HUMAN (2026-09-28), verbatim:
#   "you have upgrade the question flow patterm by understand laya, and it is
#    improved, is it correct?"
#
# MEASURED, AND THE ANSWER WAS NO: the schema was UNCHANGED. SCOPE P added 5
# ROWS, not a shape. The 5 rows carried `answer_key = "YES|NO"` as a FREE
# STRING, so a reader could not tell a yes/no from a 3-way choice, and the
# options carried no MEANING — which is exactly what `laya` needs to calibrate.
#
# laya's question shape, from the model card:
#
#     {"department": {"type": "choice", "instructions": "...",
#                     "criteria": {"billing": "invoices, payments, refunds",
#                                  "technical": "bugs, outages, system errors"}},
#      "urgency":    {"type": "score", "instructions": "...",
#                     "criteria": ["not urgent", "soon", "blocking"]},
#      "churn_risk": {"type": "noul", "instructions": "..."}}
#
# THE LOAD-BEARING GAP IS `criteria`. Without it an answer is a LABEL with no
# meaning, so it cannot be calibrated — and calibration is the whole reason to
# use laya at all.
#
# WITH THESE TWO COLUMNS, THE SAME ROW FEEDS EITHER ENGINE:
#   laya needs {type, instructions, criteria}  <- answer_type, question_text, criteria_json
#   the 7B needs a question + the good answer  <- question_text, answer_key
# ---------------------------------------------------------------------------
# `text` IS THE DEFAULT, AND THAT IS A BACKWARD-COMPATIBILITY DECISION.
#
# MEASURED: SCOPE B's proof passes `answer_key="YES when every claim has a
# path:line or a command"` — a DESCRIPTION of a good answer, not an option list.
# With `choice` as the default, `_options_of` split that on `|`, found ONE
# option, and refused it with `MISSING_OPTION_CRITERION` — so a schema change
# broke every existing caller. A migration that breaks its callers is not
# additive.
#
# `text` is also HONEST: it says "this answer is free-form", which is exactly
# what the old schema meant. And it is USEFUL: a `text` question CANNOT be asked
# of `laya` (which answers choice/score/noul only), so the type tells a reader
# which engine can answer the question.
ANSWER_TYPES = ("choice", "score", "noul", "text")

# The types `laya` can answer. A `text` question is for a text model.
LAYA_ANSWER_TYPES = ("choice", "score", "noul")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS question_template_registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    template_key  TEXT    NOT NULL UNIQUE,
    factor_key    TEXT    NOT NULL,
    question_text TEXT    NOT NULL,
    answer_key    TEXT    NOT NULL,
    asks_about    TEXT    NOT NULL,
    covers_pct    REAL,
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- SCOPE Q. `choice` = pick one of `answer_key`; `score` = place on an
    -- ORDERED scale; `noul` = yes/no, where the PROBABILITY is the answer;
    -- `text` = a free-form answer (the default, and the only type `laya`
    -- cannot answer).
    answer_type   TEXT    NOT NULL DEFAULT 'text'
                  CHECK (answer_type IN ('choice', 'score', 'noul', 'text')),
    -- The MEANING of each option, so an answer can be calibrated. A JSON object
    -- for `choice` (option -> meaning), a JSON array for `score` (the ordered
    -- scale), and `{}` for `noul` (which needs none).
    criteria_json TEXT    NOT NULL DEFAULT '{}'
    -- 🔴 NO FOREIGN KEY ON `factor_key`, AND IT WAS MEASURED, NOT PREFERRED
    -- (2026-09-29). This declared
    --     FOREIGN KEY (factor_key) REFERENCES skill_factor_registry (factor_key)
    -- and it could NEVER work: the parent's only key on `factor_key` is
    -- `uq_factor_scope_key`, an EXPRESSION index on
    -- `(COALESCE(skill_key,''), factor_key)`, and SQLite refuses an expression
    -- index as an FK parent key (docs s3: the parent key must be a named column
    -- "exact match to the columns of a single UNIQUE index").
    --
    -- Effect: with `PRAGMA foreign_keys=ON` (91 places in this repo set it) ANY
    -- insert raised `foreign key mismatch` -- even with a VALID `factor_key`, so
    -- the constraint enforced NOTHING and only broke writers.
    --
    -- THE POLICY (human-locked): a native FK is only allowed on the parent's
    -- PRIMARY KEY, and only for a MANDATORY + LOAD-BEARING reference. This table
    -- is neither: MEASURED, ZERO production modules read it and only a seeder
    -- writes it. So it follows factor 8 `lazy_fk_ontology_relation` -- no native
    -- FK, and the tie is checked at the business layer (`upsert_template`
    -- REFUSES `UNKNOWN_FACTOR` by looking the factor up itself).
    --
    -- The parent lookup that replaces the FK is in `upsert_template`.
)
"""

# The columns SCOPE Q adds, so an older DB can be migrated ADDITIVELY.
_Q_COLUMNS = (
    ("answer_type", "TEXT NOT NULL DEFAULT 'text'"),
    ("criteria_json", "TEXT NOT NULL DEFAULT '{}'"),
)


def _options_of(answer_key: str) -> list[str]:
    """The options a `choice` answer_key declares, split on `|`.

    MEASURED: the 5 SCOPE P rows use `"YES|NO"`, so `|` is the separator the
    register already uses. A single-option answer_key is one option, not zero.
    """
    s = str(answer_key or "").strip()
    if not s:
        return []
    return [p.strip() for p in s.split("|") if p.strip()]


def check_shape(answer_type: str, answer_key: str,
                criteria_json: Any) -> dict[str, Any]:
    """The SCOPE Q rules. Returns a verdict; NEVER raises.

    A column nobody validates is a column nobody can trust, so the rules are
    enforced at the WRITE SITE:

      * an unknown `answer_type` matches nothing SILENTLY -> REFUSED
      * a `choice` question MUST declare criteria for EVERY option in its
        answer_key. An option with no meaning is a label, and a label cannot be
        calibrated.
      * a `score` question MUST declare an ORDERED criteria list (the scale).
      * a `noul` question needs NO criteria — the probability IS the answer.
      * a `text` question needs NO criteria — the answer is free-form, and the
        type itself says `laya` cannot answer it.
    """
    at = str(answer_type or "").strip()
    if at not in ANSWER_TYPES:
        return {"ok": False, "code": "UNKNOWN_ANSWER_TYPE",
                "message": ("answer_type %r is not one of %s — an undefined "
                            "type matches nothing silently"
                            % (at, ", ".join(ANSWER_TYPES)))}
    raw = criteria_json
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            parsed: Any = {}
        else:
            try:
                import json as _json
                parsed = _json.loads(s)
            except Exception as exc:
                return {"ok": False, "code": "BAD_CRITERIA_JSON",
                        "message": "criteria_json is not valid JSON: %s" % exc}
    else:
        parsed = raw if raw is not None else {}

    if at in ("noul", "text"):
        # A yes/no needs no criteria: the probability IS the answer. A free-form
        # answer needs none either: there is no option to give a meaning to.
        return {"ok": True, "answer_type": at, "criteria": parsed}

    if at == "score":
        if not isinstance(parsed, list) or not parsed:
            return {"ok": False, "code": "MISSING_SCALE",
                    "message": ("a `score` question needs an ORDERED criteria "
                                "list (the scale), got %r" % (parsed,))}
        return {"ok": True, "answer_type": at, "criteria": parsed}

    # `choice`: every option must carry a meaning.
    if not isinstance(parsed, dict):
        return {"ok": False, "code": "BAD_CRITERIA_SHAPE",
                "message": ("a `choice` question needs a criteria OBJECT "
                            "(option -> meaning), got %r" % (parsed,))}
    opts = _options_of(answer_key)
    if not opts:
        return {"ok": False, "code": "MISSING_ANSWER_KEY",
                "message": "a `choice` question needs options in answer_key"}
    missing = [o for o in opts if not str(parsed.get(o) or "").strip()]
    if missing:
        return {"ok": False, "code": "MISSING_OPTION_CRITERION",
                "message": ("these options carry no meaning: %s — an option "
                            "with no meaning is a label, and a label cannot be "
                            "calibrated" % ", ".join(missing)),
                "missing": missing}
    return {"ok": True, "answer_type": at, "criteria": parsed}


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table, and ADDITIVELY migrate an older one.

    SCOPE Q: `answer_type` and `criteria_json` were added after the table
    existed, so an older DB needs the columns added. `ALTER TABLE ... ADD COLUMN`
    with a DEFAULT is ADDITIVE — no row is lost — and the default is chosen so
    the migration cannot leave a row in a state the rules would refuse.
    """
    conn.execute(_CREATE_SQL)
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(question_template_registry)")}
    for name, decl in _Q_COLUMNS:
        if name not in cols:
            conn.execute("ALTER TABLE question_template_registry ADD COLUMN "
                         "%s %s" % (name, decl))
    conn.commit()
    return {"ok": True}


def upsert_template(conn: sqlite3.Connection, *, template_key: str,
                    factor_key: str, question_text: str, answer_key: str,
                    asks_about: str, cite_ref: str,
                    covers_pct: float | None = None,
                    is_active: int = 1,
                    answer_type: str = "text",
                    criteria_json: Any = None) -> dict[str, Any]:
    """Register ONE question template. Returns a verdict; NEVER raises.

    REFUSES:
      * `UNKNOWN_FACTOR`   -- the factor IS the key (D2). A question keyed on a
                              factor that does not exist can never be asked.
      * `MISSING_QUESTION` -- a template with no question is a heading.
      * `MISSING_ANSWER_KEY` -- a question whose good answer is unstated cannot
                              be judged, so its answer is not evidence.
      * `MISSING_ASKS_ABOUT` -- **the load-bearing refusal.** A question that
                              does not name the FACT its answer reveals is a
                              TOPIC, not a question. This is the defect the
                              human named: a question that "helps for 80% of
                              cases" must say WHICH fact it settles.
      * `MISSING_CITE_REF` -- no citation, no row.
      * `BAD_COVERS_PCT`   -- a coverage outside 0..100 is not a percentage.
      * `UNKNOWN_ANSWER_TYPE` / `MISSING_OPTION_CRITERION` / `MISSING_SCALE`
        (SCOPE Q) -- the answer's SHAPE and the MEANING of each option. See
        `check_shape`: a `choice` option with no meaning is a label, and a label
        cannot be calibrated.
    """
    for field, value in (("template_key", template_key),
                         ("factor_key", factor_key),
                         ("question_text", question_text),
                         ("answer_key", answer_key),
                         ("asks_about", asks_about),
                         ("cite_ref", cite_ref)):
        if not str(value or "").strip():
            return {"ok": False, "code": "MISSING_%s" % field.upper(),
                    "message": "a question template needs a %s" % field}
    if str(asks_about).strip() == NA:
        return {"ok": False, "code": "MISSING_ASKS_ABOUT",
                "message": "`asks_about` must name the FACT the answer reveals. "
                           "A question that reveals no fact is a topic, not a "
                           "question."}
    cp = None
    if covers_pct not in (None, "", NA):
        try:
            cp = float(covers_pct)
        except (TypeError, ValueError):
            return {"ok": False, "code": "BAD_COVERS_PCT",
                    "message": "covers_pct must be a number, got %r"
                               % (covers_pct,)}
        if not (0.0 <= cp <= 100.0):
            return {"ok": False, "code": "BAD_COVERS_PCT",
                    "message": "covers_pct must be within 0..100, got %r" % cp}
    # SCOPE Q: the answer's SHAPE and the MEANING of each option, checked at the
    # WRITE SITE. A column nobody validates is a column nobody can trust.
    #
    # THE FACTOR IS CHECKED FIRST, and that is a MEASURED fix: the shape check
    # used to run before it, so a template keyed on an UNKNOWN factor was refused
    # with `MISSING_OPTION_CRITERION` — a refusal about the WRONG defect. The
    # factor IS the key (D2), so a question keyed on a factor that does not exist
    # can never be asked, and that is the more fundamental refusal.
    try:
        ensure_schema(conn)
        f = conn.execute("SELECT factor_key FROM skill_factor_registry WHERE "
                         "factor_key=?", (str(factor_key),)).fetchone()
        if not f:
            return {"ok": False, "code": "UNKNOWN_FACTOR",
                    "message": "no skill_factor_registry row for factor_key=%r. "
                               "The factor IS the key (D2)." % factor_key}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    shape = check_shape(answer_type, answer_key, criteria_json)
    if not shape.get("ok"):
        return {"ok": False, "code": shape.get("code"),
                "message": shape.get("message"),
                "missing": shape.get("missing")}
    import json as _json
    criteria_text = _json.dumps(shape["criteria"], ensure_ascii=False,
                                sort_keys=True)
    try:
        conn.execute(
            "INSERT INTO question_template_registry "
            "(template_key, factor_key, question_text, answer_key, asks_about, "
            " covers_pct, cite_ref, is_active, answer_type, criteria_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(template_key) DO UPDATE SET "
            "factor_key=excluded.factor_key, "
            "question_text=excluded.question_text, "
            "answer_key=excluded.answer_key, asks_about=excluded.asks_about, "
            "covers_pct=excluded.covers_pct, cite_ref=excluded.cite_ref, "
            "is_active=excluded.is_active, answer_type=excluded.answer_type, "
            "criteria_json=excluded.criteria_json, "
            "updated_at=CURRENT_TIMESTAMP",
            (str(template_key), str(factor_key), str(question_text),
             str(answer_key), str(asks_about), cp, str(cite_ref),
             1 if is_active else 0, shape["answer_type"], criteria_text))
        conn.commit()
        row = conn.execute("SELECT id FROM question_template_registry WHERE "
                           "template_key=?", (str(template_key),)).fetchone()
        return {"ok": True, "id": int(row["id"]),
                "template_key": str(template_key),
                "factor_key": str(factor_key), "covers_pct": cp,
                "answer_type": shape["answer_type"],
                "criteria": shape["criteria"]}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def templates_for(conn: sqlite3.Connection, factor_key: str) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM question_template_registry WHERE factor_key=? AND "
        "is_active=1 ORDER BY id", (str(factor_key),))]


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many ACTIVE factors have a question — a RATIO, never a count.

    The human's number was "80% case". This MEASURES it and reports it; it does
    not assert it. A factor with no question is REPORTED, because a factor nobody
    can ask about is a rule nobody can find a source for.
    """
    ensure_schema(conn)
    factors = [str(r[0]) for r in conn.execute(
        "SELECT factor_key FROM skill_factor_registry WHERE is_active=1")]
    have = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT factor_key FROM question_template_registry "
        "WHERE is_active=1")}
    covered = [f for f in factors if f in have]
    return {"factors": len(factors), "with_question": len(covered),
            "without_question": len(factors) - len(covered),
            "covered_pct": round(100.0 * len(covered) / len(factors), 2)
                           if factors else 0.0,
            "missing": [f for f in factors if f not in have]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--factor", default="")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.init:
            print(ensure_schema(conn))
        if a.factor:
            for t in templates_for(conn, a.factor):
                print("  %-28s %s" % (t["template_key"], t["question_text"][:70]))
        if a.coverage:
            c = coverage(conn)
            print("factors=%d with_question=%d without=%d covered_pct=%.2f"
                  % (c["factors"], c["with_question"], c["without_question"],
                     c["covered_pct"]))
        if not any((a.init, a.coverage, a.factor)):
            print("nothing to do; try --init --coverage --factor <key>")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
