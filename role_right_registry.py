"""role_right_registry.py — a ROLE is a DECLARED right set, and it is MEASURABLE.

THE USER'S RULING (2026-09-24), which this module implements rather than restates:

    "researcher 只讀，所以佢嘅輸出必須被 VERIFY（因為冇 artifact 可以查），
     writer 嘅輸出必須符合 STANDARD（因為 artifact 可以查）。兩者係互補。"

and the correction that makes it TRUE:

    a read-only worker does NOT make errors less likely. It makes them HARDER TO
    DETECT, because the worker produces no artifact to inspect. MEASURED, and the
    evidence is this session: a research answer reported "7.0% name coverage" when
    the real figure was 20.2%. Only a PROOF caught it. Had the worker been
    read-only and merely spoken the number, nothing would have.

So the model this module declares is:

    research  -> may_read=1,  may_write=0, may_verify=0
    write     -> may_read=1,  may_write=1, may_verify=0
    verify    -> may_read=1,  may_write=0, may_verify=1

`write` and `verify` are SEPARATE on purpose: a worker that checks its own writing
is not an independent check. The user's two halves COMPLEMENT — the writer has an
artifact a standard can check, the researcher has only a claim, so the claim needs
a verifier.

WHY A NEW AXIS INSTEAD OF A NEW MODE RIGHT
------------------------------------------
`mode_right_registry` keys rights on the MODE (`ask`/`plan`/`agent`). MEASURED: it
has no `read_only` and no `verification` right, and it CANNOT answer "may THIS
WORKER write", because a mode is not a worker. So the WORKER axis is added BESIDE
it and the MODE axis is left untouched.

WHAT THIS MODULE REUSES, AND WHY
--------------------------------
`role_capability.py` ALREADY has `set_worker_role` / `authority_for`, and
`worker_registry.role_id` ALREADY exists. MEASURED: `role_id` is NULL on all 6
workers and `role_capability` defines NO roles (`researcher`/`writer`/`verifier` do
not appear). So the MECHANISM existed and the CONTENT did not — which is exactly
the shape this repo keeps finding. This module supplies the DECLARED vocabulary and
the MEASUREMENT; it does not rebuild the mechanism.

FAIL CLOSED
-----------
A worker whose `role_id` is NULL is reported `NO_ROLE` with `may_write=None`, NOT
`may_write=1`. A missing declaration is UNKNOWN, and an unknown that reads as
"allowed" is how a gate stops gating.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"

# ---------------------------------------------------------------------------
# THE CLOSED ROLE VOCABULARY, as data. It follows the shape
# `capability_kind_registry` already proves (a key + a definition + is_active), so
# there is no new concept — only a new axis.
#
# A role is a RIGHT SET, not a job title, so `writer`/`verifier` are separate: a
# worker that checks its own writing is not an independent check.
# ---------------------------------------------------------------------------
ROLES: dict[str, dict[str, Any]] = {
    "researcher": {
        "definition": ("READS and REPORTS. Produces no product artifact, so its "
                       "output is a CLAIM and must be verified before it is relied "
                       "on."),
        "may_read": 1, "may_write": 0, "may_verify": 0,
        "because": ("a read-only worker cannot damage state, but its CLAIM has no "
                    "artifact for a standard to check"),
    },
    "writer": {
        "definition": ("PRODUCES a product artifact (code, a plan, an execution "
                       "log) that must CONFORM to its declared standard."),
        "may_read": 1, "may_write": 1, "may_verify": 0,
        "because": ("its output IS an artifact, so a format standard can check it "
                    "without a second worker"),
    },
    "verifier": {
        "definition": ("CHECKS another worker's output against a standard or a "
                       "proof. Independent by construction."),
        "may_read": 1, "may_write": 0, "may_verify": 1,
        "because": ("a claim with no artifact (a researcher's report) can only be "
                    "checked by an independent measurement"),
    },
}

# WHAT each right MEANS, so a reader does not infer it from a column name.
RIGHTS: dict[str, str] = {
    "read": "may READ/measure anything: files, the DB, the screen",
    "write": "may WRITE a product artifact (code, a plan, an execution log)",
    "verify": "may ISSUE a verdict on another worker's output",
}

# The INSTRUMENT each role is checked by. A role with no instrument would be an
# unmeasurable role — the failure this module exists to prevent.
INSTRUMENT: dict[str, str] = {
    "researcher": "a proof or a second measurement (the claim has no artifact)",
    "writer": "artifact_format.py --check (the artifact has a declared format)",
    "verifier": "skill_contract_store / the proof of the thing verified",
}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the two registers the WORKER axis needs. Idempotent.

    NO native FK clause: a LAZY FK is a DECLARED pattern in this repo (95 of the
    tables), because activation order is `activation_gate`'s decision and a hard FK
    would make the registers impossible to seed in isolation.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS role_registry ("
        "  role_key TEXT PRIMARY KEY,"
        "  definition TEXT NOT NULL,"
        "  may_read INTEGER NOT NULL DEFAULT 0,"
        "  may_write INTEGER NOT NULL DEFAULT 0,"
        "  may_verify INTEGER NOT NULL DEFAULT 0,"
        "  instrument TEXT NOT NULL DEFAULT 'NA',"
        "  because TEXT NOT NULL DEFAULT 'NA',"
        "  cite_ref TEXT NOT NULL,"
        "  is_active INTEGER NOT NULL DEFAULT 1)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS role_right_registry ("
        "  role_key TEXT NOT NULL,"
        "  right_key TEXT NOT NULL,"
        "  value INTEGER NOT NULL,"
        "  meaning TEXT NOT NULL DEFAULT 'NA',"
        "  why TEXT NOT NULL DEFAULT 'NA',"
        "  cite_ref TEXT NOT NULL,"
        "  is_active INTEGER NOT NULL DEFAULT 1,"
        "  UNIQUE (role_key, right_key))")
    conn.commit()
    return {"ok": True, "tables": ["role_registry", "role_right_registry"]}


def seed_roles(conn: sqlite3.Connection, *, cite_ref: str,
               commit: bool = True) -> dict[str, Any]:
    """Register the three roles and their rights, ALL cited.

    REFUSES an uncited seed: a role with no citation is a word nobody can check —
    the same rule every register in this repo follows.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "reason": "UNCITED_SEED",
                "why": "a role with no citation is a word nobody can check"}
    ensure_schema(conn)
    created: list[str] = []
    for role_key, spec in ROLES.items():
        exists = conn.execute("SELECT 1 FROM role_registry WHERE role_key = ?",
                              (role_key,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO role_registry (role_key, definition, may_read, "
                "may_write, may_verify, instrument, because, cite_ref, is_active) "
                "VALUES (?,?,?,?,?,?,?,?,1)",
                (role_key, spec["definition"], int(spec["may_read"]),
                 int(spec["may_write"]), int(spec["may_verify"]),
                 INSTRUMENT.get(role_key, "NA"), spec["because"], str(cite_ref)))
            created.append(role_key)
        for right, meaning in RIGHTS.items():
            value = int(spec.get("may_%s" % right, 0))
            row = conn.execute(
                "SELECT 1 FROM role_right_registry WHERE role_key = ? AND "
                "right_key = ?", (role_key, right)).fetchone()
            if row:
                continue
            conn.execute(
                "INSERT INTO role_right_registry (role_key, right_key, value, "
                "meaning, why, cite_ref, is_active) VALUES (?,?,?,?,?,?,1)",
                (role_key, right, value, meaning,
                 "the role's %s right, from its definition" % right,
                 str(cite_ref)))
    if commit:
        conn.commit()
    return {"ok": True, "created": created, "roles": sorted(ROLES),
            "cite_ref": str(cite_ref).strip()}


def roles_declared(conn: sqlite3.Connection) -> dict[str, Any]:
    """The declared vocabulary, with every right, READ FROM THE DB."""
    if not _table_exists(conn, "role_registry"):
        return {"ok": False, "reason": "NO_role_registry",
                "declared_in_code": sorted(ROLES)}
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM role_registry WHERE is_active = 1 ORDER BY role_key")]
    rights: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT role_key, right_key, value FROM "
                          "role_right_registry WHERE is_active = 1"):
        rights.setdefault(str(r["role_key"]), {})[str(r["right_key"])] = int(r["value"])
    for r in rows:
        r["rights"] = rights.get(str(r["role_key"]), {})
    return {"ok": bool(rows), "roles": rows, "count": len(rows),
            "rights_vocabulary": sorted(RIGHTS),
            "would_be_red_if": "a role is used that is not declared here"}


def role_authority(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """THE MEASUREMENT: what a worker MAY do, and what is MISSING.

    A worker whose `role_id` is NULL — or whose role is not in the vocabulary — is
    reported `NO_ROLE` with `may_write=None`. It is NEVER reported as unrestricted:
    an unknown read as "allowed" is how a gate stops gating.
    """
    try:
        w = conn.execute(
            "SELECT worker_id, worker_key, role_id, is_active FROM worker_registry "
            "WHERE worker_key = ?", (str(worker_key),)).fetchone()
    except sqlite3.OperationalError as e:
        return {"ok": False, "reason": "NO_WORKER_registry", "why": str(e)}
    if w is None:
        return {"ok": False, "reason": "NO_SUCH_WORKER", "worker_key": str(worker_key)}
    role_id = w["role_id"]
    role_key: str | None = None
    if role_id is not None:
        r = conn.execute("SELECT role_key FROM role_registry WHERE role_key = ?",
                         (str(role_id),)).fetchone()
        if r:
            role_key = str(r["role_key"])
    out: dict[str, Any] = {
        "ok": True, "worker_key": str(w["worker_key"]),
        "worker_id": int(w["worker_id"]),
        "role_id": (str(role_id) if role_id is not None else None),
        "role": role_key,
        "declared": role_key is not None,
        "may_read": None, "may_write": None, "may_verify": None,
        "instrument": None, "missing_rights": [],
    }
    if role_key is None:
        out["verdict"] = "NO_ROLE"
        out["why"] = ("the worker declares no role, so its rights are UNKNOWN, not "
                      "unrestricted — assign one with a citation")
        return out
    rr = {str(x["right_key"]): int(x["value"]) for x in conn.execute(
        "SELECT right_key, value FROM role_right_registry WHERE role_key = ? "
        "AND is_active = 1", (role_key,))}
    out["may_read"] = rr.get("read")
    out["may_write"] = rr.get("write")
    out["may_verify"] = rr.get("verify")
    out["rights"] = rr
    row = conn.execute("SELECT instrument FROM role_registry WHERE role_key = ?",
                       (role_key,)).fetchone()
    out["instrument"] = str(row["instrument"]) if row else None
    out["missing_rights"] = [k for k in RIGHTS if k not in rr]
    out["verdict"] = "DECLARED" if not out["missing_rights"] else "INCOMPLETE"
    return out


def declare_role(conn: sqlite3.Connection, worker_key: str, role_key: str, *,
                 cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """THE ONE WRITE PATH: assign a DECLARED role to a worker, with a citation.

    REFUSES:
      * `UNCITED_ASSIGNMENT` — assigning a role is a claim about authority; an
                                uncited claim is exactly what a role should prevent.
      * `UNDECLARED_ROLE`     — the role is not in the vocabulary, so its rights are
                                UNKNOWN. A typo must not mint a phantom role.
      * `NO_SUCH_WORKER`      — the worker does not exist.
      * `NO_CHANGE`           — reported, not a silent success.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "reason": "UNCITED_ASSIGNMENT",
                "why": "a role assignment is a claim about authority; cite it"}
    if role_key not in {str(r["role_key"]) for r in conn.execute(
            "SELECT role_key FROM role_registry WHERE is_active = 1")}:
        return {"ok": False, "reason": "UNDECLARED_ROLE", "role_key": str(role_key),
                "declared": sorted(ROLES),
                "why": "an undeclared role has UNKNOWN rights; declare it first"}
    w = conn.execute("SELECT worker_id, role_id FROM worker_registry WHERE "
                     "worker_key = ?", (str(worker_key),)).fetchone()
    if w is None:
        return {"ok": False, "reason": "NO_SUCH_WORKER", "worker_key": str(worker_key)}
    was = w["role_id"]
    if (str(was) if was is not None else None) == str(role_key):
        return {"ok": True, "changed": False, "worker_key": str(worker_key),
                "role": str(role_key), "why": "already at the requested role"}
    conn.execute("UPDATE worker_registry SET role_id = ? WHERE worker_key = ?",
                 (str(role_key), str(worker_key)))
    if commit:
        conn.commit()
    return {"ok": True, "changed": True, "worker_key": str(worker_key),
            "was": (str(was) if was is not None else None), "role": str(role_key),
            "cite_ref": str(cite_ref).strip()}


def gap(conn: sqlite3.Connection) -> dict[str, Any]:
    """THE NUMBERS. The user's 'standardize to MEASURE it' — a count, not a claim."""
    try:
        workers = [dict(r) for r in conn.execute(
            "SELECT worker_id, worker_key, role_id FROM worker_registry")]
    except sqlite3.OperationalError as e:
        return {"ok": False, "reason": "NO_WORKER_registry", "why": str(e)}
    declared = {str(r["role_key"]) for r in conn.execute(
        "SELECT role_key FROM role_registry WHERE is_active = 1")} \
        if _table_exists(conn, "role_registry") else set()
    with_role, without, undeclared = [], [], []
    for w in workers:
        rid = w["role_id"]
        if rid is None:
            without.append(w["worker_key"])
        elif str(rid) in declared:
            with_role.append({"worker": w["worker_key"], "role": str(rid)})
        else:
            undeclared.append({"worker": w["worker_key"], "role_id": str(rid)})
    # The rights the WRITER standard is checked by, so the link is DECLARED here.
    import artifact_format as af
    audit = af.audit()
    rg = af.rights_gap(conn)
    return {"ok": True,
            "workers": len(workers),
            "with_role": with_role,
            "without_role": without,
            "undeclared_role_id": undeclared,
            "declared_roles": sorted(declared),
            "workforce_pct_assigned": (round(100.0 * len(with_role) / len(workers), 1)
                                       if workers else 0.0),
            "role_rights_vocabulary": sorted(RIGHTS),
            "mode_rights_vocabulary": sorted(rg.get("rights_vocabulary") or []),
            "the_two_axes_are_separate": {
                "mode_axis": "mode_right_registry (ask/plan/agent) — UNCHANGED",
                "worker_axis": "role_right_registry (this module) — read/write/verify",
                "why": ("a mode is not a worker, so a MODE right cannot answer '"
                        "may THIS WORKER write'"),
            },
            "writer_standard": {
                "instrument": "artifact_format.py",
                "artifacts": audit["artifacts"],
                "conformant": audit["conformant"],
                "per_kind": {k: "%d/%d" % (v["conformant"], v["artifacts"])
                             for k, v in sorted(audit["per_kind"].items())},
            },
            "researcher_standard": {
                "instrument": "a proof or a second measurement",
                "why": ("a researcher produces no artifact, so there is nothing for "
                        "a format standard to check; only an independent "
                        "measurement can"),
            },
            "would_be_red_if": "a worker has no role, so its rights are unknown"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--roles", action="store_true",
                    help="the declared role vocabulary and its rights")
    ap.add_argument("--gap", action="store_true",
                    help="how many workers have a role, and what is still unknown")
    ap.add_argument("--authority", metavar="WORKER_KEY", default=None,
                    help="what one worker may do, and what is missing")
    ap.add_argument("--seed", action="store_true",
                    help="register the declared roles (idempotent)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.seed:
            out = seed_roles(conn, cite_ref="role_right_registry.py:1")
        elif a.gap:
            out = gap(conn)
        elif a.authority:
            out = role_authority(conn, a.authority)
        else:
            out = roles_declared(conn)
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    if a.seed:
        print("created roles: %s" % (out.get("created") or "none (already present)"))
        print("declared     : %s" % ", ".join(out.get("roles") or []))
        print("cite_ref     : %s" % out.get("cite_ref"))
        return 0

    if a.gap:
        print("workers            : %d" % out["workers"])
        print("WITH a role        : %d (%s%%)"
              % (len(out["with_role"]), out["workforce_pct_assigned"]))
        print("without a role      : %d %s"
              % (len(out["without_role"]), out["without_role"]))
        print("role_id not declared: %s" % (out["undeclared_role_id"] or "none"))
        print("declared roles      : %s" % ", ".join(out["declared_roles"]))
        print("worker-axis rights  : %s"
              % ", ".join(out["role_rights_vocabulary"]))
        print("mode-axis rights    : %s (UNCHANGED)"
              % ", ".join(out["mode_rights_vocabulary"]))
        print()
        print("writer standard     : %s -> %d of %d artifacts conform"
              % (out["writer_standard"]["instrument"],
                 out["writer_standard"]["conformant"],
                 out["writer_standard"]["artifacts"]))
        for k, v in out["writer_standard"]["per_kind"].items():
            print("      %-10s %s" % (k, v))
        print("researcher standard : %s"
              % out["researcher_standard"]["instrument"])
    elif a.authority:
        print("worker    : %s" % out.get("worker_key"))
        print("role      : %s" % (out.get("role") or "(NONE)"))
        print("verdict   : %s" % out.get("verdict"))
        print("may_read=%s may_write=%s may_verify=%s"
              % (out.get("may_read"), out.get("may_write"), out.get("may_verify")))
        print("instrument: %s" % out.get("instrument"))
        if out.get("why"):
            print("why       : %s" % out["why"])
        if out.get("missing_rights"):
            print("MISSING rights: %s" % out["missing_rights"])
    else:
        if not out.get("ok"):
            print("REFUSED: %s" % out.get("reason"))
            return 0
        print("declared roles: %d" % out["count"])
        for r in out["roles"]:
            print("   %-12s read=%s write=%s verify=%s"
                  % (r["role_key"], r["may_read"], r["may_write"], r["may_verify"]))
            print("        %s" % r["definition"])
            print("        instrument: %s" % r["instrument"])
        print()
        print("rights vocabulary: %s" % ", ".join(out["rights_vocabulary"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
