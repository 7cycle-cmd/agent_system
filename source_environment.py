"""source_environment.py -- the BRIDGE from a `source` row to an ENVIRONMENT.

THE USER (2026-09-25, verbatim):

    "where is enviornment for role = researcher
     enviornment : 豆包 Browser > 工作伙伴 > 软件研发小组 > 任務
     -> identity / worker = LLM : 豆包 , local = 0

     where is enviornment for role = Verfiter
     enviornment : Google Chrome > https://chat.deepseek.com/a/chat/s/7e589851-...
     -> identity / worker = LLM : DeepSeek, local = 0

     where is enviornment for role = researcher
     enviornment : Microsoft Egde > [IT Project Management Alignment - Google
                   Gemini](https://gemini.google.com/app/d299b973911129cd)
     -> identity / worker = LLM : Gemini, local = 0"

MEASURED, AND THE ANSWER TO "where is it": the three environments ALREADY
EXIST -- in `source`, not in `working_environment`:

    id 2  doubao          豆包             APP      url NULL
    id 3  chrome_deepseek Google Chrome    BROWSER  url https://chat.deepseek.com/...
    id 4  edge_gemini     Microsoft Edge   BROWSER  url https://gemini.google.com/app

So the data was never missing. What was missing is a JOIN: `source` has no
`environment_id`, `working_environment` has no `source_key`, and
`source.app_id` is NULL for all 4 rows even though `app` holds `chrome`
(`app_id=3`, kind `browser`). The step-1 page reads `working_environment`, so
it could not see any of them.

WHY A BRIDGE AND NOT A COPY
---------------------------
The `source` row is the OBSERVATION (a page was seen, with a URL and a
hotkey). The `working_environment` row is the DECLARED PATH (kind > product >
surface). They are two different facts about the same place, so this module
DERIVES the second from the first and CITES it -- it does not re-type the URL
by hand, because a re-typed URL is a second copy that drifts.

A REFUSAL IS RAISED, NOT RETURNED
---------------------------------
A caller that ignores a soft failure would believe the environment exists when
it does not. `list_bridges()` never raises, because a status read that can
raise turns a page into an outage.
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

SOURCE_TABLE = "source"
SOURCE = "source_environment:source x working_environment"


class BridgeRefused(Exception):
    """Raised when a bridge would name a source the system does not know."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("source_environment refused — not written: %s"
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


def sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The source VOCABULARY, READ from `source`. Never typed here."""
    _as_rows(conn)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, source_key, name, kind, url, hotkey, source_kind, "
            "app_id FROM %s WHERE is_active=1 ORDER BY id" % SOURCE_TABLE)]
    except sqlite3.OperationalError:
        return []


def ensure_channel_for_source(conn: sqlite3.Connection, *, source_key: str,
                              commit: bool = True) -> dict[str, Any]:
    """DECLARE the channel a source is observed on, if it is not declared.

    MEASURED BLOCKER (2026-09-25): the bridge could not declare ANY of the
    user's three environments, because `channel_registry` has no `doubao`, no
    `chrome` and no `edge`, and `source.app_id` is NULL for all 4 rows so the
    app join cannot answer either. `working_environment.channel_id` is NOT
    NULL and is a FOREIGN KEY, so an environment cannot be declared without a
    channel.

    THE RULE IS THE REPO'S OWN, not a new one. `_migrate_worker_environment_
    channels.py:114` already declares a channel from an OBSERVATION:

        "DECLARED because it was OBSERVED in identity_registry.channel but was
         missing from this register; measured: ... observed N time(s)"

    A `source` row IS an observation -- a place was seen, with a URL and a
    hotkey. So the channel is DECLARED from it, and the source row is the
    citation. This is not an invention: the evidence is the row itself.

    A SOFT-DELETED channel is REVIVED, not duplicated (`channel_key` is
    UNIQUE, and the repo's law is SOFT DELETE ONLY, so the row is the same row
    coming back).
    """
    _as_rows(conn)
    sk = str(source_key or "").strip()
    if not sk:
        raise BridgeRefused(["source_key is required"])
    row = conn.execute(
        "SELECT id, source_key, name, kind, url, source_kind FROM %s "
        "WHERE source_key=? AND is_active=1" % SOURCE_TABLE, (sk,)).fetchone()
    if row is None:
        known = [s["source_key"] for s in sources(conn)]
        raise BridgeRefused(["source_key %r is not in `%s` (known: %s)"
                             % (sk, SOURCE_TABLE, known)])
    # The channel_key IS the source_key: it is already UNIQUE in `source`, so
    # re-deriving a second key would be a second name for one thing.
    key = sk
    name = str(row["name"] or sk)
    cite = ("measured: source.source_key=%r observed (name=%r, kind=%r, "
            "url=%r)" % (sk, name, row["kind"], row["url"]))
    existing = conn.execute(
        "SELECT channel_id, is_active FROM channel_registry WHERE "
        "channel_key=?", (key,)).fetchone()
    if existing and int(existing["is_active"]) == 1:
        return {"ok": True, "action": "already_declared", "channel_key": key,
                "channel_id": int(existing["channel_id"])}
    if existing:
        if commit:
            conn.execute(
                "UPDATE channel_registry SET is_active=1, updated_at="
                "datetime('now') WHERE channel_id=?",
                (int(existing["channel_id"]),))
            conn.commit()
        return {"ok": True, "action": "revived", "channel_key": key,
                "channel_id": int(existing["channel_id"]), "cite_ref": cite}
    if not commit:
        return {"ok": True, "action": "would_create", "channel_key": key,
                "cite_ref": cite}
    cur = conn.execute(
        "INSERT INTO channel_registry (channel_key, name, description, "
        "is_active, version) VALUES (?, ?, ?, 1, '1')",
        (key, name,
         "DECLARED because it was OBSERVED in `source` but was missing from "
         "this register; %s" % cite))
    conn.commit()
    return {"ok": True, "action": "created", "channel_key": key,
            "channel_id": int(cur.lastrowid), "cite_ref": cite}


def declare_from_source(conn: sqlite3.Connection, *, source_key: str,
                        kind: str, product: str, surface: str = "chat",
                        nav_path: str = "", cite_ref: str = "",
                        commit: bool = True) -> dict[str, Any]:
    """Declare ONE environment FROM a `source` row.

    The `url` is TAKEN FROM the source row, never passed in: the source is the
    observation, so re-typing its URL would create a second copy that drifts.

    REFUSES (raises `BridgeRefused`):
      * an unknown `source_key` (the vocabulary is `source`)
      * a missing `kind` / `product` (the path has three parts)
      * a missing `cite_ref` (no citation, no environment)
    """
    _as_rows(conn)
    reasons: list[str] = []
    sk = str(source_key or "").strip()
    kd = str(kind or "").strip()
    pd = str(product or "").strip()
    if not sk:
        reasons.append("source_key is required")
    if not kd:
        reasons.append("kind is required (the path is kind > product > surface)")
    if not pd:
        reasons.append("product is required (the path is kind > product > surface)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no environment)")
    row = None
    if sk:
        row = conn.execute(
            "SELECT id, source_key, name, kind, url, source_kind FROM %s "
            "WHERE source_key=? AND is_active=1" % SOURCE_TABLE,
            (sk,)).fetchone()
        if row is None:
            known = [s["source_key"] for s in sources(conn)]
            reasons.append("source_key %r is not in `%s` (known: %s)"
                           % (sk, SOURCE_TABLE, known))
    if reasons:
        raise BridgeRefused(reasons)

    # NO CHANNEL (2026-09-25). MEASURED: this used to DECLARE a channel from
    # the source observation, because `working_environment` required a
    # `channel_id`. The user: "環境 is 環境!!!! not related to channel". The
    # environment is now declared on its OWN three parts, so the channel step
    # is GONE -- and with it the 8 fake channels it created.
    import working_environment as we

    cite = str(cite_ref).strip()
    out = we.declare(conn, kind=kd, product=pd,
                     surface=surface, url=str(row["url"] or ""),
                     nav_path=nav_path, cite_ref=cite, commit=commit)
    out["source_key"] = sk
    out["source_url"] = row["url"]
    return out


def _channel_for_source(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    """The channel a source belongs to. READ, never guessed.

    MEASURED: `source.app_id` is NULL for all 4 rows, so the app join cannot
    answer. The fallback matches the source's `name` against
    `channel_registry.name` -- and a source that matches NOTHING returns "",
    so the caller REFUSES instead of inventing a channel.
    """
    _as_rows(conn)
    app_id = None
    try:
        app_id = row["app_id"]
    except (IndexError, KeyError):
        app_id = None
    if app_id is not None:
        r = conn.execute(
            "SELECT cr.channel_key FROM app a JOIN channel_registry cr "
            "ON cr.channel_key = a.app_key WHERE a.app_id=? AND "
            "cr.is_active=1", (int(app_id),)).fetchone()
        if r is not None:
            return str(r["channel_key"])
    name = str(row["name"] or "").strip()
    if name:
        r = conn.execute(
            "SELECT channel_key FROM channel_registry WHERE is_active=1 "
            "AND (name=? OR name LIKE ?) ORDER BY channel_id LIMIT 1",
            (name, name + " (%")).fetchone()
        if r is not None:
            return str(r["channel_key"])
    return ""


def list_bridges(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which sources HAVE an environment, and which do not. Never raises.

    THE JOIN IS ON `url`, AND A NULL URL IS HANDLED EXPLICITLY.
    MEASURED BUG (2026-09-25): the first version joined `w.url = s.url`, and
    reported `bridged=2` when THREE environments had just been declared --
    because `doubao`'s url is NULL and in SQL `NULL = NULL` is NULL, not true.
    A join on a NULLABLE column silently drops every row that has no value
    there, which is exactly the row the user asked about.

    NO CHANNEL (2026-09-25): the join used to go through `channel_registry`,
    because the environment carried a `channel_id`. The user: "環境 is
    環境!!!! not related to channel". The environment is matched by its OWN
    `url` now.
    """
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT s.id, s.source_key, s.name, s.kind, s.url, s.hotkey, "
            "w.environment_id, w.display, w.nav_path, w.url AS env_url, "
            "w.is_active AS env_active "
            "FROM %s s LEFT JOIN working_environment w "
            "ON w.is_active=1 AND (w.url = s.url OR (w.url IS NULL AND "
            "s.url IS NULL)) "
            "WHERE s.is_active=1 ORDER BY s.id" % SOURCE_TABLE)]
    except Exception as exc:
        return {"ok": False, "rows": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    bridged = [r for r in rows if r["environment_id"] is not None]
    return {"ok": True, "rows": rows, "count": len(rows),
            "bridged": len(bridged),
            "unbridged": [r["source_key"] for r in rows
                          if r["environment_id"] is None],
            "source": SOURCE}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--declare" in args:
            i = args.index("--declare")
            out = declare_from_source(
                conn, source_key=args[i + 1], kind=args[i + 2],
                product=args[i + 3],
                nav_path=args[i + 4] if len(args) > i + 4 else "",
                cite_ref="cli:source_environment")
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(list_bridges(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
