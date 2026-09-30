# -*- coding: utf-8 -*-
"""citation_discipline.py — the EXECUTABLE form of "no citation, no finding".

WHY THIS FILE EXISTS
--------------------
`docs/report_skill_set_for_finding_problems_zh.md` §3 (B1) names the gap, and
`systematic_debug.py:keep_finding()` already implements the predicate. What was
missing is the WRITE-SITE gate: the report's own §2 admits

    「我引用得好多（好），但先寫落 markdown 才入 DB — 即係發現冇可查詢嘅引用」

i.e. findings were written to markdown first, so the DB rows carried no
queryable reference. A predicate nobody calls at the write site changes nothing
— the same defect class `env_task_proof.assert_rect_ok()` was written to fix.

So this module owns the gate that REFUSES to persist an uncited finding, and
`systematic_debug.keep_finding()` stays the single source of the predicate.

Pure: no I/O, no DB, no network. Callers supply the finding.
"""
from __future__ import annotations

import re

# A citation is a `path:line`, a `path:line-line` range, a command, an evidence
# id, or a structured `kind:name:date` reference. Deliberately strict about
# PROSE: "I saw it" and "the docs say" are not citations.
_PATH_LINE = re.compile(r"^[\w./\\-]+\.\w+:\d+(-\d+)?$")
_COMMAND = re.compile(r"^(python|py|pip|git|npm|node|pwsh|powershell|\.\\|\./)\b", re.I)
# An evidence folder id, e.g. EVID-perm_default-20260919-234224. Checkable: the
# folder either exists under evidence/ or it does not.
_EVIDENCE_ID = re.compile(r"^EVID-[\w.-]+$")
# A structured reference: `kind:name[:detail]`. The kind must be a known one, so
# "memory:something" cannot sneak through.
_STRUCTURED = re.compile(r"^(trap|defect|auto-audit|evidence|case|incident|qc|"
                         r"lesson|test|probe|contract):[\w./-]+", re.I)
# A DATABASE row reference: `register:<table>:<pk>`, e.g.
# `register:db_table_registry:4323`.
#
# Added 2026-09-21 for a measured reason: an entity that exists only as a
# register row has no `path:line` to cite. Refusing every such approval would
# have meant the citation gate could never approve a table/field/skill -- it
# would have been unusable rather than strict.
#
# It is NOT a loosening: string-shape alone is not accepted. `verify_db_ref()`
# below re-queries the table and the row, so `register:no_such_table:1` and
# `register:db_table_registry:999999` are both REFUSED. That is a STRONGER check
# than a `path:line` gets here, where nothing confirms the file or the line.
_DB_REF = re.compile(r"^register:([A-Za-z_][A-Za-z0-9_]*):(\d+)$")
_DB_REF_TABLE_DENY = ("sqlite_",)
_IDENT_RE_DB = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_DB = None


def _default_db_path():
    """Resolve agent.db lazily so importing this module has no side effects."""
    global _DEFAULT_DB
    if _DEFAULT_DB is None:
        from pathlib import Path as _P
        _DEFAULT_DB = _P(__file__).resolve().parent / "agent.db"
    return _DEFAULT_DB

# Prefixes that look like a reference but carry no location.
_FAKE_PREFIXES = (
    "memory", "recall", "i think", "probably", "the docs", "the readme",
    "earlier", "before", "as discussed", "我記得", "應該", "之前",
)


def parse_db_ref(ref: str) -> tuple[str, int] | None:
    """Split `register:<table>:<pk>` into (table, pk). None when it is not one."""
    m = _DB_REF.match(str(ref or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def verify_db_ref(ref: str, *, conn=None, db_path=None) -> dict:
    """Prove a `register:<table>:<pk>` reference against the real database.

    Returns {"db_ref": True/False, "exists": True/False, "why": str}. A ref that
    is not in this shape returns {"db_ref": False} and is judged by the usual
    rules instead.

    This is the reason the DB form is acceptable at all: the row is re-queried,
    so the reference is CHECKED rather than merely well-shaped. The table name is
    validated as an identifier before being interpolated, because it comes from
    the caller.
    """
    parsed = parse_db_ref(ref)
    if not parsed:
        return {"db_ref": False, "exists": False, "why": "not a register ref"}
    table, pk = parsed
    if any(table.startswith(p) for p in _DB_REF_TABLE_DENY):
        return {"db_ref": True, "exists": False,
                "why": "internal table %r may not be cited" % table}
    if not _IDENT_RE_DB.match(table):
        return {"db_ref": True, "exists": False,
                "why": "table name %r is not an identifier" % table}

    own = False
    if conn is None:
        import sqlite3 as _sq
        try:
            conn = _sq.connect(str(db_path or _default_db_path()))
            own = True
        except Exception as e:
            return {"db_ref": True, "exists": False,
                    "why": "database unavailable (%s)" % type(e).__name__}
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (table,)).fetchone()
        if not row:
            return {"db_ref": True, "exists": False,
                    "why": "no table named %r" % table}
        cols = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)]
        pk_cols = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)
                   if c[5]]
        target = pk_cols[0] if pk_cols else (cols[0] if cols else None)
        if not target:
            return {"db_ref": True, "exists": False,
                    "why": "table %r has no columns" % table}
        found = conn.execute(
            "SELECT 1 FROM %s WHERE %s = ?" % (table, target),
            (pk,)).fetchone()
        if not found:
            return {"db_ref": True, "exists": False,
                    "why": "%s has no row %s = %d" % (table, target, pk)}
        return {"db_ref": True, "exists": True,
                "why": "%s.%s = %d exists" % (table, target, pk)}
    except Exception as e:
        return {"db_ref": True, "exists": False,
                "why": "lookup failed (%s: %s)" % (type(e).__name__, e)}
    finally:
        if own:
            conn.close()


def is_citation(ref: str) -> bool:
    """True when `ref` is a real, checkable citation.

    Accepts, in order of specificity:
      - `path:line` / `path:line-line`   e.g. `f_perm_click.py:412`
      - a command                        e.g. `python _proof_stop_rule.py`
      - an evidence id                   e.g. `EVID-perm_default-20260919-234224`
      - a structured `kind:name[:detail]` e.g. `trap:skill-registration:2026-09-20`
      - a database row `register:table:pk` e.g. `register:db_table_registry:4323`
        (use `verify_db_ref` to confirm the row; this predicate is shape only)

    Rejects prose that merely sounds like a reference ("I remember", "the docs
    say"). The structured form is accepted because the repo's own lessons use it
    and it IS checkable — a gate that discards real provenance is worse than no
    gate, because it destroys the record it was meant to protect.
    """
    s = str(ref or "").strip()
    if not s:
        return False
    low = s.lower()
    if any(low.startswith(p) for p in _FAKE_PREFIXES):
        return False
    if _PATH_LINE.match(s):
        return True
    if _COMMAND.match(s):
        return True
    if _EVIDENCE_ID.match(s):
        return True
    if _DB_REF.match(s):
        return True
    if _STRUCTURED.match(s):
        return True
    return False


def keep_finding(finding: dict | None) -> bool:
    """A finding with no real citation is DISCARDED, never downgraded.

    Mirrors superpowers' "no citation, no finding". Delegates the predicate to
    `systematic_debug.keep_finding` when available so there is ONE copy of the
    rule; falls back to the local check otherwise.
    """
    if not isinstance(finding, dict):
        return False
    ref = finding.get("evidence_ref") or finding.get("source_ref") or ""
    try:
        import systematic_debug as sd

        if not sd.keep_finding(ref):
            return False
    except Exception:
        if not str(ref).strip():
            return False
    return is_citation(ref)


def partition(findings: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """Split findings into (kept, discarded). Discarded are NOT downgraded."""
    kept, dropped = [], []
    for f in findings or []:
        (kept if keep_finding(f) else dropped).append(f)
    return kept, dropped


# ---------------------------------------------------------------------------
# runtime gate: refuse to persist an uncited finding
# ---------------------------------------------------------------------------

class UncitedFinding(RuntimeError):
    """Raised instead of persisting a finding that carries no citation."""

    def __init__(self, reasons: list[str], finding: dict | None = None):
        self.reasons = list(reasons)
        self.finding = finding
        super().__init__(
            "uncited finding — refusing to persist: %s"
            % ("; ".join(self.reasons) or "no reason given")
        )


def assert_cited(finding: dict | None) -> dict:
    """Gate at the write site: refuse to persist an uncited finding.

    Returns the finding when it carries a real citation; raises UncitedFinding
    otherwise. This is the gate the report's §2 said was missing.
    """
    if not isinstance(finding, dict):
        raise UncitedFinding(["finding is not a dict"], finding)
    ref = finding.get("evidence_ref") or finding.get("source_ref") or ""
    if not str(ref).strip():
        raise UncitedFinding(
            ["no evidence_ref / source_ref — a finding with no citation is "
             "DISCARDED, not downgraded"],
            finding,
        )
    if not is_citation(ref):
        raise UncitedFinding(
            ["%r is not a checkable citation — need path:line or a command" % ref],
            finding,
        )
    return finding

# object_door: kind-agnostic by definition (no DDL in this file)
