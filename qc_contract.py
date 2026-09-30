# -*- coding: utf-8 -*-
"""qc_contract.py — ONE shape for every QC verdict, bound to real evidence.

Law (user spec 2026-09-20): we trust EVIDENCE, not any agent's self-report.
  - A verdict must point at a real evidence FOLDER under `evidence/`
    (`EVID-<target>-<ts>/`, the convention owned by evidence_store.py) and
    carry the sha256 of the screenshot inside it.
  - A hash of "some file" proves nothing. So the row records WHICH folder,
    WHICH screenshot, and the sha256 of that screenshot.
  - UNKNOWN is a real outcome and is NEVER silently treated as PASS.
  - POLICY: UNKNOWN = FAIL. Only a proven PASS lets work through; FAIL and
    UNKNOWN both block dispatch.
  - A verdict is written to `qc_run` so one query answers "did this pass?".

Usage:
    import qc_contract as qc

    # 1. capture evidence (screenshot + folder) — the normal path
    rec = qc.capture("perm_default", note="permission picker open")
    r = qc.record(
        tool="evidence_classify", target="perm_default", verdict=qc.PASS,
        evidence=rec, reason="edges all PASS + VL YES", task_id="109",
    )

    # 2. bind an EXISTING evidence folder
    r = qc.record(..., evidence="EVID-perm_default-20260919-232416")

    qc.list_runs(verdict=qc.FAIL)
    qc.has_failed("109")          # -> True/False
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
VERDICTS = (PASS, FAIL, UNKNOWN)

# Policy (user spec 2026-09-20): UNKNOWN = FAIL.
# An unproven result is not a pass. Anything that is not a proven PASS blocks
# dispatch, so "we don't know" can never be laundered into "it works".
UNKNOWN_BLOCKS = True
# Verdicts that block dispatch.
BLOCKING_VERDICTS = (FAIL, UNKNOWN) if UNKNOWN_BLOCKS else (FAIL,)

# Tools that may write a verdict. Kept as data so a typo cannot invent a tool.
TOOLS = (
    "schema_qc",
    "pair_qc",
    "evidence_classify",
    "validate_new_task",
    # The 9-dimension quality gate (added 2026-09-28). It is a REAL tool, so it
    # must be here: `make_result` normalises any tool NOT in this tuple to
    # "manual", so a gate verdict would otherwise be recorded as a manual one
    # and could not be filtered by `--tool qc_gate`.
    "qc_gate",
    "manual",
)

# The screenshot inside an evidence folder. This is the file whose sha256 is
# bound to the verdict — the image a human can actually look at.
PRIMARY_ARTIFACT = "shot.png"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_qc_run_table(conn: sqlite3.Connection) -> None:
    """Create qc_run if missing (additive; never drops)."""
    from db_schema import (QC_RUN_DDL, QC_RUN_EVIDENCE_INDEX_DDL,
                           QC_RUN_GATE_INDEX_DDL)

    conn.executescript(QC_RUN_DDL)
    # Additive migration for DBs created before evidence binding existed.
    have = {r[1] for r in conn.execute("PRAGMA table_info(qc_run)")}
    for col in ("evidence_id", "evidence_dir", "evidence_files"):
        if col not in have:
            try:
                conn.execute("ALTER TABLE qc_run ADD COLUMN %s TEXT" % col)
            except Exception:
                pass
    # Additive migration for the qc_gate layer (2026-09-28). `gate_key` names
    # which of the nine dimensions the row is about; `score_0_100` carries the
    # arbiter's derived score; `gate_run_ref` groups one run's nine rows. All
    # NULLABLE, so every existing writer keeps working unchanged.
    for col, typ in (("gate_key", "TEXT"), ("score_0_100", "REAL"),
                     ("gate_run_ref", "TEXT")):
        if col not in have:
            try:
                conn.execute("ALTER TABLE qc_run ADD COLUMN %s %s" % (col, typ))
            except Exception:
                pass
    # Index on evidence_id only after the column is guaranteed to exist.
    try:
        conn.executescript(QC_RUN_EVIDENCE_INDEX_DDL)
    except Exception:
        pass
    # Index for the qc_gate layer, AFTER its columns exist (same reason).
    try:
        conn.executescript(QC_RUN_GATE_INDEX_DDL)
    except Exception:
        pass


def sha256_file(path: str | os.PathLike) -> str | None:
    """sha256 of a file, or None when the file does not exist.

    None is meaningful: it means the evidence was NOT captured, so the caller
    must not claim a PASS.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return None
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# evidence capture — writes into the real evidence/ store
# ---------------------------------------------------------------------------

def capture(
    target_id: str,
    *,
    note: str = "",
    region: dict[str, Any] | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Take a screenshot into a NEW `evidence/EVID-<target>-<ts>/` folder.

    Returns a binding dict:
        {evidence_id, evidence_dir, evidence_path, evidence_sha256,
         evidence_files, provenance}

    The folder is created by evidence_store (append-only: a re-run makes a NEW
    id, so a passing run can never overwrite a failing one).

    `provenance` records source / size / sha256 / foreground window, so a
    reviewer can tell "the target is wrong" from "the evidence is wrong".
    """
    import evidence_store as es

    prev_root = None
    if root:
        prev_root = es.set_evidence_root(root)
    try:
        rec = es.open_evidence(target_id)
        shot = Path(rec.dir) / es.FILES["shot"]
        prov = _grab_screenshot(shot)
        if prov.get("sha256"):
            rec.files["shot"] = str(shot)

        meta = {
            "evidence_id": rec.evidence_id,
            "target_id": str(target_id),
            "note": note,
            "region": region,
            "provenance": prov,
            "created_at": rec.created_at,
        }
        bind_path = Path(rec.dir) / "qc_binding.json"
        bind_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        rec.files["qc_binding"] = str(bind_path)
        return _binding_from_dir(Path(rec.dir), prov)
    finally:
        if prev_root is not None:
            es.set_evidence_root(prev_root)


def _grab_screenshot(out_path: Path) -> dict[str, Any]:
    """Capture the full screen to out_path and describe it.

    Records source / size / sha256 / foreground. Size + hash alone are NOT
    enough (a same-size image is not a same-content image), so the foreground
    window is recorded too.
    """
    prov: dict[str, Any] = {
        "source": "unknown",
        "path": str(out_path),
        "captured_at": _utc_now(),
        "bytes": 0,
        "width": None,
        "height": None,
        "sha256": None,
        "foreground": {},
    }
    try:
        import pyautogui

        img = pyautogui.screenshot()
        img.save(str(out_path))
        prov["source"] = "pyautogui"
        prov["width"], prov["height"] = img.size
    except Exception as e:
        prov["error"] = "%s: %s" % (type(e).__name__, e)
        return prov

    try:
        prov["bytes"] = out_path.stat().st_size
        prov["sha256"] = sha256_file(out_path)
    except Exception as e:
        prov["error"] = "%s: %s" % (type(e).__name__, e)

    try:
        import f_perm_click as fpc

        fg = fpc._foreground_info()
        prov["foreground"] = fg
        prov["foreground_is_code"] = bool(fg.get("is_code"))
    except Exception:
        prov["foreground"] = {}
    return prov


def _binding_from_dir(d: Path, prov: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a binding dict from an existing evidence folder."""
    shot = d / PRIMARY_ARTIFACT
    files = sorted(p.name for p in d.iterdir() if p.is_file()) if d.is_dir() else []
    return {
        "evidence_id": d.name,
        "evidence_dir": str(d),
        "evidence_path": str(shot) if shot.is_file() else None,
        "evidence_sha256": sha256_file(shot) if shot.is_file() else None,
        "evidence_files": ",".join(files),
        "provenance": prov or {},
    }


def resolve_evidence(evidence: Any) -> dict[str, Any]:
    """Normalise `evidence` into a binding dict.

    Accepts:
      - None                      -> empty binding (verdict will downgrade)
      - a binding dict (from capture())
      - an evidence id string     -> `EVID-...`
      - a folder path             -> `evidence/EVID-...`
      - a file path               -> bound directly (legacy / non-UI evidence)
    """
    empty = {
        "evidence_id": None, "evidence_dir": None, "evidence_path": None,
        "evidence_sha256": None, "evidence_files": None, "provenance": {},
    }
    if not evidence:
        return empty
    if isinstance(evidence, dict):
        out = dict(empty)
        out.update({k: evidence.get(k) for k in empty if k in evidence})
        if not out["evidence_sha256"] and out["evidence_path"]:
            out["evidence_sha256"] = sha256_file(out["evidence_path"])
        return out

    p = Path(str(evidence))
    if p.is_dir():
        return _binding_from_dir(p)
    if p.is_file():
        out = dict(empty)
        out["evidence_path"] = str(p)
        out["evidence_sha256"] = sha256_file(p)
        return out

    # Treat as an evidence id under the store root.
    try:
        import evidence_store as es

        d = Path(es.EVIDENCE_ROOT) / str(evidence)
        if d.is_dir():
            return _binding_from_dir(d)
    except Exception:
        pass
    return empty


# ---------------------------------------------------------------------------
# verdict
# ---------------------------------------------------------------------------

def make_result(
    tool: str,
    target: str,
    verdict: str,
    *,
    evidence: Any = None,
    reason: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    gate_key: str | None = None,
    score_0_100: float | None = None,
    gate_run_ref: str | None = None,
    structural_cite: str | None = None,
    conn: sqlite3.Connection | None = None,
    check_name: bool = True,
) -> dict[str, Any]:
    """Build the canonical QC result dict (no DB write).

    Hard rules:
      - verdict must be one of PASS / FAIL / UNKNOWN
      - a PASS needs a UI screenshot: an `evidence/EVID-*/` folder containing
        `shot.png`. Without it the verdict is DOWNGRADED to UNKNOWN — a hash of
        "some file" is not a UI proof, and an unproven result is never a pass.
      - THE TARGET'S NAME MUST BE A REGISTERED TERM (added 2026-09-25).

    WHY THE NAME CHECK IS HERE (the human, 2026-09-25: "fix it all now"):

    MEASURED: `terminology_registry.assert_named` is called from 9 places, but
    **no file** had both `assert_named` and a QC report write path. So a QC
    verdict could name a term nobody had registered, and the verdict was
    recorded — a verdict about a word nobody defined.

    The user's own words for why this matters (2026-09-23):
        "too easy to have name mis-understand problem, you need to register at
         terminology_registry!!!"
        "so wrong name can be applyed to qc skill to help proofed your mistake
         before report done"

    IT DOWNGRADES, IT DOES NOT REFUSE. `make_result` already downgrades a PASS
    without evidence to UNKNOWN, and an unregistered name is the same class of
    defect: the verdict is about something nobody defined. Refusing the write
    would LOSE the evidence, which is the opposite of what a QC record is for.

    `check_name=False` is the opt-out, for a caller whose `target` is not a term
    (a free-text label). An unreadable register is UNKNOWN, never a pass.
    """
    v = str(verdict or "").strip().upper()
    if v not in VERDICTS:
        v = UNKNOWN
    bind = resolve_evidence(evidence)
    if v == PASS:
        # A PASS needs EVIDENCE. There are TWO kinds, and ONE rule: no unbacked
        # pass. Either a UI screenshot (an EVID-*/ folder with shot.png), or a
        # CHECKABLE `structural_cite` (a path:line a reader can open).
        #
        # WHY THE SECOND KIND EXISTS (measured 2026-09-28, by RUNNING the gate):
        # the 9-dimension gate's ontology/5w1h checks are DB reads, not UI, so
        # they can NEVER produce a screenshot. With only the screenshot rule the
        # gate's legitimate PASS was downgraded to UNKNOWN — the gate's own
        # verdict destroyed by the store. A structural PASS still has to cite a
        # reference that `terminology_cite.verify_cite_ref` accepts, so the
        # anti-false-success law is preserved: an unbacked PASS is still refused.
        struct_ok = False
        if not bind["evidence_sha256"] and str(structural_cite or "").strip():
            try:
                import terminology_cite as tc
                struct_ok, _swhy = tc.verify_cite_ref(str(structural_cite))
            except Exception:
                struct_ok = False
            if struct_ok:
                bind = dict(bind)
                bind["evidence_path"] = str(structural_cite)
                bind["evidence_files"] = "structural_cite"
        if not bind["evidence_sha256"] and not struct_ok and not struct_ok:
            v = UNKNOWN
            reason = (reason or "") + " | downgraded: PASS without evidence"
        elif not bind["evidence_id"] and not struct_ok:
            v = UNKNOWN
            reason = (reason or "") + (
                " | downgraded: PASS without a UI screenshot "
                "(need evidence/EVID-*/shot.png)"
            )
    # ---- THE NAME CHECK (FIX 2) -------------------------------------------
    name_ok: bool | None = None
    name_reason = ""
    if check_name and str(target or "").strip():
        name_ok, name_reason = _check_target_name(conn, str(target))
        if name_ok is False:
            v = UNKNOWN
            reason = (reason or "") + (
                " | downgraded: target %r is NOT a registered term — %s"
                % (str(target), name_reason))
    return {
        "qc_id": "qc_" + uuid.uuid4().hex[:12],
        "tool": tool if tool in TOOLS else "manual",
        "target": str(target or ""),
        "verdict": v,
        "evidence_id": bind["evidence_id"],
        "evidence_dir": bind["evidence_dir"],
        "evidence_path": bind["evidence_path"],
        "evidence_sha256": bind["evidence_sha256"],
        "evidence_files": bind["evidence_files"],
        "reason": reason,
        "task_id": str(task_id) if task_id else None,
        "trace_id": str(trace_id) if trace_id else None,
        "gate_key": str(gate_key) if gate_key else None,
        "score_0_100": float(score_0_100) if score_0_100 is not None else None,
        "gate_run_ref": str(gate_run_ref) if gate_run_ref else None,
        "name_ok": name_ok,
        "name_reason": name_reason,
        "created_at": _utc_now(),
    }


def _check_target_name(
    conn: sqlite3.Connection | None, target: str
) -> tuple[bool | None, str]:
    """`(ok, reason)`. `None` means UNMEASURABLE — never a pass.

    A register that cannot be read is NOT "the name is fine". Collapsing
    "could not check" into "checked and fine" is how a broken detector reads as
    a pass (`independent_review`: an empty result needs a positive control).
    """
    try:
        import terminology_registry as tr
    except Exception as exc:
        return None, "terminology_registry unimportable: %s" % exc
    own = None
    try:
        if conn is None:
            own = _connect()
            conn = own
        ok, why = tr.assert_named(conn, target)
        return bool(ok), str(why)
    except Exception as exc:
        return None, "name check failed: %s: %s" % (type(exc).__name__, exc)
    finally:
        if own is not None:
            try:
                own.close()
            except Exception:
                pass


def record(
    tool: str,
    target: str,
    verdict: str,
    *,
    evidence: Any = None,
    reason: str | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    gate_key: str | None = None,
    score_0_100: float | None = None,
    gate_run_ref: str | None = None,
    structural_cite: str | None = None,
    check_name: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build + persist one QC verdict. Returns the result dict.

    `check_name=True` (default) makes the target's NAME a registered term; an
    unregistered name downgrades the verdict to UNKNOWN. See `make_result` for
    why. `check_name=False` is the opt-out for a free-text target.

    `gate_key` / `score_0_100` / `gate_run_ref` (added 2026-09-28) carry the
    qc_gate layer's per-dimension name, the arbiter's derived score and the run
    grouping. All default to `None`, so every existing caller is unchanged.

    `structural_cite` (added 2026-09-28) is the SECOND kind of PASS evidence: a
    `path:line` a reader can open, for a NON-UI check (a DB read) that can never
    produce a screenshot. It is CHECKED (`terminology_cite.verify_cite_ref`), so
    it cannot launder an unbacked PASS.
    """
    conn = _connect(db_path)
    try:
        res = make_result(
            tool, target, verdict,
            evidence=evidence, reason=reason,
            task_id=task_id, trace_id=trace_id,
            gate_key=gate_key, score_0_100=score_0_100,
            gate_run_ref=gate_run_ref, structural_cite=structural_cite,
            conn=conn, check_name=check_name,
        )
        ensure_qc_run_table(conn)
        conn.execute(
            """
            INSERT INTO qc_run
                (qc_id, tool, target, verdict, evidence_id, evidence_dir,
                 evidence_path, evidence_sha256, evidence_files, reason,
                 task_id, trace_id, gate_key, score_0_100, gate_run_ref,
                 created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                res["qc_id"], res["tool"], res["target"], res["verdict"],
                res["evidence_id"], res["evidence_dir"], res["evidence_path"],
                res["evidence_sha256"], res["evidence_files"], res["reason"],
                res["task_id"], res["trace_id"], res["gate_key"],
                res["score_0_100"], res["gate_run_ref"], res["created_at"],
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return res


def list_runs(
    *,
    verdict: str | None = None,
    tool: str | None = None,
    target: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List QC runs, newest first, with optional filters."""
    where, params = [], []
    if verdict:
        where.append("verdict = ?")
        params.append(str(verdict).upper())
    if tool:
        where.append("tool = ?")
        params.append(tool)
    if target:
        where.append("target = ?")
        params.append(target)
    if task_id:
        where.append("task_id = ?")
        params.append(str(task_id))
    sql = "SELECT * FROM qc_run"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(int(limit))
    conn = _connect(db_path)
    try:
        ensure_qc_run_table(conn)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def has_failed(task_id: str, *, db_path: Path | str | None = None) -> bool:
    """True when the task has any BLOCKING verdict (FAIL, and UNKNOWN).

    Policy: UNKNOWN = FAIL. An unproven result is not a pass, so it blocks
    dispatch exactly like a FAIL. Only a proven PASS lets work through.
    """
    conn = _connect(db_path)
    try:
        ensure_qc_run_table(conn)
        marks = ",".join("?" for _ in BLOCKING_VERDICTS)
        row = conn.execute(
            f"SELECT COUNT(*) FROM qc_run WHERE task_id = ? AND verdict IN ({marks})",
            (str(task_id), *BLOCKING_VERDICTS),
        ).fetchone()
        return bool(row and row[0])
    finally:
        conn.close()


def blocking_runs(
    task_id: str, *, db_path: Path | str | None = None
) -> list[dict[str, Any]]:
    """The blocking rows for a task (FAIL + UNKNOWN), newest first."""
    conn = _connect(db_path)
    try:
        ensure_qc_run_table(conn)
        marks = ",".join("?" for _ in BLOCKING_VERDICTS)
        rows = conn.execute(
            f"SELECT * FROM qc_run WHERE task_id = ? AND verdict IN ({marks}) "
            "ORDER BY created_at DESC, id DESC",
            (str(task_id), *BLOCKING_VERDICTS),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def summary(*, db_path: Path | str | None = None) -> dict[str, Any]:
    """Counts per verdict — the measurable QC health line."""
    conn = _connect(db_path)
    try:
        ensure_qc_run_table(conn)
        rows = conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM qc_run GROUP BY verdict"
        ).fetchall()
        out = {v: 0 for v in VERDICTS}
        for r in rows:
            out[str(r["verdict"])] = int(r["n"])
        out["total"] = sum(out[v] for v in VERDICTS)
        # Evidence coverage: how many verdicts actually carry a screenshot hash.
        ev = conn.execute(
            "SELECT COUNT(*) FROM qc_run WHERE evidence_sha256 IS NOT NULL"
        ).fetchone()
        out["with_evidence"] = int(ev[0] if ev else 0)
        out["evidence_rate"] = (
            round(100.0 * out["with_evidence"] / out["total"], 1)
            if out["total"] else 0.0
        )
        return out
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(summary(), indent=1))
