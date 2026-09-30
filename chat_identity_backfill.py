# -*- coding: utf-8 -*-
"""chat_identity_backfill.py — make `identity_registry` the ONE identity source.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
    "not by new module to totally replace chat_main?"
    then choosing option (b): "**b** and **totally remove old when new proofed**"

THE INVERSION: `identity_registry` becomes the identity; `chat_main` stops being
one and becomes the OUTPUT FORMAT. That is the user's own framing —
"identity is same for everywhere, different is output format".

WHAT IS DERIVED, AND WHAT IS REFUSED (the whole point)
------------------------------------------------------
MEASURED: `chat_main` itself records NO worker and NO purpose for the 62 rows —
`chat_main.worker_id` is 0/62, `chat_main` has no `why` column, and
`worker_registry`'s original 4 workers match 0 of the provider values.

But a PROVIDER **is** recorded, and it resolves through `app`:

    chat_main.ide = 'VS Code'  (51 rows)  -> app.app_key = 'vscode'

The provider is ALSO recorded in `chat_identity_log.ide` for 9 more chats whose
`chat_main` row records none (the `legacy_migration` rows 1-9). That is a
RECORDED OBSERVATION of the same chat, so it is USED — I first refused those 9,
which was an OVER-REFUSAL of a provider that IS recorded.

A DISAGREEMENT IS NOT A VOTE: if `chat_identity_log` records TWO different
providers for one chat, no provider is derivable and the row is REFUSED
(`AMBIGUOUS_PROVIDER`). Picking the majority would be inventing one.

NOTE on `chat_identity_log.source`: it is the ROW's WRITE PROVENANCE
(`api` / `verify` / `chat_center_backfill`), NOT the chat's purpose, so it is
NEVER used to derive a purpose. Only `chat_main.source` — the chat's own
recorded origin — may. Prose cite rejected earlier; all cites are `measured:`.

The PURPOSE is derived only where the row RECORDS one: `source='chat_center'`
matches the user's phrase "chatting is for purpose -> we need to provide services"
and the existing route `chat_identity.worker_identity_flow`. The
`legacy_migration` (9) and `session_registry` (1) rows record NO purpose, so they
are **REFUSED and REPORTED** — deriving one would be inventing it.

Run:
    .\\.venv\\Scripts\\python.exe chat_identity_backfill.py --measure
    .\\.venv\\Scripts\\python.exe chat_identity_backfill.py            # dry run
    .\\.venv\\Scripts\\python.exe chat_identity_backfill.py --apply
"""
from __future__ import annotations

import argparse
import json
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

# The provider TEXT a chat records -> an `app.app_key`. The mapping is EXPLICIT
# because the chat records a DISPLAY NAME ("VS Code"), not a key, and a fuzzy
# match would let a near-miss pick the wrong provider.
PROVIDER_TEXT_TO_APP: dict[str, str] = {
    "vs code": "vscode",
    "vscode": "vscode",
    "copilot": "vscode",          # the llm column's value; the provider is still VS Code
    "openclaw": "openclaw",
    "chrome": "chrome",
    "ollama": "ollama",
    "doubao": "chrome",           # 豆包 runs in the browser the user named
}

# A chat's recorded `source` -> the PURPOSE it serves. Only a source that RECORDS
# a purpose is here; anything else is refused.
SOURCE_TO_PURPOSE: dict[str, str] = {
    "chat_center": "establish the worker identity for a chat",
}

# `source` values that are PROOF FIXTURES, not real chats. Listed so they are
# REPORTED rather than silently counted as data.
FIXTURE_SOURCES: tuple[str, ...] = ("f1_new_session", "proof_task_binding",
                                    "proof", "demo")


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _app_for(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT app_id, app_key, name, kind FROM app "
                        "WHERE app_key = ?", (key,)).fetchone()


def _channel_key(conn: sqlite3.Connection) -> str:
    """The ONE declared CHANNEL key. READ from `channel_registry`, never typed.

    MEASURED (2026-09-25): the caller passed `str(prov["app_key"])`, so
    `identity_registry.channel` held `vscode` -- an APP KEY, not a channel. THE
    USER: "vscode not channel!!! / channel is local > agent_system / vscode is
    working enviornment". A channel that cannot be read returns `NA`, so the
    caller REFUSES rather than inventing one.
    """
    try:
        import channel_registry as cr
        chans = cr.list_channels(conn).get("channels") or []
        return str(chans[0]["channel_key"]) if chans else "NA"
    except Exception:
        return "NA"


def _observed_provider(conn: sqlite3.Connection, chat_id: int
                       ) -> dict[str, Any] | None:
    """The provider recorded by the OBSERVATION table. None if absent.

    A DISAGREEMENT REFUSES: two recorded providers for one chat means the data
    cannot decide, and a majority vote would invent the answer.
    """
    if not _table_exists(conn, "chat_identity_log"):
        return None
    cols = {c[1] for c in conn.execute("PRAGMA table_info(chat_identity_log)")}
    if "chat_id" not in cols or "ide" not in cols:
        return None
    vals = [r[0] for r in conn.execute(
        "SELECT DISTINCT ide FROM chat_identity_log WHERE chat_id=? AND "
        "ide IS NOT NULL AND TRIM(ide) <> ''", (chat_id,))]
    if not vals:
        return None
    if len(vals) > 1:
        return {"ambiguous": sorted(str(v) for v in vals)}
    return {"text": str(vals[0])}


def _worker_from_app(app: sqlite3.Row, cite: str) -> dict[str, Any]:
    return {"ok": True, "app_key": str(app["app_key"]),
            "worker_key": "PROVIDER-%s" % str(app["app_key"]).upper(),
            "name": "%s (provider)" % app["name"],
            "worker_type": "provider.%s" % str(app["kind"]), "cite": cite}


def derive_worker(conn: sqlite3.Connection, row: dict[str, Any]
                  ) -> dict[str, Any]:
    """The PROVIDER worker for a chat, from a RECORDED fact. REFUSES otherwise."""
    chat_id = int(row["id"])
    ide = str(row.get("ide") or "").strip()
    llm = str(row.get("llm") or "").strip()
    src = str(row.get("source") or "").strip()
    # WHICH recorded field names the provider, in order of specificity.
    for tok in (ide, llm, src):
        key = PROVIDER_TEXT_TO_APP.get(tok.lower())
        if not key:
            continue
        app = _app_for(conn, key)
        if not app:
            # A mapping to an app that does not exist must NOT be used.
            continue
        w = _worker_from_app(app, "measured: chat_main.ide/llm/source = %r -> "
                                  "register:app:%d (%s)"
                                  % (tok, int(app["app_id"]), app["app_key"]))
        w["provider_text"] = tok
        return w
    # FALLBACK: the OBSERVATION table records a provider `chat_main` does not.
    obs = _observed_provider(conn, chat_id)
    if obs and "ambiguous" in obs:
        return {"ok": False, "code": "AMBIGUOUS_PROVIDER",
                "why": ("chat_identity_log records %d DIFFERENT providers for "
                        "chat %d (%s); a disagreement is not a vote, so none is "
                        "derived" % (len(obs["ambiguous"]), chat_id,
                                     ", ".join(obs["ambiguous"]))),
                "cite": "measured: chat_identity_log.chat_id=%d DISTINCT ide" % chat_id}
    if obs and "text" in obs:
        key = PROVIDER_TEXT_TO_APP.get(obs["text"].lower())
        app = _app_for(conn, key) if key else None
        if app:
            w = _worker_from_app(
                app, "measured: chat_main records no provider; "
                     "chat_identity_log.ide = %r for chat_id=%d -> "
                     "register:app:%d (%s)"
                     % (obs["text"], chat_id, int(app["app_id"]), app["app_key"]))
            w["provider_text"] = obs["text"]
            return w
    return {"ok": False, "code": "NO_PROVIDER",
            "why": ("no RECORDED provider (chat_main ide=%r llm=%r source=%r; "
                    "chat_identity_log %s) maps to an `app` row, so a worker "
                    "cannot be derived — inventing one would be a fabricated "
                    "identity" % (ide, llm, src,
                                  "none" if obs is None else repr(obs))),
            "cite": "measured: chat_main id=%s records no ide/llm/source" % chat_id}


def _observed_purpose(conn: sqlite3.Connection, chat_id: int
                      ) -> dict[str, Any] | None:
    """The PURPOSE recorded by the OBSERVATION table (`chat_center_message`).

    A row there is RECORDED EVIDENCE that chat_center served this chat. Two
    DIFFERENT purpose-bearing sources for one chat REFUSE (not a vote).
    """
    if not _table_exists(conn, "chat_center_message"):
        return None
    cols = {c[1] for c in conn.execute("PRAGMA table_info(chat_center_message)")}
    if "chat_id" not in cols or "source" not in cols:
        # MEASURED: chat_center_message has NO `source` column at all (its
        # columns are role/content/catalog_id/skill_id/...), so it records a
        # chat's SERVED CONTENT, never a purpose. Reported, not worked around.
        return {"no_source_column": sorted(cols)}
    rows = [dict(r) for r in conn.execute(
        "SELECT DISTINCT source FROM chat_center_message WHERE chat_id=?", (chat_id,))]
    if not rows:
        return None
    srcs = sorted({str(r["source"] or "").strip() for r in rows})
    purposes = {SOURCE_TO_PURPOSE[s] for s in srcs if s in SOURCE_TO_PURPOSE}
    unrecognised = [s for s in srcs if s and s not in SOURCE_TO_PURPOSE]
    if len(purposes) > 1:
        return {"ambiguous": sorted(purposes)}
    if unrecognised or not purposes:
        return {"unrecognised": srcs}
    return {"purpose": sorted(purposes)[0], "sources": srcs}


def derive_purpose(conn: sqlite3.Connection, row: dict[str, Any]
                   ) -> dict[str, Any]:
    """The PURPOSE a chat serves, ONLY where the data RECORDS one."""
    chat_id = int(row["id"])
    src = str(row.get("source") or "").strip()
    want = SOURCE_TO_PURPOSE.get(src)
    cite = "measured: chat_main.source = %r -> purpose %r" % (src, want)
    if not want:
        # FALLBACK: the OBSERVATION table records a purpose `chat_main` does not
        # (the migrated rows carry `source='legacy_migration'`, a migration
        # marker, while the chat's own messages sit in `chat_center_message`).
        obs = _observed_purpose(conn, chat_id)
        if obs and "ambiguous" in obs:
            return {"ok": False, "code": "AMBIGUOUS_PURPOSE",
                    "why": ("chat_center_message records %d different purposes "
                            "(%s) for chat %d; a disagreement is not a vote"
                            % (len(obs["ambiguous"]), ", ".join(obs["ambiguous"]),
                               chat_id)),
                    "cite": "measured: chat_center_message.chat_id=%d" % chat_id}
        if obs and "purpose" in obs:
            want = obs["purpose"]
            cite = ("measured: chat_main.source = %r records no purpose; "
                    "chat_center_message.source = %s for chat_id=%d -> purpose %r"
                    % (src, obs["sources"], chat_id, want))
        else:
            return {"ok": False, "code": "NO_RECORDED_PURPOSE",
                    "why": ("no purpose is recorded for this chat: "
                            "chat_main.source = %r is a migration/marker value, "
                            "and %s"
                            % (src,
                               "chat_center_message records no purpose at all "
                               "(it has no `source` column, only served content)"
                               if obs and "no_source_column" in obs else
                               "chat_center_message records %s"
                               % ("none" if obs is None
                                  else repr(obs.get("unrecognised"))))),
                    "cite": "measured: chat_main id=%s source=%r" % (chat_id, src)}
    # A purpose is only usable when a ROUTE fulfils it.
    if _table_exists(conn, "purpose_route_registry"):
        r = conn.execute("SELECT route_key FROM purpose_route_registry WHERE "
                         "purpose_key = ? AND is_active = 1", (want,)).fetchone()
        if not r:
            return {"ok": False, "code": "PURPOSE_NOT_ROUTED",
                    "why": "no active route fulfils %r" % want,
                    "cite": "register:purpose_route_registry (none for %r)" % want}
    return {"ok": True, "purpose": want, "cite": cite}


def _chats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "chat_main"):
        return []
    return [dict(r) for r in conn.execute(
        "SELECT id, session_id, ide, llm, source FROM chat_main ORDER BY id")]


def candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Chats that CAN become identities: a derived worker AND a derived purpose."""
    out: list[dict[str, Any]] = []
    for row in _chats(conn):
        w = derive_worker(conn, row)
        p = derive_purpose(conn, row)
        if not (w["ok"] and p["ok"]):
            continue
        out.append({"chat_main_id": int(row["id"]), "session_id": row["session_id"],
                    "provider": w, "purpose": p,
                    "cite": "%s; %s" % (w["cite"], p["cite"])})
    return out


def refused(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Chats that CANNOT become identities, each with the NAMED reason."""
    out: list[dict[str, Any]] = []
    for row in _chats(conn):
        w = derive_worker(conn, row)
        p = derive_purpose(conn, row)
        if w["ok"] and p["ok"]:
            continue
        out.append({"chat_main_id": int(row["id"]),
                    "session_id": row["session_id"],
                    "source": row.get("source"),
                    "code": (w.get("code") if not w["ok"] else p.get("code")),
                    "why": (w.get("why") if not w["ok"] else p.get("why")),
                    "cite": (w.get("cite") if not w["ok"] else p.get("cite"))})
    return out


def referenced_chats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which chat ids are REFERENCED by a live table (so removal must keep them)."""
    refs: dict[str, Any] = {}
    for table, col in (("chat_identity_log", "chat_id"),
                       ("chat_center_message", "chat_id"),
                       ("chat_reply_log", "chat_id")):
        if not _table_exists(conn, table):
            continue
        cols = {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}
        if col not in cols:
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL" % (col, table, col))]
        resolving = [i for i in ids if conn.execute(
            "SELECT 1 FROM chat_main WHERE id = ?", (i,)).fetchone()]
        refs[table] = {"distinct": len(ids), "resolving_against_chat_main":
                       len(resolving), "ids": sorted(map(str, resolving))}
    return refs


def fixture_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """The FIXTURE rows, named — so a fixture is never counted as data."""
    if not _table_exists(conn, "chat_reply_log"):
        return {"ok": True, "rows": 0, "fixtures": 0, "sources": {}}
    srcs = {str(r[0]): int(r[1]) for r in conn.execute(
        "SELECT source, COUNT(*) FROM chat_reply_log GROUP BY source")}
    fixtures = sum(n for s, n in srcs.items() if s in FIXTURE_SOURCES)
    total = sum(srcs.values())
    bad = conn.execute(
        "SELECT COUNT(*) FROM chat_reply_log WHERE chat_id IS NOT NULL "
        "AND LENGTH(chat_id) > 10").fetchone()[0]
    return {"ok": True, "rows": total, "fixtures": fixtures,
            "real_rows": total - fixtures, "sources": srcs,
            "rows_with_non_id_chat_id": bad,
            "verdict": ("REPORTED, not fixed here: %d of %d chat_reply_log rows "
                        "are PROOF FIXTURES (sources %s); the hash-shaped chat_id "
                        "is therefore mostly fixture pollution, NOT a live-data "
                        "blocker" % (fixtures, total, list(FIXTURE_SOURCES)))}


def purpose_sources_census(conn: sqlite3.Connection) -> dict[str, Any]:
    """WHERE a chat's purpose could have been recorded, and whether it was.

    A refusal must NAME the broken hop, so this records that every candidate
    carrier was CHECKED — an absence is a measurement, not an assumption.
    """
    census: dict[str, Any] = {}
    for table, col in (("chat_main", "source"), ("chat_identity_log", "source"),
                       ("chat_center_message", "source"),
                       ("chat_center_message", "event"),
                       ("chat_center_message", "title"),
                       ("chat_center_message", "catalog_name")):
        if not _table_exists(conn, table):
            census["%s.%s" % (table, col)] = "NO_TABLE"
            continue
        cols = {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}
        if col not in cols:
            census["%s.%s" % (table, col)] = "NO_COLUMN"
            continue
        vals = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL" % (col, table, col))}
        census["%s.%s" % (table, col)] = {
            "distinct": len(vals),
            "carries_A_purpose": sorted(v for v in vals
                                        if v.strip() in SOURCE_TO_PURPOSE)}
    census["purpose_carriers_KNOWN"] = sorted(SOURCE_TO_PURPOSE)
    return census


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    cand = candidates(conn)
    ref = refused(conn)
    refs = referenced_chats(conn)
    total = len(_chats(conn))
    return {"chats": total, "candidates": len(cand), "refused": len(ref),
            "candidate_rows": cand[:5], "refused_rows": ref,
            "refused_by_code": {c: sum(1 for r in ref if r["code"] == c)
                                for c in {r["code"] for r in ref}},
            "referenced": refs,
            "fixtures": fixture_report(conn),
            "purpose_sources": purpose_sources_census(conn),
            "identities_now": conn.execute(
                "SELECT COUNT(*) FROM identity_registry").fetchone()[0]
            if _table_exists(conn, "identity_registry") else 0}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the provider worker, open the identity, keep `chat_id`.

    NO SECOND WRITER: `worker_registry.register_worker` and
    `identity_registry.open_identity` are the existing paths.
    """
    import identity_registry as ir
    import worker_registry as wr
    workers: list[dict[str, Any]] = []
    opened = 0
    existed = 0
    linked = 0
    refused_now: list[dict[str, Any]] = []
    seen_workers: dict[str, int] = {}
    for c in candidates(conn):
        prov = c["provider"]
        wkey = prov["worker_key"]
        if wkey not in seen_workers:
            # The provider worker. Idempotent on `worker_key`.
            r = wr.register_worker(
                conn, worker_key=wkey, name=prov["name"],
                worker_type=prov["worker_type"],
                capability_ref="provider.transport",
                physical_path=prov["app_key"], uses_text=prov["cite"],
                cite_ref=prov["cite"])
            if not r.get("ok"):
                refused_now.append({"chat_main_id": c["chat_main_id"],
                                    "code": "WORKER_REFUSED", "why": str(r),
                                    "cite": prov["cite"]})
                continue
            # MEASURED: register_worker returns the row NESTED under "worker",
            # not flat -- reading `r["worker_id"]` gave None for a worker that
            # WAS written (caught by this file's own proof, QC-03).
            wid = (r.get("worker") or {}).get("worker_id")
            if wid is None:
                refused_now.append({"chat_main_id": c["chat_main_id"],
                                    "code": "WORKER_ID_UNREADABLE",
                                    "why": "register_worker ok but no nested "
                                           "worker.worker_id: %r" % (sorted(r),),
                                    "cite": prov["cite"]})
                continue
            seen_workers[wkey] = int(wid)
            workers.append({"worker_key": wkey, "created": r.get("created"),
                            "worker_id": int(wid),
                            "cite": prov["cite"]})
        try:
            res = ir.open_identity(
                conn, session_id=str(c["session_id"]), worker_key=wkey,
                workflow_id=2, channel=_channel_key(conn),
                why=str(c["purpose"]["purpose"]),
                cite_ref="%s; %s" % (c["cite"], c["purpose"]["cite"]))
        except ir.IdentityRefused as exc:
            refused_now.append({"chat_main_id": c["chat_main_id"],
                                "code": "IDENTITY_REFUSED", "why": str(exc),
                                "cite": c["cite"]})
            continue
        if res.get("created"):
            opened += 1
        else:
            existed += 1
        key = res["identity"]["identity_key"]
        # KEEP THE RELATION: the identity points at the EXISTING chat row.
        st = ir.set_chat_id(conn, key, int(c["chat_main_id"]),
                            cite_ref="measured: chat_main.id=%d is the referenced "
                                     "chat row" % c["chat_main_id"])
        if st.get("ok"):
            linked += 1
    return {"ok": True, "workers": workers, "opened": opened, "existed": existed,
            "linked": linked, "refused_now": refused_now,
            "identities_total": conn.execute(
                "SELECT COUNT(*) FROM identity_registry").fetchone()[0]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        res = apply(conn) if args.apply else measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.apply:
            print("APPLIED: opened=%d existed=%d linked=%d workers=%d"
                  % (res["opened"], res["existed"], res["linked"],
                     len(res["workers"])))
            for w in res["workers"]:
                print("   worker %-18s created=%s id=%s"
                      % (w["worker_key"], w["created"], w["worker_id"]))
            for r in res["refused_now"]:
                print("   REFUSED chat=%s %s" % (r["chat_main_id"], r["why"][:70]))
            print("   identities total: %d" % res["identities_total"])
        else:
            print("chats total      : %d" % res["chats"])
            print("candidates       : %d" % res["candidates"])
            print("refused          : %d  %s" % (res["refused"], res["refused_by_code"]))
            print("identities now   : %d" % res["identities_now"])
            print("REFERENCED chats (a removal must keep these):")
            for t, v in res["referenced"].items():
                print("   %-22s distinct=%-4d resolving=%-4d"
                      % (t, v["distinct"], v["resolving_against_chat_main"]))
            print("FIXTURES: %s" % res["fixtures"]["verdict"])
            print()
            print("CANDIDATE sample:")
            for c in res["candidate_rows"]:
                print("   chat=%d worker=%s purpose=%r"
                      % (c["chat_main_id"], c["provider"]["worker_key"],
                         c["purpose"]["purpose"][:44]))
            print("REFUSED (never invented):")
            for r in res["refused_rows"]:
                print("   chat=%-3d %-24s %s" % (r["chat_main_id"], r["code"],
                                                 r["why"][:58]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
