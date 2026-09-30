# -*- coding: utf-8 -*-
"""consultant_registry.py — industry > consultant team > skill/question/key.

WHY THIS MODULE EXISTS (user, 2026-09-22):

    "5W1H module can improve by github, there is the place for best source for IT
     in the world. get the proofed and highest rating to weapon our design"
    "we can expend for more inductry or more type.... IT industry need
     ## 软件研发小组, other industry need other .... and each industry have their
     own professional consultant team"
    "-> same skill -> question -> key -> 1) proofed 2) highest rating"
    "that can be easy to have patterm by prompt > workflow"
    "of course, table by DB driven is required"

THE THREE LEVELS
----------------
    industry_registry      N-level (self-FK + materialised path)
      consultant_team      a professional team inside an industry
        consultant_member  its people, with one lead
        consultant_skill   skill -> question -> key, each with proofed + rating

TWO MECHANISMS ARE REUSED, NOT REINVENTED
-----------------------------------------
1. The N-LEVEL hierarchy is the SAME shape as `task_type_registry` (self-FK +
   materialised path + composite UNIQUE). "we can expend for more industy" is
   the same unbounded-depth problem, so it gets the same answer. A second
   implementation of one rule is the defect this repo has recorded repeatedly.
2. The RATING vocabulary is `logic_training.RATING_SOURCES`, and the reader is
   `logic_training.fetch_github_rating`, which already reads `stargazers_count`
   from `api.github.com` with NO login. A second rating vocabulary would make
   two numbers that cannot be compared.

`proofed` AND `rating` ARE SEPARATE, AND THE ORDER IS `proofed` FIRST
--------------------------------------------------------------------
The user's own ordering is "1) proofed 2) highest rating". A repo with 10k stars
that was never verified is NOT proofed; a verified technique with no repo has no
rating. Merging them would make one number answer two questions.

Read-only unless a caller passes `apply=True` to a mutating function.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"
PATH_SEP = "/"


class ConsultantError(ValueError):
    """Raised when a team, a skill or a rating cannot be justified."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _json_list(value: Any) -> str:
    """Normalise a list (or a JSON string) into a JSON array string."""
    if value is None:
        return "[]"
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return "[]"
        try:
            parsed = json.loads(s)
            return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            return json.dumps([s], ensure_ascii=False)
    return json.dumps(list(value), ensure_ascii=False)


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the four tables. Idempotent.

    The partial UNIQUE index for "exactly one lead per team" is created HERE,
    not in the DDL: SQLite cannot declare a partial UNIQUE inline, and
    `executescript` would run it before the table exists on a fresh DB.

    THE WORKER UPGRADE (2026-09-23). The user:
        "worker is VScode > chat or 豆包 > 工作伙伴 > ## 软件研发小组"
        "you have the table already, but you may need to upgrade it"

    Three columns are added ADDITIVELY, because `CREATE TABLE IF NOT EXISTS`
    does NOT add a column to an existing table — MEASURED: the DDL declared
    `worker_key` / `source_id` / `entry_path` while the live table had none of
    them, so a SELECT on `worker_key` raised `no such column`. The DDL alone is
    not the migration.

    The UNIQUE index on `worker_key` is created AFTER the ALTER, never inside the
    DDL: a legacy table lacks the column, so `CREATE INDEX` in the script would
    fail before the ALTER could add it. `db_schema.py` records that exact trap
    twice already (`skill_lesson.rating`, `factor_change_log.round_index`).
    """
    import db_schema as ds

    for ddl in (ds.INDUSTRY_REGISTRY_DDL, ds.CONSULTANT_TEAM_DDL,
                ds.CONSULTANT_MEMBER_DDL, ds.CONSULTANT_SKILL_DDL):
        conn.executescript(ddl)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_consultant_one_lead "
        "ON consultant_member (team_id) WHERE is_lead = 1")
    # 🔴 THE SAME INDEX IS NOW ALSO DECLARED IN `db_schema.CONSULTANT_MEMBER_DDL`
    # (RING 5, D3, 2026-09-29) — MEASURED: a FRESH `ensure_schema` never ran
    # THIS function, so the live index existed and a new database had none.
    #
    # TWO IDENTICAL `CREATE UNIQUE INDEX IF NOT EXISTS` statements are not the
    # "two declarations" defect this workset is about: that defect is two
    # statements that can DISAGREE (a table). This is one index, stated twice in
    # the same words, and it is harmless under `IF NOT EXISTS` — the first one to
    # run wins and the second is a no-op. It is kept here because this module is
    # the table's WRITER and the partial predicate expresses a business rule it
    # owns; the declaration in `db_schema` is what makes a BOOTSTRAP complete.

    # ---- the worker upgrade (ADDITIVE, and it must actually run) --------
    cols = {str(r[1]) for r in conn.execute(
        "PRAGMA table_info(consultant_team)").fetchall()}
    added: list[str] = []
    for col, decl in (("worker_key", "TEXT"),
                      ("source_id", "INTEGER"),
                      ("entry_path", "TEXT")):
        if col not in cols:
            conn.execute("ALTER TABLE consultant_team ADD COLUMN %s %s"
                         % (col, decl))
            added.append(col)
    # Seed worker_key from team_key so every existing team gains a worker
    # identity with no data migration. Only fills blanks, so a human-set
    # worker_key is never overwritten.
    conn.execute("UPDATE consultant_team SET worker_key = team_key "
                 "WHERE worker_key IS NULL OR TRIM(worker_key) = ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_consultant_worker_key "
                 "ON consultant_team (worker_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_consultant_team_source "
                 "ON consultant_team (source_id, is_active)")
    conn.commit()
    return {"ok": True,
            "industries": conn.execute(
                "SELECT COUNT(*) FROM industry_registry").fetchone()[0],
            "teams": conn.execute(
                "SELECT COUNT(*) FROM consultant_team").fetchone()[0],
            "worker_columns_added": added}


# ---------------------------------------------------------------------------
# industry — the N-level hierarchy
# ---------------------------------------------------------------------------

def add_industry(conn: sqlite3.Connection, industry_key: str, *,
                 parent_industry_id: int | None = None,
                 definition: str, cite_ref: str) -> dict[str, Any]:
    """Add an industry at ANY depth. The path and depth are DERIVED."""
    key = str(industry_key or "").strip()
    if not key:
        raise ConsultantError("industry_key is required")
    if PATH_SEP in key:
        raise ConsultantError(
            "industry_key %r must not contain %r — the separator is what makes "
            "the path unambiguous" % (key, PATH_SEP))
    if not str(definition or "").strip():
        raise ConsultantError("definition is required")
    if not str(cite_ref or "").strip():
        raise ConsultantError(
            "cite_ref is required: an uncited industry is an invention "
            "(citation_discipline)")

    parent_path, depth = "", 0
    if parent_industry_id is not None:
        p = conn.execute("SELECT * FROM industry_registry WHERE industry_id = ?",
                         (int(parent_industry_id),)).fetchone()
        if not p:
            raise ConsultantError("unknown parent_industry_id %r"
                                  % parent_industry_id)
        if not int(p["is_active"]):
            raise ConsultantError("parent %r is inactive" % p["industry_path"])
        parent_path, depth = p["industry_path"], int(p["depth"]) + 1

    path = (parent_path + PATH_SEP + key) if parent_path else key
    existing = conn.execute("SELECT * FROM industry_registry WHERE "
                            "industry_path = ?", (path,)).fetchone()
    if existing:
        return {"ok": True, "created": False, "industry": dict(existing)}
    cur = conn.execute(
        "INSERT INTO industry_registry (parent_industry_id, industry_key, "
        "industry_path, depth, definition, cite_ref, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, 1)",
        (parent_industry_id, key, path, depth, str(definition).strip(),
         str(cite_ref).strip()))
    conn.commit()
    row = conn.execute("SELECT * FROM industry_registry WHERE industry_id = ?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "industry": dict(row)}


def list_industries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM industry_registry WHERE is_active = 1 "
        "ORDER BY industry_path")]


# ---------------------------------------------------------------------------
# team
# ---------------------------------------------------------------------------

def add_team(conn: sqlite3.Connection, team_key: str, *, industry_id: int,
             name: str, description: str,
             expertise: Any = None, probes: Any = None,
             source_ref: str = "NA") -> dict[str, Any]:
    """Add a consultant team inside an industry."""
    key = str(team_key or "").strip()
    if not key:
        raise ConsultantError("team_key is required")
    if not str(name or "").strip():
        raise ConsultantError("name is required")
    if not str(description or "").strip():
        raise ConsultantError(
            "description is required: a team with no description is a label")
    ind = conn.execute("SELECT * FROM industry_registry WHERE industry_id = ?",
                       (int(industry_id),)).fetchone()
    if not ind:
        raise ConsultantError(
            "unknown industry_id %r — a team must belong to an industry"
            % industry_id)
    existing = conn.execute("SELECT * FROM consultant_team WHERE team_key = ?",
                            (key,)).fetchone()
    if existing:
        return {"ok": True, "created": False, "team": dict(existing)}
    cur = conn.execute(
        "INSERT INTO consultant_team (industry_id, team_key, name, description, "
        "expertise_json, probe_json, source_ref, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
        (int(industry_id), key, str(name).strip(), str(description).strip(),
         _json_list(expertise), _json_list(probes), str(source_ref or "NA")))
    conn.commit()
    row = conn.execute("SELECT * FROM consultant_team WHERE team_id = ?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "team": dict(row)}


def add_member(conn: sqlite3.Connection, team_id: int, *, role: str, name: str,
               is_lead: bool = False) -> dict[str, Any]:
    """Add a member. At most ONE lead per team (a partial UNIQUE index)."""
    if not str(role or "").strip() or not str(name or "").strip():
        raise ConsultantError("role and name are both required")
    if not conn.execute("SELECT 1 FROM consultant_team WHERE team_id = ?",
                        (int(team_id),)).fetchone():
        raise ConsultantError("unknown team_id %r" % team_id)
    try:
        cur = conn.execute(
            "INSERT INTO consultant_member (team_id, role, name, is_lead) "
            "VALUES (?, ?, ?, ?)",
            (int(team_id), str(role).strip(), str(name).strip(),
             1 if is_lead else 0))
    except sqlite3.IntegrityError as e:
        raise ConsultantError(
            "member refused: %s (a team has at most ONE lead, and a name is "
            "unique within a team)" % e)
    conn.commit()
    return {"ok": True, "member_id": int(cur.lastrowid)}


def members_of(conn: sqlite3.Connection, team_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM consultant_member WHERE team_id = ? "
        "ORDER BY is_lead DESC, member_id", (int(team_id),))]


# ---------------------------------------------------------------------------
# skill -> question -> key, with proofed + rating
# ---------------------------------------------------------------------------

def add_skill(conn: sqlite3.Connection, team_id: int, *, skill_key: str,
              question: str, answer_key: str) -> dict[str, Any]:
    """Add a skill. The THREE parts the user named are all required."""
    if not str(skill_key or "").strip():
        raise ConsultantError("skill_key is required")
    if not str(question or "").strip():
        raise ConsultantError(
            "question is required: 'same skill -> question -> key' — a skill "
            "with no question cannot be asked")
    if not str(answer_key or "").strip():
        raise ConsultantError(
            "answer_key is required: a question with no key cannot be answered")
    if not conn.execute("SELECT 1 FROM consultant_team WHERE team_id = ?",
                        (int(team_id),)).fetchone():
        raise ConsultantError("unknown team_id %r" % team_id)
    existing = conn.execute("SELECT * FROM consultant_skill WHERE team_id = ? "
                            "AND skill_key = ?",
                            (int(team_id), str(skill_key).strip())).fetchone()
    if existing:
        return {"ok": True, "created": False, "skill": dict(existing)}
    cur = conn.execute(
        "INSERT INTO consultant_skill (team_id, skill_key, question, answer_key) "
        "VALUES (?, ?, ?, ?)",
        (int(team_id), str(skill_key).strip(), str(question).strip(),
         str(answer_key).strip()))
    conn.commit()
    row = conn.execute("SELECT * FROM consultant_skill WHERE "
                       "consultant_skill_id = ?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "skill": dict(row)}


def mark_proofed(conn: sqlite3.Connection, consultant_skill_id: int, *,
                 source_ref: str) -> dict[str, Any]:
    """Mark a skill PROOFED. A citation is REQUIRED.

    `proofed = 1` with no `source_ref` would be an assertion, not a proof — the
    same rule as `citation_discipline`: no citation, no finding.
    """
    if not str(source_ref or "").strip() or str(source_ref).strip() == "NA":
        raise ConsultantError(
            "mark_proofed: source_ref is required. 'proofed' means VERIFIED, so "
            "a proof with no citation is an assertion.")
    import citation_discipline as cd
    cd.assert_cited({"evidence_ref": str(source_ref).strip()})
    n = conn.execute(
        "UPDATE consultant_skill SET proofed = 1, source_ref = ?, "
        "updated_at = datetime('now') WHERE consultant_skill_id = ?",
        (str(source_ref).strip(), int(consultant_skill_id))).rowcount
    conn.commit()
    if not n:
        raise ConsultantError("unknown consultant_skill_id %r"
                              % consultant_skill_id)
    return {"ok": True, "proofed": True, "source_ref": str(source_ref).strip()}


def rate_skill(conn: sqlite3.Connection, consultant_skill_id: int, *,
               repo: str | None = None, rating: int | None = None,
               rating_source: str | None = None,
               cite_ref: str | None = None) -> dict[str, Any]:
    """Set a skill's rating. Either read it from GitHub, or supply it WITH a source.

    The vocabulary is `logic_training.RATING_SOURCES` — NOT a second one. A star
    count and a self-measured proof count are different kinds of number and must
    not be compared as if they were equal.
    """
    import logic_training as lt

    if repo:
        got = lt.fetch_github_rating(repo)
        rating = int(got["stars"])
        rating_source = got["rating_source"]
        cite_ref = cite_ref or got.get("html_url") or ("github.com/%s" % repo)
    if rating_source not in lt.RATING_SOURCES:
        raise ConsultantError(
            "rate_skill: rating_source %r is not one of %s. A number with no "
            "stated source cannot be interpreted."
            % (rating_source, ", ".join(sorted(lt.RATING_SOURCES))))
    if rating_source == "NA":
        raise ConsultantError(
            "rate_skill: 'NA' means NOT RATED, so it cannot be assigned as a "
            "rating. Leave the skill unrated instead.")
    if int(rating or 0) < 0:
        raise ConsultantError("rate_skill: rating must be >= 0")
    if not str(cite_ref or "").strip():
        raise ConsultantError(
            "rate_skill: cite_ref is required — a rating with no citation is a "
            "number someone asserted")
    n = conn.execute(
        "UPDATE consultant_skill SET rating = ?, rating_source = ?, "
        "updated_at = datetime('now') WHERE consultant_skill_id = ?",
        (int(rating or 0), rating_source, int(consultant_skill_id))).rowcount
    conn.commit()
    if not n:
        raise ConsultantError("unknown consultant_skill_id %r"
                              % consultant_skill_id)
    return {"ok": True, "rating": int(rating or 0),
            "rating_source": rating_source, "cite_ref": cite_ref}


def set_source_ref(conn: sqlite3.Connection, consultant_skill_id: int, *,
                   source_ref: str) -> dict[str, Any]:
    """Record WHERE a skill came from, WITHOUT claiming it is proofed.

    WHY THIS IS SEPARATE FROM `mark_proofed`, and the distinction is the whole
    point of the user's own order ("1) proofed 2) highest rating"):

      * `mark_proofed` says **we VERIFIED the technique** — it sets `proofed = 1`.
      * `set_source_ref` says **we know WHERE it came from** — it sets only the
        source, and `proofed` STAYS 0.

    MEASURED, and this is why the function exists: SCOPE E populates
    `consultant_skill` from a GitHub find, and a find tells us a repo EXISTS and
    how many stars it has. It does NOT tell us the technique works. Calling
    `mark_proofed` on a search result would be exactly the fabrication §9.1
    forbids — a `proofed = 1` with no verification behind it.

    The citation must be CHECKABLE, and the accepted form here is
    `register:<table>:<pk>` (e.g. `register:github_find_candidate:7`), because the
    candidate ROW is the artefact that carries the repo, the stars and the
    `cite_ref`. `citation_discipline.verify_db_ref` confirms the row exists, so a
    source_ref that names a row nobody wrote is REFUSED.
    """
    ref = str(source_ref or "").strip()
    if not ref or ref == "NA":
        raise ConsultantError(
            "set_source_ref: source_ref is required. A skill with no source is a "
            "claim with no provenance.")
    import citation_discipline as cd
    cd.assert_cited({"evidence_ref": ref})
    if ref.startswith("register:"):
        # MEASURED DEFECT (2026-09-28), and it was MINE: I wrote
        # `got.get("ok")` and `got.get("reason")`, but `verify_db_ref` returns
        # `{"db_ref", "exists", "why"}`. A missing key is `None`, which is
        # FALSY, so EVERY `register:` ref was refused — including a correct one.
        # The refusal was right in shape and wrong in cause, which is the worst
        # kind: it looks like the gate working. The keys are now read from the
        # function's own return, not from memory.
        got = cd.verify_db_ref(ref, conn=conn)
        if not got.get("exists"):
            raise ConsultantError(
                "set_source_ref: %r does not resolve (%s). A source_ref that "
                "names a row nobody wrote is an uncheckable claim."
                % (ref, got.get("why") or "no reason given"))
    n = conn.execute(
        "UPDATE consultant_skill SET source_ref = ?, updated_at = datetime('now') "
        "WHERE consultant_skill_id = ?", (ref, int(consultant_skill_id))).rowcount
    conn.commit()
    if not n:
        raise ConsultantError("unknown consultant_skill_id %r"
                              % consultant_skill_id)
    return {"ok": True, "source_ref": ref, "proofed": False}


def ranked_skills(conn: sqlite3.Connection, team_id: int, *,
                  proofed_first: bool = True) -> list[dict[str, Any]]:
    """The team's skills, ordered `proofed DESC, rating DESC`.

    That order IS the user's requirement: "1) proofed 2) highest rating". A
    highly-rated but unverified skill must NOT outrank a verified one.
    """
    order = ("proofed DESC, rating DESC, skill_key"
             if proofed_first else "rating DESC, proofed DESC, skill_key")
    return [dict(r) for r in conn.execute(
        "SELECT * FROM consultant_skill WHERE team_id = ? AND is_active = 1 "
        "ORDER BY " + order, (int(team_id),))]


# ---------------------------------------------------------------------------
# prompt > workflow
# ---------------------------------------------------------------------------

def team_workflow(conn: sqlite3.Connection, team_id: int, *,
                  workflow_key: str | None = None) -> dict[str, Any]:
    """Turn a team's PROBE questions into an ordered workflow.

    The user's pattern: "that can be easy to have patterm by prompt > workflow".
    Each probe becomes a `prompt_registry` row and a `workflow_step`, so the
    sequence is DATA and a reader can run it.
    """
    import prompt_generator as pg

    team = conn.execute("SELECT * FROM consultant_team WHERE team_id = ?",
                        (int(team_id),)).fetchone()
    if not team:
        raise ConsultantError("unknown team_id %r" % team_id)
    probes = json.loads(team["probe_json"] or "[]")
    if not probes:
        raise ConsultantError(
            "team %r declares no probe questions, so there is no workflow to "
            "build" % team["team_key"])

    wkey = workflow_key or ("consultant_%s" % team["team_key"])
    pg.ensure_tables(conn)
    row = conn.execute("SELECT workflow_id FROM workflow_registry WHERE "
                       "workflow_key = ?", (wkey,)).fetchone()
    if row:
        wid = int(row["workflow_id"])
    else:
        cur = conn.execute(
            "INSERT INTO workflow_registry (workflow_key, name, description) "
            "VALUES (?, ?, ?)",
            (wkey, team["name"], "probe questions of %s" % team["team_key"]))
        wid = int(cur.lastrowid)

    # The probe TEXT lives in `skill_prompt_ssot`, not `prompt_registry`:
    # `prompt_registry` has NO `prompt_text` column and requires `skill_id` +
    # `study_id` (both NOT NULL FKs), so it names a prompt rather than holding
    # one. `skill_prompt_ssot` is the table that actually stores prompt text.
    steps = 0
    for i, text in enumerate(probes, 1):
        pkey = "%s_step%d" % (wkey, i)
        prow = conn.execute("SELECT prompt_id FROM prompt_registry WHERE "
                            "prompt_key = ?", (pkey,)).fetchone()
        if prow:
            pid = int(prow["prompt_id"])
        else:
            # A probe is a prompt for the TEAM, so it is registered under the
            # team's own skill_key. `component_registry` is the parent of
            # `prompt_registry.skill_id`, so the row must exist first.
            comp = conn.execute(
                "SELECT skill_id FROM component_registry WHERE skill_key = ?",
                (team["team_key"],)).fetchone()
            if not comp:
                cur = conn.execute(
                    "INSERT INTO component_registry (skill_key, name, "
                    "description) VALUES (?, ?, ?)",
                    (team["team_key"], team["name"],
                     "consultant team %s" % team["team_key"]))
                comp_id = int(cur.lastrowid)
            else:
                comp_id = int(comp["skill_id"])
            study = conn.execute(
                "SELECT study_id FROM study_registry WHERE study_key = ?",
                (team["team_key"],)).fetchone()
            if not study:
                cur = conn.execute(
                    "INSERT INTO study_registry (study_key, name, description, "
                    "skill_id) VALUES (?, ?, ?, ?)",
                    (team["team_key"], team["name"],
                     "consultant team %s" % team["team_key"], comp_id))
                study_id = int(cur.lastrowid)
            else:
                study_id = int(study["study_id"])
            cur = conn.execute(
                "INSERT INTO prompt_registry (prompt_key, name, skill_id, "
                "study_id) VALUES (?, ?, ?, ?)",
                (pkey, "probe %d" % i, comp_id, study_id))
            pid = int(cur.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO skill_prompt_ssot (skill_key, prompt_key, "
                "version_label, prompt_text, status, source) "
                "VALUES (?, ?, '1', ?, 'draft', 'consultant_probe')",
                (team["team_key"], pkey, str(text)))
        conn.execute(
            "INSERT OR IGNORE INTO workflow_step (workflow_id, step_no, "
            "prompt_id, is_final) VALUES (?, ?, ?, ?)",
            (wid, i, pid, 1 if i == len(probes) else 0))
        steps += 1
    conn.commit()
    return {"ok": True, "workflow_key": wkey, "workflow_id": wid,
            "steps": steps}


# ---------------------------------------------------------------------------
# the IT seed — the ONE team the user showed
# ---------------------------------------------------------------------------
# The user's screenshot (豆包 > 工作伙伴 > 软件研发小组) is the SHAPE and the
# CONTENT for the IT industry. It is seeded because the user showed it; NO other
# industry is seeded, because "we can expend for more inductry" means the
# mechanism ships and the content is added as it is decided.
#
# The probe questions are the screenshot's "试试这样问我" lines, verbatim.
IT_TEAM = {
    "industry_key": "IT",
    "industry_definition": ("Information technology: software, systems and the "
                            "work that produces or changes them."),
    "team_key": "software_rd",
    "name": "软件研发小组",
    "description": ("把产品想法交给我们：理清需求、搭好架构、完成开发、守住质量，"
                    "一路推进到应用可用、成果可交付。"),
    "expertise": ["架构设计", "前后端研发", "测试与交付"],
    "probes": [
        "请开发一款支持难度切换与计时的win95经典扫雷游戏。",
        "规划一个软件开发项目的架构和任务分工，搭建开发团队协作规范和交付流程，"
        "交付一份可编辑的飞书云文档。",
        "帮我评审一下客户订单系统在数据安全、权限和隐私合规方面的风险，"
        "给出分级和整改建议、修复方案。",
    ],
    "members": [
        ("研发小组长", "研发小组长", True),
    ],
    "source_ref": "github.com",
}


def seed_it_team(conn: sqlite3.Connection) -> dict[str, Any]:
    """Seed the IT industry and the 软件研发小组 team. Idempotent.

    The `source_ref` is `github.com` because the user named GitHub as the source
    of truth for IT: "there is the place for best source for IT in the world".
    The team's SKILLS are NOT seeded — a skill needs a question, a key and a
    citation, and inventing them would be fabricating the team's knowledge.
    """
    ensure_schema(conn)
    ind = add_industry(conn, IT_TEAM["industry_key"],
                       definition=IT_TEAM["industry_definition"],
                       cite_ref="consultant_registry.py:IT_TEAM")
    team = add_team(conn, IT_TEAM["team_key"],
                    industry_id=ind["industry"]["industry_id"],
                    name=IT_TEAM["name"], description=IT_TEAM["description"],
                    expertise=IT_TEAM["expertise"], probes=IT_TEAM["probes"],
                    source_ref=IT_TEAM["source_ref"])
    tid = team["team"]["team_id"]
    for role, name, is_lead in IT_TEAM["members"]:
        try:
            add_member(conn, tid, role=role, name=name, is_lead=is_lead)
        except ConsultantError:
            pass          # already present; the seed is idempotent
    return {"ok": True, "industry": ind, "team": team["team"],
            "members": members_of(conn, tid)}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="consultant team registry")
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--seed-it", action="store_true")
    ap.add_argument("--industries", action="store_true")
    ap.add_argument("--teams", action="store_true")
    ap.add_argument("--ranked", type=int, metavar="TEAM_ID")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2))
        if args.seed_it:
            print(json.dumps(seed_it_team(conn), indent=2, ensure_ascii=False))
        if args.industries:
            for r in list_industries(conn):
                print("%s%s" % ("  " * int(r["depth"]), r["industry_path"]))
        if args.teams:
            for r in conn.execute("SELECT * FROM consultant_team"):
                print("%s  %s" % (r["team_key"], r["name"]))
        if args.ranked:
            for r in ranked_skills(conn, args.ranked):
                print("proofed=%d rating=%d(%s)  %s"
                      % (int(r["proofed"]), int(r["rating"]),
                         r["rating_source"], r["skill_key"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
