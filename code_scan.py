#!/usr/bin/env python
"""code_scan.py — the WHOLE-SITE coding-quality scanner (a DISPATCHER).

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
It is NOT a fourth reader. MEASURED 2026-09-28, the site already HAS three
readers, each with a proven reader/population discipline:

    hardcode_scan  a literal that a registry could have supplied   (hardcode)
    code_shape     a parameter REBOUND in its own body             (SHAPE)
    ruff_reader    the DEFECT classes F401/F841/I001/UP009/F821    (BUG/CLEANUP)

Writing a fourth reader that re-implements any of them would be a SECOND truth
about "what counts" — the drift `one_parser_only` names. So this module does one
thing: it runs the EXISTING three over the whole site and normalises their
findings into ONE shape, then gives each a FINGERPRINT so it can be put on a
waiting list without being re-reported every day.

WHY THE CLASS SPLIT MATTERS (the human: "can it help to found out BUG")
----------------------------------------------------------------------
A raw count is not a to-do list. MEASURED over the whole site: 1526 defect-class
ruff findings, of which **53 are F821 (an UNDEFINED NAME)** and 5 are F811 (a name
that SHADOWS a real function). Those two are latent CRASHES:

    MEASURED: logic_generator.py annotated `sqlite3.Connection` in 18 signatures
    while never importing sqlite3, so `typing.get_type_hints()` raised NameError.

The other 1473-odd are CLEANUP (unused import/variable, unsorted block, BOM) —
worth fixing, but nothing crashes. A list that mixes them lets the 53 crashes
hide under the 1473 nits, so the severity is DERIVED FROM THE CLASS, not assigned
by eye.

THE FINGERPRINT (why it does not re-report every day)
-----------------------------------------------------
The key the human asked for — "not finding and checking everyday" — is that a
finding somebody has ALREADY been given must not be handed over again. Two
design points, both MEASURED-justified:

  * the fingerprint is `sha1(rule|path|symbol)`, where `symbol` is the enclosing
    function/name when the reader provides one and otherwise the rule + path.
    It is deliberately NOT the raw line number: MEASURED, an unrelated edit above
    a finding shifts every line below it, so a line-based key would resurrect the
    whole list on any edit.
  * `open_fingerprints()` reads what is ALREADY waiting, so the scan reports only
    NEW findings.

READ-ONLY BY DEFAULT. `scan()` never writes the DB. `enqueue_new()` writes ONLY
through the existing queue API, and only when `apply=True`.
"""
from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# SEVERITY IS DERIVED FROM THE CLASS. `BUG` = a latent crash or a shadowed
# definition. `CLEANUP` = unused/unsorted/BOM. `SHAPE` = a rebound parameter,
# which is worse than a literal because it makes the output true by construction.
#
# THE BUG RULES COME FROM THE READER, NOT FROM A COPY HERE. MEASURED 2026-09-28:
# my first version listed `F811` in `BUG_RULES` while `ruff_reader.DEFECT_CLASSES`
# did NOT, so ruff was never asked for F811 and this module would have reported
# F811 = 0 forever — a zero that meant "never measured", not "none found". That is
# the `one_parser_only` defect, so the list is read from the reader and the extra
# names are ADDED to it rather than restated.
import ruff_reader as _rr  # noqa: E402

BUG_RULES: tuple[str, ...] = _rr.DEFECT_CLASSES
CLEANUP_RULES: tuple[str, ...] = ()
SHAPE_RULES: tuple[str, ...] = ("param_rebound",)


def severity_for(rule: str) -> str:
    """The severity a rule carries. A rule nobody classified is NOT guessed.

    The DEFECT classes are split by KIND, and both kinds are DEFECTS the site
    wants fixed; the split exists because a list that mixes them lets a crash hide
    under a nit.

      BUG-class rules   (undefined / redefined / shadowed NAME) = a latent crash
      CLEANUP-class     (unused import/variable, unsorted block, BOM) = safe fix
    """
    if rule in BUG_RULES:
        # F821/F811/F402/F823 are name-resolution failures: the module can fail
        # at import or at a type check. The unused/BOM rules are not.
        if rule in ("F821", "F811", "F402", "F823"):
            return "BUG"
        return "CLEANUP"
    if rule in SHAPE_RULES:
        return "SHAPE"
    return "UNCLASSIFIED"


def owner_for(conn: sqlite3.Connection, rel_path: str) -> dict:
    """WHO OWNS this file — the answer to "will fix by who?".

    THE QUESTION THIS ANSWERS (the human, 2026-09-28): *"corn scan? will fix by
    who? who will know?"* A queue row with no owner is not a to-do item; it is a
    message nobody is accountable for. So the owner is RESOLVED, not assumed.

    MEASURED 2026-09-28 over the 11 files the pending bugs touch:

        conftest.py, db_schema.py, skill_library_api.py, terminology_alias.py,
        terminology_blacklist.py            -> code_registry has a row (OWNED)
        _proof_copy_button_trigger_point.py, _proof_playwright_step_kind.py,
        _proof_playwright_workflow_scroll.py, _proof_terminology_rubbish.py,
        _proof_vscode_session_identity.py, _try_llm_3level_audit.py
                                            -> 0 rows (UNOWNED)

    So HALF the list has no owner, and every unowned one is a `_`-prefixed session
    script. That is a real distinction, not a formatting detail: an unowned
    one-off script is not "fixed" by a worker, it is RETIRED or deleted, and only
    a human decides that.

    THE OWNER IS READ, NEVER GUESSED: `code_registry.file_path` -> `module_name`.
    A file with no row is `UNOWNED`, which is a REAL outcome — the same rule the
    gate follows, where a subject nobody bound is UNKNOWN and never a pass.
    """
    rel = str(rel_path).replace("\\", "/").lstrip("./")
    try:
        rows = conn.execute(
            "SELECT module_name, status FROM code_registry "
            "WHERE file_path LIKE ? ORDER BY id DESC LIMIT 1",
            ("%" + rel,)).fetchall()
    except Exception:
        rows = []
    if rows:
        r = rows[0]
        return {"state": "OWNED",
                "owner": str(r["module_name"] or ""),
                "cite_ref": "code_registry.file_path ~ %s" % rel}
    # `_`-prefixed = a session/proof/try script. Named separately because the
    # ACTION differs: retire it, do not staff it.
    if Path(rel).name.startswith("_"):
        return {"state": "UNOWNED_SCRIPT",
                "owner": "",
                "cite_ref": "code_registry has no row for %s" % rel}
    return {"state": "UNOWNED",
            "owner": "",
            "cite_ref": "code_registry has no row for %s" % rel}


def entity_for(conn: sqlite3.Connection, rel_path: str) -> dict:
    """THE ENTITY of this file — the object the human named: "task with task ID
    and entity_id".

    WHY (the human's correction, 2026-09-28): *"sorry, object is task with task ID
    and entity_id"*. A finding is a raw fact; the OBJECT that must travel is a
    TASK whose identity is `task_id` + `entity_id`. So the entity is RESOLVED, not
    invented, and it is read from the register that owns the vocabulary.

    MEASURED: `entity_type_registry` holds 18 types and **`R` is `code`**,
    pointing at `code_registry` (pk `id`). `test_case_registry` by contrast is
    EMPTY, so an entity type that happens to exist but has no rows is NOT a usable
    object — the type has to be both REGISTERED and OCCUPIED.

    The id form is NOT composed here. MEASURED 2026-09-28: my first version
    emitted `R-67`, and that is WRONG — `entity_id.format` requires ALL FOUR parts
    `{LETTER}-{table_id}-{row_id}-{version}`, and its own docstring says the
    3-part form is the OLD, AMBIGUOUS one (`F-38-11` reads as version 11 OR row
    11). So the id is FORMED BY THE ONE MINTER, which also REFUSES a bad id
    instead of returning a string that looks fine.
    """
    rel = str(rel_path).replace("\\", "/").lstrip("./")
    # The TYPE is read, never assumed: `R` -> code, from entity_type_registry.
    try:
        t = conn.execute(
            "SELECT type_letter, entity_kind, register_table, pk_column FROM "
            "entity_type_registry WHERE entity_kind='code' AND is_active=1"
        ).fetchone()
    except Exception:
        t = None
    letter = str(t["type_letter"]) if t is not None else ""
    # table_id = db_table_registry.db_table_id of the table the letter's register
    # IS (`rule 3` of entity_id.verify), read not guessed.
    table_id = None
    try:
        tr = conn.execute(
            "SELECT db_table_id FROM db_table_registry WHERE table_key='code_registry'"
        ).fetchone()
        table_id = int(tr["db_table_id"]) if tr is not None else None
    except Exception:
        table_id = None
    try:
        row = conn.execute(
            "SELECT id FROM code_registry WHERE file_path LIKE ? "
            "ORDER BY id DESC LIMIT 1", ("%" + rel,)).fetchone()
    except Exception:
        row = None
    if row is None or not letter or not table_id:
        # NO ROW = NO ENTITY. Reported as UNRESOLVED, never minted: an id minted
        # for a file nothing registered is an id nobody else can resolve.
        why = ("code_registry has no row for %s" % rel) if row is None else \
              ("entity_type/table_id unresolved (letter=%r table_id=%r)"
               % (letter, table_id))
        return {"state": "UNRESOLVED", "entity_type": letter,
                "entity_ref_id": None, "entity_id": "", "cite_ref": why}
    rid = int(row["id"])
    import entity_id as eid
    try:
        composed = eid.format(letter, table_id, rid, 1)
    except Exception as exc:
        return {"state": "UNRESOLVED", "entity_type": letter,
                "entity_ref_id": rid, "entity_id": "",
                "cite_ref": "entity_id.format refused: %s" % exc}
    return {"state": "RESOLVED", "entity_type": letter, "entity_ref_id": rid,
            "entity_id": composed,
            "table_id": table_id,
            "cite_ref": ("entity_id.format(%s, %d, %d, 1) from "
                         "db_table_registry + code_registry"
                         % (letter, table_id, rid))}


def fingerprint(rule: str, path: str, symbol: str = "") -> str:
    """A STABLE id for one finding that survives unrelated edits.

    NOT the line number: MEASURED, an edit above a finding shifts every line
    below it, so a line-keyed fingerprint would resurrect the whole list.

    `symbol` must DISCRIMINATE, or findings collide. MEASURED 2026-09-28: the
    first version passed `(rule, path)` alone for ruff findings, so every finding
    in one file got the same id (6 sample rows, one fingerprint) — and a NEW
    undefined name in an already-flagged file was then dropped as a duplicate.
    Callers pass the name the reader reports (the enclosing function for a
    rebound parameter, the undefined NAME for ruff), so the key is
    `rule | file | the thing that must be FIXED`.
    """
    rel = str(path).replace("\\", "/").lstrip("./")
    raw = "%s|%s|%s" % (rule, rel, symbol or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(BASE_DIR).as_posix()
    except Exception:
        return str(p).replace("\\", "/")


def scan(*, include_hardcode: bool = True, limit: int | None = None) -> dict:
    """Run the EXISTING readers over the whole site. READ-ONLY, idempotent.

    Returns `{ok, counts, findings, by_severity, by_rule, population}` where
    every finding carries a `cite_ref` (`path:line`). A finding with no cite is
    REFUSED, not downgraded (`citation-discipline`).
    """
    import entity_backfill as eb
    findings: list[dict[str, Any]] = []
    refused: list[str] = []

    # ---- reader 3: ruff DEFECT classes (whole site) ----------------------
    import ruff_reader as rr
    tree = rr.measure_tree()
    ruff_ok = bool(tree.get("ok"))
    if ruff_ok:
        # ruff reports per position; a symbol is not always available, so the
        # fingerprint falls back to rule|path and the LINE is kept for the cite.
        for x in rr._run([rr._interpreter(), "-m", "ruff", "check",
                          str(BASE_DIR), "--exclude", ".venv",
                          "--exclude", "node_modules", "--exclude",
                          "qc_evidence", "--select",
                          ",".join(rr.DEFECT_CLASSES),
                          "--output-format", "json"], 900).get("raw") or []:
            code = str(x.get("code"))
            rel = str(x.get("filename") or "").replace("\\", "/")
            rel = rel.split("agent_system/")[-1].lstrip("/")
            line = x.get("location", {}).get("row")
            if not rel or not line:
                refused.append("ruff %s at %r" % (code, x.get("filename")))
                continue
            findings.append({
                "reader": "ruff_reader",
                "rule": code,
                "severity": severity_for(code),
                "path": rel,
                "line": int(line),
                "cite_ref": "%s:%s" % (rel, line),
                "symbol": "",
                "message": str(x.get("message") or "")[:180],
                # MEASURED DEFECT, FIXED HERE: the first version passed only
                # `(code, rel)`, so EVERY ruff finding in one file collapsed to
                # ONE fingerprint. MEASURED: the 6 sample rows all read
                # `72fec7c215c5a2b0`. The harm is not cosmetic: once a file has
                # ONE queued F821, a NEW undefined name in the SAME file carries
                # the identical fingerprint and is therefore **silently dropped**
                # — the scanner would stop reporting new bugs in a file it had
                # already flagged.
                #
                # THE MESSAGE NAMES THE SYMBOL ("Undefined name `conn`"), so it is
                # the right `symbol` input: two different undefined names in one
                # file become two findings, while a finding that merely MOVES
                # (same rule + same message) keeps its identity — which is what
                # S7 asserts. Six uses of the SAME undefined name in one file
                # legitimately collapse to one row: fixing it is ONE action.
                "fingerprint": fingerprint(code, rel,
                                           str(x.get("message") or "")),
            })

    # ---- reader 2: a parameter rebound in its own function ---------------
    import code_shape as csh
    n_shape = 0
    for p in eb.source_files():
        if p.name.startswith("_"):
            continue
        try:
            r = csh.redefined_params(p)
        except Exception:
            continue
        if not r.get("ok"):
            continue
        for f in (r.get("findings") or []):
            rel = _rel(Path(str(f.get("file") or p)))
            fn = str(f.get("function") or "")
            findings.append({
                "reader": "code_shape",
                "rule": "param_rebound",
                "severity": severity_for("param_rebound"),
                "path": rel,
                "line": int(f.get("line") or 0),
                "cite_ref": str(f.get("cite_ref") or "%s:%s"
                                 % (rel, f.get("line"))),
                "symbol": fn,
                "message": ("parameter %r is rebound in %s(), so the output is "
                            "true by construction" % (f.get("param"), fn)),
                "fingerprint": fingerprint("param_rebound", rel,
                                           "%s.%s" % (fn, f.get("param"))),
            })
            n_shape += 1

    # ---- reader 1: hardcoded literals (OPT-IN; it needs a live conn) -----
    n_hard = 0
    if include_hardcode:
        try:
            import sqlite3 as _s
            conn = _s.connect(str(BASE_DIR / "agent.db"), timeout=20)
            conn.row_factory = _s.Row
            try:
                import hardcode_scan as hc
                for p in eb.source_files():
                    for c in hc.scan_file(p, BASE_DIR, conn=conn):
                        if c.get("is_comment") or c.get("is_message") \
                                or c.get("excluded"):
                            continue
                        rel = _rel(Path(str(c.get("file") or p)))
                        ln = int(c.get("line") or 0)
                        rule = str(c.get("rule") or "HARDCODE")
                        findings.append({
                            "reader": "hardcode_scan",
                            "rule": rule,
                            "severity": "CLEANUP",
                            "path": rel,
                            "line": ln,
                            "cite_ref": "%s:%s" % (rel, ln),
                            "symbol": "",
                            "message": str(c.get("literal") or "")[:120],
                            "fingerprint": fingerprint(rule, rel,
                                                       str(c.get("literal"))[:40]),
                        })
                        n_hard += 1
            finally:
                conn.close()
        except Exception as exc:
            refused.append("hardcode_scan could not run: %s: %s"
                           % (type(exc).__name__, exc))

    if limit:
        findings = findings[:int(limit)]

    by_rule: dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    return {
        "ok": True,
        "ruff_ok": ruff_ok,
        "population": {"source_files_seen": len(eb.source_files()),
                       "readers": ["ruff_reader", "code_shape"]
                                  + (["hardcode_scan"] if include_hardcode
                                     else [])},
        "counts": {"findings": len(findings), "shape": n_shape,
                   "hardcode": n_hard, "refused": len(refused)},
        "by_severity": dict(sorted(by_sev.items())),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "refused": refused[:20],
        "findings": findings,
    }


def open_fingerprints(conn: sqlite3.Connection) -> set[str]:
    """The fingerprints ALREADY waiting in the queue — the dedupe key set.

    READ-ONLY. Reads `validate_task_queue` (the EXISTING waiting list, MEASURED
    at 142 rows) and pulls the fingerprint out of each payload. A queue row that
    carries no fingerprint is IGNORED rather than assumed new: a payload nobody
    can key is not something to dedupe against.
    """
    out: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT payload FROM validate_task_queue WHERE status IN "
            "('pending','running')").fetchall()
    except Exception:
        return out
    import json
    for r in rows:
        try:
            payload = r["payload"] if not isinstance(r, tuple) else r[0]
            data = payload if isinstance(payload, dict) else json.loads(payload)
        except Exception:
            continue
        fp = str((data or {}).get("code_scan_fingerprint") or "").strip()
        if fp:
            out.add(fp)
    return out


def new_findings(res: dict, already: set[str], *,
                 min_severity: str = "BUG") -> list[dict]:
    """Only findings that are NEW and at or above `min_severity`.

    The DEFAULT is `BUG`, deliberately: the human asked "can it found out BUG",
    and MEASURED the site has 53 F821 + 5 F811 + 353 rebound params while it also
    has ~1500 cleanup nits. Defaulting to everything would bury the 58 crashes
    under the nits, which is the same "unreachable target" failure in list form.
    """
    order = {"BUG": 0, "SHAPE": 1, "CLEANUP": 2, "UNCLASSIFIED": 3}
    cut = order.get(min_severity, 0)
    out = []
    for f in res.get("findings") or []:
        if f["fingerprint"] in already:
            continue
        if order.get(f["severity"], 9) > cut:
            continue
        out.append(f)
    return out


def enqueue_new(res: dict, already: set[str], *, apply: bool = False,
                min_severity: str = "BUG",
                conn: sqlite3.Connection | None = None) -> dict:
    """Put only the NEW findings on the EXISTING waiting list.

    It writes through the existing queue API (`enqueue_task`), NOT a new table:
    MEASURED, `validate_task_queue` already holds the flow and
    `run_task_queue_kicker.py` already drains it. A second list would be the
    "second truth" defect.

    `apply=False` (default) is a DRY RUN: it reports what WOULD be queued.

    `conn` (optional) resolves the OWNER of each file, so a row says WHO fixes it.
    Without it the owner is `UNRESOLVED` — reported as such, never guessed.
    """
    todo = new_findings(res, already, min_severity=min_severity)
    # ONE ROW PER FIXING ACTION. MEASURED 2026-09-28: the first --apply wrote 58
    # rows for only **17** distinct fingerprints, because the same undefined name
    # (`conn`) is used 18 times in one file. Every one of those is the SAME
    # action — add the import / define the name. A waiting list with 58 near-
    # duplicate rows for 17 actions is a list nobody can work.
    #
    # So the first occurrence becomes the row and the REST are carried as
    # `code_scan_cites` with an `occurrences` count: nothing is lost, and the list
    # is a to-do list of ACTIONS.
    groups: dict[str, list[dict]] = {}
    for f in todo:
        groups.setdefault(f["fingerprint"], []).append(f)
    # THE OWNER, resolved once per file — this is the "will fix by who" answer.
    owners: dict[str, dict] = {}
    # AND THE ENTITY, resolved once per file — the "task with task ID and
    # entity_id" identity the human named. A task with no entity_id is a task
    # nobody can trace back to the code it is about.
    entities: dict[str, dict] = {}
    for f in todo:
        if f["path"] not in owners:
            owners[f["path"]] = (owner_for(conn, f["path"]) if conn is not None
                                 else {"state": "UNRESOLVED", "owner": "",
                                       "cite_ref": ""})
            entities[f["path"]] = (entity_for(conn, f["path"])
                                   if conn is not None
                                   else {"state": "UNRESOLVED",
                                         "entity_type": "", "entity_ref_id": None,
                                         "entity_id": "", "cite_ref": ""})
        f["owner"] = owners[f["path"]]
        f["entity"] = entities[f["path"]]

    out = {"would_enqueue": len(groups), "occurrences": len(todo),
           "by_severity": {}, "by_owner_state": {}, "by_entity_state": {},
           "applied": False, "sample": []}
    for f in todo:
        out["by_severity"][f["severity"]] = \
            out["by_severity"].get(f["severity"], 0) + 1
    primary: list[tuple[dict, list[dict]]] = []
    for fp, fs in groups.items():
        primary.append((fs[0], fs))
    for p in primary:
        st = (p[0].get("owner") or {}).get("state") or "UNRESOLVED"
        out["by_owner_state"][st] = out["by_owner_state"].get(st, 0) + 1
        es = (p[0].get("entity") or {}).get("state") or "UNRESOLVED"
        out["by_entity_state"][es] = out["by_entity_state"].get(es, 0) + 1
    out["sample"] = [dict(p[0], occurrences=len(p[1]),
                          cites=[x["cite_ref"] for x in p[1]][:8])
                     for p in primary[:5]]
    if not apply or not primary:
        return out
    from src.task_center.ontology_store import (
        enqueue_task,
        ensure_ontology_registry_schema,
    )
    ensure_ontology_registry_schema()
    n = 0
    for f, fs in primary:
        own = f.get("owner") or {}
        ent = f.get("entity") or {}
        payload = {
            "task": "CODE.BUG — %s at %s (%d occurrence%s)"
                    % (f["rule"], f["cite_ref"], len(fs),
                       "" if len(fs) == 1 else "s"),
            "channel": "local_pc",
            "module": "code_quality",
            "capability": "code_scan.scan",
            "code_scan_rule": f["rule"],
            "code_scan_severity": f["severity"],
            "code_scan_cite_ref": f["cite_ref"],
            "code_scan_cites": [x["cite_ref"] for x in fs],
            "code_scan_occurrences": len(fs),
            "code_scan_fingerprint": f["fingerprint"],
            "code_scan_message": f["message"],
            # WHO FIXES IT. MEASURED: half the bug files have NO code_registry
            # row, so "UNOWNED" is a real answer and must be visible rather than
            # silently blank — an unowned row is the one nobody is accountable
            # for, and it is the one a reader must be able to SPOT.
            "code_scan_owner_state": own.get("state") or "UNRESOLVED",
            "code_scan_owner": own.get("owner") or "",
            "code_scan_owner_cite": own.get("cite_ref") or "",
            # THE OBJECT: a task with a task id AND an entity id (the human's
            # correction). `entity_id` is `R-<code_registry.id>`, READ from the
            # register; a file with no row is UNRESOLVED, never minted.
            "entity_type": ent.get("entity_type") or "",
            "entity_ref_id": ent.get("entity_ref_id"),
            "entity_id": ent.get("entity_id") or "",
            "entity_state": ent.get("state") or "UNRESOLVED",
            "entity_cite": ent.get("cite_ref") or "",
        }
        try:
            r = enqueue_task(payload, priority=1 if f["severity"] == "BUG" else 5)
            if r.get("ok"):
                n += 1
        except Exception:
            continue
    out["applied"] = True
    out["enqueued"] = n
    return out


def main() -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description="whole-site coding-quality scan")
    ap.add_argument("--no-hardcode", action="store_true")
    ap.add_argument("--min-severity", default="BUG",
                    choices=["BUG", "SHAPE", "CLEANUP", "UNCLASSIFIED"])
    ap.add_argument("--apply", action="store_true",
                    help="enqueue the NEW findings on the existing waiting list")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = scan(include_hardcode=not args.no_hardcode)
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        already = open_fingerprints(conn)
        todo = enqueue_new(res, already, apply=args.apply,
                           min_severity=args.min_severity, conn=conn)
    finally:
        conn.close()
    if args.json:
        print(json.dumps({"scan": {k: res[k] for k in
                                   ("ok", "population", "counts",
                                    "by_severity", "by_rule")},
                          "already_waiting": len(already),
                          "todo": {k: v for k, v in todo.items()
                                   if k != "sample"}},
                         indent=1, ensure_ascii=False))
    else:
        print("population : %s" % res["population"])
        print("findings   : %s" % res["counts"])
        print("by severity: %s" % res["by_severity"])
        print("by rule    : %s" % res["by_rule"])
        print("already waiting (dedupe): %d" % len(already))
        print("NEW at %s: %s" % (args.min_severity, todo["by_severity"]))
        print("WHO FIXES IT: %s" % todo.get("by_owner_state"))
        print("ENTITY      : %s" % todo.get("by_entity_state"))
        for f in todo["sample"]:
            own = f.get("owner") or {}
            print("   %-5s %-6s %-44s owner=%s%s"
                  % (f["severity"], f["rule"], f["cite_ref"],
                     own.get("state"),
                     (" (" + own["owner"] + ")") if own.get("owner") else ""))
    return 0


# MEASURED DEFECT IN MY OWN FIRST WRITE: this file defined `main()` and never
# CALLED it, so `python code_scan.py` exited 0 with ZERO output — a scanner that
# reports nothing and looks successful. That is the silent-empty failure this
# whole layer exists to catch, so the entry point is stated explicitly.
if __name__ == "__main__":
    sys.exit(main())