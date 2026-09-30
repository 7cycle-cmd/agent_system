"""Run the plan gate so that ITS OWN BREAKAGE CANNOT BE SILENT.

WHY THIS EXISTS — MEASURED, NOT ARGUED (2026-09-29)
===================================================

The gate is a `PreToolUse` hook. MEASURED end-to-end, by appending a deliberate
`SyntaxError` to `scripts/plan_gate.py` and then attempting a write:

    gate with a syntax error   ->  exit 1, stdout EMPTY
    the write                   ->  SUCCEEDED
    probe file                  ->  written

VS Code reads "non-zero exit with no stdout" as ALLOW, so the gate is
**decorative whenever its own source cannot be parsed**, and the state is
SILENT: no log line, no message, nothing for the human to notice. A gate that
disappears without saying so is worse than no gate, because it is believed.

WHY IT CANNOT SIMPLY "FAIL CLOSED"
----------------------------------
The hook has a 10 s timeout (`.github/hooks/plan_gate.json`) and VS Code's
contract is that a hook fault must not wedge the editor. Denying on a fault would
turn a gate bug into an inability to edit ANY file — the 2026-09-18 deadlock this
repo has already paid for. So the honest fix is a THIRD state:

    healthy   ->  the gate DECIDES   (deny / allow), exactly as before
    degraded  ->  the gate CANNOT DECIDE, the write is permitted, and a
                  `systemMessage` SAYS SO, naming the file and the exception

WHAT THIS MUST NOT DO
---------------------
* It must NOT re-implement, soften or reinterpret any decision. It runs the gate
  and propagates its stdout and its exit code VERBATIM, so `what the gate
  decides` and `what the editor does` cannot drift.
* It must NOT block. Exit code is 0 in both states.

Usage (identical to the gate):
    python scripts/plan_gate_launcher.py < payload.json
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(BASE, "plan_gate.py")

# The message MUST be specific enough to act on: WHAT is broken, WHERE, and the
# CONSEQUENCE. A generic "gate failed" would be indistinguishable from noise.
def degraded_message(path: str, exc: BaseException | str) -> str:
    detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return (
        "# ⚠ PLAN GATE DEGRADED — WRITES ARE NOT BEING CHECKED\n\n"
        "The entity/allowlist gate could not be RUN, so this write was allowed\n"
        "WITHOUT a coverage check. This is a FAULT, not a mode.\n\n"
        "* file      : `%s`\n"
        "* reason    : %s\n\n"
        "Fix the gate, then re-attempt the write. Until then every write is\n"
        "unchecked while looking normal, which is the failure this message\n"
        "exists to make visible.\n"
        % (os.path.relpath(path, os.path.dirname(BASE)), detail)
    )


def gate_is_parsable() -> tuple[bool, str]:
    """Can the gate even be COMPILED? Checked before running it.

    WHY `ast.parse` AND NOT A TRY/RUN: a gate that raises at import time (a bad
    edit, a missing import, a syntax error) exits non-zero with no stdout, which
    the editor reads as ALLOW. Parsing first converts that silent allow into a
    NAMED fault we can report.
    """
    try:
        with open(GATE, "rb") as f:
            ast.parse(f.read(), filename=GATE)
    except SyntaxError as exc:
        return False, "%s:%s: %s" % (os.path.basename(GATE), exc.lineno, exc.msg)
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, exc)
    return True, ""


def emit(obj: dict) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))
    sys.stdout.flush()
    return 0


def main() -> int:
    # The gate's own kill switch must still work, and must not be reported as a
    # fault: an operator who turned the gate off did not break it.
    if os.environ.get("PLAN_GATE", "").strip().lower() in ("off", "0", "false"):
        return 0

    raw = sys.stdin.read()

    ok, why = gate_is_parsable()
    if not ok:
        sys.stderr.write("[plan_gate_launcher] DEGRADED: %s\n" % why)
        return emit({"systemMessage": degraded_message(GATE, why)})

    try:
        p = subprocess.run(
            [sys.executable, GATE],
            input=raw, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=9,
        )
    except subprocess.TimeoutExpired:
        return emit({"systemMessage": degraded_message(
            GATE, "the gate exceeded its 9s budget (the hook allows 10s)")})
    except Exception as exc:
        return emit({"systemMessage": degraded_message(GATE, exc)})

    out = p.stdout or ""

    # A NON-ZERO EXIT WITH NO DECISION IS A FAULT, and must be REPORTED rather
    # than passed through as the silent allow it looks like.
    if p.returncode != 0 and not out.strip():
        err = (p.stderr or "").strip().splitlines()
        return emit({"systemMessage": degraded_message(
            GATE, err[-1] if err else "exit %s with no output" % p.returncode)})

    sys.stdout.write(out)
    sys.stdout.flush()
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())