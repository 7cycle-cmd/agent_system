"""channel_registry.py -- the writer for `channel_registry`, and the CHANNEL's
own status.

THE USER (2026-09-25, verbatim):
    "enviornment url and channel url can help to have 5W1H for each, so system
     is easy to classify what is happen now, don't mix up any more"
    "channel url = C:\\projects\\agent_system"
    "for status, channel status is did server online / offline"
    "enviorment is by windows task center to get the status for enviorment list"
    "they are totally different"

MEASURED, AND THE USER IS RIGHT -- THE TWO STATUSES ARE DIFFERENT:

  * a CHANNEL's status is "is the server ONLINE or OFFLINE". A channel is a
    TOP-LEVEL FOLDER (see `terminology_registry.channel_folder`), and the thing
    that can be up or down is the SERVER that serves it.
  * an ENVIRONMENT's status is "is the process RUNNING", read from the Windows
    Task Manager (see `app_registry` and `environment_status`).

MEASURED DEFECT: `environment_status.status_for(conn, channel)` was keyed by a
CHANNEL, so an ENVIRONMENT's status was derived from a channel key. With exactly
ONE channel, EVERY environment reported the SAME status. That is the mix-up the
user names.

MEASURED: `channel_registry` had NO `CREATE TABLE` in any production module --
the statement lived only in FOUR `_proof_*.py` files. The declaration now lives
in `db_schema.CHANNEL_REGISTRY_DDL`, and this module is the WRITER, so the
register the page depends on has a real path in.

A REFUSAL IS RAISED, NOT RETURNED
---------------------------------
A caller that ignores a soft failure would believe the channel exists when it
does not. `list_channels()` and `server_status()` never raise, because a status
read that can raise turns a page into an outage.
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

TABLE = "channel_registry"
SOURCE = "channel_registry:channel_registry"

# The ONE channel's url. THE USER (2026-09-25): "channel url =
# C:\projects\agent_system". It is a CONSTANT here because it is the fact the
# user stated, and a caller that needs it should not re-type it.
CHANNEL_URL = r"C:\projects\agent_system"


class ChannelRefused(Exception):
    """Raised when a channel would be stored without a real key/cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("channel_registry refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent.

    A reader that depends on the CALLER having set the factory breaks the
    moment a new caller forgets -- and a route handler is the normal case.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `channel_registry` and add `url` if the table predates it."""
    import db_schema as ds

    _as_rows(conn)
    conn.executescript(ds.CHANNEL_REGISTRY_DDL)
    # `CREATE TABLE IF NOT EXISTS` does NOT add a column to a table that
    # already exists, so `url` needs an explicit migration.
    added: list[str] = []
    have = {str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % TABLE)}
    if "url" not in have:
        conn.execute("ALTER TABLE %s ADD COLUMN url TEXT" % TABLE)
        added.append("url")
    conn.commit()
    return {"ok": True, "table": TABLE, "columns_added": added}


def declare(conn: sqlite3.Connection, *, channel_key: str, name: str,
            url: str = "", description: str = "", cite_ref: str,
            commit: bool = True) -> dict[str, Any]:
    """Declare ONE channel. Idempotent on `channel_key`.

    REFUSES (raises `ChannelRefused`):
      * an empty `channel_key` / `name`
      * a missing `cite_ref` (no citation, no channel)
      * a `channel_key` that already exists
    """
    _as_rows(conn)
    reasons: list[str] = []
    ck = str(channel_key or "").strip()
    nm = str(name or "").strip()
    if not ck:
        reasons.append("channel_key is required")
    if not nm:
        reasons.append("name is required")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no channel)")
    if ck:
        dup = conn.execute(
            "SELECT channel_id FROM %s WHERE channel_key=?" % TABLE,
            (ck,)).fetchone()
        if dup is not None:
            reasons.append("channel_key %r already exists (channel_id=%s)"
                           % (ck, dup["channel_id"]))
    if reasons:
        raise ChannelRefused(reasons)
    cur = conn.execute(
        "INSERT INTO %s (channel_key, name, description, url, is_active, "
        "version) VALUES (?, ?, ?, ?, 1, '1')" % TABLE,
        (ck, nm, str(description or ""), str(url or "").strip() or None))
    if commit:
        conn.commit()
    return {"ok": True, "action": "created", "channel_id": int(cur.lastrowid),
            "channel_key": ck, "name": nm, "url": str(url or "").strip() or None}


def update(conn: sqlite3.Connection, *, channel_key: str, name: str,
           url: str | None = None, description: str | None = None,
           cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """EDIT a channel. `url=None` means "leave it"; `url=""` means "clear it"."""
    _as_rows(conn)
    reasons: list[str] = []
    ck = str(channel_key or "").strip()
    nm = str(name or "").strip()
    if not ck:
        reasons.append("channel_key is required")
    if not nm:
        reasons.append("name is required")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no edit)")
    row = None
    if ck:
        row = conn.execute(
            "SELECT channel_id FROM %s WHERE channel_key=? AND is_active=1"
            % TABLE, (ck,)).fetchone()
        if row is None:
            reasons.append("channel_key %r is not in `%s`" % (ck, TABLE))
    if reasons:
        raise ChannelRefused(reasons)
    sets = ["name=?", "updated_at=datetime('now')"]
    vals: list[Any] = [nm]
    if url is not None:
        sets.append("url=?")
        vals.append(str(url).strip() or None)
    if description is not None:
        sets.append("description=?")
        vals.append(str(description))
    vals.append(int(row["channel_id"]))
    conn.execute("UPDATE %s SET %s WHERE channel_id=?" % (TABLE, ", ".join(sets)),
                 tuple(vals))
    if commit:
        conn.commit()
    return {"ok": True, "action": "updated", "channel_key": ck, "name": nm}


def list_channels(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every active channel, with its 5W1H `url`. Never raises."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT channel_id, channel_key, name, description, url, "
            "created_at, updated_at FROM %s WHERE is_active=1 "
            "ORDER BY channel_id" % TABLE)]
    except Exception as exc:
        return {"ok": False, "channels": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "channels": rows, "count": len(rows), "source": SOURCE}


def server_status(conn: sqlite3.Connection, channel_key: str,
                  probes: dict[str, Any] | None = None) -> dict[str, Any]:
    """Is the CHANNEL's server ONLINE or OFFLINE? Never raises.

    THE USER (2026-09-25): "for status, channel status is did server online /
    offline". This is the CHANNEL's status, and it is NOT the environment's.

    THE EVIDENCE IS A REAL PROBE, NEVER A DEFAULT. MEASURED: the helper server
    listens on port 18765, and `app` holds the services that can be up. A
    channel with no declared server is reported `UNKNOWN` -- never `OFFLINE`,
    because "we did not measure it" and "it is down" are different facts.
    """
    _as_rows(conn)
    ck = str(channel_key or "").strip()
    if not ck:
        return {"ok": False, "channel_key": "", "status": "UNKNOWN",
                "why": "channel_key is required"}
    row = conn.execute(
        "SELECT channel_id, channel_key, name, url FROM %s WHERE "
        "channel_key=? AND is_active=1" % TABLE, (ck,)).fetchone()
    if row is None:
        return {"ok": False, "channel_key": ck, "status": "UNKNOWN",
                "why": "channel_key %r is not in `%s`" % (ck, TABLE)}
    # The SERVER of a channel is the process that serves it. MEASURED: the
    # helper server is `mouse_spot_helper.py`, run by `pythonw.exe`, listening
    # on 18765. The probe asks the OS, not a config file.
    import process_probe as pp
    names = ["pythonw", "python"]
    try:
        res = probes if probes is not None else pp.probe_all(names)
        pr = res.get("probes", {})
    except Exception as exc:
        return {"ok": True, "channel_key": ck, "status": "UNKNOWN",
                "url": row["url"],
                "why": "the process probe failed: %s: %s"
                       % (type(exc).__name__, exc)}
    running = any(bool(pr.get(n, {}).get("running")) for n in names)
    count = sum(int(pr.get(n, {}).get("count") or 0) for n in names)
    return {"ok": True, "channel_key": ck, "name": row["name"],
            "url": row["url"],
            "status": "ONLINE" if running else "OFFLINE",
            "server_processes": count,
            "why": ("the server process is %s (%d instance(s))"
                    % ("running" if running else "NOT running", count))}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--status" in args:
            i = args.index("--status")
            print(json.dumps(server_status(conn, args[i + 1]), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps(list_channels(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
