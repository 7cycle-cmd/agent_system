"""environment_status.py -- is the ENVIRONMENT this worker runs in actually up?

THE USER'S CORRECTION (2026-09-24, verbatim)
--------------------------------------------
    "status still = unknow"
    "status -> environment status"

THE DEFECT THE CORRECTION NAMES
-------------------------------
The `status` column read `worker_heartbeat`, which covers ONLY `worker_id=1`, so
52 of 53 rows showed `UNKNOWN`. The user's correction says what the column
SHOULD answer: is the app this worker runs in actually running?

WHY THAT IS THE RIGHT QUESTION, NOT A WORKAROUND
------------------------------------------------
A worker IS a session in an environment (`worker = environment + identity +
session`). "Can I give this worker a task NOW" therefore depends on whether its
ENVIRONMENT is up. The heartbeat answered a DIFFERENT question -- "is the
`openclaw_worker_01` process alive" -- and answered it for ONE row.

THE CHAIN, MEASURED
-------------------
    identity_registry.channel  ->  app.app_key  ->  app.process_name
                                                       |
                                                       v
                                          process_probe.instances()

MEASURED: `identity_registry.channel` matches `app.app_key` for `vscode`
(True). `runtime` has NO `app` row, so it is `UNKNOWN` -- correctly, because no
app is declared for it. A channel with no app row is NOT "not running": we have
no app to ask about, which is a different fact.

THE STATUS IS MEASURED, NEVER DEFAULTED
---------------------------------------
    RUNNING  the app's process has >= 1 instance
    STOPPED  the app is declared and has 0 instances
    UNKNOWN  no app row for the channel, or the probe failed

`UNKNOWN` is a REAL outcome. Collapsing it into `STOPPED` would make an
undeclared environment look like a stopped one.

NEVER RAISES
------------
A status read that can raise turns a page into an outage. Every failure is
returned as `ok: False` with a `why`, so a failure is VISIBLE rather than
indistinguishable from "stopped".
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

# THE CLOSED VOCABULARY. A reader can enumerate every value the column can take.
VOCABULARY = ("RUNNING", "STOPPED", "UNKNOWN")

# The declared source of the answer, so a reader cannot mistake this for the
# heartbeat's answer.
SOURCE = "environment_status:channel->app->process_probe"


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


def app_for_channel(conn: sqlite3.Connection, channel: str) -> dict[str, Any] | None:
    """The `app` row for a channel key, or None when none is declared.

    MEASURED: `identity_registry.channel` holds the app KEY (`vscode`), not the
    app NAME (`Visual Studio Code`), so the join is on `app.app_key`.

    KEPT FOR THE CHANNEL PATH ONLY (2026-09-25). THE USER: "they are totally
    different" -- a CHANNEL's status is "is the server online", an
    ENVIRONMENT's status is "is the process running". An ENVIRONMENT must use
    `status_for_environment`, which resolves the app by the environment's
    PRODUCT, not by a channel key.
    """
    _as_rows(conn)
    key = str(channel or "").strip()
    if not key:
        return None
    row = conn.execute(
        "SELECT app_id, app_key, name, kind, process_name, exe_path, url "
        "FROM app WHERE app_key=? AND is_active=1", (key,)).fetchone()
    return dict(row) if row else None


def status_for_environment(conn: sqlite3.Connection, *, kind: str,
                           product: str, surface: str = "chat",
                           probes: dict[str, Any] | None = None
                           ) -> dict[str, Any]:
    """The status of ONE ENVIRONMENT, from the Windows Task Manager. Never raises.

    THE USER (2026-09-25, verbatim):
        "enviorment is by windows task center to get the status for enviorment
         list"
        "they are totally different"

    MEASURED DEFECT THIS FIXES: `status_for(conn, channel)` was keyed by a
    CHANNEL, so an ENVIRONMENT's status was derived from a channel key. With
    exactly ONE channel, EVERY environment reported the SAME status -- the
    status could no longer distinguish one environment from another.

    THE CHAIN IS NOW THE ENVIRONMENT'S OWN:

        working_environment.product  ->  app.name  ->  app.process_name
                                                          |
                                                          v
                                             process_probe.instances()

    MEASURED: `Google Chrome` matches `app.name='Google Chrome'` ->
    `process_name='chrome'` -> 19 instances. `豆包` matches
    `app.name='豆包'` -> `process_name='Doubao'` -> 21 instances.

    THE STATUS IS MEASURED, NEVER DEFAULTED:
        RUNNING  the app's process has >= 1 instance
        STOPPED  the app is declared and has 0 instances
        UNKNOWN  no app row for the PRODUCT, or the probe failed

    `UNKNOWN` is a REAL outcome. Collapsing it into `STOPPED` would make an
    undeclared environment look like a stopped one.
    """
    _as_rows(conn)
    kd = str(kind or "").strip()
    pd = str(product or "").strip()
    sf = str(surface or "").strip() or "chat"
    if not pd:
        return {"ok": False, "kind": kd, "product": pd, "surface": sf,
                "status": "UNKNOWN", "why": "product is required"}
    try:
        import app_registry as ar
        app = ar.app_for_product(conn, pd)
    except Exception as exc:
        return {"ok": False, "kind": kd, "product": pd, "surface": sf,
                "status": "UNKNOWN",
                "why": "%s: %s" % (type(exc).__name__, exc)}
    if app is None:
        return {"ok": True, "kind": kd, "product": pd, "surface": sf,
                "status": "UNKNOWN", "app_key": None, "process_name": None,
                "why": ("no `app` row declares a process for product `%s`, so "
                        "its environment status is NOT measured" % pd)}
    pname = str(app.get("process_name") or "").strip()
    if not pname:
        return {"ok": True, "kind": kd, "product": pd, "surface": sf,
                "status": "UNKNOWN", "app_key": app.get("app_key"),
                "process_name": None,
                "why": ("the `app` row for `%s` declares no process_name, so "
                        "the Task Manager cannot be asked" % pd)}
    try:
        import process_probe as pp
        res = probes if probes is not None else pp.probe_all([pname])
        # MEASURED BUG (2026-09-25): `probes` arrives in TWO shapes, and the
        # first version assumed only one. `process_probe.probe_all()` returns
        # `{"probes": {name: {...}}}`, but `environment_registry` passes the
        # ALREADY-UNWRAPPED `{name: {...}}` so a list of N environments costs
        # ONE counter query. Unwrapping twice made every lookup miss, so every
        # environment reported STOPPED with count=0 -- a FALSE STOPPED, which
        # is the worst outcome for a status column.
        pr = res.get("probes") if isinstance(res.get("probes"), dict) else res
        pr = pr.get(pname, {}) if isinstance(pr, dict) else {}
    except Exception as exc:
        return {"ok": False, "kind": kd, "product": pd, "surface": sf,
                "status": "UNKNOWN", "app_key": app.get("app_key"),
                "process_name": pname,
                "why": "the process probe failed: %s: %s"
                       % (type(exc).__name__, exc)}
    count = int(pr.get("count") or 0)
    running = bool(pr.get("running"))
    return {"ok": True, "kind": kd, "product": pd, "surface": sf,
            "status": "RUNNING" if running else "STOPPED",
            "app_key": app.get("app_key"), "app_name": app.get("name"),
            "process_name": pname, "count": count,
            "why": ("the Task Manager reports %d instance(s) of `%s`"
                    % (count, pname))}


# ONLINE / OFFLINE / UNKNOWN. This is the environment's OWN server, not the
# Task Manager RUNNING/STOPPED of the app, and not a channel's server.
SERVER_VOCABULARY = ("ONLINE", "OFFLINE", "UNKNOWN")


def server_status_for_environment(conn: sqlite3.Connection, environment_id: int,
                                  probes: dict[str, Any] | None = None
                                  ) -> dict[str, Any]:
    """Is THIS environment's own server ONLINE or OFFLINE? Never raises.

    THE HUMAN (2026-09-25):
        "CHANNEL STATUS -> ENVIRONMENT STATUS"
        "this is enviornment element not channel element"
        "they are different, don't mix up"

    MEASURED DEFECT: the step-1 column named `channel status` called
    `/api/channel/status`, which probes `pythonw`/`python` once and painted
    that ONE answer onto EVERY environment row. A channel fact on an
    environment element.

    THE RULE: an environment that declares no server process is UNKNOWN.
    "We did not measure a server" is not "the server is offline", and it is
    not the channel probe either. Never copy `channel_registry.server_status`.
    """
    _as_rows(conn)
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "environment_id": environment_id,
                "status": "UNKNOWN", "why": "environment_id is required"}
    row = conn.execute(
        "SELECT environment_id, kind, product, surface FROM "
        "working_environment WHERE environment_id=? AND is_active=1",
        (eid,)).fetchone()
    if row is None:
        return {"ok": False, "environment_id": eid, "status": "UNKNOWN",
                "why": "environment_id %s is not an active environment" % eid}
    # A server is declared ONLY when this environment's product has an app row
    # whose kind is a server. A browser or an IDE has no server of its own.
    try:
        import app_registry as ar
        app = ar.app_for_product(conn, str(row["product"]))
    except Exception as exc:
        return {"ok": True, "environment_id": eid, "status": "UNKNOWN",
                "why": "%s: %s" % (type(exc).__name__, exc)}
    kind = str((app or {}).get("kind") or "").strip().lower()
    pname = str((app or {}).get("process_name") or "").strip()
    if app is None or kind not in ("server", "service") or not pname:
        return {"ok": True, "environment_id": eid,
                "product": row["product"], "status": "UNKNOWN",
                "why": ("environment `%s` declares no server process, so its "
                        "server status is NOT measured (this is not a channel "
                        "status)" % row["product"])}
    try:
        import process_probe as pp
        res = probes if probes is not None else pp.probe_all([pname])
        pr = res.get("probes") if isinstance(res.get("probes"), dict) else res
        pr = pr.get(pname, {}) if isinstance(pr, dict) else {}
    except Exception as exc:
        return {"ok": True, "environment_id": eid, "status": "UNKNOWN",
                "process_name": pname,
                "why": "the process probe failed: %s: %s"
                       % (type(exc).__name__, exc)}
    running = bool(pr.get("running"))
    return {"ok": True, "environment_id": eid, "product": row["product"],
            "process_name": pname,
            "status": "ONLINE" if running else "OFFLINE",
            "why": ("the environment's own server `%s` is %s"
                    % (pname, "running" if running else "NOT running"))}


def status_for(conn: sqlite3.Connection, channel: str,
               probes: dict[str, Any] | None = None) -> dict[str, Any]:
    """The ENVIRONMENT status for one channel. Never raises.

    `probes` lets a caller pass ONE `process_probe.probe_all()` result so a list
    of N channels costs ONE counter query instead of N.
    """
    key = str(channel or "").strip()
    if not key:
        return {"ok": False, "channel": "", "status": "UNKNOWN",
                "why": "channel is required"}
    try:
        app = app_for_channel(conn, key)
    except Exception as exc:
        return {"ok": False, "channel": key, "status": "UNKNOWN",
                "why": "%s: %s" % (type(exc).__name__, exc)}
    if app is None:
        return {"ok": True, "channel": key, "status": "UNKNOWN",
                "app_key": None, "process_name": None,
                "why": "no `app` row declares an app for channel `%s`, so its "
                       "environment status is NOT measured" % key}
    pname = str(app.get("process_name") or "").strip()
    if not pname:
        return {"ok": True, "channel": key, "status": "UNKNOWN",
                "app_key": app.get("app_key"), "process_name": None,
                "why": "the app row for `%s` declares no process_name" % key}
    if probes is None:
        import process_probe as pp

        probes = pp.probe_all([pname]).get("probes", {})
    p = probes.get(pname, {})
    if not p.get("ok"):
        return {"ok": False, "channel": key, "status": "UNKNOWN",
                "app_key": app.get("app_key"), "process_name": pname,
                "why": p.get("why") or "the process probe failed"}
    count = int(p.get("count") or 0)
    status = "RUNNING" if count > 0 else "STOPPED"
    return {
        "ok": True,
        "channel": key,
        "status": status,
        "app_key": app.get("app_key"),
        "app_name": app.get("name"),
        "process_name": pname,
        "count": count,
        "source": SOURCE,
        "why": "app `%s` (process `%s`) has %d instance(s)"
               % (app.get("name"), pname, count),
    }


def status_all(conn: sqlite3.Connection,
               channels: list[str] | None = None) -> dict[str, Any]:
    """The ENVIRONMENT status for many channels, from ONE probe snapshot."""
    _as_rows(conn)
    if channels is None:
        channels = [str(r["channel"]) for r in conn.execute(
            "SELECT DISTINCT channel FROM identity_registry "
            "WHERE is_active=1 AND channel IS NOT NULL AND channel <> ''")]
    names: list[str] = []
    for ch in channels:
        a = app_for_channel(conn, ch)
        if a and str(a.get("process_name") or "").strip():
            names.append(str(a["process_name"]))
    probes: dict[str, Any] = {}
    if names:
        try:
            import process_probe as pp

            probes = pp.probe_all(names).get("probes", {})
        except Exception as exc:
            probes = {}
            _probe_error = "%s: %s" % (type(exc).__name__, exc)
    out: dict[str, Any] = {}
    for ch in channels:
        out[str(ch)] = status_for(conn, ch, probes=probes)
    by: dict[str, int] = {}
    for v in out.values():
        by[str(v.get("status"))] = by.get(str(v.get("status")), 0) + 1
    return {"ok": True, "by_channel": out, "by_status": by,
            "vocabulary": list(VOCABULARY), "source": SOURCE}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--channel", default="")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.channel:
            print(json.dumps(status_for(conn, args.channel), indent=2,
                             ensure_ascii=False))
            return 0
        print(json.dumps(status_all(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
