# -*- coding: utf-8 -*-
"""apply_definition_evidence.py — replace a PROSE definition with the EVIDENCE one.

THE HUMAN (2026-09-27): "do it now", after `_run_definition_evidence.py` measured
**109 terms whose definition is prose with no fact behind it** and the 7B second
door FAILed 134 of 150.

WHAT IT REPLACES, AND WHAT IT REFUSES TO REPLACE
------------------------------------------------
It replaces a definition ONLY when the deterministic check reports `PROSE` — the
definition names no field of the object it is supposed to describe. A definition
that already names facts is LEFT ALONE even when the 7B dislikes its wording: the
deterministic check is the gate, and the 7B is a SECOND OPINION, not the trigger.

THE OLD DEFINITION IS WRITTEN DOWN BEFORE IT IS REPLACED
--------------------------------------------------------
`terminology_registry` has NO history table — MEASURED: the only tables matching
`%history%` are `ontology_revision_history` and `alert_history`, and
`definition_sha256` simply OVERWRITES. So `update_term` would DESTROY the old
definition, which is the only record of what an earlier reader understood.

Therefore nothing is written until EVERY old (definition, cite_ref, sha256) has been
persisted to a ledger file, and `--undo <ledger>` exists to put them all back. A
replacement with no record of what it replaced is a memory loss, not an edit.

IT REUSES THE WRITE PATH. `terminology_registry.update_term` does the write — this
module does not build its own UPDATE, because two writers of one column is the
defect the repo keeps naming.

Run:
    .\\.venv\\Scripts\\python.exe apply_definition_evidence.py --dry-run
    .\\.venv\\Scripts\\python.exe apply_definition_evidence.py --apply
    .\\.venv\\Scripts\\python.exe apply_definition_evidence.py --undo <ledger.json>
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import definition_from_evidence as dfe  # noqa: E402
import terminology_registry as tr  # noqa: E402

DB = BASE / "agent.db"
LEDGER_DIR = BASE / "qc_evidence"


def targets(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """`(to_change, protected)`. PROSE is the trigger; DOMAIN KNOWLEDGE is the guard.

    MEASURED, and it is why there are TWO lists: of the 109 definitions the
    deterministic check flagged as PROSE, **11 name a REAL VALUE** that exists in
    the table — `catalog` ("which kind of work a skill belongs to for FINDING it"),
    `tdd_type`, `route_registry`, `plan_session_log`. Those say what the rows MEAN,
    which a schema dump cannot, so replacing them would trade an uncheckable
    definition for a DESTROYED one.

    So the trigger is PROSE minus the guard, and the protected set is RETURNED rather
    than filtered away silently — a definition left alone is a decision, and a
    decision nobody can see is the defect this repo keeps naming.
    """
    to_change, protected = [], []
    for t in conn.execute(
            "SELECT term_id, term_key, definition, cite_ref, definition_sha256 "
            "FROM terminology_registry WHERE is_active = 1 ORDER BY term_key"):
        f = dfe.facts_for(conn, str(t["term_key"]))
        if not f.get("ok"):
            continue
        unsupported = dfe.unsupported_claims(str(t["definition"]), f)
        if "PROSE" not in unsupported:
            continue
        gen = dfe.describe(f)
        if not gen:
            continue
        hit = dfe.names_a_real_value(conn, f, str(t["definition"]))
        rec = {
            "term_id": int(t["term_id"]), "term_key": str(t["term_key"]),
            "old_definition": str(t["definition"]),
            "old_cite_ref": str(t["cite_ref"]),
            "old_sha256": str(t["definition_sha256"]),
            "new_definition": gen, "new_cite_ref": f["cite"],
            "object_kind": f["kind"], "unsupported": unsupported,
            "named_values": hit,
        }
        (protected if hit else to_change).append(rec)
    return to_change, protected


def refresh_row_count_pins(conn: sqlite3.Connection, *, apply: bool) -> dict:
    """Repair definitions that PIN a live row count. REPORTED, then rewritten.

    MEASURED DEFECT IN MY OWN FIRST VERSION of `describe()`: it wrote
    `holds 12 row(s)`, and a row count changes on the next INSERT — so 97 definitions
    were WRONG the moment the table grew. The repo's own law names this: "a check that
    pins a NUMBER a legitimate operation moves is STALE".

    A definition that pins no count is LEFT ALONE. The trigger is the pinned count
    itself, found by LOOKING at the text and re-measuring it against the DB.
    """
    rows = list(conn.execute(
        "SELECT term_id, term_key, definition, cite_ref FROM terminology_registry "
        "WHERE is_active = 1 AND definition LIKE '% row(s)%'"))
    out = []
    for t in rows:
        f = dfe.facts_for(conn, str(t["term_key"]))
        if not f.get("ok"):
            continue
        gen = dfe.describe(f)
        if not gen or gen == str(t["definition"]):
            continue
        out.append({"term_id": int(t["term_id"]), "term_key": str(t["term_key"]),
                    "old_definition": str(t["definition"]),
                    "old_cite_ref": str(t["cite_ref"]),
                    "new_definition": gen, "new_cite_ref": f["cite"]})
    if not apply:
        return {"found": len(rows), "to_change": len(out), "items": out}
    if out:
        write_ledger([dict(x, old_sha256="") for x in out])
    changed = 0
    for it in out:
        r = tr.update_term(conn, it["term_id"], definition=it["new_definition"],
                           cite_ref=it["new_cite_ref"])
        if r.get("ok"):
            changed += 1
    conn.commit()
    left = conn.execute("SELECT COUNT(*) FROM terminology_registry "
                        "WHERE is_active=1 AND definition LIKE '% row(s)%'").fetchone()[0]
    return {"found": len(rows), "to_change": len(out), "changed": changed,
            "still_pinning": int(left), "items": out}


def _ledger_path() -> Path:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    return LEDGER_DIR / ("definition_evidence_ledger_%s.json"
                         % datetime.now().strftime("%Y%m%d_%H%M%S"))


def write_ledger(items: list[dict]) -> Path:
    p = _ledger_path()
    p.write_text(json.dumps({"created_at": datetime.now().isoformat(),
                             "count": len(items), "items": items},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def undo(conn: sqlite3.Connection, ledger: Path, *, apply: bool) -> dict:
    """Put every recorded definition back. The ledger is the ONLY source."""
    data = json.loads(ledger.read_text(encoding="utf-8"))
    restored, refused = 0, []
    for it in data["items"]:
        if not apply:
            restored += 1
            continue
        r = tr.update_term(conn, int(it["term_id"]),
                           definition=it["old_definition"],
                           cite_ref=it["old_cite_ref"])
        if r.get("ok"):
            restored += 1
        else:
            refused.append({"term_id": it["term_id"], "reason": r.get("reason")})
    if apply:
        conn.commit()
    return {"ledger": str(ledger), "recorded": data["count"],
            "restored": restored, "refused": refused}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--undo", default="", metavar="LEDGER")
    ap.add_argument("--refresh-row-count", action="store_true",
                    help="repair definitions that PIN a live row count")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        if a.refresh_row_count:
            r = refresh_row_count_pins(conn, apply=bool(a.apply))
            print("REFRESH definitions that PIN a live row count")
            print("  pinning a count : %d" % r["found"])
            print("  to change       : %d" % r["to_change"])
            if a.apply:
                print("  changed         : %d" % r["changed"])
                print("  STILL pinning   : %d (re-measured)" % r["still_pinning"])
            return 0
        if a.undo:
            r = undo(conn, Path(a.undo), apply=True)
            print("UNDO from %s" % r["ledger"])
            print("  recorded  : %d" % r["recorded"])
            print("  restored  : %d" % r["restored"])
            print("  refused   : %s" % (r["refused"] or "none"))
            return 0 if not r["refused"] else 1

        items, protected = targets(conn)
        print("=" * 78)
        print("REPLACE A PROSE DEFINITION WITH THE EVIDENCE ONE")
        print("=" * 78)
        print("  PROSE candidates                : %d" % (len(items) + len(protected)))
        print("  PROTECTED (names a REAL value)  : %d  <-- LEFT ALONE, domain knowledge"
              % len(protected))
        print("  TO CHANGE                       : %d" % len(items))
        print("  trigger                         : the DETERMINISTIC check, MINUS the "
              "domain-knowledge guard (the 7B is a 2nd opinion, not a trigger)")
        if protected:
            print()
            print("  --- PROTECTED (a schema dump would DESTROY what this says) ---")
            for p in protected[:12]:
                print("     %-26s names %s" % (p["term_key"], p["named_values"][:2]))
        print()
        for it in items[:8]:
            print("  %s (%s)" % (it["term_key"], it["object_kind"]))
            print("     OLD: %s" % it["old_definition"][:96])
            print("     NEW: %s" % it["new_definition"][:96])
        if len(items) > 8:
            print("  ... and %d more" % (len(items) - 8))

        if a.dry_run:
            print()
            print("DRY RUN — nothing written. `--apply` writes a LEDGER first.")
            return 0

        # ---- THE LEDGER COMES FIRST. Nothing is written before it exists. ----
        led = write_ledger(items)
        print()
        print("ledger written BEFORE any change: %s" % led.name)
        print("  (a replacement with no record of what it replaced is a memory "
              "loss, not an edit)")
        bak = DB.with_name(DB.name + ".bak_definition_evidence_"
                           + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(DB, bak)
        print("db backup: %s" % bak.name)

        changed, refused = 0, []
        for it in items:
            r = tr.update_term(conn, int(it["term_id"]),
                               definition=it["new_definition"],
                               cite_ref=it["new_cite_ref"])
            if r.get("ok"):
                changed += 1
            else:
                refused.append({"term_key": it["term_key"],
                                "reason": r.get("reason")})
        conn.commit()

        # ---- VERIFY from the DB, not from the return values ---------------
        still_prose = 0
        for it in items:
            f = dfe.facts_for(conn, it["term_key"])
            d = conn.execute("SELECT definition FROM terminology_registry "
                             "WHERE term_id=?", (it["term_id"],)).fetchone()[0]
            if "PROSE" in dfe.unsupported_claims(str(d), f):
                still_prose += 1
        print()
        print("  changed                         : %d" % changed)
        print("  refused                         : %s" % (refused or "none"))
        print("  STILL PROSE after (re-measured) : %d" % still_prose)
        print("  undo with                       : --undo %s" % led.name)
        return 0 if not refused and still_prose == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
