"""Shared field_tdd_rule loader + runtime validate.

SSOT table: existing field_tdd_rule on agent.db (NOT a parallel member_db).
Law:
  - MCS declares rules (register_id + rule_json)
  - Pair QC / API / UI consume the same loader
  - gate stays never for Pair QC detect path; API may enforce on write
  - region=+CC; phone=local digits; e164=compose only
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

FAIL_BUSINESS = "business_defect"
FAIL_TRANSIENT = "transient_execution"

# Canonical membership records (import into live field_tdd_rule)
MEMBERSHIP_FIELD_TDD_RECORDS: list[dict[str, Any]] = [
    {
        "register_id": "reg_membership_member_region_2f5d7336f433",
        "system_key": "membership",
        "slice_key": "region",
        "field_name": "region",
        "depends_on": [],
        "fail_class": FAIL_BUSINESS,
        "op": "match",
        "tdd_type_code": "text",
        "rule_json": {
            "op": "match",
            "pattern": r"^\+\d{1,4}$",
            "fail_class": FAIL_BUSINESS,
            "field_kind": "region",
            "primary_key": True,
            "storage_type": "calling_code_plus",
            "example": "+86",
            "note": "calling code only: +86 / +852 / +886 / +1",
            "depends_on": [],
        },
    },
    {
        "register_id": "reg_membership_member_phone_63e3047cbe53",
        "system_key": "membership",
        "slice_key": "phone",
        "field_name": "phone",
        "depends_on": ["region"],
        "fail_class": FAIL_BUSINESS,
        "op": "local_digits_by_region",
        "tdd_type_code": "text",
        "rule_json": {
            "op": "local_digits_by_region",
            "fail_class": FAIL_BUSINESS,
            "field_kind": "phone",
            "storage_type": "local_digit_string",
            "example": "13800138000",
            "depends_on": ["region"],
            "note": "local digits only, NO leading +",
            "by_region": {
                "+86": {"pattern": r"^\d{11}$", "local_len": 11, "note": "China local 11 digits"},
                "+852": {"pattern": r"^\d{8}$", "local_len": 8, "note": "HK local 8 digits"},
                "+886": {
                    "pattern": r"^\d{8,9}$",
                    "local_len_min": 8,
                    "local_len_max": 9,
                    "note": "Taiwan local 8-9 digits",
                },
                "+1": {"pattern": r"^\d{10}$", "local_len": 10, "note": "US/CA local 10 digits"},
            },
            "default": {"pattern": r"^\d{6,15}$", "note": "generic local digits"},
            "rules": [
                {
                    "when": {"region": "+86"},
                    "op": "match_local",
                    "pattern": r"^\d{11}$",
                    "note": "China local 11 digits",
                    "fail_class": FAIL_BUSINESS,
                },
                {
                    "when": {"region": "+852"},
                    "op": "match_local",
                    "pattern": r"^\d{8}$",
                    "note": "HK local 8 digits",
                    "fail_class": FAIL_BUSINESS,
                },
                {
                    "when": {"region": "+886"},
                    "op": "match_local",
                    "pattern": r"^\d{8,9}$",
                    "note": "Taiwan local 8-9 digits",
                    "fail_class": FAIL_BUSINESS,
                },
                {
                    "when": {"region": "+1"},
                    "op": "match_local",
                    "pattern": r"^\d{10}$",
                    "note": "US/CA local 10 digits",
                    "fail_class": FAIL_BUSINESS,
                },
                {
                    "when": {"default": True},
                    "op": "match_local",
                    "pattern": r"^\d{6,15}$",
                    "note": "generic local digits only",
                    "fail_class": FAIL_BUSINESS,
                },
            ],
            "compose_with": ["region"],
            "compose_note": "e164 = region + phone compose only; do not store e164 as phone",
        },
    },
    {
        "register_id": "reg_membership_member_address_721c9058ea21",
        "system_key": "membership",
        "slice_key": "address",
        "field_name": "address",
        "depends_on": [],
        "fail_class": FAIL_BUSINESS,
        "op": "text_length_clean",
        "tdd_type_code": "text",
        "rule_json": {
            "op": "text_length_clean",
            "min_len": 3,
            "max_len": 500,
            "trim_whitespace": True,
            "allow_newline": False,
            "fail_class": FAIL_BUSINESS,
            "field_kind": "address",
            "depends_on": [],
            "note": "soft rule: only clean & length check, no strict address format regex",
        },
    },
    {
        "register_id": "reg_membership_member_name_69baf1719f7e",
        "system_key": "membership",
        "slice_key": "name",
        "field_name": "name",
        "depends_on": [],
        "fail_class": FAIL_BUSINESS,
        "op": "range_len",
        "tdd_type_code": "text",
        "rule_json": {
            "op": "range_len",
            "min": 1,
            "max": 80,
            "min_len": 1,
            "max_len": 80,
            "fail_class": FAIL_BUSINESS,
            "field_kind": "name",
            "depends_on": [],
            "rules": [
                {
                    "op": "range_len",
                    "min": 1,
                    "max": 80,
                    "fail_class": FAIL_BUSINESS,
                }
            ],
        },
    },
    {
        "register_id": "reg_membership_member_gender_32dccc4b18f4",
        "system_key": "membership",
        "slice_key": "gender",
        "field_name": "gender",
        "depends_on": [],
        "fail_class": FAIL_BUSINESS,
        "op": "in_set",
        "tdd_type_code": "text",
        "rule_json": {
            "op": "in_set",
            "values": ["M", "F", "X", "U"],
            "enum": ["M", "F", "X", "U"],
            "fail_class": FAIL_BUSINESS,
            "field_kind": "gender",
            "depends_on": [],
            "rules": [{"op": "in_set", "values": ["M", "F", "X", "U"]}],
        },
    },
    {
        "register_id": "reg_membership_member_contact_method_bf7b1d40fc4b",
        "system_key": "membership",
        "slice_key": "contact_method",
        "field_name": "contact_method",
        "depends_on": [],
        "fail_class": FAIL_BUSINESS,
        "op": "min_len",
        "tdd_type_code": "text",
        "rule_json": {
            "op": "min_len",
            "min_len": 2,
            "value": 2,
            "fail_class": FAIL_BUSINESS,
            "field_kind": "contact_method",
            "depends_on": [],
            "enum_hint": ["phone", "email", "line", "wechat"],
            "rules": [{"op": "min_len", "value": 2}],
        },
    },
]


def _json_loads(raw: Any, default: Any = None) -> Any:
    if default is None:
        default = {}
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return default
    text = raw.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _parse_depends(raw: Any) -> list[str]:
    data = _json_loads(raw, [])
    if isinstance(data, list):
        return [str(x) for x in data if str(x).strip()]
    if isinstance(data, str) and data.strip():
        return [data.strip()]
    return []


def normalize_rule_envelope(
    rule: dict[str, Any] | None,
    *,
    op: str | None = None,
    fail_class: str | None = None,
    depends_on: list[str] | None = None,
) -> dict[str, Any]:
    """Ensure rule_json has op/fail_class/depends_on at top level."""
    out = dict(rule or {})
    if op and not out.get("op"):
        out["op"] = op
    if fail_class and not out.get("fail_class"):
        out["fail_class"] = fail_class
    if depends_on is not None and "depends_on" not in out:
        out["depends_on"] = list(depends_on)
    # alias min_len/max_len <-> min/max
    if "min_len" in out and "min" not in out:
        out["min"] = out["min_len"]
    if "max_len" in out and "max" not in out:
        out["max"] = out["max_len"]
    if "min" in out and "min_len" not in out:
        out["min_len"] = out["min"]
    if "max" in out and "max_len" not in out:
        out["max_len"] = out["max"]
    if out.get("op") == "min_len" and "value" not in out and "min_len" in out:
        out["value"] = out["min_len"]
    return out


def load_field_tdd(
    conn: sqlite3.Connection,
    *,
    system_key: str | None = None,
    slice_key: str | None = None,
    field_name: str | None = None,
    register_id: str | None = None,
    tdd_rule_id: int | None = None,
    fallback_template: bool = True,
) -> dict[str, Any] | None:
    """Load one active field_tdd_rule row. Returns normalized dict or None."""
    row = None
    if tdd_rule_id is not None:
        row = conn.execute(
            """
            SELECT id, request_id, system_key, slice_key, field_name, tdd_type_code,
                   rule_json, depends_on_json, register_id, task_id, status, notes
            FROM field_tdd_rule WHERE id = ?
            """,
            (int(tdd_rule_id),),
        ).fetchone()
    if row is None and register_id:
        row = conn.execute(
            """
            SELECT id, request_id, system_key, slice_key, field_name, tdd_type_code,
                   rule_json, depends_on_json, register_id, task_id, status, notes
            FROM field_tdd_rule
            WHERE register_id = ? AND status IN ('draft', 'active')
            ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (register_id,),
        ).fetchone()
    if row is None and (slice_key or field_name):
        key = (slice_key or field_name or "").strip()
        if system_key:
            row = conn.execute(
                """
                SELECT id, request_id, system_key, slice_key, field_name, tdd_type_code,
                       rule_json, depends_on_json, register_id, task_id, status, notes
                FROM field_tdd_rule
                WHERE system_key = ? AND (slice_key = ? OR field_name = ?)
                  AND status IN ('draft', 'active')
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC
                LIMIT 1
                """,
                (system_key, key, key),
            ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT id, request_id, system_key, slice_key, field_name, tdd_type_code,
                       rule_json, depends_on_json, register_id, task_id, status, notes
                FROM field_tdd_rule
                WHERE (slice_key = ? OR field_name = ?)
                  AND status IN ('draft', 'active')
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC
                LIMIT 1
                """,
                (key, key),
            ).fetchone()

    if row is not None:
        keys = [
            "id",
            "request_id",
            "system_key",
            "slice_key",
            "field_name",
            "tdd_type_code",
            "rule_json",
            "depends_on_json",
            "register_id",
            "task_id",
            "status",
            "notes",
        ]
        item = {keys[i]: row[i] for i in range(len(keys))}
        rule = normalize_rule_envelope(_json_loads(item.get("rule_json"), {}))
        depends = _parse_depends(item.get("depends_on_json"))
        if not depends:
            depends = list(rule.get("depends_on") or [])
        rule = normalize_rule_envelope(rule, depends_on=depends)
        return {
            "id": int(item["id"]) if item.get("id") is not None else None,
            "request_id": item.get("request_id"),
            "system_key": item.get("system_key"),
            "slice_key": item.get("slice_key"),
            "field_name": item.get("field_name"),
            "tdd_type_code": item.get("tdd_type_code") or "text",
            "register_id": item.get("register_id"),
            "task_id": item.get("task_id"),
            "status": item.get("status"),
            "notes": item.get("notes"),
            "depends_on": depends,
            "op": rule.get("op"),
            "fail_class": rule.get("fail_class") or FAIL_BUSINESS,
            "rule": rule,
            "rule_json": rule,
            "source": "field_tdd_rule",
        }

    if not fallback_template:
        return None

    # template fallback (MCS built-ins / canonical records)
    key = (slice_key or field_name or "").strip().lower()
    for rec in MEMBERSHIP_FIELD_TDD_RECORDS:
        if rec["slice_key"] == key or rec["field_name"] == key:
            if system_key and rec["system_key"] != system_key:
                continue
            rule = normalize_rule_envelope(
                dict(rec["rule_json"]),
                op=rec.get("op"),
                fail_class=rec.get("fail_class"),
                depends_on=list(rec.get("depends_on") or []),
            )
            return {
                "id": None,
                "request_id": None,
                "system_key": rec["system_key"],
                "slice_key": rec["slice_key"],
                "field_name": rec["field_name"],
                "tdd_type_code": rec.get("tdd_type_code") or "text",
                "register_id": rec.get("register_id"),
                "task_id": None,
                "status": "template",
                "notes": "fallback MEMBERSHIP_FIELD_TDD_RECORDS",
                "depends_on": list(rec.get("depends_on") or []),
                "op": rule.get("op"),
                "fail_class": rule.get("fail_class") or FAIL_BUSINESS,
                "rule": rule,
                "rule_json": rule,
                "source": "canonical_record",
            }
    try:
        from managed_coding import FIELD_TDD_TEMPLATES

        tmpl = FIELD_TDD_TEMPLATES.get(key)
        if tmpl:
            rule = normalize_rule_envelope(dict(tmpl))
            return {
                "id": None,
                "system_key": system_key,
                "slice_key": key,
                "field_name": key,
                "tdd_type_code": rule.get("tdd_type_code") or "text",
                "register_id": register_id,
                "depends_on": list(rule.get("depends_on") or []),
                "op": rule.get("op"),
                "fail_class": rule.get("fail_class") or FAIL_BUSINESS,
                "rule": rule,
                "rule_json": rule,
                "source": "FIELD_TDD_TEMPLATES",
                "status": "template",
            }
    except Exception:
        pass
    return None


def _fullmatch(pattern: str, value: str) -> tuple[bool, str | None]:
    try:
        return bool(re.fullmatch(pattern, value or "")), None
    except re.error as e:
        return False, str(e)


def validate_value_against_tdd(
    value: Any,
    rule_or_loaded: dict[str, Any] | None,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Runtime validate one value. Used by API write path / tests.

    Returns {ok, value, op, errors, fail_class, cleaned?}.
    """
    context = context or {}
    loaded = rule_or_loaded or {}
    # accept either load_field_tdd result or bare rule_json
    if "rule" in loaded and isinstance(loaded.get("rule"), dict):
        rule = normalize_rule_envelope(loaded["rule"])
        op = loaded.get("op") or rule.get("op")
        fail_class = loaded.get("fail_class") or rule.get("fail_class") or FAIL_BUSINESS
        depends = loaded.get("depends_on") or rule.get("depends_on") or []
    else:
        rule = normalize_rule_envelope(loaded)
        op = rule.get("op")
        fail_class = rule.get("fail_class") or FAIL_BUSINESS
        depends = rule.get("depends_on") or []

    raw = "" if value is None else str(value)
    errors: list[str] = []
    cleaned = raw

    # dependency presence (soft check — caller should pass context)
    for dep in depends:
        if dep not in context or context.get(dep) in (None, ""):
            # only hard-fail when op needs it (phone)
            if op in ("local_digits_by_region", "match_local_by_region") and dep == "region":
                return {
                    "ok": False,
                    "value": raw,
                    "op": op,
                    "errors": [f"depends_missing:{dep}"],
                    "fail_class": fail_class,
                }

    if op == "match":
        pat = str(rule.get("pattern") or "")
        ok, err = _fullmatch(pat, raw)
        if err:
            errors.append(err)
        if not ok:
            errors.append(rule.get("note") or f"match_fail:{pat}")
        return {
            "ok": ok and not errors,
            "value": raw,
            "op": op,
            "pattern": pat,
            "errors": errors,
            "fail_class": fail_class,
        }

    if op in ("local_digits_by_region", "match_local_by_region"):
        region = context.get("region")
        # normalize region lightly
        if region is not None:
            region = str(region).strip()
            if region and not region.startswith("+") and region.isdigit():
                region = f"+{region}"
        by_region = rule.get("by_region") if isinstance(rule.get("by_region"), dict) else {}
        rules = rule.get("rules") if isinstance(rule.get("rules"), list) else []
        pat = None
        note = None
        if region and by_region.get(region):
            spec = by_region[region]
            if isinstance(spec, dict):
                pat = spec.get("pattern")
                note = spec.get("note")
        if not pat:
            for r in rules:
                if not isinstance(r, dict):
                    continue
                when = r.get("when") or {}
                if when.get("region") == region:
                    pat = r.get("pattern")
                    note = r.get("note")
                    break
                if when.get("default"):
                    pat = pat or r.get("pattern")
                    note = note or r.get("note")
        if not pat:
            default = rule.get("default") if isinstance(rule.get("default"), dict) else {}
            pat = default.get("pattern") or r"^\d{6,15}$"
            note = default.get("note") or "generic local"
        ok, err = _fullmatch(str(pat), raw)
        if err:
            errors.append(err)
        if raw and not raw.isdigit():
            ok = False
            errors.append("local_digits_only")
        if not ok:
            errors.append(note or f"phone_invalid_for_{region}")
        return {
            "ok": bool(ok) and not err,
            "value": raw,
            "op": op,
            "region": region,
            "pattern": pat,
            "errors": errors,
            "fail_class": fail_class,
            "note": note,
        }

    if op == "range_len":
        mn = int(rule.get("min_len") if rule.get("min_len") is not None else rule.get("min") or 0)
        mx = int(rule.get("max_len") if rule.get("max_len") is not None else rule.get("max") or 10**9)
        n = len(raw)
        ok = mn <= n <= mx
        if not ok:
            errors.append(f"length_{n}_not_in_{mn}_{mx}")
        return {
            "ok": ok,
            "value": raw,
            "op": op,
            "min": mn,
            "max": mx,
            "errors": errors,
            "fail_class": fail_class,
        }

    if op == "min_len":
        mn = int(rule.get("min_len") if rule.get("min_len") is not None else rule.get("value") or 0)
        ok = len(raw) >= mn
        if not ok:
            errors.append(f"min_len_{mn}")
        return {
            "ok": ok,
            "value": raw,
            "op": op,
            "min_len": mn,
            "errors": errors,
            "fail_class": fail_class,
        }

    if op == "max_len":
        mx = int(rule.get("max_len") if rule.get("max_len") is not None else rule.get("value") or 0)
        ok = len(raw) <= mx
        if not ok:
            errors.append(f"max_len_{mx}")
        return {
            "ok": ok,
            "value": raw,
            "op": op,
            "max_len": mx,
            "errors": errors,
            "fail_class": fail_class,
        }

    if op == "in_set":
        vals = [str(x) for x in (rule.get("values") or rule.get("enum") or [])]
        # case-sensitive first; also allow upper match for gender codes
        ok = raw in vals or raw.upper() in [v.upper() for v in vals]
        if not ok:
            errors.append(f"not_in_set:{vals}")
        return {
            "ok": ok,
            "value": raw if raw in vals else (raw.upper() if raw.upper() in [v.upper() for v in vals] else raw),
            "op": op,
            "values": vals,
            "errors": errors,
            "fail_class": fail_class,
        }

    if op == "text_length_clean":
        allow_nl = bool(rule.get("allow_newline"))
        text = raw if allow_nl else raw.replace("\r", " ").replace("\n", " ")
        if rule.get("trim_whitespace", True):
            text = " ".join(text.split())
        mn = int(rule.get("min_len") or rule.get("min") or 0)
        mx = int(rule.get("max_len") or rule.get("max") or 10**9)
        ok = mn <= len(text) <= mx
        if not ok:
            errors.append(f"length_{len(text)}_not_in_{mn}_{mx}")
        return {
            "ok": ok,
            "value": text,
            "cleaned": text,
            "op": op,
            "min_len": mn,
            "max_len": mx,
            "errors": errors,
            "fail_class": fail_class,
        }

    if op == "lookup":
        return {
            "ok": True,
            "value": raw,
            "op": op,
            "errors": [],
            "fail_class": fail_class,
            "note": rule.get("note") or "derived_display",
        }

    # unknown op — pass through (declare-only)
    return {
        "ok": True,
        "value": raw,
        "op": op or "none",
        "errors": [],
        "fail_class": fail_class,
        "note": "no_op_or_unsupported_pass",
    }


def ensure_address_register(
    conn: sqlite3.Connection,
    *,
    register_id: str = "reg_membership_member_address_721c9058ea21",
    system_key: str = "membership",
    commit: bool = False,
) -> dict[str, Any]:
    """Ensure address code_register row exists (optional slice; no auto task tree)."""
    row = conn.execute(
        "SELECT id, register_id, function_name, slice_key, status FROM code_register WHERE register_id = ?",
        (register_id,),
    ).fetchone()
    if row:
        return {
            "ok": True,
            "action": "exists",
            "id": int(row[0]),
            "register_id": row[1],
            "function_name": row[2],
            "slice_key": row[3],
            "status": row[4],
        }
    # avoid UNIQUE(module_name, function_name) clash
    clash = conn.execute(
        """
        SELECT register_id FROM code_register
        WHERE module_name = ? AND function_name = ?
        """,
        ("membership", "member_address"),
    ).fetchone()
    if clash:
        return {
            "ok": True,
            "action": "name_exists_other_id",
            "register_id": clash[0],
            "note": "member_address already registered under different id",
        }
    cur = conn.execute(
        """
        INSERT INTO code_register (
            register_id, module_name, function_name, system_key, slice_key,
            tacid, status, notes, source
        ) VALUES (?, 'membership', 'member_address', ?, 'address', '1.6', 'active', ?, 'field_tdd.import')
        """,
        (
            register_id,
            system_key,
            "optional address slice; Function ID for field_tdd_rule binding",
        ),
    )
    if commit:
        conn.commit()
    return {
        "ok": True,
        "action": "created",
        "id": int(cur.lastrowid),
        "register_id": register_id,
        "function_name": "member_address",
        "slice_key": "address",
        "status": "active",
    }


def import_membership_field_tdd(
    conn: sqlite3.Connection,
    *,
    ensure_address: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    """UPSERT canonical 6 rules into existing field_tdd_rule table."""
    from managed_coding import _upsert_field_tdd_rule  # local helper

    address_info = None
    if ensure_address:
        address_info = ensure_address_register(conn, commit=False)

    # map register_id -> task_id from code_register
    reg_task: dict[str, int | None] = {}
    for r in conn.execute(
        """
        SELECT register_id, slice_task_id, task_id
        FROM code_register
        WHERE system_key = 'membership'
        """
    ).fetchall():
        rid = str(r[0] or "")
        tid = r[1] if r[1] is not None else r[2]
        reg_task[rid] = int(tid) if tid is not None else None

    updated: list[dict[str, Any]] = []
    for rec in MEMBERSHIP_FIELD_TDD_RECORDS:
        rid = rec.get("register_id")
        # if address register ended up under different id, rebind
        if rec["slice_key"] == "address" and address_info and address_info.get("register_id"):
            rid = address_info["register_id"]
        rule = normalize_rule_envelope(
            dict(rec["rule_json"]),
            op=rec.get("op"),
            fail_class=rec.get("fail_class"),
            depends_on=list(rec.get("depends_on") or []),
        )
        tid = reg_task.get(str(rid)) if rid else None
        tdd_id = _upsert_field_tdd_rule(
            conn,
            request_id=None,
            system_key=str(rec["system_key"]),
            slice_key=str(rec["slice_key"]),
            field_name=str(rec["field_name"]),
            tdd_type_code=str(rec.get("tdd_type_code") or "text"),
            rule=rule,
            register_id=str(rid) if rid else None,
            task_id=tid,
            notes=f"canonical import: {rec.get('op')} / {rule.get('note') or rec['slice_key']}",
        )
        updated.append(
            {
                "tdd_id": tdd_id,
                "slice_key": rec["slice_key"],
                "register_id": rid,
                "op": rule.get("op"),
                "depends_on": rule.get("depends_on") or [],
                "task_id": tid,
            }
        )
    if commit:
        conn.commit()
    return {
        "ok": True,
        "gate": "never",
        "pipeline": "D_managed_coding",
        "imported_n": len(updated),
        "rows": updated,
        "address_register": address_info,
        "table": "field_tdd_rule",
        "db_law": "existing agent.db field_tdd_rule — no parallel member_db schema",
    }


def run_selftest(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Offline checks for loader + validate + import."""
    from db_schema import ensure_schema, get_db_path

    own = conn is None
    if own:
        path = get_db_path(None)
        ensure_schema(path)
        conn = sqlite3.connect(path)
    assert conn is not None
    steps: list[dict[str, Any]] = []
    try:
        imp = import_membership_field_tdd(conn, ensure_address=True, commit=True)
        assert imp.get("ok") and imp.get("imported_n") == 6, imp
        steps.append({"step": "import", "ok": True, "n": imp["imported_n"]})

        region = load_field_tdd(conn, system_key="membership", slice_key="region")
        assert region and region.get("register_id", "").startswith("reg_"), region
        assert region["op"] == "match", region
        vr = validate_value_against_tdd("+86", region)
        assert vr["ok"], vr
        vr_bad = validate_value_against_tdd("CN", region)
        assert not vr_bad["ok"], vr_bad
        steps.append({"step": "region_validate", "ok": True})

        phone = load_field_tdd(conn, system_key="membership", slice_key="phone")
        assert phone and phone["op"] == "local_digits_by_region", phone
        assert "region" in (phone.get("depends_on") or []), phone
        vp = validate_value_against_tdd(
            "13800138000", phone, context={"region": "+86"}
        )
        assert vp["ok"], vp
        vp_bad = validate_value_against_tdd(
            "1380013800", phone, context={"region": "+86"}
        )
        assert not vp_bad["ok"], vp_bad
        vp_dep = validate_value_against_tdd("13800138000", phone, context={})
        assert not vp_dep["ok"], vp_dep
        steps.append({"step": "phone_validate", "ok": True})

        name = load_field_tdd(conn, system_key="membership", slice_key="name")
        assert validate_value_against_tdd("Ada", name)["ok"]
        gender = load_field_tdd(conn, system_key="membership", slice_key="gender")
        assert validate_value_against_tdd("M", gender)["ok"]
        cm = load_field_tdd(conn, system_key="membership", slice_key="contact_method")
        assert validate_value_against_tdd("sms", cm)["ok"]
        addr = load_field_tdd(conn, system_key="membership", slice_key="address")
        va = validate_value_against_tdd("  hello   world  ", addr)
        assert va["ok"] and va["value"] == "hello world", va
        steps.append({"step": "other_fields", "ok": True})

        # register_id load path
        by_reg = load_field_tdd(
            conn, register_id="reg_membership_member_phone_63e3047cbe53"
        )
        assert by_reg and by_reg["slice_key"] == "phone", by_reg
        steps.append({"step": "load_by_register_id", "ok": True, "id": by_reg.get("id")})

        return {
            "ok": True,
            "gate": "never",
            "steps": steps,
            "import": imp,
            "notes": "shared loader + validate; existing field_tdd_rule table",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "steps": steps,
        }
    finally:
        if own and conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    from db_schema import ensure_schema, get_db_path

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["selftest"]
    cmd = args[0].strip().lower()
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ns, _ = ap.parse_known_args(args[1:])
    path = get_db_path(ns.db)
    ensure_schema(path)
    conn = sqlite3.connect(path)
    try:
        if cmd in ("selftest", "test"):
            out = run_selftest(conn)
        elif cmd in ("import", "upsert", "seed-tdd"):
            out = import_membership_field_tdd(conn, commit=True)
        elif cmd in ("load",):
            ap2 = argparse.ArgumentParser()
            ap2.add_argument("--system", default="membership")
            ap2.add_argument("--field", required=True)
            ap2.add_argument("--register-id", default=None)
            ns2 = ap2.parse_args(args[1:])
            out = load_field_tdd(
                conn,
                system_key=ns2.system,
                slice_key=ns2.field,
                register_id=ns2.register_id,
            )
        else:
            print("usage: field_tdd.py selftest|import|load --field phone", file=sys.stderr)
            return 2
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if (out or {}).get("ok", True) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
