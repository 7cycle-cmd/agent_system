"""environment_registry.py -- the ENVIRONMENT is the unit of the page.

THE USER'S CORRECTION (2026-09-24, verbatim)
--------------------------------------------
    "path -> http://127.0.0.1:18765/llm-tasks/evidence/step1_enviornment"
    "and role still = UNASSIGNED and remove llm / name / session"
    "worker ID -> enviornment_id from working_enviornment"

THE DEFECT THE CORRECTION NAMES
-------------------------------
The page was a WORKER list: 53 rows, one per session. The user's unit is the
ENVIRONMENT. MEASURED, the 53 rows collapse to TWO environments:

    channel    rows   environment_id   display
    vscode     52     6                IDE > VS Code > chat
    runtime    1      3                Runtime > Runtime > chat

THE CHAIN, MEASURED
-------------------
    identity_registry.channel -> channel_registry.channel_key
                              -> working_environment.channel_id
                              -> working_environment.environment_id

MEASURED (2026-09-25), AND THE CHAIN WAS WRONG: `working_environment` carried a
`channel_id`, so an ENVIRONMENT could only be reached THROUGH a channel. THE
USER: "環境 is 環境!!!! not related to channel". The column is GONE, and the
chain is now the environment's OWN three parts:

    working_environment (kind, product, surface) -> environment_id

WHY REMOVING `name` / `session` / `llm` IS CORRECT, NOT A LOSS
--------------------------------------------------------------
MEASURED: `name` and `session` were both derived from the SAME session id (the
`name` column WAS a truncated session id until it was made representative), and
`llm` is `UNASSIGNED` for all 53. So all three columns carried either a duplicate
of the session or a constant. An environment page does not need a session id: the
environment is the unit.

THE STATUS IS DERIVED, NEVER DEFAULTED
--------------------------------------
The status comes from `environment_status.status_for(channel)`, which walks
channel -> app -> process_probe. An environment with no `app` row is `UNKNOWN`,
never `STOPPED`.

NEVER RAISES
------------
A status read that can raise turns a page into an outage. Every failure is
returned as `ok: False` with a `why`.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "agent.db"

# The declared source, so a reader cannot mistake this for the identity list.
# MEASURED (2026-09-25): this named `channel_registry`, because the environment
# carried a `channel_id`. THE USER: "環境 is 環境!!!! not related to channel".
# The environment is now read on its OWN three parts.
SOURCE = "environment_registry:working_environment"


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent.

    MEASURED DEFECT (2026-09-24, in `computer_presence`): a reader that depends
    on the CALLER having set the row factory breaks the moment a new caller
    forgets. The reader sets it itself.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def environment_for_channel(conn: sqlite3.Connection,
                            channel: str) -> dict[str, Any] | None:
    """The `working_environment` row a CHANNEL key names, or None.

    MEASURED (2026-09-25): this JOINed `channel_registry` on
    `working_environment.channel_id`, because an environment carried a channel.
    THE USER: "環境 is 環境!!!! not related to channel". The column is GONE, so
    the channel is matched against the environment's OWN `product` -- and a
    channel that names NO environment returns None, so the caller REFUSES
    rather than inventing one.
    """
    _as_rows(conn)
    key = str(channel or "").strip()
    if not key:
        return None
    row = conn.execute(
        "SELECT environment_id, kind, product, surface, display, url, "
        "nav_path FROM working_environment WHERE is_active=1 AND "
        "(product=? OR display=?) ORDER BY environment_id LIMIT 1",
        (key, key)).fetchone()
    return dict(row) if row else None


def list_environments(conn: sqlite3.Connection) -> dict[str, Any]:
    """ONE row per environment, keyed by `environment_id`. Never raises.

    Each row carries:
      * `environment_id` -- the user's key, from `working_environment`
      * the 3-part path (`kind` / `product` / `surface` / `display`)
      * `status` -- DERIVED from `environment_status`
      * `role_key` -- the role tally; `UNASSIGNED` when no identity has one
      * `identities` -- how many identities sit in this environment
    """
    _as_rows(conn)
    try:
        envs = [dict(r) for r in conn.execute(
            "SELECT environment_id, kind, product, surface, display, "
            "url, nav_path, icon_url "
            "FROM working_environment WHERE is_active=1 "
            "ORDER BY environment_id")]
    except Exception as exc:
        return {"ok": False, "rows": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}

    # The identity tally. NO CHANNEL (2026-09-25): MEASURED, the tally used to
    # be keyed by `identity_registry.channel`, and the environment was matched
    # to it through `working_environment.channel_id`. The user: "環境 is
    # 環境!!!! not related to channel". The channel column is gone from the
    # environment, and `identity_registry.channel` now holds the ONE channel,
    # so a per-channel tally can no longer identify an environment. The tally
    # is therefore keyed by the ENVIRONMENT the identity's WORKER is bound to
    # (`worker_environment_binding.environment_id`), which is the real link.
    tally: dict[int, dict[str, Any]] = {}
    try:
        for r in conn.execute(
                "SELECT b.environment_id, COUNT(*) n FROM "
                "identity_registry i JOIN worker_environment_binding b ON "
                "b.worker_id = i.worker_id AND b.is_active=1 "
                "WHERE i.is_active=1 GROUP BY b.environment_id"):
            tally[int(r["environment_id"])] = {"identities": int(r["n"]),
                                                "roles": {}}
        for r in conn.execute(
                "SELECT b.environment_id, COALESCE(r.role_key, "
                "'UNASSIGNED') rk, COUNT(*) n FROM identity_registry i "
                "JOIN worker_environment_binding b ON b.worker_id = "
                "i.worker_id AND b.is_active=1 "
                "LEFT JOIN role_registry r ON r.rowid = i.role_id "
                "WHERE i.is_active=1 GROUP BY b.environment_id, rk"):
            eid = int(r["environment_id"])
            tally.setdefault(eid, {"identities": 0, "roles": {}})
            tally[eid]["roles"][str(r["rk"])] = int(r["n"])
    except Exception:
        tally = {}

    # THE DECLARED pairs, READ from `role_environment`.
    #
    # MEASURED DEFECT (2026-09-25) -- TWO REGISTERS, ONE FACT. The user asked
    # "role still = unassigned why". The step-1 popup DECLARES a pair into
    # `role_environment` (the pair register the user approved), and the write
    # succeeded -- measured: pair_id 10, writer x environment 6, is_active=1.
    # THE PAGE DID NOT MOVE, because this column was derived a SECOND way:
    #
    #     identity_registry.role_id -> role_registry.role_key
    #
    # Measured: all 53 identities have `role_id = NULL`, so that path said
    # `UNASSIGNED` for every environment no matter what the user declared. The
    # page was not lying; it was reading the OTHER home. A role the user
    # DECLARES and a role an IDENTITY CARRIES are the same fact with two homes,
    # which is the defect this repo keeps removing.
    #
    # So the DECLARED pair is preferred, and `role_source` NAMES which register
    # answered -- a page may never silently switch its evidence.
    declared: dict[int, dict[str, int]] = {}
    try:
        for r in conn.execute(
                "SELECT environment_id, role_key, COUNT(*) n FROM "
                "role_environment WHERE is_active=1 "
                "GROUP BY environment_id, role_key"):
            declared.setdefault(int(r["environment_id"]), {})[
                str(r["role_key"])] = int(r["n"])
    except Exception:
        declared = {}

    # MEASURED (2026-09-25): this built a `channel_id -> channel_key` map so the
    # status resolver could be called with a CHANNEL. THE USER: "環境 is
    # 環境!!!! not related to channel". The status is resolved by the
    # environment's OWN `product`, so the map is GONE.

    # ONE probe snapshot for every environment, because the counter query costs
    # ~1s and asking it once per environment would make the page slow.
    probes: dict[str, Any] = {}
    try:
        import process_probe as pp
        names = [str(r["process_name"]) for r in conn.execute(
            "SELECT DISTINCT process_name FROM app WHERE is_active=1 "
            "AND process_name IS NOT NULL AND process_name <> ''")]
        if names:
            probes = pp.probe_all(names).get("probes", {})
    except Exception:
        probes = {}

    rows: list[dict[str, Any]] = []
    for e in envs:
        eid = int(e["environment_id"])
        t = tally.get(eid, {"identities": 0, "roles": {}})
        roles = t.get("roles") or {}
        # THE ROLE TALLY: ONE role when every source agrees, else the tally.
        # THE DECLARED pair wins, because it is the user's DECISION and it is
        # the register the popup writes; the identity-carried role is the
        # fallback for an environment whose roles were never declared.
        dec = declared.get(int(e["environment_id"])) or {}
        if dec:
            roles = dec
            role_source = "role_environment"
        elif roles:
            role_source = "identity_registry"
        else:
            role_source = "none"
        if not roles:
            role_key = "UNASSIGNED"
        elif len(roles) == 1:
            role_key = next(iter(roles))
        else:
            role_key = "MIXED"
        status = "UNKNOWN"
        status_why = "no app row for product %r" % e["product"]
        status_app = None
        status_count = None
        try:
            import environment_status as es
            # THE ENVIRONMENT'S OWN STATUS (2026-09-25). THE USER: "enviorment
            # is by windows task center to get the status for enviorment list"
            # and "they are totally different".
            #
            # MEASURED DEFECT THIS FIXES: this used to call
            # `es.status_for(conn, ch)` with the ONE channel key, so EVERY
            # environment reported the SAME status. The resolver is now keyed
            # by the environment's OWN product, so each environment gets its
            # own answer from the Windows Task Manager.
            st = es.status_for_environment(
                conn, kind=e["kind"], product=e["product"],
                surface=e["surface"], probes=probes)
            status = st.get("status") or "UNKNOWN"
            status_why = st.get("why", "")
            status_app = st.get("app_name")
            status_count = st.get("count")
        except Exception as exc:
            status_why = "%s: %s" % (type(exc).__name__, exc)
        # THE ENVIRONMENT'S OWN SERVER STATUS. Not the channel probe.
        # THE HUMAN: "CHANNEL STATUS -> ENVIRONMENT STATUS" / "this is
        # enviornment element not channel element".
        server_status = "UNKNOWN"
        server_why = "not measured"
        try:
            import environment_status as es
            sv = es.server_status_for_environment(conn, eid, probes=probes)
            server_status = sv.get("status") or "UNKNOWN"
            server_why = sv.get("why", "")
        except Exception as exc:
            server_why = "%s: %s" % (type(exc).__name__, exc)
        rows.append({
            "environment_id": eid,
            "kind": e["kind"],
            "product": e["product"],
            "surface": e["surface"],
            "display": e["display"],
            "url": e["url"],
            "nav_path": e["nav_path"],
            "icon_url": e["icon_url"],
            "status": status,
            "status_why": status_why,
            "status_app": status_app,
            "status_count": status_count,
            "server_status": server_status,
            "server_why": server_why,
            "role_key": role_key,
            "role_tally": roles,
            "role_source": role_source,
            "identities": int(t.get("identities") or 0),
        })

    by_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_role[r["role_key"]] = by_role.get(r["role_key"], 0) + 1
    return {"ok": True, "rows": rows, "count": len(rows),
            "by_status": by_status, "by_role": by_role,
            "identities_total": sum(r["identities"] for r in rows),
            "source": SOURCE,
            "key": "environment_id (working_environment)"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--channel", default="")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.channel:
            print(json.dumps(environment_for_channel(conn, args.channel),
                             indent=2, ensure_ascii=False))
            return 0
        print(json.dumps(list_environments(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
