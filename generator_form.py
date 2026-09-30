#!/usr/bin/env python
"""generator_form.py — the SUBMIT GATE for a generator form.

THE HUMAN'S REQUIREMENT (2026-09-28, verbatim)
----------------------------------------------
    "how to have table standardize form submit before logic generator"
    "how to standardlize, why you hate that, it is same format forever, just
     step form"
    "without table how can i have question template, fuck!!!"

WHY THIS MODULE IS SMALL — AND WHY THAT IS THE ANSWER
-----------------------------------------------------
My FIRST plan proposed a new `generator_standard` table and a new hard gate. Both
were WRONG, and the human said so. MEASURED:

  * the step form ALREADY EXISTS (`pattern_template`, 41 rows, 30 subject_kinds,
    one fixed column set, ordered by `sort_order`);
  * the gate ALREADY EXISTS (`pattern_template.assert_conforms`), and its refusal
    already NAMES the field, the rule, the why and a copyable example;
  * the verdict store ALREADY EXISTS (`pattern_instance`).

So this module does NOT define a format, a gate or a table. It does exactly TWO
things that were missing:

  1. READ the step form for `subject_kind='generator'` and EVALUATE each step
     against the generator as it really is (module imports? entry callable?
     validator present or DECLARED absent? its declared fields covering what the
     validator reads?);
  2. CALL `pattern_template.assert_conforms` per step — never re-implement it —
     and call the generator's OWN validator. A second implementation of a check
     is a second place that knows the rule.

THE ONE HOLE IT CLOSES (MEASURED)
---------------------------------
`logic_generator.generate()` ALREADY raises `SpecError` for a blank `subject` and
for missing `field_types`, and `generator_center.run()` already returns
`MISSING_VALUE`. The ONLY thing that PASSES when it should not is a spec whose
six 5W1H dimensions are ALL EMPTY: `validate_spec` returns `[]` and `generate()`
happily emits 26 questions. That — plus carrying the EVIDENCE that the wording
came from a gate rather than from nothing — is this gate's job.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError, OSError):
    pass

import pattern_template as pt  # noqa: E402 -- after sys.path (repo pattern)

DB = BASE / "agent.db"
#: The subject kind this module gates. It is a VALUE in `pattern_template`, not a
#: constant this module chooses: `assert_conforms` REFUSES a kind no row declares,
#: so the form cannot drift away from the register without going red.
SUBJECT_KIND = "generator"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# THE FACTS — measured per generator, never assumed
# ---------------------------------------------------------------------------
def generator_facts(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    """Everything the 5 steps ask about ONE generator, MEASURED.

    Nothing here reads a literal: the module and entry come from
    `generator_center.GENERATORS`, the validator is FOUND by name on the module,
    and the declared fields come from `generator_center.required_fields`, which is
    itself a reader of `generator_required_field`.
    """
    import generator_center as gc
    g = gc.find(key)
    if g is None:
        return {"ok": False, "key": str(key), "reason": "UNKNOWN_GENERATOR",
                "declared": [x["key"] for x in gc.GENERATORS]}
    module_name = str(g["module"])
    entry_name = str(g["entry"])
    try:
        mod = importlib.import_module(module_name)
        module_ok, module_err = True, ""
    except Exception as exc:  # noqa: BLE001 -- a module that will not import
        mod, module_ok, module_err = None, False, "%s: %s" % (type(exc).__name__, exc)
    entry = getattr(mod, entry_name, None) if module_ok else None
    validators = ([n for n in dir(mod)
                   if n.startswith("validate") and callable(getattr(mod, n))]
                  if module_ok else [])
    # THE DECLARATION for a module with no validator. It is read from
    # `pattern_instance` — the record `pattern_template.record_instance` owns —
    # because S2 already wrote one per generator. A generator with NEITHER a
    # validator NOR a `none_declared` record FAILS step 4; that is the whole point.
    declared_absent = False
    for r in conn.execute(
            "SELECT detail FROM pattern_instance WHERE subject_kind=? AND "
            "subject_ref=? AND verdict='PASS' ORDER BY instance_id DESC LIMIT 5",
            (SUBJECT_KIND, str(key))):
        if "validator_kind=none_declared" in str(r["detail"] or ""):
            declared_absent = True
            break
    fields = gc.required_fields(conn, key)
    return {"ok": True, "key": str(key), "module": module_name,
            "entry": entry_name, "module_ok": module_ok, "module_err": module_err,
            "entry_ok": callable(entry),
            "validator": (validators[0] if validators else None),
            "validator_found": bool(validators),
            "validator_declared_absent": declared_absent,
            "validator_ok": bool(validators) or declared_absent,
            "fields_ok": bool(fields.get("ok")),
            "fields": fields.get("fields") or [],
            "fields_count": int(fields.get("count") or 0),
            "cite": "generator_center.GENERATORS + importlib + required_fields"}


def validator_reads(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    """The names a generator's validator READS, read from the validator's SOURCE.

    WHY A SOURCE READ: the set a validator reads is the set the form must ask for,
    and reading it from the function itself means a NEW rule in the validator
    becomes a required field with no edit here. `inspect.getsource` is the only
    place that fact exists; a hand-kept list would drift the moment the validator
    grew a rule.
    """
    facts = generator_facts(conn, key)
    vname = facts.get("validator")
    if not vname:
        return {"ok": True, "key": key, "validator": None, "reads": [],
                "reason": "NO_VALIDATOR"}
    mod = importlib.import_module(str(facts["module"]))
    src = inspect.getsource(getattr(mod, str(vname)))
    names: list[str] = []
    for m in re.findall(r'\.get\(\s*"([a-z_][a-z0-9_]*)"', src):
        if m not in names:
            names.append(m)
    return {"ok": True, "key": key, "validator": vname, "reads": names,
            "cite": "%s.py:%s" % (facts["module"], vname)}


def uncovered_fields(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    """The fields the validator READS that the form does NOT declare (step 5).

    THIS IS THE MEASURED G1. For `logic_generator` the validator reads
    `subject, dimensions, field_count, field_types` while the form declares only
    `subject`, so three requirements were invisible to a user.
    """
    reads = validator_reads(conn, key)
    if not reads.get("validator"):
        # NO VALIDATOR = NOTHING TO CHECK, and this must SAY SO. MEASURED
        # 2026-09-28: this branch first omitted `covered` entirely, so
        # `bool(missing.get("covered"))` was `False` and step 5 FAILED for the
        # four generators that expose no validator — a false FAIL caused by a
        # missing key, not by a fact.
        #
        # IT IS NOT A SILENT PASS, and that is the point: the DECLARATION lives in
        # step 4 (`has_validator`), which REQUIRES the explicit
        # `validator_kind='none_declared'`. So the absence is already recorded once
        # and this step simply has no names to compare. A generator with neither a
        # validator NOR the declaration still fails step 4.
        return {"ok": True, "key": key, "reads": [], "declared": [],
                "missing": [], "covered": True, "reason": "NO_VALIDATOR",
                "note": ("nothing to check: step `has_validator` requires the "
                         "explicit declaration, and this step compares names the "
                         "validator READS")}
    declared = {str(f["field_name"]) for f in generator_facts(conn, key)["fields"]}
    missing = [n for n in reads["reads"] if n not in declared]
    return {"ok": True, "key": key, "reads": reads["reads"],
            "declared": sorted(declared), "missing": missing,
            "covered": not missing}


# ---------------------------------------------------------------------------
# THE GATE — per step, through `assert_conforms`
# ---------------------------------------------------------------------------
def check_generator(conn: sqlite3.Connection, key: str,
                    *, record: bool = False) -> dict[str, Any]:
    """Evaluate the 5 `generator` steps for ONE generator.

    EVERY step goes through `pattern_template.assert_conforms`, so the refusal
    wording, the `must`/`inactive` handling and the `NO_TEMPLATE` behaviour are
    the EXISTING ones. This function contributes only the FACTS.
    """
    facts = generator_facts(conn, key)
    if not facts.get("ok"):
        return facts
    missing = uncovered_fields(conn, key)
    steps = [
        ("declared", True, "generator_center.GENERATORS"),
        ("has_module", bool(facts["module_ok"]),
         "%s (err=%s)" % (facts["module"], facts["module_err"])),
        ("has_entry", bool(facts["entry_ok"]),
         "getattr(%s, %r)" % (facts["module"], facts["entry"])),
        ("has_validator", bool(facts["validator_ok"]),
         ("validator_kind=validate:%s" % facts["validator"] if facts["validator_found"]
          else "validator_kind=none_declared" if facts["validator_declared_absent"]
          else "NO validator and NO none_declared declaration")),
        ("fields_declared", bool(missing.get("covered")),
         "reads=%s declared=%s missing=%s" % (missing.get("reads"),
                                              missing.get("declared"),
                                              missing.get("missing"))),
    ]
    out: list[dict[str, Any]] = []
    for item_kind, present, observed in steps:
        # THE ACTIVATION DEADLOCK, AND WHY `allow_inactive=True` IS REQUIRED
        # HERE. MEASURED 2026-09-28: with the default (`allow_inactive=False`),
        # a template that is `is_active=0` is REFUSED, so the verdict recorded was
        # `FAIL` — the rule's own truth was never measured. `activation_evidence`
        # then counted 0 PASS, so the template could NEVER be activated. A gate
        # that cannot be reached because it is not yet active is a DEADLOCK, and
        # it was MY design, not an existing one.
        #
        # SO THE TWO FACTS ARE SEPARATED, which is what the parameter exists for:
        #   * does THE RULE HOLD?          -> evaluated with allow_inactive=True,
        #                                     and THAT is what earns activation;
        #   * is the TEMPLATE A LIVE RULE? -> reported as `active`, and enforced
        #                                     on submit.
        res = pt.assert_conforms(conn, subject_kind=SUBJECT_KIND,
                                 item_kind=item_kind, present=bool(present),
                                 observed=str(observed), allow_inactive=True)
        tpl = pt.template_for(conn, SUBJECT_KIND, item_kind) or {}
        row = {"step": item_kind, "present": bool(present),
               "ok": bool(res.get("ok")), "code": res.get("code"),
               "active": bool(int(tpl.get("is_active") or 0)),
               "observed": str(observed)[:200]}
        if not res.get("ok"):
            row["refusal"] = {k: res.get(k) for k in
                              ("field", "rule", "why", "example", "message")
                              if res.get(k)}
        out.append(row)
        if record:
            pt.record_instance(conn, subject_kind=SUBJECT_KIND, subject_ref=str(key),
                               verdict="PASS" if res.get("ok") else "FAIL",
                               detail=str(observed)[:400], item_kind=item_kind,
                               cite_ref="generator_form.py:check_generator",
                               gate_run_ref="GATE-generator-%s" % key)
    bad = [r for r in out if not r["ok"]]
    return {"ok": not bad, "key": str(key), "steps": out,
            "failed": [r["step"] for r in bad],
            "refusals": [r.get("refusal") for r in bad],
            "cite": "pattern_template.assert_conforms (the EXISTING gate)"}


# ---------------------------------------------------------------------------
# SUBMIT — the form's values, gated BEFORE the generator runs
# ---------------------------------------------------------------------------
def submit(conn: sqlite3.Connection, key: str,
           values: dict[str, Any] | None = None,
           *, record: bool = False) -> dict[str, Any]:
    """THE DEFAULT ENTRY POINT: gate a form the user is about to submit.

    THREE checks, and only three, because the rest is already refused:
      (1) the DECLARED fields must be present — reads `generator_required_field`;
      (2) the module's OWN validator must pass — CALLED, never re-implemented;
      (3) THE HOLE: an all-EMPTY 5W1H dimension set is REFUSED here, because
          MEASURED `validate_spec` returns `[]` for it and `generate()` then
          emits a full question list as if the form were complete.
    """
    vals = values or {}
    gen = check_generator(conn, key, record=record)
    if not gen.get("ok"):
        return {"ok": False, "stage": "generator_steps", "detail": gen}
    facts = generator_facts(conn, key)
    declared = [str(f["field_name"]) for f in facts["fields"]]
    telling = [f for f in facts["fields"] if int(f.get("is_required") or 0)]

    def _filled(v: Any) -> bool:
        """Is this value PRESENT? `0` and `False` ARE values.

        MEASURED 2026-09-28, and it was MY bug: the first version tested
        `str(vals.get(name) or "")`, so `field_count=0` — a legitimate count for a
        form with no fields — became `""` and was refused as "not collected". A
        falsy test on a NUMBER is the same defect as counting the wrong
        population: it answers a different question than the one asked.
        """
        if v is None:
            return False
        if isinstance(v, str):
            return bool(v.strip())
        if isinstance(v, (list, tuple, dict, set)):
            return bool(v)
        return True          # 0, 0.0, False are PRESENT values

    absent = []
    skipped_conditional: list[str] = []
    for f in telling:
        name = str(f["field_name"])
        # A REQUIRED FIELD MAY BE CONDITIONAL, and the declaration already says
        # so: `rule='depends_on:<parent>'` is what the register records (MEASURED:
        # `prompt_generator.field_types` style rows carry it).
        #
        # MEASURED 2026-09-28, MY defect: this gate demanded `field_types` on a
        # form with `field_count=0`, while the validator it CLAIMS to call returns
        # `[]` for exactly that spec. A gate STRICTER than the standard it calls is
        # not the standard — it refuses a form the generator accepts, which is how
        # a gate teaches people to bypass it.
        rule = str(f.get("rule") or "")
        if rule.startswith("depends_on:"):
            parent = rule.split(":", 1)[1].strip()
            pv = vals.get(parent)
            try:
                parent_false = (pv is None) or (int(pv) == 0)
            except (TypeError, ValueError):
                parent_false = not str(pv or "").strip()
            if parent_false:
                # THE CONDITION DECIDES ONLY WHETHER THE FIELD IS *REQUIRED*.
                # IT DOES NOT DECIDE WHETHER A VALUE THAT WAS SENT IS EXAMINED.
                #
                # MEASURED 2026-09-28 (correcting my own earlier claim): the
                # validator DOES tolerate the parent being falsy --
                #   validate_spec({"subject":s,"dimensions":d,"field_count":0,
                #                  "field_types":["TEXT"]})   -> []
                # so this is NOT a divergence from the standard; both accept it.
                # The `continue` is kept for the honest reason instead: the value
                # is handed to the VALIDATOR below (the declared fields are passed
                # through by name), so a dependent value that WAS sent is still
                # judged by the module's own rule and not silently dropped here.
                skipped_conditional.append(name)
                if not _filled(vals.get(name)):
                    continue
        if not _filled(vals.get(name)):
            absent.append(name)
    # THE EMPTY-ANSWER HOLE. Only meaningful when the form carries dimensions; a
    # generator whose declared fields have no `dimensions` is not asked to have
    # them, so the check is scoped to the field being DECLARED.
    dims = vals.get("dimensions")
    empty_dims: list[str] = []
    if "dimensions" in declared:
        if not isinstance(dims, dict):
            empty_dims = ["<not a mapping>"]
        else:
            empty_dims = [k for k, v in dims.items() if not str(v or "").strip()]
    out: dict[str, Any] = {
        "ok": not absent and not empty_dims, "key": str(key),
        "declared_fields": declared,
        "required_fields": [str(f["field_name"]) for f in telling],
        "absent": absent, "empty_dimensions": empty_dims,
        "conditional_skipped": skipped_conditional,
        "generator_steps": {s["step"]: s["ok"] for s in gen["steps"]},
    }
    if absent or empty_dims:
        out["refusals"] = (
            [{"field": f, "rule": "declared required",
              "why": "a required input the form did not collect cannot be "
                     "defaulted without inventing a value",
              "example": "send %r" % f} for f in absent]
            + [{"field": "dimensions.%s" % d,
                "rule": "a 5W1H answer must be non-empty",
                "why": "MEASURED: validate_spec accepts six EMPTY dimensions and "
                       "generate() then emits a full question list, so an unfilled "
                       "form is answered rather than refused",
                "example": "dimensions={'what': '...', ...}"}
               for d in empty_dims])
        return out
    # (2) THE VALIDATOR, CALLED. The form's own field names are passed through by
    # name; a validator that needs a key the form did not send is a step-5 defect,
    # so it is reported as such rather than as a validator error.
    if facts["validator_found"]:
        mod = importlib.import_module(str(facts["module"]))
        fn = getattr(mod, str(facts["validator"]))
        spec = {k: vals[k] for k in declared if k in vals}
        for k in ("subject", "dimensions", "field_count", "field_types"):
            if k in vals and k not in spec:
                spec[k] = vals[k]
        errs = fn(spec)
        out["validator"] = {"name": facts["validator"], "errors": list(errs or [])}
        if errs:
            out["ok"] = False
            out["refusals"] = [{"field": "<spec>",
                                "rule": "the generator's own validator",
                                "why": "the module's validator IS the standard; "
                                       "this gate CALLS it rather than copying it",
                                "example": str(e)} for e in errs]
    else:
        out["validator"] = {"name": None, "errors": [],
                            "note": "validator_kind=none_declared"}
    if record:
        pt.record_instance(conn, subject_kind=SUBJECT_KIND, subject_ref=str(key),
                           verdict="PASS" if out["ok"] else "FAIL",
                           detail="submit: absent=%s empty_dims=%s" % (absent, empty_dims),
                           cite_ref="generator_form.py:submit",
                           gate_run_ref="SUBMIT-%s" % key)
    return out


# ---------------------------------------------------------------------------
# S5 -- THE EVIDENCE THE ANSWER MUST CARRY (reported, never repaired here)
# ---------------------------------------------------------------------------
def provenance_report(conn: sqlite3.Connection, subject: str,
                      subject_kind: str = "table") -> dict[str, Any]:
    """S5: the `wording_source` of ONE subject, through BOTH readers.

    THE MEASURED DEFECT (G5), AND THE ROOT CAUSE IS NOT WHERE I FIRST WROTE IT.
    ------------------------------------------------------------------------
    My plan asserted an ASYMMETRY: "`data_analyze.questions_for` returns `{''}`
    EMPTY while `logic_generator.generate` returns `{'gate'}` FILLED for the SAME
    20 questions". MEASURED 2026-09-28: **that is FALSE**. Both readers read the
    SAME spec through the SAME function, so both returned `32/32 EMPTY` for
    `pattern_template` and `26/26 EMPTY` for `generator_required_field`. A
    finding that compares two readers must first prove they CAN differ.

    THE ACTUAL CAUSE, MEASURED FROM THE SOURCE:
        data_analyze.py: `spec = spec_for(conn, subject_kind, subject)`
        data_analyze.py: `result = generate(spec)`        <-- `conn` NOT passed
    `questions_for(conn, ...)` HOLDS the connection and drops it. So the
    provenance is DISCARDED, not absent: `generate(spec, conn=conn,
    subject_kind=...)` fills it (`{'gate'}` 32/32) for the SAME spec. The fix is
    therefore in `data_analyze.py`, which this plan's Forbidden list protects, so
    this function MEASURES and REPORTS and repairs nothing.

    WHY IT BELONGS ON THE SUBMIT GATE: a form that is accepted and whose answer
    then loses its provenance is indistinguishable, in the stored record, from an
    answer with no evidence — which is the failure the citation rule exists to
    prevent.
    """
    import data_analyze as da
    import logic_generator as lg

    spec = da.spec_for(conn, subject_kind, subject)
    shipped = da.questions_for(conn, subject_kind, subject)["questions"]
    direct = lg.generate(spec, conn=conn, subject_kind=subject_kind)["questions"]

    def _stat(qs: list[dict[str, Any]]) -> dict[str, Any]:
        empty = [i for i, q in enumerate(qs)
                 if not str(q.get("wording_source") or "").strip()]
        return {"questions": len(qs), "empty": len(empty),
                "filled": len(qs) - len(empty),
                "values": sorted({str(q.get("wording_source") or "<empty>")
                                  for q in qs})}

    a, b = _stat(shipped), _stat(direct)
    return {
        "ok": not a["empty"],           # a form whose answer loses its evidence fails
        "subject_kind": subject_kind, "subject": str(subject),
        "same_spec": True,
        "as_shipped": a,
        "when_conn_is_passed": b,
        "cause": ("data_analyze.questions_for holds `conn` and calls "
                  "generate(spec) without it, so the register-supplied wording "
                  "is discarded -- generate(spec, conn=conn, subject_kind=...) "
                  "fills it for the same spec"),
        "cite": "data_analyze.py:174 (generate(spec)) vs generator_form.py:provenance_report",
        "repaired_by_this_module": False,
        "owner": "data_analyze.py (Forbidden in this plan: report only)",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="the generator form submit gate")
    ap.add_argument("--steps", metavar="GEN", help="the 5 steps for one generator")
    ap.add_argument("--all", action="store_true", help="check every generator")
    ap.add_argument("--reads", metavar="GEN", help="the names its validator READS")
    ap.add_argument("--uncovered", metavar="GEN", help="fields read but not declared")
    ap.add_argument("--provenance", metavar="SUBJECT",
                    help="S5: the wording provenance of ONE subject, both readers")
    ap.add_argument("--record", action="store_true", help="write pattern_instance rows")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect()
    try:
        def show(res: dict) -> None:
            if args.json:
                print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
                return
            for k, v in res.items():
                if k in ("steps", "refusals", "fields"):
                    continue
                print("   %-22s %s" % (k, v))
            for s in (res.get("steps") or []):
                print("   %-18s %-5s %s" % (s["step"], "PASS" if s["ok"] else "FAIL",
                                            s.get("observed", "")[:80]))
                if s.get("refusal"):
                    print("        %s" % str(s["refusal"].get("message", ""))[:160])

        if args.all:
            import generator_center as gc
            bad = []
            for g in gc.GENERATORS:
                r = check_generator(conn, g["key"], record=args.record)
                print("== %s" % g["key"])
                show(r)
                if not r.get("ok"):
                    bad.append(g["key"])
            print("FAILING generators: %s" % (bad or "none"))
            return 1 if bad else 0
        if args.steps:
            show(check_generator(conn, args.steps, record=args.record))
            return 0
        if args.reads:
            show(validator_reads(conn, args.reads))
            return 0
        if args.uncovered:
            show(uncovered_fields(conn, args.uncovered))
            return 0
        if args.provenance:
            r = provenance_report(conn, args.provenance)
            if args.json:
                print(json.dumps(r, indent=1, ensure_ascii=False, default=str))
            else:
                print("   subject              %s (%s)" % (r["subject"], r["subject_kind"]))
                print("   as shipped           %s" % r["as_shipped"])
                print("   when conn is passed  %s" % r["when_conn_is_passed"])
                print("   cause                %s" % r["cause"])
                print("   cite                 %s" % r["cite"])
                print("   repaired here?       %s (owner: %s)"
                      % (r["repaired_by_this_module"], r["owner"]))
            return 0 if r["ok"] else 1
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())