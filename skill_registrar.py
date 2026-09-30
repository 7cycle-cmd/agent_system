# -*- coding: utf-8 -*-
"""skill_registrar.py — register a skill into the 4 SSOT layers from a DECLARATION.

WHY THIS EXISTS
---------------
Registering a skill used to mean writing a `_register_<name>_skill.py` script.
Five of those exist and they are the same program five times:

    _register_debug_skill.py
    _register_human_decision_skill.py
    _register_no_null_skill.py
    _register_independent_review_skill.py
    _register_problem_citation_skills.py

Each carries its own FIELDS / TDD_CASES / PURPOSE and calls the same four
upserts. The user's requirement (2026-09-22):

    "yes, but the methid is by skill and be auto!! not have work forever, is how
    to auto"

So this module reads a per-skill `contract.yaml` and writes the four layers.
Adding a skill is now writing a skill file. A sixth script would be the defect
this module removes.

THE FOUR LAYERS, AND WHY THEY ARE NOT TREATED UNIFORMLY
-------------------------------------------------------
  1. skill_prompt_ssot        -> upsert_skill_prompt   (the dropdown)
  2. skill_contract_template  -> upsert_contract       (taxonomy_path hard-required)
  3. skill_contract_field     -> upsert_field          (>=5 rows)
  4. skill_contract_tdd_case  -> upsert_tdd_case       (>=3 pass + >=2 hard_fail)

MEASURED: they do NOT agree on how they fail.
  * `upsert_skill_prompt` RAISES ValueError on a missing key/version/text.
  * `upsert_contract` / `upsert_field` / `upsert_tdd_case` RETURN a refusal dict
    (`{"ok": False, "code": ...}`) and never raise.
A registrar that treats them uniformly either crashes on layer 1 or silently
ignores a refused layer 2-4. So layer 1 is wrapped and layers 2-4 are CHECKED.

WHAT IT REFUSES
---------------
  * a skill with NO declaration — inventing a contract is fabricating a rule
  * a declaration missing a required key
  * a TDD case whose `expected` is not a non-empty dict — `_expected_matches`
    returns `(True, "no expectation recorded")` for an empty one, so such a case
    CANNOT FAIL
  * a BEHAVIOUR case (one with no `expected["rule"]`) when no probe token is
    declared, or when the declared token is absent from `skill_tdd_runner.PROBES`
    — such a case returns "no probe registered" and FAILS, so the contract would
    be documented but unproven

Run:
    .\\.venv\\Scripts\\python.exe skill_registrar.py --all
    .\\.venv\\Scripts\\python.exe skill_registrar.py --all --apply
    .\\.venv\\Scripts\\python.exe skill_registrar.py --skill skill_5w1h --apply
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# cp950 console: printing a non-cp950 character raises UnicodeEncodeError and
# kills the run before its verdict. Measured on this repo before.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"
SKILLS_ROOT = BASE_DIR / "skills"
RUNNER = BASE_DIR / "skill_tdd_runner.py"
DECLARATION_NAME = "contract.yaml"

# The keys a declaration MUST carry. `field_seed` OR `fields` supplies layer 3.
REQUIRED_KEYS = ("skill_key", "contract_id", "taxonomy_path", "purpose")
# `expected["rule"]` values the payload validator can decide WITHOUT a probe
# token (see `skill_tdd_runner._make_payload_probe`).
PAYLOAD_RULES = ("valid", "missing", "type", "enum", "unregistered", "immutable")


class DeclarationError(ValueError):
    """Raised when a declaration cannot be used as written."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def declaration_path(skill_dir: Path | str) -> Path:
    return Path(skill_dir) / DECLARATION_NAME


def load_declaration(skill_dir: Path | str) -> dict[str, Any] | None:
    """Read `contract.yaml` from a skill directory.

    Returns None when the file is ABSENT — that is not an error, it is a skill
    that has not declared a contract yet. `register_all` REPORTS those rather
    than inventing one.
    """
    p = declaration_path(skill_dir)
    if not p.is_file():
        return None
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise DeclarationError(
            "PyYAML is required to read %s: %s" % (p.name, e)) from e
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if data is None:
        raise DeclarationError("%s is empty" % p)
    if not isinstance(data, dict):
        raise DeclarationError(
            "%s must be a mapping, got %s" % (p, type(data).__name__))
    data["_path"] = str(p)
    return data


def validate_declaration(decl: dict[str, Any]) -> list[str]:
    """Every reason the declaration cannot be used. Returns errors, never raises.

    Returning a LIST rather than raising lets the caller report ALL the problems
    at once. A declaration fixed one error at a time is a declaration edited
    five times.
    """
    errs: list[str] = []
    for k in REQUIRED_KEYS:
        if not str(decl.get(k) or "").strip():
            errs.append("missing required key %r" % k)
    if not decl.get("field_seed") and not decl.get("fields"):
        errs.append("needs `field_seed` (a module name) or `fields` (a list)")
    cases = decl.get("tdd_cases") or []
    if not isinstance(cases, list):
        errs.append("`tdd_cases` must be a list")
        cases = []
    kinds = [str(c.get("kind") or "") for c in cases if isinstance(c, dict)]
    n_pass = kinds.count("pass")
    n_hard = kinds.count("hard_fail")
    if n_pass < 3:
        errs.append("needs >=3 pass cases, has %d" % n_pass)
    if n_hard < 2:
        errs.append("needs >=2 hard_fail cases, has %d" % n_hard)
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            errs.append("tdd_cases[%d] is not a mapping" % i)
            continue
        ck = str(c.get("case_key") or "")
        if not ck:
            errs.append("tdd_cases[%d] has no case_key" % i)
        if str(c.get("kind") or "") not in ("pass", "hard_fail"):
            errs.append("tdd_cases[%d] kind must be pass|hard_fail" % i)
        if not str(c.get("assertion") or "").strip():
            errs.append("tdd_cases[%d] has no assertion" % i)
        exp = c.get("expected")
        # THE EMPTY-EXPECTED TRAP. `_expected_matches` returns
        # (True, "no expectation recorded") for a non-dict or empty dict, so a
        # case with no expectation is a case that CANNOT FAIL.
        if not isinstance(exp, dict) or not exp:
            errs.append(
                "tdd_cases[%d] (%s) `expected` must be a NON-EMPTY dict — an "
                "empty one passes unconditionally" % (i, ck or "?"))
    return errs


def behaviour_cases(decl: dict[str, Any]) -> list[str]:
    """Case keys that need a dedicated probe (no `expected["rule"]`)."""
    out: list[str] = []
    for c in decl.get("tdd_cases") or []:
        if not isinstance(c, dict):
            continue
        exp = c.get("expected") or {}
        rule = str(exp.get("rule") or "") if isinstance(exp, dict) else ""
        if rule not in PAYLOAD_RULES:
            out.append(str(c.get("case_key") or "?"))
    return out


def probe_token_present(token: str) -> bool:
    """Is `token` registered in `skill_tdd_runner.PROBES`?

    Read from the SOURCE, not by importing the runner: importing it pulls in the
    whole probe stack (evidence store, vision, DB handles) for one string, and a
    source read is what `_register_human_decision_skill.py` already does.
    """
    if not token:
        return False
    src = RUNNER.read_text(encoding="utf-8", errors="replace")
    return ('"%s"' % token) in src


# ---------------------------------------------------------------------------
# Field sources
# ---------------------------------------------------------------------------

def fields_from_seed(module_name: str) -> list[dict[str, Any]]:
    """Read a field source module's `DIMENSIONS` and normalise it.

    WHY A POINTER AND NOT A COPY
    ----------------------------
    `skill_5w1h`'s six dimensions are ALREADY declared once, in
    `skill_5w1h.DIMENSIONS`, and the skill's own "Not to do" section forbids a
    second copy:

        "DO NOT hand-write a second copy of the six dimensions. Derive from
         DIMENSIONS; a copy is the drift this skill exists to prevent."

    So a declaration may POINT AT that source. Restating the six here would make
    the registrar force exactly the drift the skill exists to prevent.

    The source's `DIMENSIONS` entries are `(field_name, question, hard_rule,
    mandatory)`.
    """
    mod = importlib.import_module(module_name)
    dims = getattr(mod, "DIMENSIONS", None)
    if not dims:
        raise DeclarationError(
            "field_seed %r has no DIMENSIONS" % module_name)
    out: list[dict[str, Any]] = []
    for entry in dims:
        if not isinstance(entry, (tuple, list)) or len(entry) < 4:
            raise DeclarationError(
                "field_seed %r DIMENSIONS entry is not "
                "(field_name, question, hard_rule, mandatory): %r"
                % (module_name, entry))
        name, _question, hard_rule, mandatory = entry[0], entry[1], entry[2], entry[3]
        out.append({
            "field_name": str(name),
            "data_type": "TEXT",
            "hard_rule": str(hard_rule),
            "mandatory": bool(mandatory),
            "immutable": False,
        })
    return out


def fields_from_list(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, f in enumerate(raw):
        if not isinstance(f, dict):
            raise DeclarationError("fields[%d] is not a mapping" % i)
        out.append({
            "field_name": str(f.get("field_name") or ""),
            "data_type": str(f.get("data_type") or "TEXT"),
            "hard_rule": str(f.get("hard_rule") or ""),
            "mandatory": bool(f.get("mandatory")),
            "immutable": bool(f.get("immutable")),
        })
    return out


def resolve_fields(decl: dict[str, Any]) -> list[dict[str, Any]]:
    if decl.get("field_seed"):
        return fields_from_seed(str(decl["field_seed"]))
    return fields_from_list(decl.get("fields") or [])


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _layer_ok(res: Any) -> tuple[bool, str]:
    """Normalise a layer result. Layers 2-4 RETURN a refusal dict."""
    if isinstance(res, dict):
        if res.get("ok"):
            return True, str(res.get("action") or "ok")
        return False, "%s: %s" % (res.get("code") or "REFUSED",
                                  res.get("message") or res)
    return True, str(res)


def ensure_skill_identity(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    name: str,
    description: str = "",
    taxonomy_path: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    """Write the skill's IDENTITY into `skill_registry`. Idempotent.

    WHY THIS EXISTS — A DEFECT THE PROOF FOUND (2026-09-24)
    -------------------------------------------------------
    `register_skill` wrote FOUR layers (prompt ssot, contract, fields, tdd
    cases) and **never wrote `skill_registry`**. MEASURED: after a full
    `apply=True` registration, `SELECT COUNT(*) FROM skill_registry` was 0. That
    is HALF A REGISTRATION, and it is not cosmetic: `skill_factor.register_factor`
    REFUSES a `skill_key` that is not in `skill_registry`

        "a factor for a skill that does not exist can never be measured"

    so a skill registered by its declaration could not carry a factor — the
    precise wall `_proof_done_chain.py` hit. A registration path that leaves the
    identity table empty makes the factor system unusable for every newly
    declared skill.

    The DDL is IMPORTED from `split_skill_registry.skill_registry_DDL`, which is
    the migration that owns this table, rather than restated here — a second
    `CREATE TABLE` is exactly the drift this repo removes.
    """
    key = str(skill_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_SKILL_KEY",
                "message": "skill_key is required to mint an identity"}
    import split_skill_registry as ssr
    conn.executescript(ssr.skill_registry_DDL)
    row = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                       (key,)).fetchone()
    if row:
        return {"ok": True, "action": "already_registered",
                "skill_id": int(row[0])}
    cur = conn.execute(
        "INSERT INTO skill_registry (skill_key, name, description, parser, "
        "taxonomy_path) VALUES (?,?,?,?,?)",
        (key, str(name or key).strip() or key, str(description or "")[:400],
         "result_yes_no", str(taxonomy_path or "NA")))
    if commit:
        conn.commit()
    return {"ok": True, "action": "registered", "skill_id": int(cur.lastrowid)}


def register_skill(
    conn: sqlite3.Connection,
    skill_dir: Path | str,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Register ONE skill from its declaration. Idempotent.

    `apply=False` writes NOTHING and still reports what WOULD happen, so a dry
    run is a real observation rather than a promise.

    Returns a PER-LAYER report. A partial registration must be VISIBLE: a
    registrar that returns a bare bool cannot say WHICH layer refused.
    """
    import skill_contract_store as scs
    import skill_prompt as sp

    skill_dir = Path(skill_dir)
    report: dict[str, Any] = {
        "skill_dir": str(skill_dir),
        "apply": bool(apply),
        "layers": {},
        "refused": [],
        "ok": False,
    }

    decl = load_declaration(skill_dir)
    if decl is None:
        report["refused"].append(
            "no %s — a skill with no declaration is NOT auto-registered "
            "(inventing a contract is fabricating a rule)" % DECLARATION_NAME)
        return report

    errs = validate_declaration(decl)
    if errs:
        report["refused"].extend(errs)
        return report

    skill_key = str(decl["skill_key"]).strip()
    contract_id = str(decl["contract_id"]).strip()
    taxonomy_path = str(decl["taxonomy_path"]).strip()
    version_label = str(decl.get("version_label") or "v1_strict").strip()
    report["skill_key"] = skill_key
    report["contract_id"] = contract_id

    # --- the probe-token gate, BEFORE anything is written -------------------
    need = behaviour_cases(decl)
    token = str(decl.get("probe_token") or "").strip()
    if need:
        if not token:
            report["refused"].append(
                "behaviour cases %s need a probe token, and none is declared — "
                "such a case returns 'no probe registered' and FAILS"
                % (need[:3],))
            return report
        if not probe_token_present(token):
            report["refused"].append(
                "probe token %r is not registered in %s.PROBES — every case "
                "keyed with it would return 'no probe registered' and FAIL"
                % (token, RUNNER.name))
            return report
    report["probe_token"] = token
    report["behaviour_cases"] = need

    # --- the canonical file (layer 1's text) --------------------------------
    canonical = None
    for cand in sorted(skill_dir.glob("*.skill.md")):
        canonical = cand
        break
    if canonical is None:
        report["refused"].append(
            "no *.skill.md in %s — layer 1 copies the canonical file's text"
            % skill_dir)
        return report
    text = canonical.read_text(encoding="utf-8")
    report["canonical"] = canonical.name
    report["canonical_sha"] = hashlib.sha256(
        text.encode("utf-8")).hexdigest()[:16]

    fields = resolve_fields(decl)
    cases = [c for c in (decl.get("tdd_cases") or []) if isinstance(c, dict)]

    if not apply:
        report["layers"] = {
            "identity": {"ok": True, "action": "would-register",
                         "detail": skill_key},
            "ssot": {"ok": True, "action": "would-upsert",
                     "detail": "%d chars" % len(text)},
            "contract": {"ok": True, "action": "would-upsert",
                         "detail": taxonomy_path},
            "fields": {"ok": True, "action": "would-upsert",
                       "detail": "%d fields" % len(fields)},
            "tdd": {"ok": True, "action": "would-upsert",
                    "detail": "%d cases" % len(cases)},
        }
        report["ok"] = True
        report["dry_run"] = True
        return report

    # --- layer 0: skill_registry (THE IDENTITY) -----------------------------
    # FIRST, because every later layer refers to this skill by key, and because
    # `skill_factor.register_factor` REFUSES a factor for a skill that is not
    # here. MEASURED: without this layer a full registration left the table at 0
    # rows, so a newly declared skill could not carry a factor at all.
    ident = ensure_skill_identity(
        conn, skill_key=skill_key,
        name=str(decl.get("name") or skill_key),
        description=str(decl["purpose"])[:400], taxonomy_path=taxonomy_path,
        commit=False)
    report["layers"]["identity"] = {
        "ok": bool(ident.get("ok")),
        "action": str(ident.get("action") or ident.get("code")),
        "detail": skill_key,
    }
    if not ident.get("ok"):
        report["refused"].append("layer 0 identity refused: %s"
                                 % ident.get("message"))
        return report

    # --- layer 1: skill_prompt_ssot (RAISES, so it is wrapped) --------------
    try:
        out = sp.upsert_skill_prompt(
            conn, skill_key=skill_key, version_label=version_label,
            prompt_text=text, status="draft", activate=False,
            notes=str(decl.get("notes") or "") or None)
        report["layers"]["ssot"] = {
            "ok": True,
            "action": str(out.get("action") if isinstance(out, dict) else out),
            "detail": "%d chars" % len(text),
        }
    except Exception as e:
        report["layers"]["ssot"] = {
            "ok": False, "action": "RAISED",
            "detail": "%s: %s" % (type(e).__name__, e),
        }
        report["refused"].append("layer 1 ssot raised: %s" % e)
        return report

    # --- layer 2: skill_contract_template (RETURNS a refusal dict) ----------
    res = scs.upsert_contract(
        contract_id, skill_key, taxonomy_path, str(decl["purpose"]),
        environment=decl.get("environment"),
        purpose_not_responsible=decl.get("purpose_not_responsible"),
        flow=decl.get("flow"),
        not_to_do=decl.get("not_to_do"),
        # A newly registered contract is DRAFT, never active: it has not been
        # proven. `status` is this table's activation state (it has no
        # `is_active` column), and the 100-run gate is what promotes it.
        status="draft",
        source="declaration",
        notes=str(decl.get("notes") or "") or None,
        conn=conn,
    )
    ok, detail = _layer_ok(res)
    report["layers"]["contract"] = {"ok": ok, "action": detail,
                                    "detail": taxonomy_path}
    if not ok:
        report["refused"].append("layer 2 contract refused: %s" % detail)
        return report

    # --- layer 3: skill_contract_field --------------------------------------
    written, refused = [], []
    for f in fields:
        r = scs.upsert_field(
            contract_id, f["field_name"], f["data_type"], f["hard_rule"],
            mandatory=f["mandatory"], immutable=f["immutable"],
            taxonomy_path=taxonomy_path, conn=conn)
        ok, detail = _layer_ok(r)
        (written if ok else refused).append(
            f["field_name"] if ok else "%s (%s)" % (f["field_name"], detail))
    # ---- THE PRUNE, APPLIED TO FIELDS TOO (2026-09-29) --------------------
    # THE SAME PRINCIPLE AS LAYER 4: a declaration is the WHOLE SET, not a
    # delta. `upsert_field` is upsert-only, so a RENAMED field name would leave
    # the old row behind exactly as a renamed case key did. The human asked
    # whether the prune was general or a one-off — it was a one-off, and this is
    # the generalisation. A principle enforced in ONE place is the "stated but
    # not enforced" disease this repo keeps paying for.
    pruned_f = scs.prune_fields(
        contract_id, [f["field_name"] for f in fields], conn=conn)
    report["layers"]["fields"] = {
        "ok": not refused, "action": "upserted",
        "detail": "%d written, %d refused, %d pruned"
                  % (len(written), len(refused),
                     int(pruned_f.get("deleted") or 0)),
        "written": written, "refused": refused,
        "pruned": pruned_f.get("deleted_keys") or [],
    }
    if refused:
        report["refused"].append("layer 3 fields refused: %s" % refused)
    if not pruned_f.get("ok"):
        report["refused"].append("layer 3 prune refused: %s"
                                 % pruned_f.get("message"))

    # --- layer 4: skill_contract_tdd_case -----------------------------------
    written, refused = [], []
    for c in cases:
        r = scs.upsert_tdd_case(
            str(c["case_key"]), contract_id, str(c["kind"]),
            str(c["assertion"]),
            input_payload=c.get("input") or c.get("input_payload"),
            expected=c.get("expected"), conn=conn)
        ok, detail = _layer_ok(r)
        (written if ok else refused).append(
            str(c["case_key"]) if ok else "%s (%s)" % (c["case_key"], detail))
    # ---- THE PRUNE (2026-09-29) -------------------------------------------
    # MEASURED DEFECT: `upsert_tdd_case` UPSERTS but never DELETES, so RENAMING
    # a case key left the OLD key in the table. `SKILL.MEASUREMENT.CROSS.CHECK`
    # then ran 11 cases and reported **9/11 passed**, with two stale keys
    # failing as "no probe registered". The defect was caught by an AGENT
    # reading the runner's output — self-awareness, not a mechanism — which is
    # the exact disease this repo keeps paying for.
    #
    # A DECLARATION IS THE WHOLE SET, NOT A DELTA. So after upserting, the
    # table is made to EQUAL the declaration. This is the mechanism: a rename
    # can no longer leave a stale case behind, and nobody has to notice.
    pruned = scs.prune_tdd_cases(
        contract_id, [str(c["case_key"]) for c in cases], conn=conn)
    report["layers"]["tdd"] = {
        "ok": not refused, "action": "upserted",
        "detail": "%d written, %d refused, %d pruned"
                  % (len(written), len(refused), int(pruned.get("deleted") or 0)),
        "written": written, "refused": refused,
        "pruned": pruned.get("deleted_keys") or [],
    }
    if refused:
        report["refused"].append("layer 4 tdd refused: %s" % refused)
    if not pruned.get("ok"):
        report["refused"].append("layer 4 prune refused: %s"
                                 % pruned.get("message"))

    conn.commit()
    report["ok"] = not report["refused"]
    return report


def register_all(
    conn: sqlite3.Connection,
    skills_root: Path | str = SKILLS_ROOT,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    """Register every skill that HAS a declaration.

    Skills with NO declaration are REPORTED, not skipped silently: a silent skip
    makes "nothing to do" and "I did not look" the same output.
    """
    root = Path(skills_root)
    declared: list[Path] = []
    undeclared: list[str] = []
    for p in sorted(root.rglob("*.skill.md")):
        d = p.parent
        if declaration_path(d).is_file():
            if d not in declared:
                declared.append(d)
        else:
            undeclared.append(p.name[: -len(".skill.md")])

    results = [register_skill(conn, d, apply=apply) for d in declared]
    return {
        "apply": bool(apply),
        "declared": len(declared),
        "undeclared": len(undeclared),
        "undeclared_skills": sorted(undeclared),
        "results": results,
        "ok": all(r.get("ok") for r in results) if results else True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(rep: dict[str, Any]) -> None:
    key = rep.get("skill_key") or Path(rep.get("skill_dir", "?")).name
    print("\n=== %s ===" % key)
    if rep.get("refused"):
        for r in rep["refused"]:
            print("  REFUSED  %s" % r)
    for name, layer in (rep.get("layers") or {}).items():
        print("  %-9s %-5s %-14s %s"
              % (name, "ok" if layer.get("ok") else "FAIL",
                 layer.get("action", ""), layer.get("detail", "")))
    if rep.get("dry_run"):
        print("  (dry run — nothing written)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="register skills from declarations")
    ap.add_argument("--all", action="store_true",
                    help="every skill under --root that has a contract.yaml")
    ap.add_argument("--skill", metavar="KEY",
                    help="one skill by its directory name")
    ap.add_argument("--root", default=str(SKILLS_ROOT))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="write; without it this is a dry run")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        import skill_contract_store as scs
        import skill_prompt as sp
        sp.ensure_skill_tables(conn)
        scs.ensure_skill_contract_schema(conn)

        if args.skill:
            hits = [p.parent for p in Path(args.root).rglob("*.skill.md")
                    if p.parent.name == args.skill]
            if not hits:
                print("no skill directory named %r under %s"
                      % (args.skill, args.root))
                return 2
            rep = register_skill(conn, hits[0], apply=args.apply)
            _print_report(rep)
            return 0 if rep.get("ok") else 1

        if not args.all:
            ap.print_help()
            return 2

        out = register_all(conn, args.root, apply=args.apply)
        for rep in out["results"]:
            _print_report(rep)
        print("\n=== SUMMARY ===")
        print("  declared   : %d" % out["declared"])
        print("  undeclared : %d (reported, NOT invented)" % out["undeclared"])
        if out["undeclared_skills"]:
            print("    %s" % ", ".join(out["undeclared_skills"][:8]))
        print("  apply      : %s" % out["apply"])
        print("  ok         : %s" % out["ok"])
        return 0 if out["ok"] else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
