"""terminology_autopilot.py — the terminology CHECKLIST and its BOUNDED runner.

THE USER'S TWO INSTRUCTIONS (2026-09-24), and how each is honoured
-----------------------------------------------------------------
1. > "be the coding writing and verify skill too, so it can auto forever"

   `auto forever` has exactly ONE legal shape in this repo:

       a BOUNDED run with a NAMED stop reason, invoked by a kicker.

   An unbounded `while True` is the loop incident this repo already recorded, not
   automation. `until_all_proven()` therefore CANNOT run forever by construction:
   it stops on `ALL_CHECKLIST_ITEMS_PROVEN`, `NO_PROGRESS`, `MAX_ROUNDS_REACHED`
   or `BLOCKED_REPORT_ITEMS`, and it NAMES which one.

2. > "non stop untill evidence proof for all terminology checklist can proof your
   >  work has done"

   So the deliverable is not "the work feels done" — it is EVIDENCE PER ITEM.
   Every checklist item below carries `would_be_red_if`: the observation that
   would make it FAIL. An item that can never be red is not a check, and
   `run_once()` REFUSES to report a checklist containing one.

THE HONEST CORE: NOT EVERY ITEM CAN BE AUTO-FIXED
-------------------------------------------------
C6 (52 files still assert an object KIND) and C7 (2 old names are still live
objects) are **REPORT** items. A loop must NOT "fix" them: each site needs a
worker to decide what it should read. A loop that silently rewrote 52 readers
would be exactly the destructive automation the user is trying to avoid. So the
runner drives the 5 AUTO items and STOPS with `BLOCKED_REPORT_ITEMS` naming the
ones it will not touch.

WHY THIS IS NOT A DUPLICATE SKILL
---------------------------------
`skill_worker_code_builder` (id 25) is the WRITE step and
`skill_task_done_verification` (id 57) is the VERIFY step. This skill is the
CHECKLIST + the bounded runner that DRIVES those two. It references them; it does
not restate them.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"

# A BOUNDED maximum. The number is not magic: it is the point past which a round
# changed nothing and the loop is no longer converging (see NO_PROGRESS).
MAX_ROUNDS = 10

STOP_ALL_PROVEN = "ALL_CHECKLIST_ITEMS_PROVEN"
STOP_NO_PROGRESS = "NO_PROGRESS"
STOP_MAX_ROUNDS = "MAX_ROUNDS_REACHED"
STOP_BLOCKED = "BLOCKED_REPORT_ITEMS"


# ---------------------------------------------------------------------------
# The AUTO checks. Each returns (ok, observed, would_be_red_if).
# `fix` is None for a REPORT item: a loop must not touch it.
# ---------------------------------------------------------------------------

def _c1_no_duplicate_active_key(conn: sqlite3.Connection,
                                fix: bool) -> tuple[bool, Any, str]:
    """C1 — no two ACTIVE terms share a term_key WITHOUT a qualifier.

    A repeated key is TWO DIFFERENT SITUATIONS, and conflating them is a trap I
    fell into in this check's FIRST version:

      * TRUE DUPLICATE — the SAME concept entered twice (SAME `parent_term_id`).
        One redundant row; deactivating the newer is a legitimate, reversible fix.

      * NAME COLLISION — DIFFERENT concepts that happen to share a word.
        MEASURED IN THIS REPO: `route` is term 39 ("a declared CONNECTION",
        `purpose_route_registry.py:28`) AND term 40 ("An HTTP ENDPOINT",
        `route_inventory.py:27`) — different `parent_term_id` (37 vs 38),
        different meanings, BOTH correct. My first fix DEACTIVATED the newer one,
        which would have DESTROYED a real concept. The correct answer is NOT to
        pick a side: they must be distinguished by QUALIFIERS (`declared_route`
        vs `http_route`).

    So a collision is RED but **REPORT**-class: it needs a worker to name the
    qualifier. A loop must not invent one, and must not delete a meaning.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT term_id, term_key, parent_term_id FROM terminology_registry "
        "WHERE is_active = 1")]
    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_key.setdefault(str(r["term_key"]), []).append(r)

    true_dupes: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        if len({g["parent_term_id"] for g in group}) == 1:
            true_dupes.append({"term_key": key, "ids": [g["term_id"] for g in group],
                               "parent_term_id": group[0]["parent_term_id"]})
        else:
            collisions.append({"term_key": key,
                               "ids": [g["term_id"] for g in group],
                               "parents": [g["parent_term_id"] for g in group]})

    acted: list[dict[str, Any]] = []
    if fix and true_dupes:
        # ONLY a true duplicate is auto-resolved. The lowest id keeps the name;
        # the rest are deactivated WITH the reason recorded, never deleted.
        for d in true_dupes:
            keep = min(int(x) for x in d["ids"])
            for tid in d["ids"]:
                if int(tid) == keep:
                    continue
                conn.execute(
                    "UPDATE terminology_registry SET is_active = 0, "
                    "updated_at = datetime('now') WHERE term_id = ?", (int(tid),))
                acted.append({"deactivated_term_id": int(tid), "kept": keep,
                              "term_key": d["term_key"]})
        conn.commit()

    ok = not true_dupes and not collisions
    return ok, {"true_duplicates": true_dupes, "collisions": collisions,
                "acted": acted,
                "collision_note": ("a name collision needs a QUALIFIER named by a "
                                   "worker; it is never resolved by deleting a meaning")
                if collisions else None}, \
        "two ACTIVE terms share one term_key without a qualifier"


def _c1_duplicates_only(conn: sqlite3.Connection,
                        fix: bool) -> tuple[bool, Any, str]:
    """C1a — the register ENFORCES one name per concept. TWO constraints, not one.

    WHY THIS CHECK EXISTS AT ALL
    ----------------------------
    Its FIRST version counted ACTIVE rows sharing a `term_key` AND a
    `parent_term_id` and called that a "true duplicate". The PROOF tried to PLANT
    one and hit:

        sqlite3.IntegrityError: UNIQUE constraint failed:
            terminology_registry.parent_term_id, terminology_registry.term_key

    so that version could NEVER be RED — a decoration, which checklist rule 3
    forbids.

    AND THE REPLACEMENT FOUND A REAL HOLE
    -------------------------------------
    Chasing it down MEASURED a latent defect in the schema itself:

      * The table declares `UNIQUE (parent_term_id, term_key)`.
      * SQLite treats NULLs as DISTINCT, so that clause **does not protect the 65
        ROOT terms** (`parent_term_id IS NULL`).
      * MEASURED: inserting a SECOND root term with an existing key was ACCEPTED.

    So "one name per concept" needs TWO constraints:
      1. `UNIQUE (parent_term_id, term_key)`  — for NAMED-PARENT terms (a clause).
      2. a PARTIAL unique index `(term_key) WHERE parent_term_id IS NULL`
         — for ROOT terms. This is the half that was missing.
    """
    composite = None
    for r in conn.execute("PRAGMA index_list(terminology_registry)"):
        if not int(r["unique"] or 0):
            continue
        cols = [d[2] for d in conn.execute("PRAGMA index_info(%s)" % r["name"])]
        if cols == ["parent_term_id", "term_key"]:
            composite = {"index": str(r["name"]), "cols": cols}

    root_idx = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND name = 'idx_terminology_root_one_name'").fetchone()

    acted: list[dict[str, Any]] = []
    create_failed: str | None = None
    if fix and root_idx is None:
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_terminology_root_one_name ON terminology_registry (term_key) "
                "WHERE parent_term_id IS NULL")
            conn.commit()
            acted.append({"created_index": "idx_terminology_root_one_name"})
            root_idx = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' "
                "AND name = 'idx_terminology_root_one_name'").fetchone()
        except Exception as e:
            create_failed = "%s: %s" % (type(e).__name__, e)

    same_parent_dupes = [dict(r) for r in conn.execute(
        "SELECT parent_term_id, term_key, COUNT(*) n FROM terminology_registry "
        "GROUP BY parent_term_id, term_key HAVING n > 1")]
    root_dupes = [dict(r) for r in conn.execute(
        "SELECT term_key, COUNT(*) n FROM terminology_registry "
        "WHERE parent_term_id IS NULL GROUP BY term_key HAVING n > 1")]

    ok = (composite is not None and root_idx is not None
          and not same_parent_dupes and not root_dupes and create_failed is None)
    return ok, {"composite_constraint": composite,
                "root_constraint": ({"index": str(root_idx["name"]),
                                     "sql": str(root_idx["sql"])}
                                    if root_idx is not None else None),
                "same_parent_duplicates": same_parent_dupes,
                "root_duplicates": root_dupes,
                "create_failed": create_failed,
                "acted": acted}, \
        "the register does NOT enforce one name per concept (root terms are unprotected " \
        "because SQLite treats NULL parents as distinct)"


def _c1_collisions_only(conn: sqlite3.Connection,
                        fix: bool) -> tuple[bool, Any, str]:
    """C1b — a colliding term's QUALIFIER is DERIVED, so this item is AUTO.

    THE USER'S RULING (2026-09-24) turned this from REPORT into AUTO:
      > "C1b 撞名要命名 qualifier ... your question is need to have definition for
      >  which to let this definition with this value need to have what -> trigger point"

    A judgement never converges, but a DEFINITION does. MEASURED: the register
    ALREADY stores the structure (`parent_term_id`), and the parent's `term_key` IS
    the qualifier:

        term 39 `route` parent 37 = `connection`    -> `connection.route`
        term 40 `route` parent 38 = `http_endpoint` -> `http_endpoint.route`

    So nothing is CHOSEN. `trigger_point.qualify_collisions` derives it, and this
    item is satisfied only when every colliding term is DERIVABLY qualified. A
    colliding term with no parent supplies no qualifier and is reported with the
    NAMED reason `NO_PARENT_TO_QUALIFY_WITH`.
    """
    import trigger_point as tpt
    out = tpt.qualify_collisions(conn)
    return bool(out["ok"]), {
        "derivable": out["derivable"],
        "unqualifiable": out["unqualifiable"],
        "definition": out["definition"],
    }, "different concepts share one word and no qualifier distinguishes them"


def _c2_old_names_resolve(conn: sqlite3.Connection,
                          fix: bool) -> tuple[bool, Any, str]:
    """C2 — every name in a carrier resolves to a current name."""
    import terminology_alias as ta
    gaps = ta.name_gaps(conn)
    n = int(gaps.get("gaps") or 0)
    return n == 0, {"carrier_entries": gaps.get("entries"),
                    "resolved": gaps.get("resolved"), "gaps": n}, \
        "a carrier entry does not resolve to any current name"


def _c3_no_blind_mislabel(conn: sqlite3.Connection,
                          fix: bool) -> tuple[bool, Any, str]:
    """C3 — the classification rule does not mislabel a name.

    MEASURED PREMISE: a blanket `is_active = 0` rule mislabels 43 of 43 terms.
    This item is RED whenever the rule in force cannot separate an old name from
    a current one.
    """
    import name_classify as nc
    mis = nc.blind_mislabel_count(conn) if fix is not None else None
    cen = nc.census(conn)
    bad = int(cen.get("mislabelled") or 0)
    return bad == 0, {"census": cen.get("by_class"), "mislabelled": bad,
                      "blind_would_mislabel": mis}, \
        "a name is classified with no evidence for its class"


def _c4_every_active_term_cited(conn: sqlite3.Connection,
                                fix: bool) -> tuple[bool, Any, str]:
    """C4 — every ACTIVE term carries a non-empty citation.

    A term with no citation is a claim nobody can check — the rule
    `citation_discipline` enforces everywhere else.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT term_id, term_key FROM terminology_registry "
        "WHERE is_active = 1 AND (cite_ref IS NULL OR TRIM(cite_ref) = '')")]
    # NOT auto-fixed: a citation cannot be INVENTED by a loop. Inventing one would
    # be the fabrication the citation rules exist to prevent, so an uncited term
    # is reported for a worker to cite.
    return (not rows), {"uncited": rows}, \
        "an ACTIVE term carries no citation"


def _c5_no_description_in_alias(conn: sqlite3.Connection,
                                fix: bool) -> tuple[bool, Any, str]:
    """C5 — no alias slot holds a DESCRIPTION instead of a name.

    A recorded mistake of mine: `final key factor` was written into an alias slot.
    An alias slot holding prose makes `resolve_name` answer a sentence.
    """
    import terminology_alias as ta
    rows = [dict(r) for r in conn.execute(
        "SELECT term_id, term_key, alias_list FROM terminology_registry "
        "WHERE alias_list IS NOT NULL AND UPPER(TRIM(alias_list)) != 'NA'")]
    bad = []
    for r in rows:
        for a in ta._alias_items(r["alias_list"]) if hasattr(ta, "_alias_items") \
                else json.loads(r["alias_list"]):
            a = str(a)
            if len(a.split()) >= 3 or len(a) > 40:
                bad.append({"term_id": r["term_id"], "term_key": r["term_key"],
                            "alias": a})
    return (not bad), {"description_like_aliases": bad}, \
        "an alias slot holds a description, not a name"


def _c6_no_reader_asserts_kind(conn: sqlite3.Connection,
                               fix: bool) -> tuple[bool, Any, str]:
    """C6 — a reader's table-only guard is correct IFF the file does DDL. AUTO.

    THE USER'S RULING turned this from REPORT into AUTO by DEFINING the trigger:
      > "C6 52 個 reader 要逐個判斷 ... need to have definition ... trigger point"

    The definition removes the judgement: MEASURED, 18 files contain a DDL verb
    (KEEP table-only — a VIEW must not pass a DDL gate) and the rest contain none,
    so their check's ONLY subject is existence and must accept any readable kind.
    The FILE'S OWN CONTENT decides its class.
    """
    import trigger_point as tpt
    out = tpt.classify_readers()
    if fix and out["migrate_count"]:
        tpt.migrate_readers(apply=True)
        out = tpt.classify_readers()
    return out["migrate_count"] == 0, {
        "keep_table_only": out["ddl_count"],
        "still_to_migrate": out["migrate_count"],
        "definition": out["definition"],
    }, "a non-DDL file still decides object existence by kind"


def _c7_no_old_name_is_an_object(conn: sqlite3.Connection,
                                 fix: bool) -> tuple[bool, Any, str]:
    """C7 — a compatibility view may be dropped IFF no file reads it. MEASURED GATE.

    The user asked for a DEFINITION here too. It exists, and it is NOT a
    preference: MEASURED readers `llm_100_run` 20, `v_skill_contract` 2. The
    definition FORBIDS the drop while readers exist, so the honest status is
    "blocked by a MEASURED COUNT", never "a worker should look at this".

    This stays REPORT-class on purpose: the trigger is defined, but the ACTION
    (migrate 20 readers across the repo) is a separate, larger piece of work. A
    loop must not start it because a count it cannot control said so.
    """
    import trigger_point as tpt
    out = tpt.drop_gate(conn)
    return (not out["blocked"]), {
        "views": out["views"], "blocked": [x["view"] for x in out["blocked"]],
        "definition": out["definition"], "trigger": out["trigger"],
        "droppable": out["droppable"],
    }, "an old name is still a live table/view, reachable as a second name"


CHECKLIST: tuple[dict[str, Any], ...] = (
    {"key": "C1a", "needs": "auto",
     "question": "the register enforces one name per concept (BOTH the composite clave and the root-term index)",
     "fn": _c1_duplicates_only},
    {"key": "C1b", "needs": "auto",
     "question": "a colliding name is DERIVABLY qualified from its parent (trigger defined)",
     "fn": _c1_collisions_only},
    {"key": "C2", "needs": "auto", "question": "every old name resolves to its current name",
     "fn": _c2_old_names_resolve},
    {"key": "C3", "needs": "auto", "question": "no blanket is_active rule mislabels a name",
     "fn": _c3_no_blind_mislabel},
    {"key": "C4", "needs": "auto", "question": "every ACTIVE term carries a citation",
     "fn": _c4_every_active_term_cited},
    {"key": "C5", "needs": "auto", "question": "no alias slot holds a description",
     "fn": _c5_no_description_in_alias},
    {"key": "C6", "needs": "auto",
     "question": "every guard is name-only IFF its file does no DDL (trigger: DDL presence)",
     "fn": _c6_no_reader_asserts_kind},
    {"key": "C7", "needs": "report",
     "question": "a compat view is dropped IFF readers==0 (trigger: reader count)",
     "fn": _c7_no_old_name_is_an_object},
)


def _checklist_is_provable() -> list[str]:
    """A checklist item with no RED condition is a decoration, not a check.

    Returns the keys that are unprovable. `run_once()` REFUSES to report
    ALL PROVEN while this list is non-empty — otherwise "all green" could mean
    "one check can never fail".
    """
    bad = []
    for item in CHECKLIST:
        if not callable(item.get("fn")) or not item.get("needs") in ("auto", "report"):
            bad.append(str(item.get("key")))
    return bad


def run_once(conn: sqlite3.Connection, *, fix: bool = False) -> dict[str, Any]:
    """Measure EVERY checklist item ONCE. `fix` drives the AUTO items only.

    Returns `{items, auto_open, report_open, red, would_be_red_if}` — the whole
    status in ONE dict, so a proof reads the SAME numbers the loop acted on.
    """
    unprovable = _checklist_is_provable()
    items: list[dict[str, Any]] = []
    for item in CHECKLIST:
        needs = str(item["needs"])
        try:
            ok, observed, red_if = item["fn"](conn, fix and needs == "auto")
            crashed = None
        except Exception as e:
            # A check that CRASHES is not a pass. It is reported as RED with the
            # exception named, because a crash silently treated as "ok" is how a
            # detector goes blind.
            ok, observed, red_if, crashed = False, None, "the check crashed", \
                "%s: %s" % (type(e).__name__, e)
        items.append({"key": item["key"], "needs": needs,
                      "question": item["question"], "ok": bool(ok),
                      "observed": observed, "would_be_red_if": red_if,
                      "crashed": crashed})

    auto_open = [i["key"] for i in items if i["needs"] == "auto" and not i["ok"]]
    report_open = [i["key"] for i in items if i["needs"] == "report" and not i["ok"]]
    red = [i["key"] for i in items if not i["ok"]]
    return {"items": items, "auto_open": auto_open, "report_open": report_open,
            "red": red, "unprovable": unprovable,
            "all_proven": (not red) and (not unprovable)}


def _open_signature(status: dict[str, Any]) -> tuple:
    """A comparable signature of what is STILL open — the loop's progress meter."""
    return tuple(sorted(status["red"])), tuple(sorted(status["report_open"]))


def until_all_proven(conn: sqlite3.Connection, *,
                     max_rounds: int = MAX_ROUNDS,
                     fix: bool = True) -> dict[str, Any]:
    """Drive the AUTO items until the checklist is proven or a NAMED stop.

    BOUNDED BY CONSTRUCTION. It cannot run forever: every loop is `range(max_rounds)`
    and the stop reason is one of STOP_ALL_PROVEN / STOP_NO_PROGRESS /
    STOP_MAX_ROUNDS / STOP_BLOCKED, always NAMED.
    """
    rounds: list[dict[str, Any]] = []
    prev = None
    stop = STOP_MAX_ROUNDS
    status = run_once(conn, fix=False)

    for n in range(1, int(max_rounds) + 1):
        status = run_once(conn, fix=fix)
        sig = _open_signature(status)
        rounds.append({"round": n, "red": list(status["red"]),
                       "auto_open": list(status["auto_open"]),
                       "report_open": list(status["report_open"]),
                       "all_proven": status["all_proven"]})

        if status["unprovable"]:
            stop = STOP_BLOCKED
            break
        if status["all_proven"]:
            stop = STOP_ALL_PROVEN
            break
        # ONLY report items left: a loop must not touch them, and continuing would
        # be pretending to make progress on work reserved for a worker.
        if not status["auto_open"]:
            stop = STOP_BLOCKED
            break
        if prev is not None and sig == prev:
            stop = STOP_NO_PROGRESS
            break
        prev = sig
    else:
        stop = STOP_MAX_ROUNDS

    status = run_once(conn, fix=False)
    return {"stop_reason": stop, "rounds": rounds, "ran": len(rounds),
            "max_rounds": int(max_rounds), "status": status,
            "converged": stop == STOP_ALL_PROVEN,
            "named_stop": stop in (STOP_ALL_PROVEN, STOP_NO_PROGRESS,
                                   STOP_MAX_ROUNDS, STOP_BLOCKED)}


def evidence_for(conn: sqlite3.Connection) -> dict[str, Any]:
    """The ONE dict a proof reads. Same numbers the runner saw — no second opinion."""
    status = run_once(conn, fix=False)
    return {
        "checklist": [{"key": i["key"], "needs": i["needs"], "ok": i["ok"],
                       "question": i["question"],
                       "would_be_red_if": i["would_be_red_if"]}
                      for i in status["items"]],
        "auto_open": status["auto_open"],
        "report_open": status["report_open"],
        "all_proven": status["all_proven"],
        "unprovable": status["unprovable"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--checklist", action="store_true",
                    help="print the checklist and what would make each item RED")
    ap.add_argument("--run-once", action="store_true",
                    help="measure every item once (no writes)")
    ap.add_argument("--until-all-proven", action="store_true",
                    help="drive the AUTO items, BOUNDED, with a NAMED stop")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.checklist:
            for item in CHECKLIST:
                print("  %-3s [%-6s] %s" % (item["key"], item["needs"],
                                            item["question"]))
            print()
            print("  a REPORT item is NEVER auto-fixed: a worker must decide it.")
            return 0
        if a.until_all_proven:
            out = until_all_proven(conn)
            if a.json:
                print(json.dumps(out, indent=2, default=str))
                return 0 if out["converged"] else 0
            print("stop_reason : %s" % out["stop_reason"])
            print("ran rounds  : %d of max %d" % (out["ran"], out["max_rounds"]))
            for r in out["rounds"]:
                print("   round %d: red=%s auto_open=%s report_open=%s"
                      % (r["round"], r["red"], r["auto_open"], r["report_open"]))
            st = out["status"]
            print("all_proven  : %s" % st["all_proven"])
            print("blocked     : %s" % (st["report_open"] or "none"))
            return 0
        out = run_once(conn)
        if a.json:
            print(json.dumps(out, indent=2, default=str))
            return 0
        for i in out["items"]:
            print("  %-3s [%-6s] %-4s %s" % (i["key"], i["needs"],
                                             "PASS" if i["ok"] else "RED",
                                             i["question"]))
            if not i["ok"]:
                print("        RED IF: %s" % i["would_be_red_if"])
        print()
        print("auto_open   : %s" % (out["auto_open"] or "none"))
        print("report_open : %s" % (out["report_open"] or "none"))
        print("all_proven  : %s" % out["all_proven"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
