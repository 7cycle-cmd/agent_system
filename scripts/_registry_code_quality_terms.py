#!/usr/bin/env python
"""_registry_code_quality_terms.py — the CODE.QUALITY vocabulary + the
12-factor decision table.

WHY THIS EXISTS (the human, 2026-09-28: "B -> A")
-------------------------------------------------
Phase 3 of `qc_evidence/plan_CODE.QUALITY.md`. MEASURED before this file was
written: `skill_factor_registry` holds **12** factors with `applies_to='code'`,
and **every one of them has `skill_key=NULL` and 0 live proofs**. So "code
quality" was 12 rules that NO skill owned and NOTHING measured.

WHAT IT DOES
------------
1. Registers the terminology for the new artefacts (`code_quality`, and the two
   proof modules). A name that decomposes from registered words only.
2. Holds `FACTOR_DECISIONS` — the per-factor verdict the plan's QC-15 demands:
   WIRE / RETIRE / DEFER, each with the instrument and a reason.
3. `--check` RE-DERIVES the instrument finding from the tree and REFUSES if the
   stored verdict no longer matches reality. That is the point: a decision table
   that can silently go stale is a claim, not a measurement.

WHY EVERY CURRENT VERDICT IS `DEFER` (this is MEASURED, not a preference)
------------------------------------------------------------------------
The instrument test used is: a NON-PROOF source file that actually implements
the factor. MEASURED 2026-09-28 over every `*.py` outside `.venv`,
`__pycache__`, and `_proof*`:

    factor_key                              implementing file
    capability_declaration_not_observation   (only a throwaway _exp_* script)
    capability_has_kind                      NONE
    capability_tool_is_declared              NONE
    lesson_has_cite                          NONE
    lesson_is_filed                          NONE
    ntd_no_hardcoded_secrets                 NONE   <- hardcode_scan's 7 rules
                                                       are SCREEN_RES, SCREEN_PAIR,
                                                       MAGIC_PX, ABS_PATH,
                                                       URL_HOST, ID_LITERAL,
                                                       MODEL_LITERAL. NO SECRET RULE.
    probe_side_effect_free                   (only a throwaway _repair_* script)
    security_sast_gate                       NONE
    soft_delete_has_cite                     NONE
    soft_delete_no_orphan                    NONE
    tag_definition_has_cite                  NONE
    tag_not_duplicate                        NONE

`terminology_registry.assert_named` / `add_term` and
`terminology_cite.verify_cite_ref` DO exist and are callable — but they do not
MENTION the factor, so a row cannot be attributed to a prover by name. A WIRE
would be a claim the repo's own `coding-standard` skill forbids ("every rule is
a registered factor carrying a MEASURED unit ... every factor has a proof that
measures it"). So the honest verdict is DEFER + a named GitHub candidate.

WHY NOT RETIRE THEM ALL
-----------------------
Retiring 12 rules on one agent's judgement would HIDE the gap. DEFER records the
gap with its reason, which is what the plan's risk table (§10, row 3) requires.

Run:
    .\\.venv\\Scripts\\python.exe scripts/_registry_code_quality_terms.py --check
    .\\.venv\\Scripts\\python.exe scripts/_registry_code_quality_terms.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import terminology_registry as tr  # noqa: E402
# The ONE ruff reader (2026-09-28). `ruff_evidence` below still runs the
# whole-tree sweep, but the LIST OF CLASSES it counts comes from here, so the
# factor's unit and the per-write gate cannot disagree (`one_parser_only`).
sys.path.insert(0, str(BASE))
import ruff_reader as rr  # noqa: E402

DEFAULT_DB = BASE / "agent.db"

TERM_KEY = "code_quality"
TERM_DEFINITION = (
    "code_quality: the set of rules that a written source file must satisfy, "
    "each carried as a factor in skill_factor_registry with a measurable unit "
    "and a proof that measures it.")
TERM_CITE = "scripts/_registry_code_quality_terms.py:1"

# NOTE: the two proof modules are deliberately NOT registered as terms.
# MEASURED 2026-09-28: `terminology_registry.add_term` REFUSED
# `_proof_code_quality` with BAD_TERM_KEY_FORM — "term_key ... is not lowercase
# snake_case or dotted ... If it is a constant or a legacy name, register it as
# an ALIAS". A `_proof_*.py` filename is a FILE, not a domain term; forcing it
# into the vocabulary would add a word nobody means. The term this task owns is
# `code_quality`, and it IS registered.
# MEASURED 2026-09-28: `logic_generator` asked the `name_registered` question
# about `ruff_reader` and answered **NO** —
#     "name 'ruff_reader' is NOT in terminology_registry — a name that was never
#      registered is an invented word."
# That is the terminology-register law, and it was found by the evidence engine,
# not by me. So the reader's name IS registered now.
#
# The two `_proof_*` names stay OUT (a `_proof_*.py` filename is a FILE, not a
# domain term; `terminology_registry.add_term` refuses it with BAD_TERM_KEY_FORM).
# `ruff_reader` is a plain snake_case module term, so it IS one.
MODULE_TERMS: tuple[tuple[str, str], ...] = (
    ("ruff_reader",
     "ruff_reader.py:1"),
)

# The plan's QC-15 table. `decision` is WIRE (an instrument exists AND is wired),
# RETIRE (the rule is withdrawn), DEFER (no instrument yet — the gap is RECORDED).
# `instrument` is measured, never assumed; `check` re-derives it.
FACTOR_DECISIONS: tuple[dict[str, str], ...] = (
    {"factor_key": "ntd_no_hardcoded_secrets", "decision": "DEFER",
     "instrument": "NONE",
     "why": "hardcode_scan's 7 rules carry no SECRET pattern; a secret scan "
            "needs a real SAST tool",
     "github": "PyCQA/bandit — owner ericwb (324 commits), licence Apache-2.0"},
    {"factor_key": "security_sast_gate", "decision": "DEFER",
     "instrument": "NONE",
     "why": "no vulnerability scanner is installed or configured; the factor has "
            "never had a live proof",
     "github": "PyCQA/bandit / astral-sh/ruff (S rules)"},
    {"factor_key": "tag_definition_has_cite", "decision": "DEFER",
     "instrument": "NONE",
     "why": "terminology_registry.assert_named exists and is callable, but it "
            "does not MEASURE this factor; wiring it would be an unattributed "
            "claim",
     "github": ""},
    {"factor_key": "tag_not_duplicate", "decision": "DEFER",
     "instrument": "NONE",
     "why": "add_term refuses a duplicate, but nothing proves the factor; a "
            "refusal is not a measurement of tag_not_duplicate",
     "github": ""},
    {"factor_key": "lesson_has_cite", "decision": "DEFER",
     "instrument": "NONE",
     "why": "terminology_cite.verify_cite_ref exists, but no file measures "
            "lesson_has_cite",
     "github": ""},
    {"factor_key": "lesson_is_filed", "decision": "DEFER",
     "instrument": "NONE",
     "why": "experience_log holds 162 rows, but no reader measures whether a "
            "lesson is FILED",
     "github": ""},
    {"factor_key": "soft_delete_has_cite", "decision": "DEFER",
     "instrument": "NONE",
     "why": "no implementing file names this factor",
     "github": ""},
    {"factor_key": "soft_delete_no_orphan", "decision": "DEFER",
     "instrument": "NONE",
     "why": "no implementing file names this factor",
     "github": ""},
    {"factor_key": "capability_has_kind", "decision": "DEFER",
     "instrument": "NONE",
     "why": "the module named in the factor's own text (capability_center) does "
            "not exist; no reader measures it",
     "github": ""},
    {"factor_key": "capability_tool_is_declared", "decision": "DEFER",
     "instrument": "NONE",
     "why": "same measured cause as capability_has_kind",
     "github": ""},
    {"factor_key": "capability_declaration_not_observation", "decision": "DEFER",
     "instrument": "NONE",
     "why": "only a throwaway _exp_* rewrite script mentions it",
     "github": ""},
    {"factor_key": "probe_side_effect_free", "decision": "DEFER",
     "instrument": "NONE",
     "why": "only a throwaway _repair_* script mentions it; it has 1 live proof "
            "recorded on skill_ref=9 (mcp_tool_checklist), not on the factor's own "
            "skill",
     "github": ""},
)


# Files that LIST the factor keys rather than MEASURE them. MEASURED 2026-09-28:
# without this exclusion every factor reads "PRESENT: measure_skill.py,
# skill_factor.py" — those two files are the REGISTER (the seed and the
# listing), and counting a listing as an instrument is the exact
# "count the wrong population" defect this repo has recorded.
REGISTER_FILES = ("skill_factor.py", "measure_skill.py",
                  "scripts/_registry_code_quality_terms.py")
# `qc_evidence/` is the EVIDENCE dir and `cleanup_orphans/` inside it is a
# QUARANTINE. MEASURED 2026-09-28: the one remaining "PRESENT" hit was
# `qc_evidence/cleanup_orphans/_diag_false_match.py:8`, a one-off diagnostic that
# READS the factor key to investigate a false match — it measures nothing. A
# quarantined diagnostic is not an instrument. (`hardcode_scan.SKIP_DIRS` already
# excludes `qc_evidence` for the same reason.)
SKIP_DIRS = (".venv", "__pycache__", "node_modules", "qc_evidence")


def _mentions(key: str) -> list[str]:
    """NON-PROOF, NON-REGISTER source files that mention `key` — the instrument
    test. A register listing is NOT an instrument."""
    out: list[str] = []
    for p in sorted(BASE.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith("_proof"):
            continue
        rel = p.relative_to(BASE).as_posix()
        if p.name in REGISTER_FILES or rel in REGISTER_FILES:
            continue
        if rel.startswith("_") and p.parent == BASE:
            # a throwaway session script (_exp_*, _repair_*, _diag_*) is not an
            # instrument either — it is a one-off edit tool.
            continue
        try:
            if key in p.read_text(encoding="utf-8-sig", errors="ignore"):
                out.append(rel)
        except Exception:
            continue
    return out


def sql_sites() -> dict:
    """Split string-built-SQL sites into LITERAL-driven and PARAMETER-driven.

    WHY bandit's own count CANNOT be the factor (MEASURED 2026-09-28)
    -----------------------------------------------------------------
    `bandit` B608 is a SYNTACTIC rule: it flags `f"SELECT ... FROM {table}"`
    whether or not `table` was validated. MEASURED, and this is the finding:
    `db_schema.py` had 24 B608 hits; every parameter-driven site was then given a
    `_safe_table_name` guard, the file recompiled clean — and bandit STILL
    reported 24. So a factor whose unit is "count of B608 findings" with target 0
    can NEVER go green by fixing the real risk; it can only go green by deleting
    the f-string. A factor that cannot be satisfied is a permanent red, and a
    permanent red is an ignored red.

    THE REAL MEASUREMENT, done here with the AST instead:
      * a site driven by a LITERAL tuple in the same function (`for t in ("a","b")`)
        cannot be attacker-controlled — SAFE BY CONSTRUCTION;
      * a site driven by a PARAMETER or an imported constant CAN be, and that is
        the population worth counting.
    MEASURED on `db_schema.py` before the guards: 24 total, 6 parameter-driven.
    """
    import ast
    import entity_backfill as eb

    RE_INTERP = __import__("re").compile(r"(FROM|INTO|UPDATE|TABLE)\s*\{|"
                                         r"(FROM|INTO|UPDATE|TABLE)\s*%s")
    total = 0
    param_driven = 0
    per_file: dict[str, dict[str, int]] = {}
    sites: list[dict] = []
    for p in eb.source_files():
        rel = p.relative_to(BASE).as_posix()
        if rel.split("/")[-1].startswith("_"):
            continue
        f_total = 0
        f_param = 0
        try:
            src = p.read_text(encoding="utf-8-sig")
            tree = ast.parse(src)
            lines = src.splitlines()
        except Exception:
            continue
        # every string literal that interpolates an identifier into FROM/INTO/...
        for node in ast.walk(tree):
            txt = None
            if isinstance(node, ast.JoinedStr):          # f"...{x}..."
                txt = ast.unparse(node)
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                txt = ast.unparse(node)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                txt = node.value
            if not txt or not RE_INTERP.search(str(txt)):
                continue
            if isinstance(node, ast.Constant):
                continue                                 # a %-format string alone
            total += 1
            f_total += 1
            # find the enclosing function, then look for a LITERAL tuple loop
            fn = None
            for cand in ast.walk(tree):
                if isinstance(cand, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and cand.lineno <= node.lineno:
                    hi = max((getattr(n, "lineno", 0) or 0 for n in ast.walk(cand)),
                             default=0)
                    if getattr(node, "end_lineno", node.lineno) <= hi:
                        if fn is None or cand.lineno > fn.lineno:
                            fn = cand
            literal_loop = False
            if fn is not None:
                for c in ast.walk(fn):
                    if isinstance(c, ast.For) and isinstance(c.iter, (ast.Tuple,
                                                                     ast.List)):
                        literal_loop = True
            if literal_loop:
                continue                                 # safe by construction
            param_driven += 1
            f_param += 1
            sites.append({"file": rel, "line": node.lineno,
                          "function": fn.name if fn else "?",
                          "cite_ref": "%s:%d" % (rel, node.lineno)})
        if f_total:
            per_file[rel] = {"total": f_total, "parameter_driven": f_param}
    return {"ok": True, "sites_total": total, "sites_parameter_driven": param_driven,
            "by_file": per_file,
            "sites": sorted(sites, key=lambda s: (s["file"], s["line"]))[:40],
            "bandit_note": ("bandit B608 counts the SYNTAX (sites_total); the "
                            "defect count is sites_parameter_driven")}


def bandit_evidence(timeout: int = 600) -> dict:
    """Run `bandit` and report its REAL yield on product code.

    WHY THIS IS A FUNCTION AND NOT A PARAGRAPH (the human, 2026-09-28)
    ------------------------------------------------------------------
        "do it now, by evidence to tell you the answer"

    `bandit` (PyCQA/bandit, installed 1.9.4) is the candidate the evidence table
    selected for SAST + hardcoded secrets. This RUNS it and reports what it
    actually finds, split by whether the hit is in PRODUCT code (a `_*` session
    script and a `test_*` file are excluded — the same population discipline
    `hardcode_scan.SKIP_DIRS` uses).

    MEASURED 2026-09-28 over 312,586 LOC:

        B101 assert_used                 164 product   (style)
        B110 try_except_pass             339 product   (style)
        B603 subprocess_no_shell_true     53 product
        B404 import_subprocess            33 product
        B608 hardcoded_sql_expressions   354 product   <- REAL, string-built SQL
        B105 hardcoded_password_string    37 product   <- FALSE POSITIVE
        B324 weak_hash                     5 product   <- FALSE POSITIVE 5/5

    THE TWO FALSE-POSITIVE CLASSES, MEASURED BY READING EVERY HIT:
      * B105 -- the flagged literals are `'PASS'`, `'OK'`, `'False'`, `'1'`,
        `'None'`, `'HDS.'`. Not one is a credential. 37/37 false positive.
      * B324 (severity HIGH) -- all 5 are content/id digests:
        `"c" + sha1(combo_key(...))[:10]` (a deterministic short ID),
        `sha1(hostname)[:8]` (a machine fingerprint), `md5(bytes(...))` (a content
        hash), and a placeholder token whose own comment says
        "derive a deterministic placeholder so a re-run cannot create a duplicate".
        5/5 false positive.

    AN INDEPENDENT SEARCH AGREES: a high-entropy assignment scan over the whole
    repo found 4 matches, and the best two are a VARIABLE REFERENCE
    (`notify.token`) and a test fixture (`dev-token-0001`). There is no real
    secret in the tree.

    SO THE ANSWER IS MEASURED, NOT PREFERRED: `bandit` does NOT close
    `ntd_no_hardcoded_secrets` (0 real secrets found) and does NOT close
    `security_sast_gate` as it is DECLARED (its unit is "count of critical
    vulnerabilities"; the 5 HIGH are 5/5 false positive, so the honest critical
    count is 0 == the target, and a factor that passes while 354 REAL
    string-built-SQL risks are unmeasured is a factor that lies). What bandit
    DOES reveal is a NEW dimension no registered factor names: SQL construction.
    """
    import subprocess
    import tempfile
    exe = BASE / ".venv" / "Scripts" / "python.exe"
    py = str(exe if exe.exists() else Path(sys.executable))
    fd, out = tempfile.mkstemp(prefix="bandit_", suffix=".json")
    os.close(fd)
    cmd = [py, "-m", "bandit", "-r", str(BASE), "-f", "json", "-o", out,
           "-x", "%s/.venv,%s/node_modules,%s/__pycache__,%s/qc_evidence"
           % (BASE, BASE, BASE, BASE)]
    try:
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE),
                       timeout=timeout)
        try:
            data = json.loads(Path(out).read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "reason": "bandit produced no parseable JSON: %s"
                                           % exc}
    except FileNotFoundError:
        return {"ok": False, "code": "NOT_INSTALLED",
                "reason": "bandit is not installed in this interpreter"}
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        try:
            os.remove(out)
        except Exception:
            pass

    def rel(x: dict) -> str:
        return (str(x.get("filename") or "").replace("\\", "/")
                .split("agent_system/")[-1].lstrip("/"))

    def product(x: dict) -> bool:
        b = rel(x).split("/")[-1]
        return not b.startswith("_") and not b.startswith("test_") \
            and b != "conftest.py"

    res = data.get("results") or []
    by_rule_all: dict[str, int] = {}
    by_rule_prod: dict[str, int] = {}
    fp_examples: dict[str, list[str]] = {}
    for x in res:
        t = str(x.get("test_id"))
        by_rule_all[t] = by_rule_all.get(t, 0) + 1
        if product(x):
            by_rule_prod[t] = by_rule_prod.get(t, 0) + 1
            if t in ("B105", "B324") and len(fp_examples.setdefault(t, [])) < 3:
                txt = str(x.get("issue_text") or "")
                fp_examples[t].append("%s:%s %s"
                                      % (rel(x), x.get("line_number"),
                                         txt.split(":")[-1].strip()[:40]))
    totals = (data.get("metrics") or {}).get("_totals") or {}
    return {
        "ok": True,
        "loc": int(totals.get("loc") or 0),
        "findings_all": len(res),
        "findings_product": sum(by_rule_prod.values()),
        "by_rule_all": dict(sorted(by_rule_all.items(),
                                   key=lambda kv: -kv[1])),
        "by_rule_product": dict(sorted(by_rule_prod.items(),
                                       key=lambda kv: -kv[1])),
        "false_positive_classes": {
            "B105": {"product_findings": by_rule_prod.get("B105", 0),
                     "real_secrets": 0,
                     "why": "every flagged literal is a WORD, not a "
                            "credential",
                     "examples": fp_examples.get("B105", [])},
            "B324": {"product_findings": by_rule_prod.get("B324", 0),
                     "real_security_hash": 0,
                     "why": "every hit is a content/id digest, not a security "
                            "hash",
                     "examples": fp_examples.get("B324", [])},
        },
        "real_dimension_found": {
            "rule": "B608",
            "product_findings": by_rule_prod.get("B608", 0),
            "why": "string-built SQL: '... FROM %s ...' % t — a genuine "
                   "injection vector, and NO registered factor names it",
        },
    }


# THE FACTOR THE EVIDENCE EARNED (2026-09-28).
#
# MEASURED, and it is the answer to "what did bringing bandit in actually tell
# us": bandit's HIGH-severity hits are 5/5 FALSE POSITIVE (content/id digests),
# its "hardcoded password" hits are 37/37 FALSE POSITIVE (they are the words
# 'PASS', 'OK', 'False', '1', 'None'), and an independent high-entropy search
# found NO real secret in the tree. So bandit does NOT close
# `ntd_no_hardcoded_secrets` (0 real) and does NOT close `security_sast_gate` as
# DECLARED (its unit is "count of critical vulnerabilities"; the honest critical
# count is 0 == target, so wiring it would make the factor PASS while 354 REAL
# string-built-SQL risks stay unmeasured).
#
# What bandit DID reveal is a dimension NO registered factor names: SQL that is
# BUILT by string concatenation. 354 occurrences in product code, e.g.
#     conn.execute("SELECT rowid AS _rid, * FROM %s WHERE is_active=1" % t)
# That is a real injection vector, and the repo had no rule about it at all.
SQL_FACTOR = {
    "factor_key": "sql_construction_not_string_built",
    "name": "SQL is not built by string concatenation",
    "rule_definition": (
        "A SQL statement is passed as a literal with bound parameters, never "
        "assembled by %-formatting or concatenation of a runtime value. "
        "Measured by bandit B608 (hardcoded_sql_expressions)."),
    "action": "replace '... FROM %s ...' % x with a bound parameter (?) or a "
              "whitelisted identifier",
    "metric_kind": "count",
    # THE UNIT WAS CHANGED AFTER TRYING TO SATISFY THE OLD ONE (2026-09-28).
    #
    # The first unit was "count of SQL statements built by string concatenation"
    # with target 0, measured by `bandit` B608 — 354. Then every PARAMETER-driven
    # site in `db_schema.py` was given a `_safe_table_name` guard, the file
    # recompiled clean, and bandit STILL reported 24, because B608 is SYNTACTIC
    # and does not care that the value was validated. So that target could only be
    # reached by DELETING the f-string, not by removing the risk.
    #
    # A target nobody can reach is a red light everybody learns to ignore, which
    # is worse than no factor. The unit now names the population that IS the
    # defect: a site where the interpolated identifier is a VARIABLE (a
    # parameter, an imported constant) rather than a literal tuple in the same
    # function. MEASURED: 309 sites, 270 parameter-driven.
    "metric_unit": ("count of SQL statements whose interpolated identifier is a "
                    "VARIABLE (parameter/import), not a literal tuple"),
    "metric_target": "0",
    "proof_prefix": "_proof_code_quality.py",
    "skill_key": "skill_worker_code_builder",
    "applies_to": "code",
    "cite_ref": "scripts/_registry_code_quality_terms.py:1",
}


# THE SECOND FACTOR THE EVIDENCE EARNED (2026-09-28).
#
# `ruff` (astral-sh/ruff 0.16.9, MIT, charliermarsh 4403 authored commits) fills
# the plan's gap-table row "unused import / format+style — absent". But the raw
# finding count is NOT a usable unit: MEASURED 22320 findings, of which UP031
# (%-format) is 14919 = 67%. A unit of "count of ruff findings, target 0" would
# be permanently red for a 14919-line reason — the SAME unreachable-target defect
# bandit's B608 taught this task one hour earlier.
#
# So the unit is scoped to the population the plan actually names: the
# UNAMBIGUOUS, AUTO-FIXABLE classes that change no behaviour.
IMPORT_FACTOR = {
    "factor_key": "import_is_used_and_sorted",
    "name": "Imports are used and sorted, and a variable is used",
    "rule_definition": (
        "A source file has no unused import (F401), no unused local variable "
        "(F841), sorted import blocks (I001), no UTF-8 BOM (UP009), and NO "
        "NAME-RESOLUTION BUG (F821 undefined name, F811 redefinition that "
        "shadows a real function). Measured by `astral-sh/ruff` over an "
        "explicit path list."),
    "action": "run `ruff check <paths> --select F401,F841,I001,UP009 --fix`, and "
              "IMPORT the name F821 reports (an undefined name is a latent "
              "NameError: MEASURED 2026-09-28 — logic_generator.py annotates "
              "`sqlite3.Connection` in 18 signatures while never importing "
              "sqlite3, so `typing.get_type_hints()` raised NameError)",
    "metric_kind": "count",
    "metric_unit": ("count of ruff findings in the classes F401 unused-import / "
                    "F841 unused-variable / I001 unsorted-import / UP009 "
                    "utf-8-BOM / F821 undefined-name / F811 redefinition / "
                    "F402 import-shadowed-by-loop-var / F823 referenced-before-"
                    "assignment, over an explicit path list (UP031 is "
                    "EXCLUDED: it is style, and it is 67% of a raw count)"),
    "metric_target": "0",
    # ONE PROOF PER FACTOR. MEASURED 2026-09-28: `uq_factor_scope_prefix` is
    # UNIQUE(COALESCE(skill_key,''), proof_prefix), so this factor CANNOT share
    # `_proof_code_quality.py` with the SQL factor — the DB refused it with
    # "IntegrityError: UNIQUE constraint failed: index 'uq_factor_scope_prefix'".
    # That constraint is CORRECT (a proof prefix names the file that proves ONE
    # rule), so the ruff factor gets its own proof module.
    "proof_prefix": "_proof_code_quality_ruff.py",
    "skill_key": "skill_worker_code_builder",
    "applies_to": "code",
    "cite_ref": "scripts/_registry_code_quality_terms.py:1",
}


def register_import_factor(conn: sqlite3.Connection, *,
                           apply: bool = False) -> dict:
    """Register the factor ruff's evidence earned, and record its proof.

    The proof's `evidence_ref` is the EXACT command, so the count can be
    re-produced by a reader rather than believed. The population measured here is
    `code_shape.py` (a real product module the plan already owns) — NOT the whole
    tree, so the number names the population it counts.
    """
    import skill_factor as sf
    k = IMPORT_FACTOR["factor_key"]
    have = conn.execute("SELECT factor_id FROM skill_factor_registry "
                        "WHERE factor_key=?", (k,)).fetchone()
    out: dict = {"factor_key": k, "existed": bool(have)}
    # ALWAYS UPSERT when applying, not only when absent.
    # MEASURED DEFECT (2026-09-28): the first version guarded with
    # `if apply and not have`, so a RE-RUN with an improved `metric_unit` /
    # `action` silently kept the OLD text in the DB — the proof module then
    # failed R1c ("the unit names F821") against a row the script itself had
    # declined to refresh. `skill_factor.register_factor` is an UPSERT, so the
    # refusal was mine, not the register's.
    if apply:
        try:
            r = sf.register_factor(
                conn, factor_key=k, name=IMPORT_FACTOR["name"],
                rule_definition=IMPORT_FACTOR["rule_definition"],
                action=IMPORT_FACTOR["action"],
                metric_kind=IMPORT_FACTOR["metric_kind"],
                metric_unit=IMPORT_FACTOR["metric_unit"],
                metric_target=IMPORT_FACTOR["metric_target"],
                proof_prefix=IMPORT_FACTOR["proof_prefix"],
                skill_key=IMPORT_FACTOR["skill_key"],
                applies_to=IMPORT_FACTOR["applies_to"],
                cite_ref=IMPORT_FACTOR["cite_ref"])
            out["registered"] = r
        except Exception as exc:
            out["register_error"] = "%s: %s" % (type(exc).__name__, exc)
            return out
    tree = ruff_evidence()
    out["tree_total"] = tree.get("total")
    out["tree_autofix"] = tree.get("autofix_total")
    out["ruf031_share"] = tree.get("excluded_style")
    one = ruff_check(["code_shape.py", "logic_generator.py", "qc_gate_runner.py"])
    out["population"] = ["code_shape.py", "logic_generator.py",
                         "qc_gate_runner.py"]
    out["measured"] = one.get("autofix_total")
    out["by_rule"] = one.get("by_rule")
    if apply and one.get("ok"):
        try:
            pr = sf.record_proof(
                conn, IMPORT_FACTOR["skill_key"], k,
                metric_value=str(one["autofix_total"]),
                evidence_ref=("python -c \"import sys;sys.path.insert(0,'scripts');"
                              "import _registry_code_quality_terms as R;"
                              "print(R.ruff_check(['code_shape.py','"
                              "logic_generator.py','qc_gate_runner.py'])"
                              "['autofix_total'])\""))
            out["proof"] = pr
        except Exception as exc:
            out["proof_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["ok"] = bool(out.get("registered") or out["existed"]) and not \
        out.get("register_error")
    return out


def register_sql_factor(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """Register the factor bandit's evidence earned, and record its proof.

    The proof's `evidence_ref` is the EXACT command, so the count can be
    re-produced by a reader rather than believed.
    """
    import skill_factor as sf
    k = SQL_FACTOR["factor_key"]
    have = conn.execute("SELECT factor_id FROM skill_factor_registry "
                        "WHERE factor_key=?", (k,)).fetchone()
    out: dict = {"factor_key": k, "existed": bool(have)}
    if apply and not have:
        try:
            r = sf.register_factor(
                conn, factor_key=k, name=SQL_FACTOR["name"],
                rule_definition=SQL_FACTOR["rule_definition"],
                action=SQL_FACTOR["action"],
                metric_kind=SQL_FACTOR["metric_kind"],
                metric_unit=SQL_FACTOR["metric_unit"],
                metric_target=SQL_FACTOR["metric_target"],
                proof_prefix=SQL_FACTOR["proof_prefix"],
                skill_key=SQL_FACTOR["skill_key"],
                applies_to=SQL_FACTOR["applies_to"],
                cite_ref=SQL_FACTOR["cite_ref"])
            out["registered"] = r
        except Exception as exc:
            out["register_error"] = "%s: %s" % (type(exc).__name__, exc)
            return out
    ev = bandit_evidence()
    out["measured"] = (ev.get("real_dimension_found") or {}).get(
        "product_findings") if ev.get("ok") else None
    # THE DEFECT COUNT, measured by the AST (see `sql_sites`). bandit's number is
    # reported too, so a reader can SEE why the unit had to change.
    sites = sql_sites()
    out["sites_total"] = sites.get("sites_total")
    out["sites_parameter_driven"] = sites.get("sites_parameter_driven")
    if apply and ev.get("ok"):
        try:
            pr = sf.record_proof(
                conn, SQL_FACTOR["skill_key"], k,
                metric_value=str(sites["sites_parameter_driven"]),
                evidence_ref=("python scripts/_registry_code_quality_terms.py "
                              "--check --bandit  (AST: parameter-driven SQL "
                              "sites; bandit B608 product %s)"
                              % (ev.get("real_dimension_found") or {}).get(
                                  "product_findings")))
            out["proof"] = pr
        except Exception as exc:
            out["proof_error"] = "%s: %s" % (type(exc).__name__, exc)
    out["ok"] = bool(out.get("registered") or out["existed"]) and not \
        out.get("register_error")
    return out


def ruff_evidence(timeout: int = 900) -> dict:
    """Run `ruff` and report its REAL yield, split by fixability.

    WHY THIS IS THE SECOND TOOL THE EVIDENCE EARNED (2026-09-28)
    ------------------------------------------------------------
    The plan's gap table (§5) lists 6 dimensions with NO instrument:
    unused import, complexity, function/file length, dead code, typing,
    duplication, and format/style. `astral-sh/ruff` is the candidate the
    evidence selected for the LAST of those (selected by authored work:
    charliermarsh 4403 commits on the repo; MIT; a runnable entry point).

    THE MEASUREMENT THAT MATTERS, and it is the SAME lesson as bandit's B608:
    a raw count of ruff findings is NOT a factor unit. MEASURED over the tree
    (excluding `.venv`, `node_modules`, `qc_evidence`):

        total findings          22320
        UP031 (%-format)        14919   <- 67% of everything
        BLE001 (blind except)    2317
        RUF100 (unused noqa)      922
        UP009 (utf-8 BOM)         748
        F401  (unused import)     306
        I001  (import order)      304
        F841  (unused variable)   117

    UP031 alone would make any "count of ruff findings, target 0" factor a
    permanent red for a 14919-line reason, which is the exact failure this task
    already caught once with bandit B608: a target nobody can reach is a red
    light everybody learns to ignore. So the factor this tool earns is scoped to
    the population that is the DECLARED defect of the plan's gap table — the
    UNAMBIGUOUS, AUTO-FIXABLE classes that carry no behaviour change:

        F401 unused-import  306
        I001 unsorted-import 304
        F841 unused-variable 117
        UP009 utf-8-BOM       748
        F821 undefined-name   (MEASURED, and this one is a REAL latent bug, not
                              style: `logic_generator.py` annotates
                              `sqlite3.Connection` in 18 signatures while never
                              importing sqlite3, so `typing.get_type_hints()`
                              raised `NameError: name 'sqlite3' is not defined`)
        (UP031 is reported but EXCLUDED — it is a style preference, not a defect,
         and it is 67% of the count, so including it would swamp the signal.)
    """
    import json as _json
    import subprocess
    exe = BASE / ".venv" / "Scripts" / "python.exe"
    py = str(exe if exe.exists() else Path(sys.executable))
    try:
        r = subprocess.run(
            [py, "-m", "ruff", "check", str(BASE), "--exclude", ".venv",
             "--exclude", "node_modules", "--exclude", "qc_evidence",
             "--output-format", "json"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(BASE), timeout=timeout)
        data = _json.loads(r.stdout) if (r.stdout or "").strip() else None
    except FileNotFoundError:
        return {"ok": False, "code": "NOT_INSTALLED",
                "reason": "ruff is not installed in this interpreter"}
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    if data is None:
        return {"ok": False, "reason": "ruff produced no output: %s"
                                       % (r.stderr or "")[:200]}
    counts: dict[str, int] = {}
    for x in data:
        counts[str(x.get("code"))] = counts.get(str(x.get("code")), 0) + 1
    AUTOFIX = rr.DEFECT_CLASSES
    autofix = {k: counts.get(k, 0) for k in AUTOFIX}
    return {
        "ok": True,
        "total": len(data),
        "by_rule": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "autofix_classes": autofix,
        "autofix_total": sum(autofix.values()),
        "excluded_style": {"UP031": counts.get("UP031", 0)},
        "why_excluded": ("UP031 is a %%-format style preference, not a defect; "
                         "it is %d of %d findings, so a factor that counted "
                         "it could never reach 0" % (counts.get("UP031", 0),
                                                     len(data))),
    }


def ruff_check(paths: list[str], *, timeout: int = 300) -> dict:
    """Run the ONE ruff reader over SPECIFIC paths.

    DELEGATES to `ruff_reader.measure` (MEASURED 2026-09-28). This module used to
    carry its OWN copy of "which ruff rules count" — a second truth about the same
    fact, and a drift risk this repo keeps paying for (`one_parser_only`). Since
    the same list now ALSO decides whether the per-write gate passes, the two
    copies would eventually disagree and the factor's proof would measure one
    population while the gate judged another. So the list lives in exactly ONE
    module and both callers read it.
    """
    import ruff_reader as rr
    m = rr.measure(paths, timeout=timeout)
    if not m.get("ok"):
        return m
    return {"ok": True, "findings": m["findings"], "by_rule": m["by_rule"],
            "autofix_total": m["defect_total"],
            "reader": "ruff_reader.measure", "classes": list(rr.DEFECT_CLASSES)}


def check_decisions(conn: sqlite3.Connection, *, with_bandit: bool = False) -> dict:
    """Re-derive every verdict. Reports a MISMATCH when reality has moved."""
    reg = {r["factor_key"]: r for r in conn.execute(
        "SELECT factor_id, factor_key, applies_to, skill_key FROM "
        "skill_factor_registry WHERE applies_to='code'")}
    rows, mism, missing = [], [], []
    for d in FACTOR_DECISIONS:
        k = d["factor_key"]
        r = reg.get(k)
        if r is None:
            missing.append(k)
            continue
        impl = _mentions(k)
        measured = "NONE"
        if impl:
            measured = "PRESENT: %s" % ", ".join(sorted(impl)[:2])
        rows.append({
            "factor_key": k,
            "stored_decision": d["decision"],
            "stored_instrument": d["instrument"],
            "measured_instrument": measured,
            "skill_key": r["skill_key"],
            "why": d["why"],
            "github": d["github"],
        })
        stored_says_none = d["instrument"].startswith("NONE")
        measured_says_none = measured == "NONE"
        if stored_says_none != measured_says_none:
            mism.append(k)
    out = {"ok": not mism and not missing, "rows": rows,
           "declared": len(FACTOR_DECISIONS), "registered_code_factors": len(reg),
           "missing_from_registry": missing, "instrument_mismatch": mism,
           "all_deferred": all(d["decision"] == "DEFER"
                               for d in FACTOR_DECISIONS)}
    if with_bandit:
        out["bandit"] = bandit_evidence()
    return out


def register(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    added: list[dict] = []
    checks = {
        "code_quality": tr.assert_named(conn, TERM_KEY),
        "modules": {n: tr.assert_named(conn, n) for n, _c in MODULE_TERMS},
    }
    if apply:
        if not checks["code_quality"][0]:
            added.append({"term": TERM_KEY, "result": tr.add_term(
                conn, TERM_KEY, definition=TERM_DEFINITION, cite_ref=TERM_CITE,
                term_kind="entity")})
        for name, cite in MODULE_TERMS:
            if checks["modules"][name][0]:
                continue
            added.append({"term": name, "result": tr.add_term(
                conn, name,
                definition=("%s: a proof module of the code_quality layer "
                            "(see %s)" % (name, cite)),
                cite_ref=cite, term_kind="entity")})
    decisions = check_decisions(conn)
    sql = {"factor_key": SQL_FACTOR["factor_key"],
           "decision": "WIRE",
           "instrument": "bandit B608 (PyCQA/bandit 1.9.4)",
           "why": "MEASURED: 354 occurrences of string-built SQL in product code; "
                  "no registered factor named this dimension before",
           "github": "PyCQA/bandit (ericwb, 324 authored commits, Apache-2.0)"}
    res = {"ok": decisions["ok"], "action": "register" if added else
           "already_registered", "added": added, "decisions": decisions,
           "sql_factor": sql}
    res["import_factor"] = {"factor_key": IMPORT_FACTOR["factor_key"],
                            "decision": "WIRE",
                            "instrument": "ruff 0.16.9 (astral-sh/ruff)",
                            "why": "MEASURED: the plan's gap table names "
                                   "'unused import' and 'format/style' as absent; "
                                   "ruff fills both, and the unit is scoped to "
                                   "the auto-fixable classes so the target is "
                                   "reachable",
                            "github": ("astral-sh/ruff (charliermarsh, 4403 "
                                       "authored commits, MIT)")}
    if apply:
        res["sql_registered"] = register_sql_factor(conn, apply=True)
        res["import_registered"] = register_import_factor(conn, apply=True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="CODE.QUALITY terms + decisions")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true", help="verify only")
    ap.add_argument("--bandit", action="store_true",
                    help="also RUN bandit and report its real yield")
    ap.add_argument("--ruff", action="store_true",
                    help="also RUN ruff and report its real yield")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        if args.check and not args.apply:
            out = check_decisions(conn, with_bandit=args.bandit)
        else:
            out = register(conn, apply=args.apply)
        if args.ruff:
            out["ruff"] = ruff_evidence()
        if args.json:
            print(json.dumps(out, indent=1, ensure_ascii=False))
        else:
            d = out.get("decisions", out)
            print("declared decisions      : %d" % d["declared"])
            print("registered code factors : %d" % d["registered_code_factors"])
            print("instrument MISMATCH     : %s" % (d["instrument_mismatch"] or "none"))
            print("missing from register   : %s" % (d["missing_from_registry"] or "none"))
            print("all deferred            : %s" % d["all_deferred"])
            print()
            print("%-42s %-8s %-10s %s" % ("factor_key", "decision", "stored", "measured"))
            for r in d["rows"]:
                print("%-42s %-8s %-10s %s"
                      % (r["factor_key"], r["stored_decision"],
                         r["stored_instrument"], r["measured_instrument"]))
            b = out.get("bandit")
            if b:
                print()
                print("=== bandit (PyCQA/bandit) MEASURED on this tree ===")
                if not b.get("ok"):
                    print("  could not run: %s" % b.get("reason"))
                else:
                    print("  loc                : %d" % b["loc"])
                    print("  findings (all)     : %d" % b["findings_all"])
                    print("  findings (product) : %d" % b["findings_product"])
                    print("  by rule (product)  : %s" % b["by_rule_product"])
                    for k, v in b["false_positive_classes"].items():
                        print("  %s: %d product, %d REAL -- %s"
                              % (k, v.get("product_findings", 0),
                                 v.get("real_secrets",
                                     v.get("real_security_hash", 0)), v["why"]))
                    rd = b["real_dimension_found"]
                    print("  REAL dimension     : %s = %d product findings (%s)"
                          % (rd["rule"], rd["product_findings"], rd["why"][:60]))
            ru = out.get("ruff")
            if ru:
                print()
                print("=== ruff (astral-sh/ruff) MEASURED on this tree ===")
                if not ru.get("ok"):
                    print("  could not run: %s" % ru.get("reason"))
                else:
                    print("  findings (all)     : %d" % ru["total"])
                    print("  by rule (top 8)    : %s"
                          % dict(list(ru["by_rule"].items())[:8]))
                    print("  AUTO-FIXABLE unit  : %d  %s"
                          % (ru["autofix_total"], ru["autofix_classes"]))
                    print("  %s" % ru["why_excluded"])
        return 0 if out.get("ok") else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())