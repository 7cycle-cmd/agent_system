"""Evidence classify — binary PASS/FAIL on an evidence image via local 7B-VL.

WHY THIS EXISTS
---------------
A worker needs to classify their own work from evidence. The hard part is not
producing the screenshot, it is getting a trustworthy verdict from it.

Design rules (all fail-closed):

1. BINARY, not JSON. The model answers "Result: YES|NO" + "Reason:". A free-form
   JSON schema invites the model to invent fields; a yes/no is hard to fake.
2. UNKNOWN is a real outcome. If the model does not answer yes/no, the verdict is
   UNKNOWN — never silently PASS. `parse_verify_response()` returns
   `correct=None` in that case and we honour it.
3. The red guide box is part of the prompt. "Does the text inside the RED BOX
   read X?" is far less ambiguous than "does this crop show X?", because the box
   gives the model a reference frame. See evidence_overlay.py.
4. Local model only (Ollama qwen2.5vl:7b on 127.0.0.1:18803) — zero token cost.

This module never writes state. It returns a verdict; the caller decides.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Fixed prompt template. Kept stable so runs are comparable across time — a
# reworded prompt is a different experiment and invalidates prior results.
PROMPT_TEMPLATE = (
    "You are a strict visual inspector.\n"
    "The image has a RED BOX drawn on it. The red box marks the region of interest.\n"
    "Question: does the text inside the RED BOX read \"{label}\"?\n"
    "Rules:\n"
    "  - Judge ONLY what is inside the red box. Ignore everything outside it.\n"
    "  - If the red box is empty, covers the wrong thing, or the text is unreadable,\n"
    "    answer NO.\n"
    "  - Do not guess. Partial or similar text is NOT a match.\n"
    "Output exactly two lines:\n"
    "Result: [YES / NO]\n"
    "Reason: one short sentence.\n"
)

# Question 1 (gating): can the model even see the box? If not, the overlay did
# not draw or the model cannot perceive it — and asking question 2 anyway would
# produce a confident answer for the wrong reason. This is the check that would
# have caught "why is the model saying NO to the correct label?" (test setup
# passed a bare image while the prompt referenced a red box).
PROMPT_Q1 = (
    "Look at this image. Is there a RED BOX (a red rectangle drawn on top of\n"
    "the screenshot) present anywhere in the image?\n"
    "Answer NO if you cannot see any red rectangle.\n"
    "Output exactly two lines:\n"
    "Result: [YES / NO]\n"
    "Reason: one short sentence.\n"
)

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNKNOWN = "UNKNOWN"


@dataclass
class ClassifyResult:
    verdict: str                 # PASS | FAIL | UNKNOWN
    answer: str | None           # "YES" | "NO" | None
    reason: str
    raw: str
    parser: str
    model: str
    confidence: float
    image_path: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "answer": self.answer,
            "reason": self.reason,
            "raw": self.raw,
            "parser": self.parser,
            "model": self.model,
            "confidence": self.confidence,
            "image_path": self.image_path,
            "error": self.error,
        }


def classify_yes_no(
    image_path: str | Path | None,
    label: str,
    *,
    prompt: str | None = None,
    model: str | None = None,
    timeout: float | None = None,
) -> ClassifyResult:
    """Ask the local VL model a binary question about an evidence image.

    Returns PASS only on an explicit YES. NO -> FAIL. Anything else -> UNKNOWN.
    Never raises: a vision failure becomes UNKNOWN with `error` set.
    """
    if not label or not str(label).strip():
        return ClassifyResult(
            verdict=VERDICT_UNKNOWN, answer=None,
            reason="no label supplied — cannot ask a question",
            raw="", parser="none", model=model or "?", confidence=0.0,
            image_path=str(image_path) if image_path else None,
            error="label required",
        )

    if image_path is None or not Path(str(image_path)).is_file():
        return ClassifyResult(
            verdict=VERDICT_UNKNOWN, answer=None,
            reason="evidence image missing",
            raw="", parser="none", model=model or "?", confidence=0.0,
            image_path=str(image_path) if image_path else None,
            error="image not found",
        )

    try:
        import vision_analyze
    except Exception as e:
        return ClassifyResult(
            verdict=VERDICT_UNKNOWN, answer=None,
            reason="vision_analyze unavailable",
            raw="", parser="none", model=model or "?", confidence=0.0,
            image_path=str(image_path), error="%s: %s" % (type(e).__name__, e),
        )

    text = prompt or PROMPT_TEMPLATE.format(label=str(label).strip())

    # Bounded retry on TRANSIENT transport failures only. Ollama on a local GPU
    # can drop a connection when several vision calls run back-to-back
    # (RemoteDisconnected / ConnectionReset). That is not a verdict, so without a
    # retry a transient blip would block verification. A real error still
    # surfaces as UNKNOWN after the last attempt — never a silent pass.
    attempts = 3
    res = None
    last_err = ""
    for i in range(attempts):
        try:
            res = vision_analyze.analyze_evidence(
                str(image_path),
                fault_type="evidence_classify",
                prompt=text,
                parse_mode="result_yes_no",
                model=model,
                timeout=timeout,
            )
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, e)
            res = None
        if res is not None and not res.error:
            break
        err = (res.error if res is not None else "") or last_err
        transient = any(
            k in err for k in ("RemoteDisconnected", "ConnectionReset",
                               "ConnectionAborted", "timed out", "Timeout",
                               "broken pipe", "EOF occurred")
        )
        if not transient or i == attempts - 1:
            break
        # brief, growing pause so the GPU queue can drain
        time.sleep(0.6 * (i + 1))

    if res is None:
        return ClassifyResult(
            verdict=VERDICT_UNKNOWN, answer=None,
            reason="vision call failed after %d attempt(s)" % attempts,
            raw="", parser="none", model=model or "?", confidence=0.0,
            image_path=str(image_path), error=last_err,
        )

    detail = res.detail or {}
    correct = detail.get("correct")
    parser = str(detail.get("parser") or "none")
    reason = str(detail.get("reason") or res.summary or "")
    raw = str(res.raw_text or "")

    if res.error:
        return ClassifyResult(
            verdict=VERDICT_UNKNOWN, answer=None,
            reason=reason or "vision error",
            raw=raw, parser=parser, model=res.model or (model or "?"),
            confidence=0.0, image_path=str(image_path), error=res.error,
        )

    if correct is True:
        verdict, answer = VERDICT_PASS, "YES"
    elif correct is False:
        verdict, answer = VERDICT_FAIL, "NO"
    else:
        # parser == "none": the model did not answer yes/no. Fail closed.
        verdict, answer = VERDICT_UNKNOWN, None

    return ClassifyResult(
        verdict=verdict,
        answer=answer,
        reason=reason,
        raw=raw,
        parser=parser,
        model=res.model or (model or "?"),
        confidence=float(detail.get("confidence") or 0.0),
        image_path=str(image_path),
        error=None,
    )


def classify_with_overlay(
    image_path: str | Path,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    label: str,
    out_path: str | Path,
    *,
    tol_pass: int = 3,
    tol_warn: int = 8,
    require_edges: bool = True,
    require_box_first: bool = True,
) -> dict[str, Any]:
    """Full evidence-classify pipeline: overlay -> edge verdicts -> 2 VL questions.

    Two questions are asked, in order:

      1. "do you see a red box at the image?"   <- GATING self-check
      2. "image inside red box = <label>, yes or no?"   <- the classification

    Question 1 exists because question 2 alone can return a confident NO for the
    wrong reason (the overlay failed to draw, or the model cannot perceive it).
    When Q1 is not YES the result is UNKNOWN, not FAIL — we did not learn
    anything about the label.

    PASS requires ALL of:
      - every edge verdict is PASS (when require_edges)
      - Q1 is YES (when require_box_first)
      - Q2 is YES

    Returns a single dict so a caller can log one JSON line.
    """
    import evidence_overlay

    base: dict[str, Any] = {
        "label": str(label),
        "rect_real": {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)},
        # Store the prompt actually sent, so the evidence record is
        # self-explanatory and stays reproducible if the template is reworded.
        "prompt_q1": PROMPT_Q1,
        "prompt_q2": PROMPT_TEMPLATE.format(label=str(label).strip()),
        "output_format": "Result: [YES / NO] / Reason: one short sentence",
    }

    ov = evidence_overlay.build_overlay(
        image_path, x1, y1, x2, y2, out_path,
        label=label, tol_pass=tol_pass, tol_warn=tol_warn,
    )
    if not ov.ok:
        return dict(base, **{
            "ok": False,
            "verdict": VERDICT_UNKNOWN,
            "error": ov.error,
            "edges": [],
            "edges_all_pass": False,
            "questions": {},
            "vl": None,
            "overlay_path": None,
        })

    questions: dict[str, Any] = {}
    # The VL must read the TEXT-FREE render. Any text on the image competes with
    # the box contents, and a 7B-VL will report the annotation as the answer.
    vl_image = ov.vl_path or ov.overlay_path

    # ---- Q1: is a red box actually visible? (gating) ----
    if require_box_first:
        q1 = classify_yes_no(vl_image, "a red box drawn on the image",
                             prompt=PROMPT_Q1)
        questions["q1_red_box_present"] = q1.as_dict()
        if q1.verdict != VERDICT_PASS:
            return dict(base, **{
                "ok": True,
                "verdict": VERDICT_UNKNOWN,
                "edges": [e.as_dict() for e in ov.edges],
                "edges_all_pass": ov.all_pass,
                "questions": questions,
                "vl": None,
                "overlay_path": ov.overlay_path,
                "vl_path": ov.vl_path,
                "error": ("Q1 failed: model does not see a red box "
                          "(%s) — cannot classify the label"
                          % (q1.answer or q1.verdict)),
            })

    # ---- Q2: does the box content match the label? ----
    q2 = classify_yes_no(vl_image, label)
    questions["q2_text_in_box"] = q2.as_dict()

    edges_ok = ov.all_pass if require_edges else True
    if not edges_ok:
        # Geometry is wrong -> the answer to Q2 is not trustworthy either way.
        verdict = VERDICT_FAIL
    elif q2.verdict == VERDICT_PASS:
        verdict = VERDICT_PASS
    elif q2.verdict == VERDICT_FAIL:
        verdict = VERDICT_FAIL
    else:
        verdict = VERDICT_UNKNOWN

    return dict(base, **{
        "ok": True,
        "verdict": verdict,
        "edges": [e.as_dict() for e in ov.edges],
        "edges_all_pass": ov.all_pass,
        "questions": questions,
        "vl": q2.as_dict(),
        "overlay_path": ov.overlay_path,
        "vl_path": ov.vl_path,
        "error": None,
    })


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 3:
        print("usage: evidence_classify.py IMG LABEL [X1 Y1 X2 Y2 OUT]")
        raise SystemExit(1)
    if len(sys.argv) >= 8:
        out = classify_with_overlay(
            sys.argv[1],
            int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6]),
            sys.argv[2], sys.argv[7],
        )
    else:
        out = classify_yes_no(sys.argv[1], sys.argv[2]).as_dict()
    print(json.dumps(out, indent=2, ensure_ascii=False))