"""prompt_generator.py — compose a prompt from the registers.

THE FORMULA
-----------
    prompt = skill + study + wording

    skill.context        -> skill_registry.description
    skill.output_contract-> skill_registry.output_schema
    wording.template     -> wording_registry.template, ONE value PER dimension
    study.case_payload   -> study_registry.fields_json

    workflow = ordered sequence of INDEPENDENT prompts

WHY THIS EXISTS
---------------
The registers stored the formula but nothing EXECUTED it — a warehouse with no
forklift. This module is the forklift: it reads the registers and produces the
prompt text, so the schema is proven by use rather than by assertion.

It also carries the capability retired from `prompt_dimension.py`: enumerating
every combination of wording values (10 values across 4 dimensions = 36
combinations) so test variants can be generated in bulk.

SSOT: this module reads ONLY the registers. It does not import prompt_dimension.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re as _re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# ---------------------------------------------------------------------------
# THE LOGIC LAYERS — the axis the register was missing.
#
# `dim_key` groups the settings; here the group is the LAYER. The values of each
# dimension are the layers themselves, so adding `metric_logic` or `env_logic`
# is a new `wording_key` row, NOT a code change. That is what "layer is design"
# means in table form.
#
# Each layer carries three settings, and they are the minimum a SCOPED question
# needs — measured, not guessed. My first hand-written prompt had none of them
# and scored 42.9% on the middle layer with the same 7B.
# ---------------------------------------------------------------------------
LOGIC_LAYERS: dict[str, dict[str, Any]] = {
    "env_logic": {
        "sort_order": 1,
        "ask": ("Is the STATE this factor depends on established — is there a "
                "named environment, window or resource the measurement runs in?"),
        "guard": ("You are judging the environment the factor assumes ONLY. "
                  "metric_kind, metric_unit and metric_target are not part of "
                  "this question. Do not consider them."),
        "examples": ("EXAMPLE (YES): the factor names the window it runs in.\n"
                     "EXAMPLE (NO): the measurement's environment is unstated."),
    },
    "metric_logic": {
        "sort_order": 2,
        "ask": ("Is this a measurable metric — is metric_kind one of boolean, "
                "count, pct, score_0_100, metric_unit non-empty, and "
                "metric_target numeric (true/false when the kind is boolean)?"),
        "guard": ("You are judging whether the metric CAN be measured ONLY. You "
                  "are not judging whether the unit names a subject, and not "
                  "whether the kind agrees with the unit. Those are different "
                  "questions asked elsewhere."),
        "examples": ("EXAMPLE (YES): kind=count, unit=\"count of retries\", "
                     "target=\"0\" -> all three hold -> YES\n"
                     "EXAMPLE (NO): kind=vibes, target=\"high\" -> kind is not "
                     "in the list -> NO"),
    },
    "consistency_logic": {
        "sort_order": 3,
        "ask": ("Does metric_kind AGREE with metric_unit — a pct in percent, a "
                "count not in percent, a score in scores?"),
        "guard": ("metric_target is NOT part of this question. Do not consider "
                  "it. If metric_kind is not one of boolean, count, pct, "
                  "score_0_100 the question cannot be answered — reply N/A."),
        "examples": ("EXAMPLE (YES): kind=count, unit=\"count of dead services\" "
                     "-> a count unit for a count kind -> YES\n"
                     "EXAMPLE (NO): kind=pct, unit=\"count of dead services\" "
                     "-> a percent kind with a count unit -> NO"),
    },
    "grounding_logic": {
        "sort_order": 4,
        "ask": ("Does metric_unit name a SPECIFIC SUBJECT a third party could "
                "count or measure?"),
        "guard": ("You are judging the unit ONLY. metric_kind and metric_target "
                  "are not part of this question. Do not consider them."),
        "examples": ("EXAMPLE (YES): \"count of findings written without a "
                     "checkable citation\" -> names findings -> YES\n"
                     "EXAMPLE (NO): \"count\" -> names no subject -> NO"),
    },
}


class ComposeError(ValueError):
    """Raised when a composition names a skill/study/wording that does not exist."""


# ---------------------------------------------------------------------------
# TERMINOLOGY — the register the prompt generator was NOT reading.
#
# WHY (the user, 2026-09-23):
#
#     "prompt generator need to included teminology_registry, it can help alot
#      fucking mis-understand problem, all by index, be easy for 7B"
#
# MEASURED BEFORE THIS: `grep -c terminology prompt_generator.py` -> 0. The
# generator composed prompts whose NAMES were free text — `LOGIC_LAYERS` is a
# Python dict, and `wording_registry.template` holds prose. So a prompt could
# name a thing the register had never defined, and the 7B would answer about a
# word nobody had agreed on. That is the mis-understanding the user names.
#
# THE FIX, and it is the same rule that fixed `register_fill`: the model does not
# choose a NAME, it chooses an INDEX. An index is checkable (it is either in
# range or it is not), while a name is not (a plausible name is indistinguishable
# from a correct one). So:
#
#     the register supplies the INDEXED LIST   (deterministic)
#     the 7B answers with an INDEX             (checkable)
#     an out-of-range index is REFUSED         (the gate)
#
# THE INDEX IS `term_id`, NOT THE LIST POSITION
# ---------------------------------------------
# MEASURED (`_diag_terminology_index.py`): `term_id` is an INTEGER PRIMARY KEY
# (rowid), assigned at INSERT and never reused, with 0 gaps in the current 32
# rows. So `ORDER BY term_id` is stable under INSERT — a new term APPENDS and
# every existing index keeps its meaning. A list POSITION would shift the moment
# a term is inserted in the middle, and a stored answer would silently point at a
# different term. The index is therefore the `term_id`, and the list is ordered
# by it so the two agree.
#
# `is_active` IS NOT A FILTER HERE, AND THAT IS DELIBERATE
# -------------------------------------------------------
# MEASURED: all 32 rows are `is_active=0`. That is BY DESIGN —
# `_proof_terminology_registry.py:126` asserts "a term is unproven until proven",
# and activation is `activation_gate.activate()`'s job. An index filtered on
# `is_active=1` would be EMPTY, and the feature would look broken while being
# correct. So the reader returns EVERY term and REPORTS the active count, so a
# caller can see the state instead of inferring it from an empty list.
# ---------------------------------------------------------------------------

TERM_SLOT_RE = _re.compile(r"\{\{term:([a-zA-Z0-9_]+)\}\}")


def terminology_index(
    conn: sqlite3.Connection, *, include_inactive: bool = True
) -> dict[str, Any]:
    """The register as an INDEXED list. `{ok, count, active, terms, by_index}`.

    `terms` is ordered by `term_id`, and each entry carries BOTH the `index`
    (what the 7B answers with) and the `term_id` (what the index IS). They are
    equal by construction, and carrying both means a caller can DETECT a shift
    rather than trust that there was none.

    `include_inactive=True` is the default because `is_active=0` is the declared
    default for a term (see the block comment above) — filtering on it would
    return nothing.
    """
    sql = ("SELECT term_id, term_key, term_kind, parent_term_id, definition, "
           "cite_ref, is_active FROM terminology_registry ")
    if not include_inactive:
        sql += "WHERE is_active = 1 "
    sql += "ORDER BY term_id"
    try:
        rows = [dict(r) for r in conn.execute(sql)]
    except sqlite3.OperationalError as exc:
        # "the table says no" and "there is no table yet" are different answers.
        # Conflating them made an un-migrated DB refuse EVERY term once already.
        return {"ok": False, "code": "NO_terminology_registry",
                "message": "terminology_registry is not readable: %s" % exc,
                "count": 0, "active": 0, "terms": [], "by_index": {}}

    terms: list[dict[str, Any]] = []
    for r in rows:
        terms.append({
            "index": int(r["term_id"]),
            "term_id": int(r["term_id"]),
            "term_key": r["term_key"],
            "term_kind": r["term_kind"],
            "parent_term_id": r["parent_term_id"],
            "definition": r["definition"],
            "cite_ref": r["cite_ref"],
            "is_active": int(r["is_active"] or 0),
        })
    return {
        "ok": True,
        "count": len(terms),
        "active": sum(1 for t in terms if t["is_active"]),
        "terms": terms,
        "by_index": {t["index"]: t for t in terms},
    }


def render_term_index(conn: sqlite3.Connection) -> str:
    """The indexed list as TEXT — what the 7B is given.

    One term per line, `[index] term_key — definition`. The index is FIRST on the
    line because that is the token the model must return, and a model reads the
    first token of a line as the label.

    The definition is TRUNCATED to one line: a multi-line definition would break
    the one-term-per-line shape, and a model that loses the line structure loses
    the index with it.
    """
    idx = terminology_index(conn)
    if not idx["ok"]:
        return ""
    lines = []
    for t in idx["terms"]:
        defn = " ".join(str(t["definition"] or "").split())
        if len(defn) > 160:
            defn = defn[:157] + "..."
        lines.append("[%d] %s (%s) — %s"
                     % (t["index"], t["term_key"], t["term_kind"], defn))
    return "\n".join(lines)


def assert_terms_registered(
    conn: sqlite3.Connection, text: str, *, where: str = "template"
) -> tuple[bool, str]:
    """`(ok, reason)`. Every `{{term:NAME}}` in `text` must be a REGISTERED term.

    This is the WRITE-site gate, and it is the point of the whole feature: a
    prompt that names an unregistered term asks the model about a word nobody
    defined, and the model answers confidently. The run then looks like a
    measurement of that term and is a measurement of nothing — the same defect
    `assert_template_slots` exists to catch for brace syntax.

    The reason NAMES the term, because the fix is "register this word", not "try
    again". A refusal that does not say WHICH word is missing sends the reader
    back to guessing.
    """
    wanted = TERM_SLOT_RE.findall(text or "")
    if not wanted:
        return True, "no {{term:...}} slots"
    idx = terminology_index(conn)
    if not idx["ok"]:
        return False, ("cannot check {{term:...}} slots: %s" % idx["message"])
    known = {t["term_key"] for t in idx["terms"]}
    missing = sorted(set(wanted) - known)
    if missing:
        return False, (
            "template names term(s) %s that are NOT in terminology_registry — a "
            "name that was never registered is an invented word. Register it "
            "with a definition and a cite_ref first. (where=%s)"
            % (missing, where))
    return True, ("all %d term slot(s) are registered: %s"
                  % (len(set(wanted)), sorted(set(wanted))))


def resolve_term_index(
    conn: sqlite3.Connection, answer: Any
) -> tuple[bool, dict[str, Any] | str]:
    """`(ok, term_or_reason)`. The 7B's answer must be an index that EXISTS.

    REFUSES an out-of-range index. It does NOT clamp to the nearest term: a
    clamped answer is a wrong answer wearing a right one's clothes, and the
    caller cannot tell. An index the register does not hold is a refusal, and the
    refusal says what the register DOES hold.
    """
    idx = terminology_index(conn)
    if not idx["ok"]:
        return False, idx["message"]
    raw = str(answer or "").strip()
    # A model often wraps the number: "[7]", "7.", "index 7". Take the first
    # integer, and only the first — a second number means the answer is not a
    # single index and must not be guessed at.
    nums = _re.findall(r"\d+", raw)
    if not nums:
        return False, ("answer %r holds no index; the register holds %d term(s) "
                       "indexed %s"
                       % (raw, idx["count"],
                          sorted(idx["by_index"])[:8]))
    if len(nums) > 1:
        return False, ("answer %r holds %d numbers; an index answer must be ONE "
                       "index" % (raw, len(nums)))
    n = int(nums[0])
    if n not in idx["by_index"]:
        return False, ("index %d is NOT in terminology_registry (it holds %d "
                       "term(s), indexed %s) — an out-of-range index is refused, "
                       "never clamped to the nearest term"
                       % (n, idx["count"], sorted(idx["by_index"])[:8]))
    return True, idx["by_index"][n]


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ---------------------------------------------------------------------------
# read the parts
# ---------------------------------------------------------------------------
def get_skill(skill_key: str, *, db_path: Path | str | None = None) -> dict[str, Any]:
    # POST-SPLIT: the composition engine's "skill" is a COMPONENT (a prompt
    # composition target) — the parent of `wording_registry`. It reads
    # component_registry and, when the component IS a skill, the row's
    # `skill_ref` bridges to the real skill. Reading `skill_registry` here would
    # silently pick up the 27 skills, NONE of which owns any wording value.
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM component_registry WHERE skill_key = ? AND is_active = 1",
            (skill_key,),
        ).fetchone()
        if not row:
            raise ComposeError(f"skill not found or inactive: {skill_key}")
        return dict(row)
    finally:
        conn.close()


def get_study(study_key: str, *, db_path: Path | str | None = None) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM study_registry WHERE study_key = ? AND is_active = 1",
            (study_key,),
        ).fetchone()
        if not row:
            raise ComposeError(f"study not found or inactive: {study_key}")
        return dict(row)
    finally:
        conn.close()


def list_wording(
    skill_key: str, *, dim_key: str | None = None, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """All active wording values for a skill, optionally one dimension."""
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT w.* FROM wording_registry w "
            "JOIN component_registry k ON k.skill_id = w.skill_id "
            "WHERE k.skill_key = ? AND w.is_active = 1"
        )
        params: list[Any] = [skill_key]
        if dim_key:
            sql += " AND w.dim_key = ?"
            params.append(dim_key)
        sql += " ORDER BY w.dim_key, w.sort_order, w.wording_key"
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def list_dimensions(skill_key: str, *, db_path: Path | str | None = None) -> list[str]:
    """The dimension keys a skill has wording for, in a stable order."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT w.dim_key, MIN(w.sort_order) AS o FROM wording_registry w "
            "JOIN component_registry k ON k.skill_id = w.skill_id "
            "WHERE k.skill_key = ? AND w.is_active = 1 "
            "GROUP BY w.dim_key ORDER BY o, w.dim_key",
            (skill_key,),
        ).fetchall()
        return [r["dim_key"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# compose
# ---------------------------------------------------------------------------
def compose(
    skill_key: str,
    study_key: str,
    dim_values: dict[str, str] | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compose one prompt from skill + study + wording.

    `dim_values` maps dim_key -> wording_key, e.g.
        {"context": "strict_boundary", "negation": "stack", "output": "with_unknown"}

    A dimension with no value chosen is SKIPPED (not an error) — that is how a
    prompt can be a partial combination. A dimension naming a value that does
    not exist IS an error, because that is a typo, not a choice.
    """
    skill = get_skill(skill_key, db_path=db_path)
    study = get_study(study_key, db_path=db_path)

    # COMPARE BY ID, AND THE ID IS THE COMPONENT'S. MEASURED 2026-09-27, and it
    # CORRECTS an earlier mis-diagnosis in this file's own plan:
    #
    #   `get_skill` reads `component_registry` (see its docstring: the
    #   composition engine's "skill" IS a component). So `skill["skill_id"]` is
    #   a COMPONENT id, and `study_registry.skill_id` is declared
    #   `REFERENCES component_registry (skill_id)`.
    #
    # MEASURED: all 63 studies' `skill_id` resolves to a `component_registry`
    # row (63/63), and the 2 ACTIVE studies resolve to the components they
    # should (`worker_identity_case` -> `verdict_3line`, `mouse_spot_case` ->
    # `mouse_spot_verify`). The FK is RIGHT.
    #
    # A key comparison was tried and REVERTED: `study_registry.study_key` is NOT
    # a `component_registry.skill_key` (MEASURED: 2/63 agree), so joining by key
    # returned NOTHING and BROKE `verdict_3line`, which had worked. The 61/63
    # "agreement" with `skill_registry` is a coincidence of naming, not evidence.
    if int(study["skill_id"]) != int(skill["skill_id"]):
        raise ComposeError(
            f"study {study_key} belongs to component skill_id={study['skill_id']}, "
            f"not {skill_key} (skill_id={skill['skill_id']})"
        )

    dim_values = dict(dim_values or {})
    available = list_wording(skill_key, db_path=db_path)
    by_dim: dict[str, dict[str, dict[str, Any]]] = {}
    for w in available:
        by_dim.setdefault(w["dim_key"], {})[w["wording_key"]] = w

    # validate every requested dimension/value BEFORE building anything
    for dim, val in dim_values.items():
        if dim not in by_dim:
            raise ComposeError(
                f"skill {skill_key} has no dimension {dim!r}; "
                f"available: {sorted(by_dim)}"
            )
        if val not in by_dim[dim]:
            raise ComposeError(
                f"dimension {dim!r} has no value {val!r}; "
                f"available: {sorted(by_dim[dim])}"
            )

    # build the parts in a stable order
    parts: list[str] = []
    if skill.get("description"):
        parts.append(str(skill["description"]).strip())

    for dim in list_dimensions(skill_key, db_path=db_path):
        if dim not in dim_values:
            continue
        text = str(by_dim[dim][dim_values[dim]]["template"] or "").strip()
        if text:
            parts.append(text)

    if skill.get("output_schema"):
        parts.append(str(skill["output_schema"]).strip())

    # the study payload: the case fields, rendered as a table
    payload = _render_study(study)
    if payload:
        parts.append(payload)

    prompt_text = "\n\n".join(p for p in parts if p)
    return {
        "skill_key": skill_key,
        "study_key": study_key,
        "dim_values": dim_values,
        "prompt_text": prompt_text,
        "sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
        "composition_key": composition_key(skill_key, study_key, dim_values),
        "parts": len(parts),
    }


def _render_study(study: dict[str, Any]) -> str:
    """Render study.fields_json as the case payload block."""
    raw = study.get("fields_json")
    if not raw:
        return ""
    try:
        fields = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)
    if not isinstance(fields, list) or not fields:
        return ""
    lines = ["| sort | Field | Value |", "|--|-------|-------|"]
    for f in fields:
        if not isinstance(f, dict):
            continue
        lines.append(
            "| %s | %s | %s |"
            % (f.get("sort", ""), f.get("field", ""), f.get("value", ""))
        )
    return "\n".join(lines)


def composition_key(skill_key: str, study_key: str, dim_values: dict[str, str]) -> str:
    """Canonical key: sha256 over the sorted parts.

    Same parts -> same key, always. This is what makes a prompt REPRODUCIBLE
    rather than copied, and it is what lets a 100-run record WHICH composition
    it measured.
    """
    canonical = json.dumps(
        {
            "skill": skill_key,
            "study": study_key,
            "dims": {k: dim_values[k] for k in sorted(dim_values)},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# bulk enumeration — the capability ported from prompt_dimension.py
# ---------------------------------------------------------------------------
def enumerate_combinations(
    skill_key: str, *, db_path: Path | str | None = None
) -> list[dict[str, str]]:
    """Every combination of wording values, one value per dimension.

    This is the 36-combination capability from the retired prompt_dimension.py
    (context 2 x criterion 3 x negation 3 x output 2 = 36), now driven by
    `wording_registry` instead of a bespoke table.
    """
    dims = list_dimensions(skill_key, db_path=db_path)
    if not dims:
        return []
    per_dim: list[list[str]] = []
    for d in dims:
        vals = [w["wording_key"] for w in list_wording(skill_key, dim_key=d, db_path=db_path)]
        if not vals:
            return []
        per_dim.append(vals)
    out: list[dict[str, str]] = []
    for combo in itertools.product(*per_dim):
        out.append(dict(zip(dims, combo)))
    return out


def generate_variants(
    skill_key: str,
    study_key: str,
    *,
    persist: bool = False,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Compose every combination. Optionally persist each into prompt_registry.

    Persisting is idempotent: a prompt whose key already exists is skipped, so
    re-running does not duplicate.
    """
    combos = enumerate_combinations(skill_key, db_path=db_path)
    out: list[dict[str, Any]] = []
    for dim_values in combos:
        composed = compose(skill_key, study_key, dim_values, db_path=db_path)
        if persist:
            composed["prompt_key"] = persist_variant(composed, db_path=db_path)
        out.append(composed)
    return out


def persist_variant(composed: dict[str, Any], *, db_path: Path | str | None = None) -> str:
    """Store one composed variant as a prompt_registry row + its prompt_wording rows."""
    conn = _connect(db_path)
    try:
        skill = conn.execute(
            "SELECT skill_id FROM component_registry WHERE skill_key = ?",
            (composed["skill_key"],),
        ).fetchone()
        study = conn.execute(
            "SELECT study_id FROM study_registry WHERE study_key = ?",
            (composed["study_key"],),
        ).fetchone()
        if not skill or not study:
            raise ComposeError("skill or study vanished before persist")

        dims = composed["dim_values"]
        suffix = "_".join(f"{k}-{dims[k]}" for k in sorted(dims)) or "base"
        prompt_key = f"{composed['skill_key']}__{suffix}"

        row = conn.execute(
            "SELECT prompt_id FROM prompt_registry WHERE prompt_key = ?", (prompt_key,)
        ).fetchone()
        if row:
            return prompt_key  # idempotent

        cur = conn.execute(
            "INSERT INTO prompt_registry (prompt_key, name, description, skill_id, study_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                prompt_key,
                prompt_key,
                f"generated variant ({composed['composition_key']})",
                int(skill["skill_id"]),
                int(study["study_id"]),
            ),
        )
        prompt_id = int(cur.lastrowid)

        for dim, val in dims.items():
            w = conn.execute(
                "SELECT wording_id FROM wording_registry "
                "WHERE skill_id = ? AND dim_key = ? AND wording_key = ?",
                (int(skill["skill_id"]), dim, val),
            ).fetchone()
            if not w:
                raise ComposeError(f"wording vanished: {dim}={val}")
            conn.execute(
                "INSERT INTO prompt_wording (prompt_id, wording_id, dim_key) VALUES (?, ?, ?)",
                (prompt_id, int(w["wording_id"]), dim),
            )
        conn.commit()
        return prompt_key
    finally:
        conn.close()


def get_prompt_composition(
    prompt_key: str, *, db_path: Path | str | None = None
) -> dict[str, Any] | None:
    """Read a stored prompt back and re-compose it from its parts.

    This is the reproducibility check: the stored prompt must equal the prompt
    composed from its own skill + study + wording.
    """
    conn = _connect(db_path)
    try:
        p = conn.execute(
            "SELECT p.*, k.skill_key, s.study_key FROM prompt_registry p "
            "JOIN component_registry k ON k.skill_id = p.skill_id "
            "JOIN study_registry s ON s.study_id = p.study_id "
            "WHERE p.prompt_key = ? AND p.is_active = 1",
            (prompt_key,),
        ).fetchone()
        if not p:
            return None
        dims = {
            r["dim_key"]: r["wording_key"]
            for r in conn.execute(
                "SELECT pw.dim_key, w.wording_key FROM prompt_wording pw "
                "JOIN wording_registry w ON w.wording_id = pw.wording_id "
                "WHERE pw.prompt_id = ?",
                (int(p["prompt_id"]),),
            )
        }
    finally:
        conn.close()

    composed = compose(p["skill_key"], p["study_key"], dims, db_path=db_path)
    composed["prompt_key"] = prompt_key
    return composed


# ---------------------------------------------------------------------------
# COMPATIBILITY LAYER — the retired prompt_dimension.py API, backed by registers
#
# WHY: 5 call sites still call these names. Rather than rewrite each one (and
# risk drift), the SAME names are provided here, reading `wording_registry`
# instead of `prompt_dimension`. Callers change ONE import line.
#
# NOTE: `compose_prompt` here is SLOT SUBSTITUTION ({{dim:x}} -> text), which is
# a DIFFERENT operation from `compose()` above (which builds a prompt from
# parts). Both are needed; they are not alternatives.
# ---------------------------------------------------------------------------
# `re` is imported at the TOP of this module (as `_re`), because the terminology
# index — which sits above this line — needs it. It was imported HERE first, and
# moving the terminology block above this point made that a NameError at import.

SLOT_RE = _re.compile(r"\{\{dim:([a-zA-Z0-9_]+)\}\}")

# A brace group that matches NEITHER slot syntax. Such a group is not a slot, so
# neither composer will substitute it, and it reaches the model as literal text
# ("Does {{subject}} SERVE {{object}}?" sent verbatim). Nothing refused it: the
# rule lived in the composer's docstring, and writers never call the composer.
# This regex is what lets `assert_template_slots()` apply the rule at the WRITE.
BARE_SLOT_RE = _re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


class DimensionError(ValueError):
    """Raised when a combination names a dimension/value that does not exist."""


def assert_template_slots(template: str, *, where: str = "wording") -> None:
    """REFUSE a template whose braces are not real slots.

    Measured defect (2026-09-21): four `ontology_relation` relation templates
    were registered with `{{subject}}` / `{{object}}`. Those match `{{dim:x}}`
    and `{{case:field}}` equally not at all. `compose_prompt(strict=True)`
    would not have caught them either — it only refuses a slot it CAN see, and
    it refuses a template with NO slots, but `relation` templates were composed
    as PART of a larger template that had other slots.

    A literal `{{subject}}` in a prompt is not a cosmetic bug: the model is
    asked about the string "{{subject}}" and answers confidently, so the run
    looks like a measurement of the relation and is actually a measurement of
    nothing. Hence a WRITE-site refusal.
    """
    seen = set(SLOT_RE.findall(template))
    # `{{term:NAME}}` is a REAL slot (it resolves to a term INDEX), so it must be
    # excluded from the bare-brace check. MEASURED DEFECT this prevents: without
    # this line, adding the term slot would make `assert_template_slots` refuse
    # every template that uses it — the gate would reject the feature it exists
    # to protect.
    seen |= set(TERM_SLOT_RE.findall(template))
    bare = set(BARE_SLOT_RE.findall(template)) - seen
    if bare:
        raise DimensionError(
            "template contains brace group(s) %s that match neither "
            "{{dim:...}}, {{term:...}} nor {{case:...}}; they would be sent to "
            "the model verbatim. Fix or remove them. (where=%s, template=%r)"
            % (sorted(bare), where, template)
        )


# ---------------------------------------------------------------------------
# THE MEASURED UNIT — a prose row is proven by the units it is made of.
#
# WHY (the human, 2026-09-25):
#
#     "logic generation can help to have factor -> output = measured unit too,
#      散文 can be proofed by measured unit as that are combination of measured
#      unit / you need to design proof type by multi measured unit / single
#      measured unit, so you can have that"
#
# MEASURED BEFORE THIS: `wording_registry` holds 38 rows of PROSE and NO unit.
# Of those 38, only 6 name a factor field (`metric_kind`/`metric_unit`/
# `metric_target`) in their text; 32 name none. So "read the unit out of the
# prose" is NOT available — the evidence says so.
#
# THE UNIT IS THE `dim_key`, AND IT IS ALREADY THERE. MEASURED, the 38 rows
# carry 10 `dim_key` values (context 4, criterion 3, negation 5, output 4,
# relation 4, failure_class 5, scope_guard 4, example_pair 4, logic_layer_ask 4,
# logic_layer_role 1). A `dim_key` IS a measured unit: a named axis, already a
# column, and `combo_key` already composes them order-independently.
#
# THE VERDICT IS AND (the human's decision 甲). A prose row PASSES only when
# EVERY unit in its composition PASSES. This matches `factor_template`'s own
# rules, which are all `must_be_*` — every one is required.
#
# EVERY UNIT TEST IS A STRING TEST ON THE ROW ITSELF. No model call, no
# judgement, no new table. That is what makes the verdict checkable.
# ---------------------------------------------------------------------------

# A wording_key that DECLARES its template empty. MEASURED: `context.bare`,
# `negation.none` and `negation.neutral` are empty BY DESIGN — "no negation is
# stated" is a real value, not a gap. An empty template PASSES only when its
# wording_key is in this set, so an ACCIDENTAL empty is still a failure.
DECLARED_EMPTY = frozenset({"bare", "none", "neutral"})


def _unit_ok(dim_key: str, wording_key: str, template: str) -> tuple[bool, str]:
    """`(ok, reason)` for ONE wording row. A STRING TEST, never a model call.

    Each test is written against the MEASURED templates in the live register, so
    it cannot pass vacuously: `criterion` really does carry `PASS`/`FAIL`,
    `output` really does carry `Answer`/`Result`/`JSON`, `scope_guard` really
    does carry `ONLY`/`NOT`, `example_pair` really does carry `EXAMPLE`, and
    `logic_layer_ask` really does end with `?`.
    """
    t = str(template or "")
    s = t.strip()
    if not s:
        if str(wording_key) in DECLARED_EMPTY:
            return True, "declared empty (%s)" % wording_key
        return False, ("empty template and %r is not a declared-empty key"
                       % wording_key)
    if dim_key == "criterion":
        ok = ("PASS" in t or "FAIL" in t)
        return ok, ("names a PASS/FAIL condition" if ok
                    else "names no PASS/FAIL condition")
    if dim_key == "output":
        ok = ("Answer" in t or "Result" in t or "JSON" in t)
        return ok, ("names an answer format" if ok else "names no answer format")
    if dim_key == "relation":
        ok = bool(SLOT_RE.search(t) or TERM_SLOT_RE.search(t) or "{{case:" in t)
        return ok, ("has a real slot" if ok else "has no real slot")
    if dim_key == "scope_guard":
        ok = ("ONLY" in t or "NOT" in t)
        return ok, ("says what is NOT asked" if ok
                    else "does not say what is NOT asked")
    if dim_key == "example_pair":
        ok = "EXAMPLE" in t
        return ok, ("has a worked example" if ok else "has no worked example")
    if dim_key == "logic_layer_ask":
        ok = s.endswith("?")
        return ok, ("asks a question" if ok else "does not end with ?")
    # context / negation / failure_class / logic_layer_role: non-empty is the test
    return True, "non-empty"


def unit_verdict(conn: sqlite3.Connection, skill_id: int) -> dict[str, Any]:
    """Measure EVERY `dim_key` of a skill. `{ok, units, proof_type, ...}`.

    One entry per `dim_key`, each carrying its row count, its pass count and the
    reason for every row — so a FAILURE names the row that failed instead of
    reporting a bare number.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT wording_id, wording_key, dim_key, template, is_active "
        "FROM wording_registry WHERE skill_id = ? "
        "ORDER BY dim_key, sort_order, wording_key",
        (int(skill_id),))]
    units: dict[str, dict[str, Any]] = {}
    for r in rows:
        dim = str(r["dim_key"])
        ok, reason = _unit_ok(dim, r["wording_key"], r["template"])
        u = units.setdefault(dim, {"dim_key": dim, "rows": 0, "passed": 0,
                                   "failed": [], "reasons": []})
        u["rows"] += 1
        if ok:
            u["passed"] += 1
        else:
            u["failed"].append(r["wording_key"])
        u["reasons"].append("%s: %s" % (r["wording_key"], reason))
    for u in units.values():
        u["ok"] = (u["passed"] == u["rows"])
    return {
        "skill_id": int(skill_id),
        "units": units,
        "unit_count": len(units),
        "proof_type": proof_type_for(units),
        "ok": (all(u["ok"] for u in units.values()) if units else False),
        "rows": len(rows),
    }


def proof_type_for(units: dict[str, Any] | None) -> str:
    """DERIVED, never declared: `single` for ONE unit, `multi` for MORE.

    A `proof_type` a caller can ASSERT is a `proof_type` a caller can get wrong
    — the same reason `terminology_registry.definition_hash` is derived by the
    writer rather than accepted from the caller.

    `none` when there are no units at all, and that is NOT `single`: a row with
    no unit cannot be proven, and calling it `single` would let it pass.
    """
    n = len(units or {})
    return "single" if n == 1 else ("multi" if n > 1 else "none")


def prose_proof(conn: sqlite3.Connection, skill_id: int) -> dict[str, Any]:
    """The AND verdict (decision 甲): EVERY unit must PASS.

    A prose row with NO unit CANNOT pass — `proof_type == "none"` is a refusal,
    not a pass. That is the whole point: prose that names no unit is prose
    nothing can check.
    """
    v = unit_verdict(conn, skill_id)
    failed = sorted(d for d, u in v["units"].items() if not u["ok"])
    return {
        "skill_id": v["skill_id"],
        "proof_type": v["proof_type"],
        "unit_count": v["unit_count"],
        "units": v["units"],
        "failed_units": failed,
        "ok": bool(v["units"]) and not failed,
        "rule": "AND — every unit must PASS (decision 甲)",
    }


# ---------------------------------------------------------------------------
# THE QUESTION-TEMPLATE RECOMMENDATION (added 2026-09-25).
#
# WHY (the human): "when we need to find factor X -> system can recommend
# template for us / totally help 7B".
#
# MEASURED: `prompt_combo` IS the question-template table (36 rows, each with
# its `axes_json` = the multi measured units, its `template_text` = the prose,
# and its measured performance). `prompt_composition` IS the usage table (39
# rows). So this block ADDS ONLY the one link that was missing — MEASURED: zero
# tables had both a `factor_*` column and a template name.
# ---------------------------------------------------------------------------

def seed_unit_registry(conn: sqlite3.Connection) -> dict[str, Any]:
    """Fill `unit_registry` from the MEASURED unit values. Idempotent.

    The units are READ, never invented: `wording_registry.dim_key` (the prose
    dimensions) and `skill_factor_registry.metric_kind` (the metric kinds). A
    unit that is not in one of those columns is not a unit this system has.
    """
    seen: dict[str, tuple[str, str]] = {}
    for r in conn.execute("SELECT DISTINCT dim_key FROM wording_registry "
                          "WHERE dim_key IS NOT NULL AND dim_key <> ''"):
        seen[str(r["dim_key"])] = ("dimension", "wording_registry.dim_key")
    for r in conn.execute("SELECT DISTINCT metric_kind FROM skill_factor_registry "
                          "WHERE metric_kind IS NOT NULL AND metric_kind <> ''"):
        k = str(r["metric_kind"])
        seen.setdefault(k, ("metric", "skill_factor_registry.metric_kind"))
    added = 0
    for key, (kind, src) in sorted(seen.items()):
        cur = conn.execute(
            "INSERT OR IGNORE INTO unit_registry (unit_key, unit_kind, source_column) "
            "VALUES (?, ?, ?)", (key, kind, src))
        added += cur.rowcount
    conn.commit()
    return {"ok": True, "measured": len(seen), "added": added,
            "units": sorted(seen)}


def link_template_factor(conn: sqlite3.Connection, combo_id: int,
                         factor_id: int) -> dict[str, Any]:
    """Link a template to a factor, with `scoring` DERIVED from the template.

    `scoring` is NOT a parameter. It is read from `prompt_combo`'s own measured
    performance, so a caller cannot assert a score — the same reason
    `proof_type_for` derives `proof_type` instead of accepting it.

    REFUSES a combo or factor that does not exist: a link to nothing is a link
    that will never resolve, and it would look like a recommendation.
    """
    combo = conn.execute("SELECT id, balanced_accuracy_pct, accuracy_pct "
                         "FROM prompt_combo WHERE id = ?", (int(combo_id),)).fetchone()
    if not combo:
        return {"ok": False, "code": "NO_SUCH_COMBO", "combo_id": int(combo_id)}
    factor = conn.execute("SELECT factor_id, factor_key FROM skill_factor_registry "
                          "WHERE factor_id = ?", (int(factor_id),)).fetchone()
    if not factor:
        return {"ok": False, "code": "NO_SUCH_FACTOR", "factor_id": int(factor_id)}
    score = combo["balanced_accuracy_pct"]
    if score is None:
        score = combo["accuracy_pct"]
    conn.execute(
        "INSERT INTO question_template_factor (combo_id, factor_id, scoring) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (combo_id, factor_id) DO UPDATE SET "
        "scoring = excluded.scoring, updated_at = CURRENT_TIMESTAMP",
        (int(combo_id), int(factor_id), score))
    conn.commit()
    return {"ok": True, "combo_id": int(combo_id), "factor_id": int(factor_id),
            "scoring": score, "derived_from": "prompt_combo.balanced_accuracy_pct",
            "factor_key": factor["factor_key"]}


def recommend_template(conn: sqlite3.Connection, factor_id: int,
                       *, limit: int = 5) -> dict[str, Any]:
    """Given a FACTOR, the templates that measure it, best score first.

    REFUSES when the factor has no linked template. An empty list returned as a
    PASS would be a recommendation with no evidence — a guess wearing a
    recommendation's clothes. So `ok=False` and the reason names the factor.
    """
    factor = conn.execute("SELECT factor_id, factor_key, name FROM skill_factor_registry "
                          "WHERE factor_id = ?", (int(factor_id),)).fetchone()
    if not factor:
        return {"ok": False, "code": "NO_SUCH_FACTOR", "factor_id": int(factor_id),
                "templates": []}
    rows = [dict(r) for r in conn.execute(
        "SELECT q.combo_id, q.scoring, c.skill_key, c.combo_key, c.axes_json, "
        "c.template_text, c.status "
        "FROM question_template_factor q "
        "JOIN prompt_combo c ON c.id = q.combo_id "
        "WHERE q.factor_id = ? "
        "ORDER BY (q.scoring IS NULL), q.scoring DESC, q.combo_id "
        "LIMIT ?", (int(factor_id), int(limit)))]
    if not rows:
        return {"ok": False, "code": "NO_LINKED_TEMPLATE",
                "factor_id": int(factor_id), "factor_key": factor["factor_key"],
                "templates": [],
                "message": ("factor %s has no linked template — link one with "
                            "link_template_factor() before recommending"
                            % factor["factor_key"])}
    for r in rows:
        try:
            axes = json.loads(r["axes_json"] or "{}")
        except (TypeError, ValueError):
            axes = {}
        r["units"] = sorted(axes)
        r["unit_count"] = len(axes)
        r["proof_type"] = proof_type_for({u: {} for u in axes})
    return {"ok": True, "factor_id": int(factor_id),
            "factor_key": factor["factor_key"], "factor_name": factor["name"],
            "templates": rows, "count": len(rows),
            "ordered_by": "scoring DESC (derived from prompt_combo)"}


def registry(
    conn: sqlite3.Connection, skill_key: str, *, include_inactive: bool = False
) -> dict[str, dict[str, Any]]:
    """{dim_key: {dim_key, dim_name, sort_order, values: {value_key: {...}}}}."""
    sql = (
        "SELECT w.* FROM wording_registry w "
        "JOIN component_registry k ON k.skill_id = w.skill_id "
        "WHERE k.skill_key = ? "
        + ("" if include_inactive else "AND w.is_active = 1 ")
        + "ORDER BY w.sort_order, w.dim_key, w.wording_key"
    )
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(sql, (skill_key,)):
        d = out.setdefault(
            r["dim_key"],
            {
                "dim_key": r["dim_key"],
                "dim_name": r["dim_key"],
                "sort_order": r["sort_order"],
                "values": {},
            },
        )
        d["values"][r["wording_key"]] = {
            "value_key": r["wording_key"],
            "value_text": r["template"],
            "description": r["description"],
            "sort_order": r["sort_order"],
            "is_active": bool(r["is_active"]),
        }
    # drop dimensions with no values at all
    return {k: v for k, v in out.items() if v["values"]}


def dimensions(conn: sqlite3.Connection, skill_key: str) -> list[str]:
    return list(registry(conn, skill_key).keys())


def combo_key(values: dict[str, str]) -> str:
    """Canonical, order-independent key for a combination."""
    return "|".join("%s=%s" % (k, values[k]) for k in sorted(values))


def combo_key_short(values: dict[str, str]) -> str:
    """Filesystem/URL-safe short key (used as prompt_key)."""
    return "c" + hashlib.sha1(combo_key(values).encode("utf-8")).hexdigest()[:10]


def expand_combos(
    conn: sqlite3.Connection,
    skill_key: str,
    *,
    axes: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Cartesian product of the registry (or of an explicit `axes` filter)."""
    reg = registry(conn, skill_key)
    if not reg:
        raise DimensionError("no dimensions registered for skill %r" % skill_key)
    chosen: list[tuple[str, list[str]]] = []
    for dim_key in sorted(reg):
        if axes is not None and dim_key in axes:
            wanted = list(axes[dim_key])
            known = reg[dim_key]["values"]
            bad = [w for w in wanted if w not in known]
            if bad:
                raise DimensionError(
                    "dimension %r has no value(s) %s; known=%s"
                    % (dim_key, bad, sorted(known))
                )
            vals = wanted
        else:
            vals = sorted(reg[dim_key]["values"])
        if not vals:
            raise DimensionError("dimension %r has no active values" % dim_key)
        chosen.append((dim_key, vals))

    combos: list[dict[str, str]] = [{}]
    for dim_key, vals in chosen:
        combos = [dict(c, **{dim_key: v}) for c in combos for v in vals]
    return combos


def compose_prompt(
    conn: sqlite3.Connection,
    skill_key: str,
    values: dict[str, str],
    *,
    template: str,
    strict: bool = True,
) -> str:
    """Substitute {{dim:X}} slots with the selected values.

    `strict=True` REFUSES a dimension/value that is not registered, a slot with
    no supplied value, and a leftover slot in the output — every one of those
    would otherwise reach the model as literal text.
    """
    reg = registry(conn, skill_key)
    slots = set(SLOT_RE.findall(template))
    term_slots = set(TERM_SLOT_RE.findall(template))

    # A template with ONLY `{{term:...}}` slots is still composable — the term
    # slot is a real slot. MEASURED DEFECT this prevents: the original check
    # looked at `{{dim:...}}` alone, so a term-only template would be refused as
    # "nothing to compose" while having something to compose.
    if strict and not slots and not term_slots:
        raise DimensionError(
            "template has no {{dim:...}} or {{term:...}} slots; nothing to "
            "compose. A template without slots cannot be varied, so composing it "
            "would silently return the same prompt for every combination."
        )

    missing = sorted(slots - set(values))
    if strict and missing:
        raise DimensionError(
            "template needs dimension(s) %s but no value was supplied" % missing
        )
    unknown = sorted(set(values) - set(reg))
    if strict and unknown:
        raise DimensionError(
            "unknown dimension(s) %s; registered=%s" % (unknown, sorted(reg))
        )

    mapping: dict[str, str] = {}
    for dim_key in sorted(slots | set(values)):
        if dim_key not in values:
            continue
        val_key = values[dim_key]
        if dim_key not in reg:
            mapping[dim_key] = ""
            continue
        vals = reg[dim_key]["values"]
        if val_key not in vals:
            if strict:
                raise DimensionError(
                    "dimension %r has no value %r; known=%s"
                    % (dim_key, val_key, sorted(vals))
                )
            mapping[dim_key] = ""
            continue
        mapping[dim_key] = vals[val_key]["value_text"]

    out = template
    for dim_key, text in mapping.items():
        out = out.replace("{{dim:%s}}" % dim_key, text)

    # ---- the TERM slots: substituted with the INDEX, not the term text -----
    # This is the whole point of the feature. A prompt that substituted the term's
    # DEFINITION would give the model prose to agree with; substituting the INDEX
    # makes the model's answer a NUMBER, which is checkable. The term list itself
    # is supplied separately (see `render_term_index`), so the model has the
    # mapping and the answer is a lookup rather than a recall.
    term_slots = TERM_SLOT_RE.findall(out)
    if term_slots:
        idx = terminology_index(conn)
        if not idx["ok"]:
            raise DimensionError(
                "template uses {{term:...}} but the register is unreadable: %s"
                % idx["message"])
        by_key = {t["term_key"]: t for t in idx["terms"]}
        for name in sorted(set(term_slots)):
            if name not in by_key:
                raise DimensionError(
                    "template names term %r which is NOT in "
                    "terminology_registry — a name that was never registered is "
                    "an invented word. Register it first." % name)
            out = out.replace("{{term:%s}}" % name,
                              str(by_key[name]["index"]))

    leftover = SLOT_RE.findall(out)
    if strict and leftover:
        raise DimensionError(
            "unsubstituted slot(s) %s would be sent to the model verbatim" % leftover
        )
    leftover_term = TERM_SLOT_RE.findall(out)
    if strict and leftover_term:
        raise DimensionError(
            "unsubstituted term slot(s) %s would be sent to the model verbatim"
            % leftover_term
        )
    return out


def compose_combo(
    conn: sqlite3.Connection,
    skill_key: str,
    values: dict[str, str],
    *,
    template: str,
    strict: bool = True,
) -> dict[str, Any]:
    """compose + the identity of the result, in one call."""
    text = compose_prompt(conn, skill_key, values, template=template, strict=strict)
    return {
        "combo_key": combo_key(values),
        "prompt_key": combo_key_short(values),
        "axes": dict(sorted(values.items())),
        "prompt_text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def record_combo(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    combo: dict[str, Any],
    status: str = "candidate",
    commit: bool = True,
) -> dict[str, Any]:
    """Record a combo in `prompt_combo` (unchanged table — it is a RESULT log)."""
    conn.execute(
        """INSERT INTO prompt_combo
           (skill_key, combo_key, prompt_key, axes_json, template_text, status)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(skill_key, combo_key) DO UPDATE SET
             prompt_key=excluded.prompt_key,
             axes_json=excluded.axes_json,
             template_text=excluded.template_text,
             updated_at=CURRENT_TIMESTAMP""",
        (
            skill_key,
            combo["combo_key"],
            combo["prompt_key"],
            json.dumps(combo["axes"], ensure_ascii=False),
            combo["prompt_text"],
            status,
        ),
    )
    if commit:
        conn.commit()
    return {"ok": True, "combo_key": combo["combo_key"], "prompt_key": combo["prompt_key"]}


# The mouse_spot template, kept here so callers stop importing prompt_dimension.
MOUSE_SPOT_TEMPLATE = """You are a visual inspector for Mouse Spot Helper.

{{dim:context}}

Target icon: {{target_name}}

{{dim:criterion}}

{{dim:negation}}

{{dim:output}}
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create the register tables (the old module's ensure_tables equivalent)."""
    # 🔴 THREE OF THESE NAMES DID NOT EXIST. MEASURED 2026-09-29: the real names
    # are `COMPONENT_registry_DDL`, `PROMPT_registry_DDL` and
    # `SKILL_registry_DDL` (`db_schema.py`). The lowercase spellings appear
    # NOWHERE in `db_schema.py`, so this function raised `ImportError` on EVERY
    # call — including the `component_registry` fix the comment below records,
    # which therefore never took effect.
    from db_schema import (
        COMPONENT_registry_DDL,
        PROMPT_COMBO_DDL,
        PROMPT_registry_DDL,
        PROMPT_WORDING_DDL,
        SKILL_registry_DDL,
        STUDY_registry_DDL,
        WORDING_registry_DDL,
        WORKFLOW_registry_DDL,
        WORKFLOW_STEP_DDL,
    )

    # MEASURED DEFECT (2026-09-21): `component_registry` was MISSING from this
    # list, yet `upsert_dimension` INSERTs into it and `wording_registry.skill_id`
    # is a FK to it. On an EMPTY database — a fresh proof, a new install — the
    # write path therefore crashed with "no such table: component_registry". It
    # worked on `agent.db` only because the table happened to already exist, so
    # the gap was invisible until a proof used a clean DB.
    #
    # ORDER MATTERS: the parent must exist before the child that FKs to it.
    for ddl in (
        COMPONENT_registry_DDL,
        SKILL_registry_DDL,
        STUDY_registry_DDL,
        WORDING_registry_DDL,
        PROMPT_registry_DDL,
        PROMPT_WORDING_DDL,
        WORKFLOW_registry_DDL,
        WORKFLOW_STEP_DDL,
        PROMPT_COMBO_DDL,
    ):
        conn.executescript(ddl)


def upsert_dimension(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    dim_key: str,
    value_key: str,
    value_text: str,
    dim_name: str | None = None,
    description: str | None = None,
    sort_order: int = 0,
    commit: bool = True,
) -> int:
    """Upsert one wording value (the old module's upsert_dimension equivalent).

    Creates the parent skill if it does not exist, so a caller can register a
    dimension for a brand-new skill in one call.

    Refuses a template with unresolvable brace groups at THIS point — the write
    site — because the composer's strict mode cannot see a slot whose syntax it
    does not recognise. See `assert_template_slots`.
    """
    assert_template_slots(value_text, where="%s.%s=%s" % (skill_key, dim_key, value_key))
    ensure_tables(conn)
    row = conn.execute(
        "SELECT skill_id FROM component_registry WHERE skill_key = ?", (skill_key,)
    ).fetchone()
    if row:
        skill_id = int(row["skill_id"])
    else:
        cur = conn.execute(
            "INSERT INTO component_registry (skill_key, name) VALUES (?, ?)",
            (skill_key, skill_key),
        )
        skill_id = int(cur.lastrowid)

    existing = conn.execute(
        "SELECT wording_id FROM wording_registry "
        "WHERE skill_id = ? AND dim_key = ? AND wording_key = ?",
        (skill_id, dim_key, value_key),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE wording_registry SET template = ?, name = COALESCE(?, name), "
            "description = COALESCE(?, description), sort_order = ?, "
            "updated_at = datetime('now') WHERE wording_id = ?",
            (value_text, dim_name, description, int(sort_order), int(existing["wording_id"])),
        )
        wid = int(existing["wording_id"])
    else:
        cur = conn.execute(
            "INSERT INTO wording_registry "
            "(wording_key, name, description, skill_id, dim_key, template, sort_order) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (value_key, dim_name or value_key, description, skill_id, dim_key,
             value_text, int(sort_order)),
        )
        wid = int(cur.lastrowid)
    if commit:
        conn.commit()
    return wid


def seed_mouse_spot(
    conn: sqlite3.Connection,
    skill_key: str = "mouse_spot_verify",
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Seed the mouse_spot wording values into `wording_registry`.

    Idempotent: an existing (skill, dim, value) is updated, not duplicated.
    """
    ensure_tables(conn)
    row = conn.execute(
        "SELECT skill_id FROM component_registry WHERE skill_key = ?", (skill_key,)
    ).fetchone()
    if not row:
        raise DimensionError(
            "skill %r is not registered; create it before seeding wording" % skill_key
        )
    skill_id = int(row["skill_id"])

    n = 0
    for dim_key, values in MOUSE_SPOT_VALUES.items():
        for order, (value_key, spec) in enumerate(values.items()):
            existing = conn.execute(
                "SELECT wording_id FROM wording_registry "
                "WHERE skill_id = ? AND dim_key = ? AND wording_key = ?",
                (skill_id, dim_key, value_key),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE wording_registry SET template = ?, description = ?, "
                    "sort_order = ?, updated_at = datetime('now') WHERE wording_id = ?",
                    (spec["text"], spec["desc"], order, int(existing["wording_id"])),
                )
            else:
                conn.execute(
                    "INSERT INTO wording_registry "
                    "(wording_key, name, description, skill_id, dim_key, template, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (value_key, spec["name"], spec["desc"], skill_id, dim_key,
                     spec["text"], order),
                )
            n += 1
    if commit:
        conn.commit()
    return {"ok": True, "skill_key": skill_key, "values": n}


MOUSE_SPOT_VALUES: dict[str, dict[str, dict[str, str]]] = {
    "context": {
        "plain": {
            "name": "context",
            "text": ("You are shown a screenshot with a red crosshair (+). The "
                     "crosshair marks where the mouse was captured."),
            "desc": "Describe the image literally, no role framing.",
        },
        "task_framed": {
            "name": "context",
            "text": ("Your job is to decide whether a mouse capture landed on "
                     "the target it was aiming for."),
            "desc": "Frame it as a decision the model is responsible for.",
        },
    },
    "criterion": {
        "positive_only": {
            "name": "criterion",
            "text": ("PASS (answer YES) when the red crosshair sits inside the "
                     "target icon's own picture. FAIL (answer NO) when the "
                     "crosshair is on the screen but not inside that picture."),
            "desc": "State the positive and the negative in one sentence.",
        },
        "strict_boundary": {
            "name": "criterion",
            "text": ("PASS condition ONLY: the red crosshair must lie within the "
                     "physical pixel boundary of the target icon itself."),
            "desc": "Boundary-only wording; no negative case stated.",
        },
        "presence_gate": {
            "name": "criterion",
            "text": ("First confirm the picker is on screen. If it is not, report "
                     "root_cause=picker_not_open and answer UNKNOWN instead of "
                     "judging position. If it is on screen, PASS (YES) when the "
                     "crosshair sits inside the target icon's own picture."),
            "desc": "Gate on presence before judging position.",
        },
    },
    "negation": {
        "none": {"name": "negation", "text": "", "desc": "No negative rules."},
        "single": {
            "name": "negation",
            "text": "Being merely near the icon is not a hit.",
            "desc": "One short negative rule.",
        },
        "stack": {
            "name": "negation",
            "text": ("Hard rules: being close, pointing toward, or inside the "
                     "large blue preview circle does NOT count as PASS. If the "
                     "crosshair lands on any other icon, even an adjacent one, "
                     "return FAIL. Proximity is never accepted. Ignore the blue "
                     "circle entirely; it is NOT a detection boundary."),
            "desc": "Stacked negative rules.",
        },
    },
    "output": {
        "simple": {
            "name": "output",
            "text": ("Answer in this exact format:\nResult: [YES / NO]\n"
                     "Reason: one short sentence naming what the crosshair is on."),
            "desc": "Two-way output.",
        },
        "with_unknown": {
            "name": "output",
            "text": ("Answer in this exact format:\nResult: [YES / NO / UNKNOWN]\n"
                     "Reason: one short sentence naming what the crosshair is on."),
            "desc": "Three-way output including UNKNOWN.",
        },
    },
}


def seed_logic_layers(
    conn: sqlite3.Connection,
    skill_key: str,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Register the LOGIC-LAYER axis — the dimension the generator was missing.

    THE SURPRISE IN THE TABLE (measured 2026-09-21)
    -----------------------------------------------
    `wording_registry` already implements "table format with multi setting":
    one `dim_key` with many `wording_key` values, each a `template` + a
    `description` + a `sort_order`. That mechanism is fine. The SETTINGS are the
    problem — read them:

        context / criterion / negation / output / failure_class / relation

    Every one describes the SHAPE of an answer: how to frame the image, how to
    phrase the criterion, one word vs JSON, which failure class. NOT ONE names
    WHICH QUESTION is being asked. So a judge composed from these settings has no
    way to say "the target is not part of this question", and a defect in one
    consideration silently smears into another.

    That is not a theory. It is what produced the wrong number I reported:
    with a hand-written prompt whose L2 asked about kind+unit+target together,
    the model scored L2 42.9%; splitting the question by layer and adding the
    scope guard took it to 85.7% on the SAME model. The 7B was never the defect
    — the missing axis was.

    So this adds the axis as DATA, through `upsert_dimension`, which enforces the
    slot syntax at the write site. Layers are rows; a new layer is a row, not a
    code change.

    The three settings per layer are the three things a scoped question needs:
      `logic_layer_ask`  what THIS layer asks (positive framing, no "STRICT")
      `scope_guard`      what this layer must NOT consider
      `example_pair`     one YES and one NO, so the label is not one-shot
    """
    n = 0
    for layer_key, spec in LOGIC_LAYERS.items():
        for dim_key, text, desc in (
            ("logic_layer_ask", spec["ask"], "what this layer asks"),
            ("scope_guard", spec["guard"], "what this layer must not consider"),
            ("example_pair", spec["examples"], "one YES and one NO example"),
        ):
            upsert_dimension(
                conn,
                skill_key=skill_key,
                dim_key=dim_key,
                value_key=layer_key,
                value_text=text,
                description=desc,
                sort_order=spec["sort_order"],
                commit=False,
            )
            n += 1
    # The shared frame: a judge instruction belongs in the SYSTEM turn, not
    # buried in the user turn where it reads as content to agree with.
    upsert_dimension(
        conn,
        skill_key=skill_key,
        dim_key="logic_layer_role",
        value_key="scoped_judge",
        value_text=(
            "You answer ONE yes/no question about a factor definition. You judge "
            "only the question asked and ignore every other aspect of the factor."
        ),
        description="the system-turn frame for a scoped judge",
        sort_order=0,
        commit=False,
    )
    if commit:
        conn.commit()
    return {"ok": True, "skill_key": skill_key, "values_upserted": n,
            "layers": sorted(LOGIC_LAYERS)}


# A layer TEMPLATE. The scope guard sits between the question and the payload on
# purpose: a model reads the last instruction before the data as the live one.
#
# MEASURED (2026-09-21), and this is the honest state of the generator:
#
#   my defective hand-written prompt ............ all-3  28.6%
#   this generator (layer + scope guard) ........ all-3  71.4%
#   the hand-fixed prompt (v2) .................. all-3  85.7%
#
# So the axis WORKS — expressing the layer and its scope guard took the same 7B
# from 28.6% to 71.4%. It is still 14 points short of the hand-fixed prompt, and
# the gap is identifiable: v2 states L1's requirement as a BULLETED CHECKLIST of
# three conditions, while this template states it as one prose sentence.
#
# Two attempts to close that gap by rewording the template FAILED (71.4% -> 42.9%
# with a labelled "SCOPE" heading). Per the debugging rule, rewording is not the
# fix: the ASK needs to be a STRUCTURED field (a list of conditions), not a
# sentence. That is the next increment, and it is recorded rather than guessed at.
LOGIC_LAYER_TEMPLATE = """QUESTION: {{dim:logic_layer_ask}}
{{dim:scope_guard}}

{{dim:example_pair}}

TERMS (answer with the INDEX in brackets, never the name):
{term_index}

FACTOR:
{payload}

Reply with exactly one word: YES or NO."""


def registered_layers(conn: sqlite3.Connection, skill_key: str) -> list[str]:
    """The logic layers AS ROWS — the register is the source, not the dict.

    WHY THIS EXISTS (QC-10): `LOGIC_LAYERS` is a Python dict, so adding a layer
    was a CODE CHANGE. The user's rule is that an OPEN set is a TABLE. This reads
    the layers from `wording_registry` (the `logic_layer_ask` dimension), so a
    layer added by an INSERT appears in the output with no code change.

    `LOGIC_LAYERS` keeps its role as the SEED — `seed_logic_layers` writes it —
    and stops being the source. The two are reconciled by `sort_order`, so a
    seeded layer and a hand-inserted one order together.
    """
    reg = registry(conn, skill_key)
    dim = reg.get("logic_layer_ask", {}).get("values", {})
    if not dim:
        return []
    return sorted(dim, key=lambda k: (dim[k]["sort_order"], k))


def compose_layer_prompts(
    conn: sqlite3.Connection,
    skill_key: str,
    *,
    layers: list[str] | None = None,
    payload: str = "{payload}",
    with_terms: bool = True,
) -> dict[str, Any]:
    """Emit one SCOPED prompt per logic layer, from the register.

    This is the generator inside the prompt generator: the layers are table rows,
    so adding a layer is `register`, not a code edit. Each result carries its
    `combo_key` and `sha256`, so two prompts can be compared as artefacts instead
    of as text someone remembers writing.

    `with_terms=True` injects the INDEXED terminology list (the user's request:
    "all by index, be easy for 7B"). The list is the register's own order, so the
    model looks a name up rather than recalling it — and its answer is a number,
    which `resolve_term_index` can check.
    """
    wanted = layers or registered_layers(conn, skill_key) \
        or sorted(LOGIC_LAYERS, key=lambda k: LOGIC_LAYERS[k]["sort_order"])
    reg = registry(conn, skill_key)
    need = {"logic_layer_ask", "scope_guard", "example_pair"}
    missing = sorted(need - set(reg))
    if missing:
        raise DimensionError(
            "skill %r has no logic-layer dimension(s) %s. Run seed_logic_layers() "
            "first — a layer prompt composed without its scope guard is the "
            "defect this axis exists to remove." % (skill_key, missing)
        )
    role = reg.get("logic_layer_role", {}).get("values", {})
    system = (role.get("scoped_judge", {}).get("value_text") or "")
    term_index = render_term_index(conn) if with_terms else ""
    out: dict[str, Any] = {"skill_key": skill_key, "system": system,
                           "term_index": term_index, "prompts": {}}
    for layer_key in wanted:
        # A layer is a ROW, so the guard is "is it registered", not "is it in the
        # dict". MEASURED DEFECT this fixes: the original check was
        # `layer_key not in LOGIC_LAYERS`, so a layer added as a ROW was refused
        # by the very function that should compose it.
        if layer_key not in reg.get("logic_layer_ask", {}).get("values", {}):
            raise DimensionError(
                "unknown logic layer %r; registered=%s"
                % (layer_key, registered_layers(conn, skill_key))
            )
        values = {"logic_layer_ask": layer_key, "scope_guard": layer_key,
                  "example_pair": layer_key}
        text = compose_prompt(conn, skill_key, values,
                              template=LOGIC_LAYER_TEMPLATE)
        out["prompts"][layer_key] = {
            "prompt_text": (text.replace("{term_index}", term_index)
                                .replace("{payload}", payload)),
            "combo_key": combo_key(values),
            "prompt_key": combo_key_short(values),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="compose prompts from the registers")
    ap.add_argument("--db", default=None)
    ap.add_argument("--skill", default=None)
    ap.add_argument("--study", default=None)
    ap.add_argument("--dims", default=None, help='JSON, e.g. {"output":"with_unknown"}')
    ap.add_argument("--enumerate", action="store_true", help="list every combination")
    ap.add_argument("--generate", action="store_true", help="compose every combination")
    ap.add_argument("--persist", action="store_true", help="store generated variants")
    ap.add_argument("--read-back", default=None, help="re-compose a stored prompt_key")
    args = ap.parse_args(argv)

    if args.read_back:
        print(json.dumps(get_prompt_composition(args.read_back, db_path=args.db), indent=2))
        return 0
    if args.enumerate:
        combos = enumerate_combinations(args.skill, db_path=args.db)
        print(json.dumps({"count": len(combos), "combinations": combos}, indent=2))
        return 0
    if args.generate:
        out = generate_variants(
            args.skill, args.study, persist=args.persist, db_path=args.db
        )
        print(json.dumps({"count": len(out), "variants": out}, indent=2))
        return 0
    if args.skill and args.study:
        dims = json.loads(args.dims) if args.dims else {}
        print(json.dumps(compose(args.skill, args.study, dims, db_path=args.db), indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
