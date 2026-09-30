# -*- coding: utf-8 -*-
"""env_proof.py — the shared pre-action proof gate.

Implements the rules of the `env_task_proof` skill
(`skills/1_core/env_task_proof/`) as executable checks, so the rule is a GATE
and not prose.

Why this module exists
----------------------
The same root cause produced three artifacts that all said "prove it first":
`env_task_proof`, `evidence_provenance`, `picker_presence_proof`. None of them
had anything that would FAIL when the proof was missing, so every new automation
path re-implemented its own checks and the failure recurred.

The specific failure: a capture of Chrome at 1920x1080 was cropped with picker
coordinates, the overlay landed on a "VS code" app tile, and the result was
recorded as a confident `geometry_fail` with a healthy-looking provenance block
(source=pyautogui, size ok, sha256 real). **A same-size image is not a
same-content image** — size and hash are file properties; they cannot say what
was photographed.

What is a "proof record"
------------------------
A dict of measured facts about the capture, normally `res["provenance"]` from
f_perm_click. The minimum for a JUDGEMENT (PASS/FAIL) to be written:

    source          - which capture path was used, and not "unknown"
    sha256          - the image was real
    foreground      - the front window, with a real process name
    foreground_is_code (or equivalent) - the right app was in front

UNKNOWN is NOT a judgement, so writing it is always allowed. That keeps the
honest "we learned nothing" outcome recordable while blocking the dishonest
"I judged it" outcome.

Usage
-----
    from env_proof import proof_status, ProofRequired

    ok, reasons = proof_status(payload)
    if not ok:
        raise ProofRequired(reasons)
"""
from __future__ import annotations

from typing import Any

# Verdicts that assert a judgement about the target. UNKNOWN/None do not.
JUDGEMENT_VERDICTS = ("PASS", "FAIL")

# Container-absent classification order. Earlier entries win.
# Rationale: judging geometry on a capture that does not contain the container
# is how a wrong-source capture became an unexplainable `geometry_fail`, which
# sent the operator to re-measure a rect that was never wrong.
ABSENCE_CATEGORY = "picker_not_open"
CATEGORY_ORDER = (
    ABSENCE_CATEGORY,
    "source_suspect",
    "box_not_seen",
    "geometry_fail",
    "text_mismatch",
    "no_vl_answer",
    "pass",
    "unknown",
)


class ProofRequired(Exception):
    """Raised when a judgement is about to be recorded without a proof record."""

    def __init__(self, reasons: list[str], verdict: str = ""):
        self.reasons = list(reasons)
        self.verdict = verdict
        super().__init__(
            "proof record missing/incomplete — refusing to record verdict %r: %s"
            % (verdict or "?", "; ".join(self.reasons) or "no reason given")
        )


def _is_blank(v: Any) -> bool:
    return v is None or str(v).strip() == "" or str(v).strip().lower() == "unknown"


def proof_status(payload: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Check whether a payload carries a usable proof record.

    Returns (ok, reasons). `reasons` lists every missing element, so a refusal
    says WHAT was missing rather than just "refused".

    Never raises: a malformed payload is reported as not-ok.
    """
    reasons: list[str] = []
    if not isinstance(payload, dict):
        return False, ["payload is not a dict"]

    prov = payload.get("provenance")
    if not isinstance(prov, dict) or not prov:
        return False, ["no provenance block — capture origin unknown"]

    if _is_blank(prov.get("source")):
        reasons.append("provenance.source missing (which capture path?)")
    if _is_blank(prov.get("sha256")):
        reasons.append("provenance.sha256 missing (image not verified)")
    if prov.get("error"):
        reasons.append("provenance.error present (%s)" % prov.get("error"))

    fg = prov.get("foreground")
    if not isinstance(fg, dict) or not fg:
        reasons.append(
            "provenance.foreground missing (which window was in front?) — "
            "a same-size image is not a same-content image"
        )
    else:
        if _is_blank(fg.get("process")):
            reasons.append("provenance.foreground.process missing")
        if fg.get("is_code") is None and prov.get("foreground_is_code") is None:
            reasons.append("foreground app not judged (is_code / foreground_is_code)")

    return (not reasons), reasons


def has_proof(payload: dict[str, Any] | None) -> bool:
    """True when the payload carries a usable proof record."""
    return proof_status(payload)[0]


def asserts_judgement(payload: dict[str, Any] | None) -> bool:
    """True when the payload asserts PASS/FAIL (a judgement, not an absence)."""
    if not isinstance(payload, dict):
        return False
    v = str(payload.get("verdict") or "").strip().upper()
    return v in JUDGEMENT_VERDICTS


def proof_required(payload: dict[str, Any] | None) -> bool:
    """True when this payload needs a proof record before it may be written."""
    return asserts_judgement(payload)


def classify_order_ok(category: str) -> tuple[bool, str]:
    """Check a root_cause category is known, and report its precedence.

    Also encodes the ordering rule: absence must be tested before geometry.
    """
    cat = str(category or "").strip()
    if cat not in CATEGORY_ORDER:
        return False, "unknown category %r (known: %s)" % (cat, ", ".join(CATEGORY_ORDER))
    return True, "precedence %d of %d" % (CATEGORY_ORDER.index(cat) + 1, len(CATEGORY_ORDER))


# Categories that mean "no judgement was made". These pair with verdict UNKNOWN.
ABSENCE_CATEGORIES = (
    ABSENCE_CATEGORY,      # picker_not_open
    "source_suspect",
    "box_not_seen",
    "no_vl_answer",
)
# Categories that assert a measurement was taken.
GEOMETRY_CATEGORIES = ("geometry_fail", "text_mismatch")


def corrected_classification(payload: dict[str, Any] | None) -> tuple[dict, list[str]]:
    """DERIVE the only classification consistent with the observed state.

    This is the rule engine the write gate and the TDD cases share. It returns
    (classification, violations) where `classification` is the corrected
    {category, verdict} and `violations` explains every rule the input broke.

    An earlier version only compared the claimed category against the verdict,
    so `geometry_fail + FAIL` slipped through even when the container was
    KNOWN to be absent — the rule lacked the one input that matters. A mutation
    test exposed that: the absence check could be neutered with every case still
    green. Container state is now a first-class input.

    Rules
    -----
    1. container absent (picker_ok is False) -> category MUST be
       picker_not_open and verdict MUST be UNKNOWN. Any geometry claim is a
       fabricated measurement.
    2. container present -> category MUST NOT be picker_not_open.
    3. verdict UNKNOWN -> category MUST be an absence category (no judgement).
    4. geometry category -> verdict MUST be PASS/FAIL (a measurement implies a
       judgement), unless the container was absent (rule 1 wins).
    """
    if not isinstance(payload, dict):
        return {"category": "unknown", "verdict": "UNKNOWN"}, ["payload is not a dict"]

    picker_ok = payload.get("picker_ok")
    claimed = str((payload.get("root_cause") or {}).get("category") or "").strip()
    verdict = str(payload.get("verdict") or "").strip().upper()
    violations: list[str] = []

    if picker_ok is False:
        if claimed != ABSENCE_CATEGORY:
            violations.append(
                "container absent (picker_ok=False) but category is %r — no "
                "geometry judgement was possible" % (claimed or "?")
            )
        if verdict != "UNKNOWN":
            violations.append(
                "container absent but verdict is %r — must be UNKNOWN"
                % (verdict or "?")
            )
        if violations:
            return {"category": ABSENCE_CATEGORY, "verdict": "UNKNOWN"}, violations
        return {"category": ABSENCE_CATEGORY, "verdict": "UNKNOWN"}, []

    if picker_ok is None:
        # Container state was never established. That is fine for a PASS (if the
        # label matched, the target was evidently there) and fine for an absence
        # diagnosis. It is NOT fine for a FAILURE blamed on geometry: claiming
        # "the rect is wrong" requires having proven the row was visible.
        if claimed in GEOMETRY_CATEGORIES and verdict == "FAIL":
            violations.append(
                "FAIL blamed on %r but the container state was never proven "
                "(picker_ok not set) — a rect cannot be wrong if the row was "
                "not shown to be on screen" % claimed
            )
            return {"category": ABSENCE_CATEGORY, "verdict": "UNKNOWN"}, violations

    if picker_ok is True and claimed == ABSENCE_CATEGORY:
        violations.append(
            "container present (picker_ok=True) but category is %s"
            % ABSENCE_CATEGORY
        )

    if verdict == "UNKNOWN" and claimed in GEOMETRY_CATEGORIES:
        violations.append(
            "verdict UNKNOWN but category %r claims a measurement" % claimed
        )
    if claimed in GEOMETRY_CATEGORIES and verdict not in ("PASS", "FAIL"):
        violations.append(
            "category %r claims a measurement but verdict is %r"
            % (claimed, verdict or "?")
        )

    if violations:
        return {"category": claimed or "unknown", "verdict": verdict or "UNKNOWN"}, violations
    return {"category": claimed or "unknown", "verdict": verdict or "UNKNOWN"}, []


def validate_classification(payload: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """True when the payload's classification is consistent with its state."""
    _, violations = corrected_classification(payload)
    return (not violations), violations


def absence_beats_geometry(category: str, verdict: str) -> tuple[bool, str]:
    """Reject the specific invalid combination seen in the real failure.

    Kept for callers that only have (category, verdict). Prefer
    `corrected_classification()`, which also takes the container state — without
    that input this check cannot catch `geometry_fail + FAIL` while the
    container is absent, which is exactly the real failure.
    """
    cat = str(category or "").strip()
    v = str(verdict or "").strip().upper()
    if cat == ABSENCE_CATEGORY and v == "FAIL":
        return False, (
            "container-absent must be UNKNOWN, not FAIL — no geometry "
            "judgement was made"
        )
    if cat == "geometry_fail" and v == "UNKNOWN":
        return False, (
            "geometry_fail claims a measurement; if the container was absent "
            "use %s" % ABSENCE_CATEGORY
        )
    return True, "ok"
