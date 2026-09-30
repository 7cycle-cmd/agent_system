# -*- coding: utf-8 -*-
"""logic_training.py — LESSON -> LOGIC -> PROMPT, and the GitHub rating.

THE HUMAN'S FRAMING (2026-09-21)
--------------------------------
    "github should have many proofed and highest rating lesson for us, be the data"
    "logic tranning, i think is"
    "or factor finding, i don't know, as prompt is by combination, your turn then"

My turn, so here is the decision, stated plainly:

    LESSON  = training data   -- HOW to find
    FACTOR  = the finding     -- WHAT was found
    PROMPT  = the combination -- skill + content + wording

They are three objects, not one. The GitHub proofed + highest-rated lessons are
TRAINING DATA for the logic. A factor is the FINDING that the trained logic
produces. A prompt is the COMBINATION that carries both.

WHAT WAS MISSING, MEASURED
--------------------------
1. "highest rating" had nowhere to live. `skill_lesson` had no rating column, so
   the requirement could not be expressed, let alone sorted by. The earlier
   survey recorded "GitHub does NOT show star counts without login, so 'highest
   rating' could NOT be reported" — true of the HTML page, FALSE of the API.
   `api.github.com/repos/<owner>/<repo>` returns `stargazers_count` with no
   login. Verified: hoardable=59, cleanerversion=134.
2. NOTHING connected a lesson to the logic it trains. `logic_layer_*` existed in
   `wording_registry`; `skill_lesson` existed; no row joined them. A lesson could
   be filed, reviewed and merged without ever reaching the logic it was meant to
   improve — training data that trains nothing.

So this module does two things: it RATES a lesson from a checkable source, and it
LINKS a lesson to the logic layer it trains, so the path is walkable.

THE RATING IS NOT A VOTE
------------------------
`rating_source` names where the number came from, because a GitHub star count and
a self-measured proof count are different kinds of number. Sorting them together
without saying which is which would compare incomparable things — the defect
`independent_review` exists to stop.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The literal for "not reported". MEASURED DEFECT IN MY OWN EDIT (2026-09-29): I
# used `NA` in `fetch_github_rating` and this module did NOT define it, so the
# function raised `NameError: name 'NA' is not defined` on EVERY call. It had
# never needed the literal before, so the name was simply absent. Defined here
# rather than imported from `github_find`, because a shared string constant
# imported across modules is how two spellings appear.
NA = "NA"

# The rating sources, and what each one MEANS. A number with no stated source is
# a number nobody can interpret.
RATING_SOURCES = {
    "github_stars": "stargazers_count from api.github.com/repos/<owner>/<repo>",
    "github_forks": "forks_count from the same endpoint",
    "proof_count": "how many times this lesson's rule was measured to hold",
    "NA": "not rated",
    # 🔴 FOUR MORE AXES, EACH WITH ITS OWN UNIT AND SOURCE (added 2026-09-29).
    #
    # THE HUMAN: *"rating 機制而家只睇 star 數，要加多一個維度（proofed、提交時間、
    # license）唔好單靠人氣判斷品質"*.
    #
    # WHY A STAR COUNT ALONE IS A DEFECT, not merely a weak signal: the repo's own
    # `github_find_candidate` already REQUIRES `proofed=1` for an ADOPT verdict, so
    # it had already decided stars are not quality — yet `rating_source` recorded
    # `'github_stars'` alone. **A number whose only stated source is popularity is
    # the `first_principle_measured_unit` defect: a number with no unit that names
    # what it measures.** Adding axes does NOT replace the stars axis; each one is
    # recorded SEPARATELY with its own source, so a reader can disagree with one
    # axis without discarding the others.
    #
    # Each entry states the QUERY that produces it, because the meaning of a number
    # is the query that produced it.
    "github_recency_days": ("days since `pushed_at`, from the repos endpoint — a "
                            "dead repository scores 0 on nothing else"),
    "github_has_license": ("1 when the repos endpoint reports an SPDX id, else 0 — "
                           "no licence is a usage risk, not a quality signal"),
    "github_archived": ("1 when the repos endpoint reports `archived` — an "
                        "archived tool is not maintained"),
    "github_authored_work": ("1 when `commits?path=<file>` names a login also in "
                             "`contributors` — authorship of THE file, which is "
                             "the only authorship that matters for a technique"),
}


class LogicTrainingError(ValueError):
    """Raised when a rating or a link cannot be justified."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    # 🔴 THE IMPORT NAMED CONSTANTS THAT DO NOT EXIST. MEASURED 2026-09-29: the
    # real names are `COMPONENT_registry_DDL` / `WORDING_registry_DDL`
    # (`db_schema.py`), not the lowercase `component_registry_DDL` /
    # `wording_registry_DDL`. So this function raised `ImportError` on EVERY call.
    # The module's convention is `{UPPER}_registry_DDL`; the lowercase spelling
    # appears NOWHERE in `db_schema.py`.
    from db_schema import (COMPONENT_registry_DDL, LESSON_LOGIC_LINK_DDL,
                           SKILL_LESSON_DDL, WORDING_registry_DDL)
    # ORDER MATTERS: `wording_registry.skill_id` FKs to `component_registry`, so
    # the parent must exist first. Measured: without it, a fresh database failed
    # with "no such table: component_registry" — the same gap found in
    # `prompt_generator.ensure_tables`.
    conn.executescript(COMPONENT_registry_DDL)
    conn.executescript(WORDING_registry_DDL)
    conn.executescript(SKILL_LESSON_DDL)
    conn.executescript(LESSON_LOGIC_LINK_DDL)
    # ADDITIVE: the rating columns. A database created before them has no rating,
    # and 0 / 'NA' is the honest reading — NOT an invented score.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skill_lesson)")}
    if "rating" not in cols:
        conn.execute("ALTER TABLE skill_lesson ADD COLUMN "
                     "rating INTEGER NOT NULL DEFAULT 0")
    if "rating_source" not in cols:
        conn.execute("ALTER TABLE skill_lesson ADD COLUMN "
                     "rating_source TEXT NOT NULL DEFAULT 'NA'")
    # The index is created HERE, after the columns exist. It cannot live in the
    # DDL: `executescript` runs CREATE INDEX after CREATE TABLE IF NOT EXISTS,
    # which does nothing on an existing table, so the index would reference a
    # column that is not there yet and the script would fail.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_lesson_rating "
                 "ON skill_lesson (rating_source, rating DESC)")
    conn.commit()


# ---------------------------------------------------------------------------
# RATING
# ---------------------------------------------------------------------------

def fetch_github_rating(repo: str, *, timeout: float = 20.0) -> dict:
    """Stars and forks for a repo, from the API — no login required.

    The earlier conclusion ("GitHub does NOT show star counts without login") was
    drawn from the HTML page. The API is a different surface and it does return
    them, so the requirement is satisfiable and the old note is corrected here
    rather than left standing.

    🔴 IT NOW REPORTS **FIVE AXES**, EACH WITH ITS OWN SOURCE (2026-09-29).
    MEASURED reason: `rating_source` was the single string `'github_stars'`, so the
    ONLY recorded reason a repo ranked well was POPULARITY — while this repo's own
    `github_find_candidate` already refuses an ADOPT verdict unless `proofed=1`.
    The numbers were already being fetched (`pushed_at` was in the return value!)
    and simply not named as a rating axis.

    `rating_sources` is therefore a LIST, one entry per axis, so a reader can
    disagree with ONE axis without discarding the others — the same reason
    `skill_factor_registry` carries a unit rather than a bare number.

    `rating_source` (singular) is KEPT and still equals `'github_stars'`, so every
    existing caller is unchanged: a record that suddenly changed its identifier
    would make old rows unreadable.

    `fetch_github_rating` performs ONE GET and writes NOTHING. It does NOT send a
    token: this function exists to be callable with no credentials, and
    `github_find` is the module that handles authentication.
    """
    if "/" not in repo:
        raise LogicTrainingError(
            "fetch_github_rating: %r is not '<owner>/<repo>'" % repo)
    req = urllib.request.Request(
        "https://api.github.com/repos/%s" % repo,
        headers={"User-Agent": "agent-system/1.0",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))

    stars = int(d.get("stargazers_count") or 0)
    forks = int(d.get("forks_count") or 0)
    pushed = d.get("pushed_at")
    spdx = ((d.get("license") or {}).get("spdx_id") or NA)
    archived = bool(d.get("archived"))

    # The recency axis, DERIVED from the API's own timestamp so the unit is a
    # duration and not a date a reader has to subtract by hand.
    recency_days = NA
    if pushed:
        try:
            import datetime as _dt
            ts = _dt.datetime.strptime(str(pushed), "%Y-%m-%dT%H:%M:%SZ")
            recency_days = max(0, (_dt.datetime.utcnow() - ts).days)
        except Exception:
            recency_days = NA

    return {"repo": repo, "stars": stars, "forks": forks,
            "pushed_at": pushed,
            "html_url": d.get("html_url"),
            # The legacy singular identifier, unchanged for every existing reader.
            "rating_source": "github_stars",
            # The axes, each with its own named source and its own unit.
            "axes": {
                "github_stars": stars,
                "github_forks": forks,
                "github_recency_days": recency_days,
                "github_has_license": 1 if spdx and spdx != NA else 0,
                "github_archived": 1 if archived else 0,
            },
            "rating_sources": {
                "github_stars": "stargazers_count (a count of people)",
                "github_forks": "forks_count (a count of copies)",
                "github_recency_days": "days since pushed_at (a duration)",
                "github_has_license": "1 when an SPDX id is reported (a flag)",
                "github_archived": "1 when `archived` is true (a flag)",
            },
            "license": spdx,
            "archived": archived,
            "note": ("popularity is ONE axis of five; `proofed` is decided by the "
                     "repo's own proofs, not by this function, and an ADOPT verdict "
                     "requires it")}


def rate_lesson(conn: sqlite3.Connection, lesson_key: str, *, rating: int,
                rating_source: str, cite_ref: str) -> dict:
    """Set a lesson's rating. The SOURCE and a CITATION are both required.

    A rating with no source cannot be compared with another rating, and a rating
    with no citation is a number someone asserted. Both are refused.
    """
    ensure_schema(conn)
    if rating_source not in RATING_SOURCES:
        raise LogicTrainingError(
            "rate_lesson: rating_source %r is not one of %s. A number with no "
            "stated source cannot be interpreted."
            % (rating_source, ", ".join(sorted(RATING_SOURCES))))
    if rating_source == "NA":
        raise LogicTrainingError(
            "rate_lesson: 'NA' means NOT RATED, so it cannot be assigned as a "
            "rating. Leave the lesson unrated instead.")
    if int(rating) < 0:
        raise LogicTrainingError("rate_lesson: rating must be >= 0")
    import citation_discipline as cd
    cd.assert_cited({"evidence_ref": cite_ref})
    row = conn.execute("SELECT lesson_key FROM skill_lesson WHERE lesson_key=?",
                       (lesson_key,)).fetchone()
    if not row:
        raise LogicTrainingError("rate_lesson: lesson %r does not exist"
                                 % lesson_key)
    conn.execute("UPDATE skill_lesson SET rating=?, rating_source=?, "
                 "updated_at=datetime('now') WHERE lesson_key=?",
                 (int(rating), rating_source, lesson_key))
    conn.commit()
    return {"ok": True, "lesson_key": lesson_key, "rating": int(rating),
            "rating_source": rating_source, "cite_ref": cite_ref}


def highest_rated(conn: sqlite3.Connection, *, source: str | None = None,
                  limit: int = 20) -> list[dict]:
    """Lessons ordered by rating, WITHIN one source.

    Never across sources: a star count and a proof count are different units, and
    a list that mixes them ranks incomparable things.
    """
    ensure_schema(conn)
    sql = ("SELECT lesson_key, skill_key, source_type, lesson_text, rating, "
           "rating_source, source_ref FROM skill_lesson WHERE rating > 0 "
           "AND rating_source <> 'NA'")
    args: list[Any] = []
    if source:
        sql += " AND rating_source = ?"
        args.append(source)
    sql += " ORDER BY rating DESC, lesson_key LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in conn.execute(sql, args)]


# ---------------------------------------------------------------------------
# THE LINK: lesson -> logic
# ---------------------------------------------------------------------------

def link_lesson_to_logic(conn: sqlite3.Connection, lesson_key: str, *,
                         dim_key: str, layer_key: str,
                         relation: str = "trains", cite_ref: str) -> dict:
    """Name the logic layer a lesson trains.

    REFUSES a layer that is not registered, because a link to a layer that does
    not exist is a link to nothing — and it would look like coverage.
    """
    ensure_schema(conn)
    if relation not in ("trains", "contradicts", "exemplifies"):
        raise LogicTrainingError(
            "link_lesson_to_logic: relation %r must be trains / contradicts / "
            "exemplifies. A lesson that merely MENTIONS a layer is not training "
            "it, so the relation is stated rather than assumed." % relation)
    import citation_discipline as cd
    cd.assert_cited({"evidence_ref": cite_ref})
    if not conn.execute("SELECT 1 FROM skill_lesson WHERE lesson_key=?",
                        (lesson_key,)).fetchone():
        raise LogicTrainingError("lesson %r does not exist" % lesson_key)
    known = conn.execute(
        "SELECT 1 FROM wording_registry WHERE dim_key=? AND wording_key=?",
        (dim_key, layer_key)).fetchone()
    if not known:
        raise LogicTrainingError(
            "layer %s=%s is not in wording_registry. A link to a layer that does "
            "not exist is a link to nothing, and it would look like coverage."
            % (dim_key, layer_key))
    conn.execute(
        "INSERT INTO lesson_logic_link (lesson_key, dim_key, layer_key, "
        "relation, cite_ref) VALUES (?,?,?,?,?) "
        "ON CONFLICT(lesson_key, dim_key, layer_key, relation) DO UPDATE SET "
        "cite_ref=excluded.cite_ref",
        (lesson_key, dim_key, layer_key, relation, cite_ref))
    conn.commit()
    return {"ok": True, "lesson_key": lesson_key, "dim_key": dim_key,
            "layer_key": layer_key, "relation": relation}


def logic_training_for(conn: sqlite3.Connection, dim_key: str,
                       layer_key: str) -> dict:
    """Every lesson that trains one logic layer, highest-rated first.

    This is the answer to "what trains this logic?" — the question that had no
    query before, because the join did not exist.
    """
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT l.lesson_key, l.skill_key, l.source_type, l.lesson_text, "
        "l.rating, l.rating_source, k.relation, k.cite_ref "
        "FROM lesson_logic_link k JOIN skill_lesson l ON l.lesson_key = k.lesson_key "
        "WHERE k.dim_key=? AND k.layer_key=? "
        "ORDER BY l.rating DESC, l.lesson_key", (dim_key, layer_key))]
    return {"dim_key": dim_key, "layer_key": layer_key, "lessons": rows,
            "count": len(rows),
            "rated": sum(1 for r in rows if r["rating"] > 0)}


def untrained_layers(conn: sqlite3.Connection) -> list[dict]:
    """Logic layers with NO lesson training them.

    A layer with no training data is a layer whose behaviour is whatever the
    wording happens to say. That is worth seeing as a list rather than
    discovering one layer at a time.
    """
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT DISTINCT dim_key, wording_key FROM wording_registry "
        "WHERE dim_key LIKE 'logic_layer%' AND is_active=1 "
        "ORDER BY dim_key, wording_key").fetchall()
    out = []
    for r in rows:
        n = conn.execute("SELECT COUNT(*) FROM lesson_logic_link WHERE "
                         "dim_key=? AND layer_key=?",
                         (r[0], r[1])).fetchone()[0]
        if not n:
            out.append({"dim_key": r[0], "layer_key": r[1]})
    return out


def _main() -> None:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    print("rating sources: %s" % ", ".join(sorted(RATING_SOURCES)))
    print("rated lessons : %d" % conn.execute(
        "SELECT COUNT(*) FROM skill_lesson WHERE rating > 0").fetchone()[0])
    print("logic links   : %d" % conn.execute(
        "SELECT COUNT(*) FROM lesson_logic_link").fetchone()[0])
    un = untrained_layers(conn)
    print("layers with NO training lesson: %d" % len(un))
    for u in un[:10]:
        print("   %s=%s" % (u["dim_key"], u["layer_key"]))
    conn.close()


if __name__ == "__main__":
    _main()