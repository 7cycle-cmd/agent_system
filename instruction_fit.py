"""instruction_fit.py — does a text block fit a source's instruction box?

WHY THIS EXISTS
---------------
`docs/paste_outside_worker_skill_short.md` tells the reader to run this tool
before pasting a block into Gemini's custom-instruction box. The tool did not
exist, so the doc's advice could not be followed and the "safe budget" of 1500
was a guess nobody could check.

THE RULE
--------
`source.instruction_limit` is the MEASURED character limit of that box.
**NULL means NOT MEASURED — never "unlimited".** A NULL limit is reported as
UNVERIFIED, and the caller is told the number is unknown rather than being told
the block fits. Treating NULL as unlimited is how an over-long block gets
silently truncated by the app.

Exit codes: 0 = fits (or advisory), 1 = does not fit, 2 = usage error.
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# Used ONLY when a source has no measured limit. It is a CONSERVATIVE GUESS, and
# every report says so — a guess presented as a fact is worse than no number.
CONSERVATIVE_BUDGET = 1500


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def get_source(source_key: str, *, db_path: Path | str | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM source WHERE source_key = ?", (source_key,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def extract_block(text: str, *, begin: str = "=== BEGIN ===", end: str = "=== END ===") -> str:
    """Return the text between the BEGIN/END markers, or the whole text.

    The paste docs wrap the block in markers so the surrounding commentary is
    not pasted. Measuring the WHOLE file would over-count by the commentary and
    report a false failure.

    CRITICAL: the marker must be matched as a WHOLE LINE, not as a substring.
    The doc's own summary table mentions `=== BEGIN ===` in prose, so a plain
    `split(begin)` picks the TABLE's mention and measures the wrong region —
    which silently over-reports the block size.
    """
    lines = text.splitlines()
    start = None
    stop = None
    for i, line in enumerate(lines):
        if start is None and line.strip() == begin:
            start = i + 1
        elif start is not None and line.strip() == end:
            stop = i
            break
    if start is not None and stop is not None and stop >= start:
        return "\n".join(lines[start:stop]).strip()
    return text.strip()


def measure(text: str) -> dict[str, int]:
    """Character counts. `chars` is what a character limit counts."""
    return {
        "chars": len(text),
        "chars_no_newlines": len(text.replace("\n", "").replace("\r", "")),
        "lines": text.count("\n") + 1 if text else 0,
        "words": len(text.split()),
    }


def check_fit(
    text: str,
    *,
    source_key: str | None = None,
    limit: int | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Compare a block against a limit. Returns a report, never raises on fit."""
    m = measure(text)
    source = None
    verified = False

    if limit is not None:
        # The caller supplied the number explicitly, so it is a FACT for this
        # call — not a guess. Reporting it as unverified would be wrong.
        verified = True
    elif source_key:
        source = get_source(source_key, db_path=db_path)
        if source is None:
            return {
                "ok": False,
                "error": "source not found: %s" % source_key,
                "measured": m,
            }
        raw = source.get("instruction_limit")
        if raw is not None:
            limit = int(raw)
            verified = True

    if limit is None:
        # NOT MEASURED. Report the conservative budget as a GUESS, and say so.
        limit = CONSERVATIVE_BUDGET
        verified = False

    fits = m["chars"] <= limit
    return {
        "ok": True,
        "source_key": source_key,
        "source_name": (source or {}).get("name"),
        "limit": limit,
        "limit_verified": verified,
        "limit_source": "measured" if verified else "conservative_guess",
        "measured": m,
        "fits": fits,
        "headroom": limit - m["chars"],
        "over_by": 0 if fits else m["chars"] - limit,
        "verdict": (
            "FITS" if fits else "TOO LONG"
        ) + ("" if verified else " (limit UNVERIFIED — conservative guess)"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="does a text block fit an instruction box?")
    ap.add_argument("--db", default=None)
    ap.add_argument("--text-file", default=None, help="file to measure")
    ap.add_argument("--text", default=None, help="literal text to measure")
    ap.add_argument("--source-key", default=None, help="source whose limit to use")
    ap.add_argument("--limit", type=int, default=None, help="explicit limit (overrides source)")
    ap.add_argument("--raw", action="store_true", help="measure the whole file, not the BEGIN/END block")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.text_file:
        p = Path(args.text_file)
        if not p.is_file():
            print("text file not found: %s" % p, file=sys.stderr)
            return 2
        text = p.read_text(encoding="utf-8", errors="replace")
        if not args.raw:
            text = extract_block(text)
    elif args.text is not None:
        text = args.text
    else:
        ap.print_help()
        return 2

    report = check_fit(text, source_key=args.source_key, limit=args.limit, db_path=args.db)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        if not report.get("ok"):
            print("ERROR: %s" % report["error"], file=sys.stderr)
            return 2
        m = report["measured"]
        print("source      : %s" % (report["source_key"] or "(none)"))
        print("limit       : %s  [%s]" % (report["limit"], report["limit_source"]))
        print("chars       : %s" % m["chars"])
        print("lines/words : %s / %s" % (m["lines"], m["words"]))
        print("headroom    : %s" % report["headroom"])
        print("verdict     : %s" % report["verdict"])
        if not report["limit_verified"]:
            print()
            print("NOTE: this source's instruction_limit is NULL (NOT MEASURED).")
            print("      The %s budget above is a CONSERVATIVE GUESS, not a fact."
                  % CONSERVATIVE_BUDGET)
            print("      Measure the real box, then store it:")
            print("        python -c \"import skill_library_api as s; "
                  "print(s.upsert_source(source_key='%s', name='...', "
                  "instruction_limit=<REAL_NUMBER>))\"" % (report["source_key"] or "<key>"))

    return 0 if report.get("fits") else 1


if __name__ == "__main__":
    raise SystemExit(main())