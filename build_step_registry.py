# -*- coding: utf-8 -*-
"""build_step_registry.py — THE BUILD STEP AS A ROW, and the standard it carries.

SCOPE A of `qc_evidence/plan_GITHUB.CONSULTANT.UPGRADE.md` (APPROVED 2026-09-28).

THE HUMAN, verbatim:

    "they should have build step, and the is the plcae can by table standardize
     to have manage"
    "what is the key for question to find at github? = factor = what?"
    "is wrokflow and playwright too"

THE RULING THIS MODULE IMPLEMENTS
---------------------------------
* **D1** the build step IS the unit of the standard, and it lives in a TABLE.
* **D2** the step's KEY is the **factor** (`factor_key`), because the factor is
  what names the thing to FIND. The HOW is a numbered step list.
* **D4** the "document" axis is `cite_ref` — MEASURED: `document` is NOT a
  registered term while `cite` IS, so a `document` column would be a 4th private
  spelling of one concept (`terminology-register` refuses that).
* **D7/O8** the 5W1H questions `when` and `where` have NO home in
  `factor_first_principle.FIRST_PRINCIPLE_QUESTIONS` — MEASURED, that set is
  FIVE by invariant ("expanding to six would break the name 'first principle'").
  A BUILD STEP needs an order and a home, so the questions live HERE, in
  `STEP_QUESTIONS`, which covers all six dimensions.

WHY A NEW TABLE AND NOT AN EXTENSION OF `workflow_step`
-------------------------------------------------------
MEASURED: `workflow_step` (103 rows) already carries `step_kind`,
`question_template`, `expected`, `parser`, `step_status`; `playwright_step` (26)
carries `expect`, `proof`, `cite_ref`; `skill_prompt_step` (0) carries
`question_template`. **The missing piece is not a step table — it is THE JOIN:**
one row that names its factor, its layer, its module, its route, its purpose, its
workflow, its playwright and its cite, so a reader can see the WHOLE build. That
join is what this register is, and it POINTS AT the existing tables rather than
replacing them.

READ-ONLY BY DEFAULT. `upsert_step` writes; nothing else does.
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
# THE 5W1H QUESTIONS FOR A BUILD STEP — the home O8 asked for.
#
# `factor_first_principle.FIRST_PRINCIPLE_QUESTIONS` is FIVE by invariant and
# covers `why, what, what, how, who`. MEASURED: `uncovered_dimensions()` returns
# `('when', 'where')`, and that is CORRECT for a factor. A STEP is a different
# object: it has an ORDER and a HOME, so its questions cover all six.
#
# EACH QUESTION NAMES A UNIT, because the same law that binds a factor binds the
# question that derives it (D6): a question whose answer is not a unit cannot
# produce a measurable factor.
# ---------------------------------------------------------------------------
STEP_QUESTIONS = (
    ("what", "What does this step CHANGE, named concretely?",
     "count of artifacts the step writes"),
    ("why", "Why is this step needed — what breaks without it?",
     "count of defects this step prevents"),
    ("who", "Who runs it, and who approves the result?",
     "count of owners named for the step"),
    ("when", "When does it happen — what must already be done, and what depends "
             "on it?",
     "count of steps that must already be done before it"),
    ("where", "Where does the artifact live — the exact path or route?",
     "count of files or routes the step resolves to"),
    ("how", "How is it verified — which command or check can FAIL?",
     "count of checks that can fail for this step"),
)

# The dimensions, IMPORTED from the ONE place they are declared. A second copy
# here is the drift this mapping exists to remove.
import skill_5w1h as fw  # noqa: E402

# ---------------------------------------------------------------------------
# THE STEP KIND VOCABULARY IS A REGISTER, NOT A LITERAL (QC-08).
#
# MEASURED DEFECT this avoids: `playwright_step_registry.METHODS` is a tuple in
# code, so adding a method is a RELEASE. A kind is a row here, so a new kind is
# an INSERT — and `upsert_step` READS the table, so a kind that is not a row is
# refused rather than silently accepted.
# ---------------------------------------------------------------------------
SEED_KINDS = (
    ("schema", "a table, a column or an index is created or changed"),
    ("module", "a Python module or a function is written"),
    ("route", "an HTTP route is added or changed"),
    ("ui", "a page, a table or an element is rendered"),
    ("seed", "rows are written from a cited source"),
    ("proof", "a check is written that can FAIL"),
    ("register", "a vocabulary or a register is populated"),
)

_CREATE_KIND_SQL = """
CREATE TABLE IF NOT EXISTS build_step_kind_registry (
    kind_key    TEXT PRIMARY KEY,
    definition  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_STEP_SQL = """
CREATE TABLE IF NOT EXISTS build_step_registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT    NOT NULL,
    layer_key     TEXT    NOT NULL,
    step_no       INTEGER NOT NULL,
    step_kind     TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    capability    TEXT    NOT NULL,
    module        TEXT    NOT NULL DEFAULT 'NA',
    route         TEXT    NOT NULL DEFAULT 'NA',
    purpose       TEXT    NOT NULL,
    workflow_id   INTEGER,
    playwright_id INTEGER,
    owner         TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'planned',
    factor_key    TEXT    NOT NULL,
    source        TEXT    NOT NULL DEFAULT 'local',
    evidence_ref  TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, layer_key, step_no),
    FOREIGN KEY (workflow_id)   REFERENCES workflow_registry (workflow_id),
    FOREIGN KEY (playwright_id) REFERENCES playwright_environment (id)
)
"""

# ---------------------------------------------------------------------------
# THE 17-COLUMN READ VIEW.
#
# THE PLAN'S LIST HAD 17 ENTRIES WITH ONE DUPLICATE, and this is the resolution:
# entry 12 (`document`) and entry 17 (`cite_ref`) are THE SAME AXIS (D4), so
# keeping both would put two names on one concept. The duplicate is removed and
# the freed slot carries `evidence_ref`, which is a DIFFERENT axis (the run that
# measured it, not the source that justifies it). MEASURED: `document` is not a
# registered term; `cite` is.
# ---------------------------------------------------------------------------
_CREATE_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS build_step_view AS
SELECT
    s.id                                        AS id,
    s.step_no                                   AS step_no,
    s.name                                      AS name,
    s.step_kind                                 AS kind,
    s.layer_key                                 AS layer_key,
    COALESCE(t.definition, 'NA')                AS layer,
    s.capability                                AS capability,
    s.module                                    AS module,
    s.route                                     AS route,
    s.purpose                                   AS purpose,
    COALESCE(w.workflow_key, 'NA')              AS workflow,
    COALESCE(p.name, 'NA')                      AS playwright,
    s.cite_ref                                  AS cite,
    s.owner                                     AS owner,
    s.status                                    AS status,
    s.source                                    AS source,
    s.evidence_ref                              AS evidence
FROM build_step_registry s
LEFT JOIN terminology_registry t
       ON t.term_key = s.layer_key AND t.is_active = 1
LEFT JOIN workflow_registry w
       ON w.workflow_id = s.workflow_id
LEFT JOIN playwright_environment p
       ON p.id = s.playwright_id
WHERE s.is_active = 1
"""

VIEW_COLUMNS = ("id", "step_no", "name", "kind", "layer_key", "layer",
                "capability", "module", "route", "purpose", "workflow",
                "playwright", "cite", "owner", "status", "source", "evidence")

STATUSES = ("planned", "in_progress", "done", "blocked", "retired")


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the kind register, the step register and the 17-column view.

    The VIEW is DROPPED and recreated, because a view is a QUERY and an old
    definition would silently keep serving the old columns after a change — the
    same defect as a stale build. The TABLES are `IF NOT EXISTS`, so no row is
    ever lost by calling this.
    """
    conn.execute(_CREATE_KIND_SQL)
    conn.execute(_CREATE_STEP_SQL)
    conn.execute("DROP VIEW IF EXISTS build_step_view")
    conn.execute(_CREATE_VIEW_SQL)
    for k, d in SEED_KINDS:
        conn.execute(
            "INSERT INTO build_step_kind_registry (kind_key, definition) "
            "VALUES (?,?) ON CONFLICT(kind_key) DO UPDATE SET "
            "definition=excluded.definition, updated_at=CURRENT_TIMESTAMP",
            (k, d))
    conn.commit()
    return {"ok": True, "kinds": len(SEED_KINDS), "view_columns": len(VIEW_COLUMNS)}


def step_kinds(conn: sqlite3.Connection) -> list[str]:
    """The ACTIVE kinds, READ from the register (never a literal in code)."""
    ensure_schema(conn)
    return [str(r[0]) for r in conn.execute(
        "SELECT kind_key FROM build_step_kind_registry WHERE is_active=1 "
        "ORDER BY kind_key")]


def upsert_step(conn: sqlite3.Connection, *, task_id: str, layer_key: str,
                step_no: int, step_kind: str, name: str, capability: str,
                purpose: str, owner: str, factor_key: str, cite_ref: str,
                module: str = NA, route: str = NA,
                workflow_id: int | None = None,
                playwright_id: int | None = None, status: str = "planned",
                source: str = "local", evidence_ref: str = NA,
                is_active: int = 1) -> dict[str, Any]:
    """Register ONE build step. Returns a verdict; NEVER raises.

    THE FIVE RULES, each a REFUSAL at the write site (the plan's §3.1):

      R1 `UNKNOWN_FACTOR`      -- `factor_key` must resolve in
                                  `skill_factor_registry`. A step whose key names
                                  nothing cannot be found, compared or scored.
      R2 `UNCITEABLE_CITE_REF` -- the cite must be `path:LINE` or a command, and
                                  a SCRATCH file is refused for a NEW step.
      R3 `MISSING_PURPOSE`     -- a step that cannot say WHY it exists is a task,
                                  not a step.
      R4 `UNREGISTERED_NAME`   -- the name must be a registered term. A NEW name
                                  is registered FIRST (`terminology_registry.
                                  add_term`), which runs the naming law.
      R5 `UNKNOWN_WORKFLOW` / `UNKNOWN_PLAYWRIGHT` -- a link must RESOLVE. A link
                                  row that references nothing is an uncheckable
                                  claim.
    """
    # ---- the cheap shape checks first -------------------------------------
    for field, value in (("task_id", task_id), ("layer_key", layer_key),
                         ("name", name), ("capability", capability),
                         ("owner", owner), ("factor_key", factor_key)):
        if not str(value or "").strip():
            return {"ok": False, "code": "MISSING_%s" % field.upper(),
                    "message": "a build step needs a %s" % field}
    if not str(purpose or "").strip() or str(purpose).strip() == NA:
        return {"ok": False, "code": "MISSING_PURPOSE",
                "message": "a step must state WHY it exists (`purpose`)"}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no row"}
    st = str(status or "").strip().lower()
    if st not in STATUSES:
        return {"ok": False, "code": "BAD_STATUS",
                "message": "status must be one of %s, got %r"
                           % (", ".join(STATUSES), status)}
    try:
        sno = int(step_no)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_STEP_NO",
                "message": "step_no must be an integer, got %r" % (step_no,)}

    try:
        ensure_schema(conn)

        # ---- R1: the factor must EXIST ------------------------------------
        f = conn.execute("SELECT factor_key, metric_unit FROM "
                         "skill_factor_registry WHERE factor_key=?",
                         (str(factor_key),)).fetchone()
        if not f:
            return {"ok": False, "code": "UNKNOWN_FACTOR",
                    "message": "no skill_factor_registry row for factor_key=%r. "
                               "The factor IS the key (D2): a step whose key "
                               "names nothing cannot be found or scored."
                               % factor_key}

        # ---- R2: the cite must be CHECKABLE -------------------------------
        try:
            import terminology_cite as tc
            ok, why = tc.verify_cite_ref(str(cite_ref))
            if not ok:
                return {"ok": False, "code": "UNCITEABLE_CITE_REF",
                        "message": "cite_ref %r is not checkable: %s"
                                   % (cite_ref, why)}
            # A SCRATCH file is deleted after its run, so it is not evidence for
            # a NEW step. The same rule `terminology_registry.add_term` applies.
            #
            # MEASURED 2026-09-28: `is_scratch_cite` lives in
            # `terminology_registry`, NOT in `terminology_cite` — I assumed the
            # module by the name and every write then died with an
            # `AttributeError` that the `except ImportError` did NOT catch (a
            # wrong ATTRIBUTE is not a missing MODULE). The helper is imported
            # from its real home, and the guard now catches `Exception` so a
            # future rename cannot turn a refusal into a crash.
            import terminology_registry as tr
            if tr.is_scratch_cite(str(cite_ref)):
                existing = conn.execute(
                    "SELECT 1 FROM build_step_registry WHERE task_id=? AND "
                    "layer_key=? AND step_no=?", (str(task_id), str(layer_key),
                                                  sno)).fetchone()
                if not existing:
                    return {"ok": False, "code": "SCRATCH_CITE_REF",
                            "message": "cite_ref %r is a scratch file; a scratch "
                                       "file is deleted after its run, so it is "
                                       "not evidence for a NEW step" % cite_ref}
        except Exception as exc:
            return {"ok": False, "code": "CITE_CHECK_FAILED",
                    "message": "the citation check could not run: %s: %s"
                               % (type(exc).__name__, exc)}

        # ---- R4: the NAME must be a registered term -----------------------
        t = conn.execute("SELECT term_key FROM terminology_registry WHERE "
                         "term_key=? AND is_active=1", (str(name),)).fetchone()
        if not t:
            return {"ok": False, "code": "UNREGISTERED_NAME",
                    "message": "name %r is not an active term. Register it "
                               "FIRST (`terminology_registry.add_term`), which "
                               "runs the naming law — a step cannot name "
                               "something the language does not have." % name}

        # ---- the kind must be a ROW, not a literal ------------------------
        kk = str(step_kind or "").strip().lower()
        if kk not in step_kinds(conn):
            return {"ok": False, "code": "BAD_STEP_KIND",
                    "message": "step_kind %r is not in build_step_kind_registry "
                               "(active: %s). A kind is a ROW, not a release."
                               % (step_kind, ", ".join(step_kinds(conn)))}

        # ---- R5: a link must RESOLVE --------------------------------------
        wid = None
        if workflow_id not in (None, "", NA):
            try:
                wid = int(workflow_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": "BAD_WORKFLOW_ID",
                        "message": "workflow_id must be an int or None"}
            if not conn.execute("SELECT 1 FROM workflow_registry WHERE "
                                "workflow_id=?", (wid,)).fetchone():
                return {"ok": False, "code": "UNKNOWN_WORKFLOW",
                        "message": "no workflow_registry row for id=%d" % wid}
        pid = None
        if playwright_id not in (None, "", NA):
            try:
                pid = int(playwright_id)
            except (TypeError, ValueError):
                return {"ok": False, "code": "BAD_PLAYWRIGHT_ID",
                        "message": "playwright_id must be an int or None"}
            if not conn.execute("SELECT 1 FROM playwright_environment WHERE "
                                "id=?", (pid,)).fetchone():
                return {"ok": False, "code": "UNKNOWN_PLAYWRIGHT",
                        "message": "no playwright_environment row for id=%d"
                                   % pid}

        conn.execute(
            "INSERT INTO build_step_registry "
            "(task_id, layer_key, step_no, step_kind, name, capability, module, "
            " route, purpose, workflow_id, playwright_id, owner, status, "
            " factor_key, source, evidence_ref, cite_ref, is_active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(task_id, layer_key, step_no) DO UPDATE SET "
            "step_kind=excluded.step_kind, name=excluded.name, "
            "capability=excluded.capability, module=excluded.module, "
            "route=excluded.route, purpose=excluded.purpose, "
            "workflow_id=excluded.workflow_id, "
            "playwright_id=excluded.playwright_id, owner=excluded.owner, "
            "status=excluded.status, factor_key=excluded.factor_key, "
            "source=excluded.source, evidence_ref=excluded.evidence_ref, "
            "cite_ref=excluded.cite_ref, is_active=excluded.is_active, "
            "updated_at=CURRENT_TIMESTAMP",
            (str(task_id), str(layer_key), sno, kk, str(name),
             str(capability), str(module or NA), str(route or NA),
             str(purpose), wid, pid, str(owner), st, str(factor_key),
             str(source or "local"), str(evidence_ref or NA), str(cite_ref),
             1 if is_active else 0))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM build_step_registry WHERE task_id=? AND "
            "layer_key=? AND step_no=?",
            (str(task_id), str(layer_key), sno)).fetchone()
        return {"ok": True, "id": int(row["id"]), "task_id": str(task_id),
                "layer_key": str(layer_key), "step_no": sno,
                "step_kind": kk, "factor_key": str(factor_key)}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def list_steps(conn: sqlite3.Connection, *, task_id: str | None = None,
               layer_key: str | None = None,
               limit: int = 500) -> list[dict[str, Any]]:
    """The 17-column view, filtered. Reads the VIEW, so the reader and the
    register cannot disagree about the columns."""
    ensure_schema(conn)
    sql = "SELECT * FROM build_step_view"
    where, args = [], []
    if task_id:
        where.append("id IN (SELECT id FROM build_step_registry WHERE task_id=?)")
        args.append(str(task_id))
    if layer_key:
        where.append("layer_key=?")
        args.append(str(layer_key))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY layer_key, step_no LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in conn.execute(sql, tuple(args))]


def steps_for_task(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    return list_steps(conn, task_id=task_id)


def guide_state(conn: sqlite3.Connection, step_id: int) -> dict[str, Any]:
    """Is the step's PLAYWRIGHT GUIDE present? `UNMEASURED` when it is not.

    THE LAW (2026-09-25, `playwright_step_registry`): **THE GUIDE IS THE
    PROGRAM.** `_run_playwright_test()` READS `playwright_step` and executes it,
    so a step that names a playwright environment with NO guide rows promises
    something the code cannot do.

    MEASURED, and this is why the state is not a boolean: a step with no
    `playwright_id` is not "missing a guide" — it is a step that does not use
    playwright at all, which is a legitimate state. So there are THREE outcomes,
    and collapsing them would make "no guide" and "no playwright" look alike:

        NO_PLAYWRIGHT  the step names no environment (nothing to check)
        UNMEASURED     it names one, and that environment has NO guide rows
        MEASURED       it names one, and the guide has N steps

    `UNMEASURED` is NOT a pass. An absent measurement is not a passing one.
    """
    ensure_schema(conn)
    row = conn.execute("SELECT playwright_id FROM build_step_registry WHERE id=?",
                       (int(step_id),)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_STEP", "state": "UNKNOWN",
                "message": "no build_step_registry row for id=%d" % int(step_id)}
    pid = row["playwright_id"]
    if pid in (None, "", NA):
        return {"ok": True, "state": "NO_PLAYWRIGHT", "playwright_id": None,
                "guide_steps": 0,
                "message": "the step names no playwright environment"}
    try:
        n = conn.execute("SELECT COUNT(*) FROM playwright_step WHERE "
                         "playwright_id=? AND is_active=1", (int(pid),)).fetchone()[0]
    except sqlite3.OperationalError:
        n = 0
    return {"ok": True, "playwright_id": int(pid), "guide_steps": int(n),
            "state": "MEASURED" if n else "UNMEASURED",
            "message": ("the guide has %d step(s)" % n) if n else
                       ("playwright_environment id=%d has NO guide rows, so the "
                        "step promises what the code cannot do" % int(pid))}


def coverage(conn: sqlite3.Connection, *, task_id: str | None = None) -> dict[str, Any]:
    """How much of the standard is actually filled in — a RATIO, never a count.

    A COUNT would go stale the moment a step is added (the law that broke an
    earlier proof 8 times). The ratio is a PROPERTY: it stays meaningful as the
    register grows.

    A cell counts as FILLED when it is non-empty AND not the `NA` sentinel. `NA`
    is a real value meaning "not applicable", so it must not be counted as
    missing — but it must not be counted as filled either, or a step could pass
    by declaring everything inapplicable. It is reported SEPARATELY.
    """
    ensure_schema(conn)
    sql = ("SELECT name, capability, module, route, purpose, owner, status, "
           "factor_key, source, evidence_ref, cite_ref, workflow_id, "
           "playwright_id FROM build_step_registry WHERE is_active=1")
    args: tuple = ()
    if task_id:
        sql += " AND task_id=?"
        args = (str(task_id),)
    rows = [dict(r) for r in conn.execute(sql, args)]
    cells = ("name", "capability", "module", "route", "purpose", "owner",
             "status", "factor_key", "source", "evidence_ref", "cite_ref",
             "workflow_id", "playwright_id")
    filled = na = empty = 0
    for r in rows:
        for c in cells:
            v = r.get(c)
            if v is None or str(v).strip() == "":
                empty += 1
            elif str(v).strip() == NA:
                na += 1
            else:
                filled += 1
    total = filled + na + empty
    return {"steps": len(rows), "cells": total, "filled": filled,
            "na": na, "empty": empty,
            "filled_pct": round(100.0 * filled / total, 2) if total else 0.0,
            "na_pct": round(100.0 * na / total, 2) if total else 0.0,
            "empty_pct": round(100.0 * empty / total, 2) if total else 0.0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--init", action="store_true", help="create the schema")
    ap.add_argument("--kinds", action="store_true", help="list the step kinds")
    ap.add_argument("--list", action="store_true", help="list the steps")
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--task", default="")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.init:
            print(ensure_schema(conn))
        if a.kinds:
            for k in step_kinds(conn):
                print("  ", k)
        if a.list:
            for r in list_steps(conn, task_id=a.task or None):
                print("  %-4s %-10s %-28s %s"
                      % (r["step_no"], r["kind"], r["name"][:28], r["cite"]))
        if a.coverage:
            print(coverage(conn, task_id=a.task or None))
        if not any((a.init, a.kinds, a.list, a.coverage)):
            print("nothing to do; try --init --kinds --list --coverage")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
