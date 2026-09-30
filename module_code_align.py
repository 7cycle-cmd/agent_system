# -*- coding: utf-8 -*-
"""module_code_align.py — align the LEGACY module codes where a registry ALREADY
                          certifies the code, and REPORT the rest by measured role.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
Open work #4. The user's instruction was:

    "8 個 module code 需要你決定係「改名對應 **by teminology register, all by
     evidence**"

THE PREMISE DOES NOT HOLD, AND THAT IS MEASURED, NOT ASSERTED
------------------------------------------------------------
`terminology_registry` has NO mapping to give:
    * `alias_list` is empty or `'NA'` on every row;
    * `definition LIKE '%<code>%'` returns ZERO hits for ALL 10 codes.
So "對應 by terminology register" cannot be satisfied by READING it. The register
is the RECORD of a decision, not the source of one. Producing a mapping anyway
would be a finding with no citation — which this repo refuses.

THE CODES ARE NOT ONE KIND OF THING, SO ONE RULE WOULD BE WRONG
--------------------------------------------------------------
Measured role per code:
    worker_heartbeat  -> a `db_table_registry` ROW (a TABLE, not a module)
    member_card       -> an `app` AND an `onto_concept` (a DOMAIN ENTITY)
    shop_member       -> the same
    ollama            -> an `app` of kind='service'; `module_registry` already has
                         `llm_runtime` ("Local LLM runtime (qwen2.5vl:7b ...)")
    membership        -> `onto_binding` binds it with bind_type='module'
    openclaw_companion-> already `bind_type='module'`
    agent_db          -> 54 dev_task rows, no app / system_key / concept
    code_health       -> 10 dev_task rows, same
    schema_qc         -> no evidence beyond the legacy row
    watchdog          -> the same
    openclaw_gateway  -> the same
Renaming `worker_heartbeat` (a table) to a module, or `member_card` (an app AND a
concept) to a module, would be a WRONG answer — worse than a missing one.

WHY "FIND THE SIMILARLY NAMED ROW" FAILS
----------------------------------------
`module_registry` holds 23,000+ rows and only FOUR non-underscore keys
(`llm_runtime`, `mouse_spot_helper`, `openclaw_companion`, `task_center`). Almost
every row is a `.py` FILE STEM imported in bulk, so a similarly named row may be a
file, not a module.

THE RULE THIS MODULE APPLIES (derived from the measurements above)
-----------------------------------------------------------------
    A record is ALIGNED only where a registry ALREADY says the code is a module.
    Every other record is REPORTED with its actual measured role.

NOTHING IS RENAMED and NO `module_registry` row is created.
`record_certified()` writes a `terminology_registry` row ONLY for a code a registry
certifies as a module — which is what "by terminology register" can honestly mean:
the register RECORDS the decision the evidence supports.

Run:
    .\\.venv\\Scripts\\python.exe module_code_align.py --measure
    .\\.venv\\Scripts\\python.exe module_code_align.py            # dry run
    .\\.venv\\Scripts\\python.exe module_code_align.py --apply
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

# The app_key -> registry module_key links the EVIDENCE supports. Each one is
# justified by the app's OWN `description`, quoted, so it is checkable:
#   ollama  "Local LLM runtime (qwen2.5vl:7b vision, qwen2.5:7b-instruct text)"
#           -> `llm_runtime` ("LLM Runtime")
# `member_card` / `shop_member` are apps TOO, and they are deliberately NOT here:
# they are also `onto_concept` rows, so they are a DOMAIN ENTITY and mapping them
# to a module would pick one of their two roles and discard the other.
APP_SERVICE_TO_MODULE: dict[str, str] = {
    "ollama": "llm_runtime",
}

# Codes the LEGACY `module` table holds. Read at run time, this is only the shape
# of what to expect, so a NEW legacy row shows up in the report instead of hiding.
EXPECTED_CODES = 13

_MARKERS = (
    ("table", "db_table_registry", "table_key", "a database TABLE"),
    ("concept", "onto_concept", "title",
     "an ontology CONCEPT (a domain entity)"),
    ("app", "app", "app_key", "a registered APP"),
)


def _pk_col(conn: sqlite3.Connection, table: str) -> str | None:
    """The table's pk column, READ from the schema. A name guess MEASURED WRONG:
    `onto_concept`'s pk is `id`, not `concept_id`."""
    try:
        rows = [c for c in conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return None
    pks = [c[1] for c in rows if len(c) > 5 and int(c[5] or 0) > 0]
    return str(pks[0]) if len(pks) == 1 else None

# The registry ROWS that can CERTIFY a code is a module, and how to cite each.
# The citation form is `register:<table>:<pk>` because `citation_discipline`
# ACCEPTS that shape and REJECTS prose — MEASURED: my first cite_ref
# ("module.code=ollama; app.app_key = 'ollama'") was refused with
# `UNCITEABLE_CITE_REF: ... prose is not a reference`. A prose citation is not a
# finding, so the citer reads the row's pk.
CERTIFIERS: tuple[tuple[str, str], ...] = (
    ("module_registry", "module_key"),
    ("onto_binding", "bind_key"),
    ("app", "app_key"),
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> Any:
    if not _table_exists(conn, sql.split("FROM", 1)[1].split()[0]):
        return None
    try:
        r = conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None
    return r[0] if r else None


def terminology_mapping(conn: sqlite3.Connection, codes: list[str]) -> dict[str, Any]:
    """Measure what the TERMINOLOGY register supplies for the LEGACY MODULE CODES.

    NEVER ASSUMED, and SCOPED (corrected 2026-09-24). The original version counted
    a non-NA alias on ANY row of the register. MEASURED: `terminology_registry`
    now legitimately carries aliases for OTHER things (`case_registry <- 
    case_registry`, `goal <- purpose`), so a whole-table count reports those and
    the premise "the register supplies no mapping" became unmeasurable — the check
    whose SUBJECT the task changed must measure a pre-state, or it invalidates
    itself. The scope here is the question actually asked: does the register
    supply a mapping for a LEGACY MODULE CODE?
    """
    if not _table_exists(conn, "terminology_registry"):
        return {"ok": False, "reason": "terminology_registry absent",
                "alias_nonempty": 0, "definition_hits": {}}
    rows = [dict(r) for r in conn.execute(
        "SELECT term_key, alias_list, definition FROM terminology_registry")]
    code_set = {str(c) for c in codes}

    # A row is only evidence about a MODULE CODE when it NAMES one of the codes
    # this function was asked about — not when it happens to hold some alias.
    relevant = [r for r in rows if str(r["term_key"]) in code_set]
    alias_nonempty = [r["term_key"] for r in relevant
                      if (r["alias_list"] or "").strip()
                      and (r["alias_list"] or "").strip().upper() != "NA"]
    hits: dict[str, list[str]] = {}
    for c in codes:
        # WHOLE-TOKEN MATCH, AND A DOT IS PART OF THE TOKEN. MEASURED BUG: a bare
        # `c in definition` matched the code `mouse_spot_helper` inside the CURRENT
        # key `mouse_spot_helper.core` — 9 of 10 reported hits were SUBSTRING false
        # positives, and `_proof_module_code_align.py` went RED on correct data.
        # A first fix excluded only `[0-9A-Za-z_]`, which still let `mouse_spot_helper`
        # match inside `mouse_spot_helper.core`, because `.` was treated as a
        # boundary. `a.b` is a DIFFERENT name from `a`, so the dot belongs to the
        # token. Same bug class as the migration-log substring match.
        pat = re.compile(r"(?<![0-9A-Za-z_.])%s(?![0-9A-Za-z_.])"
                         % re.escape(str(c)))
        hits[c] = [r["term_key"] for r in rows
                   if pat.search(str(r["definition"] or ""))
                   or c == str(r["term_key"])]
    return {"ok": True, "terms": len(rows), "code_rows": len(relevant),
            "alias_nonempty": len(alias_nonempty),
            "alias_nonempty_keys": alias_nonempty,
            "aliases_elsewhere": len([r for r in rows
                                      if (r["alias_list"] or "").strip()
                                      and (r["alias_list"] or "").strip()
                                      .upper() != "NA"]),
            "definition_hits": hits,
            "verdict": ("the register supplies NO mapping for these %d module "
                        "codes: %d of the %d rows that NAME a code carry a "
                        "non-NA alias, and %d of %d codes appear in any term_key "
                        "or definition"
                        % (len(codes), len(alias_nonempty), len(relevant),
                           sum(1 for c in codes if hits[c]), len(codes)))}


def classify(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every LEGACY module code, with its MEASURED role and a citation."""
    legacy = [dict(r) for r in conn.execute(
        "SELECT id, code, name FROM module ORDER BY id")] \
        if _table_exists(conn, "module") else []
    out: list[dict[str, Any]] = []
    for row in legacy:
        code = str(row["code"])
        evidence: list[str] = []
        role = "unregistered"
        # --- what ELSE is this code, measured across the registries -----------
        marks: list[str] = []
        for label, tbl, col, human in _MARKERS:
            if not _table_exists(conn, tbl):
                continue
            pk = _pk_col(conn, tbl)
            if not pk:
                continue
            r = conn.execute("SELECT %s FROM %s WHERE %s = ?" % (pk, tbl, col),
                             (code,)).fetchone()
            if not r:
                continue
            marks.append("%s:%s" % (label, human))
            evidence.append("register:%s:%s" % (tbl, int(r[0])))
        # --- the system_key vocabulary (a SECOND namespace) -------------------
        sysk = conn.execute(
            "SELECT COUNT(*) FROM code_registry WHERE system_key = ?",
            (code,)).fetchone()[0] if _table_exists(conn, "code_registry") else 0
        if sysk:
            evidence.append("code_registry.system_key = %r (%d rows)"
                            % (code, sysk))
        # --- the ontology's OWN ruling (it may say the code is a module) ------
        bind_type = None
        if _table_exists(conn, "onto_binding"):
            b = conn.execute("SELECT bind_type FROM onto_binding WHERE bind_key = ?",
                             (code,)).fetchone()
            bind_type = str(b[0]) if b else None
            if bind_type:
                b_id = conn.execute("SELECT id FROM onto_binding WHERE "
                                    "bind_key = ?", (code,)).fetchone()
                evidence.append("register:onto_binding:%s" % int(b_id[0]))
        # --- how much work was filed under it ---------------------------------
        n_tasks = conn.execute(
            "SELECT COUNT(*) FROM dev_task d JOIN module m ON m.id = d.module_id "
            "WHERE m.code = ?", (code,)).fetchone()[0] + 0.0 \
            if _table_exists(conn, "dev_task") else 0
        n_tasks = int(n_tasks)
        if n_tasks:
            evidence.append("dev_task rows filed under it: %d" % n_tasks)
        # --- the verdict ------------------------------------------------------
        # THE CERTIFYING ROW, read (never guessed): the registry row that says this
        # code IS a module. Its pk is what makes the citation checkable.
        cert: dict[str, Any] | None = None
        for tbl, col in CERTIFIERS:
            if not _table_exists(conn, tbl):
                continue
            pk = _pk_col(conn, tbl)
            if not pk:
                continue
            r = conn.execute("SELECT %s FROM %s WHERE %s = ?" % (pk, tbl, col),
                             (code,)).fetchone()
            if not r:
                continue
            if tbl == "onto_binding":
                b = conn.execute("SELECT bind_type FROM onto_binding WHERE "
                                 "id = ?", (int(r[0]),)).fetchone()
                if str(b[0]) != "module":
                    # A binding that is NOT `module` does NOT certify a module.
                    continue
            # THE CITATION FORM. `terminology_cite.verify_cite_ref` (the checker
            # `add_term` actually uses) accepts ONLY: a `measured:` note, a
            # command, or a path THAT EXISTS ON DISK. MEASURED: a
            # `register:app:4` form is accepted by `citation_discipline.is_citation`
            # but REFUSED here — the two checkers DISAGREE, and this is the
            # authoritative one. `measured:` is therefore used, and it NAMES the
            # row, so the claim is re-measurable even though no file is opened.
            cert = {"table": tbl, "pk": int(r[0]),
                    "cite": ("measured:%s row %s.%s=%d IS the certifying row "
                             "for module.code=%r"
                             % (tbl, tbl, pk, int(r[0]), code))}
            break
        in_registry = cert is not None and cert["table"] == "module_registry"
        is_bound_module = cert is not None and cert["table"] == "onto_binding"
        if is_bound_module or in_registry:
            role = "module"
            verdict = "ALIGNED"
            why = ("a registry already says this code IS a module"
                   + (" (onto_binding.bind_type='module')" if is_bound_module
                      else " (module_registry row present)"))
        elif "table" in " ".join(marks):
            verdict = "REPORT"
            why = ("a registry says this code is a TABLE, not a module — renaming "
                   "it to a module would be a CATEGORY ERROR")
        elif "concept" in " ".join(marks):
            verdict = "REPORT"
            why = ("this code is also an ontology CONCEPT and an app, so it is a "
                   "DOMAIN ENTITY with two roles; mapping it to a module would "
                   "pick one and discard the other")
        elif code in APP_SERVICE_TO_MODULE:
            role = "app_service"
            verdict = "ALIGNED"
            # The citation is the APP row that describes it, so it is checkable.
            why = ("a registered APP describes this code as a service; "
                   "module_registry already names it %r"
                   % APP_SERVICE_TO_MODULE[code])
        elif "app" in " ".join(marks):
            verdict = "REPORT"
            why = ("a registered APP, not a module, and no registry maps it to one")
        elif n_tasks:
            verdict = "REPORT"
            why = ("build-time concern: %d dev_task row(s) were filed under it and "
                   "no app / system_key / concept certifies a module" % n_tasks)
        else:
            verdict = "REPORT"
            why = ("no evidence beyond the legacy `module` row: no app, no "
                   "system_key, no concept, no dev_task usage, and no ontology "
                   "binding — a rename would have nothing to cite")
        out.append({"legacy_id": int(row["id"]), "code": code,
                    "name": row["name"], "role": role, "verdict": verdict,
                    "why": why, "evidence": evidence, "marks": marks,
                    "system_key_rows": sysk, "bind_type": bind_type,
                    "dev_task_rows": n_tasks, "cert": cert,
                    "cite": ((cert or {}).get("cite")
                             or ("; ".join(evidence) or "no other registry "
                                 "holds this code"))})
    return out


def certified(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The codes a registry CERTIFIES as a module (the only recordable ones)."""
    return [c for c in classify(conn) if c["verdict"] == "ALIGNED"]


def record_certified(conn: sqlite3.Connection, *,
                     commit: bool = True) -> dict[str, Any]:
    """Write the terminology row for each CERTIFIED code. Idempotent.

    A code with no certifying evidence is REFUSED — `terminology_registry.add_term`
    itself refuses an empty citation, and this refuses BEFORE that, naming the code.
    """
    import terminology_registry as tr
    written: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for c in certified(conn):
        # The citation MUST be a CERTIFYING row's `register:table:pk`, because
        # `terminology_registry.add_term` runs it through `citation_discipline` and
        # REJECTS prose — MEASURED: my first attempt was refused with
        # `UNCITEABLE_CITE_REF`. `module_registry` counts as a certifier too, which
        # my first filter missed and silently refused two ALIGNED codes.
        cert = c.get("cert")
        if not cert:
            refused.append({"code": c["code"],
                            "why": "certified with no citing registry row",
                            "cite": c["cite"]})
            continue
        expl = APP_SERVICE_TO_MODULE.get(c["code"], c["code"])
        definition = ("the legacy module code %r; certified as module %r by %s"
                      % (c["code"], expl, cert["cite"]))
        res = tr.add_term(conn, c["code"], definition=definition,
                          cite_ref=cert["cite"],
                          term_kind="entity", taxonomy_level="module",
                          alias_list=[expl] if expl != c["code"] else None,
                          commit=commit)
        # `add_term` answers `ok`; a REFUSAL must not be recorded as a success.
        # MEASURED: an earlier version of this loop ignored `ok` and reported
        # `created=None` for three rows that were NEVER WRITTEN.
        if not res.get("ok"):
            refused.append({"code": c["code"],
                            "why": "add_term refused: %s -- %s"
                                   % (res.get("code"), res.get("message")),
                            "cite": cert["cite"]})
            continue
        written.append({"code": c["code"], "alias": expl,
                        "created": res.get("created"),
                        "term_id": res.get("term_id"), "cite": cert["cite"]})
    return {"ok": True, "written": written, "refused": refused,
            "created": sum(1 for w in written if w.get("created"))}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    cls = classify(conn)
    codes = [c["code"] for c in cls]
    by_verdict: dict[str, int] = {}
    for c in cls:
        by_verdict[c["verdict"]] = by_verdict.get(c["verdict"], 0) + 1
    roles: dict[str, int] = {}
    for c in cls:
        roles[c["role"]] = roles.get(c["role"], 0) + 1
    return {"codes": len(cls), "by_verdict": by_verdict, "roles": roles,
            "terminology": terminology_mapping(conn, codes),
            "certified": [c["code"] for c in cls if c["verdict"] == "ALIGNED"],
            "rows": cls}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    before_mr = conn.execute("SELECT COUNT(*) FROM module_registry").fetchone()[0]
    before_mod = conn.execute("SELECT COUNT(*) FROM module").fetchone()[0]
    res = record_certified(conn)
    after_mr = conn.execute("SELECT COUNT(*) FROM module_registry").fetchone()[0]
    after_mod = conn.execute("SELECT COUNT(*) FROM module").fetchone()[0]
    return {**res, "module_registry_before": before_mr,
            "module_registry_after": after_mr,
            "legacy_module_before": before_mod,
            "legacy_module_after": after_mod,
            "module_registry_created": after_mr - before_mr,
            "legacy_module_delta": after_mod - before_mod}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.measure:
            res = measure(conn)
        elif args.apply:
            res = apply(conn)
        else:
            cls = classify(conn)
            res = {"codes": len(cls),
                   "terminology": terminology_mapping(conn, [c["code"] for c in cls]),
                   "rows": [{"code": c["code"], "verdict": c["verdict"],
                             "why": c["why"]} for c in cls]}
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.measure:
            print("legacy module codes : %d" % res["codes"])
            print("  verdicts          : %s" % res["by_verdict"])
            print("  roles             : %s" % res["roles"])
            print("  certified (recordable): %s" % res["certified"])
            t = res["terminology"]
            print("terminology register: %s" % t.get("verdict"))
            print()
            for c in res["rows"]:
                print("  %-18s %-8s %s" % (c["code"], c["verdict"], c["why"][:78]))
        elif args.apply:
            print("APPLIED: created=%d refused=%d" % (res["created"],
                                                      len(res["refused"])))
            for w in res["written"]:
                print("   %-18s alias=%-18s created=%s" % (w["code"], w["alias"],
                                                           w["created"]))
            for r in res["refused"]:
                print("   REFUSED %s: %s" % (r["code"], r["why"]))
            print("   module_registry %d -> %d (created=%d)"
                  % (res["module_registry_before"], res["module_registry_after"],
                     res["module_registry_created"]))
            print("   legacy module    %d -> %d (delta=%d)"
                  % (res["legacy_module_before"], res["legacy_module_after"],
                     res["legacy_module_delta"]))
        else:
            t = res["terminology"]
            print("DRY RUN: codes=%d" % res["codes"])
            print("   terminology: %s" % t.get("verdict"))
            for r in res["rows"]:
                print("   %-18s %-8s %s" % (r["code"], r["verdict"],
                                            r["why"][:70]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
