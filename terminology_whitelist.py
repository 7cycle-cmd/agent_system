"""terminology_whitelist.py — the DECLARED list of names that LOOK wrong but are CORRECT.

THE HUMAN (2026-09-28), verbatim:
    "+ blacklist and whitelist for terminontology, can help you have the work easy"

WHY A WHITELIST IS THE OTHER HALF, NOT A CONVENIENCE
----------------------------------------------------
A blacklist alone REFUSES the repo's own vocabulary. MEASURED 2026-09-28:

    check_composite('5w1h')          -> the digit-run `5w1h` is registered (id 92)
    `db_row`, `llm_100_run`, `CP-S-00`  -> same shape as a typo to a naive checker

And the same measurement cuts the other way: `role_env` PASSES `check_composite`
because `env` is a registered term (id 1549). **So the two registers are
COMPLEMENTARY and neither is sufficient**:

    blacklist  answers "this name is DECLARED wrong"
    whitelist  answers "this name is DECLARED right, even though it looks odd"

Without the whitelist, a blacklist would be a false-positive machine; without the
blacklist, an abbreviation is invisible. Both are ROWS with a reason and a cite,
never an `if` branch.

THE RULE THIS ENFORCES (R-6, `qc_evidence/plan_UNIFIED.NAME.NO.TYPO.md`)
-----------------------------------------------------------------------
NO NAME IS CHANGED BY OPINION. Where a name is a mistake the evidence is a
blacklist row; where it is correct the evidence is a whitelist row. `name_unify`
NEVER decides; it LOOKS UP.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CITE = "terminology_whitelist.py:declare"

WHITELIST_DDL = """
CREATE TABLE IF NOT EXISTS terminology_whitelist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    scope       TEXT    NOT NULL DEFAULT 'name',
    reason      TEXT    NOT NULL,
    cite_ref    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name, scope)
);
"""

# `name`   -> the FULL name, or a TOKEN inside a name.
# `token`  -> a single word that a naive checker would flag.
# `family` -> a legitimate alias family (>=2 declared spellings of ONE concept).
SCOPES = ("name", "token", "family")

# THE SEED. `(name, scope, reason, cite_ref)`.
#
# EVERY row is a MEASUREMENT, not a taste. The ones below were all measured live
# in this session or recorded in a proof the same day.
SEED: tuple[tuple[str, str, str, str], ...] = (
    ("5w1h", "token",
     "MEASURED 2026-09-28: `5w1h` is a REGISTERED term (`terminology_registry` "
     "id 92). It LOOKS like a typo (a digit-run) to a naive spell-checker, which "
     "is exactly the false positive this row exists to prevent. It is also the "
     "survivor of a declared alias family (`skill_5w1h`, `ticket_5w1h`).",
     "terminology_registry.py:92"),
    ("skill_5w1h", "family",
     "MEASURED 2026-09-28: a declared alias of the concept `5w1h`. R-4(a): a "
     "family of >=2 DISTINCT spellings of ONE concept is NOT a wrong name.",
     "terminology_alias.py"),
    ("ticket_5w1h", "family",
     "MEASURED 2026-09-28: a declared alias of the concept `5w1h` — the SAME "
     "family as `skill_5w1h`. Both are legitimate; neither is a typo.",
     "terminology_alias.py"),
    ("db_row", "token",
     "MEASURED: the registered term for a database row. `row` and `db_row` are "
     "both names; neither is misspelled.",
     "entity_id_mint_row_registry.md"),
    ("llm_100_run", "name",
     "MEASURED 2026-09-24: a SQL VIEW that still has 20 readers; renaming it "
     "would break them. A name with a number in it is not a typo.",
     "legacy_name_carriers.md"),
    ("CP-S-00", "name",
     "MEASURED 2026-09-24: a RETIRED capability code with a `legacy_id_map` "
     "row. The shape `CP-S-NN` is the declared code format, not a typo.",
     "legacy_name_carriers.md"),
    ("env", "token",
     "🔴 MEASURED 2026-09-28: `env` IS a registered term (id 1549). It is NOT "
     "declared wrong on its own — an abbreviation is only wrong when it is a "
     "SHORTER SPELLING OF A NAME THAT EXISTS, which is a blacklist row "
     "(`role_env`), not a token rule. This row records the measurement so a later "
     "reader does not 'fix' `env` and break the abbreviation row.",
     "unified_language.py:604"),
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(WHITELIST_DDL)
    conn.commit()


def declare(conn: sqlite3.Connection, name: str, *,
            scope: str = "name", reason: str, cite_ref: str,
            commit: bool = True) -> dict:
    """THE ONE WRITE DOOR. Refusals: `EMPTY_NAME`, `BAD_SCOPE`, `UNCITED`.

    A whitelist row WITHOUT a reason is an unexplained exemption, which is the
    failure mode the human named: an exemption nobody can check is a place a real
    error can hide.
    """
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "code": "EMPTY_NAME"}
    if str(scope or "") not in SCOPES:
        return {"ok": False, "code": "BAD_SCOPE", "scope": scope,
                "declared_scopes": list(SCOPES)}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "UNCITED", "name": n}
    if not str(reason or "").strip():
        return {"ok": False, "code": "UNREASONED", "name": n,
                "message": "an exempted name with no reason is uncheckable"}
    ensure_schema(conn)
    row = conn.execute("SELECT id FROM terminology_whitelist WHERE name=? AND "
                       "scope=?", (n, str(scope))).fetchone()
    if row:
        return {"ok": True, "created": False, "id": int(row[0]), "name": n}
    conn.execute("INSERT INTO terminology_whitelist (name, scope, reason, "
                 "cite_ref, is_active) VALUES (?,?,?,?,1)",
                 (n, str(scope), str(reason).strip(), str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "name": n, "scope": str(scope)}


def _rows(conn: sqlite3.Connection) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM terminology_whitelist WHERE is_active=1")]
    except sqlite3.Error:
        return []


def is_exempt(conn: sqlite3.Connection, name: str) -> dict:
    """Is this name DECLARED correct?

    Returns `{ok, exempt, by, scope}`. `ok` is always True — this is a LOOKUP,
    and a miss is `exempt: False` with a reason, never a failure.

    🔴 MEASURED BUG IN MY FIRST VERSION — RECORDED, because it was a FALSE
    NEGATIVE that let the exact name this task exists to catch escape.
    The first version applied a `token`-scope row to ANY name CONTAINING it, so
    the row `env` exempted **`role_env`** — the abbreviation the blacklist
    correctly refuses. Running it caught this: `classify('role_env')` returned
    EXEMPT instead of WRONG.

    The corrected rule: **a whitelist row matches an EXACT name, always.** The
    `scope` records WHY the name is exempt (`token` = it is a single word that
    looks wrong; `name` = a whole name; `family` = a declared alias family); it
    does NOT widen the match. A name that merely CONTAINS a legitimate token is
    not automatically legitimate — otherwise `role_env` (and any future typo that
    happens to embed `env`) would be silently exempt.
    """
    n = str(name or "")
    low = n.lower()
    for r in _rows(conn):
        if str(r["name"]).lower() == low:
            return {"ok": True, "exempt": True, "by": r["name"],
                    "scope": str(r["scope"]), "reason": r["reason"],
                    "cite_ref": r["cite_ref"]}
    return {"ok": True, "exempt": False, "name": n}


def seed(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """Seed the declared table. Idempotent."""
    out = {"ok": True, "apply": bool(apply), "created": 0, "skipped": 0,
           "refused": [], "cite": "terminology_whitelist.py:seed"}
    if not apply:
        return out
    ensure_schema(conn)
    for name, scope, reason, cite in SEED:
        r = declare(conn, name, scope=scope, reason=reason, cite_ref=cite,
                    commit=False)
        if not r.get("ok"):
            out["refused"].append({"name": name, "code": r.get("code")})
        elif r.get("created"):
            out["created"] += 1
        else:
            out["skipped"] += 1
    conn.commit()
    out["total_active"] = len(_rows(conn))
    return out


def report(conn: sqlite3.Connection) -> dict:
    rows = _rows(conn)
    by_scope: dict[str, int] = {}
    for r in rows:
        by_scope[str(r["scope"])] = by_scope.get(str(r["scope"]), 0) + 1
    return {"ok": True, "total_active": len(rows), "by_scope": by_scope,
            "unreasoned": sum(1 for r in rows
                              if not str(r["reason"] or "").strip()),
            "uncited": sum(1 for r in rows
                           if not str(r["cite_ref"] or "").strip()),
            "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the terminology whitelist register")
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", default="")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.seed:
            print(json.dumps(seed(conn, apply=a.apply), indent=2,
                             ensure_ascii=False))
        if a.report:
            print(json.dumps(report(conn), indent=2, ensure_ascii=False))
        if a.check:
            print(json.dumps(is_exempt(conn, a.check), indent=2,
                             ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())