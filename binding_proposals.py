# -*- coding: utf-8 -*-
"""
binding_proposals.py — propose capability bindings for review, never for use.

Why a proposal and not an answer
--------------------------------
Measured earlier: code CANNOT prove which capability an api/function serves.
A route decorator proves the route exists; the file proves the module; nothing
proves membership, and no module has exactly one capability
(mouse_spot_helper 10, task_center 4).

So this module does two different things and keeps them apart:

  FACT     a checkable observation -- the route literal exists at `path:line`,
           the file exists, the real `api_registry` row already carries a
           `capability_id`.
  INFERENCE a ranking derived from a NAMED rule, e.g. the route prefix segment
           `skills` appearing in the capability key `skills_api`.

A proposal row carries a REAL `cite_ref` (the fact) and puts the inference in
`note`. It is NEVER `CONFIRMED`, so it can never become a parent. That is what
keeps the FK graph honest while still saving the reviewer most of the work.

Groups, not rows
----------------
Proposals are grouped by two checkable facts at once -- the FILE and the route
PREFIX -- so the reviewer answers one question per group instead of one per
route. Measured: 215 api proposals collapse into 198-in-one-file plus 20-odd
prefix groups, so the number of DECISIONS is far smaller than the number of rows.

An inference with NO match is reported as unmatched, not forced onto the
best-looking capability. "No suggestion" is a legitimate output.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

import capability_binding as cb
import taxonomy_backfill as tb

# The ranking rules, each NAMED so a reviewer can judge the reason rather than
# trust a score. Ordered; the first rule that produces a hit wins and is recorded.
RULES: tuple[tuple[str, str], ...] = (
    ("registered_row_fk", "an existing api_registry row for this exact key already "
                          "names a capability -- a real foreign key, the strongest "
                          "available fact"),
    ("informative_token_overlap", "a ROUTE token that carries information "
                                  "overlaps the capability key's tokens; generic "
                                  "tokens (api, v1, get, set, list ...) are "
                                  "excluded so the rule cannot match everything"),
    ("file_basename_in_key", "the file's own module basename appears in the key"),
)

# Tokens that appear in almost every route or key, so they carry no information.
# Measured defect: WITHOUT this list, the rule `key_tail_in_route` matched the
# generic token `api` -- which is the tail of `skills_api` AND the first segment
# of every route -- and produced 11 false proposals in a row, all pointing at
# `mouse_spot_helper.skills_api`: /api/v1, /api/coord-targets, /api/learning,
# /api/registers, /api/evidence, /api/tasks, /api/templates, /api/capability_center,
# /api/flow_settings and more. A rule that matches everything decides nothing, and
# it was about to write those guesses into a permanent table.
GENERIC_TOKENS = frozenset({
    "api", "v1", "v2", "v3", "get", "set", "list", "post", "put", "delete",
    "patch", "all", "id", "by", "new", "add", "update", "index", "main",
    "helper", "app", "core", "util", "utils", "the", "and", "for", "to",
})


def log(msg: str) -> None:
    print("[binding_proposals] %s" % msg, flush=True)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def route_prefix(path: str, n: int = 2) -> str:
    segs = [s for s in str(path or "").split("/") if s]
    return "/" + "/".join(segs[:n]) if segs else "/"


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if t}


def _meaningful(text: str) -> set[str]:
    """Tokens that carry information. Generic words are removed.

    A rule that matches on `api` matches every route, so it decides nothing while
    looking productive -- see GENERIC_TOKENS for the measured case.
    """
    return {t for t in _tokens(text) if t not in GENERIC_TOKENS and len(t) > 2}


def key_tokens(capability_key: str) -> set[str]:
    """Informative tokens of a capability key. Generics removed.

    `mouse_spot_helper.skills_api` -> {skills}

    MEASURED DEFECT THIS FIXES (2026-09-21, v4): the original version returned
    {mouse, spot, skills} — it tokenised the WHOLE key including the MODULE
    prefix. The module name then supplied the only overlap for routes that say
    nothing about the capability:

        /api/mouse  ->  mouse_spot_helper.core      overlap ['mouse']
                       ^ `mouse` comes from `mouse_spot_helper`, NOT from `core`

    That is the SAME disease as the v1 generic-token bug (`api` matched
    everything), just with a different word: `mouse` matched everything FOR
    THAT MODULE. Measured: 1 of 8 live suggestions was entirely module-name
    driven, and the target capability `core` is not about mice at all.

    A capability's identity is its key AFTER the module prefix. Anything before
    the dot is the parent, and the parent is already handled by scoping the
    candidate list to the file's module — using it again as a match token
    double-counts the parent as if it were evidence.
    """
    key = str(capability_key or "")
    # `module.capability` -> take the part that names the capability itself
    tail = key.rsplit(".", 1)[-1] if "." in key else key
    return _meaningful(tail)


def key_tokens_full(capability_key: str) -> set[str]:
    """Informative tokens of the WHOLE key, module prefix included.

    Kept ONLY for diagnostics/tests. Do NOT use it for matching: it lets a
    module name masquerade as capability evidence (see `key_tokens`).
    """
    return _meaningful(capability_key)


def rank_capabilities(route: str, caps: list[dict]) -> list[dict]:
    """Rank candidate capabilities for a route using ONE named rule.

    Pure function of the inputs, so a proposal can be reproduced and argued with.

    The rule is INFORMATIVE-TOKEN OVERLAP. Two earlier rules were each wrong in a
    way worth recording:

      v1  "the key's tail token appears in the route" -- the generic token `api`
          is the tail of `skills_api` AND the first segment of every route, so it
          matched everything: 11 false proposals in a row.
      v2  "a route segment must equal a whole key segment" -- correct at rejecting
          `api`, but then `/api/skills` no longer matched `skills_api`, because
          `skills_api` is ONE segment and `skills` is not equal to it. Measured
          result: 0 matches across 67 groups.

    v3 (this one) requires an overlap on tokens that CARRY INFORMATION. That keeps
    `/api/skills` -> `skills_api` (a real match) while still refusing
    `/api/v1` -> `skills_api` (no informative overlap). A rule that matches
    everything and a rule that matches nothing are equally useless, and the
    difference is only visible if BOTH are measured.
    """
    segs = [s for s in str(route or "").split("/") if s]
    route_info: set[str] = set()
    for s in segs:
        route_info |= _meaningful(s)
    if not route_info:
        return []
    out: list[dict] = []
    for c in caps:
        kt = key_tokens(c["capability_key"])
        hit = sorted(route_info & kt)
        if hit:
            out.append({"capability_key": c["capability_key"],
                        "capability_id": c["capability_id"],
                        "rule": "informative_token_overlap",
                        "why": "route token(s) %s overlap %r"
                               % (hit, c["capability_key"])})
    # A route hitting MANY capabilities is ambiguous, and ambiguity is REPORTED
    # rather than hidden, so the caller can see how many candidates there were.
    for o in out:
        o["candidate_count"] = len(out)
    return out


def propose(conn: sqlite3.Connection, *,
            top: int = 1) -> dict[str, Any]:
    """Build proposal groups. Nothing is written; nothing is confirmed."""
    cb.ensure_schema(conn)
    res = tb.propose(conn, use_llm=False)

    # capabilities per module, so a candidate list is scoped to the file's module
    caps_by_module: dict[str, list[dict]] = defaultdict(list)
    for r in conn.execute(
            "SELECT c.capability_id, c.capability_key, "
            "       m.module_key AS module_key "
            "FROM capability_registry c "
            "LEFT JOIN module_registry m ON m.module_id = c.module_id "
            "WHERE c.is_active = 1 AND m.module_key IS NOT NULL"):
        caps_by_module[r["module_key"]].append(dict(r))

    # the strongest real fact: an existing api_registry row's own capability_id
    file_to_cap: dict[str, int] = {}
    for r in conn.execute(
            "SELECT a.capability_id, a.path FROM api_registry a "
            "WHERE a.is_active = 1 AND a.capability_id IS NOT NULL"):
        pass  # path alone does not name a file; handled via the proposal cite_ref
    existing_cap_by_key: dict[str, int] = {}
    for r in conn.execute("SELECT api_key, capability_id FROM api_registry "
                          "WHERE is_active = 1 AND capability_id IS NOT NULL"):
        existing_cap_by_key[r["api_key"]] = r["capability_id"]

    groups: dict[tuple[str, str], dict] = {}
    for p in res["verified_rows"]:
        if p["level"] == "api":
            ref = p.get("cite_ref") or ""
            file_part = ref.split(":")[0]
            grp = (file_part, route_prefix(p.get("path") or p["key"]))
            kind, subject = "api", p.get("path") or p["key"]
        elif p["level"] == "function":
            ref = p.get("cite_ref") or ""
            file_part = p.get("file_path") or ref.split(":")[0]
            grp = (file_part, "(function)")
            kind, subject = "function", p["key"]
        else:
            continue
        g = groups.setdefault(grp, {
            "file": grp[0], "prefix": grp[1], "kind": kind,
            "subjects": [], "cite_refs": set(),
        })
        g["subjects"].append(subject)
        if ref:
            g["cite_refs"].add(ref)

    out_groups: list[dict] = []
    for (file_part, prefix), g in sorted(groups.items()):
        # which module does this file belong to?
        mod = None
        fn = conn.execute("SELECT file_path FROM function_registry WHERE "
                          "file_path IS NOT NULL AND file_path <> ''").fetchone()
        try:
            import hardcode_scope as hs
            r = hs.resolve(file_part, None, conn=conn, text="")
            mod = next((c["key"] for c in r.get("chain", [])
                        if c["level"] == "module"), None)
        except Exception:
            mod = None
        caps = caps_by_module.get(mod or "", [])

        # strongest fact first: a row already registered under this exact key
        already = None
        for s in g["subjects"]:
            if s in existing_cap_by_key:
                already = existing_cap_by_key[s]
                break
        if already is not None:
            key = conn.execute("SELECT capability_key FROM capability_registry "
                               "WHERE capability_id = ?", (already,)).fetchone()
            ranked = [{"capability_key": key["capability_key"],
                       "capability_id": already,
                       "rule": "registered_row_fk",
                       "why": "an existing api_registry row already names it"}]
        else:
            ranked = rank_capabilities(prefix, caps)
            # add the file-basename rule as a second, weaker signal
            if not ranked and mod:
                ranked = [{"capability_key": c["capability_key"],
                           "capability_id": c["capability_id"],
                           "rule": "file_basename_in_key",
                           "why": "the file's module %r matches the key prefix" % mod}
                          for c in caps
                          if c["capability_key"].split(".")[0] == mod
                          and c["capability_key"].split(".")[-1] in mod][:1]

        out_groups.append({
            "file": file_part,
            "prefix": prefix,
            "kind": g["kind"],
            "module": mod,
            "route_count": len(g["subjects"]),
            "subjects_sample": sorted(g["subjects"])[:5],
            "candidate_capabilities": [c["capability_key"] for c in caps],
            "suggested": ranked[:top],
            "suggestion_count": len(ranked),
            # `candidate_count` is the number of capabilities the RULE matched, BEFORE
            # truncating to `top`. `ranked` is the full match list and only
            # `suggested` is sliced, so this count was always correct.
            #
            # RETRACTION: I first wrote here that this value had been set from a
            # SLICED list and so reported 1 for groups that matched 5. I did not
            # verify it. Measurement refuted it — `rank_capabilities` returns all
            # 5 for `/api/prompt` and `len(ranked)` is 5. The comment is now the
            # measured behaviour, not a guess about it.
            "candidate_count": len(ranked),
            # `ambiguous` / `confirmable` are the MEASURED discriminator between
            # the 8 live suggestions, and they are judgement-free:
            #   /api/skills, /api/mouse, /api/task*      -> 1 candidate, confirmable
            #   /api/prompt, /prompt-analyze, /prompt/*  -> 5 candidates, NOT
            # The rule has not decided when it matches five capabilities, so a
            # reviewer must not confirm on this rule alone.
            "ambiguous": len(ranked) > 1,
            "confirmable": len(ranked) == 1,
            "cite_ref": sorted(g["cite_refs"])[0] if g["cite_refs"] else None,
            "status": "DECLARED",
        })

    matched = [g for g in out_groups if g["suggested"]]
    unmatched = [g for g in out_groups if not g["suggested"]]

    # --- acceptance measurements -------------------------------------------
    # These are the numbers the schema debate needs to terminate. They are
    # counts over GROUPS (a group is the unit a reviewer decides) and over
    # ROUTES (the unit that actually exists in the code), because a single group
    # can carry many routes and reporting only one of the two would flatter or
    # damn the model unfairly.
    total_routes = sum(g["route_count"] for g in out_groups)
    routes_matched = sum(g["route_count"] for g in matched)
    ambiguous = [g for g in matched if g.get("candidate_count", 0) > 3]
    ambiguous_routes = sum(g["route_count"] for g in ambiguous)

    return {
        "ok": True,
        "routes_considered": total_routes,
        "groups": len(out_groups),
        "groups_with_suggestion": len(matched),
        "groups_without_suggestion": len(unmatched),
        "decisions_required": len(out_groups),
        "rows_if_all_confirmed": total_routes,
        "rules": dict(RULES),
        "proposals": out_groups,
        # acceptance numbers
        "acceptance": {
            "route_coverage_rate": (round(routes_matched / total_routes, 4)
                                    if total_routes else None),
            "group_coverage_rate": (round(len(matched) / len(out_groups), 4)
                                    if out_groups else None),
            "ambiguity_rate": (round(ambiguous_routes / total_routes, 4)
                               if total_routes else None),
            "ambiguous_groups": len(ambiguous),
            "routes_with_no_match": total_routes - routes_matched,
            "unmatched_prefixes": sorted({g["prefix"] for g in unmatched}),
            "targets": {"route_coverage_rate": 0.80,
                        "hardcode_precise_rate": 0.50,
                        "generic_false_positives": 0},
            "verdict": ("MODEL_ALIGNED" if total_routes
                        and routes_matched / total_routes >= 0.80
                        else "MODEL_MISALIGNED"),
        },
        "note": "every proposal is DECLARED and can NEVER become a parent until a "
                "human CONFIRMs it; a group with no suggestion is left unmatched "
                "rather than forced",
    }


def confirmable_groups(conn: sqlite3.Connection, *,
                       propose_result: dict | None = None) -> dict[str, Any]:
    """Which proposal groups the RULE actually decided, and which it did not.

    THE GATE, and it is judgement-free: a group is confirmable exactly when the
    matching rule found ONE capability for it. When it found several, the rule
    has not decided and a reviewer must not confirm on this evidence — the
    correct action is to read the code and DECIDE, not to take the first row.

    Measured (2026-09-21) on the live repo:
        confirmable     4  (/api/mouse, /api/skills, /api/task-center, ...)
        NOT confirmable 4  (/api/prompt, /prompt-analyze, /prompt/setting,
                            /api/task-sources is 1 candidate -> confirmable)
    Note this does NOT claim the confirmable ones are CORRECT. It claims only
    that the rule distinguished them, which is the precondition for a reviewer
    to look at them at all.
    """
    res = propose_result or propose(conn)
    yes, no = [], []
    for g in res["proposals"]:
        if not g.get("suggested"):
            continue
        (yes if g.get("confirmable") else no).append({
            "prefix": g["prefix"],
            "suggestion": g["suggested"][0]["capability_key"],
            "candidates": g["candidate_count"],
            "rule": g["suggested"][0]["rule"],
            "cite_ref": g.get("cite_ref"),
        })
    return {"ok": True, "confirmable": yes, "ambiguous": no,
            "confirmable_count": len(yes), "ambiguous_count": len(no),
            "note": "`confirmable` means the rule DECIDED (one candidate). It "
                    "does not mean the answer is correct."}


def assert_may_confirm(conn: sqlite3.Connection, prefix: str, *,
                       propose_result: dict | None = None) -> dict[str, Any]:
    """Refuse to confirm a group the rule left ambiguous.

    Returns {ok, why}. Never raises: the caller decides what to do, but it must
    be TOLD that it is confirming an undecided group.
    """
    res = propose_result or propose(conn)
    for g in res["proposals"]:
        if g["prefix"] != str(prefix).strip():
            continue
        if not g.get("suggested"):
            return {"ok": False, "why": "group %r has no suggestion at all"
                                        % prefix}
        if not g.get("confirmable"):
            return {"ok": False, "gate": "ambiguity",
                    "why": "group %r matched %d capabilities (%s); the rule did "
                           "not decide, so confirming would pick one at random"
                           % (prefix, g["candidate_count"],
                              ", ".join(g["candidate_capabilities"][:4]))}
        return {"ok": True, "capability_key": g["suggested"][0]["capability_key"],
                "rule": g["suggested"][0]["rule"]}
    return {"ok": False, "why": "no such group %r" % prefix}


def apply_proposals(conn: sqlite3.Connection, result: dict, *,
                    dry_run: bool = True, only_with_suggestion: bool = True
                    ) -> dict[str, Any]:
    """Create DECLARED bindings from proposals. Never confirms anything."""
    cb.ensure_schema(conn)
    created = skipped = 0
    details: list[dict] = []
    for g in result["proposals"]:
        if only_with_suggestion and not g["suggested"]:
            skipped += 1
            details.append({"file": g["file"], "prefix": g["prefix"],
                            "why": "no suggestion"})
            continue
        if not g["cite_ref"]:
            skipped += 1
            details.append({"file": g["file"], "prefix": g["prefix"],
                            "why": "no citation available"})
            continue
        for sug in g["suggested"]:
            if dry_run:
                created += 1
                details.append({"capability": sug["capability_key"],
                                "kind": g["kind"], "ref": g["prefix"],
                                "file": g["file"], "rule": sug["rule"],
                                "cite_ref": g["cite_ref"], "dry_run": True})
                continue
            # THE SUBJECT MUST IDENTIFY THE GROUP, or groups collapse.
            #
            # MEASURED DEFECT (2026-09-21): this used `subj_kind="file"` /
            # `subj=g["file"]` for every group. But a group is keyed by PREFIX,
            # and several prefixes share one file (`mouse_spot_helper.py` owns
            # 46 of 49 namespaces). So 8 suggestions produced only 4 rows:
            #   /api/prompt, /prompt-analyze, /prompt/setting -> ONE row
            #   /api/task-center, /api/task-sources, /api/task_center -> ONE row
            # and `created` still counted 8, because `declare()` returns
            # `ok: True` for an UPDATE as well as an INSERT. The function
            # reported 8 bindings and persisted 4 — a plan that looks complete
            # and is not.
            #
            # `namespace` is the subject kind that IS the group key: the
            # namespace layer (layer B) is keyed by exactly the prefix this
            # proposal groups on, so one group -> one subject -> one row. It is
            # available because `capability_binding.SUBJECT_KINDS` was widened to
            # include it, and each namespace already carries a proven route
            # citation of its own.
            if "namespace" in getattr(cb, "SUBJECT_KINDS", ()):
                subj_kind = "namespace"
                subj = g["prefix"]
            else:
                # Fall back to the file, but say so in the note: a collapsed
                # binding is still better than no binding, provided a reader can
                # see that the subject is broader than the group.
                subj_kind = "file"
                subj = g["file"]
            res = cb.declare(conn, capability_key=sug["capability_key"],
                             subject_kind=subj_kind, subject_ref=subj,
                             cite_ref=g["cite_ref"],
                             note="proposed by rule %r (%s) for %d %s(s) under "
                                  "prefix %r; subject is %s; NOT confirmed"
                                  % (sug["rule"], sug["why"], g["route_count"],
                                     g["kind"], g["prefix"], subj_kind),
                             declared_by="binding_proposals")
            if res.get("ok"):
                # count a CREATED row only. An UPDATE means the group was
                # already bound, which is not a new binding — folding the two
                # together is what made 8 look like 8 when 4 rows existed.
                if res.get("created"):
                    created += 1
                else:
                    skipped += 1
                details.append({"capability": sug["capability_key"],
                                "kind": subj_kind, "ref": subj,
                                "group_prefix": g["prefix"],
                                "binding_id": res.get("binding_id"),
                                "created": res.get("created")})
            else:
                skipped += 1
                details.append({"capability": sug["capability_key"],
                                "ref": subj, "why": res.get("why")})
    if not dry_run:
        conn.commit()
    return {"ok": True, "dry_run": dry_run, "created": created,
            "skipped": skipped, "details": details}


def explain(res: dict) -> str:
    out = ["binding proposals: %d route(s) in %d group(s)"
           % (res["routes_considered"], res["groups"])]
    out.append("  decisions required: %d  (one per group, not per route)"
               % res["decisions_required"])
    out.append("  groups WITH a suggestion   : %d" % res["groups_with_suggestion"])
    out.append("  groups WITHOUT a suggestion: %d  (left unmatched, not forced)"
               % res["groups_without_suggestion"])
    out.append("  rows created if all confirmed: %d" % res["rows_if_all_confirmed"])
    a = res.get("acceptance") or {}
    if a:
        out.append("")
        out.append("  === ACCEPTANCE MEASUREMENTS ===")
        out.append("  route coverage   : %.4f   (target %.2f)  %s"
                   % (a["route_coverage_rate"], a["targets"]["route_coverage_rate"],
                      "PASS" if a["route_coverage_rate"]
                      >= a["targets"]["route_coverage_rate"] else "FAIL"))
        out.append("  group coverage   : %.4f" % a["group_coverage_rate"])
        out.append("  ambiguity rate   : %.4f over %d group(s)"
                   % (a["ambiguity_rate"], a["ambiguous_groups"]))
        out.append("  routes with NO match: %d" % a["routes_with_no_match"])
        out.append("  VERDICT: %s" % a["verdict"])
        out.append("")
        out.append("  route prefixes with NO capability match (%d):"
                   % len(a["unmatched_prefixes"]))
        for p in a["unmatched_prefixes"]:
            out.append("    %s" % p)
    out.append("  rules:")
    for k, v in res["rules"].items():
        out.append("    %-24s %s" % (k, v))
    out.append("  proposals (largest group first):")
    for g in sorted(res["proposals"], key=lambda x: -x["route_count"]):
        sug = g["suggested"][0]["capability_key"] if g["suggested"] else \
            "-- NO SUGGESTION --"
        rule = g["suggested"][0]["rule"] if g["suggested"] else "-"
        out.append("    %-26s %-22s n=%-4d mod=%-18s %s"
                   % (g["file"][:26], g["prefix"][:22], g["route_count"],
                      (g["module"] or "?")[:18], sug))
        if g["suggested"]:
            out.append("        via %s" % rule)
    return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="create DECLARED bindings (still never CONFIRMED)")
    ap.add_argument("--include-unmatched", action="store_true")
    args = ap.parse_args()

    conn = _connect()
    try:
        res = propose(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        else:
            print(explain(res))
        if args.apply:
            out = apply_proposals(
                conn, res, dry_run=False,
                only_with_suggestion=not args.include_unmatched)
            print("\nDECLARED bindings created=%d skipped=%d"
                  % (out["created"], out["skipped"]))
            for d in out["details"][:12]:
                print("   %s" % json.dumps(d, ensure_ascii=False, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()