# -*- coding: utf-8 -*-
"""prompt_dimension.py — multi-dimensional prompt SSOT (composition, not duplication).

THE PROBLEM THIS SOLVES
-----------------------
`skill_prompt_ssot` already had a `prompt_key` column and a
`UNIQUE (skill_key, prompt_key, version_label)` constraint — but every row used
`prompt_key='main'` (measured: 21 rows, 1 distinct value). So the axis existed
and carried no information: to try a different wording you had to write a whole
separate prompt document, and the only way to compare them was by hand.

This module makes the axis meaningful. A prompt is composed from named
DIMENSIONS:

    wording   x  negation  x  output   x  context   ...  ->  one question

Each axis holds a small set of alternative fragments. A specific combination
SUBSTITUTES the fragments into one template. Nothing is duplicated: the rules
live once per axis value, and every combination is reproducible from its keys.

WHY THIS MATTERS FOR "get to 100%"
----------------------------------
Measured in this workspace: the active strict prompt stacked 8 rules that were
almost all NEGATIONS ("does NOT count", "never accepted", "FAIL", "Ignore ...
NOT a detection boundary") and the 7B-VL collapsed to answering NO on every
single case — 0% recall on hits, 70% accuracy that was pure majority-class
reward. Changing wording by hand did not reveal that; measuring the axes does.
The negation axis below deliberately INCLUDES the bad stacked variant, because a
dimension registry that only lists good values cannot show you which one is
costing you the misses.

DESIGN RULES
------------
1. A combination is identified by its `combo_key`, a canonical sorted string of
   `dim=value` pairs. Same values -> same key, always. That key is the
   `prompt_key` stored in `skill_prompt_ssot`.
2. `compose_prompt()` REFUSES unknown dimensions and inactive/absent values. It
   never leaves a `{{dim:x}}` placeholder in the output — a silently unsubstituted
   placeholder would reach the model as literal text and quietly ruin the run.
3. The registry is data, not code: values can be added/deprecated without
   touching the composer.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB = Path(__file__).resolve().parent / "agent.db"

SLOT_RE = re.compile(r"\{\{dim:([a-zA-Z0-9_]+)\}\}")

DDL = """
CREATE TABLE IF NOT EXISTS prompt_dimension (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key   TEXT NOT NULL,
    dim_key     TEXT NOT NULL,
    dim_name    TEXT,
    value_key   TEXT NOT NULL,
    value_text  TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (skill_key, dim_key, value_key)
);
CREATE INDEX IF NOT EXISTS idx_prompt_dimension_skill
  ON prompt_dimension (skill_key, dim_key, is_active, sort_order);

CREATE TABLE IF NOT EXISTS prompt_combo (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key     TEXT NOT NULL,
    combo_key     TEXT NOT NULL,
    prompt_key    TEXT NOT NULL,
    axes_json     TEXT NOT NULL,
    template_text TEXT,
    status        TEXT NOT NULL DEFAULT 'candidate',
    accuracy_pct        REAL,
    balanced_accuracy_pct REAL,
    train_ba_pct  REAL,
    heldout_ba_pct REAL,
    train_ba_min  REAL,
    train_ba_max  REAL,
    hold_ba_min   REAL,
    hold_ba_max   REAL,
    seed_runs     INTEGER,
    hold_perfect  INTEGER,
    hold_discriminating INTEGER,
    best_streak   INTEGER,
    answer_classes TEXT,
    run_id        TEXT,
    overfit       INTEGER NOT NULL DEFAULT 0,
    promoted      INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (skill_key, combo_key)
);

CREATE TABLE IF NOT EXISTS prompt_dimension_state (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key   TEXT NOT NULL,
    dim_key     TEXT NOT NULL,
    dim_name    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (skill_key, dim_key)
);
"""


class DimensionError(ValueError):
    """Raised when a combination names a dimension/value that does not exist."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_col(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table if missing (idempotent).

    CREATE TABLE IF NOT EXISTS does not add columns to a table that already
    exists, so a schema change silently does nothing on an established DB.
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if column not in cols:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))
    except Exception:
        pass


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    # Seed-range columns. Added after the first sweep, because storing only a
    # single train figure let the UI show the certified winner's train score as
    # 100.0 when across seeds it actually ranged 71-92%. A single number implied
    # a precision the measurement did not have.
    for col, ddl in (
        ("train_ba_min", "REAL"),
        ("train_ba_max", "REAL"),
        ("hold_ba_min", "REAL"),
        ("hold_ba_max", "REAL"),
        ("seed_runs", "INTEGER"),
        ("hold_perfect", "INTEGER"),
        ("hold_discriminating", "INTEGER"),
    ):
        _ensure_col(conn, "prompt_combo", col, ddl)
    conn.commit()


# ------------------------------------------------------------------ registry

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
    is_active: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    ensure_tables(conn)
    row = conn.execute(
        "SELECT id FROM prompt_dimension WHERE skill_key=? AND dim_key=? AND value_key=?",
        (skill_key, dim_key, value_key),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE prompt_dimension SET value_text=?, dim_name=COALESCE(?,dim_name),
               description=?, sort_order=?, is_active=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (value_text, dim_name, description, sort_order, 1 if is_active else 0,
             row["id"]),
        )
        action = "updated"
    else:
        conn.execute(
            """INSERT INTO prompt_dimension
               (skill_key, dim_key, dim_name, value_key, value_text, description,
                sort_order, is_active)
               VALUES (?,?,?,?,?,?,?,?)""",
            (skill_key, dim_key, dim_name, value_key, value_text, description,
             sort_order, 1 if is_active else 0),
        )
        action = "inserted"
    conn.execute(
        """INSERT INTO prompt_dimension_state (skill_key, dim_key, dim_name, is_active, sort_order)
           VALUES (?,?,?,1,?)
           ON CONFLICT(skill_key, dim_key) DO UPDATE SET
             dim_name=COALESCE(excluded.dim_name, prompt_dimension_state.dim_name),
             sort_order=excluded.sort_order""",
        (skill_key, dim_key, dim_name, sort_order),
    )
    if commit:
        conn.commit()
    return {"ok": True, "action": action, "dim_key": dim_key, "value_key": value_key}


def registry(
    conn: sqlite3.Connection, skill_key: str, *, include_inactive: bool = False
) -> dict[str, dict[str, Any]]:
    """Return {dim_key: {dim_name, sort_order, values: {value_key: {...}}}}."""
    ensure_tables(conn)
    sql = ("SELECT * FROM prompt_dimension WHERE skill_key=? "
           + ("" if include_inactive else "AND is_active=1 ")
           + "ORDER BY sort_order, dim_key, value_key")
    out: dict[str, dict[str, Any]] = {}
    for r in conn.execute(sql, (skill_key,)):
        d = out.setdefault(r["dim_key"], {
            "dim_key": r["dim_key"],
            "dim_name": r["dim_name"] or r["dim_key"],
            "sort_order": r["sort_order"],
            "values": {},
        })
        d["values"][r["value_key"]] = {
            "value_key": r["value_key"],
            "value_text": r["value_text"],
            "description": r["description"],
            "sort_order": r["sort_order"],
            "is_active": bool(r["is_active"]),
        }
    # drop dimensions with no values at all
    return {k: v for k, v in out.items() if v["values"]}


def dimensions(conn: sqlite3.Connection, skill_key: str) -> list[str]:
    return list(registry(conn, skill_key).keys())


# ---------------------------------------------------------------- combo keys

def combo_key(values: dict[str, str]) -> str:
    """Canonical, order-independent key for a combination.

    Sorting the pairs is what makes this order-independent: callers build the
    dict in whatever order they discovered the axes, and still land on the same
    key — which is what lets a combo be looked up, stored and re-found later.
    """
    parts = ["%s=%s" % (k, values[k]) for k in sorted(values)]
    return "|".join(parts)


def combo_key_short(values: dict[str, str]) -> str:
    """Filesystem/URL-safe short key (used as prompt_key)."""
    return "c" + hashlib.sha1(combo_key(values).encode("utf-8")).hexdigest()[:10]


def expand_combos(
    conn: sqlite3.Connection,
    skill_key: str,
    *,
    axes: dict[str, Iterable[str]] | None = None,
) -> list[dict[str, str]]:
    """Cartesian product of the registry (or of an explicit `axes` filter).

    `axes={"negation": ["none"], "output": ["simple"]}` restricts a dimension;
    dimensions not mentioned use ALL their active values.
    """
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


# ---------------------------------------------------------------- composition

def compose_prompt(
    conn: sqlite3.Connection,
    skill_key: str,
    values: dict[str, str],
    *,
    template: str,
    strict: bool = True,
) -> str:
    """Substitute {{dim:X}} slots with the selected values.

    `strict=True` (default) REFUSES:
      * a dimension present in `values` that is not registered
      * a value not registered for its dimension
      * a template slot with no supplied value
      * a leftover `{{dim:...}}` in the output
    Every one of those would otherwise reach the model as literal text.
    """
    reg = registry(conn, skill_key)
    slots = set(SLOT_RE.findall(template))

    if strict and not slots:
        raise DimensionError(
            "template has no {{dim:...}} slots; nothing to compose. A template "
            "without slots cannot be varied, so composing it would silently "
            "return the same prompt for every combination."
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

    leftover = SLOT_RE.findall(out)
    if strict and leftover:
        raise DimensionError(
            "unsubstituted slot(s) %s would be sent to the model verbatim" % leftover
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
    ck = combo_key(values)
    return {
        "combo_key": ck,
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
    ensure_tables(conn)
    conn.execute(
        """INSERT INTO prompt_combo
           (skill_key, combo_key, prompt_key, axes_json, template_text, status)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(skill_key, combo_key) DO UPDATE SET
             prompt_key=excluded.prompt_key,
             axes_json=excluded.axes_json,
             template_text=excluded.template_text,
             updated_at=CURRENT_TIMESTAMP""",
        (skill_key, combo["combo_key"], combo["prompt_key"],
         json.dumps(combo["axes"], ensure_ascii=False), combo["prompt_text"], status),
    )
    if commit:
        conn.commit()
    return {"ok": True, "combo_key": combo["combo_key"],
            "prompt_key": combo["prompt_key"]}


# --------------------------------------------------- mouse_spot dimension set

MOUSE_SPOT_TEMPLATE = """You are a visual inspector for Mouse Spot Helper.

{{dim:context}}

Target icon: {{target_name}}

{{dim:criterion}}

{{dim:negation}}

{{dim:output}}
"""

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
            "desc": "One positive rule, one negative rule. No boundary jargon.",
        },
        "strict_boundary": {
            "name": "criterion",
            "text": ("PASS condition ONLY: the red crosshair must lie within the "
                     "physical pixel boundary of the target icon itself."),
            "desc": "The original wording. Uses 'physical pixel boundary'.",
        },
        "presence_gate": {
            "name": "criterion",
            "text": ("First confirm the picker is on screen. If it is not, report "
                     "root_cause=picker_not_open and answer UNKNOWN instead of "
                     "judging position. If it is on screen, PASS (YES) when the "
                     "crosshair sits inside the target icon's own picture."),
            "desc": "Adds the pre-judgement presence gate (absence beats geometry).",
        },
    },
    "negation": {
        "none": {
            "name": "negation",
            "text": "",
            "desc": "No prohibitions at all.",
        },
        "single": {
            "name": "negation",
            "text": "Being merely near the icon is not a hit.",
            "desc": "One short prohibition.",
        },
        "stack": {
            "name": "negation",
            "text": ("Hard rules: being close, pointing toward, or inside the "
                     "large blue preview circle does NOT count as PASS. If the "
                     "crosshair lands on any other icon, even an adjacent one, "
                     "return FAIL. Proximity is never accepted. Ignore the blue "
                     "circle entirely; it is NOT a detection boundary."),
            "desc": ("The original stacked prohibitions. KEPT DELIBERATELY so the "
                     "sweep can MEASURE the cost of this axis instead of assuming "
                     "it is harmless."),
        },
    },
    "output": {
        "simple": {
            "name": "output",
            "text": ("Answer in this exact format:\n"
                     "Result: [YES / NO]\n"
                     "Reason: one short sentence naming what the crosshair is on."),
            "desc": "Two-way answer.",
        },
        "with_unknown": {
            "name": "output",
            "text": ("Answer in this exact format:\n"
                     "Result: [YES / NO / UNKNOWN]\n"
                     "Reason: one short sentence naming what the crosshair is on."),
            "desc": "Allows UNKNOWN, needed by the presence_gate criterion.",
        },
    },
}


def seed_mouse_spot(conn: sqlite3.Connection, skill_key: str = "mouse_spot_verify",
                    *, commit: bool = True) -> dict[str, Any]:
    ensure_tables(conn)
    n = 0
    for dim_key, values in MOUSE_SPOT_VALUES.items():
        for order, (value_key, spec) in enumerate(values.items()):
            upsert_dimension(
                conn, skill_key=skill_key, dim_key=dim_key, value_key=value_key,
                value_text=spec["text"], dim_name=spec["name"],
                description=spec["desc"], sort_order=order, commit=False,
            )
            n += 1
    if commit:
        conn.commit()
    return {"ok": True, "skill_key": skill_key, "values": n}


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    ap.add_argument("--skill", default="mouse_spot_verify")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("seed", help="seed the mouse_spot dimension registry")
    sub.add_parser("registry", help="show registered dimensions and values")
    ex = sub.add_parser("expand", help="enumerate combinations")
    ex.add_argument("--limit", type=int, default=0)
    cp = sub.add_parser("compose", help="compose one combination")
    cp.add_argument("assignments", nargs="+", help="dim=value pairs")
    cp.add_argument("--out", default=None)

    args = ap.parse_args(argv)
    conn = _connect(args.db)
    try:
        if args.cmd == "seed":
            print(json.dumps(seed_mouse_spot(conn, args.skill), ensure_ascii=False))
            return 0
        if args.cmd == "registry":
            reg = registry(conn, args.skill)
            for dim_key in sorted(reg):
                d = reg[dim_key]
                print("%s (%s)" % (dim_key, d["dim_name"]))
                for vk, vv in sorted(d["values"].items()):
                    print("   %-18s %s" % (vk, (vv["description"] or "")[:70]))
            return 0
        if args.cmd == "expand":
            combos = expand_combos(conn, args.skill)
            print("total combinations: %d" % len(combos))
            for c in (combos[: args.limit] if args.limit else combos):
                print("  %s  %s" % (combo_key_short(c), combo_key(c)))
            return 0
        if args.cmd == "compose":
            values: dict[str, str] = {}
            for a in args.assignments:
                if "=" not in a:
                    print("bad assignment %r (want dim=value)" % a)
                    return 2
                k, v = a.split("=", 1)
                values[k] = v
            combo = compose_combo(conn, args.skill, values,
                                  template=MOUSE_SPOT_TEMPLATE)
            if args.out:
                Path(args.out).write_text(combo["prompt_text"], encoding="utf-8")
                print("wrote %s" % args.out)
            print("combo_key  : %s" % combo["combo_key"])
            print("prompt_key : %s" % combo["prompt_key"])
            print("sha256     : %s" % combo["sha256"])
            print("-" * 68)
            print(combo["prompt_text"])
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
