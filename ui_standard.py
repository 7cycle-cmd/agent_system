# -*- coding: utf-8 -*-
"""ui_standard.py — the rule module that makes "user friendly" CHECKABLE.

WHY THIS FILE EXISTS
--------------------
THE HUMAN (2026-09-27), verbatim:

    "be user fiendly as we have ui helper team , why i never have good
     experience ? i just have question and question for all your ui job?"
    "how does it can be better, github can help you by skill how to have ssot
     for all ui element to proof user friendly is not a word is standardize"
    "3) **`user_friendly for UI -> 5W1H to get factor + element(wording?) ->
     this is formula, you can proof my word, by evidence , i am not boss,
     evidenc is boss"

**"User friendly" is not a word. It is a standard, and a standard is a table.**

MEASURED, and this is the defect: `user_friendly` is NOT REGISTERED in
`terminology_registry` (0 rows of 1483) and appears in 0 files under `docs/`. It
is an adjective with no definition, no unit and no proof — so every time the agent
says "done", the human has no way to check it.

THE FIVE RULES, EACH WITH A MEASURED UNIT
-----------------------------------------
The human's formula is `UI element -> 5W1H -> factor -> proof`. The five rules
below ARE the factors, and each carries the unit that makes it auditable:

  1. `label_registered`         pct of visible labels with a terminology_registry row
  2. `why_clickable`            count of hover-only `title=` attributes
  3. `unknown_names_source`     pct of "unknown" badges naming the table AND the value
  4. `number_names_population`  pct of rendered numbers stating what they count
  5. `empty_state_names_action` pct of empty states naming a next action

THE PATTERN IS `measurement_scope.py`'s
---------------------------------------
That module declares 4 steps / 4 statuses, a `KEEP_STATUS`, a `TERMINAL` dict, and
an `assert_scoped()` that DISCARDS a non-conforming finding at the write site
rather than downgrading it. This module does the same for a UI element, because
the defect family is the same: a thing that is stated but never enforced where it
is WRITTEN.

WHY ALL FIVE RULES ALWAYS RUN
-----------------------------
A check that stops at the first failure cannot report the OTHER four. MEASURED:
the pinned page fails 4 of the 5 rules at once, and a short-circuit would have
reported only the first — so the human would fix one, re-run, and discover the
next. `check_element()` runs all five, ALWAYS, even when all five pass.

WHY AN UNSCOPED ELEMENT IS DROPPED, NOT DOWNGRADED
--------------------------------------------------
`citation-discipline`'s rule, applied here: a finding without a checkable
reference is DISCARDED at the write site, never downgraded to a low-confidence
finding. A UI element that fails a rule is not "a slightly worse element" — it is
an element that does not meet the standard, and `partition()` returns it in
`dropped` with NO `low_confidence` key, so no caller can quietly keep it.

Usage:
  python ui_standard.py --audit
  python ui_standard.py --audit --page user_environment.sessions
  python ui_standard.py --check sessions.col.measurements
"""
from __future__ import annotations

import argparse
import json
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

# THE FIVE RULES. Declared ONCE here; `ui_element_registry.validate_element` and
# the contract's fields are derived from the same five names, so a rule cannot
# exist in one place and not the other.
RULES: tuple[str, ...] = (
    "label_registered",
    "why_clickable",
    "unknown_names_source",
    "number_names_population",
    "empty_state_names_action",
)

# THE STATUSES. One per rule, plus the pass. `USER_FRIENDLY` is the KEEP status.
STATUSES: tuple[str, ...] = (
    "LABEL_UNREGISTERED",
    "WHY_HOVER_ONLY",
    "UNKNOWN_UNNAMED",
    "NUMBER_UNSCOPED",
    "EMPTY_NO_ACTION",
    "USER_FRIENDLY",
)

# The status a rule failure maps to. A dict, not a chain of ifs, so the mapping is
# DATA a reader can check rather than control flow they must trace.
RULE_STATUS: dict[str, str] = {
    "label_registered": "LABEL_UNREGISTERED",
    "why_clickable": "WHY_HOVER_ONLY",
    "unknown_names_source": "UNKNOWN_UNNAMED",
    "number_names_population": "NUMBER_UNSCOPED",
    "empty_state_names_action": "EMPTY_NO_ACTION",
}

# The ONE status that means "keep". An ALIAS for the pass, not a 9th status.
KEEP_STATUS = "USER_FRIENDLY"

# The terminal statuses: a status from which no further rule can change the
# verdict. `USER_FRIENDLY` is terminal because all five rules already passed.
TERMINAL: dict[str, bool] = {s: True for s in STATUSES}

# The unit each rule is measured in. The human's rule: "everything can measure
# with measured unit". A rule with no unit is a preference.
RULE_UNIT: dict[str, tuple[str, str]] = {
    "label_registered": ("pct", "pct of visible labels with a terminology_registry row"),
    "why_clickable": ("count", "count of hover-only title= attributes"),
    "unknown_names_source": ("pct", "pct of unknown badges naming the table AND the value"),
    "number_names_population": ("pct", "pct of rendered numbers stating what they count"),
    "empty_state_names_action": ("pct", "pct of empty states naming a next action"),
}


class UiNotFriendly(ValueError):
    """Raised when a UI element does not meet the standard."""


def _connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _result(status: str, *, keep: bool, rules: dict[str, Any],
            element_key: str = "", why: str = "") -> dict[str, Any]:
    """The ONE shape every function here returns."""
    return {"status": status, "keep": bool(keep), "element_key": element_key,
            "rules": rules, "rules_total": len(RULES),
            "rules_failed": sorted(k for k, v in rules.items() if not v.get("pass")),
            "why": why}


def check_element(el: dict[str, Any], *, terms: set[str] | None = None) -> dict[str, Any]:
    """Run ALL FIVE rules on ONE element. Never raises.

    `terms` is the set of registered term keys. When it is None the rule is
    evaluated against the element's own `term_key` being non-empty, and the
    result SAYS SO — a weaker guarantee must be named, not hidden.
    """
    kind = str(el.get("element_kind") or "")
    rules: dict[str, dict[str, Any]] = {}

    tk = str(el.get("term_key") or "").strip()
    if terms is None:
        rules["label_registered"] = {
            "pass": bool(tk),
            "detail": ("term_key %r is non-empty (the register was NOT consulted)"
                       % tk) if tk else "term_key is empty",
            "weaker": True,
        }
    else:
        rules["label_registered"] = {
            "pass": tk in terms,
            "detail": ("term_key %r is registered" % tk) if tk in terms
                      else ("term_key %r is NOT in terminology_registry" % tk),
        }

    needs_why = kind in ("badge", "number")
    has_why = bool(int(el.get("why_clickable") or 0))
    rules["why_clickable"] = {
        "pass": (not needs_why) or has_why,
        "detail": ("not applicable to a %s" % kind) if not needs_why
                  else ("clickable" if has_why else "HOVER-ONLY"),
    }

    text = str(el.get("rendered_text") or "").lower()
    is_unknown = ("unknown" in text or "underivable" in text
                  or text.startswith("no "))
    why_text = str(el.get("why_text") or "").strip()
    rules["unknown_names_source"] = {
        "pass": (not is_unknown) or bool(why_text),
        "detail": ("not an unknown badge") if not is_unknown
                  else ("names the source" if why_text
                        else "does NOT name the table and the value"),
    }

    pop = str(el.get("population") or "").strip()
    rules["number_names_population"] = {
        "pass": (kind != "number") or bool(pop),
        "detail": ("not a number") if kind != "number"
                  else ("states its population" if pop
                        else "does NOT state what it counts"),
    }

    nxt = str(el.get("next_action") or "").strip()
    rules["empty_state_names_action"] = {
        "pass": (kind != "empty_state") or bool(nxt),
        "detail": ("not an empty state") if kind != "empty_state"
                  else ("names a next action" if nxt
                        else "does NOT name a next action"),
    }

    failed = [k for k in RULES if not rules[k]["pass"]]
    if not failed:
        return _result(KEEP_STATUS, keep=True, rules=rules,
                       element_key=str(el.get("element_key") or ""))
    # The FIRST failed rule names the status, in RULES order, so the status is
    # deterministic rather than dependent on dict iteration.
    status = RULE_STATUS[failed[0]]
    return _result(status, keep=False, rules=rules,
                   element_key=str(el.get("element_key") or ""),
                   why="; ".join("%s: %s" % (k, rules[k]["detail"]) for k in failed))


def assert_user_friendly(el: dict[str, Any], *,
                         terms: set[str] | None = None) -> dict[str, Any]:
    """Raise `UiNotFriendly` unless the element meets all five rules.

    The write-site gate. A caller that wants to REGISTER an element must pass
    through here, so an element that does not meet the standard cannot be stored
    and then quietly kept.
    """
    res = check_element(el, terms=terms)
    if not res["keep"]:
        raise UiNotFriendly(
            "element %r is %s — %s. A UI element that fails a rule is not a "
            "slightly worse element; it does not meet the standard."
            % (res["element_key"], res["status"], res["why"]))
    return res


def partition(elements: list[dict[str, Any]], *,
              terms: set[str] | None = None) -> dict[str, Any]:
    """Split elements into `kept` and `dropped`. NEVER downgrades.

    A dropped element carries NO `low_confidence` key, so no caller can quietly
    keep it — the same rule `measurement_scope.partition()` applies to a finding.
    """
    kept, dropped = [], []
    for el in elements:
        res = check_element(el, terms=terms)
        item = dict(el)
        item["status"] = res["status"]
        item["rules_failed"] = res["rules_failed"]
        if res["keep"]:
            kept.append(item)
        else:
            item["why"] = res["why"]
            dropped.append(item)
    return {"kept": kept, "dropped": dropped,
            "kept_n": len(kept), "dropped_n": len(dropped),
            "has_low_confidence": any("low_confidence" in d for d in dropped)}


def audit_page(page_key: str | None = None,
               db_path: Path | str = DEFAULT_DB) -> dict[str, Any]:
    """The five baselines for a page, as NUMBERS. Never raises.

    This is the function the human's question needs: not "is it friendly" but
    "which of the five rules pass, and how many of how many".
    """
    import ui_element_registry as uer

    conn = _connect(db_path)
    try:
        rows = uer.list_elements(conn, page_key)
        terms = {str(r[0]) for r in conn.execute(
            "SELECT term_key FROM terminology_registry WHERE is_active = 1")}
    finally:
        conn.close()

    per_rule: dict[str, dict[str, Any]] = {}
    for rule in RULES:
        applicable = 0
        passing = 0
        for el in rows:
            res = check_element(el, terms=terms)
            detail = res["rules"][rule]
            # A rule that does not APPLY is not counted in the denominator —
            # counting it would make the ratio about the wrong population.
            # MEASURED 2026-09-27: the first version tested only `not a `, so
            # `not an empty state` slipped through and `empty_state_names_action`
            # reported 18/18 when only 1 element is an empty state.
            d = str(detail.get("detail", ""))
            if d.startswith("not applicable") or d.startswith("not a ") \
                    or d.startswith("not an "):
                continue
            applicable += 1
            passing += 1 if detail["pass"] else 0
        kind, unit = RULE_UNIT[rule]
        failing = applicable - passing
        # THE METRIC VALUE DEPENDS ON THE KIND, and this is the defect the first
        # version shipped: a `count` rule's metric is the count of FAILURES
        # (hover-only `title=` attributes), not the count of passes. Reporting
        # `passing` for a count rule made `why_clickable` read `7/7` while its
        # target is 0 — a number about the wrong population, which is the exact
        # defect `measurement-scope` exists to catch.
        #
        # AND A RULE THAT DOES NOT APPLY HAS NO PERCENTAGE. MEASURED 2026-09-28:
        # this line divided by `applicable` WITHOUT guarding zero, so on the 5
        # pages where NO rule applies it raised `ZeroDivisionError` and killed
        # the whole audit — including the human's own
        # `http://127.0.0.1:18765/llm-tasks/question` page. The guard already
        # existed ONE LINE BELOW (`"pct": (... if applicable else None)`), which
        # is what made the defect easy to miss: the author knew the case existed
        # and guarded the wrong expression.
        #
        # `None` is the honest value: a percentage of an EMPTY population is not
        # 0% and not 100% — it is undefined, and `meets_target` must not claim it
        # passed. A rule with no applicable element is REPORTED as
        # `applicable: 0`, never silently counted as a pass.
        metric_value = (round(100.0 * passing / applicable, 1)
                        if (kind == "pct" and applicable) else
                        (failing if kind != "pct" else None))
        target = 100 if kind == "pct" else 0
        per_rule[rule] = {
            "metric_kind": kind, "metric_unit": unit,
            "metric_value": metric_value, "target": target,
            "passing": passing, "applicable": applicable, "failing": failing,
            "pct": (round(100.0 * passing / applicable, 1) if applicable else None),
            # A rule with NO applicable element cannot MEET its target: there is
            # nothing to have met it. `None` is not a pass.
            "meets_target": (metric_value == target) if applicable else None,
        }

    part = partition(rows, terms=terms)
    return {"page_key": page_key or "(all pages)", "elements": len(rows),
            "rules": per_rule,
            "kept": part["kept_n"], "dropped": part["dropped_n"],
            "dropped_keys": [d["element_key"] for d in part["dropped"]],
            "user_friendly": part["dropped_n"] == 0}


def main() -> int:
    ap = argparse.ArgumentParser(description="the UI standard")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--page", default=None)
    ap.add_argument("--check", metavar="ELEMENT_KEY")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.check:
        import ui_element_registry as uer
        conn = _connect(args.db)
        try:
            row = conn.execute(
                "SELECT * FROM ui_element_registry WHERE element_key=?",
                (args.check,)).fetchone()
            terms = {str(r[0]) for r in conn.execute(
                "SELECT term_key FROM terminology_registry WHERE is_active = 1")}
        finally:
            conn.close()
        if not row:
            print(json.dumps({"ok": False, "code": "NO_SUCH_ELEMENT",
                              "element_key": args.check}, ensure_ascii=False))
            return 1
        print(json.dumps(check_element(dict(row), terms=terms),
                         ensure_ascii=False, indent=2))
        return 0

    res = audit_page(args.page, args.db)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    print("UI STANDARD — %s" % res["page_key"])
    print("  elements: %d   kept: %d   dropped: %d   user_friendly: %s"
          % (res["elements"], res["kept"], res["dropped"], res["user_friendly"]))
    print()
    print("  %-28s %-8s %-10s %-10s %s" %
          ("rule", "kind", "value", "target", "meets"))
    for rule in RULES:
        r = res["rules"][rule]
        print("  %-28s %-8s %-10s %-10s %s" %
              (rule, r["metric_kind"], r["metric_value"], r["target"],
               r["meets_target"]))
    if res["dropped_keys"]:
        print()
        print("  DROPPED (not downgraded):")
        for k in res["dropped_keys"]:
            print("     %s" % k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
