"""terminology_blacklist.py — the DECLARED list of names that MUST NOT exist.

THE HUMAN (2026-09-28), verbatim:
    "+ blacklist and whitelist for terminontology, can help you have the work easy"

WHY THIS IS A REGISTER AND NOT A PYTHON DICT
--------------------------------------------
The defect it replaces is MEASURED: `terminology_registry.MISSPELLINGS`
(`terminology_registry.py:202-236`) is a hand-typed dict of 32 pairs, read by
exactly TWO call sites (`add_term:1019`, `add_alias:527`). Consequences, all
measured in the same session:

  * a typo OUTSIDE the 32 is invisible (`five_w1h`, `role_env`, `conversaction`);
  * `update_term` (the RENAME path) has NO spelling check at all;
  * the dict itself violates this repo's own rule (`hardcode_audit`: *"could a
    register carry this?"*) — its NAME is `register_shaped` and was a finding.

So the rule is the same one `register_vocabulary` states: **a vocabulary is a
TABLE, not a Python edit.** Adding a typo is an INSERT with a citation. The
Python seed below is the INITIAL 32 + the measured ones; the TABLE is the SSOT
afterwards.

WHY A BLACKLIST IS *REQUIRED* AND NOT A CONVENIENCE — MEASURED 2026-09-28
------------------------------------------------------------------------
`unified_language.check_composite(c, 'role_env')` returns **COMPOSITE_OK**,
because `env` is ITSELF a registered term (`terminology_registry` id **1549**).
So NO word-level rule can refuse an abbreviation. Only a DECLARED row can. That
measurement is the whole argument for this module.

THE RULING (R-1 / R-2, `qc_evidence/plan_UNIFIED.NAME.NO.TYPO.md`)
-----------------------------------------------------------------
A wrong name has exactly TWO legal outcomes: RENAME in place, or DELETE. It may
NOT be kept as an alias, a view, a `legacy_id_map` row, or a legacy nav slug —
*a wrong name that still resolves is still a wrong name*.

THE WRITE DOOR
--------------
`declare()` is the ONE path, and it REFUSES an uncited row (`UNCITED`): a row a
reader cannot check is a second truth.
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

CITE = "terminology_blacklist.py:declare"

BLACKLIST_DDL = """
CREATE TABLE IF NOT EXISTS terminology_blacklist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wrong       TEXT    NOT NULL UNIQUE,
    correction  TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'typo',
    reason      TEXT    NOT NULL,
    cite_ref    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

# The kinds a row can carry. `typo` and `abbreviation` are the two the human
# named; `retired` is the R-1 DELETE outcome recorded as a row so a reader can
# see WHY the name is gone rather than having to guess.
#
# 🔴 `suffix` ADDED 2026-09-28. THE HUMAN, verbatim:
#     "terminology_registry is unified language only!! can't alias different,
#      alias / filename / table / field / function capability / channel / module
#      = same no matter everywhere / this is solution for mis-underestand"
# So `_register` -> `_registry` is the UNIFIED LANGUAGE, and it must be a ROW a
# reader can cite — not an `if` in a second tool. MEASURED: the other kinds name
# a WHOLE name; a suffix is a PATTERN, so it needs its own kind rather than being
# smuggled into `typo`.
KINDS = ("typo", "abbreviation", "retired", "suffix")

# THE SEED. `(wrong, correction, kind, reason, cite_ref)`.
#
# The 32 pairs are READ from `terminology_registry.MISSPELLINGS` at seed time
# (imported, never re-typed — a second copy of the list is exactly the drift this
# module exists to remove), so this tuple carries ONLY the ones the dict could
# not express plus the source citation for the 32.
MISSPELLINGS_CITE = "terminology_registry.py:202"

# The measured names the 32-pair dict MISSED, each with the measurement.
MEASURED_SEED: tuple[tuple[str, str, str, str, str], ...] = (
    ("five_w1h", "completeness_5w1h", "typo",
     "MEASURED 2026-09-28: `five_w1h` spells the digit 5 as the WORD `five`. The "
     "human declared it a wrong-spelling BUG (2026-09-27): \"five_w1h=462, this "
     "is wrong spelling BUG, have totally rename and fix\". "
     "`check_composite` refuses it (MISSING_WORD `w1h`) because only `5w1h` (id "
     "92) is a registered term.",
     "qc_evidence/plan_UNIFIED.NAME.NO.TYPO.md:step-3"),
    ("completeness_5w1h_derive", "derive_5w1h", "typo",
     "MEASURED 2026-09-28: term id 1213 (soft-deleted) spelled the digit 5 as "
     "the WORD `five`, and duplicated the ALREADY-REGISTERED `derive_5w1h` "
     "(id 1462). The correction is therefore `derive_5w1h` — a name that EXISTS. "
     "🔴 MY FIRST ROW SAID `completeness_5w1h`, which made a blanket rewrite "
     "produce `completeness_5w1h_derive`: a name that exists NOWHERE. A rewrite "
     "must land on a REAL name, or it INVENTS one.",
     "terminology_registry.py:92"),
    ("role_env", "role_environment", "abbreviation",
     "🔴 MEASURED 2026-09-28: `check_composite` PASSES `role_env` because `env` is "
     "ITSELF a registered term (id 1549). No word-level rule can refuse an "
     "abbreviation — only a DECLARED row can, which is why this register is "
     "required rather than optional.",
     "unified_language.py:604"),
    ("enviornment", "environment", "typo",
     "MEASURED 2026-09-27: the register had NO spelling check, so "
     "`enviornment_playwright` entered it via `add_alias`. Population: 12,423 "
     "correct / 3,970 wrong.",
     "terminology_registry.py:202"),
    ("enviornment_playwright", "environment_playwright", "typo",
     "MEASURED 2026-09-28: an alias of term `playwright` (a COMBINED token "
     "carrying the `enviornment` typo). The correction is `environment_playwright` "
     "— 'environment' + 'playwright' — because BOTH are registered terms, so the "
     "corrected token DECOMPOSES under `check_composite`. Correcting it to bare "
     "`playwright` would silently drop the 'environment' concept from the URL.",
     "terminology_alias.py:521"),
    ("conversaction", "conversation", "typo",
     "MEASURED 2026-09-26: the human typed `/llm-tasks/conversaction/step2-68`; "
     "the registered term is `conversation`. The UI slug table kept it routable.",
     "llm_task_monitor_ui/src/conversation-center.js:1283"),
    # 🔴 THE UNIFIED-LANGUAGE RULE, DECLARED AS A ROW (2026-09-28).
    # THE HUMAN: "terminology_registry is unified language only!! can't alias
    # different, alias / filename / table / field / function capability / channel
    # / module = same no matter everywhere".
    # MEASURED: 35 tables, 83 filenames, 41 module keys, 64 functions still spell
    # it `_register`. The rule was an `if` in `name_unify.py (SUPERSEDED: _unify_registry_naming.py):55`
    # (`OLD, NEW = "_register", "_registry"`) — a SECOND owner a reader could not
    # cite. It is now a row, so ONE owner reads it.
    ("_register", "_registry", "suffix",
     "MEASURED 2026-09-28: the unified language is `registry`, not `register`. "
     "The human ruled it a LAW, not a preference: 'terminology_registry is "
     "unified language only!! can't alias different, alias / filename / table / "
     "field / function capability / channel / module = same no matter "
     "everywhere'. MEASURED population: 35 tables, 83 filenames, 41 module keys, "
     "64 functions. The rule previously lived as an `if` in "
     "`name_unify.py (SUPERSEDED: _unify_registry_naming.py):55`, a second owner with no citation.",
     "qc_evidence/plan_NAME.UNIFY.ALL.CARRIERS.md:P0"),)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the table if absent. Idempotent."""
    conn.execute(BLACKLIST_DDL)
    conn.commit()


def declare(conn: sqlite3.Connection, wrong: str, correction: str, *,
            kind: str, reason: str, cite_ref: str, commit: bool = True) -> dict:
    """THE ONE WRITE DOOR. A row a reader cannot CHECK is refused.

    Refusals:
      * `EMPTY_WRONG`       — nothing to declare.
      * `EMPTY_CORRECTION`  — a blacklist row with no replacement only says
                              "wrong"; the reader still has to guess what is right.
      * `SAME_AS_CORRECTION`— a row that maps a name to itself is not a blacklist.
      * `BAD_KIND`          — not in `KINDS`; an undeclared kind is uncheckable.
      * `UNCITED`           — no `cite_ref`. THE RULE: no citation, no row.
      * `ALREADY_DECLARED`  — the name is already a row (UPDATE via `set_active`).
    """
    w = str(wrong or "").strip()
    c = str(correction or "").strip()
    if not w:
        return {"ok": False, "code": "EMPTY_WRONG"}
    if not c:
        return {"ok": False, "code": "EMPTY_CORRECTION", "wrong": w}
    if w.lower() == c.lower():
        return {"ok": False, "code": "SAME_AS_CORRECTION", "wrong": w}
    if str(kind or "") not in KINDS:
        return {"ok": False, "code": "BAD_KIND", "kind": kind,
                "declared_kinds": list(KINDS)}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "UNCITED", "wrong": w,
                "message": ("a blacklist row must cite the measurement that "
                            "proved the name wrong. No citation, no row.")}
    ensure_schema(conn)
    row = conn.execute("SELECT id FROM terminology_blacklist WHERE wrong=?",
                       (w,)).fetchone()
    if row:
        return {"ok": True, "created": False, "id": int(row[0]), "wrong": w}
    conn.execute(
        "INSERT INTO terminology_blacklist (wrong, correction, kind, reason, "
        "cite_ref, is_active) VALUES (?,?,?,?,?,1)",
        (w, c, str(kind), str(reason or "").strip(), str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "wrong": w, "correction": c}


def set_active(conn: sqlite3.Connection, wrong: str, *, is_active: int,
               cite_ref: str, commit: bool = True) -> dict:
    """Retire/re-activate ONE row. Uncited is refused, the same as `declare`."""
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "UNCITED", "wrong": str(wrong or "")}
    row = conn.execute("SELECT id, is_active FROM terminology_blacklist "
                       "WHERE wrong=?", (str(wrong or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_ROW", "wrong": str(wrong or "")}
    if int(row["is_active"]) == int(is_active):
        return {"ok": True, "changed": False, "id": int(row["id"])}
    conn.execute("UPDATE terminology_blacklist SET is_active=? WHERE id=?",
                 (int(is_active), int(row["id"])))
    if commit:
        conn.commit()
    return {"ok": True, "changed": True, "id": int(row["id"]),
            "is_active": int(is_active)}


def repoint(conn: sqlite3.Connection, wrong: str, correction: str, *,
            cite_ref: str, commit: bool = True) -> dict:
    """Correct a row's `correction`, WITH a citation.

    WHY THIS EXISTS — and it was needed within the hour: the first
    `completeness_5w1h_derive` row said `completeness_5w1h`, so a blanket rewrite produced
    **`completeness_5w1h_derive`, a name that exists NOWHERE**. A blacklist
    correction must name a REAL name, or the rewrite INVENTS one.

    A correction change is a CLAIM, so it needs a cite like every other write.
    Refusals: `UNCITED`, `NO_SUCH_ROW`, `SAME_CORRECTION`.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "UNCITED", "wrong": str(wrong or "")}
    row = conn.execute("SELECT id, correction FROM terminology_blacklist WHERE "
                       "wrong=?", (str(wrong or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_ROW", "wrong": str(wrong or "")}
    if str(row["correction"]) == str(correction):
        return {"ok": True, "changed": False, "id": int(row["id"])}
    conn.execute("UPDATE terminology_blacklist SET correction=?, reason="
                 "reason || ' | REPOINTED ' || ? || ' cite=' || ? WHERE id=?",
                 (str(correction), str(correction), str(cite_ref),
                  int(row["id"])))
    if commit:
        conn.commit()
    return {"ok": True, "changed": True, "id": int(row["id"]),
            "from": str(row["correction"]), "to": str(correction)}


def _rows(conn: sqlite3.Connection) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM terminology_blacklist WHERE is_active=1")]
    except sqlite3.Error:
        return []


def _token_re(wrong: str) -> "re.Pattern":
    """A WHOLE-TOKEN match, never a bare substring.

    MEASURED 2026-09-28, and it is the #1 recurring defect in this repo ("count
    CODE, not text"): a bare `wrong in name` matched the blacklist row `role_env`
    INSIDE the CORRECT name `role_environment`. `_`, `.` and `-` must stay
    BOUNDARIES (so `enviornment_playwright` still matches the `enviornment` row),
    but a LETTER or DIGIT must not. Same rule `module_code_align` derived:
    "`a.b` is a DIFFERENT name from `a`."
    """
    import re as _re
    return _re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])"
                       % _re.escape(str(wrong)), _re.IGNORECASE)


def check(conn: sqlite3.Connection, name: str) -> dict:
    """The verdict on ONE name: is it DECLARED wrong?

    BOTH an exact match and a WHOLE-TOKEN match, because the measured defect
    `enviornment` sat INSIDE `enviornment_playwright` — an equality test alone
    would have missed it. But a BARE substring match is wrong in the other
    direction: it flags the CORRECT `role_environment` for containing `role_env`.
    So the match is a coded token boundary.

    `ok=True` means "not a DECLARED wrong name" — it is NOT a claim that the name
    is correct. A check that claimed correctness would be claiming to verify
    meaning, which no declared list can do.
    """
    n = str(name or "")
    if not n:
        return {"ok": True, "code": None, "name": n}
    low = n.lower()
    # THE EXEMPTION OUTRANKS EVERYTHING: a name DECLARED correct is never wrong.
    try:
        import terminology_whitelist as wl
        ex = wl.is_exempt(conn, n)
        if ex.get("exempt"):
            return {"ok": True, "code": None, "name": n, "exempt": True,
                    "exempt_by": ex.get("by"), "cite_ref": ex.get("cite_ref")}
    except Exception:
        pass
    # 🔴 EXACT MATCH BEATS A TOKEN MATCH — MEASURED BUG IN MY FIRST VERSION.
    # One pass returned on the FIRST row that matched either way, so for
    # `completeness_5w1h_derive` the ROW ORDER decided: the `five_w1h` row (id 33, whose
    # wrong spelling is a token INSIDE `completeness_5w1h_derive`) was reached first and
    # its correction (`completeness_5w1h`) won over the row that names
    # `completeness_5w1h_derive` EXACTLY (whose correction is `derive_5w1h`). The plan then
    # proposed `completeness_5w1h_derive` — a name that exists NOWHERE.
    # **The most specific declaration must win, not the earliest.**
    rows = _rows(conn)
    for r in rows:
        if str(r["wrong"]).lower() == low:
            return {"ok": False, "code": "BLACKLISTED_NAME", "name": n,
                    "match": "exact", "wrong": r["wrong"],
                    "correction": r["correction"], "kind": r["kind"],
                    "reason": r["reason"], "cite_ref": r["cite_ref"],
                    "message": ("%r is a DECLARED %s name (%s). Correct spelling: "
                                "%r" % (n, r["kind"], r["cite_ref"],
                                        r["correction"]))}
    for r in rows:
        w = str(r["wrong"])
        m = _token_re(w).search(n)
        if m:
            return {"ok": False, "code": "BLACKLISTED_NAME", "name": n,
                    "match": "token", "wrong": r["wrong"],
                    "correction": r["correction"], "kind": r["kind"],
                    "reason": r["reason"], "cite_ref": r["cite_ref"],
                    "suggestion": (low[:m.start()] + str(r["correction"]).lower()
                                   + low[m.end():]),
                    "message": ("%r CONTAINS the DECLARED %s name %r (%s). "
                                "Likely correct name: %r"
                                % (n, r["kind"], r["wrong"], r["cite_ref"],
                                   low[:m.start()]
                                   + str(r["correction"]).lower()
                                   + low[m.end():]))}
    return {"ok": True, "code": None, "name": n}


def seed(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """Seed the table: the 32 `MISSPELLINGS` pairs + the MEASURED ones.

    The 32 are IMPORTED from `terminology_registry.MISSPELLINGS`, never re-typed
    (the import IS the citation). Idempotent — `declare` returns `created: False`
    for a row already present.
    """
    import terminology_registry as tr
    out = {"ok": True, "apply": bool(apply), "created": 0, "skipped": 0,
           "refused": [], "cite": "terminology_blacklist.py:seed"}
    if not apply:
        return out
    ensure_schema(conn)
    for wrong, correction in sorted(tr.MISSPELLINGS.items()):
        r = declare(conn, wrong, correction, kind="typo",
                    reason=("A transposition in the register's own MISSPELLINGS "
                            "table; measured live in the corpus."),
                    cite_ref=MISSPELLINGS_CITE, commit=False)
        if not r.get("ok"):
            out["refused"].append({"wrong": wrong, "code": r.get("code")})
        elif r.get("created"):
            out["created"] += 1
        else:
            out["skipped"] += 1
    for wrong, correction, kind, reason, cite in MEASURED_SEED:
        r = declare(conn, wrong, correction, kind=kind, reason=reason,
                    cite_ref=cite, commit=False)
        if not r.get("ok"):
            out["refused"].append({"wrong": wrong, "code": r.get("code")})
        elif r.get("created"):
            out["created"] += 1
        else:
            out["skipped"] += 1
    conn.commit()
    out["total_active"] = len(_rows(conn))
    return out


def report(conn: sqlite3.Connection) -> dict:
    """The SCALE as a number, per kind. A single total hides which kind grew."""
    rows = _rows(conn)
    by_kind: dict[str, int] = {}
    for r in rows:
        by_kind[str(r["kind"])] = by_kind.get(str(r["kind"]), 0) + 1
    return {"ok": True, "total_active": len(rows), "by_kind": by_kind,
            "uncited": sum(1 for r in rows if not str(r["cite_ref"] or "").strip()),
            "rows": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the terminology blacklist register")
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
            print(json.dumps(check(conn, a.check), indent=2,
                             ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())