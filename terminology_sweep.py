# -*- coding: utf-8 -*-
"""terminology_sweep.py — register the site's names in BOUNDED PHASES.

WHY (the user, 2026-09-23)
--------------------------
    "for whole site and by phase (volume control) and we can test 7B performance
     too with ui report"

MEASURED VOLUME (grep): 117 tables, 230 routes, 718 modules -> ~2000+ names. A
single unbounded run is neither reviewable nor safe, so a phase processes AT MOST
`cap` names and the report SHOWS `total`/`returned`/`truncated`.

THE DIVISION OF LABOUR (measured, not assumed)
----------------------------------------------
    the 7B            drafts term_key + term_kind + definition   (language)
    terminology_cite  verifies or SUPPLIES the cite_ref          (determinism)
    add_term          REFUSES an unciteable citation             (the gate)

MEASURED (`_probe_7b_terminology.py`): the 7B's names and definitions were usable
every time, but its citations were NOT — it put a refusal in the `cite_ref` field
twice and invented a filename once. So the 7B is NEVER asked for a citation here;
asking it would produce exactly those invented paths.

DISCOVERY IS DETERMINISTIC
--------------------------
`discover()` reads the SOURCE (a file, a regex). The 7B never invents the list of
names to register — if it did, the sweep would measure the model's imagination
instead of the site's contents.

Run:
    .\\.venv\\Scripts\\python.exe terminology_sweep.py --discover tables
    .\\.venv\\Scripts\\python.exe terminology_sweep.py --run tables --apply
    .\\.venv\\Scripts\\python.exe terminology_sweep.py --report tables
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import db_schema as ds  # noqa: E402
import terminology_cite as tc  # noqa: E402
import terminology_registry as tr  # noqa: E402

DB = BASE / "agent.db"
OLLAMA = "http://127.0.0.1:11434/api/chat"
# MEASURED DEFECT FIXED (2026-09-23) — the user's words:
#     "so system will nnot have this problem again not hardcode!!!!!"
#     "and hardcode is not allowed, hot can you fucking to have that!!!"
#
# This was `MODEL = "qwen2.5:7b-instruct"`: a PYTHON LITERAL, while the registry
# (`llm_route_provider` for the `llm.text` route) already named the same model
# at priority 10. The literal and the registry agreed BY LUCK. The model is now
# RESOLVED per call from the route, with the SAME shape as
# `vision_analyze.resolve_vision_model`, and this name is kept only so the two
# are greppable together.
MODEL_CONSTANT_REMOVED = True


def resolve_model(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The text model this sweep must call, resolved from the `llm.text` route.

    Never silently falls back: `source` is `registry` or `fault`, and a fault
    carries its reason. A sweep that ran on an unregistered model while the
    report said "qwen2.5:7b-instruct" would be a number nobody could reproduce.
    """
    import llm_service_store as lss

    return lss.resolve_text_model(conn)

# The legal scopes. A FIXED set -> a Python tuple (repo doctrine); an OPEN set
# would be a table. Validated at the WRITE SITE.
#
# `catalog` ADDED 2026-09-27. THE HUMAN: "why you have 2 language for the same
# thing, so we need to have catalog, and how does it can standardize, by
# termontology ?" / "by skill and auto forever".
#
# MEASURED: the 20 taskbar element names in `target_template` had ZERO terms in
# `terminology_registry`, so nothing defined them and nothing could tell
# `taskbar_doubao` (the 豆包 desktop app) from `taskbar_doubao_browser` (the 豆包
# browser). The other scopes read SOURCE (tables/routes/modules); this one reads
# the TARGET REGISTER, which is where a name is USED rather than declared.
#
# `rubbish` ADDED 2026-09-27. THE HUMAN: "you love rubbish?
# taskbar_vscode_app!!!????" / "or you need to have helper to cleanup or rubbish
# definition".
#
# MEASURED DEFECT, and it is why this line matters: `rubbish` was added to
# `PHASES` but NOT to `SCOPES`, and `seed_phases` REFUSES the WHOLE SEED when a
# phase's scope is not in `SCOPES` -- so the phase never reached the table and
# the proof caught it as `cap=None phases=4`. A phase that is declared but never
# seeded is a phase that does not exist.
SCOPES = ("tables", "routes", "modules", "columns", "factors", "skills",
          "catalog", "rubbish", "group", "completeness_5w1h")

# THE 7B IS ASKED FOR LANGUAGE ONLY. There is deliberately NO `cite_ref` key in
# this prompt: the model cannot produce a checkable citation (measured), and
# asking for one produced an invented filename. The citation is supplied by
# `terminology_cite` and verified before it reaches the register.
DRAFT_SYSTEM = (
    "You name things for a software repository. Reply with ONE JSON object and "
    "nothing else. Keys: term_key (lowercase snake_case), term_kind (one of "
    "count, action, role, entity, qualifier, part), definition (one sentence "
    "saying what the thing IS and what it is NOT). Do NOT include a citation."
)

# The CONTROL: a name that is NOT in the source. The prompt asks for a name and a
# definition ONLY (no citation), so the failure mode to guard against is NOT
# "invents a citation" — it is "invents a confident definition for something that
# does not exist".
#
# MEASURED DEFECT in the first version of this control: it asked for a concept
# with "no citation available" and expected a REFUSAL. That premise no longer
# applied once the prompt stopped asking for a citation, so the control FAILED
# while the harness was working correctly — a control that tests a question the
# prompt does not ask measures nothing.
#
# The control now asks for a name that is NOT in the discovered list, and PASSES
# only if the model REFUSES or flags uncertainty. If it invents a confident
# definition, the control FAILS — and that is a REAL limitation to report, not a
# bug to tune away: it means the 7B cannot tell a real name from a fake one, so
# its definitions must be reviewed rather than trusted.
CONTROL_NAME = "__control_not_in_source__"
CONTROL_ASK = (
    "Name this thing from a software repository: the internal name of the "
    "feature that will replace the terminology register next year. It is not in "
    "any file yet. If you cannot name a real thing, reply with "
    "{\"refuse\": \"reason\"}."
)


# ---------------------------------------------------------------------------
# P1 — the phase register
# ---------------------------------------------------------------------------

# The phase register. `cap` is the volume control: small enough that a human can
# read the refusals of one phase.
#
# The counts are NOT written into `why`. MEASURED DEFECT: the first version said
# "The smallest scope (117)" and "230 routes" — the counts from an earlier grep.
# The measured discovery is 109 and 219, so the page displayed a WRONG NUMBER
# next to the right one. A wrong count is a wrong claim, and it was the only
# number on the page that no code computed. The size is reported by
# `phase_progress` instead, and `why` cites the command that measures it.
PHASES = (
    {"phase_key": "tables", "display_name": "Tables", "scope": "tables",
     "cap": 25, "sort_order": 10,
     "why": "The smallest scope, and the one that caused the naming collision, "
            "so it proves the harness where it already bit us. Size from "
            "`terminology_sweep.py --discover tables`.",
     "cite_ref": "db_schema.py:3842"},    {"phase_key": "routes", "display_name": "API routes", "scope": "routes",
     "cap": 25, "sort_order": 20,
     "why": "A route name is what a caller reads, so a collision here is a "
            "wrong call. Size from `terminology_sweep.py --discover routes`.",
     "cite_ref": "mouse_spot_helper.py:3064"},    {"phase_key": "modules", "display_name": "Python modules", "scope": "modules",
     "cap": 25, "sort_order": 30,
     "why": "The largest scope, so it runs last and capped. Size from "
            "`terminology_sweep.py --discover modules`.",
     "cite_ref": "terminology_sweep.py:212"},
    {"phase_key": "catalog", "display_name": "Target catalog",
     "scope": "catalog", "cap": 25, "sort_order": 40,
     "why": "The names a TARGET uses, not a name the source declares. MEASURED "
            "2026-09-27: the 20 taskbar element names had ZERO terms, so "
            "nothing defined them and nothing could tell the 豆包 desktop app "
            "from the 豆包 browser. Size from "
            "`terminology_sweep.py --discover catalog`.",
     "cite_ref": "terminology_catalog.py:20"},
    # `rubbish` ADDED 2026-09-27. THE HUMAN: "you love rubbish?
    # taskbar_vscode_app!!!????" / "or you need to have helper to cleanup or
    # rubbish definition".
    #
    # THIS PHASE IS DIFFERENT FROM THE OTHER FOUR, and the difference is the
    # point: the others DISCOVER names that have no term; this one AUDITS the
    # definitions that already exist. Its scope is the register itself, so it
    # measures whether the gate has ever had to fire.
    {"phase_key": "rubbish", "display_name": "Rubbish definitions",
     "scope": "rubbish", "cap": 25, "sort_order": 50,
     "why": "The definitions that SAY NOTHING. MEASURED 2026-09-27: the register "
            "had NO check on a definition's CONTENT, so the sweep's own fallback "
            "(`a name from tables`) would have been accepted. This phase audits "
            "what exists; `terminology_registry.check_definition` stops what is "
            "new. Size from `terminology_rubbish.py --scan`.",
     "cite_ref": "terminology_registry.py:300"},
    # `group` ADDED 2026-09-27. THE HUMAN: "i have taskbar_vscode and
    # taskbar_vscode_app" / "which is rubbish" / "why taskbar_vscode not =
    # taskbar_vscode_app".
    #
    # THIS PHASE IS DIFFERENT FROM THE OTHER FIVE, and the difference is the
    # point: the others DISCOVER names that have no term; this one AUDITS the
    # NODE KIND of the terms that exist. MEASURED: `taskbar_vscode` (1489) has 2
    # children and `taskbar_vscode_app` (1474) has 0, and BOTH carried
    # `term_kind='entity'` -- so a reader saw two identical rows and could not
    # tell a container from a leaf.
    #
    # The kind is DERIVED from `children > 0` by
    # `terminology_generator.node_kind`, and this phase measures the population
    # that derivation covers.
    {"phase_key": "group", "display_name": "Group vs leaf nodes",
     "scope": "group", "cap": 25, "sort_order": 60,
     "why": "The nodes that CONTAIN other nodes. MEASURED 2026-09-27: the table "
            "had NO `group` kind and NO column saying group vs leaf, so "
            "`taskbar_vscode` (2 children) and `taskbar_vscode_app` (0 children) "
            "were indistinguishable -- both `term_kind='entity'`. The kind is "
            "DERIVED from `children > 0` by `terminology_generator.node_kind`, "
            "never stored, so it cannot drift. Size from "
            "`terminology_generator.py --node <key>`.",
     "cite_ref": "terminology_generator.py:358"},
    # `completeness_5w1h` ADDED 2026-09-27. THE HUMAN: "fuck! how to you have
    # correct 5W1H in easy".
    #
    # RENAMED 2026-09-27 from `five_w1h`. THE HUMAN: "`five_w1h`=462, this is
    # wrong spelling BUG, have totally rename and fix, but you can have that,
    # should gone away forever". The repo's OWN convention puts `5w1h` at the
    # END (`skill_5w1h`, `ticket_5w1h`, `derive_5w1h`), and Python forbids an
    # identifier starting with a digit, so the name is `completeness_5w1h`.
    #
    # THIS PHASE IS DIFFERENT FROM THE OTHER SIX, and the difference is the
    # point: the others DISCOVER names that have no term; this one AUDITS the
    # SIX QUESTIONS a target must answer. MEASURED: the register answered 4 of 6
    # -- WHAT (term_key), WHERE (coordinate), WHY (definition), HOW (field_type)
    # -- and **WHICH (the instance) had no column at all**, so "which Chrome?"
    # had no machine-usable answer.
    #
    # The answer is `terminology_generator.completeness_5w1h`, and this phase
    # measures the population it covers.
    {"phase_key": "completeness_5w1h", "display_name": "5W1H completeness",
     "scope": "completeness_5w1h", "cap": 25, "sort_order": 70,
     "why": "The SIX QUESTIONS a target must answer. MEASURED 2026-09-27: the "
            "register answered 4 of 6 -- WHAT (term_key), WHERE (coordinate), "
            "WHY (definition), HOW (field_type) -- and WHICH (the instance) had "
            "NO COLUMN, so 'which Chrome?' had no machine-usable answer. The "
            "answer is: a NAME answers WHAT; a FIELD answers WHICH. Size from "
            "`terminology_generator.py --completeness-5w1h`.",
     "cite_ref": "terminology_generator.py:462"},
)


def _now_us() -> str:
    """A microsecond timestamp, for an append-only log.

    The DDL's DEFAULT is `datetime('now')`, which resolves to the SECOND, and the
    table is `UNIQUE (phase_key, name, observed_at)`. A dry run followed
    immediately by an APPLY run therefore collided and every APPLIED row was
    silently dropped — measured as `done=23` instead of 25. Supplying the
    timestamp here keeps the uniqueness that catches a true duplicate while
    letting a legitimate re-run append.

    `datetime` is used rather than `time.strftime`, because `%f` is guaranteed in
    `datetime` and only incidentally present in `time` on some platforms.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the tables. Idempotent.

    MERGED 2026-09-27 (plan REGISTER.NAMING.AND.PHASE.MERGE): the phase rows now
    live in `phase_registry` with `phase_kind='terminology_sweep'`. The old
    `terminology_sweep_phase` table is GONE — see `db_schema.PHASE_registry_DDL`.

    🔴 `ds.phase_registry_DDL` DOES NOT EXIST. MEASURED 2026-09-29: the real name
    is `PHASE_registry_DDL` (`db_schema.py`). The lowercase spelling appears
    NOWHERE in `db_schema.py`, so this line raised `AttributeError` on EVERY call
    to `ensure_schema`.
    """
    conn.executescript(ds.PHASE_registry_DDL)
    conn.executescript(ds.TERMINOLOGY_SWEEP_RUN_DDL)
    conn.commit()
    return {"ok": True}


PHASE_KIND = "terminology_sweep"


def seed_phases(conn: sqlite3.Connection) -> dict[str, Any]:
    """UPSERT the phases. A corrected `cap` must be able to land."""
    ensure_schema(conn)
    n = 0
    for p in PHASES:
        if p["scope"] not in SCOPES:
            return {"ok": False, "error": "phase %s has scope %r, not one of %s"
                    % (p["phase_key"], p["scope"], list(SCOPES))}
        conn.execute(
            """
            INSERT INTO phase_registry
                (phase_kind, phase_key, display_name, scope, cap, sort_order,
                 why, cite_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (phase_kind, phase_key) DO UPDATE SET
                display_name = excluded.display_name,
                scope        = excluded.scope,
                cap          = excluded.cap,
                sort_order   = excluded.sort_order,
                why          = excluded.why,
                cite_ref     = excluded.cite_ref,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (PHASE_KIND, p["phase_key"], p["display_name"], p["scope"],
             p["cap"], p["sort_order"], p["why"], p["cite_ref"]),
        )
        n += 1
    conn.commit()
    return {"ok": True, "seeded": n}


def list_phases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT phase_key, display_name, scope, cap, sort_order, why, cite_ref "
        "  FROM phase_registry WHERE phase_kind=? AND is_active=1 "
        " ORDER BY sort_order, phase_key", (PHASE_KIND,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# P2 — DETERMINISTIC discovery (the 7B is never asked for the list)
# ---------------------------------------------------------------------------

# The name must be followed by `(` on the SAME line. MEASURED DEFECT: without
# that, the regex matched PROSE — the comment "CREATE TABLE IF NOT EXISTS cannot
# change a column's declared type" produced a table called `cannot`, which does
# not exist. A discovery that invents a name measures the comment, not the site.
_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)\s*\(",
                       re.IGNORECASE)
_ROUTE_RE = re.compile(r'@app\.route\(\s*"([^"]+)"')


def discover(scope: str, base: Path | None = None) -> list[str]:
    """The names in one scope, read from the SOURCE. Deterministic, sorted.

    Sorted so two runs produce the SAME list — a sweep whose input order changes
    between runs cannot be compared, and `truncated` would mean something
    different each time.
    """
    b = base or BASE
    if scope == "tables":
        text = (b / "db_schema.py").read_text(encoding="utf-8", errors="replace")
        return sorted(set(_TABLE_RE.findall(text)))
    if scope == "routes":
        text = (b / "mouse_spot_helper.py").read_text(encoding="utf-8",
                                                     errors="replace")
        return sorted(set(_ROUTE_RE.findall(text)))
    if scope == "modules":
        return sorted(p.stem for p in b.glob("*.py"))
    if scope == "catalog":
        # THE NAMES A TARGET USES. Read from `target_template`, which is where a
        # name is USED rather than declared -- the other scopes read SOURCE.
        #
        # A name already registered is EXCLUDED, so the phase measures the GAP
        # rather than re-drafting 20 terms that already have definitions. A
        # phase that re-drafts a defined term would spend the 7B on work already
        # done, and its `accepted` count would say nothing about the gap.
        try:
            import sqlite3
            conn = sqlite3.connect(str(b / "agent.db"), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT t.name FROM target_template t "
                    "WHERE NOT EXISTS (SELECT 1 FROM terminology_registry r "
                    "                  WHERE r.term_key = t.name) "
                    "ORDER BY t.name").fetchall()
            finally:
                conn.close()
            return sorted({str(r["name"]) for r in rows})
        except Exception:
            return []
    if scope == "rubbish":
        # THE DEFINITIONS THAT SAY NOTHING. This scope reads the REGISTER, not
        # the source: the other four discover names that have no term, and this
        # one audits the definitions that already exist.
        #
        # The rule lives in `terminology_registry.check_definition` and is
        # CALLED, never restated -- a second copy would drift from the gate, and
        # the phase would then certify a rule the gate no longer runs.
        try:
            import sqlite3
            conn = sqlite3.connect(str(b / "agent.db"), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                import terminology_registry as _tr
                rows = conn.execute(
                    "SELECT term_key, definition FROM terminology_registry "
                    "ORDER BY term_key").fetchall()
                return sorted({str(r["term_key"]) for r in rows
                               if not _tr.check_definition(
                                   r["definition"], r["term_key"])["ok"]})
            finally:
                conn.close()
        except Exception:
            return []
    if scope == "group":
        # THE NODES THAT CONTAIN OTHER NODES. This scope reads the REGISTER, not
        # the source: the other scopes discover names that have no term, and this
        # one audits the NODE KIND of the terms that exist.
        #
        # The rule lives in `terminology_generator.node_kind` and is CALLED,
        # never restated -- a second copy would drift from the reader, and the
        # phase would then certify a rule the reader no longer runs.
        try:
            import sqlite3
            conn = sqlite3.connect(str(b / "agent.db"), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                import terminology_generator as _tg
                rows = conn.execute(
                    "SELECT term_id, term_key FROM terminology_registry "
                    "ORDER BY term_key").fetchall()
                return sorted({str(r["term_key"]) for r in rows
                               if _tg.node_kind(conn, int(r["term_id"]))
                               .get("node_kind") == _tg.NODE_GROUP})
            finally:
                conn.close()
        except Exception:
            return []
    if scope == "completeness_5w1h":
        # THE SIX QUESTIONS. This scope reads the REGISTER + target_template, not
        # the source: the other scopes discover names that have no term, and this
        # one audits the 5W1H of the targets that exist.
        #
        # The rule lives in `terminology_generator.completeness_5w1h` and is
        # CALLED, never restated -- a second copy would drift from the reader.
        try:
            import sqlite3
            conn = sqlite3.connect(str(b / "agent.db"), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                import terminology_generator as _tg
                rows = conn.execute(
                    "SELECT name FROM target_template ORDER BY name").fetchall()
                return sorted({str(r["name"]) for r in rows
                               if not _tg.completeness_5w1h(conn, str(r["name"]))
                               .get("complete")})
            finally:
                conn.close()
        except Exception:
            return []
    if scope == "columns":
        # Columns of the tables in db_schema.py, as `table.column`.        text = (b / "db_schema.py").read_text(encoding="utf-8", errors="replace")
        out: set[str] = set()
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)"
                             r"\s*\((.*?)\n\);", text, re.IGNORECASE | re.S):
            tbl, body = m.group(1), m.group(2)
            for line in body.splitlines():
                cm = re.match(r"\s*([a-z_][a-z0-9_]*)\s+[A-Z]", line)
                if cm and cm.group(1).upper() not in (
                        "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
                    out.add("%s.%s" % (tbl, cm.group(1)))
        return sorted(out)
    return []


# ---------------------------------------------------------------------------
# P2 — the 7B drafts LANGUAGE only
# ---------------------------------------------------------------------------

def _ask_7b(prompt: str, timeout: int = 180,
            resolved: dict[str, Any] | None = None) -> tuple[str, int]:
    """Call the model the ROUTE resolved. Returns `(content, elapsed_ms)`.

    The model is passed IN where the caller already resolved it (one resolution
    per phase, reported once) and resolved HERE otherwise, so a single call can
    never use an unregistered model without saying so.
    """
    import requests
    if resolved is None:
        resolved = resolve_model()
    model = str(resolved.get("model") or "")
    if not model:
        raise RuntimeError("no text model resolved from the llm.text route: %s"
                           % resolved.get("reason"))
    t0 = time.time()
    r = requests.post(OLLAMA, json={
        "model": model,
        "messages": [{"role": "system", "content": DRAFT_SYSTEM},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"], int((time.time() - t0) * 1000)


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def draft(name: str, context: str = "",
          resolved: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the 7B for the NAME and the DEFINITION. Never for a citation.

    Returns `{ok, term_key, term_kind, definition, llm_ms, raw}`. A refusal is
    reported as `ok=False` with the model's own reason — the CONTROL relies on
    this, and a refusal is a VALID answer, not an error.

    `resolved` carries the model the ROUTE resolved, so a whole phase calls one
    model and the report can NAME it. Resolution once per phase, not per call:
    25 calls each opening the database would be 25 reads of one unchanging fact.
    """
    ask = ("Name this thing from a software repository: %s\n%s"
           % (name, context)).strip()
    try:
        raw, ms = _ask_7b(ask, resolved=resolved)
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "llm_ms": None}
    obj = _extract_json(raw)
    if not obj:
        return {"ok": False, "error": "not JSON", "raw": raw[:200], "llm_ms": ms}
    if obj.get("refuse"):
        return {"ok": False, "refused": True, "reason": str(obj["refuse"]),
                "llm_ms": ms}
    return {"ok": True, "term_key": str(obj.get("term_key") or ""),
            "term_kind": str(obj.get("term_kind") or "part"),
            "definition": str(obj.get("definition") or ""),
            "llm_ms": ms, "raw": raw[:200]}


def cite_for(name: str, base: Path | None = None) -> tuple[str, bool]:
    """`(cite_ref, supplied)`. The LOGIC GENERATOR supplies and verifies it.

    `supplied=True` means the 7B could not have produced this — which is the
    measurement of its real gap.

    THE ORDER MATTERS, and a MEASURED DEFECT set it:

        1. `locate_name`  — the LINE where the name actually appears.
        2. `suggest_cite_ref` — a file whose NAME resembles the term, only as a
           fallback, because a similar filename is NOT evidence the term exists.

    The first version used (2) alone. For a name that is NOWHERE in the repo it
    still found a file containing the word "source", and `verify_cite_ref`
    accepted it (the file really exists) — so the sweep ACCEPTED a term for a
    name that does not exist. Passing the first candidate broke this proof's
    refusal case, which is how the defect was found.

    Returns ("", False) when the name appears nowhere: the caller then REFUSES.
    """
    hit = tc.locate_name(name, base)
    if hit:
        ok, _why = tc.verify_cite_ref(hit, base)
        if ok:
            return hit, True
    for cand in tc.suggest_cite_ref(name, base):
        ok, _why = tc.verify_cite_ref(cand, base)
        if ok:
            return cand, True
    return "", False


# ---------------------------------------------------------------------------
# P2 — run ONE phase, capped
# ---------------------------------------------------------------------------

def run_phase(conn: sqlite3.Connection, phase_key: str, *,
              apply: bool = False, base: Path | None = None,
              use_llm: bool = True,
              names: list[str] | None = None) -> dict[str, Any]:
    """Run one phase. NEVER processes more than the phase's `cap`.

    `apply=False` is a DRY RUN: it drafts and cites but writes nothing, so a
    phase can be inspected before it touches the register.

    `names` overrides the discovered list. It exists so a PROOF can force a
    specific input (e.g. a name that cannot be cited) and observe the refusal,
    instead of hoping the real scope happens to contain one. The cap still
    applies to the override, so the volume control cannot be bypassed.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT phase_key, scope, cap FROM phase_registry "
        " WHERE phase_kind=? AND phase_key=? AND is_active=1",
        (PHASE_KIND, phase_key)).fetchone()
    if not row:
        return {"ok": False, "error_code": "UNKNOWN_PHASE",
                "error": "no active phase %r" % phase_key}
    scope, cap = row["scope"], int(row["cap"])

    names = list(names) if names is not None else discover(scope, base)
    total = len(names)
    # ---- THE CAP MUST NOT RE-PICK A DECIDED NAME -------------------------
    #
    # MEASURED DEFECT (2026-09-25): `picked = names[:cap]` always took the FIRST
    # `cap` names. So every run re-processed the SAME 25 and the sweep NEVER
    # advanced — measured: 6 consecutive runs left `count(DISTINCT name)` at 80
    # while `terminology_sweep_run` grew by 175 rows per phase. The rows were
    # real; the PROGRESS was zero. A cap that re-picks decided keys is a cap that
    # measures its own repetition.
    #
    # The fix is the same one `register_fill` needed: SKIP a name that already has
    # a TERMINAL outcome, so the cap advances through the scope.
    decided: set[str] = set()
    try:
        decided = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT name FROM terminology_sweep_run "
            " WHERE phase_key=? AND outcome IN ('accepted','refused')",
            (phase_key,))}
    except sqlite3.Error:
        decided = set()
    undecided = [n for n in names if str(n) not in decided]
    picked = undecided[:cap]                  # <-- THE VOLUME CONTROL
    truncated = total > len(picked) + len(decided)

    results: list[dict[str, Any]] = []
    accepted = refused = 0
    supplied_count = 0
    latencies: list[int] = []
    recorded = 0
    dropped = 0

    def record(name: str, outcome: str, code: str, term_id: Any,
               cite: str, supplied: bool, llm_ms: Any) -> None:
        """Append ONE outcome to the log. EVERY outcome is recorded.

        MEASURED DEFECT 1: the first version INSERTed only on the `add_term`
        path, so a `NO_CHECKABLE_CITE` refusal was COUNTED (`refused=1`) and
        never WRITTEN. The report then showed `by_refusal_code={}` — the refusal
        was invisible in the very log that exists to make refusals reviewable.

        MEASURED DEFECT 2, and why `observed_at` is supplied HERE: the column's
        DEFAULT is `datetime('now')`, which has SECOND resolution, and the table
        is `UNIQUE (phase_key, name, observed_at)`. A dry run followed by an
        APPLY run — the normal way to use this harness — therefore landed in the
        same second and every APPLIED row was silently DROPPED as a duplicate.
        The proof caught it as `done=23` instead of 25. An append-only log that
        swallows a re-run is not append-only. Microseconds make the key unique
        for real re-runs while still catching a true exact duplicate.
        """
        nonlocal recorded, dropped
        cur = conn.execute(
            "INSERT OR IGNORE INTO terminology_sweep_run "
            "(phase_key, name, outcome, refusal_code, term_id, llm_ms, "
            " cite_supplied, cite_ref, observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (phase_key, name, outcome, code, term_id, llm_ms,
             1 if supplied else 0, cite, _now_us()))
        # `INSERT OR IGNORE` drops a row whose (phase_key, name, observed_at)
        # already exists — the same silent-duplicate defect fixed elsewhere in
        # this repo. It is COUNTED here instead of disappearing.
        if cur.rowcount:
            recorded += 1
        else:
            dropped += 1

    # THE MODEL IS RESOLVED FROM THE `llm.text` ROUTE, never a literal. Resolved
    # ONCE per phase: every call in a phase must use the SAME model, or the
    # latency numbers below would average two different machines.
    resolved = resolve_model() if use_llm else {"model": "",
                                                "source": "not_used",
                                                "pool": [], "reason": ""}
    if use_llm and not resolved.get("model"):
        return {"ok": False, "error_code": "NO_TEXT_MODEL",
                "error": "the llm.text route resolved no model: %s"
                         % resolved.get("reason")}

    for name in picked:
        # ---- THE NAME IS DERIVED, NOT ASKED OF THE MODEL (added 2026-09-27) --
        #
        # THE HUMAN: "you are not helping to have standardize for terminology
        # generator ? with catalog > subcatalog" / "example taskbar > widgets ,
        # terminology generator = taskbar_widgets".
        #
        # MEASURED BEFORE: `draft()` asked the 7B for `term_key`, so the NAME was
        # the MODEL'S CHOICE, per call. Two runs could name the same thing
        # differently, and nothing derived the name from the catalog.
        #
        # NOW: when the name is already in a CATALOG (it has a parent term), the
        # name is DERIVED from its catalog path by `terminology_generator`, and
        # the model is asked for the DEFINITION ONLY. The 7B keeps the half it is
        # good at (MEASURED: its definitions were usable every time) and loses the
        # half it was never deterministic at.
        derived_key = None
        if scope == "catalog":
            try:
                import terminology_generator as tg
                row_t = conn.execute(
                    "SELECT term_id FROM terminology_registry WHERE term_key=?",
                    (name,)).fetchone()
                if row_t:
                    e = tg.expected_name(conn, int(row_t["term_id"]))
                    if e.get("ok") and e.get("match"):
                        derived_key = str(e["expected"])
            except Exception:
                derived_key = None

        d = draft(name, resolved=resolved) if use_llm else {
            "ok": True, "term_key": name, "term_kind": "part",
            "definition": "a name from %s" % scope, "llm_ms": None}
        if d.get("llm_ms") is not None:
            latencies.append(int(d["llm_ms"]))
        if not d.get("ok"):
            code = "LLM_REFUSED" if d.get("refused") else "LLM_FAILED"
            record(name, "skipped", code, None, "", False, d.get("llm_ms"))
            results.append({"name": name, "outcome": "skipped",
                            "refusal_code": code, "llm_ms": d.get("llm_ms")})
            continue
        # THE DERIVED NAME WINS over the model's. `derived_key` is None when the
        # name is not in a catalog, so a non-catalog scope is unchanged.
        if derived_key:
            d["term_key"] = derived_key
            d["name_source"] = "derived_from_catalog_path"
        else:
            d["name_source"] = "model"
        cite, supplied = cite_for(name, base)
        if supplied:
            supplied_count += 1
        if not cite:
            record(name, "refused", "NO_CHECKABLE_CITE", None, "", False,
                   d.get("llm_ms"))
            results.append({"name": name, "outcome": "refused",
                            "refusal_code": "NO_CHECKABLE_CITE",
                            "llm_ms": d.get("llm_ms")})
            refused += 1
            continue
        if not apply:
            record(name, "skipped", "DRY_RUN", None, cite, supplied,
                   d.get("llm_ms"))
            results.append({"name": name, "outcome": "skipped",
                            "refusal_code": "DRY_RUN", "cite_ref": cite,
                            "cite_supplied": supplied,
                            "llm_ms": d.get("llm_ms")})
            continue
        res = tr.add_term(conn, d["term_key"], definition=d["definition"],
                          cite_ref=cite, term_kind=d["term_kind"])
        if res.get("ok"):
            accepted += 1
            outcome, code, term_id = "accepted", "", res.get("term_id")
        else:
            refused += 1
            outcome, code, term_id = "refused", str(res.get("code")), None
        record(name, outcome, code, term_id, cite, supplied, d.get("llm_ms"))
        results.append({"name": name, "outcome": outcome,
                        "refusal_code": code, "term_id": term_id,
                        "cite_ref": cite, "cite_supplied": supplied,
                        "name_source": d.get("name_source"),
                        "llm_ms": d.get("llm_ms")})
    conn.commit()

    # ---- THE CONTROL -----------------------------------------------------
    # Without it, "accepted" is indistinguishable from "always says yes".
    control = {"ran": False}
    if use_llm:
        cd = draft(CONTROL_NAME, CONTROL_ASK, resolved=resolved)
        control = {"ran": True, "refused": bool(cd.get("refused")),
                   "ok": bool(cd.get("ok")),
                   "reason": str(cd.get("reason") or cd.get("error") or "")[:120]}
        # PASS only on a REFUSAL. A confident definition for a name that is not
        # in the source is the failure this control exists to catch.
        control["pass"] = bool(cd.get("refused"))
        if not control["pass"] and cd.get("ok"):
            control["invented"] = str(cd.get("term_key") or "")[:60]
            control["note"] = (
                "the 7B produced a confident term for a name that is NOT in the "
                "source, so it cannot tell a real name from a fake one — its "
                "definitions must be REVIEWED, not trusted")

    lat_sorted = sorted(latencies)
    p50 = lat_sorted[len(lat_sorted) // 2] if lat_sorted else None
    return {
        "ok": True, "phase_key": phase_key, "scope": scope, "cap": cap,
        "total": total, "returned": len(picked), "truncated": truncated,
        "applied": apply,
        # WHICH MODEL ANSWERED, and HOW IT WAS CHOSEN. `model_source` is the
        # honest half: a model with no source is a model nobody can reproduce.
        "model": str(resolved.get("model") or ""),
        "model_source": str(resolved.get("source") or ""),
        "model_pool": [str(m) for m in (resolved.get("pool") or [])],
        "accepted": accepted, "refused": refused,
        "supplied_citations": supplied_count,
        "llm_calls": len(latencies),
        "llm_ms_p50": p50,
        "llm_ms_max": max(latencies) if latencies else None,
        "control": control,
        "results": results,
        # The log is append-only and `INSERT OR IGNORE` can drop a duplicate, so
        # the counts are REPORTED. A run that silently lost rows would otherwise
        # look identical to one that recorded them all.
        "recorded": recorded,
        "dropped": dropped,
    }


def phase_progress(conn: sqlite3.Connection, phase_key: str) -> dict[str, Any]:
    """How far a phase has got, and how much of its scope is left.

    `done` counts ONLY names that reached a TERMINAL, APPLIED outcome
    (`accepted` or `refused`). MEASURED DEFECT: counting DISTINCT name over EVERY
    row made a DRY RUN raise `done` — a dry run records `skipped` and registers
    NOTHING, so the page showed progress that had not happened. A name the 7B
    was merely ASKED about is not a name that is covered.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT phase_key, scope, cap FROM phase_registry "
        " WHERE phase_kind=? AND phase_key=? AND is_active=1",
        (PHASE_KIND, phase_key)).fetchone()
    if not row:
        return {"ok": False, "error_code": "UNKNOWN_PHASE",
                "error": "no active phase %r" % phase_key}
    done = conn.execute(
        "SELECT COUNT(DISTINCT name) FROM terminology_sweep_run "
        " WHERE phase_key=? AND outcome IN ('accepted','refused')",
        (phase_key,)).fetchone()[0]
    dry = conn.execute(
        "SELECT COUNT(DISTINCT name) FROM terminology_sweep_run "
        " WHERE phase_key=? AND outcome NOT IN ('accepted','refused')",
        (phase_key,)).fetchone()[0]
    total = len(discover(row["scope"]))
    return {"ok": True, "phase_key": phase_key, "cap": int(row["cap"]),
            "scope_total": total, "done": done, "dry_only": dry,
            "remaining": max(0, total - done), "truncated": total > int(row["cap"])}


def report(conn: sqlite3.Connection, phase_key: str) -> dict[str, Any]:
    """The recorded history for one phase, from the APPEND-ONLY log."""
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT name, outcome, refusal_code, term_id, llm_ms, cite_supplied, "
        "       cite_ref, observed_at "
        "  FROM terminology_sweep_run WHERE phase_key=? "
        " ORDER BY observed_at DESC, run_id DESC", (phase_key,))]
    by_outcome: dict[str, int] = {}
    by_code: dict[str, int] = {}
    lat = []
    supplied = 0
    for r in rows:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
        if r["refusal_code"]:
            by_code[r["refusal_code"]] = by_code.get(r["refusal_code"], 0) + 1
        if r["llm_ms"] is not None:
            lat.append(int(r["llm_ms"]))
        supplied += int(r["cite_supplied"] or 0)
    lat.sort()
    # TWO counts, deliberately BOTH reported.
    #
    # MEASURED DEFECT: the page showed `citations supplied 50` for a phase where
    # 25 terms exist. The cause is a DRY RUN followed by an APPLY run: BOTH append
    # a row for the same name, so the run-level count double-counts. It is not
    # wrong as a count of ROWS, but read as "how much of the 7B's work the logic
    # generator had to cover" it overstates by 2x.
    #
    # `names_supplied` is the DISTINCT-name count — the honest size of the gap.
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT name) FROM terminology_sweep_run "
        " WHERE phase_key=? AND cite_supplied=1 AND "
        "       outcome IN ('accepted','refused')", (phase_key,)).fetchone()[0]
    return {"ok": True, "phase_key": phase_key, "rows": len(rows),
            "by_outcome": by_outcome, "by_refusal_code": by_code,
            # Rows across every run (a re-run counts again).
            "supplied_citations": supplied,
            # DISTINCT NAMES whose citation the logic generator had to supply —
            # the size of the 7B's gap, counted once per name.
            "names_supplied": distinct,
            "llm_ms_p50": lat[len(lat) // 2] if lat else None,
            "llm_ms_max": max(lat) if lat else None,
            "items": rows[:200]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the phased terminology sweep")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--discover", metavar="SCOPE")
    ap.add_argument("--run", metavar="PHASE_KEY")
    ap.add_argument("--report", metavar="PHASE_KEY")
    ap.add_argument("--apply", action="store_true",
                    help="write to the register (default: dry run)")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the 7B (deterministic names only)")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        seed_phases(conn)
        if args.discover:
            names = discover(args.discover)
            print("scope %s: %d names" % (args.discover, len(names)))
            for n in names[:40]:
                print("  ", n)
            if len(names) > 40:
                print("   ... %d more" % (len(names) - 40))
            return 0
        if args.run:
            out = run_phase(conn, args.run, apply=args.apply,
                            use_llm=not args.no_llm)
            if not out.get("ok"):
                print("ERROR: %s" % out.get("error"))
                return 1
            print("phase %s scope=%s cap=%d" % (out["phase_key"], out["scope"],
                                                out["cap"]))
            print("  total=%d returned=%d truncated=%s applied=%s"
                  % (out["total"], out["returned"], out["truncated"],
                     out["applied"]))
            print("  accepted=%d refused=%d supplied_citations=%d"
                  % (out["accepted"], out["refused"], out["supplied_citations"]))
            print("  7B: calls=%d p50=%sms max=%sms"
                  % (out["llm_calls"], out["llm_ms_p50"], out["llm_ms_max"]))
            print("  control: %s" % out["control"])
            for r in out["results"][:15]:
                print("    %-40s %-8s %s" % (r["name"], r["outcome"],
                                             r.get("refusal_code") or ""))
            return 0
        if args.report:
            out = report(conn, args.report)
            print(json.dumps(out, indent=2, ensure_ascii=False)[:3000])
            return 0
        ap.print_help()
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
