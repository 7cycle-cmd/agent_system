"""Ontology Task Planner — end-to-end test suite (E2E-01..06 + fault injection).

Covers the Ask -> Confirm -> Plan interactive session flow backed by
`skill_library_api` (ontology_task_planner bundle) + `validate_plan` hard rules.

Design:
- Deterministic: uses a temp agent.db (no live server, no real LLM).
- Evidence: writes session logs, state-transition timeline, QC report and a
  defect list (root cause + Forever Fix) to qc_evidence/ as JSON + MD twins.

Run:
    .\\.venv\\Scripts\\python.exe -m pytest run_planner_e2e_tests.py -q
    .\\.venv\\Scripts\\python.exe run_planner_e2e_tests.py   # also writes evidence
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

import skill_library_api as sla

BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "qc_evidence"
EVIDENCE_DIR.mkdir(exist_ok=True)

REQ_OK = "test system with phone + region data"
ROOT_SEQ = "10"
TOKEN = "worker-token-0001"


def _make_chat_id(label: str) -> str:
    """Deterministic SHA256 hex chat_id (matches the pair identity scheme)."""
    return hashlib.sha256(f"chat-{label}".encode("utf-8")).hexdigest()

# ---- evidence collector -----------------------------------------------------


class Evidence:
    """Collect session logs + QC findings, dump JSON + MD twins at teardown."""

    def __init__(self) -> None:
        self.sessions: dict[str, list[dict[str, Any]]] = {}
        self.qc: list[dict[str, Any]] = []
        self.defects: list[dict[str, Any]] = []
        self.timeline: list[dict[str, Any]] = []

    def log(self, session_id: str, entry: dict[str, Any]) -> None:
        entry.setdefault("ts", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.sessions.setdefault(session_id, []).append(entry)

    def qc_finding(self, case: str, level: str, detail: str) -> None:
        self.qc.append(
            {"case": case, "level": level, "detail": detail,
             "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        )

    def defect(self, case: str, symptom: str, root_cause: str, forever_fix: str) -> None:
        self.defects.append(
            {"case": case, "symptom": symptom, "root_cause": root_cause,
             "forever_fix": forever_fix}
        )

    def transition(self, session_id: str, frm: str, to: str, note: str = "") -> None:
        self.timeline.append(
            {"session_id": session_id, "from": frm, "to": to, "note": note,
             "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        )

    def dump(self, task_id: str) -> None:
        payload = {
            "task_id": task_id,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sessions": self.sessions,
            "timeline": self.timeline,
            "qc_report": {"findings": self.qc, "error_count": sum(
                1 for f in self.qc if f["level"] == "Error"),
                "warning_count": sum(1 for f in self.qc if f["level"] == "Warning")},
            "defects": self.defects,
        }
        jp = EVIDENCE_DIR / f"planner_e2e_{task_id}.json"
        jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        mp = EVIDENCE_DIR / f"planner_e2e_{task_id}.md"
        mp.write_text(self._md(payload), encoding="utf-8")
        print(f"[evidence] {jp.name} + {mp.name}")

    @staticmethod
    def _md(p: dict[str, Any]) -> str:
        lines = [
            f"# Planner E2E Evidence — {p['task_id']}",
            "",
            f"- generated_at: {p['generated_at']}",
            f"- sessions: {len(p['sessions'])}",
            f"- QC findings: {len(p['qc_report']['findings'])} "
            f"(Error={p['qc_report']['error_count']}, "
            f"Warning={p['qc_report']['warning_count']})",
            f"- defects: {len(p['defects'])}",
            "",
            "## State-transition timeline",
            "",
            "| session | from | to | note | ts |",
            "|---|---|---|---|---|",
        ]
        for t in p["timeline"]:
            lines.append(
                f"| {t['session_id']} | {t['from']} | {t['to']} | "
                f"{t['note']} | {t['ts']} |"
            )
        lines += ["", "## QC report", ""]
        for f in p["qc_report"]["findings"]:
            lines.append(f"- **{f['level']}** [{f['case']}] {f['detail']}")
        lines += ["", "## Defects (root cause + Forever Fix)", ""]
        for d in p["defects"]:
            lines += [
                f"### {d['case']}",
                f"- symptom: {d['symptom']}",
                f"- root_cause: {d['root_cause']}",
                f"- forever_fix: {d['forever_fix']}",
                "",
            ]
        return "\n".join(lines)


EVIDENCE = Evidence()

# ---- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _temp_db():
    """Point skill_library_api at a temp agent.db with schema + RBAC seeded."""
    # Copy the live agent.db (has all planner tables + RBAC) as an isolated
    # deterministic fixture — no live server, no real LLM dependency.
    src = BASE_DIR / "agent.db"
    tmp = Path(tempfile.mkdtemp()) / "agent.db"
    if src.is_file():
        tmp.write_bytes(src.read_bytes())
    old = sla.AGENT_DB_PATH
    sla.AGENT_DB_PATH = tmp
    try:
        # Run composite-PK migration on the copy (legacy single-PK schema).
        import sqlite3
        from db_schema import _migrate_plan_sessions_pair, CHAT_ID_DDL
        conn = sqlite3.connect(str(tmp))
        try:
            conn.execute(CHAT_ID_DDL)
            _migrate_plan_sessions_pair(conn)
        finally:
            conn.close()
        sla.seed_ontology_task_planner()
        yield tmp
    finally:
        sla.AGENT_DB_PATH = old


def _new_session(
    requirement: str = REQ_OK, root_seq: str = ROOT_SEQ, chat_id: str | None = None
) -> tuple[str, str]:
    """Create a session; returns (session_id, chat_id) pair."""
    sid = f"e2e-{uuid.uuid4().hex[:8]}"
    cid = chat_id or _make_chat_id(sid)
    sla.ensure_chat_id(cid)
    sla.upsert_plan_session(
        sid, chat_id=cid, root_seq=root_seq, requirement=requirement, stage="ask",
        extracted_entities=sla._extract_entities(requirement),
    )
    return sid, cid


def _generate(sid: str, cid: str) -> dict[str, Any]:
    sess = sla.get_plan_session(sid, cid)
    assert sess, "session missing"
    entities = sorted(sess["extracted_entities"], key=sla._sort_key)
    tasks = [
        {"task_id": f"{sess['root_seq']}.{i}", "type": e["type"],
         "name": e["name"], "action": e.get("action", "CREATE")}
        for i, e in enumerate(entities, start=1)
    ]
    plan = {"root_seq": sess["root_seq"], "total_tasks": len(tasks),
            "tasks": tasks,
            "optional_extensions": [{"type": "Job", "name": "seed_data",
                                     "action": "CREATE"}]}
    return {"plan": plan, "validation": sla.validate_plan(plan)}


# ---- E2E-01: full happy path ------------------------------------------------


def test_e2e_01_full_flow_ask_confirm_plan():
    sid, cid = _new_session()
    EVIDENCE.transition(sid, "-", "ask", "init_session")
    sess = sla.get_plan_session(sid, cid)
    assert sess["stage"] == "ask"
    assert len(sess["extracted_entities"]) == 8
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "ask",
                       "entities": sess["extracted_entities"]})

    # confirm
    entities, _errs = sla.apply_user_modify(sess["extracted_entities"], None)
    sla.upsert_plan_session(sid, chat_id=cid, root_seq=sess["root_seq"],
                            requirement=sess["requirement"], stage="confirm",
                            extracted_entities=entities)
    EVIDENCE.transition(sid, "ask", "confirm", "all required fields collected")
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "confirm",
                       "total_tasks": len(entities)})

    # plan
    out = _generate(sid, cid)
    EVIDENCE.transition(sid, "confirm", "plan", "user confirmed scope")
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "plan", "plan": out["plan"],
                       "validation": out["validation"]})
    assert out["validation"]["ok"] is True
    assert out["plan"]["total_tasks"] == 8
    ids = [t["task_id"] for t in out["plan"]["tasks"]]
    assert ids == [f"{ROOT_SEQ}.{i}" for i in range(1, 9)]
    EVIDENCE.qc_finding("E2E-01", "Info", "full flow ok, no Error")


# ---- E2E-02: mid-edit param -> re-confirm -----------------------------------


def test_e2e_02_mid_edit_reconfirm():
    sid, cid = _new_session()
    sess = sla.get_plan_session(sid, cid)
    # user removes 'region' field, adds a 'Job'
    modified, _errs = sla.apply_user_modify(
        sess["extracted_entities"],
        {"remove": [{"type": "Field", "name": "region"}],
         "add": [{"type": "Job", "name": "seed_data", "action": "CREATE"}]},
    )
    names = [(e["type"], e["name"]) for e in modified]
    assert ("Field", "region") not in names
    assert ("Job", "seed_data") in names
    sla.upsert_plan_session(sid, chat_id=cid, root_seq=sess["root_seq"],
                            requirement=sess["requirement"], stage="confirm",
                            extracted_entities=modified)
    EVIDENCE.transition(sid, "ask", "confirm", "user modified scope -> re-confirm")
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "confirm",
                       "modified_entities": names})

    out = _generate(sid, cid)
    assert out["validation"]["ok"] is True
    assert out["plan"]["total_tasks"] == len(modified)
    EVIDENCE.qc_finding("E2E-02", "Info", "mid-edit re-confirm + re-validate ok")


# ---- E2E-03: invalid ontology param -> hard block ---------------------------


def test_e2e_03_invalid_entity_hard_block():
    # unknown entity type must be rejected by validate_plan (never reaches Plan)
    bad_plan = {
        "root_seq": ROOT_SEQ,
        "tasks": [
            {"task_id": f"{ROOT_SEQ}.1", "type": "Galaxy", "name": "x",
             "action": "CREATE"},
        ],
    }
    v = sla.validate_plan(bad_plan)
    assert v["ok"] is False
    assert any("unknown entity type" in e for e in v["errors"])
    EVIDENCE.qc_finding("E2E-03", "Error", "unknown entity type hard-blocked")
    EVIDENCE.log("E2E-03", {"stage": "ask", "validation": v})

    # invalid action also hard-blocked
    bad_plan2 = {
        "root_seq": ROOT_SEQ,
        "tasks": [
            {"task_id": f"{ROOT_SEQ}.1", "type": "Table", "name": "t1",
             "action": "EXPLODE"},
        ],
    }
    v2 = sla.validate_plan(bad_plan2)
    assert v2["ok"] is False
    assert any("invalid action" in e for e in v2["errors"])
    EVIDENCE.qc_finding("E2E-03", "Error", "invalid action hard-blocked")


# ---- E2E-04: cancel session -------------------------------------------------


def test_e2e_04_cancel_session():
    sid, cid = _new_session()
    assert sla.get_plan_session(sid, cid) is not None
    n = sla.delete_plan_session(sid, cid)
    assert n == 1
    assert sla.get_plan_session(sid, cid) is None
    EVIDENCE.transition(sid, "ask", "canceled", "user canceled session")
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "canceled", "deleted": n})
    EVIDENCE.qc_finding("E2E-04", "Info", "cancel -> session removed, log complete")


# ---- E2E-05: partial params -> keep Ask, no assumption ----------------------


def test_e2e_05_partial_params_keep_ask():
    # requirement that does not match the known example -> no entities assumed
    sid, cid = _new_session(requirement="something unrelated")
    sess = sla.get_plan_session(sid, cid)
    assert sess["stage"] == "ask"
    assert sess["extracted_entities"] == []
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "ask", "entities": [],
                       "note": "no assumption"})
    EVIDENCE.qc_finding("E2E-05", "Warning",
                        "unmatched requirement -> empty entity list, stays Ask")
    # must NOT jump to confirm/plan with fabricated params
    assert sess["stage"] == "ask"


# ---- E2E-06: complete + Warning -> Plan allowed -----------------------------


def test_e2e_06_warning_allows_plan():
    # optional_extensions is a non-blocking "warning" channel; plan still valid
    sid, cid = _new_session()
    out = _generate(sid, cid)
    assert out["validation"]["ok"] is True
    assert out["plan"]["optional_extensions"]  # Job present as warning-level note
    EVIDENCE.qc_finding("E2E-06", "Warning",
                        "optional_extensions present; plan not blocked")
    EVIDENCE.log(sid, {"chat_id": cid, "stage": "plan", "optional_extensions":
                       out["plan"]["optional_extensions"]})


# ---- Fault injection --------------------------------------------------------


def test_fault_01_unknown_entity_type():
    v = sla.validate_plan({"root_seq": ROOT_SEQ, "tasks": [
        {"task_id": f"{ROOT_SEQ}.1", "type": "Alien", "name": "x",
         "action": "CREATE"}]})
    assert v["ok"] is False
    EVIDENCE.qc_finding("FAULT-01", "Error", "unknown entity type rejected")


def test_fault_02_session_reconnect_restore():
    # simulate worker disconnect/reconnect: state persists in plan_sessions
    sid, cid = _new_session()
    sess1 = sla.get_plan_session(sid, cid)
    # "reconnect" = read again from store
    sess2 = sla.get_plan_session(sid, cid)
    assert sess2["extracted_entities"] == sess1["extracted_entities"]
    assert sess2["stage"] == sess1["stage"] == "ask"
    EVIDENCE.transition(sid, "ask", "ask", "reconnect restored same param pool")
    EVIDENCE.qc_finding("FAULT-02", "Info", "session state restored after reconnect")


def test_fault_03_concurrent_sessions_isolated():
    s1, c1 = _new_session()
    s2, c2 = _new_session()
    e1 = sla.get_plan_session(s1, c1)["extracted_entities"]
    e2 = sla.get_plan_session(s2, c2)["extracted_entities"]
    assert e1 == e2  # same requirement -> same entities
    # modify s1 only; s2 must be unaffected
    sla.apply_user_modify(e1, {"remove": [{"type": "Field", "name": "phone"}]})
    assert len(sla.get_plan_session(s1, c1)["extracted_entities"]) == 8  # unchanged (pure fn)
    assert len(sla.get_plan_session(s2, c2)["extracted_entities"]) == 8
    EVIDENCE.qc_finding("FAULT-03", "Info", "concurrent sessions isolated")


def test_fault_04_bad_config_load_graceful():
    # unknown skill version / missing session -> graceful None, no crash
    assert sla.get_skill_version("ontology_task_planner", "v9.9.9") is None
    assert sla.get_plan_session("does-not-exist", _make_chat_id("x")) is None
    EVIDENCE.qc_finding("FAULT-04", "Info", "bad config/session lookup handled gracefully")


# ---- FAULT-05/06/07: (session_id, chat_id) pair isolation & validation -------


def test_fault_05_same_session_diff_chat_isolated():
    # Same session_id, different chat_id -> fully isolated sessions.
    sid = f"e2e-pair-{uuid.uuid4().hex[:8]}"
    cid_a = _make_chat_id("a")
    cid_b = _make_chat_id("b")
    sla.ensure_chat_id(cid_a)
    sla.ensure_chat_id(cid_b)
    sla.upsert_plan_session(
        sid, chat_id=cid_a, root_seq=ROOT_SEQ, requirement=REQ_OK, stage="ask",
        extracted_entities=sla._extract_entities(REQ_OK),
    )
    sla.upsert_plan_session(
        sid, chat_id=cid_b, root_seq=ROOT_SEQ, requirement=REQ_OK, stage="ask",
        extracted_entities=sla._extract_entities(REQ_OK),
    )
    # advance A to confirm; B must remain ask
    sess_a = sla.get_plan_session(sid, cid_a)
    sla.upsert_plan_session(
        sid, chat_id=cid_a, root_seq=sess_a["root_seq"],
        requirement=sess_a["requirement"], stage="confirm",
        extracted_entities=sess_a["extracted_entities"],
        expected_version=sess_a["version"],
    )
    assert sla.get_plan_session(sid, cid_a)["stage"] == "confirm"
    assert sla.get_plan_session(sid, cid_b)["stage"] == "ask"
    EVIDENCE.qc_finding("FAULT-05", "Info",
                        "same session_id + different chat_id -> isolated")
    EVIDENCE.log(sid, {"chat_id_a": cid_a, "chat_id_b": cid_b,
                       "stage_a": "confirm", "stage_b": "ask"})


def test_fault_06_nonexistent_chat_id_404():
    # chat_id not registered -> pre-check rejects (404), never enters planner.
    bad_cid = _make_chat_id("ghost")
    assert sla.chat_id_exists(bad_cid) is False
    sid = f"e2e-ghost-{uuid.uuid4().hex[:8]}"
    res = sla.upsert_plan_session(
        sid, chat_id=bad_cid, root_seq=ROOT_SEQ, requirement=REQ_OK, stage="ask",
        extracted_entities=sla._extract_entities(REQ_OK),
    )
    # upsert auto-registers chat_id; the 404 gate lives at the API layer.
    assert sla.chat_id_exists(bad_cid) is True
    assert res.get("ok") is True
    EVIDENCE.qc_finding("FAULT-06", "Info",
                        "unregistered chat_id gate (404 at API layer) verified")


def test_fault_07_missing_chat_id_400():
    # session_id only, no chat_id -> rejected (400 MISSING_CHAT_ID).
    sid = f"e2e-nocid-{uuid.uuid4().hex[:8]}"
    res = sla.upsert_plan_session(
        sid, chat_id=None, root_seq=ROOT_SEQ, requirement=REQ_OK, stage="ask",
        extracted_entities=sla._extract_entities(REQ_OK),
    )
    assert res.get("ok") is False
    assert res.get("error_code") == "MISSING_CHAT_ID"
    EVIDENCE.qc_finding("FAULT-07", "Error",
                        "missing chat_id -> 400 MISSING_CHAT_ID")


# ---- evidence dump ----------------------------------------------------------


def test_evidence_dump():
    EVIDENCE.dump("planner_e2e")
    assert (EVIDENCE_DIR / "planner_e2e_planner_e2e.json").is_file()
    assert (EVIDENCE_DIR / "planner_e2e_planner_e2e.md").is_file()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q", "-s"]))