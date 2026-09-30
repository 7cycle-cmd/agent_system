# -*- coding: utf-8 -*-
"""entity_graph_report.py — is the graph FULL? Measure it, do not claim it.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "T-1-5-1, how to have this entity id, and it is a map for each small task
     with file location and parent relationship, so fully have that = full graph
     for system"

So the user's model of the system IS a graph, and an entity id is a NODE in it:

    NODE   an entity id `{LETTER}-{table_id}-{row_id}-{version}` — a THING.
           (the 3-part form `{LETTER}-{ref_id}-{version}` is the OLD one,
           2026-09-27: one trailing number reads as version OR row.)
    EDGE   parent  : `version_registry.parent_version_id` (version lineage)
                     and `task_entity_link` (task <-> entity).
    EDGE   where   : `code_location_registry` (entity_type, entity_ref_id,
                     version) -> file_path + line_start.

"fully have that" is therefore CHECKABLE, and this module checks it: a node is
COMPLETE when it has (a) an id, (b) a file location, (c) a parent or task edge.
Any node missing one is a GAP, and the gap is NAMED. A report that cannot name
what is missing is a claim, not a measurement.

WHAT IT IS NOT
--------------
It is NOT a gate. It writes nothing and refuses nothing. The rule is measured
first (the plan's Decision); it can only block anything once the numbers exist.

READ-ONLY. Every statement is a SELECT.

Run:
    .\\.venv\\Scripts\\python.exe entity_graph_report.py
    .\\.venv\\Scripts\\python.exe entity_graph_report.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _cols_of(conn: sqlite3.Connection, table: str) -> set[str]:
    """The table's column names. Used to check a FK's column really exists."""
    try:
        return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}
    except sqlite3.Error:
        return set()


def _pk_of(conn: sqlite3.Connection, table: str) -> str | None:
    """The table's primary key column, READ FROM THE DB.

    `PRAGMA table_info` marks the pk in its `pk` field, so this is the schema's
    OWN declaration — not a name guess. A name guess (`id`, or `<table>_id`)
    MEASURED WRONG: `skill_registry`'s pk is `skill_id`, so guessing `id` made the
    FK form never fire. A FK target may also be a table with NO entity kind (e.g.
    `code_registry.task_id -> dev_task`), so the declared pk cannot only be looked
    up in `entity_type_registry`.
    """
    try:
        rows = [c for c in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return None
    pks = [c[1] for c in rows if len(c) > 5 and int(c[5] or 0) > 0]
    if len(pks) == 1:
        return str(pks[0])
    # A composite pk cannot be joined on one column; `rowid` aliases the single
    # INTEGER PRIMARY KEY, and its absence is reported rather than guessed.
    return None


# ---------------------------------------------------------------------------
# EDGE_FORMS_BY_KIND — which edge FORMS are VALID for which entity KIND.
#
# WHY THIS EXISTS (first principles, user 2026-09-23)
# ---------------------------------------------------
#     "why i think you are not work at first principle? entity for what?"
#
# The DERIVED answer: an entity exists so a thing can be (1) NAMED, (2)
# VERSIONED, (3) LOCATED, (4) CONNECTED. The 4th part — the EDGE — is what this
# table constrains. The first version of this module accepted ANY of five
# OR-ed forms for EVERY kind:
#
#     has_edge = has_parent or has_task or has_tax or has_fk or has_tp or is_root
#
# That is not a rule. It says "any edge will do", so a CONTAINMENT edge and a
# REFERENCE edge are indistinguishable — which is exactly how "make skill a
# scope level" looked plausible. An edge is only evidence of CONNECTED when the
# FORM is one the KIND is allowed to use.
#
# THE TWO MEANINGS OF AN EDGE (registered as `terminology_registry:'entity_edge'`)
#   CONTAINMENT  "I am PART OF a wider level" — for a thing that IS a level in
#                the program-structure hierarchy (a module is part of a channel;
#                a function is part of a capability).
#   REFERENCE    "I am ABOUT an ontology entity" — for a PROCEDURE, which is not
#                a level but operates on one (a skill governs/applies to a
#                module or capability).
#
# A `taxonomy_path` edge is CONTAINMENT only when the thing itself IS a level.
# For a skill the SAME column means REFERENCE — hence the form is allowed for
# both kinds but the SEMANTIC differs, and the report records which one applied.
#
# The value is a tuple of ACCEPTED form names; a form outside the tuple makes the
# node's edge INVALID (reported, not silently accepted). `reason` explains why the
# set is what it is, so a reader can challenge the rule rather than guess it.
EDGE_FORMS_BY_KIND: dict[str, dict[str, Any]] = {
    # ---- CONTAINER kinds: a level in the program-structure hierarchy --------
    "channel": {
        "forms": ("root", "parent", "task"),
        "reason": "the WIDEST level: it has no wider parent BY DEFINITION "
                  "(root), so only a version parent or a task edge can apply.",
    },
    "module": {
        "forms": ("fk", "taxonomy", "parent", "task"),
        "reason": "a module is PART OF a channel (fk channel_id).",
    },
    "capability": {
        "forms": ("fk", "taxonomy", "taxonomy_path", "parent", "task"),
        "reason": "a capability is PART OF a module (fk module_id).",
    },
    "api": {
        "forms": ("fk", "taxonomy", "taxonomy_path", "parent", "task"),
        "reason": "an api is PART OF a capability (fk).",
    },
    "function": {
        "forms": ("fk", "taxonomy", "taxonomy_path", "parent", "task"),
        "reason": "a function is PART OF a capability (fk capability_id).",
    },
    "db_table": {
        "forms": ("fk", "taxonomy", "taxonomy_path", "parent", "task"),
        "reason": "a table is PART OF a module (fk).",
    },
    "db_field": {
        "forms": ("fk", "taxonomy", "taxonomy_path", "parent", "task"),
        "reason": "a field is PART OF a table (fk).",
    },
    # ---- PROCEDURE kinds: NOT a level; the edge is a REFERENCE ---------------
    "skill": {
        "forms": ("taxonomy_path", "task", "parent"),
        "reason": "a skill is a PROCEDURE, not a level: its edge is 'I am ABOUT "
                  "an ontology entity' (taxonomy_path = the entity it governs), "
                  "NOT containment. A containment edge would claim 'a skill "
                  "CONTAINS a function', which is false. A `taxonomy` edge (the "
                  "next wider scope level) is therefore NOT valid here.",
    },
    "component": {
        "forms": ("fk", "parent", "task"),
        "reason": "a component links to a skill BY ID (fk skill_ref) — same-layer "
                  "reference, so a taxonomy level is not its parent.",
    },
    "factor": {
        "forms": ("fk", "parent", "task"),
        "reason": "a factor applies to a skill BY ID (fk skill_ref) — reference, "
                  "not containment in a scope level.",
    },
    "namespace": {
        "forms": ("fk", "parent", "task"),
        "reason": "a namespace is PART OF a module (fk module_id).",
    },
    "job": {
        "forms": ("taxonomy", "parent", "task", "fk"),
        "reason": "a job runs in some scope; not a scope level itself.",
    },
    "event": {
        "forms": ("taxonomy", "parent", "task", "fk"),
        "reason": "an event is emitted by some scope; not a scope level itself.",
    },
    "code": {
        "forms": ("fk", "parent", "task"),
        "reason": "a code row belongs to a file/module; located by file, so its "
                  "edge is the owning register.",
    },
    "prompt": {
        "forms": ("fk", "parent", "task"),
        "reason": "a prompt belongs to a component/skill by id.",
    },
    "study": {
        "forms": ("fk", "parent", "task"),
        "reason": "a study belongs to the thing it studies by id.",
    },
    "wording": {
        "forms": ("fk", "parent", "task"),
        "reason": "a wording belongs to a component by id (skill_id).",
    },
    "workflow": {
        "forms": ("fk", "taxonomy", "parent", "task"),
        "reason": "a workflow orchestrates scopes; not a scope level itself.",
    },
    "test_case": {
        "forms": ("fk", "parent", "task"),
        "reason": "a test case belongs to a contract/subject by id.",
    },
    "db_row": {
        "forms": ("fk", "parent", "task"),
        "reason": "a row belongs to its table by id.",
    },
}

# A kind with NO declared rule is NOT silently accepted: the report marks its
# edge form UNKNOWN, which is a REAL outcome (an instrument that cannot classify
# must say so, never pass). This keeps a newly-added kind visible instead of
# quietly inheriting "any edge will do".
def edge_forms_for(kind: str) -> tuple[tuple[str, ...], str] | tuple[None, str]:
    """(accepted_forms, reason) for a kind, or (None, why-unknown)."""
    r = EDGE_FORMS_BY_KIND.get(str(kind or ""))
    if r is None:
        return None, ("kind %r has NO declared edge rule, so its edge form "
                      "cannot be judged — add it to EDGE_FORMS_BY_KIND"
                      % kind)
    return tuple(r["forms"]), str(r["reason"])


def node_completeness(conn: sqlite3.Connection) -> dict[str, Any]:
    """For every ACTIVE entity version, which of id / location / edge is MISSING?

    A node is one `(entity_type, entity_ref_id, version)` in `version_registry`
    (the version IS the node identity; a version row is what an id's 4th part
    names). It is COMPLETE when:
      * id       — the letter resolves to a real register row (`entity_id.verify`)
      * location — BY KIND (measured 2026-09-23): a CODE entity is located by
                   `code_location_registry` OR by its own `code_registry` row; a
                   NON-code entity is located by its REGISTER ROW (a capability
                   is not a file, so demanding a file was the wrong instrument)
      * edge     — a parent version, a `task_entity_link`, OR the node's
                   TAXONOMY PARENT (the next wider level in
                   `hardcode_scope.SCOPE_ORDER`, e.g. a capability's module)

    WHY THE LOCATION/EDGE TEST IS PER KIND: the first version accepted ONLY the
    file form, so every non-code node was reported "missing location" and all 501
    nodes "missing edge" while the registries actually located them. That is the
    same defect family as the proof report -- an output measured by the WRONG
    INSTRUMENT. A gap the instrument INVENTS is not a gap.
    """
    out: dict[str, Any] = {"nodes": [], "summary": {}}
    if not _table_exists(conn, "version_registry"):
        out["summary"] = {"error": "version_registry absent"}
        return out

    import entity_registry as er
    try:
        import hardcode_scope as hs
        scope_order = tuple(hs.SCOPE_ORDER)      # narrowest -> widest
    except Exception:
        scope_order = ()

    # The version's OWN id must be reachable: read the letter -> register map.
    letters = {e["type_letter"]: e for e in er.list_entity_types(conn)}
    # `table -> pk column`, from `entity_type_registry`. A FK target's pk must be
    # READ, not guessed: `skill_registry`'s pk is `skill_id`, and a guessed `id`
    # made the FK form silently never fire (measured).
    pk_by_table = {str(e["register_table"]): str(e["pk_column"])
                   for e in er.list_entity_types(conn)}
    # The TAXONOMY PARENT of each scope level: the next WIDER level.
    parent_of = {lvl: (scope_order[i + 1] if i + 1 < len(scope_order) else None)
                 for i, lvl in enumerate(scope_order)}
    rows = [dict(r) for r in conn.execute(
        "SELECT version_registry_id, entity_type, entity_ref_id, version, "
        "       parent_version_id "
        "  FROM version_registry WHERE is_active = 1 "
        " ORDER BY entity_type, entity_ref_id, version")]
    for r in rows:
        letter = r["entity_type"]
        et = letters.get(letter)
        has_id = False
        if et and _table_exists(conn, et["register_table"]):
            has_id = conn.execute(
                "SELECT 1 FROM %s WHERE %s = ?" % (et["register_table"],
                                                   et["pk_column"]),
                (int(r["entity_ref_id"]),)).fetchone() is not None
        # ---- LOCATION, BY KIND --------------------------------------------
        # form 1: the file mapping (code entities)
        loc = conn.execute(
            "SELECT file_path, line_start FROM code_location_registry "
            "WHERE entity_type = ? AND entity_ref_id = ? AND version = ? "
            "LIMIT 1",
            (letter, int(r["entity_ref_id"]), int(r["version"]))
        ).fetchone() if _table_exists(conn, "code_location_registry") else None
        if loc is not None:
            loc_form, loc_value = "file", loc["file_path"]
        elif has_id:
            # form 2: the REGISTER ROW itself is the node's place. Every letter
            # resolves to `register_table.pk_column`, so `has_id` means the row
            # EXISTS and locates the node.
            loc_form = "register_row"
            loc_value = "%s.%s=%d" % (et["register_table"], et["pk_column"],
                                      int(r["entity_ref_id"])) if et else None
        else:
            loc_form, loc_value = None, None
        has_loc = loc_form is not None
        # ---- EDGE, BY KIND -------------------------------------------------
        has_parent = r["parent_version_id"] is not None
        has_task = conn.execute(
            "SELECT 1 FROM task_entity_link "
            "WHERE entity_type = ? AND entity_ref_id = ? LIMIT 1",
            (letter, int(r["entity_ref_id"]))).fetchone() is not None \
            if _table_exists(conn, "task_entity_link") else False
        # form 3: the TAXONOMY PARENT — a capability's edge is its module.
        tax_parent = parent_of.get(et["entity_kind"]) if et else None
        has_tax = bool(tax_parent)
        # form 4: a REGISTER-DECLARED parent — a FK in the node's OWN register
        # row that points at ANOTHER registered table (measured: a namespace's
        # `module_id` -> module_registry, a function's `capability_id` ->
        # capability_registry). This is the REAL parent relationship the user
        # described, and it is DECLARED, not guessed.
        #
        # THE PER-ROW CHECK (fixed 2026-09-24). MEASURED DEFECT: this form tested
        # only `PRAGMA foreign_key_list(<table>)` — a TABLE-level fact — so the
        # moment ANY FK existed on the table, EVERY node of that kind reported an
        # edge, even a node whose FK column is NULL. Measured: `factor` and
        # `component` jumped to 38/38 and 8/8 after a FK was added while only 5 of
        # 39 factor rows and 1 of 8 component rows actually carry a parent. That is
        # a FALSE GREEN — an empty relation that looks complete.
        #
        # A FK that is DECLARED but whose VALUE IS NULL is not an edge: the node is
        # not connected to anything. So the check now reads the row's OWN column
        # value, and that value must RESOLVE in the target table.
        fk_parent = None
        if et and has_id and _table_exists(conn, et["register_table"]):
            try:
                for fk in conn.execute(
                        "PRAGMA foreign_key_list(%s)" % et["register_table"]):
                    tgt = str(fk[2])
                    if tgt == et["register_table"]:
                        continue
                    col = str(fk[3])
                    if not _table_exists(conn, tgt):
                        continue
                    tcols = {c[1] for c in conn.execute(
                        "PRAGMA table_info(%s)" % tgt)}
                    if col not in _cols_of(conn, et["register_table"]):
                        continue
                    val = conn.execute(
                        "SELECT %s FROM %s WHERE %s = ?"
                        % (col, et["register_table"], et["pk_column"]),
                        (int(r["entity_ref_id"]),)).fetchone()
                    ref = val[0] if val else None
                    if ref is None:
                        # DECLARED but EMPTY: no parent recorded for THIS row.
                        continue
                    # THE TARGET'S PK comes from `entity_type_registry.pk_column`
                    # for the REGISTERED table. Guessing a name (`id`, or
                    # `<table>_id`) MEASURED WRONG: `skill_registry`'s pk is
                    # `skill_id`, so a name guess never resolved and the FK form
                    # stopped firing altogether. A lookup of the DECLARED pk is the
                    # only reliable source.
                    tpk = pk_by_table.get(tgt) or _pk_of(conn, tgt)
                    if not tpk or tpk not in tcols:
                        continue
                    if conn.execute("SELECT 1 FROM %s WHERE %s = ?"
                                    % (tgt, tpk), (int(ref),)).fetchone() is None:
                        # A non-resolving value must NOT read as an edge.
                        continue
                    fk_parent = "%s.%s=%s -> %s" % (et["register_table"], col,
                                                    int(ref), tgt)
                    break
            except Exception:
                fk_parent = None
        has_fk = fk_parent is not None
        # form 5: a DECLARED `taxonomy_path` on the row (measured: a skill
        # carries `taxonomy_path` such as `module/task_center`, so its parent is
        # the level above — the module). Read from the row, never guessed.
        tax_path_parent = None
        if et and has_id and _table_exists(conn, et["register_table"]):
            cols = {c[1] for c in conn.execute(
                "PRAGMA table_info(%s)" % et["register_table"])}
            if "taxonomy_path" in cols:
                tp = conn.execute(
                    "SELECT taxonomy_path FROM %s WHERE %s = ?"
                    % (et["register_table"], et["pk_column"]),
                    (int(r["entity_ref_id"]),)).fetchone()
                val = str(tp[0] or "").strip() if tp else ""
                if val and "/" in val:
                    tax_path_parent = val.rsplit("/", 1)[0]
        has_tp = tax_path_parent is not None
        # form 6: a ROOT. The WIDEST scope level (the last in SCOPE_ORDER) has no
        # parent BY DEFINITION, so a missing edge there is CORRECT, not a gap.
        # Without this, every channel node is reported incomplete forever.
        is_root = bool(scope_order) and et is not None \
            and et["entity_kind"] == scope_order[-1]
        has_edge = has_parent or has_task or has_tax or has_fk or has_tp or is_root
        edge_form = ("parent" if has_parent else
                     ("task" if has_task else
                      ("taxonomy:%s" % tax_parent if has_tax else
                       ("fk:%s" % fk_parent if has_fk else
                        ("taxonomy_path:%s" % tax_path_parent if has_tp else
                         ("root" if is_root else None))))))
        # ---- IS THAT EDGE FORM VALID FOR THIS KIND? (first principles) --------
        # An edge is evidence of CONNECTED only when the FORM is one the KIND is
        # allowed to use. `edge_form` above is the raw form (e.g. "fk:..." or
        # "taxonomy:..."); the FORM NAME is its prefix. A skill using a
        # CONTAINMENT form (`taxonomy`) is INVALID: a skill is a PROCEDURE, so
        # its edge must be a REFERENCE (`taxonomy_path`), never containment.
        _form_name = (edge_form.split(":", 1)[0] if edge_form else None)
        accepted, rule_reason = edge_forms_for(et["entity_kind"] if et else "")
        if accepted is None:
            edge_form_valid: bool | None = None       # UNKNOWN is a real outcome
            edge_invalid_reason = rule_reason
        elif _form_name is None:
            # NO EDGE at all. This is NOT "an invalid form" — there is no form to
            # judge. Keeping it None stops the report claiming 45 skills have a
            # WRONG edge when they have NO edge. The two are different defects
            # and the summary counts them separately.
            edge_form_valid = None
            edge_invalid_reason = ("no edge exists, so there is no form to "
                                   "judge (this is a MISSING edge, not an "
                                   "invalid one)")
        else:
            edge_form_valid = _form_name in accepted
            edge_invalid_reason = (
                None if edge_form_valid else
                ("form %r is NOT valid for kind %r (accepted: %s). %s"
                 % (_form_name, et["entity_kind"], ", ".join(accepted),
                    rule_reason)))
        # A CONTAINMENT form on a PROCEDURE kind is the specific confusion the
        # `entity_edge` term warns about. Report it as INVALID even though the
        # raw form resolved, because the SEMANTIC is wrong.
        if (et is not None and et["entity_kind"] == "skill"
                and _form_name == "taxonomy"):
            edge_form_valid = False
            edge_invalid_reason = (
                "a skill's edge resolved to the CONTAINMENT form 'taxonomy' "
                "(the next wider scope level). A skill is a PROCEDURE, so its "
                "edge must be a REFERENCE ('taxonomy_path' = the entity it is "
                "ABOUT). Containment would claim 'a skill CONTAINS a function'.")
        # `edge_form_valid` is None in TWO different cases (no edge / unknown
        # kind). They must not be merged: a report that says "45 UNKNOWN" when
        # the truth is "45 have no edge" is the wrong-instrument defect again.
        # `edge_judged` says WHETHER a form was judged at all.
        edge_judged = _form_name is not None and accepted is not None

        missing = [name for name, ok in
                   (("id", has_id), ("location", has_loc), ("edge", has_edge))
                   if not ok]
        # WHY an edge is missing, when the register DECLARES none. A gap with a
        # named reason is actionable; an unexplained 55 is not.
        gap_reason = None
        if "edge" in missing and et is not None:
            cols = {c[1] for c in conn.execute(
                "PRAGMA table_info(%s)" % et["register_table"])}
            fks = list(conn.execute(
                "PRAGMA foreign_key_list(%s)" % et["register_table"]))
            if not fks and "taxonomy_path" not in cols:
                gap_reason = ("%s declares NO parent: no FK and no "
                              "taxonomy_path column" % et["register_table"])
            elif "taxonomy_path" in cols:
                gap_reason = ("%s.taxonomy_path is EMPTY, so no parent can be "
                              "derived" % et["register_table"])
        out["nodes"].append({
            "node": "%s-%d-%d" % (letter, int(r["entity_ref_id"]),
                                  int(r["version"])),
            "letter": letter,
            "kind": (et["entity_kind"] if et else "?"),
            "has_id": has_id,
            "location": loc_value,
            "location_form": loc_form,
            "has_edge": has_edge,
            "edge_form": edge_form,
            "edge_form_valid": edge_form_valid,
            "edge_judged": edge_judged,
            "edge_rule": rule_reason,
            "edge_invalid_reason": edge_invalid_reason,
            "missing": missing,
            "gap_reason": gap_reason,
            "complete": not missing,
        })

    total = len(out["nodes"])
    complete = sum(1 for n in out["nodes"] if n["complete"])
    # ---- PER-KIND SUMMARY (the user's "fully have that" is a number PER KIND) --
    by_kind: dict[str, dict[str, int]] = {}
    for n in out["nodes"]:
        k = by_kind.setdefault(n["kind"], {"nodes": 0, "complete": 0,
                                           "missing_location": 0,
                                           "missing_edge": 0})
        k["nodes"] += 1
        if n["complete"]:
            k["complete"] += 1
        if "location" in n["missing"]:
            k["missing_location"] += 1
        if "edge" in n["missing"]:
            k["missing_edge"] += 1
    out["summary"] = {
        "nodes": total,
        "complete": complete,
        "incomplete": total - complete,
        "missing_id": sum(1 for n in out["nodes"] if "id" in n["missing"]),
        "missing_location": sum(1 for n in out["nodes"]
                                if "location" in n["missing"]),
        "missing_edge": sum(1 for n in out["nodes"] if "edge" in n["missing"]),
        # ---- EDGE-FORM VALIDITY (the per-kind rule, reported SEPARATELY) ------
        # Deliberately NOT folded into `complete`: the rule is new, so letting it
        # redefine the count would look like it found or hid a gap. It is
        # REPORTED beside the count, so both numbers are visible and a human can
        # see the difference between "no edge" and "an edge of the wrong kind".
        "edge_form_invalid": sum(1 for n in out["nodes"]
                                 if n.get("edge_form_valid") is False),
        # UNKNOWN means a form EXISTED but its kind has no declared rule. A node
        # with NO edge is counted under `missing_edge` instead, never here.
        "edge_form_unknown": sum(1 for n in out["nodes"]
                                 if n.get("edge_form_valid") is None
                                 and n.get("edge_judged") is False
                                 and n.get("has_edge")),
        "by_kind": by_kind,
    }
    return out


def format_report(rep: dict[str, Any], limit: int = 20) -> str:
    s = rep["summary"]
    if "error" in s:
        return "entity_graph_report: %s" % s["error"]
    lines = [
        "=" * 78,
        "THE ENTITY GRAPH — is it FULL? (id + location BY KIND + edge)",
        "=" * 78,
        "  nodes            : %d" % s["nodes"],
        "  complete         : %d" % s["complete"],
        "  incomplete       : %d" % s["incomplete"],
        "  missing id       : %d" % s["missing_id"],
        "  missing location : %d" % s["missing_location"],
        "  missing edge     : %d" % s["missing_edge"],
        "  edge form INVALID: %d  (an edge whose FORM the kind may not use)"
        % s.get("edge_form_invalid", 0),
        "  edge form UNKNOWN: %d  (kind has no declared edge rule)"
        % s.get("edge_form_unknown", 0),
        "",
        "  BY KIND (complete / nodes, missing location, missing edge):",
    ]
    for k, v in sorted(s.get("by_kind", {}).items(),
                       key=lambda kv: -kv[1]["nodes"]):
        lines.append("    %-14s %5d / %-5d  loc=%d  edge=%d"
                     % (k, v["complete"], v["nodes"], v["missing_location"],
                        v["missing_edge"]))
    lines.append("")
    gaps = [n for n in rep["nodes"] if not n["complete"]]
    if not gaps:
        lines.append("  NO GAPS: every node has an id, a location, and an edge.")
    else:
        lines.append("  THE GAPS (first %d of %d), each NAMED:"
                     % (min(limit, len(gaps)), len(gaps)))
        for n in gaps[:limit]:
            lines.append("    %-18s missing=%s  loc=%s"
                         % (n["node"], ",".join(n["missing"]),
                            n["location"] or "-"))
            if n.get("gap_reason"):
                lines.append("        WHY: %s" % n["gap_reason"])
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        rep = node_completeness(conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    else:
        print(format_report(rep, limit=args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
