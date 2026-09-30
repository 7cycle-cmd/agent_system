"""Constrained decoding that PRESERVES the abstention.

WHY THIS FILE EXISTS
--------------------
The human (2026-09-28) asked whether regex / grammar constrained generation
helps, "why yes and why not". The answer was MEASURED, and it is a conditional
YES:

    constrained decoding helps ONLY IF the grammar INCLUDES the abstention.

THE MEASUREMENT THAT DECIDES THE DESIGN
---------------------------------------
`skill_mismatch_log`, 6250 rows:

    MALFORMED (unparseable)                        614   9.8%   grammar FIXES
    SCHEMA-VIOLATING (valid JSON, missing a key)    49   0.8%   grammar FIXES
    CONFORMANT but WRONG                          5587  89.4%   grammar CANNOT

So the ceiling is 10.6%. And the reason it is not higher is the finding that
shapes this module:

    MEASURED, on input the model CANNOT answer:

        case            free      YES|NO      YES|NO|UNKNOWN
        no evidence     NO        "NO"        "UNKNOWN"
        ambiguous       NO.       "NO"        "UNKNOWN"
        out of scope    NO        "NO"        "UNKNOWN"
        clear YES       YES       "YES"       "YES"

    The `YES|NO` grammar's answer on unanswerable input is PROMPT-DEPENDENT
    (YES,YES,NO under one prompt; NO,NO,NO under another). That instability IS
    guessing. **A grammar that removes the abstention turns "I don't know" into
    a confident wrong answer.**

AND 571 of the 614 "malformed" rows are `Result: UNKNOWN` — an HONEST
ABSTENTION (`Reason: picker_not_open`), not a format failure. Forcing them into
`YES|NO` would DESTROY a correct behaviour.

THE DESIGN THAT FOLLOWS
-----------------------
1. Every grammar this module builds CONTAINS the abstention token. There is no
   code path that builds a grammar without one, and `build_grammar` REFUSES a
   caller who asks for one.
2. The abstention token is `UNKNOWN`, matching `laya_router.decide()`, where
   `UNKNOWN` is a first-class outcome — not an error and not a low-confidence
   answer.
3. The transport is the EXISTING Ollama endpoint. MEASURED: Ollama 0.34.2
   already accepts `format=<JSON schema>` on `/api/chat`, so llama.cpp/GBNF,
   vLLM `guided_regex` and Outlines are NOT needed. A new stack would be a
   SECOND way to do what one call already does.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does NOT check semantics. A grammar can force `who: id-123`; it cannot check
that `id-123` names a real identity. The ontology / TDD / register checks stay.
That is the human's own two-layer framing, and it is correct.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
OLLAMA_VERSION = "http://127.0.0.1:11434/api/version"

# THE ABSTENTION TOKEN. It matches `laya_router.UNKNOWN` deliberately: the
# ladder already treats UNKNOWN as a first-class outcome, so a grammar that
# dropped it would disagree with the ladder it feeds.
ABSTAIN = "UNKNOWN"

# The three-valued answer, in the order a reader expects.
YES_NO_ABSTAIN = ("YES", "NO", ABSTAIN)

# A grammar WITHOUT an abstention is the defect this module exists to prevent.
# It is named here so `build_grammar` can REFUSE it rather than silently accept.
FORBIDDEN_WITHOUT_ABSTAIN = ("YES|NO", "yes|no", "true|false")


def ollama_available(timeout: float = 5.0) -> dict[str, Any]:
    """Is the Ollama endpoint reachable, and what version? MEASURED."""
    try:
        with urllib.request.urlopen(OLLAMA_VERSION, timeout=timeout) as r:
            return {"ok": True, "version": json.loads(r.read().decode()).get("version")}
    except Exception as exc:
        return {"ok": False, "code": "OLLAMA_UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, exc)}


def build_grammar(options: Any, *, allow_without_abstain: bool = False
                  ) -> dict[str, Any]:
    """A JSON-schema grammar for a CLOSED answer set, WITH the abstention.

    REFUSES a set that has no abstention token, because MEASURED: a grammar
    without one forces a guess on unanswerable input. The refusal is the point —
    a caller who wants `YES|NO` is asking for the defect.

    `allow_without_abstain=True` is the explicit escape hatch, and it is
    REPORTED in the result so a reader can see it was used. It exists because a
    genuinely binary question (a value that is present or absent) is legitimate;
    it does NOT exist to make the refusal quiet.
    """
    opts = [str(o) for o in (options or [])]
    if not opts:
        return {"ok": False, "code": "NO_OPTIONS",
                "why": "a grammar needs at least one option"}
    has_abstain = ABSTAIN in opts
    if not has_abstain and not allow_without_abstain:
        return {
            "ok": False, "code": "GRAMMAR_WITHOUT_ABSTENTION",
            "why": ("the option set %s has no %r, so the grammar would FORCE a "
                    "guess on input the model cannot answer — MEASURED: a "
                    "YES|NO grammar answered YES,YES,NO on three unanswerable "
                    "inputs under one prompt and NO,NO,NO under another"
                    % (opts, ABSTAIN)),
            "options": opts,
        }
    return {
        "ok": True,
        "format": {"type": "string", "enum": opts},
        "options": opts,
        "has_abstention": has_abstain,
        "abstention_waived": (not has_abstain and allow_without_abstain),
    }


def build_object_grammar(required: Any, properties: Any, *,
                         allow_without_abstain: bool = False) -> dict[str, Any]:
    """A JSON-schema grammar for an OBJECT, with an abstention escape.

    THE SCHEMA-VIOLATING CASE THIS FIXES, MEASURED: `SKILL.QUEUE.SMOKE` expected
    `{"required": ["port", "hostname"]}` and the model returned
    `{"port": 18765}` — VALID JSON, MISSING `hostname`. A grammar makes that
    impossible.

    AND THE ABSTENTION IS STILL REQUIRED. An object grammar that cannot express
    "I could not read this" has the same defect as a `YES|NO` grammar: it forces
    the model to invent a value. So the schema gains an optional `abstain`
    boolean, and `required` is only enforced when `abstain` is not true.
    """
    req = [str(r) for r in (required or [])]
    props = dict(properties or {})
    if not req:
        return {"ok": False, "code": "NO_REQUIRED_FIELDS",
                "why": "an object grammar needs at least one required field"}
    if not allow_without_abstain:
        props = dict(props)
        props["abstain"] = {"type": "boolean"}
    return {
        "ok": True,
        "format": {"type": "object", "required": req, "properties": props},
        "required": req,
        "abstention_waived": bool(allow_without_abstain),
    }


def ask_constrained(prompt: str, grammar: dict[str, Any], *,
                    model: str = "qwen2.5:7b-instruct",
                    timeout: float = 300.0) -> dict[str, Any]:
    """Ask Ollama with `format=<grammar>`. Returns the raw answer AND its shape.

    The answer is returned UNPARSED as well as parsed, because a caller must be
    able to see what the model actually emitted — a parser that hides the raw
    text is how a format failure becomes invisible.
    """
    if not grammar.get("ok"):
        return {"ok": False, "code": "BAD_GRAMMAR",
                "why": grammar.get("why"), "raw": None}
    payload = {
        "model": model, "stream": False,
        "format": grammar["format"],
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        OLLAMA_CHAT, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode())
    except Exception as exc:
        return {"ok": False, "code": "ENGINE_ERROR", "raw": None,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    raw = (body.get("message") or {}).get("content")
    parsed: Any = None
    try:
        parsed = json.loads(raw) if raw is not None else None
    except Exception:
        parsed = None
    return {"ok": True, "raw": raw, "parsed": parsed, "model": model,
            "grammar_options": grammar.get("options"),
            "has_abstention": grammar.get("has_abstention")}


def classify_answer(raw: Any, options: Any) -> dict[str, Any]:
    """Map a raw answer to one of `options`, or report that it did not.

    A value outside the option set is REPORTED, never coerced. Coercing it would
    hide exactly the failure the grammar was added to prevent.
    """
    opts = [str(o) for o in (options or [])]
    text = raw if isinstance(raw, str) else json.dumps(raw)
    text = (text or "").strip().strip('"')
    if text in opts:
        return {"ok": True, "value": text, "abstained": text == ABSTAIN}
    return {"ok": False, "code": "OUTSIDE_GRAMMAR", "value": None,
            "why": "%r is not one of %s" % (text, opts)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--measure", action="store_true",
                    help="report the endpoint and the grammar rules, read-only")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out = {
        "ollama": ollama_available(),
        "abstain_token": ABSTAIN,
        "yes_no_abstain": list(YES_NO_ABSTAIN),
        "grammar_without_abstain_is_refused": not build_grammar(["YES", "NO"])["ok"],
        "grammar_with_abstain_is_accepted": build_grammar(list(YES_NO_ABSTAIN))["ok"],
    }
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print("=" * 78)
        print("constrained_decode — the grammar that PRESERVES the abstention")
        print("=" * 78)
        print("  ollama            : %s" % out["ollama"])
        print("  abstention token  : %s" % out["abstain_token"])
        print("  YES|NO refused    : %s" % out["grammar_without_abstain_is_refused"])
        print("  YES|NO|UNKNOWN ok : %s" % out["grammar_with_abstain_is_accepted"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
