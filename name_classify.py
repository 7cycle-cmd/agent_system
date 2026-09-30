# -*- coding: utf-8 -*-
"""name_classify.py — classify a NAME as RETIRED or CURRENT, from TWO signals.

THE USER (2026-09-24)
---------------------
    "if register is not full already, non stop untill can proofed old name has
     rename by terminology is_active = 0 to classify active name!!!"

THE PREMISE I MUST REFUSE, WITH ITS MEASUREMENT
-----------------------------------------------
    terminology_registry.is_active:   0 -> 43 rows,   1 -> 9 rows
    those 43 inactive rows INCLUDE:   chat_system, vscode, conversation, app,
                                      channel, capability_tool, ...

Those are CURRENT names. So `is_active = 0` **alone does NOT mean "retired
name"**, and applying the rule literally would mark 43 live terms as retired —
creating the exact confusion the rule exists to remove. `blind_mislabel_count()`
RETURNS that number, so the refusal is a measurement rather than an opinion.

THE RULE THAT SURVIVES — BOTH SIGNALS REQUIRED
----------------------------------------------

    is_active | rename evidence | class
    ----------|-----------------|---------------
        0     |      YES        | RETIRED          the case the user wants
        1     |      NO         | CURRENT
        0     |      NO         | NOT_CLASSIFIED   REFUSED + REPORTED
        1     |      YES        | CONFLICT         REFUSED + REPORTED

`mislabelled()` lists every row where the flag DISAGREES with the evidence — the
deliverable that proves the rule cannot be applied blindly.

WHAT "IS THIS NAME CURRENT" MEANS, and how it is decided
--------------------------------------------------------
`is_active` should MEAN "is this name current". The evidence of NOT current is a
rename (a carrier says this name was superseded). So the correction is DERIVED:

    is_active := 0  if rename_evidence(term) is non-empty
    is_active := 1  otherwise

and every write names the carrier that decided it. A CONFLICT is SKIPPED, never
resolved by picking a side.

Run:
    .\\.venv\\Scripts\\python.exe name_classify.py --census
    .\\.venv\\Scripts\\python.exe name_classify.py --mislabelled
    .\\.venv\\Scripts\\python.exe name_classify.py --classify ollama
    .\\.venv\\Scripts\\python.exe name_classify.py --apply
    .\\.venv\\Scripts\\python.exe name_classify.py --until-converged
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# THE FOUR CLASSES. A fifth would be a guess.
CLASSES: tuple[str, ...] = ("RETIRED", "CURRENT", "NOT_CLASSIFIED", "CONFLICT")

# The convergence cap. "non stop" must be a CONVERGING process, not an infinite
# loop — this repo has recorded loop incidents, so the bound is explicit.
MAX_ROUNDS = 10


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _term(conn: sqlite3.Connection, term_key: str) -> dict[str, Any] | None:
    if not _table_exists(conn, "terminology_registry"):
        return None
    r = conn.execute("SELECT term_id, term_key, is_active, alias_list FROM "
                     "terminology_registry WHERE lower(term_key)=?",
                     (str(term_key).strip().lower(),)).fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------------------
# THE EVIDENCE — a carrier actually recording that this name was superseded
# --------------------------------------------------------------------------
def _migration_sides(migration: str) -> tuple[str, str]:
    """The (OLD, NEW) sides of a `rename_<OLD>_to_<NEW>` migration name.

    MEASURED BUG FIXED: the first version tokenised the migration name and
    matched ANY token, so `rename_name_registry_to_terminology_registry` was
    treated as evidence against `terminology_registry` TOO — the CURRENT name —
    producing a false CONFLICT. A rename names BOTH sides, and only the OLD side
    is retired.
    """
    mig = str(migration or "").strip()
    if not mig.lower().startswith("rename_") or "_to_" not in mig:
        return ("", "")
    body = mig[len("rename_"):]
    old, _, new = body.partition("_to_")
    return (old.strip().lower(), new.strip().lower())


def rename_evidence(conn: sqlite3.Connection, term_key: str) -> list[dict[str, Any]]:
    """Every carrier that says THIS name was superseded. Each is cited.

    A carrier is only evidence when it names the term itself:
      * `legacy_id_map.old_id`        an old id -> a new id
      * `alias_list` holding a name that IS currently registered (a DISTIL the
        term is the OLD side of)
      * `schema_migration_log` a recorded rename that names it
      * a SQL VIEW of the same name  the name is kept alive only for old readers
      * `CODE_ONLY_RENAMES`           a declared Python-only rename

    A term whose ALIAS is an OLD name (`goal` <- `purpose`) has NO evidence of
    being superseded — it is the SUPERSEDING side, so it stays CURRENT.
    """
    import terminology_alias as ta
    key = str(term_key or "").strip()
    low = key.lower()
    ev: list[dict[str, Any]] = []
    if not low:
        return ev

    if _table_exists(conn, "legacy_id_map"):
        for r in conn.execute("SELECT map_id, old_id, new_id FROM legacy_id_map "
                              "WHERE lower(old_id)=?", (low,)):
            ev.append({"carrier": "legacy_id_map", "detail": "old %r -> new %r"
                                                             % (str(r["old_id"]),
                                                                str(r["new_id"])),
                       "cite": "measured: legacy_id_map:%d" % int(r["map_id"])})

    t = _term(conn, key)
    if t:
        for a in ta._aliases_of(t["alias_list"]):
            if ta._is_a_current_name(conn, a):
                ev.append({"carrier": "alias_distil",
                           "detail": "the term is the OLD side; %r is current" % a,
                           "cite": ("measured: terminology_registry.alias_list of "
                                    "%r holds the CURRENT name %r" % (key, a))})

    if _table_exists(conn, "schema_migration_log"):
        for m in conn.execute("SELECT id, migration FROM schema_migration_log "
                              "WHERE lower(migration) LIKE '%rename%'"):
            mig = str(m["migration"])
            old_side, new_side = _migration_sides(mig)
            # ONLY the OLD side is retired; the migration names BOTH.
            if low == old_side:
                ev.append({"carrier": "schema_migration_log",
                           "detail": "migration %r retires this name (the NEW "
                                     "side is %r)" % (mig, new_side),
                           "cite": "measured: schema_migration_log:%d"
                                   % int(m["id"])})

    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'"):
        if str(r["name"]).lower() == low:
            ev.append({"carrier": "view",
                       "detail": "the name is kept alive only as a VIEW",
                       "cite": "measured: sqlite_master view %r" % str(r["name"])})

    for old, _new, where in ta.CODE_ONLY_RENAMES:
        if str(old).lower() == low:
            ev.append({"carrier": "code_only", "detail": "declared at %s" % where,
                       "cite": "measured: CODE_ONLY rename declared at %s" % where})
    return ev


def classify_one(conn: sqlite3.Connection, term_key: str) -> dict[str, Any]:
    """The class, the two signals, and the evidence that decided it."""
    key = str(term_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_TERM_KEY"}
    t = _term(conn, key)
    if not t:
        return {"ok": False, "code": "NOT_A_TERM", "term_key": key,
                "cite": "measured: terminology_registry has no key %r" % key}
    ev = rename_evidence(conn, key)
    active = int(t["is_active"])
    has = bool(ev)
    if active == 0 and has:
        cls, why = "RETIRED", "is_active=0 AND a carrier records the rename"
    elif active == 1 and not has:
        cls, why = "CURRENT", "is_active=1 and NO carrier records a rename"
    elif active == 0 and not has:
        cls, why = "NOT_CLASSIFIED", ("is_active=0 but NO carrier records a "
                                     "rename — the flag alone is not evidence")
    else:
        cls, why = "CONFLICT", ("is_active=1 but a carrier records a rename — "
                                "refused, not resolved by picking a side")
    return {"ok": True, "term_key": str(t["term_key"]), "is_active": active,
            "has_rename_evidence": has, "evidence": ev, "class": cls, "why": why,
            "cite": (ev[0]["cite"] if ev
                     else "measured: terminology_registry.is_active=%d for %r"
                          % (active, str(t["term_key"])))}


def census(conn: sqlite3.Connection) -> dict[str, Any]:
    """A NUMBER per class. Progress is checkable, not asserted."""
    if not _table_exists(conn, "terminology_registry"):
        return {"ok": False, "code": "NO_registry"}
    rows = [classify_one(conn, str(r[0])) for r in conn.execute(
        "SELECT term_key FROM terminology_registry ORDER BY term_id")]
    rows = [r for r in rows if r.get("ok")]
    counts = {c: sum(1 for r in rows if r["class"] == c) for c in CLASSES}
    return {"ok": True, "terms": len(rows), "by_class": counts, "rows": rows,
            "cite": "measured: classify_one over every term"}


def mislabelled(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rows where `is_active` DISAGREES with the evidence.

    THE DELIVERABLE: this list is why `is_active=0` cannot be applied blindly.
    """
    c = census(conn)
    if not c.get("ok"):
        return c
    bad = [r for r in c["rows"] if r["class"] in ("NOT_CLASSIFIED", "CONFLICT")]
    return {"ok": True, "count": len(bad), "rows": bad,
            "by_class": {k: sum(1 for r in bad if r["class"] == k)
                         for k in ("NOT_CLASSIFIED", "CONFLICT")},
            "cite": "measured: rows where is_active and the evidence disagree"}


def blind_mislabel_count(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many terms `is_active == 0` ALONE would wrongly call RETIRED.

    A term is wrongly retired when `is_active=0` but NO carrier records a rename
    AND the name is a CURRENT registered key of something. MEASURED: this is the
    MUTATION the user's rule as stated would perform.
    """
    import terminology_alias as ta
    rows = [classify_one(conn, str(r[0])) for r in conn.execute(
        "SELECT term_key FROM terminology_registry WHERE is_active=0")]
    wrong = [r for r in rows if r.get("ok") and not r["has_rename_evidence"]]
    current_keys = [r for r in wrong
                    if ta._is_a_current_name(conn, r["term_key"])]
    return {"ok": True, "is_active_zero_terms": len(rows),
            "with_no_evidence": len(wrong),
            "that_ARE_current_keys": len(current_keys),
            "mislabel_count": len(wrong),
            "examples": [r["term_key"] for r in wrong[:8]],
            "cite": ("measured: blanket is_active=0 would mark %d of %d terms "
                     "RETIRED with no rename evidence" % (len(wrong), len(rows)))}


# --------------------------------------------------------------------------
# THE CORRECTION — DERIVED from the evidence, never from the flag
# --------------------------------------------------------------------------
def desired_active(conn: sqlite3.Connection, term_key: str) -> dict[str, Any]:
    """What `is_active` SHOULD be: 0 when a rename is recorded, 1 otherwise."""
    r = classify_one(conn, term_key)
    if not r.get("ok"):
        return r
    if r["class"] == "CONFLICT":
        return {"ok": True, "term_key": r["term_key"], "current": r["is_active"],
                "desired": None, "skip": "CONFLICT",
                "why": r["why"], "cite": r["cite"]}
    want = 0 if r["has_rename_evidence"] else 1
    return {"ok": True, "term_key": r["term_key"], "current": r["is_active"],
            "desired": want, "skip": None,
            "why": ("a rename is recorded, so the name is RETIRED"
                    if want == 0 else "no rename is recorded, so the name is CURRENT"),
            "cite": r["cite"]}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Set `is_active` from the EVIDENCE. Idempotent. A CONFLICT is skipped."""
    before = census(conn)
    changed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in before["rows"]:
        d = desired_active(conn, r["term_key"])
        if not d.get("ok"):
            continue
        if d.get("skip"):
            skipped.append({"term_key": d["term_key"], "reason": d["skip"],
                            "cite": d["cite"]})
            continue
        if d["desired"] == d["current"]:
            continue
        conn.execute("UPDATE terminology_registry SET is_active=?, "
                     "updated_at=datetime('now') WHERE term_key=?",
                     (int(d["desired"]), d["term_key"]))
        changed.append({"term_key": d["term_key"], "from": d["current"],
                        "to": d["desired"], "why": d["why"], "cite": d["cite"]})
    conn.commit()
    after = census(conn)
    return {"ok": True, "changed": changed, "changed_n": len(changed),
            "skipped": skipped, "before": before["by_class"],
            "after": after["by_class"],
            "mislabelled_after": mislabelled(conn)["count"]}


def until_converged(conn: sqlite3.Connection, *, max_rounds: int = MAX_ROUNDS
                    ) -> dict[str, Any]:
    """Repeat classify+apply until the census STOPS MOVING. Bounded, and proven.

    "non stop" is satisfied by a process that keeps working until the state is
    FIXED — not by an unbounded loop. The round count and the stop reason are
    returned, so convergence is a measurement.
    """
    rounds: list[dict[str, Any]] = []
    for i in range(int(max_rounds)):
        c = census(conn)
        r = apply(conn)
        rounds.append({"round": i + 1, "changed": r["changed_n"],
                       "census_before": r["before"], "census_after": r["after"],
                       "mislabelled_after": r["mislabelled_after"]})
        if r["changed_n"] == 0:
            return {"ok": True, "converged": True, "rounds": rounds,
                    "round_count": i + 1, "stop_reason": "no change in a round",
                    "final_census": r["after"], "mislabelled": r["mislabelled_after"]}
        if c["by_class"] == r["after"]:
            return {"ok": True, "converged": True, "rounds": rounds,
                    "round_count": i + 1,
                    "stop_reason": "the census did not move",
                    "final_census": r["after"], "mislabelled": r["mislabelled_after"]}
    return {"ok": True, "converged": False, "rounds": rounds,
            "round_count": int(max_rounds),
            "stop_reason": "the hard cap of %d rounds was reached — REPORTED, "
                           "not hidden" % int(max_rounds),
            "final_census": census(conn)["by_class"],
            "mislabelled": mislabelled(conn)["count"]}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    c = census(conn)
    return {"ok": True, "classes": list(CLASSES), "census": c["by_class"],
            "terms": c["terms"], "mislabelled": mislabelled(conn)["count"],
            "blind": blind_mislabel_count(conn),
            "cite": "measured: census + mislabelled + blind"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--mislabelled", action="store_true")
    ap.add_argument("--classify", default="")
    ap.add_argument("--blind", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--until-converged", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.classify:
            print(json.dumps(classify_one(conn, args.classify), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        if args.mislabelled:
            m = mislabelled(conn)
            print("MISLABELLED: %d  %s" % (m["count"], m["by_class"]))
            for r in m["rows"][:20]:
                print("   %-16s %-26s is_active=%s  %s"
                      % (r["class"], r["term_key"], r["is_active"], r["why"][:50]))
            if m["count"] > 20:
                print("   ... and %d more" % (m["count"] - 20))
            return 0
        if args.blind:
            b = blind_mislabel_count(conn)
            print("BLIND is_active=0 would mark %d of %d terms RETIRED with no "
                  "evidence (%d of them ARE current keys)"
                  % (b["mislabel_count"], b["is_active_zero_terms"],
                     b["that_ARE_current_keys"]))
            print("   examples: %s" % b["examples"])
            return 0
        if args.apply:
            r = apply(conn)
            print("APPLIED: changed=%d  skipped=%d" % (r["changed_n"],
                                                       len(r["skipped"])))
            print("   census %s -> %s" % (r["before"], r["after"]))
            print("   mislabelled after: %d" % r["mislabelled_after"])
            for x in r["changed"][:12]:
                print("   %-26s %s -> %s  (%s)"
                      % (x["term_key"], x["from"], x["to"], x["why"][:44]))
            if r["changed_n"] > 12:
                print("   ... and %d more" % (r["changed_n"] - 12))
            for x in r["skipped"]:
                print("   SKIP %-24s %s" % (x["term_key"], x["reason"]))
            return 0
        if args.until_converged:
            r = until_converged(conn)
            print("CONVERGED: %s  rounds=%d  (%s)"
                  % (r["converged"], r["round_count"], r["stop_reason"]))
            for x in r["rounds"]:
                print("   round %d: changed=%d  census=%s"
                      % (x["round"], x["changed"], x["census_after"]))
            print("   final census: %s   mislabelled: %d"
                  % (r["final_census"], r["mislabelled"]))
            return 0
        res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        print("classes   : %s" % res["classes"])
        print("census    : %s  (terms=%d)" % (res["census"], res["terms"]))
        print("mislabelled: %d" % res["mislabelled"])
        print("BLIND is_active=0 would retire %d terms with no evidence"
              % res["blind"]["mislabel_count"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
