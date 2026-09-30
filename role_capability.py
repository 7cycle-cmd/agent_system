# -*- coding: utf-8 -*-
"""role_capability.py — the MISSING PIECE of `who`: a worker's AUTHORITY.

THE USER (2026-09-24)
---------------------
    "for identity module, should have role premission capabilty, that is the
     missing piecese for 5W1H"

MEASURED, and it is why this file exists — the authority is DANGLING:
    roles                     3 rows, permissions JSON: ['skill:get','tasks:write']
    prefixes used             `skill`, `tasks`
    capability_key namespaces mouse_spot_helper(10) openclaw(12) task_center(4) llm(2)
    -> the prefixes match 0 capability_key
    capability_binding        0 rows

WHY THIS IS THE `who` PIECE AND NOT A 7th DIMENSION
---------------------------------------------------
`skill_5w1h.DIMENSIONS` is six dimensions, ALL `mandatory=1`
(`skill_5w1h.py:51-79`). Adding a 7th would change every generated question
list and break the mandatory-6 contract. And it is not needed: the dimension
`who` today answers

    worker_identity.who = "the session id AND the worker id, both required"

which is *which* session and *which* worker — it does NOT answer *what that
worker is permitted to do*. A worker id with no authority is HALF a `who`. This
file completes `who`; the six dimensions stay six.

REFUSE, DO NOT INVENT
---------------------
A permission is CREATED only when a MODULE already owns the namespace it names.
`tasks` -> `task_center` (module 1, and `dev_task` IS the task table), so
`tasks:read`/`tasks:write` resolve. No module owns `skill`, so `skill:get` /
`skill:create` / `skill:publish` are REFUSED and LISTED — inventing a module for
them would fabricate an owner.

Run:
    .\\.venv\\Scripts\\python.exe role_capability.py --measure
    .\\.venv\\Scripts\\python.exe role_capability.py --apply
"""
from __future__ import annotations

import argparse
import json
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

# A permission is `namespace:verb`. The NAMESPACE decides which module owns the
# capability, and it is mapped ONLY where the module provably owns that work.
# `tasks` -> task_center because `dev_task` (165 rows) IS the task table of
# module 1. `skill` has NO owning module in the 4 REAL modules of
# `module_registry` (task_center, mouse_spot_helper, openclaw_companion,
# llm_runtime), so it is deliberately ABSENT — that is the refusal.
PERMISSION_NAMESPACE_MODULE: dict[str, str] = {
    "tasks": "task_center",
}

# The capability_kind each verb is. MEASURED: the five kinds are
# thinking/eye/hand/voice/code, and the defintion of `hand` is "the provider
# ACTS: it runs a command or drives another application" — a read/write
# permission is the provider ACTING, so `hand` is the single evidence-based
# choice. (A 6th kind is NOT invented; see the proof's MUTATION case.)
PERMISSION_KIND = "hand"

# The worker identity's `who`. The TEXT is completed, never replaced: the
# existing clause stays and the AUTHORITY clause is appended, because shrinking
# a binding would silently drop an answer that was already there.
WHO_AUTHORITY_CLAUSE = (
    " and WHAT IT IS PERMITTED TO DO — the role's capabilities, every one of "
    "which must RESOLVE through `role_capability.permissions_for`"
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


# --------------------------------------------------------------------------
# the permission -> capability link
# --------------------------------------------------------------------------
def split_permission(permission: str) -> tuple[str, str]:
    """`ns:verb` -> (ns, verb). A permission with no ':' has an EMPTY namespace."""
    p = str(permission or "").strip()
    if ":" not in p:
        return "", p
    ns, _, verb = p.partition(":")
    return ns.strip(), verb.strip()


def capability_key_for(permission: str) -> str:
    """The capability_key a permission SHOULD name: `<module_key>.<ns>_<verb>`."""
    ns, verb = split_permission(permission)
    mod = PERMISSION_NAMESPACE_MODULE.get(ns)
    if not mod or not verb:
        return ""
    return "%s.%s_%s" % (mod, ns, verb)


def resolve_permission(conn: sqlite3.Connection, permission: str) -> dict[str, Any]:
    """Does this permission name a capability that EXISTS? REFUSES otherwise.

    TWO resolution paths, both DERIVED and both cited:
      1. the permission IS a capability_key (exact match), or
      2. the permission's NAMESPACE is mapped to a module
         (`PERMISSION_NAMESPACE_MODULE`) and the capability it names exists.

    Path 2 exists because `tasks:write` is a PERMISSION STRING while
    `task_center.tasks_write` is a CAPABILITY KEY — the same authority under two
    names, and the map is the declaration that says so. Treating them as the
    same by exact match alone reported `resolved=0` even AFTER the capability was
    written (caught by this file's own proof, QC-03).
    """
    perm = str(permission or "").strip()
    if not perm:
        return {"ok": False, "code": "EMPTY_PERMISSION", "permission": perm,
                "cite": "measured: an empty permission string"}
    row = conn.execute("SELECT capability_id, capability_key, module_id "
                       "FROM capability_registry WHERE capability_key = ?",
                       (perm,)).fetchone()
    if row:
        return {"ok": True, "permission": perm,
                "capability_id": int(row["capability_id"]),
                "capability_key": str(row["capability_key"]),
                "via": "exact_capability_key",
                "cite": "measured: capability_registry.capability_key = %r" % perm}
    want = capability_key_for(perm)
    if not want:
        ns, _verb = split_permission(perm)
        return {"ok": False, "code": "NO_OWNING_MODULE", "permission": perm,
                "namespace": ns,
                "why": ("no REAL module owns the %r namespace, so the capability "
                        "%r cannot be created without inventing an owner"
                        % (ns, perm)),
                "cite": ("measured: PERMISSION_NAMESPACE_MODULE = %s; the real "
                         "modules are task_center/mouse_spot_helper/"
                         "openclaw_companion/llm_runtime"
                         % sorted(PERMISSION_NAMESPACE_MODULE))}
    if not _table_exists(conn, "capability_registry"):
        return {"ok": False, "code": "NO_CAPABILITY_TABLE", "permission": perm}
    row = conn.execute("SELECT capability_id, capability_key, module_id "
                       "FROM capability_registry WHERE capability_key = ?",
                       (want,)).fetchone()
    if row:
        return {"ok": True, "permission": perm,
                "capability_id": int(row["capability_id"]),
                "capability_key": str(row["capability_key"]),
                "via": "declared_namespace_map",
                "cite": ("measured: permission %r -> capability %r through "
                         "PERMISSION_NAMESPACE_MODULE[%r]=%r"
                         % (perm, want, split_permission(perm)[0],
                            PERMISSION_NAMESPACE_MODULE.get(
                                split_permission(perm)[0])))}
    return {"ok": False, "code": "NOT_REGISTERED_YET", "permission": perm,
            "would_create": want,
            "why": ("the capability %r is not registered yet; `--apply` can "
                    "create it because the module owning %r IS known"
                    % (want, split_permission(perm)[0])),
            "cite": "measured: capability_key %r absent" % want}


def permissions_for(conn: sqlite3.Connection, role_name: str) -> dict[str, Any]:
    """Every capability a role grants, plus every permission REFUSED."""
    row = conn.execute("SELECT role_id, role_name, permissions FROM roles "
                       "WHERE role_name = ?", (str(role_name),)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_ROLE", "role_name": role_name,
                "cite": "measured: no `roles` row named %r" % role_name}
    try:
        perms = json.loads(row["permissions"] or "[]")
    except (ValueError, TypeError):
        perms = []
    resolved: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for p in perms:
        r = resolve_permission(conn, str(p))
        (resolved if r["ok"] else refused).append(r)
    return {"ok": True, "role_id": int(row["role_id"]),
            "role_name": str(row["role_name"]),
            "permissions": [str(p) for p in perms],
            "resolved": resolved, "refused": refused,
            "cite": "measured: roles.role_id=%d permissions=%s"
                    % (int(row["role_id"]), perms)}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(conn, "roles"):
        return {"ok": False, "code": "NO_ROLES_TABLE"}
    roles = [r["role_name"] for r in conn.execute(
        "SELECT role_name FROM roles ORDER BY role_id")]
    report = {n: permissions_for(conn, n) for n in roles}
    refused = [dict(r, role=n) for n, rep in report.items()
               for r in rep.get("refused", [])]
    resolved = [dict(r, role=n) for n, rep in report.items()
                for r in rep.get("resolved", [])]
    return {"ok": True, "roles": report, "resolved_total": len(resolved),
            "refused_total": len(refused),
            "refused": refused, "resolved": resolved,
            "capability_binding_rows": conn.execute(
                "SELECT COUNT(*) FROM capability_binding").fetchone()[0]
            if _table_exists(conn, "capability_binding") else 0,
            "worker_role_column": "role_id" in _columns(conn, "worker_registry")
            if _table_exists(conn, "worker_registry") else False}


# --------------------------------------------------------------------------
# creating the capability that an EXISTING role needed
# --------------------------------------------------------------------------
def create_capability(conn: sqlite3.Connection, capability_key: str, *,
                      cite_ref: str) -> dict[str, Any]:
    """Register the capability a permission names, owned by its REAL module."""
    key = str(capability_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_KEY"}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no capability: %r" % key}
    ex = conn.execute("SELECT capability_id FROM capability_registry "
                      "WHERE capability_key = ?", (key,)).fetchone()
    if ex:
        return {"ok": True, "created": False, "capability_id": int(ex[0]),
                "capability_key": key}
    ns = key.split(".")[0]
    mod = conn.execute("SELECT module_id, module_key FROM module_registry "
                       "WHERE module_key = ?", (ns,)).fetchone()
    if not mod:
        return {"ok": False, "code": "NO_SUCH_MODULE",
                "message": "no module_registry row with module_key=%r" % ns}
    cur = conn.execute(
        "INSERT INTO capability_registry (capability_key, name, description, "
        "module_id, is_active, version, capability_kind, gate_ref, why, "
        "probe_tool) VALUES (?,?,?,?,1,'1',?,'NA',?, 'NA')",
        (key, key.replace("_", " ").title(),
         "A role permission made resolvable: the capability the permission names.",
         int(mod["module_id"]), PERMISSION_KIND,
         "declared by the role permission that named it"))
    conn.commit()
    return {"ok": True, "created": True, "capability_id": int(cur.lastrowid),
            "capability_key": key, "module_id": int(mod["module_id"])}


def ensure_worker_role_column(conn: sqlite3.Connection) -> dict[str, Any]:
    """`worker_registry.role_id` — the link from a WORKER to its AUTHORITY.

    MEASURED: `worker_registry` (13 columns) has NO role column, so a worker
    holds no authority and `who` can never be completed. Nullable, and a LAZY FK
    (no native FK clause) — the DECLARED pattern for 95 of 178 tables.
    """
    if not _table_exists(conn, "worker_registry"):
        return {"ok": False, "code": "NO_WORKER_TABLE"}
    if "role_id" in _columns(conn, "worker_registry"):
        return {"ok": True, "created": False}
    conn.execute("ALTER TABLE worker_registry ADD COLUMN role_id INTEGER")
    conn.commit()
    return {"ok": True, "created": True, "lazy_fk": "roles.role_id"}


def set_worker_role(conn: sqlite3.Connection, worker_key: str, role_name: str, *,
                    cite_ref: str) -> dict[str, Any]:
    """Assign a role to a worker. REFUSES a role that does not resolve."""
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    w = conn.execute("SELECT worker_id FROM worker_registry WHERE worker_key = ?",
                     (str(worker_key),)).fetchone()
    if not w:
        return {"ok": False, "code": "UNKNOWN_WORKER", "worker_key": worker_key}
    r = conn.execute("SELECT role_id FROM roles WHERE role_name = ?",
                     (str(role_name),)).fetchone()
    if not r:
        return {"ok": False, "code": "UNKNOWN_ROLE", "role_name": role_name}
    conn.execute("UPDATE worker_registry SET role_id=?, updated_at=CURRENT_TIMESTAMP "
                 "WHERE worker_id=?", (int(r["role_id"]), int(w["worker_id"])))
    conn.commit()
    return {"ok": True, "worker_id": int(w["worker_id"]),
            "role_id": int(r["role_id"]), "role_name": role_name}


def authority_for(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """The AUTHORITY a worker holds — the third clause of `who`."""
    cols = _columns(conn, "worker_registry") if _table_exists(
        conn, "worker_registry") else set()
    if "role_id" not in cols:
        return {"ok": False, "code": "NO_ROLE_COLUMN",
                "why": "worker_registry has no role_id, so no authority is "
                       "recordable — run `--apply`",
                "cite": "measured: PRAGMA table_info(worker_registry)"}
    row = conn.execute(
        "SELECT w.worker_key, w.role_id, r.role_name, r.permissions "
        "FROM worker_registry w LEFT JOIN roles r ON r.role_id = w.role_id "
        "WHERE w.worker_key = ?", (str(worker_key),)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_WORKER", "worker_key": worker_key}
    if row["role_id"] is None:
        return {"ok": False, "code": "NO_ROLE_RECORDED",
                "worker_key": str(row["worker_key"]),
                "why": ("this worker records NO role, so its authority is "
                        "NOT ANSWERED — no role is assumed"),
                "cite": "measured: worker_registry.role_id IS NULL for %r"
                        % str(row["worker_key"])}
    rep = permissions_for(conn, str(row["role_name"]))
    return {"ok": True, "worker_key": str(row["worker_key"]),
            "role_id": int(row["role_id"]), "role_name": str(row["role_name"]),
            "capabilities": [r["capability_key"] for r in rep["resolved"]],
            "unresolved": [r["permission"] for r in rep["refused"]],
            "cite": "measured: worker_registry.role_id=%d -> roles.role_name=%r"
                    % (int(row["role_id"]), str(row["role_name"]))}


# --------------------------------------------------------------------------
# completing the `who` BINDING (append-only)
# --------------------------------------------------------------------------
def complete_who_binding(conn: sqlite3.Connection, *, cite_ref: str) -> dict[str, Any]:
    """APPEND the AUTHORITY clause to the `who` binding of `worker_identity`.

    APPEND-ONLY, and it REFUSES to shrink: `UNIQUE (subject_kind, dimension_key)`
    means there is ONE `who` cell, so the only honest way to complete it is to
    add the missing clause. A replacement that DROPPED the existing clause would
    silently delete an answer that was already there.
    """
    import dimension_binding_registry as dbr
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    row = conn.execute(
        "SELECT binding_id, binding_text FROM dimension_binding_registry "
        "WHERE subject_kind='worker_identity' AND dimension_key='who'").fetchone()
    if not row:
        return {"ok": False, "code": "NO_WHO_BINDING",
                "cite": "measured: no worker_identity/who row"}
    old = str(row["binding_text"])
    if WHO_AUTHORITY_CLAUSE.strip() in old:
        return {"ok": True, "updated": False, "binding_text": old,
                "binding_id": int(row["binding_id"])}
    new = old + WHO_AUTHORITY_CLAUSE
    if old.strip() not in new:
        return {"ok": False, "code": "WOULD_SHRINK",
                "why": "the new text does not contain the old text"}
    conn.execute("UPDATE dimension_binding_registry SET binding_text=?, "
                 "updated_at=datetime('now') WHERE binding_id=?",
                 (new, int(row["binding_id"])))
    conn.commit()
    return {"ok": True, "updated": True, "binding_id": int(row["binding_id"]),
            "binding_text": new, "previous_text": old}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Resolve every permission that CAN be resolved; report the rest."""
    out: dict[str, Any] = {"created": [], "refused": [], "roles": {}}
    for role in [r["role_name"] for r in conn.execute(
            "SELECT role_name FROM roles ORDER BY role_id")]:
        rep = permissions_for(conn, role)
        for r in rep.get("refused", []):
            want = r.get("would_create")
            if not want:
                out["refused"].append({"role": role, "permission": r["permission"],
                                       "code": r["code"], "why": r.get("why")})
                continue
            cr = create_capability(
                conn, want,
                cite_ref="measured: roles.role_name=%r declares permission %r; "
                         "module %r owns the namespace"
                         % (role, r["permission"], want.split(".")[0]))
            if cr.get("ok"):
                out["created"].append({"role": role, "permission": r["permission"],
                                       "capability_key": cr["capability_key"],
                                       "created": cr["created"]})
            else:
                out["refused"].append({"role": role, "permission": r["permission"],
                                       "code": cr.get("code"), "why": str(cr)})
        out["roles"][role] = permissions_for(conn, role)
    out["worker_role_column"] = ensure_worker_role_column(conn)
    out["who_binding"] = complete_who_binding(
        conn, cite_ref="measured: skill_5w1h.DIMENSIONS mandates six; `who` must "
                       "answer AUTHORITY (the user, 2026-09-24)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        res = apply(conn) if args.apply else measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        if args.apply:
            print("APPLIED: capabilities created/already=%d, refused=%d"
                  % (len(res["created"]), len(res["refused"])))
            for c in res["created"]:
                print("   CREATE %-16s %-28s capability=%s"
                      % (c["role"], c["permission"], c["capability_key"]))
            for r in res["refused"]:
                print("   REFUSE %-16s %-28s %s"
                      % (r["role"], r["permission"], r["code"]))
            print("   worker_registry.role_id: %s" % res["worker_role_column"])
            print("   who binding: %s" % res["who_binding"].get("updated"))
            print()
            for role, rep in res["roles"].items():
                print("   role %-11s resolved=%d refused=%d"
                      % (role, len(rep["resolved"]), len(rep["refused"])))
            return 0
        print("roles=%d  resolved=%d  refused=%d  capability_binding=%d"
              % (len(res["roles"]), res["resolved_total"], res["refused_total"],
                 res["capability_binding_rows"]))
        print("worker_registry.role_id present: %s" % res["worker_role_column"])
        print()
        for name, rep in res["roles"].items():
            print("   role %-11s permissions=%s" % (name, rep["permissions"]))
            for r in rep["resolved"]:
                print("        RESOLVES  %-14s -> %s"
                      % (r["permission"], r["capability_key"]))
            for r in rep["refused"]:
                print("        REFUSED   %-14s %s (%s)"
                      % (r["permission"], r["code"], r.get("why") or r.get("would_create")))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
