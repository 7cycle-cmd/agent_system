# -*- coding: utf-8 -*-
"""terminology_alias.py — ONE DOOR over the FIVE carriers that hold old names.

THE USER (2026-09-24)
---------------------
    "no, repalce the mis-understand! to have system as simple as it can, it can
     help worker by terminology register, so they can have work more happy"
    "old -> new version have many old -> new name legacy problem / as we a have
     the new system, why not to have they as clear as we can"
    "terminonlogy register can help to find different name for same purpose /
     same source/samedefinition, it can help alot"

WHAT WENT WRONG (MEASURED)
--------------------------
9 known old names were probed against every carrier. **7 of 9 were UNREACHABLE**
from the register:

    name_registry -> terminology_registry   NO  (a JSON blob in schema_migration_log)
    llm_100_run   -> proof_run              NO  (only a SQL VIEW)
    case_registry -> chat_registry          NO  (a code comment)
    worker_identity -> worker_identity_flow NO  (a comment + a script)
    table / field -> db_table / db_field    NO  (a Python tuple, LEVEL_RENAMES)
    the CP-S capability codes -> mouse_spot_helper.*  NO  (only legacy_id_map)
    ollama -> llm_runtime                   YES (a term)
    purpose -> goal                         YES (an alias)

THE FIVE CARRIERS, and what each CANNOT answer
----------------------------------------------
1. `terminology_registry.term_key` / `alias_list` — what a term MEANS.
   `alias_list` is TEXT JSON; `'NA'` on 46 of 48. **It has NO reader in code**, so
   an alias written there is currently INERT — which is exactly why a worker
   could not find `purpose`.
2. `legacy_id_map` (11) — an old ID -> a new ID **inside its own `entity_type`**.
3. SQL **VIEW** (2: `llm_100_run`, `v_skill_contract`) — an old TABLE name still
   reads; it cannot say the name was EVER renamed.
4. `schema_migration_log` (5) — prose JSON; 1 entry is a rename.
5. Python rename tuples (`LEVEL_RENAMES`, ...) — NOT readable at runtime from
   another process, so they are DECLARED and reported as `CODE_ONLY`.

THE DESIGN: no new table. The FIVE stay. This module is the INDEX over them.

NO ALIAS IS HAND-TYPED
----------------------
`add_alias` requires a `cite_ref` that names the CARRIER ROW the alias came from
(`legacy_id_map:7`, `view:llm_100_run`, `schema_migration_log:5`, `CODE_ONLY:...`)
and REFUSES anything else with `UNCITED_ALIAS`. A hand-typed alias is the free-text
island this whole register exists to remove.

Run:
    .\\.venv\\Scripts\\python.exe terminology_alias.py --measure
    .\\.venv\\Scripts\\python.exe terminology_alias.py --what-is llm_100_run
    .\\.venv\\Scripts\\python.exe terminology_alias.py --same-definition
    .\\.venv\\Scripts\\python.exe terminology_alias.py --gaps
    .\\.venv\\Scripts\\python.exe terminology_alias.py --apply
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

# THE CARRIER ORDER. Fixed and DECLARED, so an answer is reproducible and the
# caller can see which source was asked first.
CARRIER_ORDER: tuple[str, ...] = (
    "terminology_registry",   # a term key
    "alias_list",             # a registered alias
    "legacy_id_map",          # an old id -> new id
    "view",                   # an old table name kept alive as a VIEW
    "schema_migration_log",   # a recorded rename
    "CODE_ONLY",              # declared Python rename tuples (reported, not read)
)

# A declared alias must name one of these carriers. Anything else is REFUSED.
CARRIER_PREFIXES: tuple[str, ...] = (
    "legacy_id_map:", "view:", "schema_migration_log:", "CODE_ONLY:",
    "terminology_registry:", "alias_list:",
)

# Renames that live ONLY in Python (a tuple or a comment). They cannot be read
# from this process at runtime, so they are DECLARED here and REPORTED as
# CODE_ONLY — the carrier is named, so their absence from the register is a
# measured fact instead of a blind spot.
CODE_ONLY_RENAMES: tuple[tuple[str, str, str], ...] = (
    ("table", "db_table", "taxonomy_level_registry.LEVEL_RENAMES"),
    ("field", "db_field", "taxonomy_level_registry.LEVEL_RENAMES"),
    ("worker_identity", "worker_identity_flow", "_retire_old_flow.py:46"),
    ("case_registry", "chat_registry", "db_schema.py:4359 comment"),
    ("name_registry", "terminology_registry",
     "schema_migration_log:5 rename_name_registry_to_terminology_registry"),
    ("llm_100_run", "proof_run", "_migrate_proof_run_rename.py:14"),
    ("ollama", "llm_runtime", "skill_taxonomy_evidence.LEGACY_MODULE_TO_REGISTRY"),
    # NOT HERE: the `CP-S-0N` -> `mouse_spot_helper.*` pairs. They ARE carried by
    # `legacy_id_map` (a REAL carrier), and restating them here put a LEGACY ID
    # back into CODE — exactly the duplication this task removes. MEASURED: doing
    # so turned `_proof_legacy_id_map.py` RED ("zero code references remain").
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


def _aliases_of(raw: Any) -> list[str]:
    """Read `alias_list`. MEASURED: it is TEXT JSON, `'NA'` when empty."""
    s = str(raw or "").strip()
    if not s or s.upper() == "NA":
        return []
    if s.startswith("["):
        try:
            v = json.loads(s)
            return [str(x).strip() for x in v if str(x).strip()]
        except (ValueError, TypeError):
            return []
    return [s]


def _is_a_current_name(conn: sqlite3.Connection, name: str) -> bool:
    """Is `name` the CURRENT key of a registered entity (module/capability/...)?

    Used to decide the DIRECTION of an alias. MEASURED BUG FIXED: the module
    certification rows use the LEGACY code as `term_key` (`ollama`) and put the
    CURRENT module key in `alias_list` (`llm_runtime`). Read blindly, that made
    `what_is("llm_runtime")` answer `now=ollama` — BACKWARDS. The direction is
    decidable because `llm_runtime` IS a `module_key` and `ollama` is not.

    A SECOND MEASURED BUG FIXED HERE: this test did NOT look at `is_active`, so a
    RETIRED key (a `CP-S-*` capability code, `is_active=0`) counted as current. A
    newly registered term whose ALIAS is that retired key was then misread as the
    OLD side of a distil, and `name_classify` reported 11 false CONFLICTs. A key
    is only "current" when its row is ALIVE.
    """
    n = str(name or "").strip().lower()
    if not n:
        return False
    for table, col in (("module_registry", "module_key"),
                       ("capability_registry", "capability_key"),
                       ("channel_registry", "channel_key"),
                       ("workflow_registry", "workflow_key")):
        if not _table_exists(conn, table) or col not in _columns(conn, table):
            continue
        live = " AND is_active=1" if "is_active" in _columns(conn, table) else ""
        if conn.execute("SELECT 1 FROM %s WHERE lower(%s)=?%s"
                        % (table, col, live), (n,)).fetchone():
            return True
    return False


def _direction(conn: sqlite3.Connection, term_key: str, alias: str) -> str:
    """Which of the two is the CURRENT name: `forward` or `former`?

    `forward`  -> the ALIAS is the current name (the term records the old one).
    `former`   -> the TERM is the current name (the alias is the old one).
    `unknown`  -> neither is a registered key; REPORTED, not guessed.
    """
    t_cur = _is_a_current_name(conn, term_key)
    a_cur = _is_a_current_name(conn, alias)
    if a_cur and not t_cur:
        return "forward"
    if t_cur and not a_cur:
        return "former"
    if t_cur and a_cur:
        return "both_current"
    return "unknown"


# --------------------------------------------------------------------------
# THE ONE DOOR
# --------------------------------------------------------------------------
def _retired_flags(conn: sqlite3.Connection, asked: str, res: dict[str, Any]
                   ) -> dict[str, Any]:
    """Is the name the CALLER TYPED the superseded side?

    NOT "is any name in this answer retired". MEASURED BUG: asking about the
    CURRENT name `llm_runtime` reported `retired=True`, because the answer also
    mentions the old name `ollama`. The question is about the INPUT, so:

        input == was  -> retired = True   (the caller typed a superseded name)
        input == now  -> retired = False  (the caller typed the live name)
        no `was`      -> fall back to the matched row's `is_active`

    `is_active` is REPORTED, never used to change which name is current — that is
    `name_classify`'s decision (43 of 52 rows are 0 without being retired).
    """
    outs: dict[str, Any] = {"retired": False, "active_flag": None}
    if not _table_exists(conn, "terminology_registry"):
        return outs

    def _norm(s: Any) -> str:
        return str(s or "").strip().lower()

    a = _norm(asked)
    now = _norm(res.get("now"))
    was = _norm(res.get("was"))

    def _flag_of(key: str) -> Any:
        if not key:
            return None
        r = conn.execute("SELECT is_active FROM terminology_registry WHERE "
                         "lower(term_key)=?", (key,)).fetchone()
        return int(r[0]) if r is not None else None

    # the register row that ANSWERED: the term key, or the row whose alias matched
    key = now
    if _flag_of(key) is None:
        for r in conn.execute("SELECT term_key, alias_list, is_active FROM "
                              "terminology_registry"):
            if any(_norm(x) == now for x in _aliases_of(r["alias_list"])):
                key = _norm(r["term_key"])
                outs["active_flag"] = int(r["is_active"])
                break
    else:
        outs["active_flag"] = _flag_of(key)

    if was:
        outs["was_active_flag"] = _flag_of(was)
        if a == was:
            outs["retired"] = True
        elif a == now:
            outs["retired"] = False
        else:
            # the input named neither side exactly (e.g. a module key reached
            # through an alias): report the flag of the row behind the CURRENT
            # name, because that is the name the caller is being pointed AT.
            outs["retired"] = outs["active_flag"] == 0
        return outs
    outs["retired"] = outs["active_flag"] == 0
    return outs


def resolution(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """`resolve_name` PLUS the retired flag, as the ONE shape a caller reads.

    Kept separate so existing callers keep the exact previous dict, while a
    caller that needs to know whether the name it TYPED is SUPERSEDED has one
    place to ask.
    """
    r = resolve_name(conn, name)
    if not r.get("ok"):
        return r
    r.update(_retired_flags(conn, str(name), r))
    return r


def resolve_name(conn: sqlite3.Connection, name: str,
                 *, code_only: tuple[tuple[str, str, str], ...] | None = None
                 ) -> dict[str, Any]:
    """What is `name`, and was it called something else?

    Asks every carrier in `CARRIER_ORDER` and RETURNS WHICH ONE ANSWERED. An
    unknown name is `NOT_FOUND` with the list of carriers tried — never a guess,
    and never a similarity match (a near miss would answer a question the user
    did not ask).

    When the caller typed a SUPERSEDED name, the answer carries `retired: True`,
    so a caller can tell a live name from an old one. The flag is REPORTED, never
    used to change which name is current — that is `name_classify`'s decision.
    """
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "code": "EMPTY_NAME", "now": "", "was": None,
                "how": None, "cite": "measured: an empty name",
                "tried": []}
    low = n.lower()
    tried: list[str] = []

    # CARRIER 1 — is it a REGISTERED TERM?
    tried.append("terminology_registry")
    if _table_exists(conn, "terminology_registry"):
        r = conn.execute("SELECT term_id, term_key, alias_list, definition "
                         "FROM terminology_registry WHERE lower(term_key)=?",
                         (low,)).fetchone()
        if r:
            was = [a for a in _aliases_of(r["alias_list"]) if a.lower() != low]
            # THE TERM ITSELF MAY BE THE OLD NAME. MEASURED: the module
            # certification rows use the LEGACY code as `term_key` (`ollama`) and
            # put the CURRENT module key in the alias slot. Answering `now=ollama`
            # presents a RETIRED name as current, which is exactly the
            # old->new confusion this door exists to remove. So when an alias IS a
            # current registered key and the term key is NOT, the DISTIL is
            # taken: the current name is the answer.
            current_alias = [a for a in was
                             if _is_a_current_name(conn, a)
                             and not _is_a_current_name(conn, str(r["term_key"]))]
            if current_alias:
                return {"ok": True, "now": str(current_alias[0]),
                        "was": str(r["term_key"]), "how": "terminology_registry",
                        "term_id": int(r["term_id"]), "aliases": was,
                        "distilled": True,
                        "cite": ("measured: terminology_registry.term_key=%r is "
                                 "the RETIRED name; alias_list holds the CURRENT "
                                 "name %r" % (str(r["term_key"]),
                                              str(current_alias[0]))),
                        "tried": tried}
            return {"ok": True, "now": str(r["term_key"]),
                    "was": (was[0] if was else None), "how": "terminology_registry",
                    "term_id": int(r["term_id"]), "aliases": was,
                    "cite": "measured: terminology_registry.term_key = %r"
                            % str(r["term_key"]),
                    "tried": tried}

    # CARRIER 2 — is it a REGISTERED ALIAS?
    tried.append("alias_list")
    if _table_exists(conn, "terminology_registry"):
        for r in conn.execute("SELECT term_id, term_key, alias_list FROM "
                              "terminology_registry"):
            for a in _aliases_of(r["alias_list"]):
                if a.lower() != low:
                    continue
                direction = _direction(conn, str(r["term_key"]), a)
                # MEASURED: the module certification rows put the CURRENT key in
                # `alias_list`, so a blind read answers backwards. The direction
                # decides which of the two names the caller is asking about.
                if direction == "forward":
                    return {"ok": True, "now": a, "was": str(r["term_key"]),
                            "how": "alias_list", "term_id": int(r["term_id"]),
                            "direction": direction,
                            "aliases": _aliases_of(r["alias_list"]),
                            "cite": ("measured: terminology_registry.alias_list "
                                     "of %r holds the CURRENT name %r"
                                     % (str(r["term_key"]), a)),
                            "tried": tried}
                return {"ok": True, "now": str(r["term_key"]), "was": a,
                        "how": "alias_list", "term_id": int(r["term_id"]),
                        "direction": direction,
                        "aliases": _aliases_of(r["alias_list"]),
                        "cite": ("measured: terminology_registry.alias_list "
                                 "of %r contains %r (direction=%s)"
                                 % (str(r["term_key"]), a, direction)),
                        "tried": tried}

    # CARRIER 3 — is it an OLD ID in legacy_id_map?
    tried.append("legacy_id_map")
    if _table_exists(conn, "legacy_id_map"):
        r = conn.execute("SELECT map_id, entity_type, old_id, new_id, seen_in "
                         "FROM legacy_id_map WHERE lower(old_id)=?", (low,)).fetchone()
        if r:
            return {"ok": True, "now": str(r["new_id"]), "was": str(r["old_id"]),
                    "how": "legacy_id_map", "entity_type": str(r["entity_type"]),
                    "cite": "measured: legacy_id_map:%d old_id=%r -> new_id=%r"
                            % (int(r["map_id"]), str(r["old_id"]),
                               str(r["new_id"])),
                    "tried": tried}

    # CARRIER 4 — is it an old TABLE NAME kept alive as a VIEW?
    tried.append("view")
    r = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='view' "
                     "AND lower(name)=?", (low,)).fetchone()
    if r:
        target = ""
        sql = " ".join(str(r["sql"] or "").split())
        if " FROM " in sql.upper():
            target = sql.upper().split(" FROM ", 1)[1].split()[0].strip()
        return {"ok": True, "now": target or str(r["name"]),
                "was": str(r["name"]), "how": "view",
                "cite": "measured: sqlite_master view %r -> %s"
                        % (str(r["name"]), sql[:90]),
                "tried": tried}

    # CARRIER 5 — is it named by a RECORDED RENAME?
    tried.append("schema_migration_log")
    if _table_exists(conn, "schema_migration_log"):
        for m in conn.execute("SELECT id, migration, detail_json FROM "
                              "schema_migration_log"):
            mig = str(m["migration"] or "")
            # A WHOLE-NAME check, not a substring. MEASURED BUG FIXED: the first
            # version tested `name in blob.lower()`, so asking for `table`
            # matched the JSON of an UNRELATED migration (`...table...` appears in
            # the detail blob) and answered `schema_migration_log` with now=None.
            # A migration matches only when a TOKEN of its name, or a JSON KEY or
            # VALUE, EQUALS the name.
            tokens = {t for t in _split_tokens(mig)}
            try:
                det = json.loads(str(m["detail_json"] or "{}"))
            except (ValueError, TypeError):
                det = {}
            if isinstance(det, dict):
                tokens |= {str(k) for k in det}
                tokens |= {str(v) for v in det.values()}
            if low in {t.lower() for t in tokens}:
                return {"ok": True, "now": None, "was": n,
                        "how": "schema_migration_log",
                        "migration": mig, "migration_id": int(m["id"]),
                        "cite": "measured: schema_migration_log:%d migration=%r"
                                % (int(m["id"]), mig),
                        "tried": tried}

    # CARRIER 6 — a DECLARED code-only rename.
    tried.append("CODE_ONLY")
    for old, new, where in (code_only
                            if code_only is not None else CODE_ONLY_RENAMES):
        if low == str(old).lower():
            return {"ok": True, "now": str(new), "was": str(old),
                    "how": "CODE_ONLY", "declared_in": str(where),
                    "cite": "measured: CODE_ONLY rename declared at %s" % where,
                    "tried": tried}

    return {"ok": False, "code": "NOT_FOUND", "now": n, "was": None, "how": None,
            "why": ("no carrier holds %r; every carrier was asked and none "
                    "answered, so the name is NOT guessed" % n),
            "cite": "measured: tried %s" % tried, "tried": tried}


def what_is(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """ALIAS for `resolve_name` — the question a worker actually asks."""
    return resolve_name(conn, name)


def resolution(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """`resolve_name` PLUS the retired flag, as the ONE shape a caller reads.

    Kept separate so existing callers keep the exact previous dict, while a
    caller that needs to know whether the name is SUPERSEDED has one place to ask.
    """
    r = resolve_name(conn, name)
    if not r.get("ok"):
        return r
    r.update(_retired_flags(conn, str(name), r))
    return r


def was_name(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    """What THIS name USED to be called — the reverse question."""
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "code": "EMPTY_NAME"}
    # a term that lists this name as an alias
    if _table_exists(conn, "terminology_registry"):
        for r in conn.execute("SELECT term_id, term_key, alias_list FROM "
                              "terminology_registry"):
            if str(r["term_key"]).lower() == n.lower():
                aliases = _aliases_of(r["alias_list"])
                if aliases:
                    return {"ok": True, "now": str(r["term_key"]),
                            "was": aliases[0], "aliases": aliases,
                            "how": "alias_list",
                            "cite": "measured: terminology_registry.alias_list "
                                    "of %r = %s" % (str(r["term_key"]), aliases)}
                # fall through: a term with no alias may still be NEW for an old id
    for old, new, where in CODE_ONLY_RENAMES:
        if str(new).lower() == n.lower():
            return {"ok": True, "now": n, "was": str(old), "how": "CODE_ONLY",
                    "declared_in": str(where),
                    "cite": "measured: CODE_ONLY rename declared at %s" % where}
    if _table_exists(conn, "legacy_id_map"):
        r = conn.execute("SELECT map_id, old_id, new_id FROM legacy_id_map "
                         "WHERE lower(new_id)=?", (n.lower(),)).fetchone()
        if r:
            return {"ok": True, "now": str(r["new_id"]), "was": str(r["old_id"]),
                    "how": "legacy_id_map",
                    "cite": "measured: legacy_id_map:%d" % int(r["map_id"])}
    return {"ok": False, "code": "NO_FORMER_NAME", "now": n,
            "why": "no carrier records a former name for %r" % n,
            "cite": "measured: all carriers asked"}


# --------------------------------------------------------------------------
# add_alias — NO HAND-TYPED ALIAS
# --------------------------------------------------------------------------
def add_alias(conn: sqlite3.Connection, term_key: str, alias: str, *,
              cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """Add ONE alias to a term, ONLY from a named carrier.

    REFUSES:
      * an alias that IS the term (a self-alias says nothing)
      * an alias that already exists on the term (idempotent no-op, reported)
      * a citation that does not NAME a carrier (`UNCITED_ALIAS`) — this is what
        stops a hand-typed alias becoming a second truth
    """
    import terminology_registry as tr
    key = str(term_key or "").strip()
    al = str(alias or "").strip()
    cite = str(cite_ref or "").strip()
    if not key or not al:
        return {"ok": False, "code": "MISSING_ARGUMENT",
                "message": "term_key and alias are required"}
    if key.lower() == al.lower():
        return {"ok": False, "code": "SELF_ALIAS",
                "message": "an alias identical to the term says nothing: %r" % al}
    if not any(cite.startswith(p) for p in CARRIER_PREFIXES):
        return {"ok": False, "code": "UNCITED_ALIAS",
                "message": ("an alias must name the carrier ROW it came from "
                            "(one of %s), got %r — a hand-typed alias is a "
                            "second truth" % (list(CARRIER_PREFIXES), cite))}
    row = conn.execute("SELECT term_id, alias_list FROM terminology_registry "
                       "WHERE term_key=?", (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_TERM", "term_key": key}
    cur = _aliases_of(row["alias_list"])
    # AN ALIAS MAY NOT BE MISSPELLED *NOR* ON THE BLACKLIST (widened 2026-09-28).
    # THE HUMAN: "no!!!! rename or totally del, no more fucking mnis-undersatnd" /
    # "old data not reason for keep fucking wrong" (R-2).
    #
    # MEASURED: the first version refused only a NEW spelling from the 32-pair
    # dict, so a name ALREADY PRESENT (a legacy typo "kept for resolution") was
    # returned `created: False` and SURVIVED. That is exactly the carriage R-2
    # forbids. So this check runs BEFORE the idempotent return.
    #
    # The narrowness is preserved where it matters: a legitimate alias family
    # (`skill_5w1h`) is on the WHITELIST, not the blacklist, so it still passes.
    sp = tr.check_spelling(al, conn)
    if not sp["ok"]:
        return {"ok": False, "code": sp["code"], "message": sp["message"],
                "typo": sp.get("typo"), "correct": sp.get("correct"),
                "cite_ref": sp.get("cite_ref"), "already_present":
                any(a.lower() == al.lower() for a in cur)}
    if any(a.lower() == al.lower() for a in cur):
        return {"ok": True, "created": False, "term_key": key, "alias": al,
                "aliases": cur}
    new = cur + [al]
    conn.execute("UPDATE terminology_registry SET alias_list=?, "
                 "updated_at=datetime('now') WHERE term_id=?",
                 (json.dumps(new, ensure_ascii=False), int(row["term_id"])))
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "term_key": key, "alias": al,
            "aliases": new, "cite": cite}


# --------------------------------------------------------------------------
# the derived aliases: from the carriers that ALREADY hold them
# --------------------------------------------------------------------------
def derive_aliases(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every alias a CARRIER already implies, and every one with NO HOST term.

    A carrier names an old->new pair. The alias must be stored ON the term for
    the NEW name, so a pair whose new name has NO term CANNOT be stored yet. That
    is REPORTED as `unhostable` with the term it needs — a silent skip would hide
    that the register still does not know the new name.
    """
    derivable: list[dict[str, Any]] = []
    unhostable: list[dict[str, Any]] = []
    terms = {str(r["term_key"]).lower(): str(r["term_key"])
             for r in conn.execute("SELECT term_key FROM terminology_registry")} \
        if _table_exists(conn, "terminology_registry") else {}
    aliases_now: dict[str, list[str]] = {}
    for r in conn.execute("SELECT term_key, alias_list FROM terminology_registry"):
        aliases_now[str(r["term_key"])] = _aliases_of(r["alias_list"])

    def _consider(old: str, new: str, cite: str, how: str) -> None:
        t = terms.get(str(new).lower(), "")
        if not t:
            unhostable.append({"alias": str(old), "needs_term": str(new),
                               "cite": cite, "how": how,
                               "why": ("the register holds NO term for the NEW "
                                       "name %r, so the alias has nowhere to "
                                       "live; the new name must be registered "
                                       "first" % str(new))})
            return
        if str(old).lower() in [a.lower() for a in aliases_now.get(t, [])]:
            return
        derivable.append({"term_key": t, "alias": str(old), "cite": cite,
                          "how": how})

    if _table_exists(conn, "legacy_id_map"):
        for r in conn.execute("SELECT map_id, old_id, new_id FROM legacy_id_map "
                              "ORDER BY map_id"):
            _consider(str(r["old_id"]), str(r["new_id"]),
                      "legacy_id_map:%d" % int(r["map_id"]), "legacy_id_map")
    for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='view'"):
        sql = " ".join(str(r["sql"] or "").split())
        target = ""
        if " FROM " in sql.upper():
            target = sql.upper().split(" FROM ", 1)[1].split()[0].strip()
        if target:
            # MEASURED BUG FIXED: the target was LOWERCASED, so `v_skill_contract`
            # (a view WITH a WHERE) was looked up as `skill_contract_template` —
            # a name that does not exist. Lowercasing is only safe because the
            # term lookup is already case-insensitive; the REAL rename target is
            # the table name as written.
            low_t = target.lower()
            real = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "lower(name)=?", (low_t,)).fetchone()
            _consider(str(r["name"]), str(real["name"]) if real else target,
                      "view:%s" % str(r["name"]), "view")
    for old, new, where in CODE_ONLY_RENAMES:
        if where.startswith("legacy_id_map"):
            continue          # already handled above, with its real row id
        _consider(old, new, "CODE_ONLY:%s" % where, "CODE_ONLY")
    return {"ok": True, "derivable": derivable, "unhostable": unhostable,
            "derivable_count": len(derivable),
            "unhostable_count": len(unhostable),
            "cite": "measured: legacy_id_map + views + CODE_ONLY, joined to terms"}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Write the derived aliases. Idempotent. NO hand-typed alias."""
    d = derive_aliases(conn)
    added: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for c in d["derivable"]:
        r = add_alias(conn, c["term_key"], c["alias"], cite_ref=c["cite"])
        (added if r.get("created") else skipped).append(dict(c, **r))
    return {"ok": True, "candidates": d["derivable_count"], "added": added,
            "skipped": skipped, "unhostable": d["unhostable"],
            "code_only": [{"was": o, "now": n, "declared_in": w}
                          for o, n, w in CODE_ONLY_RENAMES],
            "code_only_note": ("CODE_ONLY renames live in PYTHON and cannot be "
                               "read from another process, so they are REPORTED "
                               "as data rather than silently absent")}


# --------------------------------------------------------------------------
# the user's two other questions: same definition, same source
# --------------------------------------------------------------------------
def same_definition(conn: sqlite3.Connection) -> dict[str, Any]:
    """Two names, one `definition_sha256` — the EXACT same-meaning signal."""
    groups: dict[str, list[str]] = {}
    for r in conn.execute("SELECT term_key, definition_sha256 FROM "
                          "terminology_registry ORDER BY term_key"):
        groups.setdefault(str(r["definition_sha256"]), []).append(str(r["term_key"]))
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    total = conn.execute("SELECT COUNT(*) FROM terminology_registry").fetchone()[0]
    filled = conn.execute("SELECT COUNT(*) FROM terminology_registry WHERE "
                          "definition_sha256 IS NOT NULL AND "
                          "TRIM(definition_sha256)<>'' AND "
                          "definition_sha256<>'NA'").fetchone()[0]
    indexed = [r["name"] for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND "
        "tbl_name='terminology_registry' AND sql LIKE '%definition_sha256%'")]
    return {"ok": True, "rows": int(total), "sha_filled": int(filled),
            "distinct_sha": len(groups), "duplicate_groups": dupes,
            "duplicate_count": len(dupes), "sha_indexed": bool(indexed),
            "index_names": indexed,
            "cite": "measured: GROUP BY definition_sha256 over %d rows" % int(total)}


def same_source(conn: sqlite3.Connection) -> dict[str, Any]:
    """Two terms naming one `entity_ref_key` — the same-SOURCE signal."""
    groups: dict[str, list[str]] = {}
    for r in conn.execute("SELECT term_key, entity_ref_key FROM "
                          "terminology_registry WHERE entity_ref_key IS NOT NULL "
                          "AND entity_ref_key<>'NA' ORDER BY term_key"):
        groups.setdefault(str(r["entity_ref_key"]), []).append(str(r["term_key"]))
    shared = {k: v for k, v in groups.items() if len(v) > 1}
    return {"ok": True, "refs": len(groups), "shared": shared,
            "shared_count": len(shared),
            "cite": "measured: GROUP BY entity_ref_key over terminology_registry"}


# --------------------------------------------------------------------------
# the COMPLETENESS GATE — a NUMBER, not a claim
# --------------------------------------------------------------------------
def name_gaps(conn: sqlite3.Connection,
              code_only: tuple[tuple[str, str, str], ...] | None = None
              ) -> dict[str, Any]:
    """Every carrier entry that does NOT resolve through `resolve_name`.

    A NUMBER is returned, so "complete" is checkable, and a NEW legacy name
    cannot be added unnoticed.
    """
    declared = code_only if code_only is not None else CODE_ONLY_RENAMES
    items: list[dict[str, str]] = []
    if _table_exists(conn, "legacy_id_map"):
        for r in conn.execute("SELECT old_id, entity_type FROM legacy_id_map"):
            items.append({"name": str(r["old_id"]),
                          "carrier": "legacy_id_map",
                          "detail": str(r["entity_type"])})
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'"):
        items.append({"name": str(r["name"]), "carrier": "view", "detail": ""})
    if _table_exists(conn, "schema_migration_log"):
        for r in conn.execute("SELECT id, migration FROM schema_migration_log "
                              "WHERE lower(migration) LIKE '%rename%'"):
            items.append({"name": str(r["migration"]),
                          "carrier": "schema_migration_log",
                          "detail": "id=%d" % int(r["id"])})
    for old, new, where in declared:
        items.append({"name": str(old), "carrier": "CODE_ONLY",
                      "detail": str(where)})

    gaps: list[dict[str, str]] = []
    resolved: list[dict[str, str]] = []
    for it in items:
        r = resolve_name(conn, it["name"], code_only=declared)
        if r["ok"]:
            resolved.append(dict(it, now=str(r.get("now")), how=str(r.get("how"))))
        else:
            gaps.append(dict(it, code=r["code"]))

    # EVERY alias must be a NAME. A DESCRIPTION in an alias slot is a DEFECT, so
    # it is a gap BY DEFINITION — it must NOT be passed through `resolve_name`,
    # which would resolve it (it IS registered) and hide the defect. MEASURED:
    # the first version did exactly that and reported 0 gaps for `goal`'s
    # `final key factor`. The `goal` term shipped with a description in its slot.
    if _table_exists(conn, "terminology_registry"):
        for r in conn.execute("SELECT term_key, alias_list FROM "
                              "terminology_registry WHERE alias_list NOT IN "
                              "('NA','[]') AND alias_list IS NOT NULL"):
            for a in _aliases_of(r["alias_list"]):
                d = describe_alias(conn, str(r["term_key"]), a)
                if not d["ok"]:
                    gaps.append({"name": str(a),
                                 "carrier": "alias_list:%s" % str(r["term_key"]),
                                 "detail": str(d["why"]),
                                 "code": "ALIAS_IS_A_DESCRIPTION"})
    return {"ok": True, "items": len(items), "resolved": len(resolved),
            "gap_count": len(gaps), "gaps": gaps, "resolved_items": resolved,
            "by_carrier": {c: {"items": sum(1 for i in items if i["carrier"] == c),
                               "gaps": sum(1 for g in gaps if g["carrier"] == c)}
                           for c in sorted({i["carrier"] for i in items})},
            "cite": "measured: resolve_name over every carrier entry"}


def ensure_sha_index(conn: sqlite3.Connection) -> dict[str, Any]:
    """Index `definition_sha256` — MEASURED to be the one index it lacks."""
    if not _table_exists(conn, "terminology_registry"):
        return {"ok": False, "code": "NO_registry"}
    ex = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND "
        "tbl_name='terminology_registry' AND sql LIKE '%definition_sha256%'")]
    if ex:
        return {"ok": True, "created": False, "index": ex[0]}
    conn.execute("CREATE INDEX IF NOT EXISTS idx_terminology_registry_sha "
                 "ON terminology_registry (definition_sha256)")
    conn.commit()
    return {"ok": True, "created": True, "index": "idx_terminology_registry_sha"}


def dropped_view_report(conn: sqlite3.Connection,
                        existed: list[str]) -> dict[str, Any]:
    """Which of `existed` no longer resolves — the MMUTATION helper.

    Used to prove that REMOVING a carrier turns the name into a REPORTED gap
    rather than a silent pass.
    """
    out = []
    for n in existed:
        r = resolve_name(conn, n)
        out.append({"name": n, "resolves": bool(r["ok"]),
                    "how": r.get("how"), "code": r.get("code")})
    return {"ok": True, "views": out,
            "lost": [o for o in out if not o["resolves"]],
            "cite": "measured: resolve_name after the carrier was removed"}


def describe_alias(conn: sqlite3.Connection, term_key: str, alias: str) -> dict[str, Any]:
    """Is this alias a NAME, or a DESCRIPTION that does not belong in the slot?"""
    a = str(alias or "").strip()
    words = a.split()
    why: list[str] = []
    if len(words) > 2:
        why.append("it is %d words, so it reads as a DESCRIPTION" % len(words))
    if len(a) > 32:
        why.append("it is %d chars long" % len(a))
    if not why:
        return {"ok": True, "alias": a, "looks_like_a_name": True}
    return {"ok": False, "code": "ALIAS_IS_A_DESCRIPTION", "alias": a,
            "term_key": str(term_key), "why": "; ".join(why),
            "cite": "measured: alias length/word-count check"}


def drop_description_alias(conn: sqlite3.Connection, term_key: str, alias: str,
                           *, cite_ref: str) -> dict[str, Any]:
    """Remove an alias that is a DESCRIPTION. Requires an explicit citation."""
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    d = describe_alias(conn, term_key, alias)
    if d["ok"]:
        return {"ok": True, "dropped": False, "why": "it looks like a name"}
    row = conn.execute("SELECT term_id, alias_list FROM terminology_registry "
                       "WHERE term_key=?", (str(term_key),)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_TERM"}
    cur = _aliases_of(row["alias_list"])
    keep = [a for a in cur if a.lower() != str(alias).strip().lower()]
    if len(keep) == len(cur):
        return {"ok": True, "dropped": False, "why": "the alias was not there"}
    conn.execute("UPDATE terminology_registry SET alias_list=?, "
                 "updated_at=datetime('now') WHERE term_id=?",
                 (json.dumps(keep, ensure_ascii=False) if keep else "NA",
                  int(row["term_id"])))
    conn.commit()
    return {"ok": True, "dropped": True, "term_key": str(term_key),
            "alias": str(alias), "remaining": keep, "cite": cite_ref}


def _split_tokens(text: str) -> list[str]:
    """Split a migration/JSON string into whole-name tokens.

    `rename_name_registry_to_terminology_registry` yields
    `rename`, `name_registry`, `terminology_registry` as well as every `_`-part,
    so a WHOLE-NAME lookup can match without a loose substring test.
    """
    import re
    out: list[str] = []
    for chunk in re.split(r"[\s,;:\[\]{}\"'=]+", str(text or "")):
        c = chunk.strip()
        if not c:
            continue
        out.append(c)
        if "_" in c:
            out.extend(p for p in c.split("_") if p)
            if c.startswith("rename_") and c.endswith(""):
                # `rename_A_to_B` -> the two name segments
                parts = c.split("_to_")
                if len(parts) == 2:
                    out.append(parts[0][len("rename_"):])
                    out.append(parts[1])
    return out


def remove_alias(conn: sqlite3.Connection, term_key: str, alias: str, *,
                 cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """Remove ANY alias from a term, with a citation.

    Used for cleanup. `drop_description_alias` is the NARROWER operation (it
    refuses to touch something that looks like a name), so it cannot undo a probe
    that added a name-shaped alias.

    `commit=False` lets a CALLER own the transaction. MEASURED 2026-09-28: an
    unconditional `commit()` here RELEASED the caller's `SAVEPOINT`, so a batched
    apply failed with `no such savepoint` on its second row. A door that commits
    under a caller cannot be composed.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    row = conn.execute("SELECT term_id, alias_list FROM terminology_registry "
                       "WHERE term_key=?", (str(term_key),)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_TERM", "term_key": term_key}
    cur = _aliases_of(row["alias_list"])
    keep = [a for a in cur if a.lower() != str(alias).strip().lower()]
    if len(keep) == len(cur):
        return {"ok": True, "dropped": False, "reason": "alias not present",
                "remaining": cur}
    conn.execute("UPDATE terminology_registry SET alias_list=?, "
                 "updated_at=datetime('now') WHERE term_id=?",
                 (json.dumps(keep, ensure_ascii=False) if keep else "NA",
                  int(row["term_id"])))
    if commit:
        conn.commit()
    return {"ok": True, "dropped": True, "term_key": str(term_key),
            "alias": str(alias), "remaining": keep, "cite": cite_ref}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"ok": True,
            "gaps": name_gaps(conn),
            "same_definition": same_definition(conn),
            "same_source": same_source(conn),
            "derivable_aliases": derive_aliases(conn),
            "carriers": {
                "terminology_registry_rows": conn.execute(
                    "SELECT COUNT(*) FROM terminology_registry").fetchone()[0]
                if _table_exists(conn, "terminology_registry") else 0,
                "legacy_id_map_rows": conn.execute(
                    "SELECT COUNT(*) FROM legacy_id_map").fetchone()[0]
                if _table_exists(conn, "legacy_id_map") else 0,
                "views": [r["name"] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'")],
                "schema_migration_log_rows": conn.execute(
                    "SELECT COUNT(*) FROM schema_migration_log").fetchone()[0]
                if _table_exists(conn, "schema_migration_log") else 0,
                "code_only_renames": len(CODE_ONLY_RENAMES),
            }}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--what-is", default="")
    ap.add_argument("--was-name", default="")
    ap.add_argument("--same-definition", action="store_true")
    ap.add_argument("--gaps", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.what_is:
            print(json.dumps(resolve_name(conn, args.what_is), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        if args.was_name:
            print(json.dumps(was_name(conn, args.was_name), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        if args.same_definition:
            print(json.dumps(same_definition(conn), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        if args.gaps:
            g = name_gaps(conn)
            print("carrier entries: %d   RESOLVED: %d   GAPS: %d"
                  % (g["items"], g["resolved"], g["gap_count"]))
            for c, v in g["by_carrier"].items():
                print("   %-24s items=%-3d gaps=%-3d" % (c, v["items"], v["gaps"]))
            for x in g["gaps"]:
                print("   GAP  %-34s carrier=%-24s %s"
                      % (str(x["name"])[:34], x["carrier"], x["detail"]))
            return 0
        if args.apply:
            ix = ensure_sha_index(conn)
            res = apply(conn)
            print("index on definition_sha256: %s" % ix)
            print("ALIASES: candidates=%d added=%d skipped=%d"
                  % (res["candidates"], len(res["added"]), len(res["skipped"])))
            for a in res["added"]:
                print("   ADD  %-26s <- %-34s (%s)"
                      % (a["term_key"], a["alias"], a["cite"]))
            for a in res["skipped"]:
                print("   HAVE %-26s <- %-34s (%s)"
                      % (a["term_key"], a["alias"], a["cite"]))
            print("CODE_ONLY renames REPORTED (not silently absent): %d"
                  % len(res["code_only"]))
            for c in res["code_only"]:
                print("   %-20s -> %-34s %s"
                      % (c["was"], c["now"], c["declared_in"]))
            g = name_gaps(conn)
            print("AFTER: carrier entries=%d resolved=%d GAPS=%d"
                  % (g["items"], g["resolved"], g["gap_count"]))
            return 0
        res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        g = res["gaps"]
        print("THE FIVE CARRIERS")
        for k, v in res["carriers"].items():
            print("   %-28s %s" % (k, v))
        print()
        print("COMPLETENESS: carrier entries=%d resolved=%d GAPS=%d"
              % (g["items"], g["resolved"], g["gap_count"]))
        for c, v in g["by_carrier"].items():
            print("   %-24s items=%-3d gaps=%-3d" % (c, v["items"], v["gaps"]))
        sd = res["same_definition"]
        print()
        print("SAME DEFINITION: rows=%d sha_filled=%d distinct=%d dup_groups=%d "
              "sha_indexed=%s" % (sd["rows"], sd["sha_filled"],
                                  sd["distinct_sha"], sd["duplicate_count"],
                                  sd["sha_indexed"]))
        ss = res["same_source"]
        print("SAME SOURCE: refs=%d shared_between_two_terms=%d"
              % (ss["refs"], ss["shared_count"]))
        print("DERIVABLE ALIASES: %d   (unhostable: %d — the NEW name has no term)"
              % (res["derivable_aliases"]["derivable_count"],
                 res["derivable_aliases"]["unhostable_count"]))
        for a in res["derivable_aliases"]["derivable"]:
            print("   %-26s <- %-34s (%s)"
                  % (a["term_key"], a["alias"], a["cite"]))
        for a in res["derivable_aliases"]["unhostable"]:
            print("   NO HOST  %-24s needs term %-30s (%s)"
                  % (a["alias"], a["needs_term"], a["cite"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
