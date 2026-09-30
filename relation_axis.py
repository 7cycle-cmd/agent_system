# -*- coding: utf-8 -*-
"""relation_axis.py — register the `relation` dimension (S1 of the prompt plan).

WHY THIS AXIS EXISTS (measured)
-------------------------------
The live dimension SSOT is `wording_registry` (NOT `prompt_dimension`, which is
retired). Measured on 2026-09-21:

    wording_registry  10 rows, 4 dim_keys, ALL under skill `mouse_spot_verify`
        context / criterion / negation / output
    prompt_registry   38 rows   (the COMPOSE-ALL path is live)
    prompt_composition 0 rows   (the STEP path has no caller)

The four axes describe a QUESTION'S SHAPE. None of them names the RELATION being
asked about, so a "does A serve B?" question cannot be expressed as a
composition — which is why the earlier attempt at it was a hand-typed string with
no `combo_key`, and why two runs of it could not be compared.

THE CORRECTION THIS ENCODES
---------------------------
I previously reported "prompt_dimension has only 4 dimensions and no relation
axis" as evidence that the mechanism could not express the question. The table I
read was RETIRED. The conclusion survived (no relation axis exists anywhere), but
the table it came from did not — so this module writes to the LIVE register and
asserts the axis is visible through the same `registry()` the composer reads.

Values: serve / unrelated / child_of / alias_of.
  * `serve` and `unrelated` are the yes/no pair already measured.
  * `child_of` and `alias_of` are the two RELATION TYPES the ontology work
    actually needs (a route child of a capability; a table alias for a table),
    so the axis covers the questions the system asks, not just the one probe.

Idempotent: re-running updates the same values rather than duplicating them.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# A DEDICATED skill_key, deliberately NOT `mouse_spot_helper`.
# Reason: scoping this axis to the mouse-spot skill would make it unreachable for
# task_center, and the relation question is an ONTOLOGY question, not a
# mouse-spot one. The composer scopes `wording_registry` by skill, so a shared
# key is what lets more than one module compose the same question shape.
SKILL_KEY = "ontology_relation"

VALUES: tuple[tuple[str, str, str, str], ...] = (
    # (dim_key, value_key, template, description)
    #
    # RETRACTION (2026-09-21): these four were first written with
    # `{{subject}}` / `{{object}}`, which match NEITHER `{{dim:x}}` nor
    # `{{case:field}}`. Nothing refused them at the time, so all four would have
    # been sent to the model with the braces intact and the run would have
    # looked like a relation measurement while measuring nothing. They now use
    # the real case-slot syntax, and `upsert_dimension` refuses bare braces.
    ("relation", "serve",
     "Does {{case:subject}} SERVE {{case:object}}?",
     "subject provides the capability named by object"),
    ("relation", "unrelated",
     "Is {{case:subject}} UNRELATED to {{case:object}}?",
     "the negation form of `serve`, asked as its own value so the two can be "
     "compared as a recorded axis rather than a hand-edited string"),
    ("relation", "child_of",
     "Is {{case:subject}} a CHILD construct of {{case:object}}?",
     "structural containment: a route belongs to a capability, a field to a table"),
    ("relation", "alias_of",
     "Is {{case:subject}} another NAME for {{case:object}}?",
     "naming equivalence: two keys that denote the same entity"),
    # A `context` axis exists so the ABILITY-vs-KNOWLEDGE question can be asked
    # as a controlled composition of the SAME relation question. Without it, a
    # wrong answer is uninterpretable: it could be "cannot judge relations" or
    # "never heard of this module", and no measurement separates them.
    ("context", "bare", "",
     "no glossary; the model sees only the two names"),
    ("context", "glossary",
     "Here are the known modules: {{case:glossary}}",
     "the same question with the candidate set and each module's role spelled "
     "out, so a wrong answer can only be a relation JUDGEMENT"),
)

# The output/negation axes are REUSED from the existing register rather than
# reinvented: an answer is still a verdict, so the shape must not fork.
SHARED_VALUES: tuple[tuple[str, str, str, str], ...] = (
    ("output", "yes_no", "Answer with exactly one word: YES or NO.",
     "a strict one-token answer, so a parse failure is VISIBLE"),
    ("output", "verdict_json", 'Answer with JSON only, shaped {"verdict":"YES|NO"}.',
     "a structured answer for callers that need a field, not a token"),
    ("negation", "neutral", "", "ask the relation as stated"),
    ("negation", "inverted", "Note: if the answer is YES, reply NO.",
     "the controlled variant used to separate PHRASING from ABILITY"),
)


def ensure_axis(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """Register the relation axis + the shared output/negation values."""
    import prompt_generator as pg

    pg.ensure_tables(conn)
    added: list[str] = []
    updated: list[str] = []
    for dim_key, value_key, template, desc in VALUES + SHARED_VALUES:
        # Verify BEFORE writing. The writer also refuses, but this reports which
        # value is bad instead of raising on the first one.
        pg.assert_template_slots(
            template, where="%s.%s=%s" % (SKILL_KEY, dim_key, value_key))
        before = conn.execute(
            "SELECT wording_id FROM wording_registry w "
            "JOIN component_registry k ON k.skill_id = w.skill_id "
            "WHERE k.skill_key = ? AND w.dim_key = ? AND w.wording_key = ?",
            (SKILL_KEY, dim_key, value_key)).fetchone()
        pg.upsert_dimension(
            conn, skill_key=SKILL_KEY, dim_key=dim_key, value_key=value_key,
            value_text=template, dim_name=dim_key, description=desc,
            commit=False,
        )
        (updated if before else added).append("%s=%s" % (dim_key, value_key))
    if commit:
        conn.commit()
    return {"ok": True, "skill_key": SKILL_KEY,
            "added": added, "updated": updated,
            "added_count": len(added), "updated_count": len(updated)}


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
    """Confirm the axis is visible to the SAME reader the composer uses."""
    import prompt_generator as pg

    reg = pg.registry(conn, SKILL_KEY)
    dims = sorted(reg.keys())
    rel = sorted(reg.get("relation", {}).get("values", {}))

    # Verify each stored template's slots against the SAME regexes the loaders
    # use. A template that passed the write gate can still be failing to compose
    # if the case allowlist changed, so re-check rather than trust.
    bare: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT w.dim_key, w.wording_key, w.template FROM wording_registry w "
        "JOIN component_registry k ON k.skill_id = w.skill_id "
        "WHERE k.skill_key = ?", (SKILL_KEY,)
    ):
        bad = (set(pg.BARE_SLOT_RE.findall(r["template"]))
               - set(pg.SLOT_RE.findall(r["template"])))
        if bad:
            bare["%s=%s" % (r["dim_key"], r["wording_key"])] = sorted(bad)

    return {
        "ok": True,
        "dimensions_visible_to_composer": dims,
        "relation_values": rel,
        "has_relation_axis": "relation" in reg,
        "has_both_relation_forms": {"serve", "unrelated"} <= set(rel),
        "has_negation_axis": "negation" in reg,
        "has_output_axis": "output" in reg,
        "templates_with_unresolvable_braces": bare,
        "all_templates_composable": not bare,
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            print(json.dumps(ensure_axis(conn), indent=2, ensure_ascii=False))
        print(json.dumps(verify(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()