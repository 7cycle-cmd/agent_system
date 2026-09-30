"""metric_kind_derive.py -- the metric-kind VOCABULARY, DERIVED from EVIDENCE.

THE USER'S INSTRUCTION (verbatim, 2026-09-24):

    "can by evidence to have logic generator to get the answer"

THE PROBLEM THIS SOLVES
-----------------------
`factor_template.metric_kind` carries the rule `must_be_known_kind`, whose `why`
is "a metric that cannot be scored is decorative". The rule needs a list of known
kinds. MEASURED: `template_conformance.known_metric_kinds` reports
`KIND_VOCABULARY_ABSENT`, because it looks only for a TABLE or a CODE literal --
so the rule has no subject and can NEVER fail.

THE VOCABULARY IS DERIVABLE FROM EVIDENCE, and this is the measurement:

    metric_kind IS the PREFIX of metric_unit.

        count        -> "count of missing required metadata fields"      (26)
        pct          -> "pct of entities matching the Ontology definition" (10)
        score_0_100  -> "score_0_100 of test suite stability"             (2)
        boolean      -> "boolean of hard delete blocked without a Check ID" (2)

So there are TWO INDEPENDENT SOURCES for the same fact -- the `metric_kind`
column and the unit's own prefix -- and a kind is CONFIRMED only when they AGREE.
That is what makes this a MEASUREMENT rather than a declaration: one source
cannot be its own evidence.

MEASURED: they agree for 38 of 40 factors. The 2 that disagree are EXACTLY the 2
that `factor_first_principle.audit_registry` calls NOT measurable:

    valid_phone_number_judgment  kind=pct   unit="correct judgments"      (no prefix)
    runtime_liveness_evidence    kind=count unit="seconds since ..."      (no prefix)

So the derivation and the auditor AGREE, from different directions. That
agreement is the evidence, and it is reported rather than asserted.

NOTHING IS HARDCODED. The kinds are read from the register, the prefixes are read
from the units, and an empty register is REFUSED rather than read as "no
disputes".
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The register that carries the two sources. Named ONCE.
FACTOR_TABLE = "skill_factor_registry"

# A unit's prefix is `<kind> of <subject>` or `<kind> on <subject>`. The
# separator is READ from the data, not assumed: the pattern accepts the
# separators the register actually uses.
PREFIX_RE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s+(?:of|on|for)\s+(.+)$",
                       re.IGNORECASE)


class KindDeriveError(Exception):
    """Raised when the vocabulary cannot be derived."""


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or os.path.join(BASE_DIR, "agent.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone())


def unit_prefix(unit: str) -> str:
    """The KIND a unit declares in its own text, or '' when it declares none.

    MEASURED: `count of X` -> `count`; `seconds since X` -> '' (no separator, so
    no prefix). Returning '' is deliberate: a caller can then tell "this unit
    declares no kind" from "this unit declares a kind I do not know".
    """
    m = PREFIX_RE.match(str(unit or ""))
    return m.group(1).lower() if m else ""


def unit_subject(unit: str) -> str:
    """The SUBJECT a unit names after its kind, or '' when it names none."""
    m = PREFIX_RE.match(str(unit or ""))
    return m.group(2).strip() if m else ""


def derive(conn: sqlite3.Connection) -> dict[str, Any]:
    """Derive the kind vocabulary from the register, with its evidence.

    Returns the CONFIRMED kinds (both sources agree), the DISPUTED rows (they
    disagree), and the counts. An EMPTY register is REFUSED, because an empty
    result would otherwise read as "no disputes" -- the same defect as a detector
    that cannot detect.
    """
    if not _table_exists(conn, FACTOR_TABLE):
        raise KindDeriveError("%s does not exist" % FACTOR_TABLE)
    # `is_active` IS READ, NOT ASSUMED (added 2026-09-27).
    #
    # MEASURED DEFECT THIS CLOSES: this read EVERY row, while the INDEPENDENT
    # auditor `factor_first_principle.audit_registry()` now classifies an
    # INACTIVE factor separately (a superseded factor is HISTORY, not a live
    # defect). So the two "independent directions" measured DIFFERENT
    # populations and `_proof_metric_kind_derive.py` R-30a went RED:
    # `audit=[] derive=['valid_phone_number_judgment']`.
    #
    # A row that does not carry the column is treated as ACTIVE, because an
    # unknown state must not be silently excused.
    try:
        rows = conn.execute(
            "SELECT factor_key, metric_kind, metric_unit, is_active FROM %s"
            % FACTOR_TABLE).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(
            "SELECT factor_key, metric_kind, metric_unit FROM %s"
            % FACTOR_TABLE).fetchall()
    if not rows:
        raise KindDeriveError(
            "%s is EMPTY, so the vocabulary cannot be derived and an empty result "
            "must not read as 'no disputes'" % FACTOR_TABLE)

    confirmed: dict[str, dict[str, Any]] = {}
    disputed: list[dict[str, Any]] = []
    no_prefix: list[dict[str, Any]] = []
    superseded: list[dict[str, Any]] = []
    for r in rows:
        kind = str(r["metric_kind"] or "").strip().lower()
        unit = str(r["metric_unit"] or "")
        # AN INACTIVE ROW IS REPORTED SEPARATELY, NOT HIDDEN. Dropping it would
        # make a real problem invisible; counting it as a live defect would
        # report a superseded factor forever. It is CLASSIFIED.
        try:
            active = int(r["is_active"] or 0) == 1
        except (IndexError, KeyError):
            active = True
        prefix = unit_prefix(unit)
        if not prefix:
            row = {"factor_key": r["factor_key"], "metric_kind": kind,
                   "metric_unit": unit,
                   "why": "the unit declares NO kind prefix"}
            (no_prefix if active else superseded).append(row)
            continue
        if prefix == kind:
            e = confirmed.setdefault(kind, {"kind": kind, "n": 0,
                                            "example_unit": unit})
            e["n"] += 1
        else:
            row = {"factor_key": r["factor_key"], "metric_kind": kind,
                   "unit_prefix": prefix, "metric_unit": unit,
                   "why": ("the column says %r but the unit's own prefix "
                           "says %r" % (kind, prefix))}
            (disputed if active else superseded).append(row)
    return {
        "ok": True,
        "source": "%s.metric_kind vs the unit's own prefix" % FACTOR_TABLE,
        "rows": len(rows),
        "kinds": sorted(confirmed),
        "confirmed": [confirmed[k] for k in sorted(confirmed)],
        "disputed": disputed,
        "no_prefix": no_prefix,
        "superseded": superseded,
        "confirmed_n": sum(e["n"] for e in confirmed.values()),
        "disputed_n": len(disputed),
        "no_prefix_n": len(no_prefix),
        "superseded_n": len(superseded),
        "why": ("a kind is CONFIRMED only when the `metric_kind` column and the "
                "unit's own prefix AGREE, so one source cannot be its own "
                "evidence. An INACTIVE row is reported in `superseded`, not "
                "counted as a live defect: a superseded factor is HISTORY."),
    }


def is_known_kind(conn: sqlite3.Connection, kind: str) -> dict[str, Any]:
    """Is `kind` a CONFIRMED kind? The answer `must_be_known_kind` needs.

    REFUSES an empty kind, and reports an UNCONFIRMED kind as such rather than
    defaulting to True -- a rule that cannot fail is the defect being removed.
    """
    text = str(kind or "").strip().lower()
    if not text:
        return {"ok": False, "kind": text, "reason": "no kind given"}
    d = derive(conn)
    if text in d["kinds"]:
        return {"ok": True, "kind": text, "known": True,
                "evidence": [e for e in d["confirmed"] if e["kind"] == text][0]}
    return {"ok": True, "kind": text, "known": False,
            "reason": ("%r is NOT a confirmed kind; the confirmed kinds are %s"
                       % (text, d["kinds"])),
            "disputed_rows": [r for r in d["disputed"]
                              if r["metric_kind"] == text]}


def check_registry(conn: sqlite3.Connection) -> dict[str, Any]:
    """Run `must_be_known_kind` against the register, from the DERIVED vocabulary.

    This is the rule made ENFORCEABLE. It reports the rows that FAIL it, so the
    rule can now fail -- which is what "the rule has no subject" meant it could
    not do.

    AN INACTIVE ROW IS NOT A LIVE FAILURE (added 2026-09-27). MEASURED: this
    counted `disputed + no_prefix` over EVERY row, so a SUPERSEDED factor
    (`valid_phone_number_judgment`, `is_active=0`) was reported as a live failure
    forever. `derive()` now classifies it into `superseded`, and this reports
    that count SEPARATELY -- dropping it would make a real problem invisible.
    """
    d = derive(conn)
    failing = list(d["disputed"]) + list(d["no_prefix"])
    return {"ok": True, "rule": "must_be_known_kind",
            "vocabulary": d["kinds"], "vocabulary_source": d["source"],
            "rows": d["rows"], "passing": d["confirmed_n"],
            "failing": failing, "failing_n": len(failing),
            "superseded": d.get("superseded") or [],
            "superseded_n": d.get("superseded_n", 0),
            "state": "ENFORCEABLE" if d["kinds"] else "SUBJECT_ABSENT",
            "why": ("the vocabulary is DERIVED from the register, so the rule now "
                    "has a subject and CAN fail. An INACTIVE row is reported in "
                    "`superseded`, not counted as a live failure: a superseded "
                    "factor is HISTORY.")}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--check" in args:
            print(json.dumps(check_registry(conn), indent=2, ensure_ascii=False))
        elif "--is-known" in args:
            i = args.index("--is-known")
            print(json.dumps(is_known_kind(conn, args[i + 1]),
                             indent=2, ensure_ascii=False))
        else:
            print(json.dumps(derive(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
