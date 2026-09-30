"""Phone 100 連勝 proof harness v2 (llm_100_run).

Multi-model routing + streak metric. Uniform random sampling with replacement
over the option set. Streak = consecutive correct answers; target N = options*5
= 110 -> QUALIFIED; cap 1000 rounds per (model, rule_version) -> NOT_QUALIFIED.
No auto-apply of instruction improvements.
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from coord_store import (
    _CREATE_LLM_100_RUN_SQL,
    init_llm_100_run_table,
    migrate_llm_100_run_model,
    migrate_llm_100_run_dim_key,
    migrate_proof_run_layer_key,
    migrate_proof_run_value_type,
)

DB_PATH = Path(__file__).resolve().parent / "agent.db"
JUDGE_SKILL_ID = "skill_phone_100_judge"
# THE FALLBACK, NOT THE SOURCE.
#
# DEFECT FOUND BY MEASURING IT (2026-09-21): this was the ONLY way the harness
# chose a model, so the 100 run's provider was a Python literal rather than a
# registry row. The registry (`llm_route_provider` for the `llm.text` route)
# already names `qwen2.5:7b-instruct` at priority 10, so the literal and the
# registry agreed BY LUCK — and would silently disagree the moment either moved.
# `resolve_model()` reads the registry; this constant is used only when the
# registry is unreadable, and the fallback is RECORDED.
DEFAULT_MODEL = "qwen2.5:7b-instruct"
# The route whose provider pool serves the 100 run.
JUDGE_SERVICE_KEY = "llm.text"
CAP_ROUNDS = 1000
# THE PHONE FALLBACK, NOT THE GENERAL TARGET (2026-09-22).
#
# MEASURED: this was the ONLY target, hard-coded as `options(22) * 5`. That is
# the PHONE case, so every prompt would be judged by the phone's number.
# `streak_target_for(conn, ref_tag)` now DERIVES the target from the prompt's own
# `threshold`, and `run_harness` uses it. This constant remains as the fallback
# for a ref_tag that is not a prompt, and the result RECORDS which was used
# (`target_source`), so a fallback is visible rather than silent.
STREAK_TARGET = 110  # options(22) * 5 — the PHONE case


def resolve_model(conn: sqlite3.Connection | None = None,
                  *, llm_route: str = JUDGE_SERVICE_KEY,
                  transport: str = "http",
                  db_path: Path | str | None = None) -> dict[str, Any]:
    """The model for a route, read from the DB-driven service registry.

    Returns `{model, llm_route, source}` where `source` is `registry` or
    `fallback`. The source is RETURNED, not assumed: a silent fallback would make
    a 100-run report a model it never actually resolved.

    `transport='http'` IS A MEASURED FIX (2026-09-28). MEASURED: this function
    returned `pool[0]` = `convaiinnovations/laya`, and the harness then called
    `vision_analyze.complete_text`, which POSTs to Ollama — which answered
    `ollama_http_404: model 'convaiinnovations/laya' not found`. **The registry
    was RIGHT and this CALLER was wrong**: laya is a Python LIBRARY, not an HTTP
    model, so no Ollama call can ever reach it.

    The harness speaks HTTP, so it asks for the HTTP pool. A caller that can
    IMPORT a library asks for `transport='python'` instead. The pool is now a
    list of providers the caller can actually reach, rather than a list of names.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        import llm_service_store as lss

        pool = lss.pool_for(conn, llm_route, transport=transport)
        if pool:
            return {"model": str(pool[0]), "llm_route": llm_route,
                    "source": "registry", "transport": transport,
                    "pool": [str(m) for m in pool]}
    except Exception:
        pass
    finally:
        if own:
            conn.close()
    return {"model": DEFAULT_MODEL, "llm_route": llm_route,
            "source": "fallback", "transport": transport, "pool": []}

# Option set (22 values): ~50% int / 50% non-int.
# NOTE: 0o17 is NOT int in PyYAML (parses as str) — kept in non-int forms.
OPTIONS = [
    # int forms
    "123", "0", "-5", "+7", "0123", "0x1F", "0b101", "1_234",
    "+85291234567", "999999999999999",
    # non-int forms
    '"123"', "true", "false", "True", "9.5", "1e3", ".5", "null", "~",
    "0o17", '"9123-4567"', '"+852 9123 4567"',
]


def oracle(raw: str) -> str:
    """Return 'YES' if yaml.safe_load(raw) is int else 'NO'.

    THE PHONE ORACLE. Kept as the DEFAULT so existing behaviour is unchanged, but
    it is no longer the ONLY oracle: `run_harness(oracle_fn=...)` takes one, and
    `oracle_for(conn, ref_tag)` resolves a prompt's own oracle from its
    `oracle_ref`.

    WHY (user, 2026-09-22): "first_principle can be the qc pass / fail for prompt
    output? ... measured unit is the key". The oracle IS the pass/fail
    definition, so a hard-coded phone oracle means the harness can prove exactly
    ONE prompt. `OPTIONS` (22 values) and `STREAK_TARGET = 110` (22 x 5) are
    phone-specific for the same reason.
    """
    try:
        val = yaml.safe_load(raw)
    except Exception:
        return "NO"
    return "YES" if type(val) is int else "NO"


# ---------------------------------------------------------------------------
# THE GENERIC ORACLE (2026-09-22)
# ---------------------------------------------------------------------------
# MEASURED, and it is the blocker for proving ANY prompt other than the phone:
#
#   * `OPTIONS` (22 values) are all "is this an int?" test values — the PHONE
#     field's TDD. `gen_values` samples from them, so EVERY prompt would be fed
#     the phone's test values.
#   * `oracle()` is the phone's ground truth. For `worker_identity` or
#     `mouse_spot_verify` it answers the WRONG QUESTION.
#
# So running a proof run on the 38 prompts today would write a number about the
# PHONE onto `worker_identity` — not a proof, a FABRICATED one, into the ONLY
# evidence table.
#
# THE GENERIC ORACLE ALREADY EXISTS: `skill_contract_store.
# validate_payload_against_contract` decides pass/fail from a contract's
# `rule_kind` / `rule_value_json`, and `RULE_KINDS = ("const", "non_blank",
# "type", "length")` is the vocabulary. That is a DECLARATIVE rule — data, not
# code — so a prompt's oracle can be resolved from its contract instead of from
# a Python function.
#
# `oracle_from_contract` wraps it as an `oracle_fn`, so `run_harness` can use it
# unchanged.
def oracle_from_contract(conn: sqlite3.Connection, contract_id: str,
                         field_name: str | None = None
                         ) -> Callable[[str], str]:
    """An `oracle_fn` that decides pass/fail from a CONTRACT's machine rule.

    The value under test is parsed with `yaml.safe_load` (so `"123"` is the
    string `123` and `123` is the int), then validated against the contract's
    `rule_kind` / `rule_value_json`.

    `field_name=None` validates the WHOLE PAYLOAD against EVERY field of the
    contract. DEFECT FOUND BY MEASURING IT (2026-09-22): `oracle_for` passed the
    PROMPT KEY as the field name, so `worker_identity` was looked up as a
    contract field — which does not exist — and the oracle answered NO for a
    COMPLETE identity block. A prompt whose subject IS the contract (an identity
    BLOCK, not one field) must validate the whole payload.

    Returns 'YES' when the value SATISFIES the rule, 'NO' otherwise. A contract
    with no machine rule for the field cannot decide, so it returns 'NO' — an
    undecidable case is not a pass.
    """
    import skill_contract_store as scs

    def _fn(raw: str) -> str:
        try:
            val = yaml.safe_load(raw)
        except Exception:
            return "NO"
        if field_name is None:
            if not isinstance(val, dict):
                return "NO"
            payload = val
        else:
            payload = {field_name: val}
        ok, _errs = scs.validate_payload_against_contract(
            contract_id, payload, conn=conn)
        return "YES" if ok else "NO"

    return _fn


def contract_fields(conn: sqlite3.Connection, contract_id: str) -> list[dict]:
    """The contract's fields, in order. `[]` when the contract is unknown."""
    try:
        rows = conn.execute(
            "SELECT field_name, mandatory, rule_kind, rule_value_json FROM "
            "skill_contract_field WHERE contract_id=? ORDER BY id",
            (contract_id,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def study_fields(conn: sqlite3.Connection, study_key: str) -> list[dict]:
    """A study's DECLARED fields, in order. `[]` when the study is unknown.

    THE STUDY IS A DECLARATION, NOT A NOTE. MEASURED (2026-09-27): 36 of the 39
    active prompts carry `oracle_ref='NA'` AND `study_id=2`, whose `fields_json`
    is `[{"sort":1,"field":"target_name","value":"Chrome"}]`. The study SAYS what
    to measure; the phone question measures something else.

    `declaration_for_ref_tag` already FINDS this study and returns
    `{"kind": "study", "ref": "mouse_spot_case"}` -- but `oracle_for` never asked,
    so all 36 fell through to `default_phone` and would have produced a
    FABRICATED proof. This is the reader that makes the declaration reachable.
    """
    try:
        row = conn.execute(
            "SELECT fields_json FROM study_registry WHERE study_key=?",
            (str(study_key),)).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row:
        return []
    try:
        fields = json.loads(str(row["fields_json"] or "[]"))
    except (ValueError, TypeError):
        return []
    return [f for f in fields if isinstance(f, dict) and f.get("field")]


def oracle_from_study(conn: sqlite3.Connection, study_key: str,
                      field: str | None = None) -> Callable[[str], str]:
    """An `oracle_fn` that decides pass/fail from a STUDY's declared fields.

    THE SAME SHAPE AS `oracle_from_contract`, because a study and a contract are
    both DECLARATIONS: the value under test is parsed, then compared against what
    the declaration says.

    `field=None` checks EVERY declared field (the study's whole subject).
    `field='target_name'` checks ONE field, so a run measures that field rather
    than the whole block.

    Returns 'YES' when the value MATCHES the declaration, 'NO' otherwise. A study
    with NO declared fields cannot decide, so it returns 'NO' -- an undecidable
    case is not a pass (the same rule `oracle_from_contract` follows).
    """
    declared = study_fields(conn, study_key)
    if field is not None:
        declared = [f for f in declared if str(f.get("field")) == str(field)]

    def _fn(raw: str) -> str:
        if not declared:
            return "NO"
        try:
            val = yaml.safe_load(raw)
        except Exception:
            return "NO"
        # A study declares `field` + `value`. The value under test must MATCH the
        # declared one. A field with no declared `value` is a PRESENCE check, so
        # any non-empty answer satisfies it.
        for f in declared:
            if "value" not in f:
                if val is None or (isinstance(val, str) and not val.strip()):
                    return "NO"
                continue
            want = f.get("value")
            if isinstance(want, str) and isinstance(val, str):
                if val.strip() != want.strip():
                    return "NO"
            elif val != want:
                return "NO"
        return "YES"

    return _fn


def options_for_study(conn: sqlite3.Connection, study_key: str,
                      field: str | None = None) -> list[str]:
    """The test VALUES for a study, DERIVED from its declared fields.

    THE SAME SHAPE AS `options_for_contract`, because a study and a contract are
    both DECLARATIONS. A study declares `field` + `value`, which is the `const`
    shape: the DECLARED value (which must pass) and a DIFFERENT one (which must
    fail). A field with no `value` is a PRESENCE check, so the two cases are a
    non-blank value and a blank one.

    MEASURED DEFECT THIS CLOSES (2026-09-27): `options_for_ref_tag` had NO study
    branch, so all 36 study prompts fell back to the phone's 22 values and
    `run_harness` REFUSED them with `DECLARATION_EXISTS_BUT_FELL_BACK`
    (`fell_back: ["options"]`). The refusal was CORRECT -- the run would have
    measured the phone. This is the resolution it was waiting for.

    Returns `[]` when the study declares no fields -- "cannot say what to test"
    is REPORTED, never filled with the phone's values.
    """
    declared = study_fields(conn, study_key)
    if field is not None:
        declared = [f for f in declared if str(f.get("field")) == str(field)]
    if not declared:
        return []
    out: list[str] = []
    for f in declared:
        if "value" in f:
            want = f.get("value")
            # The DECLARED value, then a DIFFERENT one. The different one is
            # derived from the declared one's TYPE, so it is a real wrong answer
            # rather than an arbitrary string.
            out.append(json.dumps(want))
            if isinstance(want, str):
                out.append(json.dumps(want + "_WRONG"))
            elif isinstance(want, bool):
                out.append(json.dumps(not want))
            elif isinstance(want, (int, float)):
                out.append(json.dumps(want + 1))
            else:
                out.append(json.dumps("__WRONG__"))
        else:
            # A PRESENCE check: a non-blank answer and a blank one.
            out.append(json.dumps("a real answer"))
            out.append(json.dumps("   "))
    return out


def _sample_value_for(field: dict) -> str:
    """A value that SATISFIES one field's rule, as a YAML scalar string."""
    kind = str(field.get("rule_kind") or "").strip()
    try:
        val = json.loads(str(field.get("rule_value_json") or "null"))
    except Exception:
        val = None
    if kind == "const":
        return json.dumps(val)
    if kind == "type":
        return {"int": "123", "str": '"abc"', "bool": "true",
                "float": "9.5", "list": "[1,2]", "dict": "{a: 1}"}.get(
                    str(val), '"abc"')
    if kind == "length":
        try:
            return '"%s"' % ("a" * int(val))
        except (TypeError, ValueError):
            return '"abc"'
    # `non_blank` and anything else: a non-blank string satisfies it.
    return '"abc"'


def options_for_contract(conn: sqlite3.Connection, contract_id: str,
                         field_name: str | None = None) -> list[str]:
    """The test VALUES for a contract, DERIVED from its rules.

    MEASURED: `OPTIONS` is the phone's 22 values, so every prompt would be fed
    them. A contract's own rule says what to test:

      * `type`      -> values of the RIGHT type and of WRONG types
      * `length`    -> values of the right length and of wrong lengths
      * `const`     -> the required value and a different one
      * `non_blank` -> a non-blank value and a blank one

    `field_name=None` builds WHOLE-PAYLOAD cases from EVERY field: one COMPLETE
    payload (all fields present and satisfying their rule) and one payload per
    REQUIRED field with that field REMOVED. That is what a contract whose subject
    is a BLOCK — an identity block, not one field — must be tested with.

    Returns a list of YAML-ish strings, so `gen_values` can sample from it. An
    empty list means the contract cannot say what to test — which is REPORTED,
    not filled with the phone's values.
    """
    import skill_contract_store as scs
    fields = contract_fields(conn, contract_id)
    if not fields:
        return []
    if field_name is None:
        complete = {f["field_name"]: yaml.safe_load(_sample_value_for(f))
                    for f in fields}
        out = [json.dumps(complete)]
        for f in fields:
            if not int(f.get("mandatory") or 0):
                continue
            broken = dict(complete)
            broken.pop(f["field_name"], None)
            out.append(json.dumps(broken))
        return out
    rows = [f for f in fields if f["field_name"] == field_name]
    if not rows:
        return []
    r = rows[0]
    kind = str(r.get("rule_kind") or "").strip()
    try:
        val = json.loads(str(r.get("rule_value_json") or "null"))
    except Exception:
        val = None
    if kind == "type":
        want = str(val)
        # The RIGHT type, then values of OTHER types.
        right = {"int": ["123", "0", "-5"], "str": ['"abc"', '"x"'],
                 "bool": ["true", "false"], "float": ["9.5", "1e3"],
                 "list": ["[1,2]", "[]"], "dict": ["{a: 1}", "{}"]}.get(
                     want, ["123"])
        wrong = [v for k, vs in (("int", ["123"]), ("str", ['"abc"']),
                                 ("bool", ["true"]), ("float", ["9.5"]))
                 if k != want for v in vs]
        return right + wrong
    if kind == "length":
        try:
            n = int(val)
        except (TypeError, ValueError):
            return []
        return ['"%s"' % ("a" * n), '"%s"' % ("a" * (n + 1)),
                '"%s"' % ("a" * max(0, n - 1))]
    if kind == "const":
        return [json.dumps(val), json.dumps("__different__")]
    if kind == "non_blank":
        return ['"abc"', '""', '"   "']
    return []


def flow_final_prompt(conn: sqlite3.Connection, flow_key: str
                      ) -> dict[str, Any] | None:
    """The prompt a flow ENDS on — its `is_final=1` step's prompt.

    A flow is a METHOD; the method's pass/fail is decided by the question it ends
    on. So the flow's oracle is the FINAL step's prompt's oracle, and this
    resolves that prompt.

    Returns `None` when the flow has no final step, or the step names no prompt —
    a flow that ends on nothing cannot decide anything.
    """
    try:
        row = conn.execute(
            "SELECT s.prompt_id, s.step_no FROM workflow_step s JOIN "
            "workflow_registry w ON w.workflow_id = s.workflow_id WHERE "
            "w.workflow_key=? AND s.is_final=1 ORDER BY s.step_no DESC LIMIT 1",
            (str(flow_key),)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    pid = int(row["prompt_id"])
    try:
        p = conn.execute("SELECT prompt_id, prompt_key, oracle_ref, threshold "
                         "FROM prompt_registry WHERE prompt_id=?",
                         (pid,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not p:
        return None
    return {"prompt_id": pid, "prompt_key": str(p["prompt_key"]),
            "oracle_ref": str(p["oracle_ref"] or "NA"),
            "threshold": str(p["threshold"] or "NA"),
            "step_no": int(row["step_no"])}


def declaration_for_ref_tag(conn: sqlite3.Connection, ref_tag: str
                            ) -> dict[str, Any] | None:
    """The DECLARATION a `ref_tag` should have resolved through, or `None`.

    THE GATE'S QUESTION (added 2026-09-26). MEASURED: 38 of 39 prompts resolve
    to `source='default_phone'`, and `run_harness` RECORDS that source but never
    CHECKS it. So a prompt whose `oracle_ref` is 'NA' silently measures the
    PHONE while the run is reported as evidence about something else.

    This answers "does a DECLARATION exist for this ref_tag?" — which is what
    makes the fallback a CONTRADICTION rather than a legitimate default:

      * a ref_tag WITH a declaration that fell back -> the phone question is the
        WRONG question, and the run must be REFUSED;
      * a ref_tag with NO declaration (the phone's own `1.1F`) -> the fallback is
        the ONLY resolution, and the run is legitimate.

    TWO SOURCES, and BOTH are needed. MEASURED, and this is why the first
    version of this function was WRONG:

      * A CONTRACT, via the prompt's `oracle_ref`. This CANNOT see the defect it
        was written for: the defect IS `oracle_ref='NA'`, so a lookup BY
        `oracle_ref` returns `None` and the gate could never fire on
        `worker_identity_confirm`. It still catches a DIFFERENT real case — a
        contract that exists but has NO fields, so `judge_instruction_for` falls
        back at its `if not fields` branch.
      * A STUDY, via the prompt's `study_id`. MEASURED: `worker_identity_confirm`
        has `oracle_ref='NA'` AND `study_id=1` whose `fields_json` declares
        SESSION_ID, MODEL, CHAT_ID, CHAT_SHA256. The study SAYS what to measure;
        the phone question measures something else. THIS is the source that
        fires on the actual defect.

    A FLOW resolves through its FINAL step's prompt, so a flow inherits its
    final prompt's declaration — the same rule `oracle_for` follows.

    Returns `{kind, ref}` or `None`. Never raises: an absent register means
    "cannot tell", which is `None` (no declaration), not a crash.
    """
    tag = str(ref_tag or "").strip()
    if not tag:
        return None
    try:
        import prompt_registry as pr
        resolved = pr.resolve_ref_tag(conn, tag)
    except Exception:
        return None
    kind = str(resolved.get("kind") or "")
    prompt_key = tag
    if kind == "flow":
        fp = flow_final_prompt(conn, tag)
        if not fp:
            return None
        prompt_key = str(fp["prompt_key"])
    elif kind != "prompt":
        # An entity id / job_ref / unknown: no prompt, so no declaration to miss.
        return None
    try:
        row = conn.execute(
            "SELECT oracle_ref, study_id FROM prompt_registry WHERE prompt_key=?",
            (prompt_key,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None

    # (1) A CONTRACT named by `oracle_ref`.
    ref = str(row["oracle_ref"] or "NA").strip()
    if ref not in ("", "NA"):
        try:
            hit = conn.execute(
                "SELECT contract_id FROM skill_contract_template WHERE "
                "contract_id=? AND status='active'", (ref,)).fetchone()
        except sqlite3.OperationalError:
            hit = None
        if hit:
            return {"kind": "contract", "ref": str(hit["contract_id"])}

    # (2) A STUDY that DECLARES fields. This is the source that sees the defect.
    study_id = row["study_id"]
    if study_id is not None:
        try:
            s = conn.execute(
                "SELECT study_key, fields_json FROM study_registry WHERE "
                "study_id=?", (study_id,)).fetchone()
        except sqlite3.OperationalError:
            s = None
        if s:
            try:
                fields = json.loads(str(s["fields_json"] or "[]"))
            except (ValueError, TypeError):
                fields = []
            if fields:
                return {"kind": "study", "ref": str(s["study_key"])}
    return None


def _binding_parts(ref_tag: str) -> tuple[str, str] | None:
    """`(subject_kind, dimension_key)` when `ref_tag` is a dimension binding.

    THE FORM IS DERIVED, NOT PARSED BY A SECOND RULE.
    `dimension_binding_registry.binding_ref_tag` builds
    `dimension_binding.{subject_kind}.{dimension_key}`, so this reads the SAME
    shape back. A second format would let the writer and the reader disagree.

    Returns `None` for anything else, so a caller can fall through rather than
    guess.
    """
    parts = str(ref_tag or "").strip().split(".")
    if len(parts) == 3 and parts[0] == "dimension_binding":
        return parts[1], parts[2]
    return None


def _binding_oracle(conn: sqlite3.Connection, dimension_key: str
                    ) -> Callable[[str], str]:
    """An `oracle_fn` for ONE 5W1H dimension, judged by the SKILL.5W1H contract.

    WHY A DEDICATED WRAPPER AND NOT `oracle_from_contract(field_name=...)`.
    MEASURED (2026-09-26): `validate_payload_against_contract` validates the
    WHOLE payload, and all six 5W1H fields are `mandatory=1`. So
    `oracle_from_contract(conn, "SKILL.5W1H", field_name="what")` builds
    `{"what": <answer>}` and the validator refuses with
    `Missing required field: how` — for EVERY answer, blank or not. MEASURED:
    both a real answer and a blank one returned `NO`, so the oracle could not
    decide anything.

    THE FIX IS TO FILL THE OTHER FIVE WITH A VALID PLACEHOLDER. They are NOT
    under test, so giving them a value that satisfies `non_blank` is honest: the
    run measures ONE dimension, and the other five are held constant. A
    placeholder that FAILED its own rule would make the oracle refuse for a
    reason unrelated to the dimension being measured.

    Returns 'YES' when the answer satisfies the dimension's rule, 'NO'
    otherwise. An unreadable contract returns 'NO' — an undecidable case is not
    a pass.
    """
    import skill_contract_store as scs

    # The six dimensions, from the ONE place they are declared.
    try:
        import skill_5w1h as fw
        names = list(fw.DIMENSION_NAMES)
    except Exception:  # noqa: BLE001
        names = ["what", "why", "who", "when", "where", "how"]
    others = [n for n in names if n != dimension_key]

    def _fn(raw: str) -> str:
        try:
            val = yaml.safe_load(raw)
        except Exception:
            return "NO"
        # A non-string answer cannot satisfy `non_blank` (which applies to
        # strings), so it is a NO rather than a crash.
        if not isinstance(val, str):
            return "NO"
        payload = {n: "held constant (not under test)" for n in others}
        payload[dimension_key] = val
        ok, _errs = scs.validate_payload_against_contract(
            "SKILL.5W1H", payload, conn=conn)
        return "YES" if ok else "NO"

    return _fn


def oracle_for(conn: sqlite3.Connection, ref_tag: str,
               *, spec: dict[str, Any] | None = None,
               question: dict[str, Any] | None = None) -> dict[str, Any]:
    """The oracle for a `ref_tag`, resolved from the prompt's `oracle_ref`.

    Returns `{fn, source, oracle_ref}`. `source` is RETURNED, not assumed: a
    silent fallback would make a 100-run report an oracle it never resolved —
    the same rule `resolve_model` follows.

    A prompt whose `oracle_ref` is still 'NA' falls back to the phone oracle and
    SAYS SO, so the fallback is visible rather than silent.

    A FLOW resolves through its FINAL step's prompt (2026-09-22). MEASURED: two
    flows shared the ref_tag `worker_identity`, so `proof_run` could not say WHICH
    flow a round measured. A flow now has its OWN ref_tag, and its oracle is the
    oracle of the question it ENDS on.

    THE `evidence` SOURCE (2026-09-23). The user:

        "logic generator can help to have the answer by evidence"

    MEASURED (`_diag_evidence_answer.py`): `logic_generator.generate()` emits 6
    question families, EVERY one answerable from the DB — and no code connected a
    `question_id` to a query, so this function fell back to `default_phone`, a
    HARDCODED oracle that answers the PHONE's question. A run judged by it
    measures the phone, not the table.

    So when a `spec` and a `question` are supplied AND the question has an
    evidence answer, the EVIDENCE oracle is used and `source='evidence'` is
    returned. It is tried BEFORE the phone fallback, because evidence is a
    measurement while the phone oracle is an assertion.
    """
    # ---- THE EVIDENCE ORACLE, tried FIRST --------------------------------
    # Before the prompt lookup, because a spec + question is a DIRECT statement
    # of what is being measured, while a ref_tag has to be resolved to one.
    if spec is not None and question is not None:
        try:
            import logic_generator as lg
            r = lg.answer_by_evidence(conn, spec, question)
        except Exception as exc:
            r = {"ok": False, "code": "EVIDENCE_FAILED",
                 "message": "%s: %s" % (type(exc).__name__, exc)}
        if r.get("ok"):
            return {"fn": lg.oracle_from_evidence(conn, spec, question),
                    "source": "evidence", "oracle_ref": "NA",
                    "evidence_ref": r["evidence_ref"],
                    "question_id": r["question_id"],
                    "reason": ("question %r is answered by evidence: %s"
                               % (r["question_id"], r["evidence_ref"]))}
        # A question with NO evidence answer is REPORTED and then falls through
        # to the normal resolution — never silently, and never as a guess.
        evidence_refusal = {"code": r.get("code"),
                            "message": str(r.get("message") or "")[:160]}
    else:
        evidence_refusal = None

    # ---- THE DECLARED RULE, resolved by the `ref_tag` ITSELF ----------------
    #
    # MEASURED (2026-09-24), and this is the defect the user named:
    # "why not both!! as evidence is enought to proof everything now".
    #
    # `field_tdd_rule` ALREADY holds, for `1.1F`,
    #   {"ref_tag":"1.1F","type":"int","check":"type(val) is int",
    #    "guard":[10000000,999999999999999],"active":true}
    # — a DECLARED unit, a DECLARED oracle and DECLARED bounds. And BOTH this
    # function and `judge_instruction_for` IGNORED it, judging the run with a
    # HAND-WRITTEN phone prompt instead. That is why `999999999999999` failed
    # 79.3% of rounds: the declared range was never stated to the model.
    #
    # THE LOOKUP IS BY `ref_tag`, NOT BY `oracle_ref`. MEASURED: the old path
    # queried `WHERE slice_key = oracle_ref`, but `oracle_ref` is `NA` for a
    # `ref_tag` that is not a prompt, so it could NEVER match — the declaration
    # was unreachable by construction.
    #
    # It is tried FIRST because a declaration is DATA about THIS ref_tag, while
    # the phone oracle is a literal about a DIFFERENT one.
    try:
        import field_rule_declare as _frd
        _decl = _frd.oracle_from_declaration(conn, ref_tag)
    except Exception as exc:
        _decl = {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}
    if _decl.get("ok"):
        return {"fn": _decl["fn"], "source": "field_rule",
                "oracle_ref": "NA", "rule_ref": _decl["source"],
                "declared_check": _decl["check"],
                "value_range": _decl.get("value_range"),
                "evidence_refusal": evidence_refusal,
                "reason": ("ref_tag %r is judged by its DECLARED rule %r from %s"
                           % (ref_tag, _decl["check"], _decl["source"]))}

    # A FLOW FIRST, because a flow key is NOT a prompt key — `get_prompt` would
    # return None for it and the run would silently fall back to the phone.
    try:
        import prompt_registry as pr
        resolved = pr.resolve_ref_tag(conn, ref_tag)
    except Exception:
        resolved = {"kind": "unknown"}
    if resolved.get("kind") == "flow":
        fp = flow_final_prompt(conn, ref_tag)
        if not fp:
            return {"fn": oracle, "source": "default_phone", "oracle_ref": "NA",
                    "evidence_refusal": evidence_refusal,
                    "reason": ("flow %r has no final step naming a prompt, so "
                               "the phone oracle is used — REPORTED, not silent"
                               % ref_tag)}
        # Recurse on the FINAL step's prompt, so the flow inherits the prompt's
        # own resolution (contract / field_rule / fallback) rather than a second
        # copy of that logic.
        inner = oracle_for(conn, fp["prompt_key"])
        return {"fn": inner["fn"], "source": inner["source"],
                "oracle_ref": inner.get("oracle_ref"),
                "field": inner.get("field"),
                "flow_key": str(ref_tag), "final_step": fp["step_no"],
                "final_prompt": fp["prompt_key"],
                "reason": ("flow %r resolves through its final step %d -> prompt "
                           "%r" % (ref_tag, fp["step_no"], fp["prompt_key"]))}

    try:
        import prompt_registry as pr
        p = pr.get_prompt(conn, ref_tag)
    except Exception:
        p = None
    if not p:
        # A DIMENSION BINDING. THE HUMAN (2026-09-26): "evidence can help you to
        # find B / have all data to find the way" then "gogogo".
        #
        # MEASURED DEFECT THIS CLOSES: `dimension_binding.api.what` is not a
        # prompt, so this function returned `source='default_phone'` — the run
        # would ask the 7B the PHONE's question ("is this an int?"), use the
        # PHONE's 22 values, and record the answer as evidence about a DIMENSION.
        # That is a FABRICATED proof, not a proof.
        #
        # THE ORACLE IS THE CONTRACT. `SKILL.5W1H` holds the six dimensions as
        # fields with `rule_kind='non_blank'` (set in W2), and
        # `oracle_from_contract` already wraps `validate_payload_against_contract`
        # as an `oracle_fn`. So the binding's oracle is the SAME declarative rule
        # the contract enforces — data, not a second implementation.
        #
        # THE FIELD IS THE DIMENSION. `field_name=parts[2]` validates ONE
        # dimension, so a run measures "is THIS dimension answered" rather than
        # the whole block.
        _b = _binding_parts(ref_tag)
        if _b:
            _kind, _dim = _b
            try:
                _row = conn.execute(
                    "SELECT 1 FROM dimension_binding_registry WHERE "
                    "subject_kind=? AND dimension_key=?",
                    (_kind, _dim)).fetchone()
            except sqlite3.OperationalError:
                _row = None
            if _row:
                return {"fn": _binding_oracle(conn, _dim),
                        "source": "contract", "oracle_ref": "SKILL.5W1H",
                        "field": _dim, "subject_kind": _kind,
                        "evidence_refusal": evidence_refusal,
                        "reason": ("ref_tag %r is a dimension binding, so it is "
                                   "judged by the SKILL.5W1H contract's %r field "
                                   "(rule_kind=non_blank) — NOT the phone oracle"
                                   % (ref_tag, _dim))}
            return {"fn": oracle, "source": "default_phone", "oracle_ref": "NA",
                    "evidence_refusal": evidence_refusal,
                    "reason": ("ref_tag %r is SHAPED like a dimension binding "
                               "but names no binding, so the phone oracle is "
                               "used — REPORTED, not silent" % ref_tag)}
        return {"fn": oracle, "source": "default_phone",
                "oracle_ref": "NA",
                "evidence_refusal": evidence_refusal,
                "reason": "ref_tag %r is not a prompt" % ref_tag}
    ref = str(p.get("oracle_ref") or "NA").strip()
    if ref in ("", "NA"):
        # ---- THE STUDY BRANCH (2026-09-27) --------------------------------
        # THE HUMAN: "you are helping LLM 7B to have proof run and helper for him
        # to have whole site proof run, this is our goal task".
        #
        # MEASURED DEFECT THIS CLOSES: 36 of the 39 active prompts carry
        # `oracle_ref='NA'` AND `study_id=2`, whose `fields_json` DECLARES
        # `[{"sort":1,"field":"target_name","value":"Chrome"}]`. The study SAYS
        # what to measure.
        #
        # `declaration_for_ref_tag` ALREADY finds it and returns
        # `{"kind": "study", "ref": "mouse_spot_case"}` -- and its own docstring
        # names this exact case: "A STUDY, via the prompt's `study_id` ... The
        # study SAYS what to measure; the phone question measures something else.
        # THIS is the source that fires on the actual defect."
        #
        # BUT `oracle_for` NEVER ASKED. There was no study branch, so all 36 fell
        # through to `default_phone` -- the run would ask the 7B the PHONE's
        # question and record the answer as evidence about the prompt. That is a
        # FABRICATED proof in the only evidence table, which is worse than no run.
        #
        # THE DETECTOR WAS BUILT. THE RESOLVER WAS NEVER WIRED TO IT.
        #
        # The study is tried BEFORE the phone fallback because a study is DATA
        # about THIS ref_tag, while the phone oracle is a literal about a
        # DIFFERENT one -- the same ordering rule the contract branch follows.
        _sid = p.get("study_id")
        if _sid is not None:
            try:
                _s = conn.execute(
                    "SELECT study_key FROM study_registry WHERE study_id=?",
                    (_sid,)).fetchone()
            except sqlite3.OperationalError:
                _s = None
            if _s:
                _skey = str(_s["study_key"])
                if study_fields(conn, _skey):
                    return {"fn": oracle_from_study(conn, _skey),
                            "source": "study", "oracle_ref": "NA",
                            "study_key": _skey,
                            "evidence_refusal": evidence_refusal,
                            "reason": ("prompt %r has no `oracle_ref`, but its "
                                       "study %r DECLARES fields, so the study "
                                       "oracle is used -- NOT the phone oracle"
                                       % (ref_tag, _skey))}
        return {"fn": oracle, "source": "default_phone", "oracle_ref": ref,
                "evidence_refusal": evidence_refusal,
                "reason": ("prompt %r has no `oracle_ref` and no study that "
                           "declares fields, so the phone oracle is used -- the "
                           "fallback is REPORTED, not silent" % ref_tag)}
    # A CONTRACT is the generic form: `oracle_ref` names a `contract_id`, and the
    # field is the prompt's own subject. This is the DECLARATIVE path, and it is
    # tried FIRST because a contract is data while a Python function is code.
    try:
        row = conn.execute("SELECT contract_id FROM skill_contract_template "
                           "WHERE contract_id=?", (ref,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        # DEFECT FOUND BY MEASURING IT (2026-09-22): this used to pass the PROMPT
        # KEY as the contract's field name, so `worker_identity` was looked up as
        # a contract field — which does not exist — and the oracle answered NO
        # for a COMPLETE identity block. A prompt whose subject IS the contract
        # (an identity BLOCK) validates the WHOLE PAYLOAD, so `field_name=None`.
        return {"fn": oracle_from_contract(conn, ref, None),
                "source": "contract", "oracle_ref": ref, "field": None}
    # A FIELD RULE. `field_tdd_rule` holds `rule_json`, so the rule is looked up
    # there. Kept for the phone case, which predates the contract path.
    try:
        row = conn.execute(
            "SELECT rule_json FROM field_tdd_rule WHERE slice_key=? "
            "ORDER BY id DESC LIMIT 1", (ref,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    if not row:
        return {"fn": oracle, "source": "default_phone", "oracle_ref": ref,
                "reason": ("oracle_ref %r resolves to no contract and no "
                           "field_tdd_rule, so the phone oracle is used" % ref)}
    return {"fn": oracle, "source": "field_rule", "oracle_ref": ref,
            "rule_json": str(row[0])}


def streak_target_for(conn: sqlite3.Connection, ref_tag: str,
                      *, option_count: int | None = None) -> dict[str, Any]:
    """The streak target for a `ref_tag`, DERIVED from the prompt's `threshold`.

    MEASURED: `STREAK_TARGET = 110` is hard-coded as `options(22) * 5`. That is
    the PHONE case, so every prompt would be judged by the phone's number. A
    prompt's target must come from ITS OWN threshold.

    Returns `{target, source, threshold}`. `source` is RETURNED, not assumed.

    A FLOW resolves through its FINAL step's prompt (2026-09-22), so a flow's
    target is the target of the question it ENDS on.
    """
    try:
        import prompt_registry as pr
        resolved = pr.resolve_ref_tag(conn, ref_tag)
    except Exception:
        resolved = {"kind": "unknown"}
    if resolved.get("kind") == "flow":
        fp = flow_final_prompt(conn, ref_tag)
        if not fp:
            return {"target": STREAK_TARGET, "source": "default_phone",
                    "threshold": "NA",
                    "reason": ("flow %r has no final step naming a prompt"
                               % ref_tag)}
        inner = streak_target_for(conn, fp["prompt_key"],
                                  option_count=option_count)
        inner["flow_key"] = str(ref_tag)
        inner["final_prompt"] = fp["prompt_key"]
        return inner
    try:
        import prompt_registry as pr
        p = pr.get_prompt(conn, ref_tag)
    except Exception:
        p = None
    if not p:
        # A DIMENSION BINDING. THE HUMAN (2026-09-26): "gogogo".
        #
        # MEASURED: without this, a binding fell back to the PHONE's target 110
        # (22 options x 5), so a run would be judged by the phone's number.
        #
        # THE TARGET IS DERIVED FROM THE CONTRACT'S OWN OPTION SET, the same way
        # a prompt's is: `option_count * threshold`. The threshold is the
        # contract's `non_blank` rule, which is a BINARY decision, so the
        # threshold is 5 — the same 5 consecutive wins every other target uses.
        # The option count comes from `options_for_ref_tag`, so the target and
        # the values cannot disagree.
        _b = _binding_parts(ref_tag)
        if _b:
            _kind, _dim = _b
            try:
                _row = conn.execute(
                    "SELECT 1 FROM dimension_binding_registry WHERE "
                    "subject_kind=? AND dimension_key=?",
                    (_kind, _dim)).fetchone()
            except sqlite3.OperationalError:
                _row = None
            if _row:
                _oc = (int(option_count) if option_count is not None
                       else len(options_for_ref_tag(conn, ref_tag)["options"]))
                return {"target": _oc * 5, "source": "binding_contract",
                        "threshold": "5", "option_count": _oc,
                        "subject_kind": _kind, "dimension_key": _dim,
                        "reason": ("a dimension binding: target = %d option(s) "
                                   "x 5 consecutive wins" % _oc)}
        return {"target": STREAK_TARGET, "source": "default_phone",
                "threshold": "NA",
                "reason": "ref_tag %r is not a prompt" % ref_tag}
    thr = str(p.get("threshold") or "NA").strip()
    try:
        n = int(float(thr))
    except (TypeError, ValueError):
        return {"target": STREAK_TARGET, "source": "default_phone",
                "threshold": thr,
                "reason": ("prompt %r has a non-numeric threshold %r, so the "
                           "phone target is used" % (ref_tag, thr))}
    if option_count is None:
        # DEFECT FOUND BY MEASURING IT (2026-09-22): this used to default to
        # `len(OPTIONS)` — the PHONE's 22 values — so a prompt with a 4-value
        # contract got target 4*22=88 instead of 4*4=16. The option count must
        # come from the SAME resolution the run uses.
        option_count = len(options_for_ref_tag(conn, ref_tag)["options"])
    return {"target": int(option_count) * n, "source": "prompt_threshold",
            "threshold": thr, "option_count": int(option_count)}


def gen_values(seed: int, n: int, *, options: list[str] | None = None
               ) -> list[str]:
    """Uniform random sampling with replacement over the option set.

    `options` defaults to the PHONE set, so existing behaviour is unchanged. A
    caller proving a DIFFERENT prompt passes its own set (see
    `options_for_contract`), because MEASURED: the phone's 22 values are all
    "is this an int?" tests, so every prompt would be fed them.
    """
    rng = random.Random(seed)
    pool = options if options else OPTIONS
    if not pool:
        raise ValueError("gen_values needs a non-empty option set; an empty set "
                         "would sample from nothing")
    return [rng.choice(pool) for _ in range(n)]


def options_for_ref_tag(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """The test VALUES for a `ref_tag`, DERIVED from its contract.

    Returns `{options, source, reason}`. `source` is RETURNED, not assumed: a
    silent fallback to the phone's values would make a proof run measure the
    phone while reporting the prompt.

    A FLOW resolves through its FINAL step's prompt (2026-09-22).
    """
    try:
        import prompt_registry as pr
        resolved = pr.resolve_ref_tag(conn, ref_tag)
    except Exception:
        resolved = {"kind": "unknown"}
    if resolved.get("kind") == "flow":
        fp = flow_final_prompt(conn, ref_tag)
        if not fp:
            return {"options": list(OPTIONS), "source": "default_phone",
                    "reason": ("flow %r has no final step naming a prompt"
                               % ref_tag)}
        inner = options_for_ref_tag(conn, fp["prompt_key"])
        inner["flow_key"] = str(ref_tag)
        inner["final_prompt"] = fp["prompt_key"]
        return inner
    # A DIMENSION BINDING. THE HUMAN (2026-09-26): "gogogo".
    #
    # MEASURED: without this, a binding was fed the PHONE's 22 values, every one
    # of which is an "is this an int?" test. The values must come from the
    # binding's OWN rule.
    #
    # THE RULE IS `non_blank`, A BINARY DECISION, so the option set is the two
    # cases that rule distinguishes: a NON-BLANK answer (which must pass) and a
    # BLANK one (which must fail). That is the same shape `options_for_contract`
    # builds for a `non_blank` field, and it is DERIVED from the rule rather than
    # invented.
    _b = _binding_parts(ref_tag)
    if _b:
        _kind, _dim = _b
        try:
            _row = conn.execute(
                "SELECT 1 FROM dimension_binding_registry WHERE "
                "subject_kind=? AND dimension_key=?",
                (_kind, _dim)).fetchone()
        except sqlite3.OperationalError:
            _row = None
        if _row:
            return {"options": ['"a real answer"', '"   "'],
                    "source": "binding_contract",
                    "subject_kind": _kind, "dimension_key": _dim,
                    "reason": ("a dimension binding judged by rule_kind="
                               "non_blank, so the two cases are a non-blank "
                               "answer and a blank one")}
    try:
        import prompt_registry as pr
        p = pr.get_prompt(conn, ref_tag)
    except Exception:
        p = None
    if not p:
        return {"options": list(OPTIONS), "source": "default_phone",
                "reason": "ref_tag %r is not a prompt" % ref_tag}
    ref = str(p.get("oracle_ref") or "NA").strip()
    if ref in ("", "NA"):
        # ---- THE STUDY BRANCH (2026-09-27) --------------------------------
        # THE SAME MISSING BRANCH `oracle_for` had. MEASURED: without it, all 36
        # study prompts fell back to the phone's 22 values, and `run_harness`
        # REFUSED them with `DECLARATION_EXISTS_BUT_FELL_BACK`
        # (`fell_back: ["options"]`). The refusal was CORRECT -- the run would
        # have measured the phone -- and it is why the whole-site run has never
        # happened. This is the resolution it was waiting for.
        _sid = p.get("study_id")
        if _sid is not None:
            try:
                _s = conn.execute(
                    "SELECT study_key FROM study_registry WHERE study_id=?",
                    (_sid,)).fetchone()
            except sqlite3.OperationalError:
                _s = None
            if _s:
                _skey = str(_s["study_key"])
                _opts = options_for_study(conn, _skey)
                if _opts:
                    return {"options": _opts, "source": "study",
                            "study_key": _skey,
                            "reason": ("prompt %r has no `oracle_ref`, but its "
                                       "study %r DECLARES fields, so the study's "
                                       "values are used -- NOT the phone's"
                                       % (ref_tag, _skey))}
        return {"options": list(OPTIONS), "source": "default_phone",
                "reason": ("prompt %r has no `oracle_ref` and no study that "
                           "declares fields, so the phone values are used -- the "
                           "fallback is REPORTED, not silent" % ref_tag)}
    field = None
    opts = options_for_contract(conn, ref, field)
    if not opts:
        return {"options": list(OPTIONS), "source": "default_phone",
                "reason": ("contract %r cannot say what to test, so the phone "
                           "values are used" % ref)}
    return {"options": opts, "source": "contract", "oracle_ref": ref,
            "field": field}


def _connect(db_path: Path | str | None = None):
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_table(db_path: Path | str | None = None) -> None:
    path = Path(db_path) if db_path else DB_PATH
    conn = _connect(path)
    try:
        conn.executescript(_CREATE_LLM_100_RUN_SQL)
        conn.commit()
    finally:
        conn.close()


def current_rule_version(conn: sqlite3.Connection, ref_tag: str) -> int:
    row = conn.execute(
        "SELECT MAX(rule_version) AS v FROM proof_run WHERE ref_tag = ?",
        (ref_tag,),
    ).fetchone()
    return int(row["v"]) if row and row["v"] is not None else 1


def next_round_no(conn: sqlite3.Connection, ref_tag: str, rule_version: int) -> int:
    # round_no is global per ref_tag, continuing across rule_versions.
    row = conn.execute(
        "SELECT MAX(round_no) AS r FROM proof_run WHERE ref_tag = ?",
        (ref_tag,),
    ).fetchone()
    return (int(row["r"]) + 1) if row and row["r"] is not None else 1


def current_streak(conn: sqlite3.Connection, ref_tag: str, rule_version: int, model: str) -> int:
    rows = conn.execute(
        """
        SELECT win FROM proof_run
        WHERE ref_tag = ? AND rule_version = ? AND model = ?
        ORDER BY id DESC
        """,
        (ref_tag, rule_version, model),
    ).fetchall()
    streak = 0
    for r in rows:
        if r["win"] == 1:
            streak += 1
        else:
            break
    return streak


def judge_instruction() -> str:
    return (
        'You are a YAML field-type judge. Given a raw YAML value, answer with exactly "YES" or "NO".\n'
        "Answer YES if and only if yaml.safe_load(value) has Python type int (type is int).\n"
        "YAML int forms (all YES): decimal 123, -5, +7; legacy leading-zero 0123; hex 0x1F;\n"
        "binary 0b101; underscores allowed 1_234; leading + allowed +85291234567.\n"
        'NOT int (all NO): bool true/false/True; floats 9.5 / 1e3 / .5; null / ~ / empty;\n'
        '0o17 (PyYAML parses as str); any quoted value ("123"); symbols "9123-4567";\n'
        'emoji; text with spaces "+852 9123 4567".\n'
        "Reply with one word only."
    )


def parse_verdict(text: str) -> str | None:
    """The DEFAULT parse: the reply contains YES or NO."""
    t = (text or "").strip().upper()
    if "YES" in t:
        return "YES"
    if "NO" in t:
        return "NO"
    return None


def parse_none_means_yes(text: str) -> str | None:
    """The ENUMERATION parse: the reply names the ABSENT keys, or says NONE.

    MEASURED (2026-09-22): the 7B is heavily biased to YES on a verdict question
    — it answered YES for a payload MISSING a required field 19 times out of 40,
    and NO instruction phrasing fixed it (variants A/B/D all scored recall NO
    0.00). Asking it to NAME the absent keys instead scored 0.83..1.00 over 4
    repeated runs, because naming a missing key is a different task from
    emitting a verdict. So the YES-shaped answer here is the word NONE.

    MEASURED (2026-09-26) — THIS PARSER IS NOW SUPERSEDED for the contract case.
    On a BALANCED gold set (18 cases, minority class 44%) the instruction that
    pairs with this parser scored balanced **0.85**, recall NO **0.70**: it fails
    EXACTLY the single-missing case (3 of 4 wrong), because `required - present`
    is a SET DIFFERENCE and `NONE` is the safe answer. Kept because the phone's
    `judge_instruction()` still uses it.
    """
    t = (text or "").strip().upper()
    if "NONE" in t:
        return "YES"
    if not t:
        return None
    return "NO"


def parse_key_checklist(text: str) -> str | None:
    """The PER-KEY parse: the reply is one `<KEY>=PRESENT|ABSENT` line per key.

    MEASURED (2026-09-26): this pairs with the per-key checklist instruction, and
    together they scored balanced **1.00** on the balanced gold set, CERTIFIED
    over 5 repeated runs (`[1.0, 1.0, 1.0, 1.0, 1.0]`), and 4 of 4 on the
    single-missing cases where the ABSENT-shaped instruction failed 3 of 4.

    WHY A PER-KEY TASK WORKS: it turns ONE set operation into N independent
    one-step checks. A 7B does not compute `required - present` reliably, but it
    answers "is THIS key present?" reliably.

    A MISSING LINE IS NOT A PASS. If the reply does not state a key at all, the
    answer is UNKNOWN (None), never YES — an unstated key is not a present one.

    MEASURED DEFECT IN MY OWN FIRST VERSION (2026-09-26): this function took only
    `text`, so it could not tell a COMPLETE reply from a TRUNCATED one — a reply
    stating 2 of 4 keys returned YES. The docstring said "a missing line is not a
    pass" while the code did the opposite. `make_key_checklist_parser(required)`
    is the fix: it knows how many keys were asked for.
    """
    t = (text or "").upper().replace(" ", "")
    if not t.strip():
        return None
    absent = "=ABSENT" in t
    present = "=PRESENT" in t
    if not absent and not present:
        return None
    return "NO" if absent else "YES"


def make_key_checklist_parser(required: list[str]):
    """A parser that REQUIRES every required key to be stated.

    MEASURED (2026-09-26): the generic `parse_key_checklist` cannot tell a
    COMPLETE reply from a TRUNCATED one, so a reply stating 2 of 4 keys returned
    YES. This closes that: a key the reply never mentions is UNKNOWN, not a pass.
    """
    keys = [str(k).upper() for k in (required or [])]

    def _parse(text: str) -> str | None:
        t = (text or "").upper().replace(" ", "")
        if not t.strip():
            return None
        stated = [k for k in keys if ("%s=PRESENT" % k) in t or ("%s=ABSENT" % k) in t]
        if keys and len(stated) < len(keys):
            # A key the reply never mentions is NOT a present one.
            return None
        if not keys:
            return parse_key_checklist(text)
        return "NO" if any(("%s=ABSENT" % k) in t for k in keys) else "YES"

    _parse.__name__ = "parse_key_checklist_for_required"
    return _parse


def judge_instruction_for(conn: sqlite3.Connection, ref_tag: str) -> dict[str, Any]:
    """The judge instruction for a `ref_tag`, DERIVED from its contract.

    MEASURED: `judge_instruction()` is the PHONE's question ("is this an int?"),
    so a proof run on `worker_identity` would ask the 7B the WRONG QUESTION and
    record the answer as evidence about identity. The instruction must come from
    the SAME contract the oracle uses.

    Returns `{instruction, source, oracle_ref, parse}`. `source` is RETURNED, not
    assumed, so a fallback to the phone question is visible. `parse` is the
    matching reply parser — the instruction and its parser are ONE decision, so
    they are returned together rather than kept in two places that can drift.
    """
    o = oracle_for(conn, ref_tag)
    ref = str(o.get("oracle_ref") or "NA")
    # A DIMENSION BINDING ASKS ABOUT ONE DIMENSION, NOT SIX KEYS.
    #
    # MEASURED DEFECT (2026-09-26), and it made B2 report `NOT_QUALIFIED` for a
    # reason that had nothing to do with the dimension:
    #
    #   the instruction asked "For EACH required key, output <KEY>=PRESENT or
    #   <KEY>=ABSENT. Required keys: what, why, who, when, where, how"
    #   the oracle asked "is THIS dimension non-blank?"
    #
    # The two are DIFFERENT QUESTIONS. MEASURED: the 7B answered `NO` on 1000 of
    # 1000 rounds — including the 485 rounds whose value was a real answer — so
    # the run measured the INSTRUCTION's shape, not the dimension. The oracle was
    # right (515 wins on the blank value, 0 on the real one); the QUESTION was
    # wrong.
    #
    # THE FIX: ask the ONE question the oracle decides. The value under test is
    # the dimension's answer, so the question is "is this answer non-blank?" —
    # the same rule `rule_kind='non_blank'` enforces. The parser is the matching
    # YES/NO reader, so the instruction and its parser stay ONE decision.
    if o.get("source") == "contract" and o.get("field") and _binding_parts(ref_tag):
        _dim = str(o.get("field"))
        return {"instruction": (
            "You are given ONE answer for the %r dimension of a plan.\n"
            "Decide whether the answer is NON-BLANK: it must contain at least "
            "one non-whitespace character.\n"
            "An answer that is empty or only spaces is BLANK.\n"
            "Reply with exactly one word: YES if it is non-blank, NO if it is "
            "blank." % _dim),
            "source": "binding_contract", "oracle_ref": ref,
            "field": _dim, "instruction_variant": "binding_non_blank",
            "parse": parse_verdict}
    # THE DECLARED RULE GOVERNS THE QUESTION TOO.
    # MEASURED: `judge_instruction()` is a hand-written literal that lists eleven
    # example forms and NEVER states that an integer's magnitude has no upper
    # bound. `999999999999999` therefore failed 79.3% of rounds while every value
    # whose expectation WAS stated passed 100%. When a declaration exists, the
    # instruction is DERIVED from it — so the prompt cannot omit the very bound
    # being measured, and a NEW declaration needs no prompt edit here.
    if o.get("source") == "field_rule":
        try:
            import field_rule_declare as _frd
            d = _frd.declared_instruction(conn, ref_tag)
        except Exception as exc:
            d = {"ok": False, "message": "%s: %s" % (type(exc).__name__, exc)}
        if d.get("ok"):
            return {"instruction": d["instruction"], "source": "field_rule",
                    "oracle_ref": ref, "parse": parse_verdict,
                    "rule_ref": d["source"], "declared_check": d["check"]}
    # ---- THE STUDY ASKS THE STUDY'S QUESTION (2026-09-27) -----------------
    # THE SAME RULE the binding branch above follows: "the instruction and its
    # parser are ONE decision". MEASURED DEFECT THIS CLOSES: the 36 study prompts
    # resolved to `source='study'` for the ORACLE but would still have been asked
    # the PHONE's question ("is this an int?"), so the run would measure the
    # INSTRUCTION's shape rather than the study's declared field -- the exact
    # defect the binding branch was written for, one branch over.
    #
    # The question is DERIVED from the study's declared fields, so a new study
    # needs no prompt edit here.
    if o.get("source") == "study":
        _skey = str(o.get("study_key") or "")
        _fields = study_fields(conn, _skey)
        if _fields:
            _lines = []
            for f in _fields:
                if "value" in f:
                    _lines.append("  %s = %s" % (f["field"], f["value"]))
                else:
                    _lines.append("  %s = (any non-blank answer)" % f["field"])
            return {"instruction": (
                "You are given ONE answer for the study %r.\n"
                "The study DECLARES these fields:\n%s\n"
                "Decide whether the answer MATCHES the declared value(s).\n"
                "Reply with exactly one word: YES if it matches, NO if it does "
                "not." % (_skey, "\n".join(_lines))),
                "source": "study", "oracle_ref": ref, "study_key": _skey,
                "instruction_variant": "study_declared",
                "parse": parse_verdict}
    if o.get("source") != "contract":
        return {"instruction": judge_instruction(), "source": "default_phone",
                "oracle_ref": ref, "parse": parse_verdict}
    fields = contract_fields(conn, ref)
    if not fields:
        return {"instruction": judge_instruction(), "source": "default_phone",
                "oracle_ref": ref, "parse": parse_verdict}
    req = [f["field_name"] for f in fields if int(f.get("mandatory") or 0)]
    opt = [f["field_name"] for f in fields if not int(f.get("mandatory") or 0)]
    # ---- THE QUESTION SHAPE, MEASURED (2026-09-26) ------------------------
    #
    # THE HUMAN: "how to help 7B zoom focus to the point / is question design and
    # flow problem". MEASURED: YES, and the defect is the QUESTION, not the 7B.
    #
    # The 7B's RAW reply is right — it answers `NONE` or names keys. But the
    # ABSENT-shaped instruction fails EXACTLY the single-missing case:
    #
    #   case                        want   7B raw        verdict
    #   all 4 present               YES    NONE          ok
    #   only SESSION_ID missing     NO     NONE          WRONG
    #   only MODEL missing          NO     NONE          WRONG
    #   only CHAT_ID missing        NO     CHAT_ID       ok
    #   only CHAT_SHA256 missing    NO     NONE          WRONG
    #   2+ missing (10 cases)       NO     names them    ok
    #
    # WHY: `required - present` is a SET DIFFERENCE. A 7B does not compute it
    # reliably, and `NONE` is the SAFE answer — so on a near-miss it collapses to
    # `NONE`. The question's own escape hatch is what the model reaches for.
    #
    # NOT A POSITION EFFECT: reversing the required list made it WORSE (0 of 4),
    # so it is not "the model only reads the last two".
    #
    # THE REPO'S OWN TABLE WAS STALE. The old docstring recorded F2 as
    # `1.00 / 1.00`, measured on a gold set whose minority class was 1 of 16
    # (6%) — which `prompt-measurement-discipline` Rule 1 forbids. MEASURED NOW
    # on a BALANCED gold set (18 cases, minority 44%):
    #
    #   variant                     accuracy  BALANCED  recallYES  recallNO
    #   A report ABSENT (old)          0.83      0.85       1.00      0.70
    #   D report PRESENT               1.00      1.00       1.00      1.00
    #   E per-key checklist            1.00      1.00       1.00      1.00
    #
    # Rule 4 repeated certification (5 runs each):
    #   D [1.0, 1.0, 1.0, 1.0, 1.0]  CERTIFIED
    #   E [1.0, 1.0, 1.0, 1.0, 1.0]  CERTIFIED
    #
    # E IS CHOSEN over D because it is N independent ONE-STEP checks rather than
    # one set operation, and it NAMES the absent key explicitly, so a refusal is
    # readable. THE GENERALISABLE RULE: a question whose answer can be "nothing"
    # invites the model to answer "nothing". Ask for what IS there, or ask N
    # one-step questions.
    lines = [
        "For EACH required key, output one line: <KEY>=PRESENT or <KEY>=ABSENT.",
        "Required keys: %s" % ", ".join(req),
        "Output exactly %d lines, in that order. No other text."
        % len(req),
    ]
    if opt:
        lines.append("The key(s) %s are OPTIONAL and must be ignored."
                     % ", ".join(opt))
    return {"instruction": "\n".join(lines), "source": "contract",
            "oracle_ref": ref, "required": req, "optional": opt,
            "instruction_variant": "per_key_checklist",
            "parse": make_key_checklist_parser(req)}


def tdd_question(raw: str) -> str:
    """Simple TDD-verify question: 1/2/3 numeric answer (7B can 100% handle)."""
    return (
        f'Is the value "{raw}" an integer (int)?\n'
        "Reply with exactly one number:\n"
        "1 = YES (it is an int)\n"
        "2 = NO (it is not an int)\n"
        "3 = UNSURE"
    )


def ask_flow(conn: sqlite3.Connection, ref_tag: str, raw: str, *,
             model: str | None = None, flow_key: str | None = None,
             branch: bool = True) -> dict[str, Any]:
    """Ask a question as a FLOW read from `workflow_step` (SCOPE AC/AE).

    THE HUMAN: *"the defect is that `judge_instruction()` is a one-shot question
    and does not use the flow"* and *"question is according to the answer by
    factor!!! not waste token"*.

    MEASURED, on a question evidence CANNOT answer (22 options, 3 reps):
        1-step (asked directly) : 20/22   P(100 in a row) = 0.0073%
        3-step (gate+describe)  : 22/22   P(100 in a row) = 100%
    and with the gate moved to EVIDENCE (a regex, 0 model calls):
        1 model call            : 22/22   43 ms/option vs 137 ms (69% faster)

    THE STEPS ARE READ, NOT HARDCODED. The flow's `question_template` rows are
    the questions; the `verdict_rule` on the verdict step is the computation.
    A flow that does not exist returns `{ok: False, code: 'FLOW_ABSENT'}` so the
    caller can fall back to `judge_instruction()` -- and the fallback is
    REPORTED, never silent.

    THE GATE IS ANSWERED BY EVIDENCE. A `gate` step whose `question_template`
    begins with `EVIDENCE` is answered by the regex in that text, NOT by a model
    call. MEASURED reason: the model's own gate is wrong in BOTH directions
    (gate=NO for `+85291234567`, gate=YES for `"9123-4567"`).
    """
    import question_flow as qf
    import re as _re

    key = flow_key or ("question_flow." + str(ref_tag))
    steps = qf.steps_of(conn, key)
    if not steps:
        return {"ok": False, "code": "FLOW_ABSENT", "flow_key": key,
                "verdict": None, "source": "flow_absent"}

    mdl = model or resolve_model(conn)
    calls = {"n": 0}

    def _ask(prompt: str, step: dict[str, Any]) -> str:
        text = str(prompt or "")
        # ---- THE GATE IS EVIDENCE, NOT A MODEL CALL ------------------------
        m = _re.search(r"EVIDENCE \(regex (.+?)\)", text)
        if m:
            try:
                rx = _re.compile(m.group(1))
            except _re.error:
                return "UNKNOWN"
            return "YES" if rx.match(str(raw)) else "NO"
        calls["n"] += 1
        return _llm_judge(str(raw), model=mdl, system=text)[0]

    # ---- THE VERDICT RULE IS DECLARED, NOT A BRANCH ------------------------
    rule, params = "all_judged_match", {}
    for s in steps:
        if str(s.get("step_kind")) == "verdict":
            rule = str(s.get("verdict_rule") or rule)
            raw_params = s.get("verdict_params")
            if raw_params:
                try:
                    params = json.loads(str(raw_params))
                except (ValueError, TypeError):
                    params = {}
    compute = qf.compute_declared_verdict(rule, params)

    out = qf.run_flow(conn, key, _ask, compute_verdict=compute, branch=branch)
    out["source"] = "flow"
    out["flow_key"] = key
    out["model_calls"] = calls["n"]
    out["verdict_rule"] = rule
    out["verdict_params"] = params
    return out


def parse_tdd_answer(text: str) -> str | None:
    """Parse 1/2/3 numeric answer -> YES/NO/UNSURE. Returns None if unparseable."""
    t = (text or "").strip()
    if t == "1":
        return "YES"
    if t == "2":
        return "NO"
    if t == "3":
        return "UNSURE"
    # tolerate "1." or "1)" or leading digit in a short reply
    if t[:1] == "1":
        return "YES"
    if t[:1] == "2":
        return "NO"
    if t[:1] == "3":
        return "UNSURE"
    return None


def _load_env() -> None:
    """Load .env into os.environ (without printing secrets)."""
    import os
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_last_laya_route: dict[str, Any] | None = None


def _llm_judge(raw: str, *, model: str, system: str,
               parse: Callable[[str], str | None] | None = None
               ) -> tuple[str, str | None]:
    """Call local LLM via vision_analyze.complete_text. Returns (answer, error).

    `parse` is the reply parser that MATCHES `system`. MEASURED (2026-09-22): the
    default parse looks for YES/NO, but the contract instruction asks for the
    ABSENT KEYS or the word NONE, so the default parse would read every reply as
    NO. The instruction and its parser are ONE decision, so they travel together.

    🔴 IT NOW TRIES `laya_router` FIRST, AND ONLY WHEN IT CAN REACH IT
    -----------------------------------------------------------------
    MEASURED 2026-09-29: `laya_router` had **ZERO automatic callers**. The only
    thing in the repo asking for `transport='python'` (the transport laya is
    registered under) was a PROOF, so a model at priority 5 with a green proof
    could never be reached by a production path. This is the caller.

    THE ORDER IS THE LADDER, NOT A REPLACEMENT:
      * `ACCEPT` at the top rung -> return its answer and SAY SO
      * `UNKNOWN` / `ESCALATE`   -> fall through to the 7B, exactly as before
      * `ENGINE_UNAVAILABLE`     -> fall through, because "no engine" must not
                                    look like "the engine was unsure"

    WHY IT DOES NOT SWALLOW ERRORS: a router that silently substituted itself for
    the model would make every measurement a measurement of the router. The
    outcome is RECORDED in `_last_laya_route` so the caller can read it, and any
    exception is caught so a missing engine degrades to the previous behaviour
    instead of taking the whole run down.
    """
    global _last_laya_route
    _last_laya_route = None
    if (parse or parse_verdict) is parse_verdict:
        try:
            import laya_router as _lr
            if _lr.engine_available():
                _conn = _connect()
                try:
                    question = ("Does the answer to the task satisfy the rule "
                                "below? Rule: %s" % (system or "").strip())
                    d = _lr.decide_yes_no(_conn, question, str(raw)[:4000])
                finally:
                    _conn.close()
                _last_laya_route = d
                if (d.get("outcome") == _lr.ACCEPT
                        and d.get("answer") in ("YES", "NO")):
                    return d["answer"], None
                # UNKNOWN / ESCALATE / ENGINE_UNAVAILABLE -> the 7B decides, and
                # the reason travels in the record above.
        except Exception as exc:
            _last_laya_route = {"outcome": "ROUTER_ERROR",
                                "reason": "%s: %s" % (type(exc).__name__, exc)}

    from vision_analyze import complete_text

    result = complete_text(prompt=raw, model=model, system=system, timeout=180.0)
    if result.error:
        return "", result.error
    fn = parse or parse_verdict
    got = fn(result.raw_text or "")
    if got is None:
        return "", f"unparseable reply: {result.raw_text!r}"
    return got, None


def _llm_judge_deepseek(raw: str, *, model: str, system: str) -> tuple[str, str | None]:
    """Call DeepSeek via OpenAI-compatible /v1/chat/completions. Returns (answer, error)."""
    import json as _json
    import os
    import urllib.request

    _load_env()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if not api_key:
        return "", "DEEPSEEK_API_KEY not set in .env"
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": raw},
        ],
        "temperature": 0.0,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=_json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180.0) as resp:
            body = _json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        return "", f"deepseek_call_error: {type(e).__name__}: {e}"
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return "", f"deepseek_bad_response: {str(body)[:200]}"
    text = (text or "").strip().upper()
    if "YES" in text:
        return "YES", None
    if "NO" in text:
        return "NO", None
    return "", f"unparseable reply: {text!r}"


def run_harness(
    *,
    seed: int,
    ref_tag: str = "1.1F",
    entity_name: str = "phone",
    entity_type: str = "field",
    model: str | None = None,
    cap: int = CAP_ROUNDS,
    db_path: Path | str | None = None,
    llm: bool = True,
    linked_trace_id: str | None = None,
    tdd_verify: bool = False,
    instruction: str | None = None,
    composition_key: str | None = None,
    oracle_fn: Callable[[str], str] | None = None,
    streak_target: int | None = None,
    dim_key: str = "NA",
    options: list[str] | None = None,
    write: bool = True,
    layer_key: str = "NA",
    ignore_streak: bool = False,
    value_type: str | None = None,
) -> dict[str, Any]:
    """Run rounds for one model until streak==110 (QUALIFIED) or cap (NOT_QUALIFIED).

    `model=None` (the default) resolves the model from the DB-driven service
    registry for the `llm.text` route. DEFECT FOUND BY MEASURING IT
    (2026-09-21): the default used to be the `DEFAULT_MODEL` literal, so the
    provider was a Python constant rather than a registry row. Passing an
    explicit `model` still overrides, which is what a comparison run needs.

    tdd_verify=True: use the SIMPLE 1/2/3 numeric question (7B can 100% handle,
    no DeepSeek needed). This is for TDD harness verification only, not proof.

    `instruction` / `composition_key` (added 2026-09-20): the judge instruction
    used to be ONLY the hard-coded `judge_instruction()` literal, so the prompt
    generator center could not reach this harness at all — the 100 proof ran on
    a string nobody could vary or measure. Now:
      * `instruction` overrides the default (a composed prompt from
        `skill_prompt_step.compose_case_prompt`).
      * `composition_key` is RECORDED on every round, so a 100-run states WHICH
        composition it measured instead of leaving it unrecorded.
    `judge_instruction()` remains the default, so existing behaviour is unchanged.

    `write=False` (added 2026-09-22): compute the rounds but DO NOT insert into
    `proof_run`. DEFECT FOUND BY MEASURING IT: a DRY run (`llm=False`) sets
    `llm_answer = oracle_answer`, so `win=1` ALWAYS — and it still INSERTED. That
    wrote 20 rows into the ONLY evidence table that look like a 20-streak proof
    of `worker_identity` but were never judged by any model. A run that did not
    ask a model must not leave evidence that it did.
    """
    path = Path(db_path) if db_path else DB_PATH
    init_table(path)
    migrate_llm_100_run_model(path, backfill_model=DEFAULT_MODEL)
    migrate_llm_100_run_dim_key(path)
    migrate_proof_run_layer_key(path)
    # THE VALUE FORMAT COLUMNS (added 2026-09-26). MEASURED: `proof_run` had no
    # `value_type`, so the format was re-derived by parsing on every read, and the
    # ROUTE was hardcoded (`JUDGE_SERVICE_KEY = "llm.text"`). A run whose value is
    # an image would still be judged by `qwen2.5:7b-instruct`, which cannot see
    # it. The human: "value format is the key for 7B-intract or 7B vl".
    migrate_proof_run_value_type(path)
    conn = _connect(path)
    try:
        # THE ROUTE, SELECTED BY THE VALUE FORMAT (added 2026-09-26).
        #
        # MEASURED DEFECT: this was `resolve_model(conn)` with the route defaulting
        # to `JUDGE_SERVICE_KEY = "llm.text"`, so a run whose value is an IMAGE was
        # still judged by `qwen2.5:7b-instruct`, which cannot see it. The human:
        # "value format is the key for 7B-intract or 7B vl".
        #
        # `value_type` now selects the route: `image`/`coordinate` -> `llm.vision`
        # -> `qwen2.5vl:7b`; everything else -> `llm.text` -> `qwen2.5:7b-instruct`.
        # An explicit `model` still overrides, which is what a comparison run needs.
        import value_type as _vt
        # THE MEASURED-UNIT ROW, RESOLVED FIRST (GAP 1 of FULL.CYCLE.NO.GAP).
        #
        # `proof_run_registry` declares, per (db_table, db_field), what FORMAT a
        # value has. MEASURED: 0 of 10,722 `proof_run` rows carried a
        # `proof_run_registry_id`, so the declaration existed and nothing pointed
        # at it.
        #
        # IT IS RESOLVED BEFORE `value_type` IS DECIDED, because the register IS
        # the declaration of the format. MEASURED (my first version was WRONG):
        # I resolved it AFTER, so `value_type` defaulted to `NA` and the register
        # was written as a pointer to a declaration the run had ignored. A
        # declaration that is recorded but not READ is decoration.
        #
        # It is RESOLVED, never invented. The ref_tag names the subject; the
        # subject's own register row gives the (table, field) pair. When no row
        # matches, it is **0** -- the schema's OWN "no declaration" value
        # (`proof_run_registry_id INTEGER NOT NULL DEFAULT 0`, MEASURED). A
        # fabricated id would point at a declaration about a DIFFERENT column,
        # which is worse than 0 because it looks like evidence.
        proof_run_registry_id = 0
        proof_run_registry_why = "not resolved"
        declared_value_type = None
        try:
            import prompt_registry as _pr
            _res = _pr.resolve_ref_tag(conn, ref_tag)
            if str(_res.get("kind") or "") == "prompt":
                # THE DECLARATION IS THE FIELD, NOT THE FIELD'S VALUE.
                #
                # MEASURED (my first version was WRONG): I looked up
                # `db_field_registry.field_key = <the unit's TEXT>`, e.g.
                # `'pct of cases where the worker is identified AND ...'`. That is
                # a PROSE VALUE, not a field name, so it matched nothing and the
                # resolution silently returned None for every prompt.
                #
                # The declaration that governs a prompt's measured value is the
                # row for `prompt_registry.unit` itself -- MEASURED, that is
                # `proof_run_registry.id=798` (`prompt_registry.unit -> string`).
                # The unit's TEXT says what is measured; the FIELD's row says what
                # FORMAT it has. Those are two different questions.
                _row = conn.execute(
                    "SELECT r.id, r.value_type FROM proof_run_registry r "
                    "JOIN db_table_registry t ON t.db_table_id = r.db_table_id "
                    "JOIN db_field_registry f ON f.db_field_id = r.db_field_id "
                    "WHERE t.table_key = 'prompt_registry' AND f.field_key = 'unit' "
                    "AND r.is_active = 1 ORDER BY r.id LIMIT 1").fetchone()
                if _row:
                    proof_run_registry_id = int(_row["id"])
                    declared_value_type = str(_row["value_type"] or "").strip() or None
                    proof_run_registry_why = (
                        "resolved to the declaration for prompt_registry.unit "
                        "(id=%d, value_type=%r)"
                        % (proof_run_registry_id, declared_value_type))
                else:
                    proof_run_registry_why = (
                        "no proof_run_registry row declares prompt_registry.unit")
            else:
                proof_run_registry_why = (
                    "ref_tag %r is not a prompt, so no prompt_registry.unit "
                    "declaration applies" % ref_tag)
        except Exception as _e:
            proof_run_registry_why = ("resolution failed: %s: %s"
                                      % (type(_e).__name__, _e))

        if value_type:
            vt = str(value_type).strip()
            route_key = _vt.route_for(vt)
            proof_method = _vt.proof_method_for(vt)
            vt_source = "explicit"
        elif declared_value_type:
            # THE REGISTER DECLARES THE FORMAT, so the run READS it. This is the
            # link GAP 1 was missing: the declaration existed and nothing used it.
            vt = declared_value_type
            route_key = _vt.route_for(vt)
            proof_method = _vt.proof_method_for(vt)
            vt_source = "proof_run_registry:%d" % proof_run_registry_id
        else:
            vt = "NA"
            route_key = JUDGE_SERVICE_KEY
            proof_method = "NA"
            vt_source = "default"
        route = resolve_model(conn, llm_route=route_key, db_path=path)
        if model is None:
            model = str(route["model"])
        else:
            route = {"model": str(model), "llm_route": route_key,
                     "source": "explicit", "pool": route.get("pool", [])}
        rule_version = current_rule_version(conn, ref_tag)
        round_no = next_round_no(conn, ref_tag, rule_version)
        # THE JUDGE QUESTION, resolved per ref_tag (2026-09-22). MEASURED:
        # `judge_instruction()` is the PHONE's question, so a proof run on
        # `worker_identity` would ask the 7B the WRONG QUESTION and record the
        # answer as evidence about identity. An explicit `instruction` still
        # overrides, which is what a comparison run needs.
        if instruction:
            system = instruction
            instruction_source = "explicit"
            reply_parse = parse_verdict
        else:
            ji = judge_instruction_for(conn, ref_tag)
            system = ji["instruction"]
            instruction_source = ji["source"]
            reply_parse = ji.get("parse") or parse_verdict

        # THE ORACLE, THE TARGET AND THE VALUES, resolved per ref_tag
        # (2026-09-22). All three are RETURNED with their source, so a fallback
        # is visible rather than silent. A hard-coded oracle + a hard-coded 110 +
        # the phone's 22 values means the harness can prove exactly ONE prompt.
        if oracle_fn is None:
            o = oracle_for(conn, ref_tag)
            oracle_fn = o["fn"]
            oracle_source = o["source"]
        else:
            oracle_source = "explicit"
        # OPTIONS FIRST, then the target — the target is `option_count *
        # threshold`, so resolving it before the options would use the PHONE's
        # count. DEFECT FOUND BY MEASURING IT (2026-09-22): the target was 88
        # (4*22) instead of 20 (4*5) for `worker_identity`.
        if options is None:
            v = options_for_ref_tag(conn, ref_tag)
            options = v["options"]
            options_source = v["source"]
        else:
            options_source = "explicit"
        if streak_target is None:
            t = streak_target_for(conn, ref_tag, option_count=len(options))
            streak_target = int(t["target"])
            target_source = t["source"]
        else:
            target_source = "explicit"

        # ---- THE CONTRACT-EXISTS-BUT-FELL-BACK GATE (added 2026-09-26) ------
        #
        # MEASURED DEFECT: `oracle_for`'s own docstring says "a silent fallback
        # would make a 100-run report an oracle it never resolved" — and it
        # RETURNS `source='default_phone'`. But NOTHING READ that field to
        # refuse. `run_harness` stored `instruction_source` / `oracle_source` /
        # `options_source` in the result dict and never checked them.
        #
        # MEASURED: 38 of 39 prompts resolve to `default_phone`. The reachable
        # ones are the FINAL steps of active flows — `worker_identity_confirm`
        # (the `worker_identity` flow) and `tutorial`. A run of either would ask
        # the 7B the PHONE's question ("is this an int?"), use the PHONE's 22
        # values, and record the answer as evidence about IDENTITY.
        #
        # This is the SAME "advisory gate" defect this session already fixed
        # twice (`activation_gate`, `assert_question_is_answerable`). A source
        # that is REPORTED but never REFUSED is a source that does not exist.
        #
        # THE GATE REFUSES THE CONTRADICTION, NOT THE FALLBACK. A ref_tag with
        # NO contract (the phone's own `1.1F`) still runs — the fallback is the
        # ONLY resolution there. Only a ref_tag that HAS a contract and did not
        # use it is refused.
        #
        # An EXPLICIT source is never refused: the caller stated what to measure.
        if not (instruction and oracle_fn is not None and options is not None):
            decl = declaration_for_ref_tag(conn, ref_tag)
            if decl:
                fell_back = [name for name, src in (
                    ("instruction", instruction_source),
                    ("oracle", oracle_source),
                    ("options", options_source),
                ) if src == "default_phone"]
                if fell_back:
                    return {
                        "verdict": "REFUSED",
                        "seed": seed,
                        "model": model,
                        "ref_tag": ref_tag,
                        "rounds_run": 0,
                        "wrote_evidence": False,
                        "instruction_source": instruction_source,
                        "oracle_source": oracle_source,
                        "options_source": options_source,
                        "refusal": {
                            "code": "DECLARATION_EXISTS_BUT_FELL_BACK",
                            "declaration_kind": decl["kind"],
                            "declaration_ref": decl["ref"],
                            "fell_back": fell_back,
                            "why": ("ref_tag %r HAS a %s declaration (%r), so "
                                    "the phone question is the WRONG question — "
                                    "the run would measure the PHONE and record "
                                    "it as evidence about %r"
                                    % (ref_tag, decl["kind"], decl["ref"],
                                       ref_tag)),
                            "fix": ("set `prompt_registry.oracle_ref` for this "
                                    "prompt_key to the contract that declares "
                                    "these fields (and `threshold` to the "
                                    "contract's required-field count)"),
                        },
                        "failures": [],
                        "rounds": [],
                    }

        values = gen_values(seed, cap, options=options)

        rounds: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        streak = current_streak(conn, ref_tag, rule_version, model)
        max_streak = streak
        verdict = "NOT_QUALIFIED"

        for i in range(cap):
            # `ignore_streak` (added 2026-09-26). MEASURED DEFECT: the held-out
            # verify ran **0 rounds** because the streak was ALREADY 40 >= target
            # 20, so this loop broke on its first iteration and `tdd_verify`
            # measured nothing — a stage that never ran, which `check_stages`
            # correctly refuses. A verify must MEASURE, so it must not be
            # short-circuited by the very streak it is verifying.
            if not ignore_streak and streak >= streak_target:
                verdict = "QUALIFIED"
                break
            raw = values[i]
            oracle_answer = oracle_fn(raw)
            if llm:
                if tdd_verify:
                    # Simple 1/2/3 question via local 7B (free, no DeepSeek).
                    from vision_analyze import complete_text
                    result = complete_text(prompt=tdd_question(raw), model=model, system=None, timeout=180.0)
                    if result.error:
                        llm_answer, err = "", result.error
                    else:
                        parsed = parse_tdd_answer(result.raw_text)
                        if parsed is None:
                            llm_answer, err = "", f"unparseable reply: {result.raw_text!r}"
                        else:
                            llm_answer, err = parsed, None
                elif model.startswith("deepseek"):
                    llm_answer, err = _llm_judge_deepseek(raw, model=model, system=system)
                else:
                    llm_answer, err = _llm_judge(raw, model=model, system=system,
                                                 parse=reply_parse)
            else:
                llm_answer = oracle_answer
                err = None
            if err:
                win = 0
                failure_reason = f"llm_call_error: {err}"
            else:
                win = 1 if llm_answer == oracle_answer else 0
                failure_reason = (
                    f"false {'YES' if llm_answer == 'YES' else 'NO'} (oracle={oracle_answer}) value={raw!r}"
                    if not win
                    else None
                )
            if write:
                # THE MEASURED UNIT IS WRITTEN, NOT ONLY COMPUTED.
                #
                # MEASURED DEFECT (2026-09-27, GAP 1 of FULL.CYCLE.NO.GAP): this
                # INSERT named 15 columns while `proof_run` has 21. `value_type`
                # was COMPUTED at `:1565-1575` and RETURNED at `:1975` -- and then
                # THROWN AWAY, because the INSERT omitted it. MEASURED: 0 of
                # 10,722 rows carried a `value_type`, and 0 carried a
                # `proof_run_registry_id`.
                #
                # The human's rule -- "value format is the key for 7B-intract or
                # 7B vl" -- was satisfied in the RETURN value and violated in the
                # RECORD. A value format that is not recorded cannot be audited,
                # and the route it selected cannot be re-derived.
                #
                # `proof_run_registry_id` is RESOLVED from the register, never
                # invented: it is the row that declares THIS value's format for
                # THIS (table, field). When no row matches, it stays NULL and the
                # gap is REPORTED in the result -- a fabricated id would point at
                # a declaration about a different column.
                conn.execute(
                    """
                    INSERT INTO proof_run
                        (entity_type, ref_tag, entity_name, round_no, value,
                         oracle_answer, llm_answer, win, failure_reason,
                         rule_version, model, skill_id, linked_trace_id, dim_key,
                         layer_key, value_type, proof_run_registry_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entity_type, ref_tag, entity_name, round_no, raw,
                        oracle_answer, llm_answer, win, failure_reason,
                        rule_version, model, JUDGE_SKILL_ID, linked_trace_id,
                        dim_key, layer_key, vt, proof_run_registry_id,
                    ),
                )
                conn.commit()
            rounds.append(
                {
                    "round_no": round_no, "value": raw,
                    "oracle": oracle_answer, "llm": llm_answer,
                    "win": win, "failure_reason": failure_reason,
                }
            )
            if not win:
                failures.append(rounds[-1])
                streak = 0
            else:
                streak += 1
            if streak > max_streak:
                max_streak = streak
            round_no += 1

        # ---- THE TWO-PART VERIFY, THEN THE ACTIVATION GATE (2026-09-26) ----
        #
        # THE HUMAN: "isactive 0 -> 1 / this must work by LLM 7B, it have 2 part,
        # TDD verfity and ontology Verifiy".
        #
        # MEASURED DEFECT this closes: this module flipped `is_active` on a
        # STREAK ALONE. It mentioned `ontology` 0 times, `tdd_pass` /
        # `tdd_verify_pass` / `ontology_pass` 0 / 0 / 0 times, and never called
        # `register_approval.record()` — `register_approve` held 0 rows. So a run
        # produced `is_active=1` with NO 2-part evidence behind it, while
        # `check_stages` requires three stages and `classify()` returns UNKNOWN
        # (not WORKABLE) when a stage never ran.
        #
        # THE ORDER IS LOAD-BEARING: the verify runs FIRST and the activation is
        # attempted ONLY when its verdict is APPROVED. A gate that is asked
        # before the evidence exists is a gate that cannot refuse.
        two_part: dict[str, Any] = {"attempted": False}
        # A RUN THAT MEASURED NOTHING MUST NOT RE-RECORD A VERDICT.
        #
        # MEASURED DEFECT (2026-09-27, found by RUNNING the whole-site sweep and
        # then re-running `_proof_proof_run_activation.py`, which went 7/7 -> 6/7):
        #
        # `two_part_verify.verify()` computes `tdd_pass = sum(win==1 for rounds)`.
        # When the streak ALREADY meets the target the round loop breaks on its
        # FIRST iteration, so `rounds == []` and `tdd_pass == 0`. `verify()` then
        # calls `register_approval.record()`, which UPSERTS -- so a 0-round run
        # OVERWROTE a previously APPROVED verdict with PENDING.
        #
        # MEASURED CONSEQUENCE: `worker_identity` (P/1) was the ONE target whose
        # 2-part verdict was APPROVED, i.e. the one that could activate. The
        # whole-site sweep ran it with 0 rounds and rewrote its verdict to
        # `PENDING (tdd=0 tdd_verify=10 ontology=1)`, so `activate()` now returns
        # `NOT_PROVEN` where it previously returned `ALREADY_ACTIVE`.
        #
        # A 0-round run has NO tdd evidence. Recording `tdd_pass=0` from it is not
        # a measurement -- it is the ABSENCE of one, written as if it were a
        # result. The verify is therefore SKIPPED, and the skip is REPORTED.
        if write and ref_tag and not rounds:
            two_part = {
                "attempted": False, "ok": False,
                "code": "NO_ROUNDS_TO_VERIFY",
                "message": ("the run measured 0 rounds (the streak already met "
                            "the target), so there is no tdd evidence to record "
                            "-- re-recording would overwrite a real verdict with "
                            "tdd_pass=0"),
            }
        elif write and ref_tag:
            try:
                import two_part_verify as _tpv
                two_part = _tpv.verify(
                    conn, ref_tag=ref_tag, rounds=rounds, model=model,
                    rule_version=rule_version, seed=seed,
                    # A LINE, not a symbol. MEASURED DEFECT (2026-09-27): this was
                    # `"llm_100_run_harness.py:run_harness"` -- a FUNCTION NAME,
                    # which `citation_discipline.is_citation` correctly REFUSES
                    # (`is_citation=False`). The same defect class the repo already
                    # recorded for 12 `dimension_binding_registry` rows carrying
                    # `cite_ref='subject_kind_registry.py:SUBJECT_KIND_SEED'`.
                    cite_ref="llm_100_run_harness.py:1577",
                    write=True)
                two_part["attempted"] = True
            except Exception as e:
                # A verify that cannot be reached is REPORTED, never silent.
                two_part = {"attempted": True, "ok": False,
                            "code": "VERIFY_UNREACHABLE", "message": str(e)}

        # ---- WIRE THE ACTIVATION GATE (2026-09-25) ------------------------
        #
        # THE HUMAN: "proof run, is the key for is_active=0 -> 1".
        #
        # THE KEY IS A PROVEN STREAK, and the gate that turns it into an
        # activation ALREADY EXISTED — but it was UNWIRED. MEASURED: this module
        # did not mention `activation_gate` at all, so the harness ran the
        # 100-run, recorded `proof_run`, and NEVER called `activate()`.
        # MEASURED: `worker_identity` had streak 40 >= target 20, so
        # `assert_may_activate` returned True — and nothing flipped. A gate that
        # is never called is a gate that does not exist.
        #
        # THE DISPATCH IS BY THE ref_tag's OWN KIND, not by a guess. MEASURED:
        # `prompt_registry.resolve_ref_tag` returns `kind` = `prompt` / `flow` /
        # `job_ref`. A FLOW has no entity letter (it is a `workflow_registry` row
        # keyed by `workflow_key`), so `activate()` cannot address it — the gate
        # provides `activate_flow()` for exactly that case. Calling the wrong one
        # would report `NO_SUCH_VERSION` for a flow that exists.
        #
        # The gate still DECIDES: it re-checks the streak, the ref_tag resolution
        # and the citation, and it REFUSES below target. This call cannot force
        # an activation; it can only let a PROVEN one happen.
        activation: dict[str, Any] = {"attempted": False}
        # THE 2-PART GUARD. MEASURED (2026-09-26): without this, a streak alone
        # flipped `is_active`. The activation is now attempted ONLY when the
        # 2-part verdict is APPROVED — i.e. all three stages passed.
        if write and ref_tag and two_part.get("verdict") != "APPROVED":
            activation = {
                "attempted": False, "ok": False,
                "code": "NOT_APPROVED_BY_TWO_PART_VERIFY",
                "message": ("the 2-part verify returned %r, so the activation "
                            "gate was NOT asked — a streak alone is not proof"
                            % two_part.get("verdict")),
                "two_part_verdict": two_part.get("verdict"),
                "two_part_stages": two_part.get("stages"),
            }
        elif write and ref_tag:
            try:
                import activation_gate as _ag
                import prompt_registry as _pr
                resolved = _pr.resolve_ref_tag(conn, ref_tag)
                kind = str(resolved.get("kind") or "")
                common = dict(ref_tag=ref_tag,
                              # A LINE, not a symbol. MEASURED DEFECT (2026-09-27):
                              # this was `"llm_100_run_harness.py:run_harness"`, a
                              # FUNCTION NAME, so `activate()` returned `UNCITED`
                              # and `is_active` NEVER flipped -- the whole-site
                              # blocker. With a checkable ref the SAME call returns
                              # `ALREADY_ACTIVE`, i.e. the gate lets it through.
                              cite_ref="llm_100_run_harness.py:1628",
                              target=streak_target, rule_version=rule_version,
                              model=model, decided_by="llm_100_run_harness",
                              commit=False)
                if kind == "flow":
                    activation = _ag.activate_flow(
                        conn, str(resolved.get("ref_tag") or ref_tag), **common)
                elif kind == "prompt":
                    # A prompt is addressed by its OWN key, and the gate resolves
                    # the entity behind it. `activate()` needs a letter/ref_id.
                    #
                    # MEASURED DEFECT (2026-09-26): this used `skill_id`, but
                    # `P`'s pk_column is `prompt_id` (`entity_type_registry`).
                    # `worker_identity` has prompt_id=1 and skill_id=3, so the
                    # old code addressed prompt_id=3 — a DIFFERENT prompt
                    # (`mouse_spot_verify__...`) — and because that prompt also
                    # resolves, the gate would have activated the WRONG entity
                    # silently. The ref_id is now the pk the letter declares.
                    row = conn.execute(
                        "SELECT prompt_id FROM prompt_registry WHERE prompt_key=?",
                        (str(resolved.get("ref_tag") or ref_tag),)).fetchone()
                    _pid = int(row["prompt_id"]) if row else 0
                    # THE `version` ARGUMENT IS THE `version_registry` VERSION,
                    # NOT `rule_version`. MEASURED DEFECT (2026-09-27, found by
                    # RUNNING `--remeasure`): this passed `rule_version` (6 for
                    # `worker_identity`), but `version_registry` holds version 1
                    # only, so `activate()` returned `NO_SUCH_VERSION` for a
                    # version that was never the subject. The proof
                    # `_proof_proof_run_activation.py` QC-02 had ALREADY recorded
                    # this exact defect and fixed it IN THE PROOF -- but the
                    # harness kept the bug, so the proof passed while the real
                    # call failed. A fix applied only to the test is not a fix.
                    #
                    # The version is DERIVED from `version_registry`, never
                    # hard-coded: a hard-coded 1 would break the moment a second
                    # version exists.
                    _vrow = conn.execute(
                        "SELECT MAX(version) AS v FROM version_registry "
                        "WHERE entity_type='P' AND entity_ref_id=?",
                        (_pid,)).fetchone()
                    _ver = int(_vrow["v"]) if _vrow and _vrow["v"] is not None else 1
                    activation = _ag.activate(
                        conn, "P", _pid, _ver, **common)
                elif kind == "entity_id":
                    # A DIMENSION BINDING (letter `Y`) IS ACTIVATED BY ITS OWN
                    # WRITER, NOT BY `activate()`.
                    #
                    # MEASURED DEFECT (2026-09-27): `dimension_binding_registry`
                    # is IN `activation_scope()["in_scope"]`, and
                    # `activate_binding()` is its ONLY writer — but this harness
                    # had NO `entity_id` branch, so every binding fell into the
                    # `else` below and returned `NO_ACTIVATABLE_ENTITY`.
                    # MEASURED: all 6 `ui` bindings reached a `two_part_verify`
                    # verdict of APPROVED with streak 10 >= target 10, and
                    # `is_active` stayed 0. A gate with no key is a gate that
                    # does not exist.
                    #
                    # `activate()` writes `version_registry`, which a binding
                    # does not have; `activate_binding()` writes
                    # `dimension_binding_registry.is_active` and DELEGATES to the
                    # same `assert_may_activate`, so "proven" still has ONE
                    # definition.
                    _letter = str(resolved.get("letter") or "")
                    _rid = int(resolved.get("ref_id") or 0)
                    if _letter == "Y":
                        import dimension_binding_registry as _dbr
                        _brow = conn.execute(
                            "SELECT subject_kind, dimension_key "
                            "FROM dimension_binding_registry WHERE binding_id=?",
                            (_rid,)).fetchone()
                        if not _brow:
                            activation = {
                                "attempted": True, "ok": False,
                                "code": "NO_SUCH_BINDING",
                                "message": ("ref_tag %r resolves to Y/%d, but no "
                                            "dimension_binding_registry row has "
                                            "that binding_id" % (ref_tag, _rid))}
                        else:
                            activation = _dbr.activate_binding(
                                conn, str(_brow["subject_kind"]),
                                str(_brow["dimension_key"]),
                                # THE SAME LINE the `common` dict carries. It is
                                # repeated literally because `cite_ref` is a KEY
                                # in `common`, not a name in scope — MEASURED
                                # 2026-09-27: passing the bare name raised
                                # `NameError: name 'cite_ref' is not defined`,
                                # which the `except` reported as
                                # `GATE_UNREACHABLE` and left `is_active` at 0.
                                cite_ref="llm_100_run_harness.py:1628",
                                target=streak_target,
                                rule_version=rule_version, model=model,
                                commit=False)
                    else:
                        activation = {
                            "attempted": False, "ok": False,
                            "code": "NO_ACTIVATABLE_ENTITY",
                            "message": ("ref_tag %r resolves to entity %s/%d, "
                                        "which has no activation writer wired "
                                        "here" % (ref_tag, _letter, _rid))}
                else:
                    # A `job_ref` (a legacy task label) has no entity to activate.
                    # REPORTED, not silently skipped.
                    activation = {"attempted": False, "ok": False,
                                  "code": "NO_ACTIVATABLE_ENTITY",
                                  "message": ("ref_tag %r resolves to kind %r, "
                                              "which has no entity to activate"
                                              % (ref_tag, kind))}
                activation["attempted"] = True
                activation["ref_tag_kind"] = kind
                # THE ACTIVATION IS COMMITTED HERE, AND ONLY WHEN IT CHANGED.
                #
                # MEASURED DEFECT (2026-09-27): every activation call passes
                # `commit=False` (so a caller can batch), and NOTHING in this
                # function ever committed afterwards. MEASURED: all 6 `ui`
                # bindings returned `code=ACTIVATED` while
                # `dimension_binding_registry.is_active` stayed 0 — the writer
                # said it wrote, the table said it did not, and the run reported
                # the writer. A write that is never committed is not a write.
                #
                # `changed` is checked so a no-op (`ALREADY_ACTIVE`) does not
                # commit unrelated pending work.
                if activation.get("changed"):
                    conn.commit()
            except Exception as e:
                # A gate that cannot be reached is REPORTED, never silent.
                activation = {"attempted": True, "ok": False,
                              "code": "GATE_UNREACHABLE", "message": str(e)}

        # ---- THE FACTOR LINK (GAP 2 of FULL.CYCLE.NO.GAP) ------------------
        #
        # MEASURED DEFECT (2026-09-27): `proof_run.ref_tag` ->
        # `skill_factor_registry.factor_key` joined **0 rows**. The ref_tags are
        # `worker_identity`, `1.1F`, `tutorial`, `mouse_spot_verify__...`; the
        # factor keys are `crud_create`, `lazy_fk_ontology_relation`, ... So a
        # proof run produced a `proof_run` row and the FACTOR LAYER NEVER LEARNED
        # THE RUN HAPPENED. `skill_factor_proof`'s only writer was
        # `measure_skill.py:348`; `run_harness` never called `record_proof`.
        #
        # The link is ATTEMPTED only when the ref_tag IS a factor key. When it is
        # not, the gap is REPORTED with a named reason -- never silently skipped,
        # because a silent skip is indistinguishable from a link that works.
        factor_link: dict[str, Any] = {"attempted": False}
        if write and ref_tag:
            try:
                import skill_factor as _sf
                _frow = conn.execute(
                    "SELECT factor_key, metric_kind, metric_target "
                    "FROM skill_factor_registry WHERE factor_key=? AND is_active=1",
                    (ref_tag,)).fetchone()
                if not _frow:
                    factor_link = {
                        "attempted": False, "ok": False,
                        "code": "REF_TAG_IS_NOT_A_FACTOR",
                        "message": ("ref_tag %r is not a skill_factor_registry "
                                    "factor_key, so this run cannot be recorded "
                                    "against a factor" % ref_tag),
                    }
                else:
                    # THE MEASUREMENT IS THE RUN'S OWN WIN RATE, in the factor's
                    # OWN unit. `record_proof` DERIVES `metric_pass` from the
                    # factor's kind and target, so this cannot assert a pass.
                    _wins = sum(1 for r in rounds if r.get("win") == 1)
                    _total = len(rounds)
                    _val = (round(100.0 * _wins / _total, 2) if _total else 0)
                    _sk = conn.execute(
                        "SELECT skill_key FROM skill_factor_registry "
                        "WHERE factor_key=?", (ref_tag,)).fetchone()
                    _skill = str(_sk["skill_key"] or "") if _sk else ""
                    if not _skill:
                        factor_link = {
                            "attempted": False, "ok": False,
                            "code": "FACTOR_HAS_NO_SKILL",
                            "message": ("factor %r is GENERIC (skill_key IS NULL), "
                                        "so there is no skill to record the proof "
                                        "against" % ref_tag),
                        }
                    else:
                        # THE SCOPE. The READER is this harness; the POPULATION
                        # is the ROUNDS the win rate was computed over — the set
                        # `_wins / _total` counts, not the whole run table. The
                        # table count is the run's own round total, so a scope
                        # that counted a different set would be caught.
                        _scope = _sf.proof_scope(
                            conn, ref_tag,
                            reader="llm_100_run_harness.run_harness",
                            reader_cite="llm_100_run_harness.py:run_harness",
                            count_command=("llm_100_run_harness.py:run_harness "
                                           "ref_tag=%s" % ref_tag),
                            population_count=_total,
                            population=("rounds WHERE ref_tag=%s" % ref_tag),
                            population_cite="llm_100_run_harness.py:run_harness",
                            table_count=_total)
                        _rec = _sf.record_proof(
                            conn, _skill, ref_tag, metric_value=_val,
                            evidence_ref=("llm_100_run_harness.py:run_harness "
                                          "ref_tag=%s wins=%d rounds=%d"
                                          % (ref_tag, _wins, _total)),
                            scope=_scope)
                        factor_link = {"attempted": True, "ok": True,
                                       "code": "RECORDED", "skill_key": _skill,
                                       "metric_value": _val,
                                       "wins": _wins, "rounds": _total,
                                       "record": _rec}
            except Exception as e:
                # A link that cannot be made is REPORTED, never silent.
                factor_link = {"attempted": True, "ok": False,
                               "code": "FACTOR_LINK_FAILED",
                               "message": "%s: %s" % (type(e).__name__, e)}

        # ---- THE LESSON LINK (GAP 5 of FULL.CYCLE.NO.GAP) ------------------
        #
        # MEASURED DEFECT (2026-09-27): `skill_lesson` held 128 rows and **0 of
        # them** named a `proof_run` ref_tag in `source_ref`. The `source_type`
        # values were `self_fail` (126) and `github_proofed_lesson` (2). So a
        # proof run produced NO lesson -- the cycle's LAST link, the one the human
        # named ("it can be totally full cycle with lesson"), was the missing one.
        #
        # A lesson is written ONLY when the run FAILED TO PROVE. MEASURED (my
        # first version was WRONG twice):
        #   1. I keyed it on `failures` (rounds where `win == 0`), but a run can
        #      be NOT_QUALIFIED with EVERY round won -- the streak simply never
        #      reached the target. That run DID fail to prove.
        #   2. I then keyed it on `verdict != "QUALIFIED"`, but `ignore_streak`
        #      FORCES the verdict to NOT_QUALIFIED, so every verify run looked
        #      like a failure.
        # The honest test is the STREAK against the TARGET: did this run prove?
        #
        # A QUALIFIED run has nothing to teach; writing a lesson for it would fill
        # the store with noise and make "has a lesson" meaningless. The lesson's
        # `source_ref` is a CHECKABLE citation naming the run.
        lesson_link: dict[str, Any] = {"attempted": False}
        _proved = streak >= streak_target
        if write and ref_tag and not _proved:
            try:
                import skill_learning as _sl
                _first = failures[0] if failures else None
                _why = (_first.get("failure_reason") if _first
                        else ("the streak reached %d, below the target %d"
                              % (streak, streak_target)))
                _skill_for_lesson = ""
                _fl = factor_link if factor_link.get("ok") else {}
                if _fl.get("skill_key"):
                    _skill_for_lesson = str(_fl["skill_key"])
                else:
                    _prow = conn.execute(
                        "SELECT s.skill_key FROM prompt_registry p "
                        "JOIN skill_registry s ON s.skill_id = p.skill_id "
                        "WHERE p.prompt_key=?", (ref_tag,)).fetchone()
                    if _prow:
                        _skill_for_lesson = str(_prow["skill_key"] or "")
                if not _skill_for_lesson:
                    lesson_link = {
                        "attempted": False, "ok": False,
                        "code": "NO_SKILL_FOR_LESSON",
                        "message": ("ref_tag %r resolves to no skill, so the "
                                    "lesson has no skill to attach to" % ref_tag),
                    }
                else:
                    # THE CITATION IS CHECKABLE. MEASURED, twice wrong:
                    #   (1) `"proof_run:worker_identity rv=6 model=..."` was
                    #       REFUSED -- not a checkable shape.
                    #   (2) `"llm_100_run_harness.py:1829 ref_tag=... rv=..."` was
                    #       ALSO REFUSED. `citation_discipline._PATH_LINE` is
                    #       ANCHORED (`^...$`), so ANY trailing context breaks it.
                    # The gate was right both times; the citation was wrong. The
                    # citation is now EXACTLY `path:line`; the run's identity
                    # (ref_tag / rule_version / model / streak / target) lives in
                    # the lesson TEXT, where it belongs.
                    _cite = "llm_100_run_harness.py:1832"
                    _res = _sl.add_lesson(
                        _skill_for_lesson,
                        ("proof run on %r did not prove: %s "
                         "(ref_tag=%s rv=%d model=%s streak=%d target=%d)"
                         % (ref_tag, _why, ref_tag, rule_version, model,
                            streak, streak_target)),
                        source_type="self_fail",
                        source_ref=_cite)
                    lesson_link = {"attempted": True, "ok": True,
                                   "code": "RECORDED",
                                   "skill_key": _skill_for_lesson,
                                   "source_ref": _cite,
                                   "result": _res}
            except Exception as e:
                lesson_link = {"attempted": True, "ok": False,
                               "code": "LESSON_LINK_FAILED",
                               "message": "%s: %s" % (type(e).__name__, e)}

        return {
            "verdict": verdict,
            "seed": seed,
            "model": model,
            # THE ROUTE, RECORDED. A 100-run must state WHICH route served it and
            # whether that came from the registry or a fallback — a silent
            # fallback would report a model the run never actually resolved.
            "llm_route": route.get("llm_route"),
            "model_source": route.get("source"),
            "route_pool": route.get("pool", []),
            "rounds_run": len(rounds),
            "final_streak": streak,
            "max_streak": max_streak,
            "rule_version": rule_version,
            "instruction_source": instruction_source,
            "composition_key": composition_key,
            # THE ORACLE AND THE TARGET, RECORDED with their source. A 100-run
            # must state WHICH oracle judged it and WHICH target it aimed at —
            # a silent fallback would report a proof it never actually ran.
            "oracle_source": oracle_source,
            "streak_target": streak_target,
            "target_source": target_source,
            "options_source": options_source,
            "option_count": len(options),
            # THE VALUE FORMAT AND ITS PROOF METHOD, RECORDED (added 2026-09-26).
            # A run must state WHICH format it measured and HOW that format is
            # proved -- the human: "proof run need to understand and know which
            # data format need to how to proof = same language".
            "value_type": vt,
            "value_type_source": vt_source,
            "proof_method": proof_method,
            # THE MEASURED-UNIT DECLARATION, RECORDED WITH ITS REASON (GAP 1).
            # A NULL id is a REPORTED gap, not a silent one: the reason says
            # whether the ref_tag is not a prompt, or the declaration is absent.
            "proof_run_registry_id": proof_run_registry_id,
            "proof_run_registry_why": proof_run_registry_why,
            # THE FACTOR LINK, RECORDED WITH ITS REASON (GAP 2). A run that
            # cannot reach a factor says WHY, so the gap is visible.
            "factor_link": factor_link,
            # THE LESSON LINK, RECORDED WITH ITS REASON (GAP 5). A FAILING run
            # writes a lesson whose source_ref names the ref_tag, so the cycle
            # closes and the lesson is traceable to the run.
            "lesson_link": lesson_link,
            "wrote_evidence": bool(write),
            "layer_key": layer_key,
            # THE 2-PART VERIFY OUTCOME, RECORDED. `attempted=False` means it was
            # not asked (a dry run); `verdict` is the DERIVED decision, and
            # `stages` names which of the three passed.
            "two_part_verify": two_part,
            # THE ACTIVATION OUTCOME, RECORDED. `attempted=False` means the gate
            # was not asked (a dry run, or the 2-part verify did not APPROVE);
            # `ok=False` means it REFUSED, and the code names why.
            "activation": activation,
            "failures": failures,
            "rounds": rounds,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL
    out = run_harness(seed=seed, model=model)
    print(json.dumps(
        {k: v for k, v in out.items() if k != "rounds"},
        ensure_ascii=False, indent=2,
    ))
    print(f"verdict={out['verdict']} model={out['model']} rounds={out['rounds_run']} max_streak={out['max_streak']}")