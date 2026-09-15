"""Pipeline E — Pair QC dual-path detect (MCP SSOT 🆚 UI evidence).

Law:
  detect ≠ fix
  gate = never (does not flip schema_qc match_ok)
  normalize before compare (anti false-positive)
  case packs both evidences + register_id + tdd_rule_id
  region = +CC (+86); phone = local only (13800138000); e164 = compose
  normalize is a pure function (same raw → same norm always)
  raw never rewritten; fail_class: business_defect | transient_execution

Level-1:
  offline/manual values + optional UI screenshot evidence
  pair_value_ssot seed cache for MCP path
  phone/region normalize + TDD pattern check

See docs/plan_pair_qc.md · docs/ssot_member_phone_region.md
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

PIPELINE_ID = "E_pair_qc"
GATE_POLICY = "never"
FAULT_TYPE = "pair_qc_mismatch"
OPTION_CODE = "pair_qc_mismatch"
ACTION_PAIR = "qc.pair_verify"
FAIL_BUSINESS = "business_defect"
FAIL_TRANSIENT = "transient_execution"

WHY = {
    "P1": "API success can hide UI render bugs — need dual independent paths",
    "P2": "Normalize before compare or phone format noise floods cases",
    "P3": "Fail must open case with both evidences + register_id + tdd_rule_id",
    "P4": "Detect only — never claim auto-fix",
    "P5": "Never flip Pipeline A schema match_ok",
    "P6": "region=+CC primary; phone=local digits; e164=compose; ISO2 display only",
    "P7": "Normalize is pure; raw archived untouched",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_loads(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default if default is not None else {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return default if default is not None else {}


def contracts_doc() -> dict[str, Any]:
    return {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "phase": "L1_detect",
        "why": WHY,
        "flow": [
            "resolve register_id + field + tdd_rule",
            "normalize mcp + ui (TDD-aware)",
            "fetch mcp ssot (seed/manual/L1)",
            "fetch ui extract (manual + optional frame)",
            "compare norms",
            "write pair_qc_run",
            "on fail → fault_event case + facts + optional qc.pair_verify task",
        ],
        "tables": ["pair_qc_run", "pair_value_ssot", "vision_asset", "fault_event"],
        "reuses": ["code_register", "field_tdd_rule", "dev_task"],
        "not": [
            "schema hard gate",
            "auto-fix UI/backend",
            "raw string compare without normalize",
            "MCS ontology rewrite",
        ],
        "score_note": "detect layer only; repair is separate",
        "false_positive_guard": (
            "region=+CC (e.g. +86); phone=local digits (e.g. 13800138000); "
            "e164=compose only; normalize is pure; raw never rewritten"
        ),
        "fail_class": [FAIL_BUSINESS, FAIL_TRANSIENT],
        "primary_key": "region_calling_code",
        "phone_storage": "local_digit_string",
        "ssot_doc": "docs/ssot_member_phone_region.md",
    }


# ---------------------------------------------------------------------------
# Normalize (P2) — split model: region=+CC · phone=local digits
# Law: +86 is region NOT phone; phone example 13800138000
# e164 compose = region_norm + phone_norm (detect only)
# ---------------------------------------------------------------------------

_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)\.]+")

# Longest-first calling codes (digits, no +)
CALLING_CODES: tuple[str, ...] = (
    "886",
    "852",
    "853",
    "86",
    "81",
    "82",
    "65",
    "1",
)

CC_TO_ISO2: dict[str, str] = {
    "86": "CN",
    "852": "HK",
    "853": "MO",
    "886": "TW",
    "1": "US",
    "81": "JP",
    "82": "KR",
    "65": "SG",
}
ISO2_TO_CC: dict[str, str] = {
    "CN": "86",
    "HK": "852",
    "MO": "853",
    "TW": "886",
    "US": "1",
    "CA": "1",
    "JP": "81",
    "KR": "82",
    "SG": "65",
    "CHINA": "86",
    "HONGKONG": "852",
    "HONG_KONG": "852",
    "TAIWAN": "886",
    "USA": "1",
    "UNITED STATES": "1",
}
CC_TO_COUNTRY: dict[str, str] = {
    "86": "China",
    "852": "Hong Kong",
    "853": "Macau",
    "886": "Taiwan",
    "1": "United States",
    "81": "Japan",
    "82": "Korea",
    "65": "Singapore",
}
REGION_LOCAL_LEN: dict[str, tuple[int, int]] = {
    "86": (11, 11),
    "852": (8, 8),
    "886": (8, 9),
    "1": (10, 10),
}

# Back-compat aliases used by older call sites
CC_TO_REGION = CC_TO_ISO2
REGION_TO_CC = ISO2_TO_CC
CC_LOCAL_LEN = REGION_LOCAL_LEN


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def detect_calling_code(digits: str) -> str | None:
    d = digits or ""
    for cc in CALLING_CODES:
        if d.startswith(cc):
            return cc
    return None


def iso2_from_region(region_norm: str | None) -> str | None:
    if not region_norm:
        return None
    cc = _digits_only(str(region_norm).lstrip("+"))
    return CC_TO_ISO2.get(cc)


def country_from_region(region_norm: str | None) -> str | None:
    if not region_norm:
        return None
    cc = _digits_only(str(region_norm).lstrip("+"))
    return CC_TO_COUNTRY.get(cc)


def region_code_from_cc(cc: str | None) -> str | None:
    """ISO2 from CC digits — display only."""
    if not cc:
        return None
    return CC_TO_ISO2.get(str(cc).lstrip("+"))


def region_code_from_phone(norm_or_raw: str | None) -> str | None:
    """Best-effort ISO2 from a phone raw/norm (legacy helper)."""
    if not norm_or_raw:
        return None
    sp = split_phone_raw(norm_or_raw)
    if sp.get("region"):
        return iso2_from_region(sp["region"])
    return None


def compose_e164(region_norm: str | None, phone_local: str | None) -> str:
    """Pure: region(+CC) + local digits -> +CClocal."""
    r = (region_norm or "").strip()
    p = _digits_only(phone_local or "")
    if not r and not p:
        return ""
    if not r:
        return p
    cc = _digits_only(r.lstrip("+"))
    if not cc:
        return p
    if not p:
        return f"+{cc}"
    return f"+{cc}{p}"


def split_phone_raw(raw: Any) -> dict[str, Any]:
    """If raw embeds +CC, split into region + local. Pure."""
    raw_text = "" if raw is None else str(raw)
    text = raw_text.strip().replace("＋", "+")
    cleaned = _PHONE_STRIP_RE.sub("", text)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    had_plus = cleaned.startswith("+")
    digits = _digits_only(cleaned)
    out: dict[str, Any] = {
        "raw": raw_text,
        "had_plus": had_plus,
        "digits": digits,
        "region": None,
        "local": None,
        "split": False,
    }
    if not digits:
        return out
    if had_plus:
        cc = detect_calling_code(digits)
        if cc:
            out["region"] = f"+{cc}"
            out["local"] = digits[len(cc) :]
            out["split"] = True
            return out
    for cand in CALLING_CODES:
        if cand == "1":
            continue
        if not digits.startswith(cand):
            continue
        local = digits[len(cand) :]
        lo_hi = REGION_LOCAL_LEN.get(cand)
        if lo_hi and lo_hi[0] <= len(local) <= lo_hi[1]:
            out["region"] = f"+{cand}"
            out["local"] = local
            out["split"] = True
            return out
    out["local"] = digits
    return out


def normalize_region(raw: Any) -> dict[str, Any]:
    """region = calling code +CC (e.g. +86). NOT the phone local number."""
    raw_text = "" if raw is None else str(raw)
    text = raw_text.strip().replace("＋", "+")
    errors: list[str] = []
    if not text:
        return {
            "raw": raw_text,
            "norm": "",
            "ok": False,
            "errors": ["empty_region"],
            "field_kind": "region",
            "iso2": None,
            "country": None,
            "primary_key": True,
        }
    upper = text.upper()
    if upper in ISO2_TO_CC:
        cc = ISO2_TO_CC[upper]
        norm = f"+{cc}"
        return {
            "raw": raw_text,
            "norm": norm,
            "ok": True,
            "errors": [],
            "field_kind": "region",
            "iso2": CC_TO_ISO2.get(cc),
            "country": CC_TO_COUNTRY.get(cc),
            "calling_code": norm,
            "primary_key": True,
            "cc_source": "iso2_or_alias",
        }
    cleaned = _PHONE_STRIP_RE.sub("", text)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    digits = _digits_only(cleaned)
    if not digits:
        return {
            "raw": raw_text,
            "norm": "",
            "ok": False,
            "errors": ["no_digits"],
            "field_kind": "region",
            "iso2": None,
            "country": None,
            "primary_key": True,
        }
    cc = detect_calling_code(digits)
    if cleaned.startswith("+") and cc:
        local = digits[len(cc) :]
        norm = f"+{cc}"
        if local and len(local) >= 6:
            errors.append("stripped_local_from_region_input")
    elif cc and len(digits) == len(cc):
        norm = f"+{cc}"
    elif digits in set(CALLING_CODES):
        norm = f"+{digits}"
        cc = digits
    elif len(digits) <= 4:
        norm = f"+{digits}"
        cc = digits
    else:
        if cc:
            norm = f"+{cc}"
            errors.append("region_input_looks_like_full_number")
        else:
            norm = f"+{digits}"
            cc = digits
            errors.append("unknown_region_shape")
    iso2 = CC_TO_ISO2.get(cc) if cc else None
    country = CC_TO_COUNTRY.get(cc) if cc else None
    ok = bool(re.match(r"^\+\d{1,4}$", norm or ""))
    if not ok:
        errors.append("region_not_plus_digits")
    return {
        "raw": raw_text,
        "norm": norm,
        "ok": ok,
        "errors": errors,
        "field_kind": "region",
        "iso2": iso2,
        "country": country,
        "calling_code": norm,
        "primary_key": True,
    }


def normalize_phone(
    raw: Any,
    *,
    region: str | None = None,
    default_cc: str | None = None,
) -> dict[str, Any]:
    """phone = LOCAL digits only (e.g. 13800138000). Pure.

    +86 is region, NOT phone. If raw embeds +CC, split and keep local as norm.
    Optional region arg supplies +CC for length policy and e164 compose.
    """
    raw_text = "" if raw is None else str(raw)
    text = raw_text.strip()
    errors: list[str] = []
    if not text:
        return {
            "raw": raw_text,
            "norm": "",
            "ok": False,
            "errors": ["empty_phone"],
            "field_kind": "phone",
            "local": "",
            "region_norm": None,
            "e164": "",
            "iso2": None,
        }

    region_norm = None
    region_src = None
    if region is not None and str(region).strip() != "":
        rn = normalize_region(region)
        region_norm = rn.get("norm") or None
        region_src = "arg"
    if default_cc and not region_norm:
        rn = normalize_region(default_cc)
        region_norm = rn.get("norm") or None
        region_src = "default_cc"

    sp = split_phone_raw(raw_text)
    if sp.get("split") and sp.get("local") is not None:
        local = sp["local"] or ""
        if not region_norm and sp.get("region"):
            region_norm = sp["region"]
            region_src = "split_from_phone_raw"
        elif region_norm and sp.get("region") and sp["region"] != region_norm:
            errors.append("region_arg_conflicts_phone_prefix")
            region_norm = sp["region"]
            region_src = "split_wins_over_arg"
    else:
        local = sp.get("local") or _digits_only(
            _PHONE_STRIP_RE.sub("", text.replace("＋", "+"))
        )

    if re.search(r"[A-Za-z]", text):
        errors.append("letters_not_allowed")
    if re.search(r"[^\d\s\-\(\)\.\+＋]", text):
        errors.append("symbols_or_emoji")

    norm = local  # LOCAL only
    if region_norm:
        cc = _digits_only(region_norm.lstrip("+"))
        lo_hi = REGION_LOCAL_LEN.get(cc)
        if lo_hi:
            lo, hi = lo_hi
            if not (lo <= len(norm) <= hi):
                errors.append(
                    f"region_{region_norm}_local_len_{len(norm)}_expected_{lo}_{hi}"
                )
    elif len(norm) == 11 and norm[:1] == "1" and len(norm) > 1 and norm[1] in "3456789":
        errors.append("region_missing_cn_mobile_heuristic")
        region_norm = "+86"
        region_src = region_src or "cn_mobile_heuristic"

    e164 = compose_e164(region_norm, norm)
    ok = bool(norm) and str(norm).isdigit()
    return {
        "raw": raw_text,
        "norm": norm,
        "ok": ok,
        "errors": errors,
        "field_kind": "phone",
        "local": norm,
        "region_norm": region_norm,
        "region_source": region_src,
        "calling_code": region_norm,
        "e164": e164,
        "iso2": iso2_from_region(region_norm),
        "country": country_from_region(region_norm),
        "storage_type": "digit_string",
        "note": "phone=local only; region=+CC; e164=compose",
    }


def normalize_country(raw: Any, *, region: str | None = None) -> dict[str, Any]:
    raw_text = "" if raw is None else str(raw)
    text = raw_text.strip()
    if not text and region:
        rn = normalize_region(region)
        name = rn.get("country") or ""
        return {
            "raw": raw_text,
            "norm": name,
            "ok": bool(name),
            "errors": [] if name else ["empty_country"],
            "field_kind": "country",
            "region_norm": rn.get("norm"),
            "source": "lookup_region",
        }
    errors: list[str] = []
    if not text:
        errors.append("empty_country")
    elif text.isdigit():
        errors.append("country_must_be_text_not_int")
    return {
        "raw": raw_text,
        "norm": text,
        "ok": bool(text) and not text.isdigit(),
        "errors": errors,
        "field_kind": "country",
    }


def normalize_name(raw: Any) -> dict[str, Any]:
    raw_text = "" if raw is None else str(raw)
    norm = raw_text.strip()
    errors: list[str] = []
    if not norm:
        errors.append("empty_name")
    elif len(norm) > 80:
        errors.append("name_len_gt_80")
    return {
        "raw": raw_text,
        "norm": norm,
        "ok": bool(norm) and len(norm) <= 80,
        "errors": errors,
        "field_kind": "name",
    }


def normalize_value(
    field_name: str,
    raw: Any,
    *,
    region: str | None = None,
    rule: dict[str, Any] | None = None,
    default_cc: str | None = None,
) -> dict[str, Any]:
    """TDD-aware normalize. Pure. phone=local; region=+CC."""
    fn = (field_name or "").strip().lower()
    rule = rule or {}
    kind = str(rule.get("value_type") or rule.get("tdd_type_code") or fn)

    if fn in ("region", "calling_code", "dial_code") or kind in (
        "region",
        "calling_code",
    ):
        return normalize_region(raw)
    if fn in ("phone", "mobile", "tel", "phone_local", "local_phone") or "phone" in fn:
        return normalize_phone(raw, region=region, default_cc=default_cc)
    if fn in ("country", "country_name") or kind == "country":
        return normalize_country(raw, region=region)
    if fn in ("name", "member_name", "full_name") or kind == "name":
        return normalize_name(raw)

    raw_text = "" if raw is None else str(raw)
    text = raw_text.strip()
    enum_vals = rule.get("enum") or rule.get("enum_hint")
    norm = text
    if enum_vals and len(text) <= 4:
        norm = text.upper()
    return {
        "raw": raw_text,
        "norm": norm,
        "ok": True,
        "errors": [],
        "field_kind": fn or "text",
    }


def _match_pattern(norm: str, pat: str) -> tuple[bool, str | None]:
    try:
        return bool(re.match(pat, norm or "")), None
    except re.error as e:
        return False, str(e)


def apply_tdd_pattern_check(
    norm: str,
    *,
    rule: dict[str, Any] | None,
    region: str | None = None,
    calling_code: str | None = None,
    field_kind: str | None = None,
) -> dict[str, Any]:
    """Optional pattern check from field_tdd_rule.rule_json (not a gate)."""
    rule = rule or {}
    fail_class = rule.get("fail_class") or FAIL_BUSINESS
    rules = rule.get("rules") if isinstance(rule.get("rules"), list) else []
    if not rules and isinstance(rule, list):
        rules = rule

    fk = (field_kind or rule.get("field_kind") or "").lower()
    if rule.get("op") == "match" and rule.get("pattern") and not rules:
        ok, err = _match_pattern(norm, str(rule.get("pattern")))
        return {
            "checked": True,
            "ok": ok if not err else False,
            "op": "match",
            "pattern": rule.get("pattern"),
            "fail_class": fail_class,
            **({"error": err} if err else {}),
        }

    reg = None
    if calling_code:
        reg = normalize_region(calling_code).get("norm")
    if not reg and region:
        reg = normalize_region(region).get("norm")

    by_region = rule.get("by_region") if isinstance(rule.get("by_region"), dict) else None
    if rule.get("op") in ("local_digits_by_region", "match_local_by_region") or by_region:
        by_region = by_region or rule.get("by_region") or {}
        spec = by_region.get(reg) if reg else None
        if not isinstance(spec, dict):
            spec = rule.get("default") if isinstance(rule.get("default"), dict) else {}
        pat = (spec or {}).get("pattern") or r"^\d{6,15}$"
        ok, err = _match_pattern(norm, str(pat))
        if norm and not str(norm).isdigit():
            ok = False
        return {
            "checked": True,
            "ok": ok if not err else False,
            "op": "local_digits_by_region",
            "region": reg,
            "pattern": pat,
            "norm": norm,
            "fail_class": fail_class,
            **({"error": err} if err else {}),
        }

    # legacy by_cc on e164 — if norm is local, compose first for check
    by_cc = rule.get("by_cc") if isinstance(rule.get("by_cc"), dict) else None
    if by_cc and reg:
        spec = by_cc.get(reg) or by_cc.get(reg.lstrip("+"))
        if isinstance(spec, dict) and spec.get("pattern"):
            probe = norm
            if norm and norm.isdigit() and reg:
                probe = compose_e164(reg, norm)
            ok, err = _match_pattern(probe, str(spec["pattern"]))
            return {
                "checked": True,
                "ok": ok if not err else False,
                "op": "match_by_cc_composed",
                "region": reg,
                "pattern": spec.get("pattern"),
                "norm": norm,
                "probe": probe,
                "fail_class": fail_class,
                **({"error": err} if err else {}),
            }

    if rule.get("op") == "range_len":
        mn = int(
            rule.get("min_len")
            if rule.get("min_len") is not None
            else rule.get("min") or 0
        )
        mx = int(
            rule.get("max_len")
            if rule.get("max_len") is not None
            else rule.get("max") or 10**9
        )
        n = len(norm or "")
        return {
            "checked": True,
            "ok": mn <= n <= mx,
            "op": "range_len",
            "min": mn,
            "max": mx,
            "fail_class": fail_class,
        }

    if rule.get("op") == "min_len":
        mn = int(
            rule.get("min_len")
            if rule.get("min_len") is not None
            else rule.get("value") or 0
        )
        return {
            "checked": True,
            "ok": len(norm or "") >= mn,
            "op": "min_len",
            "value": mn,
            "fail_class": fail_class,
        }

    if rule.get("op") == "max_len":
        mx = int(
            rule.get("max_len")
            if rule.get("max_len") is not None
            else rule.get("value") or 0
        )
        return {
            "checked": True,
            "ok": len(norm or "") <= mx,
            "op": "max_len",
            "value": mx,
            "fail_class": fail_class,
        }

    if rule.get("op") == "in_set":
        vals = [str(x).upper() for x in (rule.get("values") or rule.get("enum") or [])]
        ok = (norm or "").upper() in vals
        return {
            "checked": True,
            "ok": ok,
            "op": "in_set",
            "values": vals,
            "fail_class": fail_class,
        }

    if rule.get("op") == "text_length_clean":
        text = norm or ""
        if not rule.get("allow_newline"):
            text = text.replace("\r", " ").replace("\n", " ")
        if rule.get("trim_whitespace", True):
            text = " ".join(text.split())
        mn = int(rule.get("min_len") or rule.get("min") or 0)
        mx = int(rule.get("max_len") or rule.get("max") or 10**9)
        return {
            "checked": True,
            "ok": mn <= len(text) <= mx,
            "op": "text_length_clean",
            "cleaned": text,
            "min": mn,
            "max": mx,
            "fail_class": fail_class,
        }

    chosen = None
    default = None
    for r in rules:
        if not isinstance(r, dict):
            continue
        when = r.get("when") or {}
        if when.get("default"):
            default = r
            continue
        wreg = when.get("region") or when.get("calling_code") or when.get("cc")
        if wreg and reg:
            wr = normalize_region(wreg).get("norm")
            if wr == reg:
                chosen = r
                break
    chosen = chosen or default
    if not chosen:
        if fk == "region" or rule.get("primary_key") is True:
            ok, err = _match_pattern(norm, r"^\+\d{1,4}$")
            return {
                "checked": True,
                "ok": ok if not err else False,
                "op": "match",
                "pattern": r"^\+\d{1,4}$",
                "fail_class": fail_class,
            }
        return {
            "checked": False,
            "ok": None,
            "note": "no_rule",
            "region": reg,
            "fail_class": fail_class,
        }

    op = chosen.get("op")
    # bare pattern in when-rules (canonical phone JSON) => match_local
    if not op and chosen.get("pattern"):
        op = "match_local"
    if op in ("match", "match_local", "match_by_cc"):
        pat = chosen.get("pattern") or ""
        probe = norm
        # if pattern expects +CC... and norm is local, compose
        if pat.startswith("^\\+") and norm and str(norm).isdigit() and reg:
            probe = compose_e164(reg, norm)
        ok, err = _match_pattern(probe, pat)
        return {
            "checked": True,
            "ok": ok if not err else False,
            "op": op,
            "pattern": pat,
            "note": chosen.get("note"),
            "norm": norm,
            "probe": probe,
            "region": reg,
            "fail_class": chosen.get("fail_class") or fail_class,
            **({"error": err} if err else {}),
        }
    if op == "in_set":
        vals = [str(x).upper() for x in (chosen.get("values") or [])]
        ok = (norm or "").upper() in vals
        return {
            "checked": True,
            "ok": ok,
            "op": "in_set",
            "values": vals,
            "fail_class": fail_class,
        }
    if op == "min_len":
        n = int(chosen.get("value") or 0)
        return {
            "checked": True,
            "ok": len(norm or "") >= n,
            "op": "min_len",
            "value": n,
            "fail_class": fail_class,
        }
    if op == "max_len":
        n = int(chosen.get("value") or 0)
        return {
            "checked": True,
            "ok": len(norm or "") <= n,
            "op": "max_len",
            "value": n,
            "fail_class": fail_class,
        }
    if op == "range_len":
        mn = int(chosen.get("min") or chosen.get("value") or 0)
        mx = int(chosen.get("max") or 10**9)
        nlen = len(norm or "")
        return {
            "checked": True,
            "ok": mn <= nlen <= mx,
            "op": "range_len",
            "min": mn,
            "max": mx,
            "fail_class": fail_class,
        }
    if op == "lookup":
        return {
            "checked": True,
            "ok": True,
            "op": "lookup",
            "note": chosen.get("note") or "derived_display",
            "fail_class": fail_class,
        }
    return {
        "checked": False,
        "ok": None,
        "op": op,
        "note": "unsupported_op",
        "fail_class": fail_class,
    }


def compare_pair(
    *,
    mcp_norm: str | None,
    ui_norm: str | None,
    rule: dict[str, Any] | None = None,
    region: str | None = None,
    calling_code: str | None = None,
    field_kind: str | None = None,
) -> dict[str, Any]:
    """Equality after normalize. Phone compares LOCAL norms."""
    a = "" if mcp_norm is None else str(mcp_norm)
    b = "" if ui_norm is None else str(ui_norm)
    match_ok = a == b and a != ""
    if a == "" or b == "":
        match_ok = False
    reg = None
    if calling_code:
        reg = normalize_region(calling_code).get("norm")
    if not reg and region:
        reg = normalize_region(region).get("norm")
    tdd_mcp = apply_tdd_pattern_check(
        a, rule=rule, region=reg, calling_code=reg, field_kind=field_kind
    )
    tdd_ui = apply_tdd_pattern_check(
        b, rule=rule, region=reg, calling_code=reg, field_kind=field_kind
    )
    e164_a = (
        compose_e164(reg, a)
        if (field_kind == "phone" or (a.isdigit() and reg))
        else None
    )
    e164_b = (
        compose_e164(reg, b)
        if (field_kind == "phone" or (b.isdigit() and reg))
        else None
    )
    return {
        "match_ok": bool(match_ok),
        "op": "eq_norm",
        "mcp_norm": a,
        "ui_norm": b,
        "region": reg,
        "calling_code": reg,
        "iso2": iso2_from_region(reg),
        "e164_mcp": e164_a,
        "e164_ui": e164_b,
        "diff": {
            "equal": a == b,
            "mcp_empty": a == "",
            "ui_empty": b == "",
            "mcp_len": len(a),
            "ui_len": len(b),
        },
        "tdd_mcp": tdd_mcp,
        "tdd_ui": tdd_ui,
        "note": (
            "phone norm is local digits; region is +CC primary; e164 is compose only"
        ),
    }

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def verify_pair_qc_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    def _exists(t: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)
            ).fetchone()
            is not None
        )

    tables = {
        "pair_qc_run": _exists("pair_qc_run"),
        "pair_value_ssot": _exists("pair_value_ssot"),
        "vision_asset": _exists("vision_asset"),
        "fault_event": _exists("fault_event"),
        "code_register": _exists("code_register"),
        "field_tdd_rule": _exists("field_tdd_rule"),
    }
    ok = tables["pair_qc_run"] and tables["pair_value_ssot"]
    return {"ok": ok, "gate": GATE_POLICY, "pipeline": PIPELINE_ID, "tables": tables}


def upsert_pair_value_ssot(
    conn: sqlite3.Connection,
    *,
    field_name: str,
    value_raw: str,
    register_id: str | None = None,
    slice_key: str | None = None,
    region: str | None = None,
    source: str = "seed",
    meta: dict | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    rule = {}
    n = normalize_value(field_name, value_raw, region=region, rule=rule)
    cur = conn.execute(
        """
        INSERT INTO pair_value_ssot (
            register_id, field_name, slice_key, value_raw, value_norm,
            region, source, meta_json, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            register_id,
            field_name,
            slice_key,
            n["raw"],
            n["norm"],
            region,
            source if source in ("seed", "mcp", "sql", "api", "manual") else "seed",
            json.dumps(meta or {}, ensure_ascii=False),
            _utc_now_iso(),
        ),
    )
    if commit:
        conn.commit()
    return {
        "id": int(cur.lastrowid),
        "register_id": register_id,
        "field_name": field_name,
        "value_raw": n["raw"],
        "value_norm": n["norm"],
        "source": source,
    }


def fetch_mcp_ssot(
    conn: sqlite3.Connection,
    *,
    field_name: str,
    register_id: str | None = None,
    mcp_raw: str | None = None,
    region: str | None = None,
    source_prefer: str = "manual",
) -> dict[str, Any]:
    """L1: explicit mcp_raw wins; else latest pair_value_ssot row."""
    if mcp_raw is not None:
        return {
            "raw": str(mcp_raw),
            "source": source_prefer or "manual",
            "meta": {"path": "explicit"},
            "ok": True,
        }
    row = None
    if register_id:
        row = conn.execute(
            """
            SELECT value_raw, value_norm, source, id, region
            FROM pair_value_ssot
            WHERE register_id = ? AND field_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (register_id, field_name),
        ).fetchone()
    if row is None:
        row = conn.execute(
            """
            SELECT value_raw, value_norm, source, id, region
            FROM pair_value_ssot
            WHERE field_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (field_name,),
        ).fetchone()
    if row is None:
        return {
            "raw": None,
            "source": "missing",
            "meta": {},
            "ok": False,
            "error": "mcp_ssot_missing",
        }
    return {
        "raw": row[0],
        "cached_norm": row[1],
        "source": row[2] or "seed",
        "meta": {"pair_value_ssot_id": int(row[3]), "region": row[4]},
        "ok": True,
    }


def fetch_ui_extract(
    *,
    ui_raw: str | None = None,
    ui_vision_id: int | None = None,
    ui_source: str = "manual",
) -> dict[str, Any]:
    """L1: explicit UI value; optional vision_id already stored."""
    if ui_raw is None:
        return {
            "raw": None,
            "source": ui_source,
            "vision_id": ui_vision_id,
            "ok": False,
            "error": "ui_extract_missing",
        }
    return {
        "raw": str(ui_raw),
        "source": ui_source or "manual",
        "vision_id": ui_vision_id,
        "ok": True,
    }


def _load_tdd_rule(
    conn: sqlite3.Connection,
    *,
    tdd_rule_id: int | None = None,
    register_id: str | None = None,
    field_name: str | None = None,
    slice_key: str | None = None,
    system_key: str | None = None,
) -> tuple[int | None, dict[str, Any]]:
    """Load rule_json via shared field_tdd loader (existing field_tdd_rule table)."""
    try:
        from field_tdd import load_field_tdd

        loaded = load_field_tdd(
            conn,
            system_key=system_key,
            slice_key=slice_key,
            field_name=field_name,
            register_id=register_id,
            tdd_rule_id=tdd_rule_id,
            fallback_template=True,
        )
        if loaded:
            rule = dict(loaded.get("rule") or loaded.get("rule_json") or {})
            # keep loader metadata available to callers
            rule.setdefault("op", loaded.get("op"))
            rule.setdefault("fail_class", loaded.get("fail_class"))
            rule.setdefault("depends_on", loaded.get("depends_on") or [])
            rule["_tdd_meta"] = {
                "id": loaded.get("id"),
                "register_id": loaded.get("register_id"),
                "source": loaded.get("source"),
                "slice_key": loaded.get("slice_key"),
            }
            return (
                int(loaded["id"]) if loaded.get("id") is not None else None,
                rule,
            )
    except Exception:
        pass
    # last-resort direct SQL (legacy)
    row = None
    if tdd_rule_id is not None:
        row = conn.execute(
            "SELECT id, rule_json FROM field_tdd_rule WHERE id = ?",
            (int(tdd_rule_id),),
        ).fetchone()
    if row is None and register_id:
        row = conn.execute(
            """
            SELECT id, rule_json FROM field_tdd_rule
            WHERE register_id = ? ORDER BY id DESC LIMIT 1
            """,
            (register_id,),
        ).fetchone()
    if row is None and (slice_key or field_name):
        key = slice_key or field_name
        row = conn.execute(
            """
            SELECT id, rule_json FROM field_tdd_rule
            WHERE slice_key = ? OR field_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (key, key),
        ).fetchone()
    if row is None:
        return None, {}
    return int(row[0]), _json_loads(row[1], {})


def _lookup_option_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT id FROM fault_option WHERE code = ?", (OPTION_CODE,)
    ).fetchone()
    return int(row[0]) if row else None


def open_pair_fail_case(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    run_key: str,
    field_name: str,
    register_id: str | None,
    tdd_rule_id: int | None,
    mcp_raw: str | None,
    mcp_norm: str | None,
    ui_raw: str | None,
    ui_norm: str | None,
    mcp_vision_id: int | None,
    ui_vision_id: int | None,
    diff: dict[str, Any],
    create_task: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    """Open fault_event + facts; optional qc.pair_verify task. Never a gate."""
    option_id = _lookup_option_id(conn)
    channel_id = None
    module_id = None
    ch = conn.execute(
        "SELECT id FROM channel WHERE code = ? ORDER BY id LIMIT 1", ("local_pc",)
    ).fetchone()
    if ch:
        channel_id = int(ch[0])
    mod = conn.execute(
        "SELECT id FROM module WHERE code = ? ORDER BY id LIMIT 1", ("agent_db",)
    ).fetchone()
    if mod:
        module_id = int(mod[0])

    vision_id = ui_vision_id or mcp_vision_id
    cur = conn.execute(
        """
        INSERT INTO fault_event
            (worker_id, group_id, fault_type, status, detect_at,
             channel_id, module_id, option_id, vision_id)
        VALUES (NULL, 'pair_qc', ?, 'open', ?, ?, ?, ?, ?)
        """,
        (
            FAULT_TYPE,
            datetime.now(),
            channel_id,
            module_id,
            option_id,
            vision_id,
        ),
    )
    case_id = int(cur.lastrowid)

    fail_class = FAIL_BUSINESS
    if isinstance(diff, dict):
        fail_class = str(diff.get("fail_class") or FAIL_BUSINESS)
    calling_code = ""
    region_code = ""
    if isinstance(diff, dict):
        calling_code = str(diff.get("calling_code") or "")
        region_code = str(diff.get("region_code") or "")
        if not calling_code and isinstance(diff.get("compare"), dict):
            pass
    facts = [
        ("fault_type", FAULT_TYPE, "string", "pair_qc"),
        ("pipeline", PIPELINE_ID, "string", "pair_qc"),
        ("gate", GATE_POLICY, "string", "pair_qc"),
        ("fail_class", fail_class, "string", "pair_qc"),
        ("pair_qc_run_id", str(run_id), "number", "pair_qc"),
        ("run_key", run_key, "string", "pair_qc"),
        ("field_name", field_name, "string", "pair_qc"),
        ("register_id", register_id or "", "string", "pair_qc"),
        ("tdd_rule_id", str(tdd_rule_id or ""), "string", "pair_qc"),
        ("calling_code", calling_code, "string", "pair_qc"),
        ("region_code", region_code, "string", "pair_qc"),
        ("mcp_raw", mcp_raw or "", "string", "pair_qc"),
        ("mcp_norm", mcp_norm or "", "string", "pair_qc"),
        ("ui_raw", ui_raw or "", "string", "pair_qc"),
        ("ui_norm", ui_norm or "", "string", "pair_qc"),
        ("mcp_vision_id", str(mcp_vision_id or ""), "number", "pair_qc"),
        ("ui_vision_id", str(ui_vision_id or ""), "number", "pair_qc"),
        ("diff_json", json.dumps(diff, ensure_ascii=False)[:2000], "json", "pair_qc"),
        ("detect_only", "true", "bool", "pair_qc"),
        (
            "work",
            "investigate mismatch; pair QC does not auto-fix; raw archived",
            "string",
            "pair_qc",
        ),
    ]
    for keyword, value_text, value_type, source in facts:
        if value_text is None or value_text == "":
            continue
        conn.execute(
            """
            INSERT INTO fault_event_fact
                (event_id, keyword, value_text, value_type, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, keyword, str(value_text), value_type, source),
        )

    task_info = None
    if create_task:
        try:
            from db_schema import create_dev_task

            action = conn.execute(
                "SELECT id FROM task_action_name WHERE code = ?", (ACTION_PAIR,)
            ).fetchone()
            ver = None
            if channel_id and module_id:
                ver = conn.execute(
                    """
                    SELECT id FROM version_center
                    WHERE channel_id = ? AND module_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (channel_id, module_id),
                ).fetchone()
            if action and ver and channel_id and module_id:
                label = f"pair.{run_key}"[:180]
                # unique
                n = 1
                final = label
                while conn.execute(
                    "SELECT 1 FROM dev_task WHERE version_id = ? AND task_label = ?",
                    (int(ver[0]), final),
                ).fetchone():
                    n += 1
                    final = f"{label}.{n}"[:180]
                payload = {
                    "pipeline": PIPELINE_ID,
                    "gate": GATE_POLICY,
                    "pair_qc_run_id": run_id,
                    "case_id": case_id,
                    "register_id": register_id,
                    "tdd_rule_id": tdd_rule_id,
                    "field_name": field_name,
                    "mcp_norm": mcp_norm,
                    "ui_norm": ui_norm,
                    "detect_only": True,
                }
                task_info = create_dev_task(
                    conn,
                    channel_id=int(channel_id),
                    module_id=int(module_id),
                    version_id=int(ver[0]),
                    action_name_id=int(action[0]),
                    task_label=final,
                    title=f"Pair QC fail: {field_name} ({register_id or 'no-reg'})"[:240],
                    payload_json=payload,
                    status="pending",
                )
        except Exception as e:
            task_info = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    conn.execute(
        "UPDATE pair_qc_run SET case_id = ?, task_id = COALESCE(?, task_id) WHERE id = ?",
        (
            case_id,
            int(task_info["id"]) if isinstance(task_info, dict) and task_info.get("id") else None,
            run_id,
        ),
    )
    if commit:
        conn.commit()
    return {
        "case_id": case_id,
        "task": task_info,
        "option_id": option_id,
        "gate": GATE_POLICY,
    }


def run_pair_qc(
    conn: sqlite3.Connection,
    *,
    field_name: str,
    register_id: str | None = None,
    tdd_rule_id: int | None = None,
    slice_key: str | None = None,
    region: str | None = None,
    mcp_raw: str | None = None,
    ui_raw: str | None = None,
    mcp_source: str | None = None,
    ui_source: str = "manual",
    mcp_vision_id: int | None = None,
    ui_vision_id: int | None = None,
    open_case_on_fail: bool = True,
    create_task_on_fail: bool = True,
    notes: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Full L1 path: normalize → fetch → compare → persist → optional case."""
    field = (field_name or "").strip()
    if not field:
        raise ValueError("field_name required")

    rid = (register_id or "").strip() or None
    sk = (slice_key or field).strip()
    tdd_id, rule = _load_tdd_rule(
        conn,
        tdd_rule_id=tdd_rule_id,
        register_id=rid,
        field_name=field,
        slice_key=sk,
    )

    # region from arg or rule depends
    reg = (region or "").strip().upper() or None

    mcp_fetch = fetch_mcp_ssot(
        conn,
        field_name=field,
        register_id=rid,
        mcp_raw=mcp_raw,
        region=reg,
        source_prefer=mcp_source or ("manual" if mcp_raw is not None else "seed"),
    )
    ui_fetch = fetch_ui_extract(
        ui_raw=ui_raw, ui_vision_id=ui_vision_id, ui_source=ui_source
    )

    mcp_n = normalize_value(field, mcp_fetch.get("raw"), region=reg, rule=rule)
    ui_n = normalize_value(field, ui_fetch.get("raw"), region=reg, rule=rule)
    # region = +CC primary; phone.norm = local digits; e164 = compose only
    field_kind = str(mcp_n.get("field_kind") or ui_n.get("field_kind") or field)
    region_norm = mcp_n.get("region_norm") or ui_n.get("region_norm")
    if field_kind == "region":
        region_norm = mcp_n.get("norm") or ui_n.get("norm") or region_norm
    if not region_norm and reg:
        region_norm = normalize_region(reg).get("norm")
    cc = region_norm or mcp_n.get("calling_code") or ui_n.get("calling_code")
    cmp = compare_pair(
        mcp_norm=mcp_n.get("norm"),
        ui_norm=ui_n.get("norm"),
        rule=rule,
        region=region_norm or reg,
        calling_code=cc if isinstance(cc, str) else None,
        field_kind=field_kind,
    )

    run_key = f"pq_{uuid.uuid4().hex[:16]}"
    match_ok: int | None = 1 if cmp.get("match_ok") else 0
    fail_class: str | None = None
    if not mcp_fetch.get("ok") and not ui_fetch.get("ok"):
        status = "error"
        match_ok = None
        fail_class = FAIL_TRANSIENT
        cmp["degraded"] = {
            "mcp_ok": False,
            "ui_ok": False,
            "note": "both paths missing — transient_execution",
        }
    elif not mcp_fetch.get("ok") or not ui_fetch.get("ok"):
        # path missing / extract null → transient (retry), not formal business case
        status = "error"
        match_ok = None
        fail_class = FAIL_TRANSIENT
        cmp["degraded"] = {
            "mcp_ok": bool(mcp_fetch.get("ok")),
            "ui_ok": bool(ui_fetch.get("ok")),
            "note": "path missing — transient_execution; do not open business case",
            "fail_class": FAIL_TRANSIENT,
        }
    else:
        status = "pass" if match_ok == 1 else "fail"
        if status == "fail":
            fail_class = FAIL_BUSINESS

    # Persist region (+CC). ISO2 is display-only.
    region_store = region_norm or cc or reg

    diff_json = {
        "compare": cmp.get("diff"),
        "tdd_mcp": cmp.get("tdd_mcp"),
        "tdd_ui": cmp.get("tdd_ui"),
        "calling_code": cc or cmp.get("calling_code"),
        "region": region_store,
        "region_norm": region_norm,
        "iso2": iso2_from_region(region_store) if region_store else None,
        "e164_mcp": mcp_n.get("e164") or cmp.get("e164_mcp"),
        "e164_ui": ui_n.get("e164") or cmp.get("e164_ui"),
        "phone_local_mcp": mcp_n.get("local") if field_kind == "phone" else None,
        "phone_local_ui": ui_n.get("local") if field_kind == "phone" else None,
        "fail_class": fail_class,
        "mcp_fetch": {
            "ok": mcp_fetch.get("ok"),
            "source": mcp_fetch.get("source"),
            "error": mcp_fetch.get("error"),
        },
        "ui_fetch": {
            "ok": ui_fetch.get("ok"),
            "source": ui_fetch.get("source"),
            "error": ui_fetch.get("error"),
        },
        "mcp_normalize": {
            k: mcp_n.get(k)
            for k in (
                "ok",
                "errors",
                "field_kind",
                "local",
                "region_norm",
                "e164",
                "calling_code",
            )
        },
        "ui_normalize": {
            k: ui_n.get(k)
            for k in (
                "ok",
                "errors",
                "field_kind",
                "local",
                "region_norm",
                "e164",
                "calling_code",
            )
        },
        "degraded": cmp.get("degraded"),
        "primary_key": "region_calling_code",
        "phone_storage": "local_digit_string",
    }
    tdd_check = {"mcp": cmp.get("tdd_mcp"), "ui": cmp.get("tdd_ui")}

    cur = conn.execute(
        """
        INSERT INTO pair_qc_run (
            run_key, register_id, tdd_rule_id, slice_key, field_name, region,
            mcp_raw, mcp_norm, ui_raw, ui_norm,
            mcp_source, ui_source, mcp_vision_id, ui_vision_id,
            match_ok, compare_op, diff_json, tdd_check_json, status, notes, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_key,
            rid,
            tdd_id,
            sk,
            field,
            region_store,
            mcp_n.get("raw"),
            mcp_n.get("norm"),
            ui_n.get("raw"),
            ui_n.get("norm"),
            str(mcp_fetch.get("source") or mcp_source or "seed"),
            str(ui_fetch.get("source") or ui_source or "manual"),
            mcp_vision_id,
            ui_fetch.get("vision_id") or ui_vision_id,
            match_ok,
            "eq_norm",
            json.dumps(diff_json, ensure_ascii=False),
            json.dumps(tdd_check, ensure_ascii=False),
            status,
            notes,
            "pair_qc",
        ),
    )
    run_id = int(cur.lastrowid)

    case_info = None
    # Formal case only for business_defect (both paths present + mismatch)
    if status == "fail" and fail_class == FAIL_BUSINESS and open_case_on_fail:
        case_info = open_pair_fail_case(
            conn,
            run_id=run_id,
            run_key=run_key,
            field_name=field,
            register_id=rid,
            tdd_rule_id=tdd_id,
            mcp_raw=mcp_n.get("raw"),
            mcp_norm=mcp_n.get("norm"),
            ui_raw=ui_n.get("raw"),
            ui_norm=ui_n.get("norm"),
            mcp_vision_id=mcp_vision_id,
            ui_vision_id=ui_fetch.get("vision_id") or ui_vision_id,
            diff=diff_json,
            create_task=create_task_on_fail,
            commit=False,
        )

    if commit:
        conn.commit()

    row = get_pair_qc_run(conn, run_id=run_id)
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "detect_only": True,
        "run_id": run_id,
        "run_key": run_key,
        "status": status,
        "match_ok": match_ok,
        "fail_class": fail_class,
        "field_name": field,
        "register_id": rid,
        "tdd_rule_id": tdd_id,
        "region": region_store,
        "region_hint": reg,
        "calling_code": cc or cmp.get("calling_code"),
        "iso2": iso2_from_region(region_store) if region_store else None,
        "mcp": {
            "raw": mcp_n.get("raw"),
            "norm": mcp_n.get("norm"),
            "source": mcp_fetch.get("source"),
            "local": mcp_n.get("local"),
            "region_norm": mcp_n.get("region_norm"),
            "e164": mcp_n.get("e164"),
            "calling_code": mcp_n.get("calling_code"),
        },
        "ui": {
            "raw": ui_n.get("raw"),
            "norm": ui_n.get("norm"),
            "source": ui_fetch.get("source"),
            "local": ui_n.get("local"),
            "region_norm": ui_n.get("region_norm"),
            "e164": ui_n.get("e164"),
            "calling_code": ui_n.get("calling_code"),
        },
        "compare": cmp,
        "case": case_info,
        "run": row,
        "law": (
            "detect != fix; region=+CC; phone=local; e164=compose; "
            "raw archived; never schema gate"
        ),
    }


def get_pair_qc_run(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    run_key: str | None = None,
) -> dict[str, Any] | None:
    if run_id is not None:
        row = conn.execute(
            "SELECT * FROM pair_qc_run WHERE id = ?", (int(run_id),)
        ).fetchone()
    elif run_key:
        row = conn.execute(
            "SELECT * FROM pair_qc_run WHERE run_key = ?", (run_key,)
        ).fetchone()
    else:
        return None
    if not row:
        return None
    cols = [d[0] for d in conn.execute("PRAGMA table_info(pair_qc_run)").fetchall()]
    # PRAGMA order may not match SELECT * with row_factory off — use description
    # fallback: re-query with explicit names
    cur = conn.execute(
        """
        SELECT id, run_key, register_id, tdd_rule_id, slice_key, field_name, region,
               mcp_raw, mcp_norm, ui_raw, ui_norm, mcp_source, ui_source,
               mcp_vision_id, ui_vision_id, match_ok, compare_op, diff_json,
               tdd_check_json, status, case_id, task_id, notes, source, created_at
        FROM pair_qc_run WHERE id = ?
        """,
        (int(row[0]),),
    )
    r = cur.fetchone()
    if not r:
        return None
    keys = [
        "id",
        "run_key",
        "register_id",
        "tdd_rule_id",
        "slice_key",
        "field_name",
        "region",
        "mcp_raw",
        "mcp_norm",
        "ui_raw",
        "ui_norm",
        "mcp_source",
        "ui_source",
        "mcp_vision_id",
        "ui_vision_id",
        "match_ok",
        "compare_op",
        "diff_json",
        "tdd_check_json",
        "status",
        "case_id",
        "task_id",
        "notes",
        "source",
        "created_at",
    ]
    out = {k: r[i] for i, k in enumerate(keys)}
    out["diff"] = _json_loads(out.get("diff_json"), {})
    out["tdd_check"] = _json_loads(out.get("tdd_check_json"), {})
    return out


def list_pair_qc_runs(
    conn: sqlite3.Connection,
    *,
    limit: int = 30,
    status: str | None = None,
    register_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT id, run_key, register_id, tdd_rule_id, field_name, region,
               mcp_norm, ui_norm, match_ok, status, case_id, task_id, created_at
        FROM pair_qc_run
        WHERE 1=1
    """
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if register_id:
        sql += " AND register_id = ?"
        args.append(register_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = conn.execute(sql, args).fetchall()
    keys = [
        "id",
        "run_key",
        "register_id",
        "tdd_rule_id",
        "field_name",
        "region",
        "mcp_norm",
        "ui_norm",
        "match_ok",
        "status",
        "case_id",
        "task_id",
        "created_at",
    ]
    return [{k: r[i] for i, k in enumerate(keys)} for r in rows]


def pair_qc_dashboard(conn: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    runs = list_pair_qc_runs(conn, limit=limit)
    n_pass = sum(1 for r in runs if r.get("status") == "pass")
    n_fail = sum(1 for r in runs if r.get("status") == "fail")
    n_err = sum(1 for r in runs if r.get("status") == "error")
    return {
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "flow": contracts_doc()["flow"],
        "why": WHY,
        "counts": {"pass": n_pass, "fail": n_fail, "error": n_err, "listed": len(runs)},
        "runs": runs,
        "notes": "detect only; normalize before compare; dual evidence on fail cases",
    }


def run_selftest(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Offline selftest — no live MCP required."""
    from db_schema import ensure_schema, get_db_path

    own = conn is None
    if own:
        path = get_db_path(None)
        ensure_schema(path)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON;")
    assert conn is not None

    results: list[dict[str, Any]] = []
    try:
        schema = verify_pair_qc_schema(conn)
        assert schema.get("ok"), schema
        results.append({"step": "schema", "ok": True})

        # region = +CC; phone = local only
        r1 = normalize_region("+86")
        r2 = normalize_region("86")
        r3 = normalize_region("CN")
        assert r1["norm"] == r2["norm"] == r3["norm"] == "+86", (r1, r2, r3)
        results.append({"step": "normalize_region_cc", "ok": True, "norm": r1["norm"]})

        a = normalize_phone("+86 138-0013-8000")
        b = normalize_phone("138-0013-8000", region="+86")
        c = normalize_phone("13800138000", region="CN")
        assert a["norm"] == b["norm"] == c["norm"] == "13800138000", (a, b, c)
        assert a["region_norm"] == "+86" and a["e164"] == "+8613800138000", a
        results.append(
            {
                "step": "normalize_phone_local",
                "ok": True,
                "norm": a["norm"],
                "e164": a["e164"],
                "region": a["region_norm"],
            }
        )

        raw_p = "138-0013-8000"
        norms = [normalize_phone(raw_p, region="+86")["norm"] for _ in range(20)]
        assert len(set(norms)) == 1 and norms[0] == "13800138000", norms
        results.append({"step": "normalize_pure", "ok": True, "norm": norms[0]})

        hk = normalize_phone("+852 9123 4567", region="+86")
        assert hk["norm"] == "91234567", hk
        assert hk["region_norm"] == "+852", hk
        assert hk["e164"] == "+85291234567", hk
        results.append(
            {
                "step": "split_cc_local",
                "ok": True,
                "local": hk["norm"],
                "region": hk["region_norm"],
            }
        )

        pass_run = run_pair_qc(
            conn,
            field_name="phone",
            slice_key="phone",
            region="+86",
            mcp_raw="+86 138-0013-8000",
            ui_raw="138-0013-8000",
            register_id="reg_selftest_phone",
            open_case_on_fail=True,
            commit=True,
        )
        assert pass_run.get("status") == "pass", pass_run
        assert pass_run.get("match_ok") == 1, pass_run
        assert pass_run["mcp"]["norm"] == pass_run["ui"]["norm"] == "13800138000"
        assert pass_run.get("calling_code") == "+86"
        results.append(
            {
                "step": "pair_pass_format",
                "ok": True,
                "run_id": pass_run.get("run_id"),
                "norm": pass_run["mcp"]["norm"],
                "e164": pass_run["mcp"].get("e164"),
            }
        )

        fail_run = run_pair_qc(
            conn,
            field_name="phone",
            slice_key="phone",
            region="+86",
            mcp_raw="13800138000",
            ui_raw="1380013800",
            register_id="reg_selftest_phone",
            open_case_on_fail=True,
            create_task_on_fail=True,
            commit=True,
        )
        assert fail_run.get("status") == "fail", fail_run
        assert fail_run.get("match_ok") == 0, fail_run
        assert fail_run.get("fail_class") == FAIL_BUSINESS, fail_run
        assert fail_run.get("case") and fail_run["case"].get("case_id"), fail_run
        case_id = int(fail_run["case"]["case_id"])
        facts = conn.execute(
            "SELECT keyword FROM fault_event_fact WHERE event_id = ?",
            (case_id,),
        ).fetchall()
        keys = {str(x[0]) for x in facts}
        assert "register_id" in keys and "mcp_norm" in keys and "ui_norm" in keys
        assert fail_run["mcp"]["raw"] == "13800138000"
        assert fail_run["ui"]["raw"] == "1380013800"
        results.append(
            {
                "step": "pair_fail_case",
                "ok": True,
                "run_id": fail_run.get("run_id"),
                "case_id": case_id,
                "fail_class": FAIL_BUSINESS,
                "fact_keys_n": len(keys),
            }
        )

        tr = run_pair_qc(
            conn,
            field_name="phone",
            slice_key="phone",
            region="+86",
            mcp_raw="13800138000",
            ui_raw=None,
            register_id="reg_selftest_phone",
            open_case_on_fail=True,
            commit=True,
        )
        assert tr.get("status") == "error", tr
        assert tr.get("fail_class") == FAIL_TRANSIENT, tr
        assert tr.get("case") is None, tr
        results.append(
            {
                "step": "transient_no_case",
                "ok": True,
                "run_id": tr.get("run_id"),
                "fail_class": FAIL_TRANSIENT,
            }
        )

        doc = contracts_doc()
        assert doc["gate"] == "never"
        assert doc.get("primary_key") == "region_calling_code"
        assert doc.get("phone_storage") == "local_digit_string"
        results.append({"step": "contracts", "ok": True})

        return {
            "ok": True,
            "gate": GATE_POLICY,
            "pipeline": PIPELINE_ID,
            "steps": results,
            "notes": (
                "region=+CC; phone=local; pure normalize; business case on mismatch; "
                "transient skips formal case"
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "gate": GATE_POLICY,
            "error": f"{type(e).__name__}: {e}",
            "steps": results,
        }
    finally:
        if own and conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["contracts"]
    cmd = args[0].strip().lower()

    if cmd in ("contracts", "doc"):
        print(json.dumps(contracts_doc(), ensure_ascii=False, indent=2))
        return 0

    if cmd in ("selftest", "test"):
        out = run_selftest()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if cmd in ("normalize", "norm"):
        ap = argparse.ArgumentParser(prog="pair_qc.py normalize")
        ap.add_argument("--field", default="phone")
        ap.add_argument("--value", required=True)
        ap.add_argument("--region", default="CN")
        ns = ap.parse_args(args[1:])
        print(
            json.dumps(
                normalize_value(ns.field, ns.value, region=ns.region),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if cmd in ("run", "pair"):
        ap = argparse.ArgumentParser(prog="pair_qc.py run")
        ap.add_argument("--db", default=None)
        ap.add_argument("--migrate", action="store_true")
        ap.add_argument("--field", required=True)
        ap.add_argument("--region", default=None)
        ap.add_argument("--register-id", default=None)
        ap.add_argument("--tdd-rule-id", type=int, default=None)
        ap.add_argument("--slice", default=None)
        ap.add_argument("--mcp-value", default=None)
        ap.add_argument("--ui-value", default=None)
        ap.add_argument("--no-case", action="store_true")
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path

        db = get_db_path(ns.db)
        if ns.migrate:
            ensure_schema(db)
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            out = run_pair_qc(
                conn,
                field_name=ns.field,
                region=ns.region,
                register_id=ns.register_id,
                tdd_rule_id=ns.tdd_rule_id,
                slice_key=ns.slice,
                mcp_raw=ns.mcp_value,
                ui_raw=ns.ui_value,
                open_case_on_fail=not ns.no_case,
                commit=True,
            )
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if cmd in ("list", "dashboard"):
        ap = argparse.ArgumentParser(prog="pair_qc.py list")
        ap.add_argument("--db", default=None)
        ap.add_argument("--limit", type=int, default=20)
        ns = ap.parse_args(args[1:])
        from db_schema import get_db_path

        conn = sqlite3.connect(get_db_path(ns.db))
        try:
            out = pair_qc_dashboard(conn, limit=ns.limit)
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    if cmd in ("seed-ssot", "seed"):
        ap = argparse.ArgumentParser(prog="pair_qc.py seed-ssot")
        ap.add_argument("--db", default=None)
        ap.add_argument("--field", required=True)
        ap.add_argument("--value", required=True)
        ap.add_argument("--register-id", default=None)
        ap.add_argument("--region", default=None)
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path

        db = get_db_path(ns.db)
        ensure_schema(db)
        conn = sqlite3.connect(db)
        try:
            out = upsert_pair_value_ssot(
                conn,
                field_name=ns.field,
                value_raw=ns.value,
                register_id=ns.register_id,
                region=ns.region,
                source="seed",
                commit=True,
            )
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(
        "usage: pair_qc.py [contracts|selftest|normalize|run|list|seed-ssot]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
