# -*- coding: utf-8 -*-
"""skill_key_bridge.py — a DECISION LIST, not a bridge.

THE MEASUREMENT THAT FORCED THIS SHAPE
--------------------------------------
`skill_contract_template.contract_id` is an ENTITY ID: `SKILL.INDEPENDENT.REVIEW`,
`CAP.PPT.PRODUCE`, `SKILL-0001`. The authoritative `skill_prompt_ssot.skill_key`
is a BARE SLUG: `independent_review`, `ppt_produce`.

I first asked whether the bridge is MECHANICAL (uppercase-dots -> snake_case) or
HAND-NAMED. Measured: it is NEITHER, it is a third thing.

    contract_id -> snake_case resolves to a real skill :  5 of 18
    and of those 5, the resolution used the FULL name including the entity
    prefix, which is not the authoritative spelling

The decisive row: `SKILL.CONDITION.WAITING` maps mechanically to
`skill_condition_waiting`, but the real skill is `condition_based_waiting`. The
NAME ITSELF differs. So 13 of 18 are not a formatting difference at all — they
are different names, which means the mapping is a CONTENT decision.

WHAT THIS MODULE THEREFORE DOES
-------------------------------
It produces a DECISION LIST and refuses to make it. For each contract it shows
the candidates that the rule can justify, and:
    1 candidate   -> PROPOSED      (confirmable)
    >1 candidate  -> AMBIGUOUS     (the rule did not decide)
    0 candidates  -> NO_CANDIDATE  (no rule reaches it)
Only PROPOSED rows may ever be auto-linked, and only after a human sees them.

THE DISCRIMINATOR IS CANDIDATE COUNT, NEVER A SIMILARITY SCORE
--------------------------------------------------------------
`independent_review` forbids overlap scores. So matching uses
`binding_proposals.key_tokens`, the SAME informative-token rule already proven in
this repo, and the verdict comes from HOW MANY candidates matched. `fuzzy` /
`ratio` / `SequenceMatcher` are deliberately absent and their absence is asserted.

WHY NOT JUST WRITE THE 13 BY HAND
---------------------------------
`CAP.PPT.PRODUCE -> ppt_produce` looks obvious. `SKILL.CONDITION.WAITING ->
condition_based_waiting` proves the pattern is NOT reliable. Filling a mapping by
pattern would point a contract at the WRONG skill, and the report would then show
"connected" — the defect this project keeps removing, in a new place.

Dry-run by default. `--apply` writes ONLY the PROPOSED rows, into an append-only
alias table, never deleting the original key.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

AUTH_TABLE = "skill_prompt_ssot"
AUTH_COL = "skill_key"

# The alias table, in the shape `migrate_legacy_ids.legacy_id_map` established:
# append-only, a RECORD and not a second SSOT.
ALIAS_DDL = """
CREATE TABLE IF NOT EXISTS skill_key_alias (
    alias_key   TEXT NOT NULL,
    skill_key   TEXT NOT NULL,
    source      TEXT NOT NULL,          -- which table the alias came from
    cite_ref    TEXT NOT NULL,          -- a checkable citation for the mapping
    decided_by  TEXT NOT NULL,          -- who/what decided (never 'pattern')
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (alias_key, source)
);
CREATE INDEX IF NOT EXISTS idx_skill_key_alias_skill
    ON skill_key_alias (skill_key);
"""

_RX_ENTITY = re.compile(r"^([A-Z]+)(?:[-.])(.*)$")


class BridgeError(ValueError):
    """Raised when a mapping would be decided by pattern rather than by evidence."""


def authoritative(conn: sqlite3.Connection) -> list[str]:
    return sorted({str(r[0]) for r in conn.execute(
        "SELECT DISTINCT %s FROM %s" % (AUTH_COL, AUTH_TABLE)) if r[0]})


def entity_parts(contract_id: str) -> dict[str, str]:
    """Split `SKILL.INDEPENDENT.REVIEW` into its entity letter and its name.

    `SKILL-0001` has no readable name, so the name is empty and the caller must
    treat it as UNRESOLVED rather than inventing one.
    """
    s = str(contract_id or "").strip()
    m = _RX_ENTITY.match(s)
    if not m:
        return {"letter": "", "name": s}
    letter, rest = m.group(1), m.group(2)
    name = "" if re.fullmatch(r"\d+", rest) else rest
    return {"letter": letter, "name": name}


def candidates(contract_id: str, auth: list[str]) -> list[dict[str, Any]]:
    """Candidates justified by the informative-token rule. Never a score.

    DEFECT MEASURED IN THE FIRST VERSION OF THIS FUNCTION
    ----------------------------------------------------
    It counted EVERY shared token, so two obviously wrong proposals appeared:

        API.POST_TASKS_VALIDATE -> skill_proposal_validate   shared=['validate']
        TBL.CODE_REGISTER       -> skill_worker_code_builder shared=['code']

    `validate` and `code` are generic: they appear in MANY keys, so they carry no
    information. This is precisely the `binding_proposals` v1 defect ("the token
    `api` is the first segment of every route, so it matched everything").

    The fix is the same one that module already uses: a token that matches SEVERAL
    candidates is not discriminating, so it is DISCARDED before matching. What
    remains is the token set that could actually decide.

    A second consequence: with generic tokens removed, `CAP.PPT.PRODUCE` goes from
    "reaches nothing" to still reaching nothing, and that is correct — `ppt` and
    `produce` appear in NO authoritative key, so no rule can justify a mapping and
    a human must supply one.
    """
    import binding_proposals as bp

    parts = entity_parts(contract_id)
    want = bp.key_tokens(parts["name"]) if parts["name"] else set()
    if not want:
        return []

    # Token -> how many authoritative keys contain it. A token in more than one
    # key cannot discriminate between them.
    spread: dict[str, int] = {}
    keyed: list[tuple[str, set[str]]] = []
    for k in auth:
        got = bp.key_tokens(k)
        keyed.append((k, got))
        for tok in got:
            spread[tok] = spread.get(tok, 0) + 1

    informative = {t for t in want if spread.get(t, 0) == 1}
    generic = sorted(want - informative)
    out = []
    for k, got in keyed:
        shared = informative & got
        if shared:
            out.append({"skill_key": k,
                        "shared_tokens": sorted(shared),
                        "generic_tokens_dropped": generic,
                        "rule": "informative-token overlap after entity-prefix "
                                "removal AND unique-token filtering"})
    return sorted(out, key=lambda c: (-len(c["shared_tokens"]), c["skill_key"]))


def build_list(conn: sqlite3.Connection) -> dict[str, Any]:
    auth = authoritative(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT DISTINCT contract_id FROM skill_contract_template "
        "ORDER BY contract_id")]
    exact = {k for k in auth}
    out: list[dict[str, Any]] = []
    for r in rows:
        cid = str(r["contract_id"])
        parts = entity_parts(cid)
        # DIRECT match on the authoritative spelling wins, and needs no rule.
        if cid in exact:
            out.append({"contract_id": cid, "state": "EXACT",
                        "skill_key": cid, "candidates": []})
            continue
        # The bare NAME matching is the strongest non-exact signal available.
        if parts["name"] and parts["name"].lower() in exact:
            out.append({"contract_id": cid, "state": "NAME_EXACT",
                        "skill_key": parts["name"].lower(), "candidates": []})
            continue
        cands = candidates(cid, auth)
        out.append({"contract_id": cid, "state": "PENDING",
                    "skill_key": None, "candidates": cands})

    # CROSS-CONTRACT COLLISION — the test that actually catches a generic token.
    #
    # The per-token uniqueness filter DID NOT catch `validate`: measured, it
    # appears in exactly ONE authoritative key (`skill_proposal_validate`), so it
    # is "unique" by that test. But THREE different contracts resolve to that one
    # key (API.POST_TASKS_VALIDATE, CAP.VALIDATE_NEW_TASK, FN.VALIDATE_NEW_TASK),
    # and one skill cannot be the correct mapping for three unrelated contracts.
    #
    # So the discriminating test is on the CANDIDATE, not the token: a candidate
    # claimed by more than one contract does not discriminate between them.
    from collections import Counter as _C
    claimed = _C(r["candidates"][0]["skill_key"] for r in out
                 if len(r["candidates"]) == 1)
    for r in out:
        if r["state"] != "PENDING":
            continue
        cands = r["candidates"]
        if len(cands) != 1:
            r["state"] = "NO_CANDIDATE" if not cands else "AMBIGUOUS"
            continue
        top = cands[0]["skill_key"]
        if claimed[top] > 1:
            r["state"] = "AMBIGUOUS"
            r["collision"] = ("%d contracts resolve to %r; one skill cannot be "
                              "the mapping for several unrelated contracts"
                              % (claimed[top], top))
            continue
        r["state"] = "PROPOSED"
        r["skill_key"] = top
        # WEAKNESS, MEASURED AND DECLARED: `TBL.CODE_REGISTER ->
        # skill_worker_code_builder` passes EVERY test in this module — the token
        # `code` is unique among the 26 skills, only one contract claims the
        # candidate, nothing collides — and it is still WRONG on inspection: a
        # db_table contract is not a code-builder skill.
        #
        # That is the boundary this module cannot cross, and the reason it is a
        # DECISION LIST rather than a bridge. A single shared token is evidence of
        # a WORD, not of a MEANING. So PROPOSED is downgraded in the REPORT to
        # "REVIEW" whenever the evidence is a single generic-ish token, and only a
        # human may promote it.
        if len(cands[0]["shared_tokens"]) < 2 and r["contract_id"].startswith(
                ("TBL.", "FLD.", "CH.", "MOD.", "CAP.", "FN.", "API.")):
            r["state"] = "REVIEW"
            r["weakness"] = ("one shared token (%s) for a NON-skill entity type: "
                             "the word matches, the meaning is unverified"
                             % cands[0]["shared_tokens"])

    states = _C(r["state"] for r in out)
    # `decisions_required` OMITTED REVIEW and therefore UNDER-REPORTED.
    # Measured when `decision_list` printed the items: 3 AMBIGUOUS + 8
    # NO_CANDIDATE + 1 REVIEW = 12, while this key said 11. A REVIEW row needs a
    # human exactly as much as an AMBIGUOUS one does — the automatic tests passed
    # and the mapping is still wrong — so it belongs in the count. An
    # under-counting "how much work is left" field is the same defect as a
    # silently empty slot: it makes the remaining job look smaller than it is.
    needs_human = {"REVIEW", "AMBIGUOUS", "NO_CANDIDATE"}
    return {
        "authoritative_skills": len(auth),
        "contracts": len(out),
        "by_state": dict(states),
        "decisions_required": sum(v for k, v in states.items()
                                  if k in needs_human),
        "needs_human_states": sorted(needs_human),
        "rows": out,
        "note": ("PROPOSED means the rule reached exactly ONE candidate AND no "
                 "other contract claims it. It does NOT mean the mapping is "
                 "correct: measured, TBL.CODE_REGISTER -> skill_worker_code_builder "
                 "survives every automatic test and is still WRONG on inspection, "
                 "which is why it is REVIEW and counted as a decision. "
                 "AMBIGUOUS and NO_CANDIDATE are decisions for a human."),
    }


def assert_no_similarity_scoring() -> None:
    """The rule this module must not use, checked rather than remembered.

    DEFECT FIXED: the first version flagged its OWN name, because
    `assert_no_similarity_scoring` contains the substring `scoring`. A checker
    that refuses itself is a checker no one can run — the same "measured the
    wrong object" family as the text searches that matched their own docstrings.

    So the guarded names are matched as WHOLE WORDS on the function name minus
    this checker, and a POSITIVE CONTROL in the proof injects a real scoring
    function name to prove the check can still fire.
    """
    import ast

    src = Path(__file__).read_text(encoding="utf-8", errors="replace")
    funcs = {n.name for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.FunctionDef)}
    funcs.discard("assert_no_similarity_scoring")
    # Word boundaries, so `scoring` inside a checker's own name cannot trip it,
    # while a real `compute_ratio` / `fuzzy_match` still does.
    rules = tuple(re.compile(r"(^|_)%s($|_)" % w)
                  for w in ("ratio", "fuzzy", "distance", "similar", "score",
                            "nearest"))
    banned = [f for f in funcs if any(rx.search(f.lower()) for rx in rules)]
    if banned:
        raise BridgeError(
            "similarity scoring functions present: %s. The discriminator must be "
            "the CANDIDATE COUNT, because a score cannot separate 'one match' "
            "from 'the best of six'." % banned)


def ensure_alias_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ALIAS_DDL)
    conn.commit()


def apply_proposed(conn: sqlite3.Connection, result: dict[str, Any], *,
                   decided_by: str, cite_ref: str) -> dict[str, Any]:
    """Write ONLY PROPOSED rows. Refuses if the decision is not attributed."""
    assert_no_similarity_scoring()
    if not decided_by or decided_by.strip().lower() in ("pattern", "guess",
                                                        "auto", ""):
        raise BridgeError(
            "decided_by must name who or what decided; %r is a pattern, not a "
            "decision" % decided_by)
    if not cite_ref or not cite_ref.strip():
        raise BridgeError("cite_ref required: a mapping with nothing to cite is "
                          "an opinion")
    ensure_alias_schema(conn)
    written, refused = 0, []
    for r in result["rows"]:
        if r["state"] != "PROPOSED":
            if r["state"] in ("AMBIGUOUS", "NO_CANDIDATE"):
                refused.append(r["contract_id"])
            continue
        conn.execute(
            "INSERT OR REPLACE INTO skill_key_alias "
            "(alias_key, skill_key, source, cite_ref, decided_by, note) "
            "VALUES (?,?,?,?,?,?)",
            (r["contract_id"], r["skill_key"], "skill_contract_template",
             cite_ref, decided_by,
             "candidates=%d" % len(r["candidates"])))
        written += 1
    conn.commit()
    return {"written": written, "refused": len(refused),
            "refused_examples": refused[:6],
            "alias_rows": conn.execute(
                "SELECT COUNT(*) FROM skill_key_alias").fetchone()[0]}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--decided-by", default="")
    ap.add_argument("--cite-ref", default="")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        res = build_list(conn)
        out: dict[str, Any] = {"list": res}
        if args.apply:
            out["writes"] = apply_proposed(conn, res,
                                           decided_by=args.decided_by,
                                           cite_ref=args.cite_ref)
        # The list itself is short; print the states without the long rows unless
        # a reader asks for them.
        print(json.dumps({"list": {k: v for k, v in res.items() if k != "rows"},
                          **({"writes": out["writes"]} if "writes" in out else {})},
                         indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()