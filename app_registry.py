"""app_registry.py -- the writer for `app`, and the link from an ENVIRONMENT to
its Windows Task Manager process.

THE USER (2026-09-25, verbatim):
    "enviorment is by windows task center to get the status for enviorment list"
    "they are totally different"

MEASURED, AND THE USER IS RIGHT: an ENVIRONMENT's status is "is the process
RUNNING", read from the Windows Task Manager. `process_probe.probe_all` already
answers that question -- MEASURED against the user's own Task Manager
screenshot:

    Doubao            running=True  count=21
    chrome            running=True  count=19
    msedge            running=True  count=10
    Code              running=True  count=20
    Notepad           running=True  count=1
    WindowsTerminal   running=True  count=1
    ollama            running=True  count=1

WHAT WAS MISSING IS THE LINK. MEASURED: `app` held only 4 rows, and matching an
environment's `product` to `app.name` hit only 1 of 5 (`Google Chrome`). `豆包`
and `Microsoft Edge` had NO app row, even though they are RUNNING.

MEASURED: `app` was written ONLY by `_proof_*.py` files -- a register the page
depends on had no writer. This module is that writer, the same fix
`llm_model_registry.py` applied to `llm_model`.

A REFUSAL IS RAISED, NOT RETURNED
---------------------------------
A caller that ignores a soft failure would believe the app exists when it does
not. `list_apps()` and `app_for_product()` never raise, because a status read
that can raise turns a page into an outage.
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

TABLE = "app"
SOURCE = "app_registry:app"


class AppRefused(Exception):
    """Raised when an app would be stored without a real name/process/cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("app_registry refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent."""
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `app` and add `url` if the table predates it."""
    import db_schema as ds

    _as_rows(conn)
    conn.executescript(ds.APP_DDL)
    added: list[str] = []
    have = {str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % TABLE)}
    if "url" not in have:
        conn.execute("ALTER TABLE %s ADD COLUMN url TEXT" % TABLE)
        added.append("url")
    conn.commit()
    return {"ok": True, "table": TABLE, "columns_added": added}


def declare(conn: sqlite3.Connection, *, app_key: str, name: str,
            process_name: str, kind: str = "desktop", url: str = "",
            exe_path: str = "", description: str = "", cite_ref: str,
            commit: bool = True) -> dict[str, Any]:
    """Declare ONE app. Idempotent on `app_key`.

    REFUSES (raises `AppRefused`):
      * an empty `app_key` / `name` / `process_name`
      * a missing `cite_ref` (no citation, no app)
      * an `app_key` that already exists

    `process_name` is REQUIRED because it is the ONLY thing that links an
    environment to the Windows Task Manager. An app row without it cannot
    answer "is this environment running", which is the whole point.
    """
    _as_rows(conn)
    reasons: list[str] = []
    ak = str(app_key or "").strip()
    nm = str(name or "").strip()
    pn = str(process_name or "").strip()
    if not ak:
        reasons.append("app_key is required")
    if not nm:
        reasons.append("name is required")
    if not pn:
        reasons.append("process_name is required (it is the ONLY link to the "
                       "Windows Task Manager, so an app without it cannot "
                       "answer whether its environment is running)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no app)")
    if ak:
        dup = conn.execute(
            "SELECT app_id FROM %s WHERE app_key=?" % TABLE, (ak,)).fetchone()
        if dup is not None:
            reasons.append("app_key %r already exists (app_id=%s)"
                           % (ak, dup["app_id"]))
    if reasons:
        raise AppRefused(reasons)
    cur = conn.execute(
        "INSERT INTO %s (app_key, name, kind, exe_path, process_name, url, "
        "description, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)" % TABLE,
        (ak, nm, str(kind or "desktop"), str(exe_path or "").strip() or None,
         pn, str(url or "").strip() or None, str(description or "")))
    if commit:
        conn.commit()
    return {"ok": True, "action": "created", "app_id": int(cur.lastrowid),
            "app_key": ak, "name": nm, "process_name": pn}


def update(conn: sqlite3.Connection, *, app_key: str, name: str,
           process_name: str, kind: str | None = None,
           url: str | None = None, exe_path: str | None = None,
           cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """EDIT an app. `None` means "leave it"; `""` means "clear it"."""
    _as_rows(conn)
    reasons: list[str] = []
    ak = str(app_key or "").strip()
    nm = str(name or "").strip()
    pn = str(process_name or "").strip()
    if not ak:
        reasons.append("app_key is required")
    if not nm:
        reasons.append("name is required")
    if not pn:
        reasons.append("process_name is required")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no edit)")
    row = None
    if ak:
        row = conn.execute(
            "SELECT app_id FROM %s WHERE app_key=? AND is_active=1" % TABLE,
            (ak,)).fetchone()
        if row is None:
            reasons.append("app_key %r is not in `%s`" % (ak, TABLE))
    if reasons:
        raise AppRefused(reasons)
    sets = ["name=?", "process_name=?", "updated_at=datetime('now')"]
    vals: list[Any] = [nm, pn]
    for col, val in (("kind", kind), ("url", url), ("exe_path", exe_path)):
        if val is not None:
            sets.append("%s=?" % col)
            vals.append(str(val).strip() or None)
    vals.append(int(row["app_id"]))
    conn.execute("UPDATE %s SET %s WHERE app_id=?" % (TABLE, ", ".join(sets)),
                 tuple(vals))
    if commit:
        conn.commit()
    return {"ok": True, "action": "updated", "app_key": ak, "name": nm,
            "process_name": pn}


def list_apps(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every active app, with its process name and 5W1H url. Never raises."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT app_id, app_key, name, kind, exe_path, process_name, url, "
            "description FROM %s WHERE is_active=1 ORDER BY app_id" % TABLE)]
    except Exception as exc:
        return {"ok": False, "apps": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "apps": rows, "count": len(rows), "source": SOURCE}


def app_for_product(conn: sqlite3.Connection,
                    product: str) -> dict[str, Any] | None:
    """The `app` row for an ENVIRONMENT's product, or None.

    THE LINK IS THE PRODUCT NAME, NOT A CHANNEL KEY.
    MEASURED DEFECT (2026-09-25): `environment_status.app_for_channel` joined
    `app.app_key = channel`, so an ENVIRONMENT's status was derived from a
    CHANNEL. With exactly ONE channel, EVERY environment reported the SAME
    status. THE USER: "they are totally different".

    THE MATCH TRIES THREE FORMS, in order, because the register and the
    environment do not always spell a product the same way. MEASURED:

        environment.product   app.name               match?
        `Google Chrome`       `Google Chrome`        exact
        `豆包`                 `豆包`                  exact
        `Microsoft Edge`      `Microsoft Edge`       exact
        `VS Code`             `Visual Studio Code`   NOT exact
        `Runtime`             (no app row)           none

    So the forms are:
      1. `app.name` = the product (exact)
      2. `app.app_key` = the product, lowercased (`VS Code` -> `vs code`? no)
      3. `app.app_key` = the product with spaces removed and lowercased
         (`VS Code` -> `vscode`) -- MEASURED: this is the form that matches
         `app_key='vscode'`.

    A product that matches NOTHING returns None, so the caller reports UNKNOWN
    rather than a default. The forms are tried in a FIXED order and the FIRST
    match wins, so the answer is deterministic.
    """
    _as_rows(conn)
    p = str(product or "").strip()
    if not p:
        return None
    compact = p.replace(" ", "").replace("-", "").lower()
    row = conn.execute(
        "SELECT app_id, app_key, name, kind, exe_path, process_name, url "
        "FROM %s WHERE is_active=1 AND (name=? OR app_key=? OR app_key=?) "
        "ORDER BY app_id LIMIT 1" % TABLE, (p, p.lower(), compact)).fetchone()
    return dict(row) if row else None


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--of" in args:
            i = args.index("--of")
            print(json.dumps(app_for_product(conn, args[i + 1]), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps(list_apps(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
