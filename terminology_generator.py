# -*- coding: utf-8 -*-
"""terminology_generator.py — DERIVE a name from its catalog path.

PLAN: qc_evidence/plan_TERMINOLOGY.GENERATOR.AND.UI.LAYER.md (APPROVED)
STEP 3 of 9.

THE HUMAN (2026-09-27), verbatim
--------------------------------
    "you are not helping to have standardize for terminology generator ? with
     catalog > subcatalog"
    "example taskbar > widgets , terminology generator = taskbar_widgets"
    "and we found vscode_taskbar_icon"
    "au to rename to taskbar > vscode > APP"
    "have same language, not different language in different place"

THE PROBLEM, MEASURED
---------------------
MEASURED: `terminology_sweep.draft()` asks the 7B

    "Name this thing from a software repository: %s"

and takes `obj["term_key"]` from the model's JSON. **The name is the MODEL'S
CHOICE, per call.** Two runs can name the same thing differently, and nothing
derives the name from the catalog.

MEASURED: the names are in TWO conventions IN THE SAME PLACE --

    18 rows   taskbar_*           taskbar_widgets, taskbar_start, ...
     2 rows   vscode_taskbar_*    vscode_taskbar_icon, vscode_taskbar_win_n

`taskbar_widgets` puts the CATALOG first; `vscode_taskbar_icon` puts the VENDOR
first. A reader cannot tell which token is the catalog.

THE RULE
--------
    A name is DERIVED from its catalog path, joined by '_'.

        catalog path   taskbar > widgets        ->  taskbar_widgets
        catalog path   taskbar > vscode > app   ->  taskbar_vscode_app

**The catalog path is the SSOT; the name is a PROJECTION of it.** So the name
CANNOT drift from the catalog, because it is computed from it.

**AND the model is removed from the NAME path.** The 7B keeps the DEFINITION
(MEASURED: its definitions were usable every time -- `terminology_sweep.py:20-30`);
the NAME becomes deterministic.

WHAT THIS MODULE IS NOT
-----------------------
* NOT a second catalog. The catalog is `terminology_registry.parent_term_id`
  (`plan_CATALOG.AND.TERMONTOLOGY.STANDARD.md`, APPROVED).
* NOT a renamer that moves terms. `rename()` REFUSES to re-parent an existing
  term -- MEASURED: `vscode` (term_id 3) and `app` (term_id 8) already exist with
  their OWN parents, so moving them is a decision for the human, not for a script.

RUN:
    .\\.venv\\Scripts\\python.exe terminology_generator.py --check
    .\\.venv\\Scripts\\python.exe terminology_generator.py --path taskbar_widgets
    .\\.venv\\Scripts\\python.exe terminology_generator.py --measure
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# THE STDOUT GUARD. MEASURED 2026-09-27: this module is imported by
# `mouse_spot_helper`, which runs under `pythonw.exe` -- and under `pythonw`
# `sys.stdout` is **None**. A bare `sys.stdout.reconfigure(...)` therefore raised
# `AttributeError: 'NoneType' object has no attribute 'reconfigure'` and the API
# route returned 500. The guard is the fix, and it is the same shape the other
# modules in this repo use.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

DEFAULT_DB = BASE / "agent.db"

# The separator between path elements. ONE constant, so the rule has ONE home.
SEPARATOR = "_"

# The catalog this plan works on. Named, not hard-coded into a query string.
TASKBAR_CATALOG = "taskbar"

# The group whose names must agree with the register (QC-11).
TASKBAR_GROUP = "windows_taskbar"


def log(msg: str) -> None:
    print(msg, flush=True)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# THE DERIVATION — the whole point of this module
# ---------------------------------------------------------------------------
#
# THE MODEL, and it was CORRECTED BY RUNNING IT (2026-09-27).
#
# MY FIRST DRAFT joined the FULL path, including the term's own key:
#
#     path_of(taskbar_widgets) -> ['taskbar', 'taskbar_widgets']
#     name_for(...)            -> 'taskbar_taskbar_widgets'      <- DOUBLED
#
# and it reported **20 of 20 as MISMATCH**, including the 18 that are correct.
# The bug was mine: **a term's `term_key` IS its full name, not its leaf
# segment.** So the path must be built from the ANCESTORS, and the leaf segment
# is what the term's own key adds on top of its parent.
#
# THE RULE, stated once:
#
#     A child's term_key MUST START WITH its parent's term_key + '_'.
#
#         taskbar_widgets        parent taskbar          starts with 'taskbar_'   OK
#         taskbar_vscode_app     parent taskbar_vscode   starts with 'taskbar_vscode_'  OK
#         vscode_taskbar_icon    parent taskbar          does NOT start with 'taskbar_'  MISMATCH
#
# The third line is the human's complaint, and the check finds it.

def ancestors(conn: sqlite3.Connection, term_id: int) -> dict[str, Any]:
    """The ANCESTOR chain of a term, root-first, EXCLUDING the term itself.

    Returns `{ok, chain: [{term_id, term_key}, ...], error}`. A CYCLE is
    REPORTED, never looped on: a term that is its own ancestor would make the
    walk infinite.
    """
    out: dict[str, Any] = {"ok": False, "chain": [], "error": None}
    seen: list[int] = []
    chain: list[dict[str, Any]] = []
    row = conn.execute(
        "SELECT term_id, term_key, parent_term_id FROM terminology_registry "
        "WHERE term_id=?", (int(term_id),)).fetchone()
    if not row:
        out["error"] = "no terminology_registry row with term_id=%d" % term_id
        return out
    cur = row["parent_term_id"]
    limit = int(conn.execute(
        "SELECT COUNT(*) FROM terminology_registry").fetchone()[0]) + 1
    for _ in range(limit):
        if cur is None:
            out["ok"] = True
            out["chain"] = list(reversed(chain))
            return out
        if cur in seen:
            out["error"] = ("CYCLE: term_id %d is its own ancestor (chain %s)"
                            % (cur, seen))
            return out
        seen.append(cur)
        p = conn.execute(
            "SELECT term_id, term_key, parent_term_id FROM terminology_registry "
            "WHERE term_id=?", (cur,)).fetchone()
        if not p:
            out["error"] = "no terminology_registry row with term_id=%d" % cur
            return out
        chain.append({"term_id": int(p["term_id"]), "term_key": str(p["term_key"])})
        cur = p["parent_term_id"]
    out["error"] = "walk exceeded the row count (a cycle the seen-list missed)"
    return out


def root_of(conn: sqlite3.Connection, term_id: int) -> dict[str, Any]:
    """The CATALOG ROOT a term belongs to. Used to REFUSE a cross-catalog move."""
    a = ancestors(conn, term_id)
    if not a["ok"]:
        return {"ok": False, "error": a["error"]}
    if not a["chain"]:
        return {"ok": True, "root_id": int(term_id), "root_key": None}
    return {"ok": True, "root_id": a["chain"][0]["term_id"],
            "root_key": a["chain"][0]["term_key"]}


def name_for(parent_key: str | None, segment: str) -> str:
    """The NAME a parent + a leaf segment derive. PURE -- no DB, no model, no clock.

    THIS IS THE RULE, and it is one line so it cannot be read two ways:

        ('taskbar', 'widgets')          ->  taskbar_widgets
        ('taskbar_vscode', 'app')       ->  taskbar_vscode_app
        (None, 'taskbar')               ->  taskbar          (a catalog root)

    An empty segment is REFUSED rather than silently joined, because
    `taskbar_` (a trailing separator) is a name nobody can read.
    """
    seg = str(segment or "").strip()
    if not seg:
        raise ValueError("the leaf segment is empty")
    if parent_key is None:
        return seg
    p = str(parent_key).strip()
    if not p:
        raise ValueError("the parent key is empty")
    return p + SEPARATOR + seg


def expected_name(conn: sqlite3.Connection, term_id: int) -> dict[str, Any]:
    """The name this term SHOULD have, given its parent.

    A term whose key ALREADY starts with `parent_key + '_'` is correct, and its
    expected name is its own key. A term that does NOT is in another convention,
    and the expected name is `parent_key + '_' + <its own key>` -- the mechanical
    fix, shown so a reader can see WHICH convention the row is in.

    A ROOT names itself, so its expected name is its own key.
    """
    row = conn.execute(
        "SELECT term_id, term_key, parent_term_id FROM terminology_registry "
        "WHERE term_id=?", (int(term_id),)).fetchone()
    if not row:
        return {"ok": False, "error": "no term_id=%d" % term_id}
    key = str(row["term_key"])
    if row["parent_term_id"] is None:
        return {"ok": True, "term_key": key, "expected": key, "match": True,
                "parent_key": None, "segment": key}
    a = ancestors(conn, int(term_id))
    if not a["ok"]:
        return {"ok": False, "error": a["error"]}
    parent_key = a["chain"][-1]["term_key"] if a["chain"] else None
    prefix = (parent_key or "") + SEPARATOR
    if key.startswith(prefix):
        return {"ok": True, "term_key": key, "expected": key, "match": True,
                "parent_key": parent_key, "segment": key[len(prefix):]}
    return {"ok": True, "term_key": key, "expected": prefix + key, "match": False,
            "parent_key": parent_key, "segment": key}


def path_segments(conn: sqlite3.Connection, term_id: int) -> dict[str, Any]:
    """The catalog path as the SEGMENT each level adds. THE DISPLAY FORM.

    THE HUMAN (2026-09-27): *"you love rubbish? taskbar_vscode_app!!!????"*

    MEASURED BEFORE, and it was MY OWN CODE: `derived_name` built the path from
    FULL KEYS, so every level repeated its parent:

        taskbar > taskbar_vscode > taskbar_vscode_app      <- RUBBISH

    The human wrote `taskbar > vscode > APP`. The correct display is the SEGMENT
    each level adds:

        taskbar > vscode > app

    **THE NAME STAYS THE FULL KEY.** `taskbar_vscode_app` is CORRECT, because a
    `term_key` must be unique on its own. **The path is a DISPLAY; the name is an
    IDENTIFIER** -- two different things, and the panel must not conflate them.

    A segment is the part of a key that its parent's key does not already cover:
    `taskbar_vscode_app` under parent `taskbar_vscode` -> `app`.
    """
    out: dict[str, Any] = {"ok": False, "segments": [], "keys": [], "error": None}
    a = ancestors(conn, term_id)
    if not a["ok"]:
        out["error"] = a["error"]
        return out
    e = expected_name(conn, term_id)
    if not e.get("ok"):
        out["error"] = e.get("error")
        return out
    keys = [c["term_key"] for c in a["chain"]] + [e["term_key"]]
    segs: list[str] = []
    prev = ""
    for k in keys:
        prefix = (prev + SEPARATOR) if prev else ""
        segs.append(k[len(prefix):] if k.startswith(prefix) else k)
        prev = k
    out["ok"] = True
    out["keys"] = keys
    out["segments"] = segs
    return out


def derived_name(conn: sqlite3.Connection, term_id: int) -> dict[str, Any]:
    """`ancestors` + `expected_name`, as one call. The name a term SHOULD have.

    Returns BOTH forms, and the distinction is the point (see `path_segments`):

        `path`      -- the SEGMENTS, for DISPLAY:  ['taskbar','vscode','app']
        `full_path` -- the KEYS, for a caller that needs them:
                       ['taskbar','taskbar_vscode','taskbar_vscode_app']
        `name`      -- the term's own full key:    'taskbar_vscode_app'

    MEASURED DEFECT this fixes: the first version returned ONLY the full-key form
    as `path`, so the UI rendered `taskbar > taskbar_vscode > taskbar_vscode_app`.
    """
    e = expected_name(conn, term_id)
    if not e.get("ok"):
        return e
    ps = path_segments(conn, term_id)
    if not ps["ok"]:
        return {"ok": False, "error": ps["error"]}
    return {"ok": True,
            "path": ps["segments"],
            "full_path": ps["keys"],
            "name": e["expected"], "match": e["match"], "error": None}


# ---------------------------------------------------------------------------
# THE NODE KIND — is this a GROUP or a LEAF?
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-27), verbatim
# ---------------------------------
#     "i have taskbar_vscode and taskbar_vscode_app"
#     "which is rubbish"
#     "or problem is term_key kind layer definition cite_ref -> table design"
#     "why taskbar_vscode not = taskbar_vscode_app"
#
# THE PROBLEM, MEASURED
# ---------------------
# MEASURED: `taskbar_vscode` (1489) has **2 children**; `taskbar_vscode_app`
# (1474) has **0**. **Both carry `term_kind='entity'`.** So a reader looking at
# the table sees two identical rows and cannot tell which is which.
#
# MEASURED: the DDL's CHECK is
#
#     term_kind IN ('count','action','role','entity','qualifier','part')
#
# -- **there is no `group`**. And MEASURED: the table stores **no column** saying
# group vs leaf. MEASURED population: **7 terms have children; 1490 do not.**
#
# THE DECISION (plan_TERMINOLOGY.GROUP.VS.LEAF.AND.INSTANCE.NAMES.md, APPROVED)
# -----------------------------------------------------------------------------
# **A GROUP is DERIVED, not stored.**
#
#   REJECTED: adding `group` to `term_kind`'s CHECK -- `term_kind` answers
#             *"what kind of thing is this"*; a group is not a KIND of thing, it
#             is a **position in the tree**. Adding it would make `term_kind`
#             answer two questions.
#   REJECTED: adding an `is_group` column -- it is a **second truth**. It can
#             drift from `children > 0`, and a drifted `is_group` is worse than
#             none.
#   CHOSEN:   DERIVE it from `children > 0`, and SHOW it.
#
# **WHY:** the fact is **already in the table** (`parent_term_id`), and a derived
# value **cannot drift**. The defect is not that the fact is missing; it is that
# **nothing SHOWS it**. So the fix is a READER and a COLUMN, not a schema change.
#
# **AND it is ONE function**, so the API, the UI and the proof cannot disagree
# about what a group is.

NODE_GROUP = "group"
NODE_LEAF = "leaf"


def node_kind(conn: sqlite3.Connection, term_id: int) -> dict[str, Any]:
    """Is this term a GROUP (it has children) or a LEAF (it has none)?

    THE ONE READER. Every caller -- the API, the UI, the proof -- goes through
    this function, so they cannot disagree about what a group is.

    Returns:
        {"ok": True, "term_id": int, "term_key": str,
         "node_kind": "group"|"leaf", "children": int}

    **DERIVED, never stored.** The value is `children > 0`, computed here, so it
    cannot drift from `parent_term_id` -- the only place the fact lives.
    """
    row = conn.execute(
        "SELECT term_id, term_key FROM terminology_registry WHERE term_id=?",
        (int(term_id),)).fetchone()
    if not row:
        return {"ok": False, "error": "no term_id=%d" % term_id}
    n = conn.execute(
        "SELECT COUNT(*) FROM terminology_registry WHERE parent_term_id=?",
        (int(term_id),)).fetchone()[0]
    n = int(n)
    return {"ok": True, "term_id": int(row["term_id"]),
            "term_key": str(row["term_key"]),
            "node_kind": NODE_GROUP if n > 0 else NODE_LEAF,
            "children": n}


def node_kind_by_key(conn: sqlite3.Connection, term_key: str) -> dict[str, Any]:
    """`node_kind` by KEY, for a caller that has the name and not the id."""
    row = conn.execute(
        "SELECT term_id FROM terminology_registry WHERE term_key=?",
        (str(term_key),)).fetchone()
    if not row:
        return {"ok": False, "error": "no term_key=%r" % term_key}
    return node_kind(conn, int(row["term_id"]))


def node_kind_agreement(conn: sqlite3.Connection) -> dict[str, Any]:
    """QC-02: does `node_kind` AGREE with `children > 0` for EVERY term?

    This is the check that makes the derivation trustworthy: it walks the WHOLE
    register and counts the disagreements. **0 is the only passing value.**

    It is deliberately a SEPARATE function from `node_kind`, so the check does
    not reuse the thing it is checking -- it recomputes `children > 0` in SQL and
    compares.
    """
    rows = conn.execute(
        "SELECT t.term_id, t.term_key, "
        "  (SELECT COUNT(*) FROM terminology_registry c "
        "   WHERE c.parent_term_id=t.term_id) AS kids "
        "FROM terminology_registry t").fetchall()
    checked = 0
    disagree: list[dict[str, Any]] = []
    for r in rows:
        checked += 1
        kids = int(r["kids"])
        want = NODE_GROUP if kids > 0 else NODE_LEAF
        got = node_kind(conn, int(r["term_id"]))
        if not got.get("ok") or got.get("node_kind") != want:
            disagree.append({"term_id": int(r["term_id"]),
                             "term_key": str(r["term_key"]),
                             "children": kids, "want": want,
                             "got": got.get("node_kind")})
    return {"ok": True, "checked": checked, "disagreements": disagree,
            "disagreement_count": len(disagree)}


# ---------------------------------------------------------------------------
# THE 5W1H — six questions, and the ones that have NO answer
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-27), verbatim
# ---------------------------------
#     "fuck! how to you have correct 5W1H in easy"
#
# THE PROBLEM, MEASURED
# ---------------------
# MEASURED, the register answers 4 of 6:
#
#     WHAT  = term_key     (the KIND)       -> taskbar_chrome      PRESENT
#     WHICH = ???          (the INSTANCE)   -> MISSING
#     WHERE = coordinate                    -> PRESENT
#     WHEN  = ???                           -> MISSING
#     WHY   = definition                    -> PRESENT
#     HOW   = field_type                    -> PRESENT
#
# **AND THE ANSWER TO "how to have correct 5W1H in easy" IS:**
#
#     a NAME answers WHAT; a FIELD answers WHICH.
#
# The previous plan made the name a KIND (correct) but left WHICH with no home
# (incomplete). **A question with no column is a question nobody can answer.**
#
# WHY THIS IS A FUNCTION AND NOT A DOCUMENT
# -----------------------------------------
# A 5W1H written in a doc is a slogan. **A 5W1H that RETURNS the missing answers
# is a CHECK** -- it can be run, it can be asserted, and it cannot drift from the
# columns it reads.

W1H_QUESTIONS = ("what", "which", "where", "when", "why", "how")


def completeness_5w1h(conn: sqlite3.Connection, target_name: str) -> dict[str, Any]:
    """The 6 answers for ONE target, and the ones that are MISSING.

    RENAMED 2026-09-27 from `five_w1h`. THE HUMAN: "`five_w1h`=462, this is
    wrong spelling BUG, have totally rename and fix, but you can have that,
    should gone away forever".

    `five_w1h` spelled the digit as the WORD `five` and then used the
    abbreviation `w1h` -- it was neither `five_w_one_h` nor `5w1h`. The repo's
    OWN convention puts `5w1h` at the END (`skill_5w1h`, `ticket_5w1h`,
    `derive_5w1h`), and Python forbids an identifier starting with a digit, so
    the correct name is `completeness_5w1h`.

    Returns:
        {"ok": True, "target": str,
         "answers": {"what": ..., "which": ..., "where": ..., "when": ...,
                     "why": ..., "how": ...},
         "missing": ["which", ...], "complete": bool}

    **A MISSING answer is NAMED, never silently passed.** A target whose WHICH
    is unanswerable is a target a machine cannot find, and that is a finding.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(target_template)")}
    has_sel = "selector" in cols and "selector_kind" in cols
    # MEASURED DEFECT (2026-09-27): the first draft read `row["selector"]` but the
    # SELECT did not list it, so `sqlite3.Row` raised `IndexError: No item with
    # that key`. **A column that is read must be SELECTed** -- the migration adds
    # it, but the query must ask for it.
    sel_cols = ", selector, selector_kind" if has_sel else ""
    row = conn.execute(
        "SELECT id, name, label, field_type, cite_ref" + sel_cols +
        " FROM target_template WHERE name=?", (str(target_name),)).fetchone()
    if not row:
        return {"ok": False, "error": "no target_template row named %r"
                % target_name}
    sel = str(row["selector"]).strip() if has_sel else "NA"
    sel_kind = str(row["selector_kind"]).strip() if has_sel else "NA"

    # WHAT -- the KIND. The name is the kind, and the term is its definition.
    term = conn.execute(
        "SELECT term_key, definition FROM terminology_registry WHERE term_key=?",
        (str(row["name"]),)).fetchone()
    what = ({"kind": str(row["name"]),
             "definition": str(term["definition"]) if term else None}
            if term else None)

    # WHICH -- the INSTANCE. The selector, and WHAT it matches.
    which = ({"selector": sel, "selector_kind": sel_kind}
             if (sel and sel != "NA" and sel_kind and sel_kind != "NA") else None)

    # WHERE -- the coordinate. MEASURED: it lives in `environment_template`,
    # keyed by `template_id`, so a target with no collected row has no WHERE.
    env = conn.execute(
        "SELECT environment_id, x1, y1, x2, y2, cx, cy, hotkey "
        "FROM environment_template WHERE template_id=? AND is_active=1 "
        "ORDER BY environment_id LIMIT 1", (int(row["id"]),)).fetchone()
    where = dict(env) if env else None

    # WHEN -- MEASURED: there is NO column. A target is not time-scoped today.
    when = None

    # WHY -- the definition, which says what it IS and what it is NOT.
    why = str(term["definition"]) if term else None

    # HOW -- the field_type, which decides WHICH value columns carry the value.
    how = str(row["field_type"]) if row["field_type"] else None

    answers = {"what": what, "which": which, "where": where, "when": when,
               "why": why, "how": how}
    missing = [q for q in W1H_QUESTIONS if not answers.get(q)]
    return {"ok": True, "target": str(row["name"]), "answers": answers,
            "missing": missing, "complete": not missing,
            "note": ("a NAME answers WHAT; a FIELD answers WHICH. A question "
                     "with no column is a question nobody can answer.")}


def completeness_5w1h_report(conn: sqlite3.Connection, *, prefix: str = "taskbar"
                             ) -> dict[str, Any]:
    """The 5W1H for EVERY target whose name starts with `prefix`.

    RENAMED 2026-09-27 from `five_w1h_report` (see `completeness_5w1h`).

    The population is NAMED (`prefix`), so a count here is a count of THIS set
    and not of the whole table -- the `measurement-scope` rule.
    """
    names = [r["name"] for r in conn.execute(
        "SELECT name FROM target_template WHERE name LIKE ? ORDER BY name",
        (prefix + "%",))]
    rows = [completeness_5w1h(conn, n) for n in names]
    rows = [r for r in rows if r.get("ok")]
    by_q: dict[str, int] = {q: 0 for q in W1H_QUESTIONS}
    for r in rows:
        for q in r["missing"]:
            by_q[q] += 1
    return {"ok": True, "prefix": prefix, "checked": len(rows),
            "complete": sum(1 for r in rows if r["complete"]),
            "missing_by_question": by_q, "rows": rows}


# ---------------------------------------------------------------------------
# THE CHECK — a mismatch is a DEFECT, reported, never silently fixed
# ---------------------------------------------------------------------------

def check(conn: sqlite3.Connection, *, root_key: str = TASKBAR_CATALOG,
          group_key: str | None = TASKBAR_GROUP) -> dict[str, Any]:
    """Every term under `root_key`: does its `term_key` EQUAL its derived name?

    A mismatch is REPORTED with both values, so a reader can see WHICH convention
    the row is in. This function WRITES NOTHING.
    """
    out: dict[str, Any] = {"ok": False, "root_key": root_key, "rows": [],
                           "mismatches": [], "error": None}
    try:
        root = conn.execute(
            "SELECT term_id FROM terminology_registry "
            "WHERE term_key=? AND parent_term_id IS NULL", (root_key,)).fetchone()
        if not root:
            out["error"] = "no ROOT term %r" % root_key
            return out
        root_id = int(root["term_id"])
        # EVERY descendant, not just the direct children: a 3-level path
        # (taskbar > vscode > app) must be checked too.
        rows = conn.execute(
            "WITH RECURSIVE tree(term_id, term_key, parent_term_id, depth) AS ("
            "  SELECT term_id, term_key, parent_term_id, 0 "
            "  FROM terminology_registry WHERE term_id=? "
            "  UNION ALL "
            "  SELECT t.term_id, t.term_key, t.parent_term_id, tree.depth+1 "
            "  FROM terminology_registry t JOIN tree ON t.parent_term_id = tree.term_id"
            ") SELECT term_id, term_key, depth FROM tree ORDER BY depth, term_key",
            (root_id,)).fetchall()
        for r in rows:
            d = derived_name(conn, int(r["term_id"]))
            nk = node_kind(conn, int(r["term_id"]))
            rec = {"term_id": int(r["term_id"]), "term_key": r["term_key"],
                   "depth": int(r["depth"]),
                   "path": d.get("path"), "derived": d.get("name"),
                   "node_kind": nk.get("node_kind"),
                   "children": nk.get("children"),
                   "match": bool(d.get("ok")) and d.get("name") == r["term_key"]}
            out["rows"].append(rec)
            if not rec["match"]:
                out["mismatches"].append(rec)
        out["ok"] = True
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def name_drift(conn: sqlite3.Connection, *,
               group_key: str = TASKBAR_GROUP) -> dict[str, Any]:
    """QC-11: does `target_template.name` AGREE with `terminology_registry.term_key`?

    The two tables hold the same names for the same things. A name that exists in
    one and not the other is DRIFT, and it is reported with both sides.
    """
    out: dict[str, Any] = {"ok": False, "agree": [], "only_in_target": [],
                           "only_in_registry": [], "error": None}
    try:
        tnames = {r["name"] for r in conn.execute(
            "SELECT t.name FROM target_template t "
            "JOIN target_group g ON g.id = t.group_id "
            "WHERE g.group_key = ?", (group_key,))}
        rnames = {r["term_key"] for r in conn.execute(
            "SELECT term_key FROM terminology_registry "
            "WHERE term_key LIKE ?", (TASKBAR_CATALOG + SEPARATOR + "%",))}
        out["agree"] = sorted(tnames & rnames)
        out["only_in_target"] = sorted(tnames - rnames)
        out["only_in_registry"] = sorted(rnames - tnames)
        out["ok"] = True
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


# ---------------------------------------------------------------------------
# THE WRITE — generate, and rename WITHOUT re-parenting
# ---------------------------------------------------------------------------

def generate(conn: sqlite3.Connection, *, catalog_key: str, entry_key: str,
             definition: str, cite_ref: str, term_kind: str = "entity",
             taxonomy_level: str = "ui_element",
             commit: bool = True) -> dict[str, Any]:
    """Create a term whose NAME is DERIVED from its catalog path.

    The caller supplies the PATH (`catalog_key` + `entry_key`) and the MEANING
    (`definition` + `cite_ref`). The NAME is computed, never supplied -- so a
    caller cannot introduce a second convention.

    DELEGATES the write to `terminology_registry.add_term`, so every existing
    gate (spelling, naming form, citation checkability, scratch-cite refusal,
    taxonomy level, unknown parent) applies unchanged. This module adds NO check
    of its own and removes none.
    """
    import terminology_registry as tr

    root = conn.execute(
        "SELECT term_id FROM terminology_registry "
        "WHERE term_key=? AND parent_term_id IS NULL", (catalog_key,)).fetchone()
    if not root:
        return {"ok": False, "code": "UNKNOWN_CATALOG",
                "message": "no ROOT term %r (a catalog root has "
                           "parent_term_id IS NULL)" % catalog_key}
    root_id = int(root["term_id"])
    try:
        derived = name_for(catalog_key, entry_key)
    except ValueError as exc:
        return {"ok": False, "code": "BAD_PATH", "message": str(exc)}

    res = tr.add_term(conn, derived, definition=definition, cite_ref=cite_ref,
                      term_kind=term_kind, parent_term_id=root_id,
                      taxonomy_level=taxonomy_level, commit=commit)
    res["derived_name"] = derived
    res["path"] = [catalog_key, entry_key]
    return res


def rename(conn: sqlite3.Connection, term_id: int, *, new_segment: str,
           new_parent_key: str | None = None, cite_ref: str,
           commit: bool = True) -> dict[str, Any]:
    """Rename a term to its DERIVED name, and record the OLD name as an ALIAS.

    THE REFUSAL, and it is the plan's own rule, NARROWED BY MEASUREMENT:

    **REFUSES to move a term OUT of its catalog root.** MEASURED: `vscode`
    (term_id 3) and `app` (term_id 8) already exist with their OWN parents, in
    OTHER catalogs. Moving THOSE would change what another catalog contains, which
    is a decision for the human.

    **BUT a move WITHIN the same catalog root is ALLOWED**, because that is
    exactly what the human asked for:

        "au to rename to taskbar > vscode > APP"

    `vscode_taskbar_icon` is IN the taskbar catalog; placing it at
    `taskbar > vscode > app` keeps it in the same catalog. Refusing that would
    refuse the instruction.

    `new_segment` is the LEAF segment only (e.g. `app`), NOT the full name: the
    parent's key is prepended by `name_for`, so a caller cannot introduce a second
    convention by passing a full name.

    The OLD name goes into `alias_list`, so nothing that uses it breaks.
    """
    import terminology_registry as tr

    row = conn.execute(
        "SELECT term_id, term_key, parent_term_id, alias_list, cite_ref "
        "FROM terminology_registry WHERE term_id=?", (int(term_id),)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_TERM",
                "message": "no terminology_registry row with term_id=%d" % term_id}

    old_key = str(row["term_key"])
    old_parent = row["parent_term_id"]

    if old_parent is None:
        return {"ok": False, "code": "REFUSES_REPARENT",
                "message": ("REFUSED: %r is a CATALOG ROOT (parent_term_id IS "
                            "NULL). A root names itself, so it has no parent to "
                            "derive from." % old_key)}

    # THE CATALOG ROOT IS THE BOUNDARY. A move that keeps the same root is a
    # re-arrangement INSIDE one catalog; a move that changes the root would take
    # the term out of a catalog that is not this plan's to change.
    here = root_of(conn, int(term_id))
    if not here["ok"]:
        return {"ok": False, "code": "BAD_PATH", "message": here["error"]}

    if new_parent_key is None:
        a = ancestors(conn, int(term_id))
        if not a["ok"]:
            return {"ok": False, "code": "BAD_PATH", "message": a["error"]}
        parent_key = a["chain"][-1]["term_key"] if a["chain"] else None
        new_parent_id = old_parent
    else:
        p = conn.execute(
            "SELECT term_id, term_key FROM terminology_registry "
            "WHERE term_key=?", (new_parent_key,)).fetchone()
        if not p:
            return {"ok": False, "code": "UNKNOWN_PARENT",
                    "message": "no term %r to be the new parent" % new_parent_key}
        new_parent_id = int(p["term_id"])
        parent_key = str(p["term_key"])
        there = root_of(conn, new_parent_id)
        if not there["ok"]:
            return {"ok": False, "code": "BAD_PATH", "message": there["error"]}
        if int(there["root_id"]) != int(here["root_id"]):
            return {"ok": False, "code": "REFUSES_REPARENT",
                    "message": ("REFUSED: this would move %r OUT of catalog %r "
                                "into catalog %r. A term belongs to ONE catalog, "
                                "and moving it between catalogs changes what "
                                "ANOTHER catalog contains -- a decision for the "
                                "human, not for a script."
                                % (old_key, here.get("root_key"),
                                   there.get("root_key")))}

    try:
        new_key = name_for(parent_key, new_segment)
    except ValueError as exc:
        return {"ok": False, "code": "BAD_PATH", "message": str(exc)}

    if new_key == old_key and new_parent_id == old_parent:
        return {"ok": True, "changed": False, "term_id": int(term_id),
                "term_key": old_key, "message": "already the derived name"}

    # REFUSAL 2 -- the name is taken under the target parent.
    taken = conn.execute(
        "SELECT term_id FROM terminology_registry "
        "WHERE IFNULL(parent_term_id,-1)=IFNULL(?,-1) AND term_key=? "
        "AND term_id<>?", (new_parent_id, new_key, int(term_id))).fetchone()
    if taken:
        return {"ok": False, "code": "NAME_TAKEN",
                "message": ("%r is already term_id=%d under the same parent"
                            % (new_key, taken["term_id"]))}

    # The OLD name becomes an ALIAS, so a caller using it still resolves.
    aliases = tr._as_alias_list(row["alias_list"])
    if old_key not in aliases:
        aliases.append(old_key)

    conn.execute(
        "UPDATE terminology_registry SET term_key=?, parent_term_id=?, "
        "alias_list=?, updated_at=datetime('now') WHERE term_id=?",
        (new_key, new_parent_id, json.dumps(aliases, ensure_ascii=False),
         int(term_id)))
    if commit:
        conn.commit()
    return {"ok": True, "changed": True, "term_id": int(term_id),
            "old_key": old_key, "term_key": new_key,
            "old_parent_id": old_parent, "new_parent_id": new_parent_id,
            "aliases": aliases, "cite_ref": cite_ref}


# ---------------------------------------------------------------------------
# MEASURE
# ---------------------------------------------------------------------------

def measure(db_path: str | Path | None = None) -> dict[str, Any]:
    """The whole measurement, as one dict. Writes NOTHING."""
    conn = _connect(db_path)
    try:
        out: dict[str, Any] = {"ok": True}
        out["levels"] = [dict(r) for r in conn.execute(
            "SELECT level_key, level_order, is_active "
            "FROM taxonomy_level_registry ORDER BY level_order")]
        out["levels_active"] = sum(1 for r in out["levels"] if r["is_active"])
        out["ui_element_terms"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry "
            "WHERE taxonomy_level='ui_element'").fetchone()[0]
        out["taskbar_na"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry "
            "WHERE parent_term_id=1468 AND taxonomy_level='NA'").fetchone()[0]
        out["check"] = check(conn)
        out["drift"] = name_drift(conn)
        return out
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="derive a name from its catalog path")
    ap.add_argument("--check", action="store_true",
                    help="every term under the catalog: does its name match?")
    ap.add_argument("--path", metavar="TERM_KEY",
                    help="print the catalog path and the derived name")
    ap.add_argument("--node", metavar="TERM_KEY",
                    help="print whether the term is a GROUP or a LEAF")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.path:
            row = conn.execute(
                "SELECT term_id FROM terminology_registry WHERE term_key=?",
                (args.path,)).fetchone()
            if not row:
                log("no term %r" % args.path)
                return 1
            d = derived_name(conn, int(row["term_id"]))
            log("term_key : %s" % args.path)
            log("path     : %s" % " > ".join(d.get("path") or []))
            log("derived  : %s" % d.get("name"))
            log("match    : %s" % d.get("match"))
            return 0

        if args.node:
            n = node_kind_by_key(conn, args.node)
            if not n.get("ok"):
                log("no term %r" % args.node)
                return 1
            log("term_key  : %s" % n["term_key"])
            log("node_kind : %s" % n["node_kind"])
            log("children  : %d" % n["children"])
            return 0

        if args.check:
            r = check(conn)
            if not r["ok"]:
                log("check FAILED: %s" % r["error"])
                return 1
            log("checked %d term(s) under %r" % (len(r["rows"]), r["root_key"]))
            for rec in r["rows"]:
                log("  %-4s %-30s path=%-34s derived=%-30s %s"
                    % (rec["depth"], rec["term_key"],
                       " > ".join(rec["path"] or []), rec["derived"],
                       "OK" if rec["match"] else "MISMATCH"))
            log("")
            log("MISMATCHES: %d" % len(r["mismatches"]))
            for m in r["mismatches"]:
                log("  %s  should be  %s" % (m["term_key"], m["derived"]))
            d = name_drift(conn)
            log("")
            log("QC-11 drift: agree=%d only_in_target=%s only_in_registry=%s"
                % (len(d["agree"]), d["only_in_target"], d["only_in_registry"]))
            return 0 if not r["mismatches"] else 2

        m = measure(args.db)
        log(json.dumps(m, indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
