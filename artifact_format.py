"""artifact_format.py — is a WRITTEN artifact actually in the standard format?

THE USER'S GOAL (2026-09-24)
----------------------------
    "worker not writing is researcher, and they are not easy to have mistake and
     coding writing and verfity can have standardize format, it is hard to have
     bad writing skill"

DECOMPOSED, and the two halves measure very differently:

  * **A researcher cannot err** — NOT declarable today. MEASURED:
    `worker_registry.role_id` is NULL on **all 6** workers, and the rights
    vocabulary (`coding_writing`, `terminal`, ...) is keyed on MODE, not WORKER, so
    "this worker only reads" has nowhere to be written. `rights_gap()` REPORTS this
    with numbers. Changing WHO may write is a policy decision, so it is MEASURED
    here and not decided.

  * **Writing has a standard format, so bad writing is HARD** — the real gap, and
    the one this module closes. MEASURED: `docs/snippets/template_plan.md` (12
    headings), `template_agent_execution_log.md` (7), `template_qc_report.md` (6)
    ALL exist — and **nothing checks an artifact against any of them**. A format
    nobody checks is a SUGGESTION, not a standard. That is precisely why "bad
    writing skill" is currently indistinguishable from good.

THE TEMPLATE IS THE SSOT
------------------------
`required_sections()` PARSES the template file. It does NOT keep its own list,
because a second copy of one fact is a second place for it to disagree with
itself — the defect this repo has removed repeatedly (five times this session
alone). If a template gains a section, the standard changes by itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
SNIPPETS = BASE / "docs" / "snippets"
DB_PATH = BASE / "agent.db"

# The declared artifact KINDS and the template each one must follow. A KIND is a
# name for a document class, so `check()` can be asked about a class rather than a
# file path.
KINDS: dict[str, dict[str, Any]] = {
    "plan": {"template": "template_plan.md", "prefix": "plan_",
             "suffix": ".md", "where": "qc_evidence/"},
    "agent_log": {"template": "template_agent_execution_log.md",
                  "prefix": "agent_log_", "suffix": ".md", "where": "qc_evidence/"},
    "qc_report": {"template": "template_qc_report.md", "prefix": "qc_report_",
                  "suffix": ".md", "where": "qc_evidence/"},
}

# Headings a template uses as PLACEHOLDER text rather than as a required section.
_PLACEHOLDER = re.compile(r"\{\{|\}\}")

# A heading that is ENTIRELY a code span is an EXAMPLE, not a section.
#
# MEASURED BUG IN THIS MODULE'S FIRST VERSION: `template_agent_execution_log.md`
# writes
#     ### `path/to/file`
# to SHOW the reader what a diff heading looks like. Parsing it as a required
# section demanded that EVERY execution log contain a heading literally called
# `path/to/file` — which no real log can satisfy, so the check refused correct
# artifacts and its audit reported 11.9% conformance partly because of ITSELF.
_EXAMPLE_HEADING = re.compile(r"^`[^`]+`$")


def template_path(kind: str) -> Path | None:
    spec = KINDS.get(kind)
    if not spec:
        return None
    return SNIPPETS / spec["template"]


def required_sections(kind: str) -> dict[str, Any]:
    """The sections a `kind` MUST contain, PARSED from its template.

    A heading is level 2 or deeper (`## `, `### `). The level-1 title is the
    document's name, not a section, so it is excluded — otherwise every artifact
    would need its own title to match the template's placeholder.

    REFUSES:
      * `UNKNOWN_KIND`    — the kind is not declared.
      * `TEMPLATE_ABSENT` — the template file is missing, so the standard cannot
                            be known. Returning an EMPTY list here would make every
                            artifact "conformant", which is the failure mode of a
                            checker that fails open.
    """
    if kind not in KINDS:
        return {"ok": False, "reason": "UNKNOWN_KIND", "kind": kind,
                "declared": sorted(KINDS)}
    p = template_path(kind)
    if p is None or not p.exists():
        return {"ok": False, "reason": "TEMPLATE_ABSENT", "kind": kind,
                "template": str(p) if p else None}
    text = p.read_text(encoding="utf-8", errors="replace")
    sections: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        hashes = len(s) - len(s.lstrip("#"))
        if hashes < 2:
            continue  # the document TITLE is not a section
        title = s.lstrip("#").strip()
        if not title or _PLACEHOLDER.search(title):
            continue
        if _EXAMPLE_HEADING.match(title):
            continue  # an example the template SHOWS, not a section it REQUIRES
        sections.append(title)
    return {"ok": True, "kind": kind, "template": str(p.relative_to(BASE)),
            "sections": sections, "count": len(sections),
            "would_be_red_if": "an artifact omits a section its template declares"}


def _headings(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            t = s.lstrip("#").strip()
            if t:
                out.append(t)
    return out


# A leading ORDINAL (`3. `, `4) `) is DECORATION, not part of the section's name.
#
# MEASURED BUG IN THIS MODULE'S SECOND VERSION: the template declares
# `## 3. LOCKED QC CHECKLIST` and an artifact wrote `## 4. LOCKED QC CHECKLIST`.
# The section is THE SAME — only its ordinal moved, because the artifact had one
# extra section before it. Refusing that artifact would reject a correctly
# structured document for a cosmetic reason, and a checker that refuses correct
# work is worse than no checker: it trains people to ignore it.
_ORDINAL = re.compile(r"^\s*\d+\s*[.)]\s*")


def _bare(section: str) -> str:
    """A section name with its leading ordinal removed, for comparison."""
    return _ORDINAL.sub("", str(section)).strip().casefold()


def check(text: str, kind: str) -> dict[str, Any]:
    """Does this TEXT conform to `kind`'s template? Every missing section NAMED.

    A conformant artifact must contain EVERY required section. The check reports
    `missing` (a list, not a boolean), so the fix is a list of headings rather than
    a feeling that something is off.
    """
    spec = required_sections(kind)
    if not spec.get("ok"):
        return spec
    if not str(text or "").strip():
        return {"ok": False, "reason": "EMPTY_ARTIFACT", "kind": kind,
                "required": spec["sections"], "missing": list(spec["sections"])}
    present = _headings(text)
    required = spec["sections"]
    # A section is PRESENT when a heading CONTAINS it (ordinals ignored). The
    # templates write headings with template variables in some places, and an
    # artifact may add a leading number or a suffix, so containment on the BARE name
    # is the honest test. Exact equality would refuse a correctly-formatted artifact
    # for a cosmetic reason.
    bare_present = [_bare(h) for h in present]
    missing = [r for r in required
               if not any(_bare(r) in h or h in _bare(r) for h in bare_present)]
    extra = [h for h in present
             if not any(_bare(r) in _bare(h) or _bare(h) in _bare(r)
                        for r in required)]
    return {"ok": not missing, "kind": kind, "required": required,
            "present": present, "missing": missing, "extra": extra,
            "verdict": "CONFORMANT" if not missing else "NON_CONFORMANT",
            "would_be_red_if": "an artifact omits a section its template declares"}


def kind_from_filename(name: str) -> str | None:
    """Which KIND a filename declares. `None` when it is not an artifact.

    The declared naming is `plan_<task>.md` / `agent_log_<task>.md` /
    `qc_report_<task>.md`, which is also what the plan protocol requires, so the
    filename is the cheapest correct signal and a caller does not have to say.
    """
    stem = Path(str(name)).name
    for kind, spec in KINDS.items():
        if stem.startswith(str(spec["prefix"])) and stem.endswith(str(spec["suffix"])):
            return kind
    return None


def check_file(path: Path | str, kind: str | None = None) -> dict[str, Any]:
    """Check ONE file. The kind is inferred from the filename when not supplied."""
    p = Path(path)
    if kind is None:
        kind = kind_from_filename(p.name)
        if kind is None:
            return {"ok": False, "reason": "NOT_AN_ARTIFACT", "path": str(p),
                    "why": ("the filename declares no artifact kind; declared "
                            "prefixes: %s" % ", ".join(
                                str(s["prefix"]) for s in KINDS.values()))}
    if not p.exists():
        return {"ok": False, "reason": "NO_SUCH_FILE", "path": str(p)}
    text = p.read_text(encoding="utf-8", errors="replace")
    out = check(text, kind)
    out["path"] = str(p)
    return out


def audit(directory: Path | None = None) -> dict[str, Any]:
    """How many existing artifacts conform? A NUMBER, so the state is measured."""
    d = directory or (BASE / "qc_evidence")
    results: list[dict[str, Any]] = []
    if d.exists():
        for p in sorted(d.glob("*.md")):
            k = kind_from_filename(p.name)
            if k is None:
                continue
            r = check_file(p, k)
            results.append({"file": p.name, "kind": k, "ok": bool(r.get("ok")),
                            "missing": r.get("missing", []),
                            "reason": r.get("reason")})
    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    # PER-KIND BREAKDOWN. The head-line percentage hides the finding: MEASURED, all
    # 13 conformant artifacts are `plan_*` and ZERO `agent_log_*` conform — while the
    # logs are NOT empty (9-14 headings each). So the plan template IS followed and
    # the execution-log template is DECLARED BUT NEVER FOLLOWED, which is the gap a
    # single number would conceal.
    per_kind: dict[str, dict[str, int]] = {}
    for r in results:
        b = per_kind.setdefault(r["kind"], {"artifacts": 0, "conformant": 0})
        b["artifacts"] += 1
        b["conformant"] += 1 if r["ok"] else 0
    for k, b in per_kind.items():
        b["pct"] = round(100.0 * b["conformant"] / b["artifacts"], 1) \
            if b["artifacts"] else 0.0
    return {"ok": True, "directory": str(d), "artifacts": len(results),
            "conformant": len(ok), "non_conformant": len(bad),
            "pct": (round(100.0 * len(ok) / len(results), 1) if results else 0.0),
            "per_kind": per_kind,
            "failures": bad,
            "would_be_red_if": "an artifact omits a section its template declares"}


def rights_gap(conn: sqlite3.Connection) -> dict[str, Any]:
    """WHO may write, and the hole in that answer — MEASURED, not decided.

    The user's other half — "a researcher (read-only worker) is not easy to make a
    mistake" — cannot be declared today, and this REPORTS why with numbers:
      * `worker_registry.role_id` is NULL for every worker, so no worker has a role;
      * the rights vocabulary has no read-only/research right;
      * the existing rights are keyed on MODE, so a WORKER has no rights of its own.
    """
    out: dict[str, Any] = {"ok": True}
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT worker_id, worker_key, worker_type, role_id FROM worker_registry")]
    except sqlite3.OperationalError as e:
        return {"ok": False, "reason": "NO_worker_registry", "why": str(e)}
    out["workers"] = len(rows)
    out["without_role"] = [r["worker_key"] for r in rows if r.get("role_id") is None]
    out["role_id_column_present"] = all("role_id" in r for r in rows)
    try:
        rights = sorted({str(r[0]) for r in conn.execute(
            "SELECT DISTINCT right_key FROM mode_right_registry")})
    except sqlite3.OperationalError:
        rights = []
    out["rights_vocabulary"] = rights
    out["has_read_only_right"] = any(
        ("read" in r.lower() or "research" in r.lower()) for r in rights)
    out["has_verification_right"] = any("verif" in r.lower() for r in rights)
    out["keyed_on"] = "mode"
    out["verdict"] = ("WHO_MAY_WRITE_IS_NOT_DECLARABLE"
                      if out["without_role"] else "EVERY_WORKER_HAS_A_ROLE")
    out["why"] = ("a worker has no role and the rights are keyed on MODE, so "
                  "'this worker only reads' has nowhere to be written")
    out["not_decided_here"] = ("changing who may write is a policy decision; this "
                               "reports the gap so a later plan can act on numbers")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sections", metavar="KIND", nargs="?", const="__all__",
                    help="the sections each template declares")
    ap.add_argument("--audit", action="store_true",
                    help="how many artifacts in qc_evidence conform")
    ap.add_argument("--check", metavar="PATH", default=None,
                    help="check ONE artifact against its template")
    ap.add_argument("--rights-gap", action="store_true",
                    help="who may write, and the hole in that answer")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.rights_gap:
            out = rights_gap(conn)
        elif a.audit:
            out = audit()
        elif a.check:
            out = check_file(a.check)
        else:
            kinds = sorted(KINDS) if getattr(a, "sections", None) == "__all__" \
                or a.sections is None else [a.sections]
            out = {"ok": True, "kinds": {
                k: required_sections(k) for k in kinds}}
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    if a.rights_gap:
        print("verdict: %s" % out.get("verdict"))
        print("workers: %s | without a role: %s"
              % (out.get("workers"), len(out.get("without_role") or [])))
        print("rights : %s" % ", ".join(out.get("rights_vocabulary") or []))
        print("has a read-only right : %s" % out.get("has_read_only_right"))
        print("has a verification right: %s" % out.get("has_verification_right"))
        print("why    : %s" % out.get("why"))
    elif a.audit:
        print("artifacts : %d" % out["artifacts"])
        print("conformant: %d (%s%%)" % (out["conformant"], out["pct"]))
        print("NON-conformant: %d" % out["non_conformant"])
        print()
        print("PER KIND (the head-line % hides which template is followed):")
        for k, b in sorted(out["per_kind"].items()):
            print("   %-10s %d of %d conform (%s%%)"
                  % (k, b["conformant"], b["artifacts"], b["pct"]))
        print()
        for f in out["failures"][:12]:
            print("   %-52s missing=%s" % (f["file"], f["missing"] or f["reason"]))
    elif a.check:
        print("%s : %s" % (out.get("path"), out.get("verdict") or out.get("reason")))
        if out.get("missing"):
            print("   missing sections:")
            for m in out["missing"]:
                print("      - %s" % m)
    else:
        for k, spec in (out.get("kinds") or {}).items():
            if not spec.get("ok"):
                print("%-10s REFUSED: %s" % (k, spec.get("reason")))
                continue
            print("%-10s %-44s %d sections"
                  % (k, spec["template"], spec["count"]))
            for s in spec["sections"]:
                print("      - %s" % s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
