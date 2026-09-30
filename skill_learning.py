"""Skill Learning Center backend.

R&D companion to the Skill Library:
- skill_mismatch_log: ask<->confirm mismatches (captured by skill_prompt hooks)
- skill_lesson: self_fail case-study lessons + github_proofed_lesson imports
- candidate prompt versions (skill_prompt_ssot status='draft') validated by
  short streak before merge into the active Skill Library SSOT.

Guardrails:
1. Candidates (draft) are never used in production; merge requires pass_gate=1.
2. LLM prompts stay small: grouped summary + <=5 samples per group.
3. GitHub lessons are knowledge only; they must pass own gold-case validation
   (short streak on a candidate) before being marked merged.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import skill_prompt

OLLAMA_CHAT_URL = "http://127.0.0.1:11434/v1/chat/completions"
TEXT_MODEL = "qwen2.5:7b-instruct"
MAX_URL_CHARS = 20000
MAX_SAMPLES_PER_GROUP = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect(db_path: Path | str | None = None):
    conn = skill_prompt._connect(db_path)
    skill_prompt.ensure_skill_tables(conn)
    return conn


# ---------------------------------------------------------------- LLM helper

def _llm_chat(prompt: str, *, model: str = TEXT_MODEL, timeout: float = 120.0) -> str:
    """Text-only Ollama chat completion (temperature 0.0)."""
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_CHAT_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return str(data["choices"][0]["message"]["content"] or "")


def _extract_json(text: str) -> Any:
    """Extract first JSON object or array from an LLM reply.

    Uses strict=False: a 7B model emits prompt bodies with REAL newlines inside
    string values, which is invalid JSON and made json.loads() fail even though
    the reply was well-formed apart from that. strict=False permits control
    characters inside strings, which is exactly the case here.
    """
    text = (text or "").strip()
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            for kwargs in ({"strict": False}, {}):
                try:
                    return json.loads(m.group(0), **kwargs)
                except (json.JSONDecodeError, TypeError):
                    continue
    for kwargs in ({"strict": False}, {}):
        try:
            return json.loads(text, **kwargs)
        except (json.JSONDecodeError, TypeError):
            continue
    return None


# ---------------------------------------------------------------- mismatches

def list_mismatches(
    *,
    skill_key: str | None = None,
    status: str | None = None,
    limit: int = 200,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM skill_mismatch_log WHERE 1=1"
        args: list[Any] = []
        if skill_key:
            sql += " AND skill_key=?"
            args.append(skill_key)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_mismatch_status(
    mismatch_id: int,
    status: str,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    if status not in ("to_review", "reviewed", "reject", "promote_to_gold"):
        raise ValueError(f"invalid status: {status}")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM skill_mismatch_log WHERE id=?", (mismatch_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"mismatch not found: {mismatch_id}")
        conn.execute(
            "UPDATE skill_mismatch_log SET status=? WHERE id=?", (status, mismatch_id)
        )
        out: dict[str, Any] = {"ok": True, "id": mismatch_id, "status": status}
        if status == "promote_to_gold":
            # Human-confirmed truth: the model's ask_output is the correct label.
            expected = (row["ask_output"] or "").strip().upper()
            if expected not in ("YES", "NO"):
                raise ValueError("cannot promote: ask_output is not YES/NO")
            case_key = f"mmgold_{uuid.uuid4().hex[:10]}"
            conn.execute(
                """
                INSERT INTO skill_prompt_case (
                    case_key, skill_key, image_path, target_name,
                    target_action, expected, source, labeler, status
                ) VALUES (?, ?, ?, ?, '', ?, 'mismatch_promoted', 'learning_center', 'active')
                """,
                (
                    case_key,
                    row["skill_key"],
                    row["image_path"],
                    row["target_name"],
                    expected,
                ),
            )
            out["promoted_case"] = case_key
        conn.commit()
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- lessons

def list_lessons(
    *,
    skill_key: str | None = None,
    source_type: str | None = None,
    status: str | None = None,
    limit: int = 200,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM skill_lesson WHERE 1=1"
        args: list[Any] = []
        if skill_key:
            sql += " AND skill_key=?"
            args.append(skill_key)
        if source_type:
            sql += " AND source_type=?"
            args.append(source_type)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_lesson(
    skill_key: str,
    lesson_text: str,
    *,
    source_type: str = "self_fail",
    root_cause: str | None = None,
    suggested_fix: str | None = None,
    source_ref: str | None = None,
    status: str = "draft",
    db_path: Path | str | None = None,
    conn: Any | None = None,
) -> dict[str, Any]:
    """File a lesson.

    `conn` lets the caller supply an EXISTING connection so the lesson joins the
    same transaction and the same database as the change it describes.

    MEASURED (2026-09-21) while building `factor_distill`: this function always
    opened its OWN connection, so a lesson filed while a caller held an
    in-memory database was written to a DIFFERENT database and silently
    vanished from the caller's view. The caller then reported "lesson missing"
    when the lesson had in fact been written — to the wrong place. A write that
    goes somewhere unexpected is worse than one that fails, because it looks
    like it worked.
    """
    # MEASURED (2026-09-21): this tuple is a SECOND copy of the source_type
    # list that `SKILL_LESSON_DDL` also declares as a CHECK. Adding
    # 'factor_change' to the DDL alone left the lesson unfiled, and the reason
    # was swallowed into a 'LESSON_FAILED' string — a silent loss at the exact
    # point the reason is supposed to be preserved. Both lists now agree.
    if source_type not in ("self_fail", "github_proofed_lesson",
                           "factor_change"):
        raise ValueError(f"invalid source_type: {source_type}")
    if status not in ("draft", "reviewed", "merged"):
        raise ValueError(f"invalid status: {status}")
    if not (lesson_text or "").strip():
        raise ValueError("lesson_text required")

    # WRITE GATE (citation_discipline contract): a lesson is a FINDING, so it
    # must carry a checkable citation. Without this the same root cause recurs
    # forever — the rule exists in a skill file, nothing enforces it, and a
    # lesson with no reference is written as if it were evidence. The report's
    # own §2 recorded exactly this: findings were written to markdown first, so
    # the DB rows carried no queryable reference.
    #
    # An uncited lesson is DISCARDED (refused), never downgraded to a
    # low-confidence lesson — a downgrade keeps the uncited finding alive under
    # a new label, which is the defect this gate exists to stop.
    import citation_discipline as cd

    cd.assert_cited({"source_ref": source_ref, "lesson_text": lesson_text})

    _owns = conn is None
    conn = conn if conn is not None else _connect(db_path)
    try:
        # IDEMPOTENCY. DEFECT FOUND BY RUNNING IT (2026-09-21): this INSERTed
        # unconditionally, so re-running a lesson-filing script DUPLICATED every
        # lesson. Measured: a first run filed 5, a second run filed 8, and the
        # table held 13 rows for 8 distinct lessons. A lesson is a FINDING, and
        # the same finding filed twice is not two findings — it inflates every
        # count that reads this table.
        #
        # The identity is (skill_key, source_ref, lesson_text): the same rule,
        # about the same skill, citing the same evidence. A genuinely new lesson
        # differs in at least one of the three.
        existing = conn.execute(
            "SELECT lesson_key FROM skill_lesson WHERE skill_key=? AND "
            "IFNULL(source_ref,'')=IFNULL(?,'') AND lesson_text=?",
            (skill_key, source_ref, lesson_text.strip())).fetchone()
        if existing:
            return {"ok": True, "lesson_key": str(existing[0]),
                    "created": False, "reason": "already filed"}
        lesson_key = f"ls_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO skill_lesson (
                lesson_key, skill_key, source_type, root_cause, lesson_text,
                suggested_fix, source_ref, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lesson_key,
                skill_key,
                source_type,
                root_cause,
                lesson_text.strip(),
                suggested_fix,
                source_ref,
                status,
            ),
        )
        conn.commit()
        return {"ok": True, "lesson_key": lesson_key}
    finally:
        # Only close a connection this function OPENED. Closing the caller's
        # connection would take their database out from under them.
        if _owns:
            conn.close()


# ---------------------------------------------------------------- case study

_CASE_STUDY_PROMPT = """你係一個 prompt 工程 case study 分析員。
以下係一個 vision skill 喺 ask<->confirm 測試入面嘅失敗分組統計（模型答錯嘅樣本）。

{groups_summary}

請分析根因並輸出候選改良。只回一個 JSON object，格式：
{{
  "root_cause": "一句話根因",
  "lesson_text": "可重用嘅教訓/規則（一句）",
  "suggested_fix": "建議改動（修改 prompt 或新增維度）",
  "candidate_prompt": "如果建議改 prompt，輸出完整新 prompt 文本；否則 null"
}}
唔好輸出 JSON 以外嘅文字。"""


def run_case_study(
    skill_key: str,
    *,
    limit: int = 50,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """skill_case_lesson_ingestor: group failures, 7B case study, emit lesson
    + optional candidate prompt version (draft only, never active)."""
    conn = _connect(db_path)
    try:
        wrong = conn.execute(
            """
            SELECT skill_key, version_label, target_name, image_path,
                   parsed_result, final_result, raw_response, model,
                   task_run_id, created_at
            FROM skill_prompt_inference
            WHERE skill_key=? AND is_wrong=1
            ORDER BY id DESC LIMIT ?
            """,
            (skill_key, max(1, int(limit))),
        ).fetchall()
        mism = conn.execute(
            """
            SELECT skill_key, version_label, target_name, image_path,
                   ask_output, expected, raw_response, model, run_id, created_at
            FROM skill_mismatch_log
            WHERE skill_key=? AND status='to_review'
            ORDER BY id DESC LIMIT ?
            """,
            (skill_key, max(1, int(limit))),
        ).fetchall()
        if not wrong and not mism:
            return {"ok": True, "skipped": "no failures found", "groups": []}

        # Group by (model_answer, expected) pattern.
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in wrong:
            d = dict(r)
            key = f"ans={d.get('parsed_result')} exp={d.get('final_result')}"
            groups.setdefault(key, []).append(
                {
                    "target": d.get("target_name"),
                    "image": d.get("image_path"),
                    "raw": (d.get("raw_response") or "")[:120],
                }
            )
        for r in mism:
            d = dict(r)
            key = f"ans={d.get('ask_output')} exp={d.get('expected')}"
            groups.setdefault(key, []).append(
                {
                    "target": d.get("target_name"),
                    "image": d.get("image_path"),
                    "raw": (d.get("raw_response") or "")[:120],
                }
            )

        # Compact summary (guardrail: keep prompt small).
        lines = []
        for key, items in groups.items():
            lines.append(f"[{key}] x{len(items)}")
            for it in items[:MAX_SAMPLES_PER_GROUP]:
                lines.append(
                    f"  - target={it['target']} img={Path(it['image'] or '').name} raw={it['raw']!r}"
                )
        groups_summary = "\n".join(lines)

        t0 = time.perf_counter()
        raw_llm = _llm_chat(_CASE_STUDY_PROMPT.format(groups_summary=groups_summary))
        parsed = _extract_json(raw_llm) or {}
        wall_ms = int((time.perf_counter() - t0) * 1000)

        lesson_key = f"ls_{uuid.uuid4().hex[:12]}"
        source_ref = f"case_study:{skill_key}:{len(wrong)}w/{len(mism)}m"
        conn.execute(
            """
            INSERT INTO skill_lesson (
                lesson_key, skill_key, source_type, root_cause, lesson_text,
                suggested_fix, source_ref, status
            ) VALUES (?, ?, 'self_fail', ?, ?, ?, ?, 'draft')
            """,
            (
                lesson_key,
                skill_key,
                str(parsed.get("root_cause") or "")[:500] or None,
                str(parsed.get("lesson_text") or "（LLM 未返回 lesson_text）")[:1000],
                str(parsed.get("suggested_fix") or "")[:1000] or None,
                source_ref,
            ),
        )

        candidate_version = None
        candidate_prompt = (parsed.get("candidate_prompt") or "").strip()
        if candidate_prompt:
            candidate_version = f"cand_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            skill_prompt.upsert_skill_prompt(
                conn,
                skill_key=skill_key,
                version_label=candidate_version,
                prompt_text=candidate_prompt,
                status="draft",
                source="learning_center",
                notes=f"candidate from case study {lesson_key}",
                activate=False,
                commit=False,
            )
        conn.commit()

        return {
            "ok": True,
            "skill_key": skill_key,
            "n_wrong": len(wrong),
            "n_mismatch": len(mism),
            "groups": [
                {"pattern": k, "count": len(v), "samples": v[:MAX_SAMPLES_PER_GROUP]}
                for k, v in groups.items()
            ],
            "lesson_key": lesson_key,
            "candidate_version": candidate_version,
            "llm_wall_ms": wall_ms,
            "raw_llm": raw_llm[:2000],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------- github import

_GITHUB_IMPORT_PROMPT = """你係一個 agent/eval 框架經驗提取員。
以下係來自 GitHub 開源項目嘅文檔片段。提取其中可重用嘅設計規則、常見陷阱、反模式，
轉為結構化 lesson。只提取知識，唔提取可執行代碼。

{text}

只回一個 JSON array，每個元素格式：
{{"lesson_text": "一句教訓/規則", "root_cause": "背後原因（可空）", "suggested_fix": "建議（可空）"}}
如果片段冇可重用教訓，回空 array []。唔好輸出 JSON 以外嘅文字。"""

# THE SCREEN: YES or NO, and NOTHING ELSE.
#
# WHY THIS EXISTS (measured 2026-09-21)
# -------------------------------------
# Four GitHub repos were imported and every one returned `[]`. That result is
# AMBIGUOUS: it means either "this document has no reusable lesson" or "the
# extractor failed". I could not tell which, and I spent several steps
# investigating the extractor — which was working the whole time (it returned 6
# lessons from a rules document).
#
# A YES/NO screen removes the ambiguity BEFORE the expensive extraction. It is
# also the cheapest possible question, so it costs nothing to ask first.
#
# "WITHOUT EXPLAIN" IS THE POINT, NOT A STYLE CHOICE
# --------------------------------------------------
# Asking for a reason invites the model to produce one, and a produced reason is
# indistinguishable from a real one. A bare YES/NO is a DECISION, and a decision
# can be counted. The explanation, when it is needed, comes from the extraction
# step — which only runs when the screen says YES.
#
# THE QUESTION COMES AFTER THE DOCUMENT, AND THAT ORDER IS LOAD-BEARING
# ---------------------------------------------------------------------
# MEASURED (2026-09-21): with the question FIRST, the two long documents
# (18342 and 25629 chars) produced NO ANSWER AT ALL. The raw replies were
# "Thank you for providing such a detailed overview of the Hoardable gem..." and
# "It looks like the message was cut off at the end. Here's the continuation..."
#
# The model was not failing to judge — it was treating the document as a message
# TO IT and replying conversationally. A long document placed before the
# instruction reads as the user's turn, so the model answers the document instead
# of the question about it. Putting the question LAST makes it the live
# instruction, and the short document (2589 chars) had worked all along precisely
# because it was too short to be mistaken for a message.
#
# The input is also capped: the screen only needs to judge WHETHER rules exist,
# and a 25k-char document is mostly usage detail that pushes the instruction out
# of the model's attention.
SCREEN_MAX_CHARS = 8000

# THE CRITERION MUST DISCRIMINATE, AND THE FIRST ONE DID NOT
# ---------------------------------------------------------
# MEASURED (2026-09-21), with a NEGATIVE CONTROL (a document with no rules at
# all: "This project is a Python library. Install with pip install foo."):
#
#   loose criterion ("does it contain a reusable rule?")
#       hoardable YES YES YES | cleanerversion YES YES YES | control YES YES YES
#       -> a RUBBER STAMP. It said YES to a document with nothing in it, so its
#          YES carried no information. This is the "answered only one class"
#          defect: 100% on one class and 0% on the other.
#
#   strict criterion (below: a rule APPLICABLE TO A DIFFERENT project)
#       control NO NO | hoardable NO NO | cleanerversion NO NO
#       POSITIVE (systematic-debugging SKILL.md) YES YES
#       POSITIVE (citation-discipline SKILL.md)  NO NO   <- FALSE NEGATIVE
#
# So the strict criterion DISCRIMINATES (it rejects the control) but has a
# false-negative rate: it missed one of two documents that definitely contain
# rules. That is the honest state, and it is why the screen is a FILTER and not
# a verdict — a NO means "do not spend the extraction call", not "there is
# nothing here".
LESSON_SCREEN_PROMPT = """DOCUMENT:
{text}

Answer YES only if the document states a RULE that a reader could APPLY to a
DIFFERENT project. Answer NO if it only describes what THIS project does, how to
install it, or how to use it.

Answer YES or NO. One word only. Do not explain."""


def screen_for_lessons(
    text: str, *, timeout: float = 120.0
) -> dict[str, Any]:
    """YES/NO: does this document contain a reusable lesson? No explanation.

    Returns `answer` as True / False / None. `None` means the model did not
    answer YES or NO, which is a THIRD outcome and is reported as such — a
    non-answer must not be read as NO, because "the model did not answer" and
    "the document has nothing" are different facts.
    """
    body = (text or "").strip()
    if not body:
        raise ValueError("screen_for_lessons: text required")
    raw = _llm_chat(LESSON_SCREEN_PROMPT.format(text=body[:SCREEN_MAX_CHARS]),
                    timeout=timeout)
    answer: bool | None = None
    for tok in re.split(r"[^A-Za-z]+", (raw or "").upper()):
        if tok == "YES":
            answer = True
            break
        if tok == "NO":
            answer = False
            break
    return {"answer": answer, "raw": (raw or "").strip()[:80],
            "answered": answer is not None}


def import_github_lessons(
    text_or_url: str,
    skill_key: str,
    *,
    is_url: bool = False,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """skill_lesson_github_importer: fetch/paste text, 7B extracts lessons."""
    text = (text_or_url or "").strip()
    if not text:
        raise ValueError("text_or_url required")
    source_ref = text[:500]
    if is_url:
        req = urllib.request.Request(
            text, headers={"User-Agent": "skill-learning-center/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        source_ref = text_or_url
    text = text[:MAX_URL_CHARS]

    t0 = time.perf_counter()
    raw_llm = _llm_chat(_GITHUB_IMPORT_PROMPT.format(text=text))
    wall_ms = int((time.perf_counter() - t0) * 1000)
    parsed = _extract_json(raw_llm)
    if not isinstance(parsed, list):
        return {
            "ok": False,
            "error": "LLM did not return a JSON array",
            "raw_llm": raw_llm[:1000],
        }

    conn = _connect(db_path)
    imported = 0
    lessons: list[str] = []
    try:
        for item in parsed:
            if not isinstance(item, dict):
                continue
            lesson_text = str(item.get("lesson_text") or "").strip()
            if not lesson_text:
                continue
            lesson_key = f"ls_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO skill_lesson (
                    lesson_key, skill_key, source_type, root_cause, lesson_text,
                    suggested_fix, source_ref, status
                ) VALUES (?, ?, 'github_proofed_lesson', ?, ?, ?, ?, 'draft')
                """,
                (
                    lesson_key,
                    skill_key,
                    str(item.get("root_cause") or "")[:500] or None,
                    lesson_text[:1000],
                    str(item.get("suggested_fix") or "")[:1000] or None,
                    source_ref,
                ),
            )
            lessons.append(lesson_key)
            imported += 1
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "imported": imported,
        "lessons": lessons,
        "source_ref": source_ref,
        "llm_wall_ms": wall_ms,
    }


# ---------------------------------------------------------------- candidates

# Hard ceiling on streak rounds. WHY A CEILING AT ALL
# --------------------------------------------------
# `skill_prompt.streak_proof()` loops `while streak < target_streak` and only
# stops early when `max_asks` is set. `test_candidate()` did not pass one, so a
# candidate was an UNBOUNDED process.
#
# MEASURED CONSEQUENCE (mouse_spot_verify, candidate cand_20260920_041356):
#   rounds run        = 1192
#   wrong             = 216   (18.1%)
#   best streak       = 9     (target 20)
#   per-case success  = 0.819
#   expected rounds to observe 20 consecutive successes ~= 125,000
# The run had to be killed. It was not slow — at that success rate it would not
# finish in any practical time.
#
# "20 consecutive" is exponentially sensitive to the per-case rate: at p=0.95
# the chance of a 20-run is 0.95^20 = 36%, so even a good skill is unlikely to
# clear it in a short run. A gate that cannot be passed is not a strict gate,
# it is a broken one — it also blocks the honest candidate alongside the bad one.
#
# So: bound the work, report the best streak achieved, and let the caller decide.
# The gate remains `best_streak >= target_streak`; what changes is that the
# attempt TERMINATES and reports a real number instead of hanging forever.
DEFAULT_MAX_ROUNDS = 400


def test_candidate(
    skill_key: str,
    version_label: str,
    *,
    prompt_key: str = "main",
    case_keys: list[str] | None = None,
    streak: int = 20,
    max_rounds: int | None = DEFAULT_MAX_ROUNDS,
    gate_mode: str = "streak",
    min_accuracy_pct: float = 90.0,
    min_samples: int = 100,
    test_seed: int | None = None,
    require_discriminating: bool = True,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Bounded validation of a draft candidate version.

    `max_rounds` bounds the work so a candidate that cannot reach the streak
    still returns a measurable result (its best streak) instead of running
    unbounded. Pass None for no ceiling — only appropriate for an offline run
    you are willing to let sit.

    `gate_mode` selects the gate SHAPE:
      "streak" - N consecutive correct (default; unchanged behaviour)
      "rate"   - accuracy >= min_accuracy_pct over >= min_samples

    `test_seed` fixes the gold-case shuffle. Pass a FIXED seed when comparing
    two versions (A/B): a None seed shuffles differently each call, so baseline
    and candidate would see different case orders and any difference would be
    confounded with case mix.

    `require_discriminating` adds a gate on the RUN, not the score: a run whose
    answers are all one class cannot support a claim about the prompt and is
    refused. See the discrimination block in skill_prompt.streak_proof.

    The default gate SHAPE is deliberately unchanged. Switching shape is a
    decision to be made from measured evidence (see gate_note below), not a way
    to make a candidate pass.
    """
    out = skill_prompt.streak_proof(
        skill_key=skill_key,
        version_label=version_label,
        prompt_key=prompt_key,
        case_keys=case_keys,
        target_streak=streak,
        max_asks=max_rounds,
        gate_mode=gate_mode,
        min_accuracy_pct=min_accuracy_pct,
        min_samples=min_samples,
        seed=test_seed,
        db_path=db_path,
    )
    if isinstance(out, dict):
        out["bounded"] = max_rounds is not None
        out["max_rounds"] = max_rounds
        out["test_seed"] = test_seed

        # BALANCED ACCURACY IS THE HEADLINE FOR AN IMBALANCED SET.
        # Plain accuracy and balanced accuracy answer different questions:
        #   accuracy          - "how often is it right overall"
        #   balanced accuracy - "how often is it right PER CLASS"
        # On mouse_spot_verify (14 NO : 6 YES) a prompt that always answers NO
        # scores 70% accuracy with 0% YES recall. Reporting only `accuracy_pct`
        # made that look like a partially-working prompt. Both are exposed, and
        # `gate_metric` names which one the gate used so a report can never quote
        # the flattering one by accident.
        out["gate_metric"] = gate_mode
        if not out.get("balanced_accuracy_pct") and out.get("per_class"):
            rec = [v["recall_pct"] for v in out["per_class"].values()]
            out["balanced_accuracy_pct"] = round(sum(rec) / len(rec), 2) if rec else 0.0

        # DISCRIMINATION GATE. The score gate above answers "did it hit the
        # number". It cannot answer "did this run measure the prompt at all".
        # A run that answered only one class scores a function of the gold
        # set's class mix, so two different prompts necessarily tie and the tie
        # gets misread as "no difference". Refuse it explicitly.
        disc = bool(out.get("discriminating"))
        out["discrimination_gate"] = {
            "applies": bool(require_discriminating),
            "ok": disc,
            "answer_classes": out.get("answer_classes"),
            "reason": (
                "run produced %s answer class(es) %s over %s asks — a "
                "single-class run measures the gold set's mix, not the prompt"
                % (len(out.get("answer_classes") or []),
                   out.get("answer_classes"), out.get("asks"))
                if not disc else
                "run produced %d answer classes %s"
                % (len(out.get("answer_classes") or []), out.get("answer_classes"))
            ),
        }
        if require_discriminating and not disc:
            out["pass_gate"] = False
            out["gate_note"] = ("discrimination gate failed: %s"
                                % out["discrimination_gate"]["reason"])

        if not out.get("pass_gate") and not out.get("gate_note"):
            # Say WHY it did not pass, so a low best_streak is never mistaken
            # for "the candidate is bad" when the attempt simply ran out of
            # rounds or the gate SHAPE is unreachable. Without this the caller
            # sees pass_gate=0 and blames the prompt.
            bs = int(out.get("best_streak") or 0)
            acc = out.get("accuracy_pct")
            if gate_mode == "rate":
                out["gate_note"] = (
                    "rate gate not met: accuracy=%s%% (need >=%s%%) over %s "
                    "samples (need >=%s)"
                    % (acc, min_accuracy_pct, out.get("asks") or 0, min_samples)
                )
            else:
                out["gate_note"] = (
                    "streak gate not met: best_streak=%d (need %d consecutive) "
                    "but accuracy=%s%% — a 'consecutive' gate is exponentially "
                    "sensitive to the per-case rate; compare against the "
                    "baseline before judging the prompt"
                    % (bs, streak, acc)
                )
    return out


def _contract_tdd_gate(skill_key: str,
                       *, db_path: Path | str | None = None) -> dict[str, Any]:
    """Run the skill's contract TDD cases before a merge is allowed.

    WHY THIS EXISTS
    ---------------
    A contract declared "a verdict without proof is invalid" and nothing ever
    ran it. Contract-without-runner is the same defect as rule-without-gate, so
    the merge path now executes the contract's cases. A candidate cannot be
    merged while a `hard_fail` case of its contract is failing.

    OPT-IN BY CONTRACT (deliberate)
    -------------------------------
    Only skills that HAVE an active contract with TDD cases are gated. A skill
    with no contract is NOT silently treated as passing — the result says
    `not_applicable` so the caller/report can show that this particular skill is
    unprotected. Silently reporting "gate ok" for an ungoverned skill would be
    the same false-confidence defect this whole mechanism exists to remove.

    Returns {applies, ok, reason, contract_id, detail}.
    """
    out = {"applies": False, "ok": True, "reason": "", "contract_id": None,
           "detail": None}
    try:
        import skill_contract_store as scs
    except Exception as e:
        out["reason"] = "contract store unavailable (%s)" % type(e).__name__
        return out
    try:
        contracts = [
            c for c in scs.list_contracts(status="active", db_path=db_path)
            if str(c.get("skill_key") or "") == str(skill_key)
        ]
    except Exception as e:
        out["reason"] = "contract lookup failed (%s)" % type(e).__name__
        return out
    if not contracts:
        out["reason"] = ("no active contract for %r — this skill is NOT covered "
                         "by a TDD gate" % skill_key)
        return out

    cid = contracts[0]["contract_id"]
    out["applies"] = True
    out["contract_id"] = cid
    try:
        cases = scs.list_tdd_cases(cid, db_path=db_path)
    except Exception as e:
        out["ok"] = False
        out["reason"] = "tdd case lookup failed (%s)" % type(e).__name__
        return out
    if not cases:
        out["ok"] = False
        out["reason"] = ("contract %s has no TDD cases — cannot prove the "
                         "protection, so the merge is refused" % cid)
        return out
    try:
        import importlib
        runner = importlib.import_module("skill_tdd_runner")
        res = runner.run_contract(cid, record=False, verbose=False,
                                  db_path=db_path)
    except Exception as e:
        out["ok"] = False
        out["reason"] = "tdd runner failed (%s: %s)" % (type(e).__name__, e)
        return out
    out["ok"] = bool(res.get("ok"))
    out["detail"] = {"n_cases": res.get("n_cases"), "n_passed": res.get("n_passed"),
                     "failed": [r["case_key"] for r in res.get("results", [])
                                if not r.get("passed")]}
    out["reason"] = ("contract %s TDD %d/%d passed"
                     % (cid, res.get("n_passed", 0), res.get("n_cases", 0)))
    return out


def _gold_set_gate(skill_key: str, *, db_path: Path | str | None = None) -> dict[str, Any]:
    """Refuse a merge whose test evidence comes from a DEGENERATE gold set.

    THE CASE THAT PRODUCED THIS
    ---------------------------
    `mouse_spot_verify` has 11 gold cases and **every one expects `NO`**. A
    prompt that always answers "NO" scores 100% and sets pass_gate=1. The DB
    already holds exactly that: run id=1, accuracy_pct=100.0, pass_gate=1, on a
    single-class set. So the run gate can be satisfied without the skill
    discriminating anything.

    Compare `captcha_cell_detect`: 78 YES / 192 NO. That set can actually fail a
    lazy prompt, which is why its 100% means something.

    A test set with one expected class is not evidence. This check makes the
    difference machine-readable instead of leaving it to whoever reads the
    numbers.

    SKEW (added after the A/B was voided)
    ------------------------------------
    Passing the single-class check is necessary but not sufficient. After 5 YES
    cases were added the set was 14 NO / 2 YES — two classes, so it looked
    healthy — yet a model that always answers NO still scores 87.5%, and a
    measured run answered NO 60/60 for a bit-identical tie between two
    different prompts. The gate now also reports the MINORITY class share; a
    set where one class is <20% cannot meaningfully compare two prompts.

    Returns {applies, ok, reason, classes, n_cases, minority_share, note}.
    """
    out: dict[str, Any] = {"applies": False, "ok": True, "reason": "",
                           "classes": {}, "n_cases": 0, "minority_share": None,
                           "note": ""}
    conn = None
    try:
        conn = _connect(db_path)
        # Count ONLY the cases the runner actually uses. `streak_proof` selects
        # skill_prompt_case WHERE status='active' AND image_path IS NOT NULL, so
        # counting draft rows here would let the gate approve a set that the
        # evidence was never measured on — the gate and the evidence must come
        # from the same rows or the check is decorative.
        rows = conn.execute(
            "SELECT expected FROM skill_prompt_case "
            "WHERE skill_key=? AND status='active' AND image_path IS NOT NULL",
            (skill_key,),
        ).fetchall()
    except Exception as e:
        out["reason"] = "gold case lookup failed (%s)" % type(e).__name__
        return out
    finally:
        if conn is not None:
            conn.close()

    n = len(rows)
    out["n_cases"] = n
    if n == 0:
        out["reason"] = ("no ACTIVE gold cases for %r — a candidate cannot be "
                         "validated" % skill_key)
        return out
    counts: dict[str, int] = {}
    for r in rows:
        k = str(r["expected"])
        counts[k] = counts.get(k, 0) + 1
    out["classes"] = counts
    out["applies"] = True
    if len(counts) <= 1:
        out["ok"] = False
        out["reason"] = (
            "gold set has a single expected class %s across %d cases — a prompt "
            "that always answers %s scores 100%%, so passing proves nothing. "
            "Add cases with the opposite expectation."
            % (list(counts), n, list(counts)[0])
        )
        return out
    out["minority_share"] = round(min(counts.values()) / n, 3)
    out["reason"] = "%d cases across %d classes %s" % (n, len(counts), counts)
    if out["minority_share"] < 0.20:
        out["note"] = (
            "gold set is skewed: the smallest class is %.1f%% of cases (%s). A "
            "model answering only the majority class still scores %.1f%%, so a "
            "high score on this set says little about the prompt. Add cases to "
            "the minority class before comparing prompts."
            % (out["minority_share"] * 100, counts,
               max(counts.values()) / n * 100)
        )
    return out


def merge_candidate(
    skill_key: str,
    version_label: str,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """Merge a candidate into the active Skill Library SSOT.

    Two guards, both required:
      1. the candidate's latest skill_prompt_test_run must have pass_gate=1
      2. the skill's contract TDD cases must all pass (when the skill has one)

    Guard 2 is reported as `contract_gate` in the response so a caller can see
    whether this skill was actually covered. A merge of an uncovered skill is
    allowed but returns `applies: false` — visible, not silent.
    """
    conn = _connect(db_path)
    try:
        ver = skill_prompt.get_skill_version(skill_key, version_label, conn=conn)
        if not ver:
            raise ValueError(f"version not found: {version_label}")
        if ver.get("status") == "active":
            return {"ok": True, "skipped": "already active"}
        run = conn.execute(
            """
            SELECT run_id, pass_gate, accuracy_pct, n_runs, created_at
            FROM skill_prompt_test_run
            WHERE skill_key=? AND version_label=?
            ORDER BY id DESC LIMIT 1
            """,
            (skill_key, version_label),
        ).fetchone()
        if not run or not run["pass_gate"]:
            return {
                "ok": False,
                "error": "candidate has no passing test run (pass_gate=1 required)",
                "latest_run": dict(run) if run else None,
            }
        # Guard 2: the contract's TDD cases must be green. Without this a
        # contract could declare a protection that never blocks anything.
        # db_path IS THREADED THROUGH, deliberately: without it the gate always
        # read the real agent.db, so an end-to-end test on a temp DB would have
        # silently gated production data. That is why this path had never been
        # exercised end to end.
        gate = _contract_tdd_gate(skill_key, db_path=db_path)
        if gate["applies"] and not gate["ok"]:
            return {
                "ok": False,
                "error": "contract TDD gate failed — %s" % gate["reason"],
                "contract_gate": gate,
                "latest_run": dict(run),
            }
        # Guard 3: the evidence must come from a gold set that can discriminate.
        # Guard 1 accepted a pass_gate=1 that was earned on a single-class set
        # (see _gold_set_gate docstring), so the run gate alone is not enough.
        gold = _gold_set_gate(skill_key, db_path=db_path)
        if gold["applies"] and not gold["ok"]:
            return {
                "ok": False,
                "error": "gold set gate failed — %s" % gold["reason"],
                "gold_gate": gold,
                "contract_gate": gate,
                "latest_run": dict(run),
            }
        row = skill_prompt.set_active_version(
            conn, skill_key=skill_key, version_label=version_label, commit=False
        )
        # Mark lessons whose source_ref mentions this candidate as merged.
        cur = conn.execute(
            """
            UPDATE skill_lesson SET status='merged', updated_at=CURRENT_TIMESTAMP
            WHERE skill_key=? AND status IN ('draft','reviewed')
              AND (source_ref LIKE ? OR source_ref LIKE ?)
            """,
            (skill_key, f"%{version_label}%", f"%cand%{version_label}%"),
        )
        conn.commit()
        return {
            "ok": True,
            "activated": dict(row) if row else None,
            "lessons_merged": cur.rowcount,
            "run_id": run["run_id"],
            "contract_gate": gate,
            "gold_gate": gold,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------- rewrite

_REWRITE_PROMPT = """你係一個 skill prompt 改寫工程師。

以下係一個 skill 而家上線緊嘅 prompt（active version）：
--- ACTIVE PROMPT START ---
{active_prompt}
--- ACTIVE PROMPT END ---

以下係累積落嚟、尚未併入嘅教訓（lessons）：
{lessons_block}

請把所有教訓併入 prompt，輸出一個**完整**嘅新 prompt 文本。

硬性規則：
1. 保留原 prompt 所有有效規則，唔准刪走或弱化。
2. 每條 lesson 必須轉為一條可執行、可驗證嘅硬規則（唔可以只係描述）。
3. 唔准加入例子、唔准加入解釋文字，只輸出 prompt 本身。
4. 如果某條 lesson 同 prompt 衝突，以 lesson 為準，並明確覆蓋舊規則。
5. 只回一個 JSON object，格式：
{{"prompt_text": "完整新 prompt 文本", "change_summary": "一句話總結改咗咩"}}
唔好輸出 JSON 以外嘅文字。"""


def rewrite_skill_from_lessons(
    lesson_keys: list[str] | None = None,
    *,
    skill_key: str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """skill_experience_rewrite: merge lessons into a NEW draft candidate prompt.

    LAW: this function can NEVER activate. It always writes status='draft' with
    activate=False. A candidate only reaches the library by passing a 20-run
    streak with pass_gate=1, then an explicit merge.

    The active prompt is snapshotted as `base_version` BEFORE any write so the
    rewrite is auditable against the exact text it was derived from.
    """
    keys = [str(k).strip() for k in (lesson_keys or []) if str(k).strip()]
    if not keys:
        raise ValueError("lesson_keys required")

    conn = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in keys)
        rows = conn.execute(
            f"SELECT * FROM skill_lesson WHERE lesson_key IN ({placeholders})",
            keys,
        ).fetchall()
        if not rows:
            raise ValueError(f"no lessons found for: {keys}")
        lessons = [dict(r) for r in rows]
        found = {str(l.get("lesson_key")) for l in lessons}
        missing = [k for k in keys if k not in found]

        # Resolve skill_key: explicit arg wins, else the lessons' own key.
        resolved = (skill_key or "").strip()
        if not resolved:
            resolved = str(lessons[0].get("skill_key") or "").strip()
        if not resolved:
            raise ValueError("cannot resolve skill_key (no lesson has one)")

        # get_active_skill() NEVER returns None — it falls back to any version,
        # to a file skill, or to a builtin placeholder. So "no active version"
        # has to be proved explicitly, otherwise we would silently rewrite a
        # DRAFT or a placeholder and call it a rewrite of the live prompt.
        active = skill_prompt.get_active_skill(resolved, db_path=db_path)
        if str(active.get("status") or "") != "active":
            raise ValueError(
                "no ACTIVE skill version for {!r} (highest version status={!r}); "
                "refusing to rewrite from a non-active base".format(
                    resolved, active.get("status")
                )
            )
        base_version = str(active.get("version_label") or "")
        active_prompt = str(active.get("prompt_text") or "").strip()
        if not active_prompt:
            raise ValueError(f"active version {base_version} has empty prompt_text")

        lessons_block = "\n".join(
            "- [seq {}] lesson: {}\n  root_cause: {}\n  suggested_fix: {}".format(
                i + 1,
                str(l.get("lesson_text") or "").strip(),
                str(l.get("root_cause") or "-").strip() or "-",
                str(l.get("suggested_fix") or "-").strip() or "-",
            )
            for i, l in enumerate(lessons)
        )

        t0 = time.perf_counter()
        raw_llm = _llm_chat(
            _REWRITE_PROMPT.format(
                active_prompt=active_prompt, lessons_block=lessons_block
            )
        )
        parsed = _extract_json(raw_llm) or {}
        wall_ms = int((time.perf_counter() - t0) * 1000)

        new_prompt = str(parsed.get("prompt_text") or "").strip()
        if not new_prompt:
            return {
                "ok": False,
                "error": "LLM returned no prompt_text",
                "raw_llm": raw_llm[:2000],
                "base_version": base_version,
                "missing_lessons": missing,
            }
        if new_prompt == active_prompt:
            return {
                "ok": False,
                "error": "rewrite produced an identical prompt (no change)",
                "base_version": base_version,
                "missing_lessons": missing,
            }

        candidate_version = f"cand_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        skill_prompt.upsert_skill_prompt(
            conn,
            skill_key=resolved,
            version_label=candidate_version,
            prompt_text=new_prompt,
            status="draft",
            source="learning_center",
            notes=(
                f"rewrite from lessons {','.join(keys)} "
                f"base={base_version} change={str(parsed.get('change_summary') or '')[:200]}"
            ),
            activate=False,          # LAW: never activate here
            commit=False,
        )
        # Mark source lessons as reviewed (not merged — merge happens later).
        conn.execute(
            f"UPDATE skill_lesson SET status='reviewed', updated_at=CURRENT_TIMESTAMP "
            f"WHERE lesson_key IN ({placeholders}) AND status='draft'",
            keys,
        )
        conn.commit()
        return {
            "ok": True,
            "skill_key": resolved,
            "candidate_version": candidate_version,
            "base_version": base_version,
            "lesson_keys": keys,
            "missing_lessons": missing,
            "change_summary": str(parsed.get("change_summary") or ""),
            "n_lessons": len(lessons),
            "wall_ms": wall_ms,
            "raw_llm": raw_llm[:2000],
        }
    finally:
        conn.close()


def list_candidates(
    skill_key: str | None = None,
    *,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT skill_key, version_label, status, notes, updated_at "
            "FROM skill_prompt_ssot WHERE status IN ('draft','testing')"
        )
        args: list[Any] = []
        if skill_key:
            sql += " AND skill_key=?"
            args.append(skill_key)
        sql += " ORDER BY updated_at DESC"
        out: list[dict[str, Any]] = []
        for r in conn.execute(sql, args).fetchall():
            d = dict(r)
            run = conn.execute(
                """
                SELECT run_id, pass_gate, accuracy_pct, n_runs, created_at
                FROM skill_prompt_test_run
                WHERE skill_key=? AND version_label=?
                ORDER BY id DESC LIMIT 1
                """,
                (d["skill_key"], d["version_label"]),
            ).fetchone()
            d["latest_run"] = dict(run) if run else None
            out.append(d)
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------- overview

def overview(*, db_path: Path | str | None = None) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        def _count(sql: str, args: tuple = ()) -> int:
            row = conn.execute(sql, args).fetchone()
            return int(row[0]) if row else 0

        return {
            "ok": True,
            "mismatches_to_review": _count(
                "SELECT COUNT(*) FROM skill_mismatch_log WHERE status='to_review'"
            ),
            "mismatches_total": _count("SELECT COUNT(*) FROM skill_mismatch_log"),
            "lessons_self_fail": _count(
                "SELECT COUNT(*) FROM skill_lesson WHERE source_type='self_fail'"
            ),
            "lessons_github": _count(
                "SELECT COUNT(*) FROM skill_lesson WHERE source_type='github_proofed_lesson'"
            ),
            "lessons_merged": _count(
                "SELECT COUNT(*) FROM skill_lesson WHERE status='merged'"
            ),
            "candidates": len(list_candidates(db_path=db_path)),
            "skills_with_failures": [
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT skill_key FROM skill_prompt_inference
                    WHERE is_wrong=1 ORDER BY skill_key
                    """
                ).fetchall()
            ],
        }
    finally:
        conn.close()
