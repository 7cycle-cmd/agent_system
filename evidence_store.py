"""Evidence store — one folder per evidence, with an ID and a report.

Layout:
    evidence/
      <evidence_id>/
        shot.png          raw screenshot (what was actually captured)
        overlay.png       red guide box + full-span lines + edge verdicts
        crop.png          the rect cropped out (for a human close-up)
        classify.json     machine verdict (edges + VL answers + reasons)
        report.md         human report

Evidence ID format (grep-friendly, traceable back to the target):

    EVID-<target_id>-<YYYYmmdd-HHMMSS>[-<n>]

e.g. `EVID-perm_default-20260919-143355`
The `-n` suffix appears only on a same-second collision, so an ID is never
reused and never overwritten.

The store is append-only: it never rewrites an existing evidence folder. A
re-run creates a NEW id, so history is preserved and a passing run cannot
quietly replace a failing one.

Writes only under the evidence root. Never touches chat_permission.json or any
other state file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# Where evidence is written. Overridable by env var so a TEST RUN cannot pollute
# the production evidence directory.
#
# Why this exists: the gate tests call open_evidence() to exercise refusals, and
# each call created a real folder under `evidence/`. Repeated runs left hundreds
# of `EVID-tdd_noproof-*` / `EVID-gate_*` folders in the production tree. A test
# suite must not write into a real output directory — the noise is
# indistinguishable from real evidence when someone lists the folder later.
#
# Tests set this to a tmp dir (via the env var or set_evidence_root()).
EVIDENCE_ROOT_ENV = "MOUSE_SPOT_EVIDENCE_ROOT"
DEFAULT_EVIDENCE_ROOT = BASE_DIR / "evidence"


def _initial_root() -> Path:
    raw = (os.environ.get(EVIDENCE_ROOT_ENV) or "").strip()
    return Path(raw) if raw else DEFAULT_EVIDENCE_ROOT


EVIDENCE_ROOT: Path = _initial_root()


def set_evidence_root(path: str | Path | None) -> Path:
    """Point the store at another root. Returns the previous root.

    Used by tests (and by a caller that must write evidence elsewhere). Passing
    None restores the default. The env var is NOT modified, so a later import
    elsewhere is unaffected.
    """
    global EVIDENCE_ROOT
    prev = EVIDENCE_ROOT
    EVIDENCE_ROOT = Path(path) if path else _initial_root()
    return prev


def is_default_root() -> bool:
    """True when writing to the real evidence directory (not a test root)."""
    try:
        return EVIDENCE_ROOT.resolve() == DEFAULT_EVIDENCE_ROOT.resolve()
    except Exception:
        return EVIDENCE_ROOT == DEFAULT_EVIDENCE_ROOT


_ID_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")

FILES = {
    "shot": "shot.png",
    "overlay": "overlay.png",
    "overlay_vl": "overlay_vl.png",
    "crop": "crop.png",
    # The user-spec 2-PNG QC pair (evidence_redbox.py): the FULL SCREEN with the
    # red box drawn on it lives HERE, next to shot.png, so the pair is stored in
    # the same place. The cut-to-the-box render goes to evidence_redbox/<EVID>.png.
    "redbox": "redbox.png",
    "redbox_vl": "redbox_vl.png",
    "classify": "classify.json",
    "report": "report.md",
}


def _slug(text: str, limit: int = 48) -> str:
    s = _ID_SAFE.sub("_", str(text or "").strip())
    return (s[:limit] or "unknown").strip("_") or "unknown"


def new_evidence_id(target_id: str, when: float | None = None) -> str:
    """Build a unique evidence id. Disambiguates with -2, -3 on collision."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))
    base = "EVID-%s-%s" % (_slug(target_id), ts)
    if not (EVIDENCE_ROOT / base).exists():
        return base
    n = 2
    while (EVIDENCE_ROOT / ("%s-%d" % (base, n))).exists():
        n += 1
    return "%s-%d" % (base, n)


@dataclass
class EvidenceRecord:
    evidence_id: str
    target_id: str
    dir: str
    files: dict[str, str] = field(default_factory=dict)
    verdict: str = "UNKNOWN"
    created_at: str = ""
    task_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "target_id": self.target_id,
            "dir": self.dir,
            "files": self.files,
            "verdict": self.verdict,
            "created_at": self.created_at,
            "task_id": self.task_id,
        }


def open_evidence(target_id: str, task_id: str | None = None) -> EvidenceRecord:
    """Create a fresh evidence folder and return its record.

    `task_id` is optional provenance: it links the capture back to the task
    that produced it, so the report table can show date | test id | task id.
    """
    eid = new_evidence_id(target_id)
    d = EVIDENCE_ROOT / eid
    d.mkdir(parents=True, exist_ok=True)
    return EvidenceRecord(
        evidence_id=eid,
        target_id=str(target_id),
        dir=str(d),
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        task_id=str(task_id or ""),
    )


def save_classify(
    rec: EvidenceRecord,
    payload: dict[str, Any],
    *,
    enforce_proof: bool = True,
) -> str:
    """Persist the machine verdict JSON. Returns the path.

    WRITE GATE (env_task_proof contract): a payload that asserts a JUDGEMENT
    (PASS/FAIL) may not be written unless it carries a usable proof record.
    Without this the same root cause recurs forever: a rule exists in a skill
    file, nothing enforces it, and a capture of the wrong window is recorded as
    a confident verdict.

    UNKNOWN is NOT a judgement, so it is always writable — "we learned nothing"
    must stay recordable, otherwise the honest outcome is the one that gets
    suppressed.

    `enforce_proof=False` is an explicit escape hatch for tests and for
    pre-contract historical replay; it must be passed deliberately.
    """
    if enforce_proof:
        try:
            import env_proof
        except Exception:
            env_proof = None
        if env_proof is not None:
            # Rule 1: a JUDGEMENT needs a proof record.
            if env_proof.proof_required(payload):
                ok, reasons = env_proof.proof_status(payload)
                if not ok:
                    # Refuse loudly. A silently-downgraded verdict would be the
                    # same defect in a new costume.
                    raise env_proof.ProofRequired(
                        reasons, verdict=str(payload.get("verdict") or "")
                    )
            # Rule 2: the classification must be consistent with the observed
            # state. This is what rejects `geometry_fail + FAIL` when the
            # container is known absent — the real failure shape.
            ok_cls, violations = env_proof.validate_classification(payload)
            if not ok_cls:
                raise env_proof.ProofRequired(
                    ["inconsistent classification: " + v for v in violations],
                    verdict=str(payload.get("verdict") or ""),
                )
    path = Path(rec.dir) / FILES["classify"]
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rec.files["classify"] = str(path)
    rec.verdict = str(payload.get("verdict") or "UNKNOWN")
    return str(path)


def copy_artifact(rec: EvidenceRecord, key: str, src: str | Path) -> str | None:
    """Copy a produced artefact into the evidence folder under a stable name."""
    s = Path(str(src))
    if not s.is_file():
        return None
    dest = Path(rec.dir) / FILES.get(key, s.name)
    try:
        shutil.copy2(s, dest)
    except Exception:
        return None
    rec.files[key] = str(dest)
    return str(dest)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _mark(verdict: str) -> str:
    return {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}.get(verdict, "?")


def build_report(rec: EvidenceRecord, payload: dict[str, Any]) -> str:
    """Render the human report. Markdown so it reads well in any editor.

    TWO SHAPES. The original shape is a GEOMETRY record: a rect, four edge
    verdicts and a VL answer. A second shape exists — a MEASUREMENT record,
    which carries `checks` / `measured` and no rect at all (e.g. the
    `llm_status_*` environment records).

    WHY THIS BRANCHES (measured 2026-09-20): the template was hard-coded for
    geometry, so a MEASUREMENT record rendered
    `rect (real screen px) | (None,None)-(None,None)` and
    `edges all PASS: **no**` — on a record whose verdict was **PASS**. The
    report contradicted its own verdict. A report that says "no" next to PASS
    is exactly the false-signal shape this repo keeps having to fix, so each
    section is now emitted only when the data for it exists.
    """
    vl = payload.get("vl") or {}
    edges = payload.get("edges") or []
    rect = payload.get("rect_real") or {}
    q = payload.get("questions") or {}
    checks = payload.get("checks") or {}
    measured = payload.get("measured") or {}

    has_rect = any(rect.get(k) is not None for k in ("x1", "y1", "x2", "y2"))

    lines: list[str] = []
    lines.append("# Evidence Report — %s" % rec.evidence_id)
    lines.append("")
    lines.append("**FINAL VERDICT: %s**" % payload.get("verdict", "UNKNOWN"))
    lines.append("")

    lines.append("## Where")
    lines.append("")
    lines.append("| field | value |")
    lines.append("|---|---|")
    lines.append("| evidence id | `%s` |" % rec.evidence_id)
    lines.append("| target id | `%s` |" % rec.target_id)
    lines.append("| task id | `%s` |" % (payload.get("task_id") or rec.task_id or "-"))
    lines.append("| created | %s |" % rec.created_at)
    if has_rect:
        lines.append("| rect (real screen px) | `(%s,%s)-(%s,%s)` |"
                     % (rect.get("x1"), rect.get("y1"), rect.get("x2"), rect.get("y2")))
    lines.append("| label | `%s` |" % payload.get("label", "-"))
    lines.append("| model | `%s` |" % vl.get("model", "-"))
    lines.append("| folder | `%s` |" % rec.dir)
    lines.append("")

    # ---- MEASUREMENT shape: the checks that produced the verdict -----------
    if checks:
        lines.append("## Measured checks")
        lines.append("")
        lines.append("| check | result | detail |")
        lines.append("|---|---|---|")
        for name, c in checks.items():
            if not isinstance(c, dict):
                lines.append("| `%s` | %s |  |" % (name, c))
                continue
            lines.append("| `%s` | **%s** | %s |"
                         % (name,
                            "PASS" if c.get("ok") else "FAIL",
                            str(c.get("detail") or "-").replace("|", "/")[:160]))
        lines.append("")

    if measured:
        lines.append("## Measured values")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(measured, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    if payload.get("caveat"):
        lines.append("## Caveat")
        lines.append("")
        lines.append(str(payload["caveat"]))
        lines.append("")

    # ---- GEOMETRY shape: only when the data exists ------------------------
    if q:
        lines.append("## Two questions asked")
        lines.append("")
        lines.append("| # | question | answer | reason |")
        lines.append("|---|---|---|---|")
        for key, label in (
            ("q1_red_box_present", "1) do you see a red box at the image?"),
            ("q2_text_in_box", "2) image inside red box = \"%s\", yes or no?"
             % (payload.get("label") or "(no label)")),
        ):
            item = q.get(key) or {}
            lines.append("| %s | %s | **%s** | %s |"
                         % (key.replace("q", "Q"),
                            label,
                            item.get("answer") or "-",
                            (item.get("reason") or "-").replace("|", "/")[:120]))
        lines.append("")

    # The prompt is stored with the record so an old verdict stays reproducible
    # even if the template is later reworded.
    if payload.get("prompt_q1") or payload.get("prompt_q2"):
        lines.append("## Prompt sent to the VL")
        lines.append("")
        if payload.get("prompt_q1"):
            lines.append("**Q1**")
            lines.append("")
            lines.append("```")
            lines.append(str(payload["prompt_q1"]).rstrip())
            lines.append("```")
            lines.append("")
        if payload.get("prompt_q2"):
            lines.append("**Q2**")
            lines.append("")
            lines.append("```")
            lines.append(str(payload["prompt_q2"]).rstrip())
            lines.append("```")
            lines.append("")
        lines.append("Output format: `%s`"
                     % (payload.get("output_format")
                        or "Result: [YES / NO] / Reason: one short sentence"))
        lines.append("")

    if edges:
        lines.append("## Edge verdicts")
        lines.append("")
        lines.append("| edge | axis | candidate | detected | delta px | verdict |")
        lines.append("|---|---|---|---|---|---|")
        for e in edges:
            lines.append("| %s | %s | %s | %s | %s | **%s** |"
                         % (e.get("edge"), e.get("axis"), e.get("candidate"),
                            e.get("detected"), e.get("delta"), _mark(e.get("verdict"))))
        lines.append("")
        lines.append("edges all PASS: **%s**"
                     % ("yes" if payload.get("edges_all_pass") else "no"))
        lines.append("")

    lines.append("## Artefacts")
    lines.append("")
    lines.append("| artefact | file |")
    lines.append("|---|---|")
    for key in ("shot", "overlay", "overlay_vl", "crop", "classify"):
        if rec.files.get(key):
            lines.append("| %s | `%s` |" % (key, Path(rec.files[key]).name))
    lines.append("")
    # The overlay guidance only makes sense when an overlay was produced.
    if rec.files.get("overlay") or rec.files.get("overlay_vl"):
        lines.append("> The overlay PNG is the artefact a human must inspect: the red")
        lines.append("> full-span lines should cut the target row's top and bottom edges.")
        lines.append(">")
        lines.append("> `overlay_vl.png` is the text-free render the VL actually read. Keep")
        lines.append("> both: the annotated one for a human, the text-free one to reproduce")
        lines.append("> why Q2 answered the way it did.")
        lines.append("")

    if payload.get("error"):
        lines.append("## Error")
        lines.append("")
        lines.append("```")
        lines.append(str(payload["error"]))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def save_report(rec: EvidenceRecord, payload: dict[str, Any]) -> str:
    path = Path(rec.dir) / FILES["report"]
    path.write_text(build_report(rec, payload), encoding="utf-8")
    rec.files["report"] = str(path)
    return str(path)


def list_evidence(target_id: str | None = None) -> list[dict[str, Any]]:
    """List stored evidence, newest first. Reads classify.json for the verdict.

    Returns the fields the report table needs:
      date | test id | task id | prompt | result
    `prompt` is the question actually asked (stored in classify.json); older
    records predate that field and return an empty string.
    """
    if not EVIDENCE_ROOT.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for d in sorted(EVIDENCE_ROOT.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        if target_id and target_id not in d.name:
            continue
        verdict, label = "UNKNOWN", ""
        task_id = ""
        prompt = ""
        created_at = ""
        cj = d / FILES["classify"]
        if cj.is_file():
            try:
                j = json.loads(cj.read_text(encoding="utf-8"))
                verdict = str(j.get("verdict") or "UNKNOWN")
                label = str(j.get("label") or "")
                task_id = str(j.get("task_id") or "")
                prompt = str(j.get("prompt_q2") or j.get("prompt") or "")
                created_at = str(j.get("created_at") or "")
            except Exception:
                pass
        if not created_at:
            # Fall back to the folder mtime so the date column is never blank.
            try:
                created_at = time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.localtime(d.stat().st_mtime)
                )
            except OSError:
                created_at = ""
        out.append({
            "evidence_id": d.name,
            "verdict": verdict,
            "label": label,
            "task_id": task_id,
            "prompt": prompt,
            "created_at": created_at,
            "has_overlay": (d / FILES["overlay"]).is_file(),
            "has_report": (d / FILES["report"]).is_file(),
            "has_shot": (d / FILES["shot"]).is_file(),
        })
    return out


if __name__ == "__main__":
    print(json.dumps(list_evidence(), indent=2, ensure_ascii=False))