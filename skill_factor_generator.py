# -*- coding: utf-8 -*-
"""skill_factor_generator.py — the SKILL TEMPLATE for the skill generator.

WHAT THIS IS
------------
The human's requirement: "be standardize by table, this is the skill template for
skill generator".

So this module is the TEMPLATE. It takes a skill and emits its TABLE
representation — one row per applicable factor, each with a rule, an action, a
metric and a proof artifact id. The table is the SSOT; the markdown is a VIEW of
it, produced here.

WHY A GENERATOR AND NOT A HAND-WRITTEN FILE
-------------------------------------------
A hand-written skill file drifts from the register the moment either changes.
Measured 2026-09-21: of 46 `.skill.md` files, 14 match their `skill_prompt_ssot`
row, **12 DIFFER**, and 20 have no row at all. A generator cannot drift, because
there is only one source.

WHAT IT REFUSES
---------------
  * a skill that is not in `skill_registry` — an unregistered skill has no
    identity, and generating for it would create a file nothing can resolve
  * a factor with no metric target — a factor that cannot be scored is decorative
  * writing over an existing file unless `--force` is given, because the file may
    carry human edits the generator cannot reproduce

THE OUTPUT IS A VIEW, AND SAYS SO
---------------------------------
Every generated file carries a header stating that it is GENERATED and naming the
source tables. A generated file that does not say it is generated is a file
someone will edit, and then the drift returns.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import skill_factor as sf  # noqa: E402

DB = BASE / "agent.db"
OUT_DIR = BASE / "skills_generated"

GENERATED_HEADER = """<!-- GENERATED FILE — DO NOT EDIT BY HAND.
     Source: skill_factor_registry ({factors} factors) + skill_factor_proof (measured)
     Generator: skill_factor_generator.py
     Regenerate: .\\.venv\\Scripts\\python.exe skill_factor_generator.py --skill {skill}
     An edit here is LOST on the next run. Change the register instead. -->
"""


class GenerateRefused(RuntimeError):
    """Raised when generating would produce a file nothing can resolve."""


# ---------------------------------------------------------------------------
# THE PROOF STATE — "proofed" has to be a NUMBER, not a word.
#
# WHY THIS EXISTS (the user, 2026-09-23):
#
#     "proofed key factor + template -> generator can have that at the templat
#      table"
#
# MEASURED before this: `generate()` read `skill_table()` and emitted every
# factor IDENTICALLY. A factor measured 100 times and a factor NEVER measured
# produced the SAME row, so the word "proofed" had no representation in the
# output at all. A reader could not tell a proven rule from a guess.
#
# THE THREE STATES, and they are DERIVED, never asserted:
#
#     PROVEN      a streak >= the target, read from `activation_gate`
#     MEASURED    a proof row exists, but the streak is below the target
#     UNMEASURED  no proof row at all — which is NOT a pass
#
# THE STREAK IS DELEGATED. `activation_gate.streak_detail()` is the ONE
# definition of "streak" (it delegates to `llm_100_run_harness.current_streak`).
# A second implementation here would let the generator and the activation gate
# disagree about the same evidence — the drift this repo keeps hitting.
# ---------------------------------------------------------------------------

PROVEN = "PROVEN"
MEASURED = "MEASURED"
UNMEASURED = "UNMEASURED"


def proof_state(conn: sqlite3.Connection, factor_key: str,
                *, target: int | None = None) -> dict[str, Any]:
    """`{state, proofs, wins, streak, target, reason}` for ONE factor.

    The streak is read for the factor's OWN `proof_prefix` ref_tag, because that
    is the tag a proof of THIS factor is recorded under. MEASURED: the live
    `proof_run` holds ref_tags `1.1F` / `worker_identity`, which are NOT factor
    keys — so a factor with a proof row but no `proof_run` rounds is MEASURED,
    not PROVEN. Reporting it as PROVEN would be a lie.
    """
    import activation_gate as ag

    row = conn.execute(
        "SELECT factor_id, proof_prefix FROM skill_factor_registry "
        " WHERE factor_key=?", (str(factor_key),)).fetchone()
    if not row:
        return {"state": UNMEASURED, "proofs": 0, "wins": 0, "streak": 0,
                "target": target, "reason": "factor is not registered"}
    p = conn.execute(
        "SELECT COUNT(*) n, SUM(metric_pass) wins FROM skill_factor_proof "
        " WHERE factor_ref=?", (int(row["factor_id"]),)).fetchone()
    proofs = int(p["n"] or 0)
    wins = int(p["wins"] or 0)
    if proofs == 0:
        return {"state": UNMEASURED, "proofs": 0, "wins": 0, "streak": 0,
                "target": target,
                "reason": "no proof row — an absent measurement is not a pass"}

    # The streak, DELEGATED. A failure to read it must not become a PROVEN.
    tgt = int(target) if target is not None else int(ag.DEFAULT_TARGET)
    try:
        d = ag.streak_detail(conn, str(row["proof_prefix"]))
        streak = int(d["streak"])
    except Exception as exc:
        return {"state": MEASURED, "proofs": proofs, "wins": wins, "streak": 0,
                "target": tgt,
                "reason": "streak unreadable (%s), so PROVEN cannot be claimed"
                          % str(exc)[:60]}
    if streak >= tgt:
        return {"state": PROVEN, "proofs": proofs, "wins": wins,
                "streak": streak, "target": tgt,
                "reason": "streak %d >= target %d" % (streak, tgt)}
    return {"state": MEASURED, "proofs": proofs, "wins": wins, "streak": streak,
            "target": tgt,
            "reason": "streak %d < target %d" % (streak, tgt)}


def generate(conn: sqlite3.Connection, skill_key: str) -> str:
    """The TABLE representation of one skill, as markdown."""
    row = conn.execute("SELECT skill_id, name, description FROM skill_registry "
                       "WHERE skill_key=?", (skill_key,)).fetchone()
    if not row:
        raise GenerateRefused(
            "skill %r is not in skill_registry. An unregistered skill has no "
            "identity, so a generated file for it could not be resolved back to "
            "anything." % skill_key)
    t = sf.skill_table(conn, skill_key)
    if not t["rows"]:
        raise GenerateRefused(
            "skill %r has NO applicable factor. A table with no rows is not a "
            "skill definition — it is an empty file with a heading." % skill_key)

    lines = [GENERATED_HEADER.format(
                 skill=skill_key,
                 factors=conn.execute("SELECT COUNT(*) FROM "
                                      "skill_factor_registry").fetchone()[0]),
             "# Skill: %s" % skill_key, "",
             "**%s**" % (row["description"] or "(no description)"), "",
             "| # | Factor | Rule Definition | Action | Metric | Value | "
             "Proof Artifact | Proof State | State |",
             "|---|---|---|---|---|---|---|---|---|"]
    states: dict[str, int] = {PROVEN: 0, MEASURED: 0, UNMEASURED: 0}
    for i, r in enumerate(t["rows"], start=1):
        # THE PROOF STATE, derived per factor. A factor measured 100 times and a
        # factor never measured must NOT produce the same row.
        ps = proof_state(conn, r["factor_key"])
        states[ps["state"]] = states.get(ps["state"], 0) + 1
        lines.append("| %d | %s | %s | %s | `%s` target `%s` | %s | `%s` | %s | %s |"
                     % (i, r["name"],
                        r["rule_definition"].replace("|", "/"),
                        r["action"].replace("|", "/"),
                        r["metric_kind"], r["metric_target"],
                        r["metric_value"] or "-",
                        r["proof_artifact_id"], ps["state"], r["state"]))
    # THE LESSONS. A skill table that shows only its RULES hides what it has
    # LEARNED. The user's requirement: "+ to skill template so skill generator
    # can be powerful". `skill_lesson` already holds the lessons; the generator
    # simply never read them, so a generated file could not answer "what went
    # wrong here before?".
    lessons = [dict(x) for x in conn.execute(
        "SELECT lesson_text, root_cause, suggested_fix, source_ref, status "
        "FROM skill_lesson WHERE skill_key=? ORDER BY id", (skill_key,))]
    if lessons:
        lines += ["", "## Lessons (what this skill learned)", "",
                  "| # | Lesson | Root Cause | Suggested Fix | Cite | Status |",
                  "|---|---|---|---|---|---|"]
        for i, L in enumerate(lessons, start=1):
            lines.append("| %d | %s | %s | %s | `%s` | %s |"
                         % (i, str(L["lesson_text"]).replace("|", "/"),
                            str(L["root_cause"] or "-").replace("|", "/"),
                            str(L["suggested_fix"] or "-").replace("|", "/"),
                            L["source_ref"] or "-", L["status"]))
    lines += ["",
              "## Summary", "",
              "| applicable | measured | PASS | FAIL | UNMEASURED | PROVEN |",
              "|---|---|---|---|---|---|",
              "| %d | %d | %d | %d | %d | %d |"
              % (t["applicable"], t["measured"], t["passed"], t["failed"],
                 t["unmeasured"], states.get(PROVEN, 0)),
              "",
              "**UNMEASURED is not a pass.** A factor with no proof row has not "
              "been measured, and an absent measurement is not a passing one.",
              "",
              "**PROVEN requires a STREAK, not a single proof.** A factor with "
              "one proof row is MEASURED; it becomes PROVEN only when its "
              "`proof_prefix` streak reaches the target. The streak is read from "
              "`activation_gate.streak_detail()` — the ONE definition of "
              "\"streak\" — so the generator and the activation gate cannot "
              "disagree about the same evidence.",
              ""]
    return "\n".join(lines)


def ensure_skill_version(conn: sqlite3.Connection, skill_key: str) -> dict:
    """Give a skill its version 1, so `S-<skill_id>-1` VERIFIES.

    DEFECT FOUND BY RUNNING THE PROOF (2026-09-21): a newly registered skill had
    NO `version_registry` row, so `entity_id.require("S-<id>-1")` refused with
    "no active version 1". That is the gate working correctly — but it meant a
    new skill could not carry a service ticket until someone remembered to
    register a version by hand. The user's answer: "skill generator will help
    auto".

    So the generator does it. It REUSES `entity_registry.ensure_version()`, which
    itself refuses a version for an entity that does not exist — a version row
    pointing at nothing would make an id verify while resolving to nothing,
    which is worse than a missing version.
    """
    import entity_registry as er

    row = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                       (str(skill_key),)).fetchone()
    if not row:
        return {"ok": False, "why": "skill %r is not in skill_registry"
                % skill_key}
    res = er.ensure_version(
        conn, "S", int(row[0]), 1,
        note="index-0 version, cited by skill_registry.skill_id = %d"
             % int(row[0]),
        created_by="skill_factor_generator")
    return dict(res, skill_key=str(skill_key), skill_id=int(row[0]))


def write_skill(conn: sqlite3.Connection, skill_key: str, *,
                out_dir: Path | str = OUT_DIR, force: bool = False) -> dict:
    """Write the generated table. Refuses to overwrite without `force`.

    Also ensures the skill's version 1 exists, so the skill can carry a service
    ticket (`S-<skill_id>-1`) without a manual step.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.skill.md" % skill_key)
    if p.exists() and not force:
        raise GenerateRefused(
            "%s already exists. It may carry human edits the generator cannot "
            "reproduce, so overwriting is REFUSED unless --force is given."
            % p.name)
    ver = ensure_skill_version(conn, skill_key)
    text = generate(conn, skill_key)
    p.write_text(text, encoding="utf-8")
    return {"skill_key": skill_key, "path": str(p),
            "bytes": len(text.encode()), "version": ver}


def generate_all(conn: sqlite3.Connection, *, out_dir: Path | str = OUT_DIR,
                 force: bool = False) -> dict[str, Any]:
    """Every registered skill. A skill that cannot be generated is REPORTED."""
    written, refused = [], []
    for r in conn.execute("SELECT skill_key FROM skill_registry "
                          "WHERE is_active=1 ORDER BY skill_key"):
        k = r[0]
        try:
            written.append(write_skill(conn, k, out_dir=out_dir, force=force))
        except GenerateRefused as e:
            refused.append({"skill_key": k, "reason": str(e)[:90]})
    return {"written": len(written), "refused": len(refused),
            "refused_examples": refused[:6], "out_dir": str(out_dir)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--skill", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.stdout and args.skill:
            print(generate(conn, args.skill))
        elif args.all:
            print(json.dumps(generate_all(conn, out_dir=args.out,
                                          force=args.force), indent=2))
        elif args.skill:
            print(json.dumps(write_skill(conn, args.skill, out_dir=args.out,
                                         force=args.force), indent=2))
        else:
            print(json.dumps({"factors": conn.execute(
                "SELECT COUNT(*) FROM skill_factor_registry").fetchone()[0],
                "skills": conn.execute(
                    "SELECT COUNT(*) FROM skill_registry").fetchone()[0]},
                indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()