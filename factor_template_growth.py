# -*- coding: utf-8 -*-
"""factor_template_growth.py — the template GROWS from proof evidence.

WHY THIS EXISTS (the user, 2026-09-23):

    "proofed key factor + template -> generator can have that at the templat table"
    "can strong by time to time"

MEASURED before this: `FACTOR_TEMPLATE_SEED` was a fixed 9-row Python tuple. The
template could not grow, so "strong by time to time" had no mechanism. A proof
that revealed a factor needed a field the template did not have produced a
`failure_reason` that NOTHING READ.

THE LOOP, and it is a PROPOSAL loop, not a writer:

    proof_run.failure_reason  ->  a field the template lacks  ->  a PROPOSAL row
                                                                 (is_active=0)
                                                              ->  human decision
                                                              ->  the template grows

WHY IT NEVER AUTO-ACTIVATES
---------------------------
`is_active=0` is the DECLARED default for an unproven row
(`activation_gate.py:1-40`; `_proof_terminology_registry.py:126` asserts it). A
template row that activated itself would change what EVERY future factor must
carry, decided by a model reading a failure message. That is the "auto-write"
defect the human-decision path exists to prevent.

WHAT IT REFUSES
---------------
  * a proposal with no `cite_ref` — no citation, no proposal
  * a proposal for a field that ALREADY exists — a duplicate is not growth
  * a proposal whose evidence is not a real `proof_run` row
  * activating anything — this module has no activation path at all

Run:
    .\\.venv\\Scripts\\python.exe factor_template_growth.py --propose
    .\\.venv\\Scripts\\python.exe factor_template_growth.py --propose --apply
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

# A field name is a slug. The proposal's field name is DERIVED from the failure
# text, so it must be normalised — and a name that normalises to nothing is
# REFUSED rather than stored as an empty row.
SLUG_RE = re.compile(r"[^a-z0-9]+")


def next_sort_order(conn: sqlite3.Connection) -> int:
    """Where a proposal sorts: AFTER every field the template already declares.

    DERIVED, not a literal. MEASURED: the first version wrote a bare magic
    number meaning "last", and `hardcode_scan.SCREEN_PAIR` flagged it as a
    screen-dimension literal — a false positive, but the right signal: a number
    that means "last" goes stale the moment the template grows past it. Reading
    the current maximum cannot go stale, and a proposal can never displace a
    field the system depends on.
    """
    row = conn.execute("SELECT MAX(sort_order) m FROM factor_template").fetchone()
    return int(row["m"] or 0) + 1

# Words that appear in a failure message but name no FIELD. A proposal built from
# one of these would add a template row called `the` or `not`.
STOPWORDS = frozenset((
    "the", "a", "an", "and", "or", "not", "no", "is", "was", "were", "be",
    "been", "to", "of", "in", "on", "for", "with", "without", "it", "its",
    "this", "that", "these", "those", "but", "if", "then", "than", "as", "at",
    "by", "from", "has", "have", "had", "does", "did", "do", "can", "could",
    "should", "would", "must", "may", "might", "will", "shall", "there",
    "here", "when", "where", "which", "who", "what", "why", "how", "all",
    "any", "some", "none", "one", "two", "more", "less", "same", "other",
    "only", "also", "just", "very", "too", "so", "such", "into", "out",
    "up", "down", "over", "under", "again", "further", "once", "each",
    "few", "many", "most", "own", "about", "after", "before", "between",
    "during", "through", "above", "below", "off", "because", "while",
    "expected", "actual", "got", "found", "missing", "empty", "null",
    "none", "true", "false", "yes", "value", "result", "answer", "output",
))


class GrowthRefused(RuntimeError):
    """Raised when a proposal would add a row nothing can justify."""


def _slug(text: str) -> str:
    """A field name from free text. Empty when nothing usable remains."""
    s = SLUG_RE.sub("_", str(text or "").lower()).strip("_")
    parts = [p for p in s.split("_") if p and p not in STOPWORDS and len(p) > 2]
    return "_".join(parts[:4])


def existing_fields(conn: sqlite3.Connection) -> set[str]:
    """Every field the template already has, ACTIVE OR NOT.

    A proposal for a field that already exists is not growth — it is a
    duplicate. Including the INACTIVE rows is the point: a proposal that was
    already made and not yet decided must not be proposed again.
    """
    return {str(r[0]) for r in conn.execute(
        "SELECT field_name FROM factor_template")}


def refusal_evidence(conn: sqlite3.Connection) -> list[dict]:
    """The factors the MEASUREMENT layer REFUSED, with the reason it gave.

    WHY THIS REPLACED `proof_run.failure_reason` — and it is a recorded failure,
    not a preference.

    THE FIRST VERSION read `proof_run.failure_reason`. MEASURED, and it produced
    GARBAGE:

        "flow verdict != oracle"                       -> flow_verdict_oracle
        "false YES (oracle=NO) value='{\"MODEL\"...}"   -> oracle_model_abc_task
        "false NO (oracle=YES) value='999999999999999'" -> oracle_999999999999999

    Raising the evidence threshold did NOT fix it: `oracle_999999999999999`
    appeared in 88 failures and `oracle_session_abc_model` in 40. The threshold
    was treating a symptom.

    THE ROOT CAUSE IS A CATEGORY ERROR. `failure_reason` says WHY A ROUND FAILED
    (the model answered wrong). It does NOT say WHAT PROPERTY THE TEMPLATE LACKS.
    The words in it are the TEST PAYLOAD (`oracle`, `session`, `abc`, `999...`),
    not field names. Deriving a template field from it is the same defect family
    as reading `taxonomy_path` as a capability tag — a field read for something
    it does not hold.

    THE CORRECT INPUT is the MEASUREMENT layer's own refusal reasons, because a
    refusal NAMES the missing property directly:

        "metric_unit 'count' names NO SUBJECT"
        "metric_kind 'vibes' is not one of boolean, count, pct, score_0_100"

    A template field is a property EVERY factor must carry, so the evidence for
    one is a factor that could not be measured WITHOUT it. That is exactly what
    these refusals are.
    """
    import factor_first_principle as fp

    out: list[dict] = []
    for r in conn.execute("SELECT * FROM skill_factor_registry "
                          "ORDER BY sort_order"):
        d = dict(r)
        verdict = fp.audit_factor(d, conn)
        if verdict["measurable"]:
            continue
        for reason in verdict["reasons"]:
            out.append({"factor_key": d["factor_key"], "reason": reason,
                        "unit": d.get("metric_unit"),
                        "kind": d.get("metric_kind")})
    return out


def _field_from_reason(reason: str) -> str:
    """The FIELD a refusal reason names, or "" when it names none.

    A refusal reason is written by the gate, so it names the field it refused:
    `metric_unit ...` / `metric_kind ...` / `metric_target ...`. The field is
    therefore READ from the reason's own first token, not guessed from its words.

    MEASURED, and this is why the parse is strict: the first version slugged the
    WHOLE sentence, so `"metric_unit 'count' names NO SUBJECT"` became
    `metric_unit_count_names_subject` — a field name that is a sentence. The
    field is the FIRST token when that token is a known template field.
    """
    import skill_factor as sf
    try:
        known = set(sf.template_fields(_CONN_FOR_FIELDS[0])) \
            if _CONN_FOR_FIELDS else set()
    except Exception:
        known = set()
    first = str(reason or "").strip().split(" ")[0].strip("'\"")
    if first in known:
        return first
    return ""


# A module-level slot so `_field_from_reason` can read the template fields
# without threading a connection through every call. Set by `propose()`.
_CONN_FOR_FIELDS: list[sqlite3.Connection] = []


def propose(conn: sqlite3.Connection, *, apply: bool = False,
            min_evidence: int = 2) -> dict[str, Any]:
    """Propose template rows from MEASUREMENT REFUSALS. NEVER activates anything.

    Returns `{ok, evidence, proposals, refused, applied}`. A proposal carries a
    `cite_ref` naming the factor it came from, so a reader can check it.

    `min_evidence` is the quality gate: a property that ONE factor lacks may be
    that factor's own defect, while a property SEVERAL factors lack is a gap in
    the TEMPLATE. Below the threshold the candidate is REFUSED and REPORTED with
    its count — never silently dropped.
    """
    _CONN_FOR_FIELDS[:] = [conn]
    have = existing_fields(conn)
    ev = refusal_evidence(conn)

    counts: dict[str, int] = {}
    first: dict[str, dict] = {}
    refused: list[dict[str, Any]] = []
    for r in ev:
        field = _field_from_reason(r["reason"])
        if not field:
            refused.append({"factor_key": r["factor_key"],
                            "reason": "the refusal names no template field",
                            "text": str(r["reason"])[:70]})
            continue
        if field in have:
            refused.append({"factor_key": r["factor_key"], "field_name": field,
                            "reason": "the template ALREADY has this field — a "
                                      "duplicate is not growth"})
            continue
        counts[field] = counts.get(field, 0) + 1
        first.setdefault(field, r)

    proposals: list[dict[str, Any]] = []
    for field, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        if n < int(min_evidence):
            refused.append({
                "field_name": field, "seen_in": n,
                "reason": ("%d factor(s) lack it, below the evidence threshold "
                           "%d — one factor's defect is not a template gap"
                           % (n, int(min_evidence)))})
            continue
        r = first[field]
        proposals.append({
            "field_name": field,
            "kind": "text",
            "is_required": 0,
            "why": ("%d factor(s) could not be measured without it; first: %s"
                    % (n, str(r["reason"])[:120])),
            "rule": "NA",
            "cite_ref": "factor:%s" % r["factor_key"],
            "seen_in": n,
            "evidence": {"factor_key": r["factor_key"], "unit": r["unit"],
                         "kind": r["kind"]},
        })

    applied = 0
    if apply:
        order = next_sort_order(conn)
        for p in proposals:
            # NO CITATION, NO PROPOSAL. The same rule the register enforces.
            if not str(p.get("cite_ref") or "").strip():
                raise GrowthRefused(
                    "proposal %r has no cite_ref — no citation, no proposal"
                    % p.get("field_name"))
            # `is_active=0` is HARD-CODED here on purpose: this module has no
            # path that writes an active row. A template row that activated
            # itself would change what every future factor must carry, decided
            # by a model reading a failure message.
            conn.execute(
                "INSERT INTO factor_template (field_name, kind, is_required, "
                "why, rule, sort_order, is_active) VALUES (?,?,?,?,?,?,0) "
                "ON CONFLICT (field_name) DO NOTHING",
                (p["field_name"], p["kind"], int(p["is_required"]), p["why"],
                 p["rule"], order))
            order += 1
            applied += 1
        conn.commit()

    return {"ok": True, "applied": bool(apply), "evidence": len(ev),
            "min_evidence": int(min_evidence),
            "proposals": proposals, "refused": refused, "applied_n": applied,
            "note": ("a proposal is written is_active=0 and is NOT activated by "
                     "this module — activation is a human decision")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Propose factor_template rows from proof evidence")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="write the proposals as is_active=0 rows")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--min-evidence", type=int, default=2,
                    help="how many DISTINCT factors must lack a property before "
                         "it is proposed as a template field")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if not args.propose:
            ap.print_help()
            return 0
        out = propose(conn, apply=args.apply, min_evidence=args.min_evidence)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
