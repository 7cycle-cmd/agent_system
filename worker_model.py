"""worker_model.py -- a WORKER's MODEL and ENVIRONMENT, MEASURED from evidence.

THE USER'S DEFINITION (verbatim, 2026-09-24):

    "worker list in image, they are worker at VS code (is IDE), 豆包 is worker for
     under my request to have the plan under point form format (answer my question
     and reply by her view) at 豆包 browser , she is not IDE, gemini is worker for
     have the plan under point form format (answer my question and reply by his
     view) at microsoft edge browser,  DeepSeek is worker for QC at google chrome
     browser"

    "worker is LLM 7B-intract, not by other model, did you can measure it too"

So a WORKER is **model + ENVIRONMENT + task**. MEASURED against that definition:

| dimension | recorded? | measurement |
|---|---|---|
| model | **NO** | `worker_registry` has NO model column |
| environment | **PARTIAL** | `identity_registry.channel` exists but holds ONLY `vscode` (52) + `runtime` (1); NO `doubao`/`edge`/`chrome`/`gemini` |
| task | YES | `identity_registry.workflow_id` |

THE MODEL IS STILL MEASURABLE, from evidence rather than a column:
`skill_prompt_inference` holds 19849 rows, ALL `qwen2.5vl:7b` -- ONE model. So the
worker's model is a MEASUREMENT, not a guess.

NOTHING IS DEFAULTED. A worker whose model cannot be measured is reported
`UNKNOWN`, because a default would make an unmeasured worker look measured --
the same defect as a route whose health was never measured.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The tables this module READS. Named ONCE.
WORKER_TABLE = "worker_registry"
INFERENCE_TABLE = "skill_prompt_inference"
IDENTITY_TABLE = "identity_registry"
CONSULTANT_TEAM = "consultant_team"
CONSULTANT_SKILL = "consultant_skill"

# The three dimensions a worker is made of, per the user's definition.
DIMENSIONS = ("model", "environment", "task")

UNKNOWN = "UNKNOWN"


class WorkerModelError(Exception):
    """Raised when a worker's model cannot be measured."""


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or os.path.join(BASE_DIR, "agent.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone())


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(conn, table):
        return []
    return [str(r["name"]) for r in conn.execute("PRAGMA table_info(%s)" % table)]


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


# ---------------------------------------------------------------------------
# the MODEL dimension
# ---------------------------------------------------------------------------
def model_columns(conn: sqlite3.Connection) -> dict[str, Any]:
    """Does `worker_registry` have a column that names a MODEL?

    MEASURED: it does NOT. The columns are `worker_id, worker_key, name,
    worker_type, capability_ref, fallback_order, physical_path, uses_text,
    status, cite_ref, is_active, created_at, updated_at, role_id`. So the model
    is NOT recorded on the worker, and this is REPORTED rather than worked around.
    """
    cols = _columns(conn, WORKER_TABLE)
    hits = [c for c in cols if "model" in c.lower() or "llm" in c.lower()]
    return {"table": WORKER_TABLE, "columns": cols, "model_columns": hits,
            "has_model_column": bool(hits),
            "why": ("a worker's model is not recorded on the worker, so it can "
                    "only be MEASURED from the inference evidence")}


def models_in_use(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every model that ACTUALLY ran, with its counts. The evidence.

    MEASURED: `skill_prompt_inference` holds ONE model (`qwen2.5vl:7b`, 19849
    rows), and `proof_run` holds TWO (`qwen2.5:7b-instruct` 2885,
    `deepseek-v4-flash-0731` 110). The second belongs to NO worker.
    """
    out: dict[str, Any] = {"inference": [], "proof_run": []}
    if _table_exists(conn, INFERENCE_TABLE):
        out["inference"] = [
            {"model": r["model"], "n": int(r["n"])} for r in conn.execute(
                "SELECT model, COUNT(*) n FROM %s GROUP BY model "
                "ORDER BY n DESC" % INFERENCE_TABLE)]
    if _table_exists(conn, "proof_run"):
        out["proof_run"] = [
            {"model": r["model"], "n": int(r["n"]), "wins": int(r["w"] or 0)}
            for r in conn.execute(
                "SELECT model, COUNT(*) n, SUM(win) w FROM proof_run "
                "GROUP BY model ORDER BY n DESC")]
    out["inference_models"] = [e["model"] for e in out["inference"]]
    out["proof_models"] = [e["model"] for e in out["proof_run"]]
    out["one_inference_model"] = len(out["inference"]) == 1
    return out


def worker_model(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """The MODEL a worker uses, MEASURED from evidence, or `UNKNOWN`.

    The chain is: worker -> `capability_ref` -> the skill -> the model that ran
    that skill. MEASURED: `W-S-03-A`'s `capability_ref` is
    `mouse_spot_helper.mouse_spot_verify`, and `skill_prompt_inference` holds
    19720 rows for `skill_key='mouse_spot_verify'`, ALL `qwen2.5vl:7b`.

    A worker whose capability names no skill with inference rows is `UNKNOWN` --
    NOT defaulted to the one model in use, because "this worker's model was not
    measured" and "this worker uses the only model" are different facts.
    """
    key = str(worker_key or "").strip()
    if not key:
        return {"ok": False, "worker_key": key, "reason": "no worker_key given"}
    row = conn.execute(
        "SELECT worker_id, worker_key, worker_type, capability_ref, status "
        "FROM %s WHERE worker_key=?" % WORKER_TABLE, (key,)).fetchone()
    if row is None:
        return {"ok": False, "worker_key": key,
                "reason": "no %s row for %r" % (WORKER_TABLE, key)}
    cap = str(row["capability_ref"] or "")
    # The skill key is the part after the module prefix.
    skill = cap.split(".")[-1] if "." in cap else cap
    models: list[dict[str, Any]] = []
    if _table_exists(conn, INFERENCE_TABLE) and skill:
        models = [
            {"model": r["model"], "n": int(r["n"])} for r in conn.execute(
                "SELECT model, COUNT(*) n FROM %s WHERE skill_key=? "
                "GROUP BY model ORDER BY n DESC" % INFERENCE_TABLE, (skill,))]
    if not models:
        return {"ok": True, "worker_key": key, "worker_id": int(row["worker_id"]),
                "worker_type": row["worker_type"], "capability_ref": cap,
                "skill_key": skill, "model": UNKNOWN, "models": [],
                "why": ("no inference rows for skill %r, so this worker's model "
                        "was NOT measured" % skill)}
    return {"ok": True, "worker_key": key, "worker_id": int(row["worker_id"]),
            "worker_type": row["worker_type"], "capability_ref": cap,
            "skill_key": skill, "model": models[0]["model"], "models": models,
            "why": ("measured from %s rows for skill %r"
                    % (models[0]["n"], skill))}


def all_workers(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every worker with its MEASURED model, plus the ones that are UNKNOWN."""
    rows = conn.execute(
        "SELECT worker_key FROM %s ORDER BY worker_id" % WORKER_TABLE).fetchall()
    out = [worker_model(conn, str(r["worker_key"])) for r in rows]
    return {"workers": out, "total": len(out),
            "measured": sum(1 for e in out if e.get("model") not in (None, UNKNOWN)),
            "unknown": [e["worker_key"] for e in out
                        if e.get("model") == UNKNOWN],
            "model_column": model_columns(conn)}


# ---------------------------------------------------------------------------
# the ENVIRONMENT dimension
# ---------------------------------------------------------------------------
def environments(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which ENVIRONMENTS are recorded, and which are NOT.

    MEASURED (2026-09-25), AND THIS READ THE WRONG COLUMN: it read
    `identity_registry.channel`, which holds `local_pc` -- a CHANNEL, not an
    environment. THE USER: "環境 is 環境!!!! not related to channel". The
    environment observation is `chat_main.ide` (`VS Code`), and the DECLARED
    environment vocabulary is `working_environment.product`.

    The user names FOUR workers in FOUR environments (VS Code, 豆包 browser,
    Microsoft Edge, Google Chrome). The user's own list is carried here as the
    EXPECTATION, so the gap is a MEASUREMENT against a stated expectation
    rather than an opinion.

    The match is CASE-INSENSITIVE and also tries the product's first word, so
    `vscode` matches `VS Code` -- a name comparison that is case-sensitive
    would report a recorded environment as missing, which is a FALSE RED.

    MEASURED (2026-09-25): the user's list is ROMANIZED (`doubao`) while the
    DECLARED product is `豆包`, so a plain substring test reported `doubao` as
    MISSING even though the environment IS declared. The alias is carried as
    DATA (`ALIASES`), so the comparison is a measurement rather than a guess.
    """
    expected = ["vscode", "doubao", "edge", "chrome"]
    # The user's romanized name -> the DECLARED product it names. Carried as
    # data so the mapping is checkable, not inferred from a spelling.
    aliases = {"doubao": "豆包", "vscode": "vs code", "edge": "edge",
               "chrome": "chrome"}
    declared: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    if _table_exists(conn, "working_environment"):
        declared = [{"product": r["product"], "display": r["display"],
                     "n": 1} for r in conn.execute(
            "SELECT product, display FROM working_environment "
            "WHERE is_active=1 ORDER BY environment_id")]
    if _table_exists(conn, IDENTITY_TABLE) and _table_exists(conn, "chat_main"):
        observed = [{"ide": r["ide"], "n": int(r["n"])} for r in conn.execute(
            "SELECT cm.ide, COUNT(*) n FROM %s i JOIN chat_main cm ON "
            "cm.session_id = i.session_id WHERE i.is_active=1 AND "
            "cm.ide IS NOT NULL GROUP BY cm.ide ORDER BY n DESC"
            % IDENTITY_TABLE)]
    found = declared or observed
    # A name matches when it is a case-insensitive SUBSTRING of the product, so
    # `vscode` matches `VS Code` and `edge` matches `Microsoft Edge`.
    present = {str(e.get("product") or e.get("ide") or "").lower()
               for e in found}

    def _match(name: str) -> str:
        """The DECLARED product `name` names, or "" when nothing matches."""
        for p in present:
            if name in p.replace(" ", "") or name in p \
                    or aliases.get(name, "") in p:
                return p
        return ""

    # The pairing is REPORTED, so a caller can see WHICH product answered for
    # each expected name -- a bare `missing` list cannot show that.
    matched = {e: _match(e) for e in expected}
    missing = [e for e in expected if not matched[e]]
    return {"expected": expected, "found": found, "declared": declared,
            "observed": observed, "present": sorted(present),
            "aliases": aliases, "matched": matched, "missing": missing,
            "why": ("the user names four workers in four environments; the "
                    "DECLARED ones are read from working_environment.product "
                    "and the OBSERVED ones from chat_main.ide -- NOT from "
                    "identity_registry.channel, which holds a CHANNEL")}


def environment_columns(conn: sqlite3.Connection) -> dict[str, Any]:
    """Does `worker_registry` have a column that names an ENVIRONMENT?"""
    cols = _columns(conn, WORKER_TABLE)
    hits = [c for c in cols
            if any(k in c.lower() for k in ("env", "channel", "browser", "ide"))]
    return {"table": WORKER_TABLE, "environment_columns": hits,
            "has_environment_column": bool(hits),
            "why": ("a worker's environment is not recorded on the worker; it is "
                    "only visible on the IDENTITY row")}


# ---------------------------------------------------------------------------
# the CONSULTANT team
# ---------------------------------------------------------------------------
def consultant_team(conn: sqlite3.Connection) -> dict[str, Any]:
    """The consultant team: the framework, and whether any skill is attached.

    MEASURED: `consultant_team` has 1 row (`software_rd`, with `expertise_json`
    and `probe_json`), and `consultant_skill` is EMPTY (0 rows). So the team
    exists and NOTHING is attached to it.
    """
    teams: list[dict[str, Any]] = []
    if _table_exists(conn, CONSULTANT_TEAM):
        teams = [{"team_key": r["team_key"], "name": r["name"],
                  "expertise": r["expertise_json"], "source_ref": r["source_ref"],
                  "worker_key": r["worker_key"], "is_active": r["is_active"]}
                 for r in conn.execute("SELECT * FROM %s" % CONSULTANT_TEAM)]
    skills = _count(conn, "SELECT COUNT(*) FROM %s" % CONSULTANT_SKILL) \
        if _table_exists(conn, CONSULTANT_SKILL) else 0
    return {"teams": teams, "team_count": len(teams), "skill_count": skills,
            "empty": skills == 0,
            "why": ("a team with no attached skill is a framework with nothing "
                    "in it")}


# ---------------------------------------------------------------------------
# the PAIRING (worker x environment) -- the user's "1 table is not enough"
# ---------------------------------------------------------------------------
def pairing_matrix(conn: sqlite3.Connection) -> dict[str, Any]:
    """The worker x environment MATRIX, MEASURED. The user's Q6.

    THE USER (2026-09-24): "you have table, but the table not complete or 1 table
    is not enough as need to match environment too".

    MEASURED, and the user is RIGHT: NO table pairs a worker with an environment.

      * `identity_registry` has BOTH columns, but it is PER-SESSION: 53 rows, 53
        distinct `session_id`, only 3 distinct (worker_id, channel) pairs. A
        per-session observation is not a DECLARED binding.
      * `worker_identity_binding` has a worker but no environment column.
      * `worker_mode` has a worker but no environment column.
      * `worker_registry` has no environment column.
      * `channel_registry` has an environment but no worker column.

    And the TWO sources for "environment" DISAGREE on EVERY value:
    `working_environment.display` (DECLARED) holds `IDE > VS Code > chat`,
    `Browser > 豆包 > chat`, ...; `identity_registry.channel` (OBSERVED) holds
    `local_pc`. No value appears in both.

    MEASURED (2026-09-25): this read `channel_registry.channel_key`, so the
    matrix columns were CHANNELS. THE USER: "環境 is 環境!!!! not related to
    channel". The columns are now ENVIRONMENTS (`working_environment`).

    A cell with no value is reported EMPTY, never assumed.
    """
    workers = [{"worker_id": int(r["worker_id"]), "worker_key": r["worker_key"]}
               for r in conn.execute(
                   "SELECT worker_id, worker_key FROM %s ORDER BY worker_id"
                   % WORKER_TABLE)]
    declared = [str(r["display"]) for r in conn.execute(
        "SELECT display FROM working_environment WHERE is_active=1 "
        "ORDER BY environment_id")] if _table_exists(conn, "working_environment") else []
    observed: list[dict[str, Any]] = []
    pairs: set[tuple[int, str]] = set()
    if _table_exists(conn, IDENTITY_TABLE):
        observed = [{"channel": r["channel"], "n": int(r["n"])} for r in
                    conn.execute("SELECT channel, COUNT(*) n FROM %s "
                                 "GROUP BY channel ORDER BY n DESC"
                                 % IDENTITY_TABLE)]
        pairs = {(int(r["worker_id"]), str(r["channel"])) for r in conn.execute(
            "SELECT DISTINCT worker_id, channel FROM %s" % IDENTITY_TABLE)}

    # The matrix is over the DECLARED environments, because a declared list is
    # what a binding can be checked against.
    matrix: list[dict[str, Any]] = []
    for w in workers:
        cells = [{"environment": e,
                  "paired": (w["worker_id"], e) in pairs}
                 for e in declared]
        matrix.append({"worker_key": w["worker_key"], "worker_id": w["worker_id"],
                       "cells": cells,
                       "paired_count": sum(1 for c in cells if c["paired"])})
    total_cells = len(workers) * len(declared)
    filled = sum(e["paired_count"] for e in matrix)

    # The vocabulary diff: which values appear in ONE source only.
    declared_set = set(declared)
    observed_set = {str(o["channel"]) for o in observed}
    return {
        "workers": workers, "declared_environments": declared,
        "observed_environments": observed,
        "matrix": matrix,
        "total_cells": total_cells, "filled_cells": filled,
        "empty_cells": total_cells - filled,
        "workers_with_no_pairing": [e["worker_key"] for e in matrix
                                    if e["paired_count"] == 0],
        "environments_never_paired": [
            e for e in declared
            if not any((w["worker_id"], e) in pairs for w in workers)],
        "declared_only": sorted(declared_set - observed_set),
        "observed_only": sorted(observed_set - declared_set),
        "vocabulary_agrees": bool(declared_set & observed_set),
        "per_session": {
            "rows": _count(conn, "SELECT COUNT(*) FROM %s" % IDENTITY_TABLE)
            if _table_exists(conn, IDENTITY_TABLE) else 0,
            "distinct_pairs": len(pairs),
            "distinct_sessions": _count(
                conn, "SELECT COUNT(DISTINCT session_id) FROM %s" % IDENTITY_TABLE)
            if _table_exists(conn, IDENTITY_TABLE) else 0,
        },
        "why": ("NO table pairs a worker with an environment: the only table with "
                "both columns is PER-SESSION, and the two environment "
                "vocabularies share NO value"),
    }


def report(conn: sqlite3.Connection) -> dict[str, Any]:
    """The whole answer, as one dict."""
    return {
        "definition": "a WORKER is model + environment + task",
        "dimensions": list(DIMENSIONS),
        "model": {"column": model_columns(conn), "in_use": models_in_use(conn),
                  "workers": all_workers(conn)},
        "environment": {"column": environment_columns(conn),
                        "recorded": environments(conn)},
        "pairing": pairing_matrix(conn),
        "consultant": consultant_team(conn),
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--workers" in args:
            print(json.dumps(all_workers(conn), indent=2, ensure_ascii=False))
        elif "--environments" in args:
            print(json.dumps(environments(conn), indent=2, ensure_ascii=False))
        elif "--consultant" in args:
            print(json.dumps(consultant_team(conn), indent=2, ensure_ascii=False))
        elif "--pairing" in args:
            print(json.dumps(pairing_matrix(conn), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(report(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
