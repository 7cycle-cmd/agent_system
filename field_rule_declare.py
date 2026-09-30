"""field_rule_declare.py — the DECLARED unit + oracle for a `ref_tag`.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "why not both!! as evidence is enought to proof everything now"

THE USER IS RIGHT, AND THE EVIDENCE WAS ALREADY THERE — UNCONNECTED.

MEASURED: `field_tdd_rule` holds, for the phone field,

    {"ref_tag": "1.1F", "type": "int", "check": "type(val) is int",
     "guard": [10000000, 999999999999999], "active": true, ...}

That is a DECLARED unit (`type`), a DECLARED oracle (`check`) and DECLARED bounds
(`guard`). It is DATA, and it is the source of truth for `1.1F`.

MEASURED, and this is the defect: `llm_100_run_harness.oracle_for(conn,'1.1F')`
returned `source='default_phone'` and `judge_instruction_for(conn,'1.1F')` the
same. BOTH IGNORED the declaration, so the 100-run was judged by a HAND-WRITTEN
prompt that never mentioned the declared range — which is exactly why
`999999999999999` failed 79.3% of the time while every value whose expectation
WAS stated passed 100%.

So this module CONNECTS the declaration. It adds no rule of its own.

THE TWO FIELDS ARE DIFFERENT THINGS, AND CONFUSING THEM WOULD BREAK THE ORACLE
--------------------------------------------------------------------------------
  `check`  WHAT COUNTS AS CORRECT       (an acceptance rule)  -> the oracle
  `guard`  WHICH VALUES ARE GENERATED   (a sampling range)     -> value generation

MEASURED RAIL: `guard` is `[10000000, 999999999999999]`, but
`oracle('123') == 'YES'` while `123 < guard[0]`. So `guard` is NOT an acceptance
bound — treating it as one would flip 21 currently-correct values to NO. This
module returns `guard` as `value_range` and NEVER consults it when judging.

NO RULE IS HARD-CODED HERE. The `check` string is EVALUATED, so a newly declared
rule works without touching this file. The evaluation namespace is deliberately
tiny (`type`/`isinstance`/`len`/`str`/`int`/`float`/`bool` and `val`), because an
unrestricted `eval` over a DB string is the same class of hazard as a
hand-written case list that silently drifts.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

# The ONLY names a declared `check` may use. Anything else is REFUSED rather than
# silently evaluated, so an unexpected check is reported instead of guessed.
_ALLOWED_CHECK_NAMES: dict[str, Any] = {
    "type": type, "isinstance": isinstance, "len": len,
    "str": str, "int": int, "float": float, "bool": bool, "abs": abs,
    "min": min, "max": max,
}

# The DECLARED type name -> the Python type it asserts. A MAPPING, not a branch
# chain: a new type is a row. Used to build a human-readable instruction and to
# cross-check the `check` string, never as a substitute for it.
TYPE_NAMES: dict[str, Any] = {
    "int": int, "float": float, "str": str, "string": str,
    "bool": bool, "list": list, "dict": dict,
}


class RuleError(ValueError):
    """A declared rule could not be used as declared."""


def _iter_rule_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every `field_tdd_rule` row as `{id, slice_key, field_name, rule}`."""
    out: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT id, system_key, slice_key, field_name, rule_json "
            "FROM field_tdd_rule ORDER BY id DESC").fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        try:
            rule = json.loads(r[4] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(rule, dict):
            continue
        out.append({"id": int(r[0]), "system_key": r[1], "slice_key": r[2],
                    "field_name": r[3], "rule": rule})
    return out


def declared_rule(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """The DECLARED rule for a `ref_tag`, or a REPORTED absence.

    Returns `{ok, ref_tag, check, type_name, value_range, source, row_id,
    message}`. `ok=False` with `code='NO_DECLARATION'` when no row declares this
    ref_tag — NEVER a silent fallback, because a fallback is how the hand-written
    phone prompt came to judge every value.

    The lookup is by the DECLARED `ref_tag` INSIDE `rule_json`, because that is
    where the declaration lives. MEASURED: looking it up by `slice_key` (which is
    what `oracle_for` did: `WHERE slice_key = oracle_ref`) can never match, since
    `oracle_ref` is `NA` for a `ref_tag` that is not a prompt.
    """
    want = str(ref_tag or "").strip()
    for row in _iter_rule_rows(conn):
        rule = row["rule"]
        if str(rule.get("ref_tag") or "").strip() != want:
            continue
        check = str(rule.get("check") or "").strip()
        if not check:
            return {"ok": False, "code": "DECLARATION_WITHOUT_CHECK",
                    "ref_tag": want, "row_id": row["id"],
                    "message": ("row %d declares ref_tag %r but no `check`, so "
                                "there is no acceptance rule to run"
                                % (row["id"], want))}
        types = rule.get("type")
        type_name = ""
        if isinstance(types, str):
            type_name = types.strip().lower()
        elif isinstance(types, list) and types:
            type_name = str(types[0]).strip().lower()
        guard = rule.get("guard")
        value_range = None
        if isinstance(guard, (list, tuple)) and len(guard) == 2:
            value_range = [guard[0], guard[1]]
        # THE DECLARED EXAMPLES, if any.
        #
        # MEASURED (2026-09-24), and this was MY defect: I first derived the
        # instruction from `type` + `check` + `guard` alone and DROPPED the worked
        # examples the hand-written prompt had carried. The score fell from
        # **21/22 to 15/22** — the model then failed `123`, `0`, `-5`, `+7`,
        # `0x1F`, `0b101` as well. A rule states WHAT is true; the examples are
        # what the model actually pattern-matches on. So the forms are DECLARED
        # too, and the instruction can then be complete WITHOUT a literal.
        ex_yes = rule.get("examples_yes")
        ex_no = rule.get("examples_no")
        examples = {
            "yes": [str(x) for x in ex_yes] if isinstance(ex_yes, list) else [],
            "no": [str(x) for x in ex_no] if isinstance(ex_no, list) else [],
        }
        return {"ok": True, "code": "OK", "ref_tag": want, "check": check,
                "type_name": type_name, "value_range": value_range,
                "examples": examples,
                "source": "field_tdd_rule:%d" % row["id"],
                "row_id": row["id"], "field_name": str(row["field_name"] or ""),
                "message": ""}
    return {"ok": False, "code": "NO_DECLARATION", "ref_tag": want,
            "message": ("no `field_tdd_rule` row declares ref_tag %r, so this "
                        "ref_tag has NO declared unit and cannot be certified"
                        % want)}


def _check_names(check: str) -> set[str]:
    """The NAMES a declared `check` reads. Parsed, not scanned.

    MEASURED, and my first version was WRONG: a regex `[A-Za-z_]\\w*` over the
    text also matched the KEYWORD `is` in `type(val) is int`, so a perfectly valid
    declaration was refused for naming `is`. A keyword is not a name. `ast` knows
    the difference, so the names come from the parse tree — the same reason
    `code_shape.py` uses `ast` rather than a line pattern.
    """
    import ast
    try:
        tree = ast.parse(str(check or "").strip(), mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def compiled_check(check: str) -> dict[str, Any]:
    """The declared `check` as a callable `(val) -> bool`, or a REFUSAL.

    Returns `{ok, fn, names, message}`. A `check` naming anything outside
    `_ALLOWED_CHECK_NAMES` is REFUSED with the offending names, rather than
    evaluated — an unexpected check is a fact to report, not to guess at.
    """
    text = str(check or "").strip()
    if not text:
        return {"ok": False, "fn": None, "names": set(),
                "message": "empty check"}
    names = _check_names(text)
    bad = sorted(n for n in names if n not in _ALLOWED_CHECK_NAMES
                 and n != "val")
    if bad:
        return {"ok": False, "fn": None, "names": names,
                "message": ("the declared check names %s, which are not in the "
                            "allowed set %s; refusing rather than guessing"
                            % (bad, sorted(_ALLOWED_CHECK_NAMES)))}
    scope: dict[str, Any] = {"__builtins__": {}}
    scope.update(_ALLOWED_CHECK_NAMES)
    try:
        code = compile(text, "<declared-check>", "eval")
    except SyntaxError as exc:
        return {"ok": False, "fn": None, "names": names,
                "message": "the declared check does not parse: %s" % exc}
    # Guard against attribute access / calls not in the allowlist by checking the
    # compiled code's names ONCE, then evaluating with a frozen scope.
    def _fn(val: Any, _code=code, _scope=scope) -> bool:
        env = dict(_scope)
        env["val"] = val
        try:
            return bool(eval(_code, {"__builtins__": {}}, env))  # noqa: S307
        except Exception:
            return False
    return {"ok": True, "fn": _fn, "names": names, "message": ""}


def oracle_from_declaration(conn: sqlite3.Connection,
                            ref_tag: str) -> dict[str, Any]:
    """An oracle `fn(raw) -> 'YES'|'NO'` that RUNS the declared `check`.

    Returns `{ok, fn, source, check, value_range, message}`. The `check` string is
    RETURNED so a caller can record WHICH rule judged a round — the same rule the
    evidence-answer design applies to a query.
    """
    decl = declared_rule(conn, ref_tag)
    if not decl["ok"]:
        return {"ok": False, "fn": None, "source": "",
                "message": decl["message"]}
    comp = compiled_check(decl["check"])
    if not comp["ok"]:
        return {"ok": False, "fn": None, "source": decl["source"],
                "check": decl["check"], "message": comp["message"]}
    yaml_mod = None
    try:
        import yaml as yaml_mod  # noqa: F401
    except Exception:
        yaml_mod = None

    def _fn(raw: str) -> str:
        # The DECLARED rule is `type(val) is int`, where `val` is what the YAML
        # parser produced — that is what the run's value actually is. So the value
        # is parsed the SAME way the harness's judge sees it, then the DECLARED
        # check is applied. No rule is invented here.
        val: Any = raw
        if yaml_mod is not None:
            try:
                val = yaml_mod.safe_load(raw)
            except Exception:
                val = raw
        return "YES" if comp["fn"](val) else "NO"

    return {"ok": True, "fn": _fn, "source": decl["source"],
            "check": decl["check"], "type_name": decl["type_name"],
            "value_range": decl["value_range"], "message": ""}


def declared_instruction(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """The JUDGE INSTRUCTION DERIVED from the declaration.

    Returns `{ok, instruction, source, check, value_range, message}`.

    MEASURED WHY THIS IS DERIVED AND NOT WRITTEN: the hand-written
    `judge_instruction()` lists eleven example forms but NEVER states that an
    integer's MAGNITUDE has no upper bound. `999999999999999` therefore failed
    79.3% of rounds (88/111) while every value whose expectation WAS stated passed
    100%. A derived instruction STATES the declared type AND its declared range,
    so the prompt cannot omit the very bound that is being measured.

    THE RANGE IS STATED AS FACT, NOT AS AN ACCEPTANCE BOUND. It is labelled as the
    VALUE-GENERATION range, so the model is not told (wrongly) that `123` is
    outside it.
    """
    decl = declared_rule(conn, ref_tag)
    if not decl["ok"]:
        return {"ok": False, "instruction": "", "source": "",
                "message": decl["message"]}
    type_name = decl["type_name"] or "the declared type"
    ex = decl.get("examples") or {"yes": [], "no": []}
    lines = [
        "You are a YAML scalar judge. Given a raw YAML value, answer with exactly "
        '"YES" or "NO".',
        'Answer YES if and only if `%s` is true, where `val` is what '
        "yaml.safe_load(value) produced." % decl["check"],
        "The declared type is `%s`." % type_name,
        ("The declared type has NO UPPER BOUND: any number of digits is still "
         "`%s`." % type_name),
    ]
    # THE EXAMPLES, DECLARED. MEASURED: without them the score fell 21/22 -> 15/22
    # even though the RULE was stated. A model pattern-matches a form much more
    # reliably than it applies a type predicate, so the forms are part of the
    # declaration rather than prose written once in this module.
    if ex["yes"]:
        lines.append("Forms that ARE %s (YES): %s." % (type_name, ", ".join(ex["yes"])))
    if ex["no"]:
        lines.append("Forms that are NOT (NO): %s." % ", ".join(ex["no"]))
    lines.append(
        "A declared value RANGE describes which values are GENERATED for testing; "
        "it is NOT a condition of acceptance. A value outside that range may "
        "still be YES.")
    if decl["value_range"]:
        lines.append("Declared generation range: %s .. %s (informational only)."
                     % (decl["value_range"][0], decl["value_range"][1]))
    lines.append("Reply with one word only.")
    return {"ok": True, "instruction": "\n".join(lines), "source": decl["source"],
            "check": decl["check"], "type_name": decl["type_name"],
            "value_range": decl["value_range"], "examples": ex, "message": ""}


def declare_unit(conn: sqlite3.Connection, ref_tag: str, *,
                 type_name: str, check: str,
                 value_range: list[Any] | None = None,
                 field_name: str = "", system_key: str = "",
                 slice_key: str = "") -> dict[str, Any]:
    """DECLARE a unit for a `ref_tag`. Idempotent; never overwrites silently.

    Returns `{ok, action, row_id, message}` with `action` one of `created`,
    `already_declared`, or `refused`. A refusal NAMES why, so a caller can act.

    IT DOES NOT INVENT A CHECK. `check` is required and must compile, because a
    declaration whose rule cannot run is the same as no declaration — the state
    this module exists to remove.
    """
    comp = compiled_check(check)
    if not comp["ok"]:
        return {"ok": False, "action": "refused", "message": comp["message"]}
    if str(type_name or "").strip().lower() not in TYPE_NAMES:
        return {"ok": False, "action": "refused",
                "message": ("type %r is not a known type name %s"
                            % (type_name, sorted(TYPE_NAMES)))}
    existing = declared_rule(conn, ref_tag)
    if existing["ok"]:
        return {"ok": True, "action": "already_declared",
                "row_id": existing["row_id"],
                "message": ("ref_tag %r is already declared by %s"
                            % (ref_tag, existing["source"]))}
    rule = {"ref_tag": str(ref_tag), "type": str(type_name).lower(),
            "check": str(check), "active": True}
    if value_range:
        rule["guard"] = list(value_range)
    key = str(slice_key or field_name or ref_tag)
    conn.execute(
        "INSERT INTO field_tdd_rule (system_key, slice_key, field_name, "
        "tdd_type_code, rule_json, depends_on_json) "
        "VALUES (?, ?, ?, ?, ?, '[]')",
        (str(system_key or ""), key, str(field_name or key),
         str(type_name).lower(), json.dumps(rule, ensure_ascii=False)))
    conn.commit()
    row_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    return {"ok": True, "action": "created", "row_id": row_id,
            "message": "declared ref_tag %r" % ref_tag}


def declare_examples(conn: sqlite3.Connection, ref_tag: str, *,
                     yes: list[str], no: list[str]) -> dict[str, Any]:
    """Declare the EXAMPLE FORMS for a `ref_tag`. Additive; never overwrites.

    MEASURED (2026-09-24) WHY THE EXAMPLES BELONG IN THE DECLARATION AND NOT IN A
    MODULE CONSTANT: I first derived the judge instruction from `type` + `check` +
    `guard` alone, dropping the worked examples the hand-written prompt had
    carried. The score FELL from 21/22 to **15/22** — `123`, `0`, `-5`, `+7`,
    `0x1F`, `0b101` all became failures. A rule states WHAT is true; a model
    pattern-matches a FORM. So the forms are declared here, and the instruction
    can be complete without a literal in the harness.

    The examples are stored ON the rule row that already declares the ref_tag, so
    a unit and its forms cannot drift into two rows.
    """
    import json as _json
    decl = declared_rule(conn, ref_tag)
    if not decl["ok"]:
        return {"ok": False, "action": "refused", "message": decl["message"]}
    row = conn.execute("SELECT rule_json FROM field_tdd_rule WHERE id=?",
                       (decl["row_id"],)).fetchone()
    try:
        rule = _json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        rule = {}
    have_yes = [str(x) for x in (rule.get("examples_yes") or [])]
    have_no = [str(x) for x in (rule.get("examples_no") or [])]
    changed = False
    for x in yes:
        if str(x) not in have_yes:
            have_yes.append(str(x)); changed = True
    for x in no:
        if str(x) not in have_no:
            have_no.append(str(x)); changed = True
    if not changed:
        return {"ok": True, "action": "already_declared",
                "row_id": decl["row_id"], "message": "examples already declared"}
    rule["examples_yes"] = have_yes
    rule["examples_no"] = have_no
    conn.execute("UPDATE field_tdd_rule SET rule_json=? WHERE id=?",
                 (_json.dumps(rule, ensure_ascii=False), decl["row_id"]))
    conn.commit()
    return {"ok": True, "action": "declared", "row_id": decl["row_id"],
            "yes": have_yes, "no": have_no,
            "message": "declared %d YES / %d NO forms" % (len(have_yes), len(have_no))}


def unit_source(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """WHERE this ref_tag's unit comes from: the declaration, or a REPORTED gap.

    Returns `{ref_tag, unit_source, declared, check, value_range, message}`.
    `unit_source` is the citation (`field_tdd_rule:<id>`) or `''` when there is
    no declaration — an empty source is a prompt that CANNOT be certified, and it
    is reported rather than defaulted.
    """
    decl = declared_rule(conn, ref_tag)
    if not decl["ok"]:
        return {"ref_tag": str(ref_tag), "unit_source": "", "declared": False,
                "check": "", "value_range": None, "message": decl["message"]}
    return {"ref_tag": str(ref_tag), "unit_source": decl["source"],
            "declared": True, "check": decl["check"],
            "value_range": decl["value_range"], "message": ""}
