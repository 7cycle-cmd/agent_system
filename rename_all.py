# -*- coding: utf-8 -*-
"""rename_all.py — make EVERY old->new pair resolvable through the ONE DOOR.

THE USER (2026-09-24)
---------------------
    "we have unqiue name /alias now, you need to rename all now"
    "if register is not full already, non stop untill can proofed old name has
     rename by terminology is_active = 0"

MEASURED, and this is what "not full" actually means
----------------------------------------------------
    20 old->new pairs exist across the carriers
    ONLY 1 pair has a term for its NEW name
    19 do not

Of those 19, the NEW name is a PROVABLE entity in **12** cases (1 module, 10
capabilities, 1 workflow) and a table/level in the rest. So the work is
derivable: register a term for the new name ONLY where a registry row proves it
exists, cite THAT row, and then add the old name as a cited alias — the same
`add_alias` gate the rest of the system uses, which REFUSES a hand-typed alias.

NO INVENTED TERM
----------------
A new name with NO proving row is REFUSED and REPORTED (`NO_PROVENANCE`). It is
not registered from a guess, because a term with no citation is a word nobody can
check — the rule `terminology_registry.add_term` already enforces.

Run:
    .\\.venv\\Scripts\\python.exe rename_all.py --measure
    .\\.venv\\Scripts\\python.exe rename_all.py --apply
    .\\.venv\\Scripts\\python.exe rename_all.py --apply --until-done
"""
from __future__ import annotations

import argparse
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

# WHERE a NEW name can be PROVEN to exist, in priority order. Each entry is
# (table, key column, entity_kind) and the row is the CITATION.
PROVENANCE: tuple[tuple[str, str, str], ...] = (
    ("module_registry", "module_key", "module"),
    ("capability_registry", "capability_key", "capability"),
    ("channel_registry", "channel_key", "channel"),
    ("workflow_registry", "workflow_key", "workflow"),
    ("taxonomy_level_registry", "level_key", "level"),
)

MAX_ROUNDS = 8


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


def all_pairs(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Every old->new pair the FIVE carriers hold, de-duplicated."""
    import terminology_alias as ta
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []

    def _add(old: str, new: str, src: str) -> None:
        k = (str(old).lower(), str(new).lower())
        if not old or not new or k in seen:
            return
        seen.add(k)
        out.append({"was": str(old), "now": str(new), "carrier": src})

    if _table_exists(conn, "legacy_id_map"):
        for r in conn.execute("SELECT old_id, new_id, map_id FROM legacy_id_map "
                              "ORDER BY map_id"):
            _add(str(r["old_id"]), str(r["new_id"]),
                 "legacy_id_map:%d" % int(r["map_id"]))
    for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='view'"):
        sql = " ".join(str(r["sql"] or "").split())
        if " FROM " in sql.upper():
            t = sql.upper().split(" FROM ", 1)[1].split()[0].strip()
            real = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                "AND lower(name)=?", (t.lower(),)).fetchone()
            _add(str(r["name"]), str(real[0]) if real else t,
                 "view:%s" % str(r["name"]))
    for old, new, where in ta.CODE_ONLY_RENAMES:
        if where.startswith("legacy_id_map"):
            continue
        _add(old, new, "CODE_ONLY:%s" % where)
    return out


def provenance_for(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """The registry ROW that proves `name` exists — or a NAMED refusal."""
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "code": "EMPTY_NAME"}
    for table, col, kind in PROVENANCE:
        if not _table_exists(conn, table) or col not in _columns(conn, table):
            continue
        r = conn.execute("SELECT %s AS k FROM %s WHERE lower(%s)=?"
                         % (col, table, col), (n.lower(),)).fetchone()
        if r:
            return {"ok": True, "entity_kind": kind, "register": table,
                    "key": str(r["k"]),
                    "cite": "measured: %s.%s = %r" % (table, col, str(r["k"]))}
    # a TABLE is provable through sqlite_master (a table IS a registered object)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                    "lower(name)=?", (n.lower(),)).fetchone():
        return {"ok": True, "entity_kind": "db_table", "register": "sqlite_master",
                "key": n,
                "cite": "measured: sqlite_master table %r" % n}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='view' AND "
                    "lower(name)=?", (n.lower(),)).fetchone():
        return {"ok": True, "entity_kind": "view", "register": "sqlite_master",
                "key": n, "cite": "measured: sqlite_master view %r" % n}
    return {"ok": False, "code": "NO_PROVENANCE", "name": n,
            "why": ("no registry row proves %r exists, so a term cannot be "
                    "registered for it — a term with no citation is a word "
                    "nobody can check" % n),
            "cite": "measured: tried %s" % [p[0] for p in PROVENANCE]}


def register_new_name(conn: sqlite3.Connection, name: str, *,
                      old_names: list[str]) -> dict[str, Any]:
    """Register a term for a NEW name, ALIASING the old ones. Cited throughout.

    THE OLD NAMES ARE NOT WRITTEN INTO THE DEFINITION. MEASURED BUG: the first
    version wrote the old code into the DEFINITION, which put a
    LEGACY ID into a registered definition and turned
    `_proof_module_code_align.py` RED (its premise is that zero codes appear in
    any term_key or definition). An old name belongs in `alias_list` — the slot
    built for it — and the citation carries the provenance.
    """
    import terminology_registry as tr
    import terminology_alias as ta
    p = provenance_for(conn, name)
    if not p.get("ok"):
        return p
    tr.ensure_schema(conn)
    kind = "entity"
    definition = ("The CURRENT name %r, the entity `%s` proves it exists; any "
                  "earlier name for it is in `alias_list`."
                  % (name, p["register"]))
    res = tr.add_term(conn, str(name), definition=definition,
                      cite_ref=p["cite"], term_kind=kind, is_active=1)
    if not res.get("ok"):
        return {"ok": False, "code": res.get("code"), "name": name,
                "detail": str(res), "cite": p["cite"]}
    aliased: list[dict[str, Any]] = []
    for old in old_names:
        # `add_alias` REQUIRES a carrier citation, so a pair with no carrier
        # cannot be aliased — that is the gate, not an obstacle.
        a = ta.add_alias(conn, str(name), str(old),
                         cite_ref="alias_list:%s -> %s" % (old, p["cite"]))
        aliased.append({"alias": str(old), "ok": a.get("ok"),
                        "code": a.get("code"), "created": a.get("created")})
    return {"ok": True, "term": str(name), "created": res.get("created"),
            "entity_kind": p["entity_kind"], "cite": p["cite"], "aliases": aliased}


def plan(conn: sqlite3.Connection) -> dict[str, Any]:
    """What rename_all WOULD do, as a NUMBER, with each refusal named."""
    pairs = all_pairs(conn)
    terms = {str(r[0]).lower() for r in conn.execute(
        "SELECT term_key FROM terminology_registry")}
    rows: list[dict[str, Any]] = []
    by_now: dict[str, list[str]] = {}
    for p in pairs:
        by_now.setdefault(str(p["now"]).lower(), []).append(str(p["was"]))
    todo: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    have = 0
    for now, olds in by_now.items():
        if now in terms:
            have += 1
            # the term EXISTS: still alias the old names if they are not aliases
            rows.append({"now": now, "olds": olds, "state": "TERM_EXISTS"})
            continue
        p = provenance_for(conn, now)
        if p.get("ok"):
            todo.append({"now": now, "olds": olds, "provenance": p})
        else:
            refused.append({"now": now, "olds": olds, "code": p["code"],
                            "why": p.get("why")})
    return {"ok": True, "pairs": len(pairs), "distinct_new_names": len(by_now),
            "new_name_has_term": have, "to_registry": len(todo),
            "refused": refused, "todo": todo, "existing": rows,
            "cite": ("measured: %d pairs -> %d new names, %d already have a term, "
                     "%d registrable, %d refused"
                     % (len(pairs), len(by_now), have, len(todo), len(refused)))}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the NEW names that have provenance, aliasing their old names."""
    p = plan(conn)
    registered: list[dict[str, Any]] = []
    for job in p["todo"]:
        r = register_new_name(conn, job["now"], old_names=job["olds"])
        registered.append({"now": job["now"], **{k: r.get(k) for k in
                                                  ("ok", "created", "code",
                                                   "entity_kind", "cite")},
                           "aliases": r.get("aliases", [])})
    # the names that ALREADY have a term still need their old names as aliases
    aliased_existing: list[dict[str, Any]] = []
    import terminology_alias as ta
    for job in p["existing"]:
        for old in job["olds"]:
            a = ta.add_alias(conn, str(job["now"]), str(old),
                             cite_ref="alias_list:%s (term already present)" % old)
            if a.get("created"):
                aliased_existing.append({"now": job["now"], "alias": old,
                                         "created": True})
    after = plan(conn)
    return {"ok": True, "registered": registered,
            "registered_n": sum(1 for r in registered if r.get("ok")),
            "aliased_existing": aliased_existing,
            "refused": after["refused"], "refused_n": len(after["refused"]),
            "before_new_with_term": p["new_name_has_term"],
            "after_new_with_term": after["new_name_has_term"],
            "after_to_registry": after["to_registry"]}


def until_done(conn: sqlite3.Connection, *, max_rounds: int = MAX_ROUNDS
               ) -> dict[str, Any]:
    """Repeat until NOTHING registrable is left. Bounded, with the stop reason."""
    rounds: list[dict[str, Any]] = []
    for i in range(int(max_rounds)):
        r = apply(conn)
        rounds.append({"round": i + 1, "registered": r["registered_n"],
                       "after_to_registry": r["after_to_registry"],
                       "refused": r["refused_n"]})
        if r["registered_n"] == 0:
            g = name_gaps_now(conn)
            return {"ok": True, "converged": True, "rounds": rounds,
                    "round_count": i + 1,
                    "stop_reason": "nothing registrable remained",
                    "refused": r["refused_n"], "gaps": g}
    return {"ok": True, "converged": False, "rounds": rounds,
            "round_count": int(max_rounds),
            "stop_reason": "the hard cap of %d rounds was reached — REPORTED"
                           % int(max_rounds),
            "refused": plan(conn)["refused"] and len(plan(conn)["refused"]),
            "gaps": name_gaps_now(conn)}


def name_gaps_now(conn: sqlite3.Connection) -> int:
    """The door's own completeness NUMBER, so the two views cannot drift."""
    import terminology_alias as ta
    return int(ta.name_gaps(conn)["gap_count"])


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    p = plan(conn)
    import terminology_alias as ta
    import name_classify as nc
    return {"ok": True, "plan": {k: v for k, v in p.items()
                                 if k not in ("todo", "existing")},
            "door_gaps": name_gaps_now(conn),
            "name_classify": nc.census(conn)["by_class"],
            "mislabelled": nc.mislabelled(conn)["count"],
            "cite": "measured: rename_all plan + the door's own gap count"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--until-done", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.plan:
            p = plan(conn)
            print("PAIRS=%d  distinct new names=%d  already have a term=%d  "
                  "to register=%d  refused=%d"
                  % (p["pairs"], p["distinct_new_names"], p["new_name_has_term"],
                     p["to_registry"], len(p["refused"])))
            for t in p["todo"]:
                print("   TODO     %-38s <- %-28s (%s: %s)"
                      % (t["now"], ",".join(t["olds"]),
                         t["provenance"]["entity_kind"], t["provenance"]["cite"]))
            for r in p["refused"]:
                print("   REFUSED  %-38s <- %-28s %s"
                      % (r["now"], ",".join(r["olds"]), r["code"]))
            return 0
        if args.until_done:
            r = until_done(conn)
            print("CONVERGED=%s rounds=%d (%s)  refused=%s  door_gaps=%s"
                  % (r["converged"], r["round_count"], r["stop_reason"],
                     r.get("refused"), r.get("gaps")))
            for x in r["rounds"]:
                print("   round %d: registered=%d  to_registry_after=%s"
                      % (x["round"], x["registered"], x["after_to_registry"]))
            return 0
        if args.apply:
            r = apply(conn)
            print("REGISTERED: %d  (new names with a term %d -> %d)"
                  % (r["registered_n"], r["before_new_with_term"],
                     r["after_new_with_term"]))
            for x in r["registered"]:
                if x.get("ok"):
                    print("   %-38s (%s)  aliases=%s"
                          % (x["now"], x["entity_kind"],
                             [(a["alias"], a.get("created")) for a in x["aliases"]]))
                else:
                    print("   REFUSED %-32s %s" % (x["now"], x.get("code")))
            for x in r["aliased_existing"]:
                print("   ALIASED  %-32s <- %s" % (x["now"], x["alias"]))
            print("REFUSED (no provenance): %d" % r["refused_n"])
            for x in r["refused"]:
                print("   %-38s <- %-28s %s" % (x["now"], ",".join(x["olds"]),
                                                x["code"]))
            print("door gaps now: %d" % name_gaps_now(conn))
            return 0
        res = measure(conn)
        if args.json:
            import json
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        p = res["plan"]
        print("PAIRS=%d  new names=%d  have a term=%d  to register=%d  refused=%d"
              % (p["pairs"], p["distinct_new_names"], p["new_name_has_term"],
                 p["to_registry"], len(p["refused"])))
        print("door gaps   : %d" % res["door_gaps"])
        print("name_classify: %s  mislabelled=%d"
              % (res["name_classify"], res["mislabelled"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
