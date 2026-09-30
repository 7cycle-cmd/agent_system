# -*- coding: utf-8 -*-
"""enforcement_audit.py — DISCOVER a rule that is stated but not enforced.

WHY THIS EXISTS
---------------
MEASURED 2026-09-29. Twice in one session a defect was found by a HUMAN noticing,
not by a mechanism:

  * `measurement_scope.assert_scoped` — a write-site gate with **0 production
    callers** (only its own proof and the TDD probe call it).
  * the `prune` principle — enforced in **1 of 2** child tables.

The human named the class exactly:

    「一個規則／機制函數**存在**，但佢嘅**生產 write site 冇實際 call 佢**
     （只有 proof／test 叫佢）→ 就係「stated but not enforced」。」
    「呢個特徵係**機器可檢測**嘅，唔使靠人望。」

This module is that machine. A rule DECLARES where it must be enforced
(`must_enforce_at`); the audit checks whether those sites actually CALL it.

TWO INDEPENDENT READERS (QC-10 — the `SAME_READER` rule)
--------------------------------------------------------
An audit that only re-reads the rule's own declaration shares the declaration's
blind spot: a WRONG declaration and a WRONG audit would agree while both are
wrong. So the audit runs TWO readers that differ in METHOD and SOURCE:

  * Reader A — the DECLARED-SITE scan: reads `must_enforce_at`, checks those
    files call the rule.
  * Reader B — the REPO-WIDE scan: does NOT read the declaration at all; it
    scans every product `.py` for a call to the rule.

| status | meaning |
|---|---|
| `ENFORCED` | the declared site calls it AND the repo-wide scan finds it |
| `UNWIRED` | the declared site does NOT call it AND the repo-wide scan finds NOTHING |
| `MISPLACED` | the declared site does NOT call it BUT the repo-wide scan finds it elsewhere — the DECLARATION is wrong |
| `UNDECLARED` | the rule has no `must_enforce_at` — nobody stated where it lands |

THE CALLER CHECK IS AN `ast` CALL, NOT A SUBSTRING. A mention in a comment or a
docstring is not a call; a substring check would report `ENFORCED` for a rule
that is only TALKED about.

THE FOLLOW-UP IS A GATE, NOT A LOG (QC-09)
------------------------------------------
A finding that is only printed is a new "stated but not enforced". So a finding
is TRACKED in `enforcement_finding` (`OPEN`/`CLOSED`), and `main()` EXITS
NON-ZERO while any finding is `OPEN` — so the sweep fails and a delivery cannot
be marked done while a finding is open.

WHAT THIS DOES NOT DO (stated, not hidden)
------------------------------------------
It catches ONE class: a rule that DECLARES where it must be enforced and is not
called there. It CANNOT catch a defect nobody has stated a rule for — a brand
new hole with no rule at all. The human said this explicitly and it is repeated
here so nobody later treats this as a universal detector.

Usage:
  python enforcement_audit.py            # audit, exit non-zero if any OPEN
  python enforcement_audit.py --report   # audit, always exit 0 (read-only view)
"""
from __future__ import annotations

import argparse
import ast
import os
import pathlib
import sqlite3
import sys
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "agent.db"

# --- the status vocabulary -------------------------------------------------
ENFORCED = "ENFORCED"
UNWIRED = "UNWIRED"
MISPLACED = "MISPLACED"
UNDECLARED = "UNDECLARED"

# The statuses that are a FINDING (must be tracked and must fail the run).
FINDING_STATUSES = (UNWIRED, MISPLACED, UNDECLARED)

OPEN = "OPEN"
CLOSED = "CLOSED"

# Directories that hold no PRODUCT code — the same convention `entity_backfill`
# and `hardcode_scan` use, so the three agree about what "the repo's code" is.
SKIP_DIRS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "dist", "out",
    "chrome_cdp_profile", ".vite", ".pytest_cache", "site-packages",
    "hb_snapshots", "debug_shots", "helper_watchdog_snaps", "evidence",
    "evidence_final", "evidence_steps", "fault_evidence", "qc_evidence",
    "hko_proof", "skills", "docs",
})
# A leading `_` marks a one-shot SESSION script (a proof, a diagnostic). A proof
# CALLS the rule it proves, so counting proofs would report every rule ENFORCED
# and the audit would certify nothing. This is the `measurement_self_pollution`
# defect: the instrument must not read itself.
SESSION_PREFIX = "_"


# ---------------------------------------------------------------------------
# Schema — the module owns its DDL (the `skill_factor.py` precedent)
# ---------------------------------------------------------------------------

ENFORCEMENT_RULE_DDL = """
CREATE TABLE IF NOT EXISTS enforcement_rule (
    rule_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key        TEXT    NOT NULL UNIQUE,
    rule_module     TEXT    NOT NULL,
    -- WHERE the rule MUST be called. Comma-separated repo-relative paths.
    -- EMPTY is a FINDING (`UNDECLARED`): nobody stated where it lands.
    must_enforce_at TEXT    NOT NULL DEFAULT '',
    enforcement_proof TEXT  NOT NULL DEFAULT '',
    cite_ref        TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

ENFORCEMENT_FINDING_DDL = """
CREATE TABLE IF NOT EXISTS enforcement_finding (
    finding_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key    TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    detail      TEXT    NOT NULL DEFAULT '',
    owner       TEXT    NOT NULL DEFAULT 'unassigned',
    note        TEXT    NOT NULL DEFAULT '',
    first_seen  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (rule_key)
);
"""

# THE SEED — the rules MEASURED this session. A seed populates an empty table and
# is then never authoritative again (the `ticket_store.DEFAULT_SERVICES` pattern).
#
# `prune_tdd_cases` / `prune_fields` ARE called by `skill_registrar` — the
# positive example, so the audit is not a detector that only ever fires.
# `assert_scoped` has NO declared write site — the `UNDECLARED` flag the human
# named, and the real defect measured this session.
SEED_RULES: tuple[tuple[str, str, str, str, str], ...] = (
    ("prune_tdd_cases", "skill_contract_store", "skill_registrar.py",
     "_proof_measurement_cross_check.py", "skill_contract_store.py:1507"),
    ("prune_fields", "skill_contract_store", "skill_registrar.py",
     "_proof_measurement_cross_check.py", "skill_contract_store.py:1530"),
    ("assert_scoped", "measurement_scope", "skill_factor.py",
     "_proof_measurement_scope.py", "skill_factor.py:record_proof"),
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ENFORCEMENT_RULE_DDL)
    conn.executescript(ENFORCEMENT_FINDING_DDL)
    conn.commit()


def seed_rules(conn: sqlite3.Connection) -> int:
    """Populate an EMPTY `enforcement_rule` from the seed. Idempotent.

    ALSO repairs a rule whose `must_enforce_at` is EMPTY while the seed now
    names one. MEASURED 2026-09-29: `assert_scoped` was seeded with an empty
    site (nobody had stated where it lands), then its write site was found and
    wired. A seed that only fills an empty TABLE would leave that row
    `UNDECLARED` forever — the finding could never close. Filling an empty
    COLUMN is not overwriting a stated site, so a site a human set by hand is
    never touched.
    """
    ensure_schema(conn)
    n = conn.execute("SELECT COUNT(*) FROM enforcement_rule").fetchone()[0]
    if n:
        # Repair ONLY a row the seed itself left empty. The cite_ref guard is
        # what makes this safe: a proof (or a human) that DELIBERATELY inserts an
        # empty site uses its own cite_ref, so it is never overwritten. Without
        # the guard, QC-03's planted empty `assert_scoped` row was rewritten to
        # ENFORCED and the UNDECLARED case could never be tested.
        repaired = 0
        for key, mod, sites, proof, cite in SEED_RULES:
            if not sites:
                continue
            cur = conn.execute(
                "UPDATE enforcement_rule SET must_enforce_at=? "
                "WHERE rule_key=? AND TRIM(must_enforce_at)='' AND cite_ref=?",
                (sites, key, cite))
            repaired += cur.rowcount
        if repaired:
            conn.commit()
        return 0
    for key, mod, sites, proof, cite in SEED_RULES:
        conn.execute(
            "INSERT INTO enforcement_rule (rule_key, rule_module, "
            "must_enforce_at, enforcement_proof, cite_ref) VALUES (?,?,?,?,?)",
            (key, mod, sites, proof, cite))
    conn.commit()
    return len(SEED_RULES)


# ---------------------------------------------------------------------------
# Reader A — the DECLARED-SITE scan
# ---------------------------------------------------------------------------

def _calls_in_file(path: pathlib.Path, rule_key: str) -> bool:
    """Does this file CALL `rule_key`? An `ast` CALL, not a substring.

    A mention in a comment or docstring is NOT a call. MEASURED: a substring
    check would report `ENFORCED` for a rule that is only TALKED about — the
    same "check on prose is not a check on code" defect already recorded.
    """
    try:
        tree = ast.parse(path.read_bytes(), filename=str(path))
    except Exception:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = None
        if isinstance(f, ast.Name):
            name = f.id
        elif isinstance(f, ast.Attribute):
            name = f.attr
        if name == rule_key:
            return True
    return False


def declared_site_calls(rule: dict, base: pathlib.Path) -> tuple[bool, list[str]]:
    """Reader A: do the DECLARED sites call the rule? Returns (any, checked)."""
    sites = [s.strip() for s in str(rule.get("must_enforce_at") or "").split(",")
             if s.strip()]
    checked: list[str] = []
    for s in sites:
        p = base / s
        checked.append(s)
        if p.is_file() and _calls_in_file(p, str(rule["rule_key"])):
            return True, checked
    return False, checked


# ---------------------------------------------------------------------------
# Reader B — the REPO-WIDE scan (does NOT read the declaration)
# ---------------------------------------------------------------------------

def product_files(base: pathlib.Path) -> list[pathlib.Path]:
    """Every PRODUCT `.py`, sorted. Excludes session scripts and SKIP_DIRS."""
    out: list[pathlib.Path] = []
    for p in sorted(base.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith(SESSION_PREFIX):
            continue
        out.append(p)
    return out


def repo_wide_callers(rule_key: str, base: pathlib.Path) -> list[str]:
    """Reader B: every product file that CALLS the rule, ignoring the declaration."""
    hits: list[str] = []
    for p in product_files(base):
        if _calls_in_file(p, rule_key):
            hits.append(p.relative_to(base).as_posix())
    return hits


# ---------------------------------------------------------------------------
# The audit — two readers, one verdict
# ---------------------------------------------------------------------------

def audit_rule(rule: dict, base: pathlib.Path) -> dict[str, Any]:
    """The verdict for ONE rule, from BOTH readers."""
    key = str(rule["rule_key"])
    sites = str(rule.get("must_enforce_at") or "").strip()
    if not sites:
        return {"rule_key": key, "status": UNDECLARED,
                "detail": "no must_enforce_at — nobody stated where this rule "
                          "must be enforced",
                "declared_sites": [], "repo_wide": []}
    declared_ok, checked = declared_site_calls(rule, base)
    repo = repo_wide_callers(key, base)
    if declared_ok and repo:
        return {"rule_key": key, "status": ENFORCED,
                "detail": "declared site calls it; repo-wide also finds it",
                "declared_sites": checked, "repo_wide": repo}
    if not declared_ok and not repo:
        return {"rule_key": key, "status": UNWIRED,
                "detail": "the declared site does NOT call it, and the "
                          "repo-wide scan finds NOTHING",
                "declared_sites": checked, "repo_wide": repo}
    if not declared_ok and repo:
        return {"rule_key": key, "status": MISPLACED,
                "detail": "the declared site does NOT call it, but the "
                          "repo-wide scan finds it in %s — the DECLARATION is "
                          "wrong" % ", ".join(repo),
                "declared_sites": checked, "repo_wide": repo}
    # declared_ok and not repo: the declared site calls it but the repo-wide
    # scan (which excludes session scripts) does not — the caller is a session
    # script, i.e. only a PROOF calls it. That is the defect, not a pass.
    return {"rule_key": key, "status": UNWIRED,
            "detail": "only a SESSION SCRIPT calls it (the declared site is a "
                      "session script, or the repo-wide scan excludes it) — a "
                      "proof calling a rule is not production enforcement",
            "declared_sites": checked, "repo_wide": repo}


def audit(conn: sqlite3.Connection, base: pathlib.Path | None = None) -> dict[str, Any]:
    """Audit every active rule. Reports, never raises."""
    b = base or BASE
    ensure_schema(conn)
    seed_rules(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM enforcement_rule WHERE is_active=1 ORDER BY rule_key")]
    results = [audit_rule(r, b) for r in rows]
    findings = [r for r in results if r["status"] in FINDING_STATUSES]
    return {"checked": len(results), "rules": results,
            "findings": findings, "open": len(findings),
            "enforced": len(results) - len(findings)}


def record_findings(conn: sqlite3.Connection, report: dict) -> dict[str, int]:
    """UPSERT the findings: a finding is TRACKED, not printed (QC-09).

    A rule that is a finding gets an `OPEN` row; a rule that is no longer a
    finding gets `CLOSED`. The row is never deleted, so "was this ever open?"
    survives.

    A FINDING CLOSES WHEN THE RULE IS NO LONGER A FINDING — INCLUDING WHEN THE
    RULE IS REMOVED FROM THE REGISTRY. MEASURED DEFECT IN MY FIRST VERSION: the
    close loop iterated `report["rules"]`, so a finding whose rule had been
    DELETED from `enforcement_rule` was never visited and stayed `OPEN` forever —
    the run could never go green again. The close pass therefore runs over the
    FINDING TABLE, not over the current rule list.
    """
    ensure_schema(conn)
    opened = closed = 0
    finding_keys = {f["rule_key"] for f in report["findings"]}
    for r in report["rules"]:
        key = r["rule_key"]
        if key not in finding_keys:
            continue
        row = conn.execute("SELECT status FROM enforcement_finding WHERE "
                           "rule_key=?", (key,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO enforcement_finding (rule_key, status, detail) "
                "VALUES (?,?,?)", (key, OPEN, r["detail"]))
            opened += 1
        else:
            conn.execute(
                "UPDATE enforcement_finding SET status=?, detail=?, "
                "last_seen=datetime('now') WHERE rule_key=?",
                (OPEN, r["detail"], key))
    # THE CLOSE PASS RUNS OVER THE FINDING TABLE, so a rule that was REMOVED
    # from the registry (and is therefore absent from `report["rules"]`) still
    # closes. A finding that can never close is a gate that can never pass.
    for f in conn.execute("SELECT rule_key FROM enforcement_finding "
                          "WHERE status=?", (OPEN,)).fetchall():
        key = str(f[0])
        if key not in finding_keys:
            conn.execute(
                "UPDATE enforcement_finding SET status=?, "
                "last_seen=datetime('now') WHERE rule_key=?", (CLOSED, key))
            closed += 1
    conn.commit()
    return {"opened": opened, "closed": closed}


def open_findings(conn: sqlite3.Connection) -> list[dict]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM enforcement_finding WHERE status=? ORDER BY rule_key",
        (OPEN,))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="enforcement audit")
    ap.add_argument("--report", action="store_true",
                    help="print the audit and always exit 0 (read-only view)")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        rep = audit(conn)
        rec = record_findings(conn, rep)
        print("== enforcement audit ==")
        print("  checked=%d  enforced=%d  findings=%d"
              % (rep["checked"], rep["enforced"], rep["open"]))
        for r in rep["rules"]:
            print("  %-10s %-22s %s" % (r["status"], r["rule_key"], r["detail"]))
        print("  findings opened=%d closed=%d" % (rec["opened"], rec["closed"]))
        opens = open_findings(conn)
        if opens:
            print("\nOPEN FINDINGS (must be followed up, not just reported):")
            for f in opens:
                print("  %-22s owner=%s  %s"
                      % (f["rule_key"], f["owner"], f["detail"][:80]))
        if args.report:
            return 0
        # THE FOLLOW-UP IS A GATE: a non-zero exit fails the sweep, so a
        # delivery cannot be marked done while a finding is open.
        return 1 if opens else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
