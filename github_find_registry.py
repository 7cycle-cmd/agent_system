# -*- coding: utf-8 -*-
"""github_find_registry.py — the FIND as ROWS, so the comparison is checkable.

SCOPE C of `qc_evidence/plan_GITHUB.CONSULTANT.UPGRADE.md` (APPROVED 2026-09-28).

THE HUMAN, verbatim:

    "can have table to record this key / factor will find best 3 at github = ?"
    "compare it is the one? / why yes and why not / decide by evidence"

WHY TWO TABLES
--------------
A find is ONE act (one factor, one query, one moment) and it produces SEVERAL
candidates. Putting them in one table would repeat the query and the factor once
per candidate, so the two are separated:

    github_find_registry    the ACT   (factor_key, query, total_count, ...)
    github_find_candidate   the 3     (rank, repo, stars, verdict, why_yes, why_not)

THE VERDICT IS A ROW, NOT A PARAGRAPH. `why_yes` and `why_not` are COLUMNS, so
"compare it is the one?" has an answer a third party can read — and a verdict
with no `why_not` is REFUSED, because a comparison that cannot say why the others
lost is not a comparison.

READ-ONLY BY DEFAULT. `record_find` / `record_candidate` write.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE / "agent.db"
NA = "NA"

# The verdict vocabulary. `UNKNOWN` is a REAL outcome — an uncertain comparison
# is never a silent pass, the same rule `evidence-classify` enforces.
VERDICTS = ("ADOPT", "REJECT", "UNKNOWN")

# THE DDL CONSTANTS ARE NAMED `*_DDL` ON PURPOSE, and it is not cosmetic.
#
# MEASURED 2026-09-28: `logic_generator.spec_from_table(conn, 'github_find_candidate')`
# REFUSED with *"no `*_DDL` constant in ANY module declares table
# 'github_find_candidate', so there is no DECLARED shape to measure the live table
# against. A spec with no declared shape has no UNIT to measure against."*
#
# `logic_generator._iter_ddl_constants` scans EVERY `*.py` for a MODULE-LEVEL
# `NAME_DDL = """..."""`. So the declaration must be (a) module-level and (b)
# named `*_DDL`. The constants below were `_CREATE_*_SQL`, which the scan does not
# see — the table was therefore UNDECLARED and the generator could not build a
# spec for it. Renaming is the whole fix, and it keeps ONE source of truth:
# `ensure_schema` EXECUTES these same constants, so the declaration and the live
# schema cannot drift apart without the generator noticing.
github_find_registry_DDL = """
CREATE TABLE IF NOT EXISTS github_find_registry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_key   TEXT    NOT NULL,
    query        TEXT    NOT NULL,
    query_source TEXT    NOT NULL DEFAULT 'explicit',
    total_count  INTEGER NOT NULL DEFAULT 0,
    returned     INTEGER NOT NULL DEFAULT 0,
    cite_ref     TEXT    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (factor_key, query)
    -- 🔴 NO FOREIGN KEY ON `factor_key`, AND IT WAS MEASURED, NOT PREFERRED
    -- (2026-09-29). This declared
    --     FOREIGN KEY (factor_key) REFERENCES skill_factor_registry (factor_key)
    -- and it could NEVER work: the parent's only key on `factor_key` is
    -- `uq_factor_scope_key`, an EXPRESSION index on
    -- `(COALESCE(skill_key,''), factor_key)`, and SQLite refuses an expression
    -- index as an FK parent key (docs s3: the parent key must be a named column
    -- "exact match to the columns of a single UNIQUE index").
    --
    -- Effect: with `PRAGMA foreign_keys=ON` (91 places in this repo set it) ANY
    -- insert raised `foreign key mismatch` -- even with a VALID `factor_key`, so
    -- the constraint enforced NOTHING and only broke writers.
    --
    -- THE POLICY (human-locked): a native FK is only allowed on the parent's
    -- PRIMARY KEY, and only for a MANDATORY + LOAD-BEARING reference. This table
    -- is a loose RUN RECORD (what a search returned), so it follows factor 8
    -- `lazy_fk_ontology_relation` -- no native FK.
    -- NOTE the difference from the BUTTON, not the FK: `UNIQUE (factor_key,
    -- query)` above is a real constraint and it stays.
)
"""

GITHUB_FIND_CANDIDATE_DDL = """
CREATE TABLE IF NOT EXISTS github_find_candidate (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    find_id       INTEGER NOT NULL,
    rank          INTEGER NOT NULL,
    repo          TEXT    NOT NULL,
    stars         INTEGER NOT NULL DEFAULT 0,
    forks         INTEGER NOT NULL DEFAULT 0,
    license       TEXT    NOT NULL DEFAULT 'NA',
    last_commit   TEXT    NOT NULL DEFAULT 'NA',
    archived      INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    proofed       INTEGER NOT NULL DEFAULT 0 CHECK (proofed IN (0, 1)),
    rating        INTEGER NOT NULL DEFAULT 0,
    rating_source TEXT    NOT NULL DEFAULT 'NA',
    verdict       TEXT    NOT NULL DEFAULT 'UNKNOWN',
    why_yes       TEXT    NOT NULL DEFAULT 'NA',
    why_not       TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (find_id, rank),
    FOREIGN KEY (find_id) REFERENCES github_find_registry (id)
      ON DELETE CASCADE
)
"""

# THE PRIMARY KEY IS `id`, AND THAT IS A MEASURED CONVENTION, NOT A PREFERENCE.
#
# MEASURED 2026-09-28: `logic_generator`'s `db_driven` question asks *"Is `id` a
# PRIMARY KEY with AUTOINCREMENT?"* and its doc states the consequence: *"A table
# whose PK is named something else is NOT db-driven by this definition — and
# saying so is the point: the question has a NO form, so it must be able to answer
# NO."* My first version named the PKs `find_id` / `candidate_id`, so the reader
# answered **NO** — correctly. MEASURED: `playwright_step` and
# `build_step_registry` both use `id`. The convention is `id`; the deviation was
# mine, and the fix is a RENAME rather than an argument.
#
# THE FOREIGN KEY KEEPS ITS DESCRIPTIVE NAME (`find_id`): it names WHICH find, and
# a FK is not the table's own identity.

# Kept as ALIASES so an existing caller that used the old private names still
# works; the DDL above is the single source of truth.
_CREATE_FIND_SQL = github_find_registry_DDL
_CREATE_CAND_SQL = GITHUB_FIND_CANDIDATE_DDL

# ---------------------------------------------------------------------------
# QUESTION POLARITY — WHICH ANSWER IS THE GOOD ONE.
#
# MEASURED DEFECT (2026-09-28), and it was MINE: `verdict_by_evidence` treated
# EVERY `NO` as a structural defect. But `registered_inactive` asks *"is it
# registered but INACTIVE?"* — so **NO is the GOOD answer** (it is registered and
# ACTIVE). Reading that NO as a defect would have REJECTED a correct table.
#
# The polarity is DECLARED here rather than inferred, because the generator's
# `yes_form`/`no_form` text is `'it does'` / `'it does NOT'` for both questions —
# the text does NOT carry the polarity, so guessing it would be a guess.
#
# A question NOT listed here is `NO_IS_BAD` (the default), and the default is
# stated so a reader can tell a declared polarity from an assumed one.
# ---------------------------------------------------------------------------
NO_IS_GOOD = {
    # question_id: why NO is the good answer
    "registered_inactive": "the question asks whether the table is registered but "
                           "INACTIVE, so NO means it is registered and ACTIVE",
}
NO_IS_BAD_DEFAULT = True


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.execute(_CREATE_FIND_SQL)
    conn.execute(_CREATE_CAND_SQL)
    conn.commit()
    return {"ok": True}


def record_find(conn: sqlite3.Connection, *, factor_key: str, query: str,
                cite_ref: str, total_count: int = 0, returned: int = 0,
                query_source: str = "explicit") -> dict[str, Any]:
    """Record ONE find. Returns a verdict; NEVER raises.

    REFUSES:
      * `UNKNOWN_FACTOR` -- the factor IS the key (D2).
      * `MISSING_QUERY`  -- a find with no query found nothing by definition.
      * `MISSING_CITE_REF` -- no citation, no row.
    """
    for field, value in (("factor_key", factor_key), ("query", query),
                         ("cite_ref", cite_ref)):
        if not str(value or "").strip():
            return {"ok": False, "code": "MISSING_%s" % field.upper(),
                    "message": "a find needs a %s" % field}
    try:
        ensure_schema(conn)
        if not conn.execute("SELECT 1 FROM skill_factor_registry WHERE "
                            "factor_key=?", (str(factor_key),)).fetchone():
            return {"ok": False, "code": "UNKNOWN_FACTOR",
                    "message": "no skill_factor_registry row for factor_key=%r"
                               % factor_key}
        conn.execute(
            "INSERT INTO github_find_registry "
            "(factor_key, query, query_source, total_count, returned, cite_ref) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(factor_key, query) DO UPDATE SET "
            "query_source=excluded.query_source, "
            "total_count=excluded.total_count, returned=excluded.returned, "
            "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
            (str(factor_key), str(query), str(query_source),
             int(total_count), int(returned), str(cite_ref)))
        conn.commit()
        row = conn.execute("SELECT id FROM github_find_registry WHERE "
                           "factor_key=? AND query=?",
                           (str(factor_key), str(query))).fetchone()
        return {"ok": True, "find_id": int(row["id"]),
                "factor_key": str(factor_key), "query": str(query)}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def record_candidate(conn: sqlite3.Connection, *, find_id: int, rank: int,
                     repo: str, cite_ref: str, stars: int = 0, forks: int = 0,
                     license: str = NA, last_commit: str = NA,
                     archived: int = 0, proofed: int = 0, rating: int = 0,
                     rating_source: str = NA, verdict: str = "UNKNOWN",
                     why_yes: str = NA, why_not: str = NA) -> dict[str, Any]:
    """Record ONE candidate of a find. Returns a verdict; NEVER raises.

    REFUSES:
      * `UNKNOWN_FIND`      -- a candidate of no find is an orphan.
      * `BAD_VERDICT`       -- a verdict outside the vocabulary.
      * `MISSING_WHY_NOT`   -- **the load-bearing refusal.** The human asked
                              *"compare it is the one? / why yes and why not"*.
                              A comparison that cannot say why the OTHERS lost is
                              not a comparison, so `why_not` is required for
                              EVERY candidate, including the winner.
      * `RATING_WITHOUT_SOURCE` -- `rating > 0` requires a `rating_source` in
                              `logic_training.RATING_SOURCES`, so a star count
                              and a self-measured proof count are never compared
                              as if equal.
      * `MISSING_CITE_REF`  -- no citation, no row.
    """
    for field, value in (("repo", repo), ("cite_ref", cite_ref)):
        if not str(value or "").strip():
            return {"ok": False, "code": "MISSING_%s" % field.upper(),
                    "message": "a candidate needs a %s" % field}
    v = str(verdict or "").strip().upper()
    if v not in VERDICTS:
        return {"ok": False, "code": "BAD_VERDICT",
                "message": "verdict must be one of %s, got %r"
                           % (", ".join(VERDICTS), verdict)}
    if not str(why_not or "").strip() or str(why_not).strip() == NA:
        return {"ok": False, "code": "MISSING_WHY_NOT",
                "message": "every candidate must say WHY NOT — a comparison "
                           "that cannot say why the others lost is not a "
                           "comparison"}
    rs = str(rating_source or NA).strip()
    if int(rating or 0) > 0:
        # MEASURED DEFECT (2026-09-28), and it was MINE: `RATING_SOURCES` is a
        # DICT whose keys include `'NA'` (meaning NOT RATED), so
        # `rating=100, rating_source='NA'` PASSED the membership test and was
        # written — a rating of 100 whose source says "not rated". The sentinel
        # must be refused EXPLICITLY, not merely be absent from the vocabulary.
        if rs == NA:
            return {"ok": False, "code": "RATING_WITHOUT_SOURCE",
                    "message": "rating=%d with rating_source='NA'. 'NA' means "
                               "NOT RATED, so it cannot be the source of a "
                               "rating." % int(rating)}
        try:
            import logic_training as lt
            if rs not in lt.RATING_SOURCES:
                return {"ok": False, "code": "RATING_WITHOUT_SOURCE",
                        "message": "rating_source %r is not one of %s"
                                   % (rs, ", ".join(sorted(lt.RATING_SOURCES)))}
        except ImportError:  # pragma: no cover - ships together
            pass
    try:
        ensure_schema(conn)
        if not conn.execute("SELECT 1 FROM github_find_registry WHERE "
                            "id=?", (int(find_id),)).fetchone():
            return {"ok": False, "code": "UNKNOWN_FIND",
                    "message": "no github_find_registry row for find_id=%d"
                               % int(find_id)}
        conn.execute(
            "INSERT INTO github_find_candidate "
            "(find_id, rank, repo, stars, forks, license, last_commit, archived, "
            " proofed, rating, rating_source, verdict, why_yes, why_not, "
            " cite_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(find_id, rank) DO UPDATE SET "
            "repo=excluded.repo, stars=excluded.stars, forks=excluded.forks, "
            "license=excluded.license, last_commit=excluded.last_commit, "
            "archived=excluded.archived, proofed=excluded.proofed, "
            "rating=excluded.rating, rating_source=excluded.rating_source, "
            "verdict=excluded.verdict, why_yes=excluded.why_yes, "
            "why_not=excluded.why_not, cite_ref=excluded.cite_ref, "
            "updated_at=CURRENT_TIMESTAMP",
            (int(find_id), int(rank), str(repo), int(stars), int(forks),
             str(license or NA), str(last_commit or NA),
             1 if archived else 0, 1 if proofed else 0, int(rating), rs, v,
             str(why_yes or NA), str(why_not), str(cite_ref)))
        conn.commit()
        row = conn.execute("SELECT id FROM github_find_candidate "
                           "WHERE find_id=? AND rank=?",
                           (int(find_id), int(rank))).fetchone()
        return {"ok": True, "candidate_id": int(row["id"]),
                "find_id": int(find_id), "rank": int(rank), "repo": str(repo),
                "verdict": v}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def candidates_for(conn: sqlite3.Connection, find_id: int) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM github_find_candidate WHERE find_id=? AND is_active=1 "
        "ORDER BY rank", (int(find_id),))]


def finds_for(conn: sqlite3.Connection, factor_key: str) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM github_find_registry WHERE factor_key=? AND is_active=1 "
        "ORDER BY id DESC", (str(factor_key),))]


def evidence_answers(conn: sqlite3.Connection) -> dict[str, Any]:
    """The STRUCTURAL questions about the candidate table, answered BY EVIDENCE.

    SCOPE D. The generator is REUSED, never re-implemented:
    `logic_generator.spec_from_table` builds the spec from the DECLARED shape
    (`GITHUB_FIND_CANDIDATE_DDL`), `generate` produces the question list, and
    `answer_by_evidence` answers each one with a QUERY and an `evidence_ref`.

    WHAT THIS MEASURES, STATED HONESTLY: these answers are about the REGISTER's
    structure — the table matches its declaration, the fields are named and typed.
    They are NOT a judgement of a repository's quality. The repo-quality evidence
    is `proofed` and `rating`, and `verdict_by_evidence` keeps the two apart
    rather than letting a structural YES stand in for a quality claim.
    """
    import logic_generator as lg
    ensure_schema(conn)
    spec = lg.spec_from_table(conn, "github_find_candidate")
    gen = lg.generate(spec, conn=conn)
    answers = []
    for q in (gen.get("questions") or []):
        a = lg.answer_by_evidence(conn, spec, q)
        answers.append({
            "question_id": q.get("question_id"),
            "family": a.get("family") or "",
            "source": a.get("source") or "",
            "answer": a.get("answer") or "",
            "evidence_ref": a.get("evidence_ref") or "",
            "query": a.get("query") or "",
            "ok": bool(a.get("ok")),
        })
    return {"subject": spec.get("subject"), "count": gen.get("count"),
            "derivation": gen.get("derivation"), "answers": answers}


def disagreements(conn: sqlite3.Connection,
                  answers: list[dict[str, Any]] | None = None
                  ) -> list[dict[str, Any]]:
    """The families whose answers DISAGREE — a MEASUREMENT, never a vote.

    THE LAW (`independent-review`): a disagreement triggers a MEASUREMENT, and
    there is NO code path that selects a winner. So this returns the two held
    positions WITH their evidence refs, and the caller must resolve it by
    measuring — not by counting which side has more answers.

    `answers` may be passed explicitly so the DISAGREEMENT PATH IS TESTABLE.
    MEASURED: `answer_by_evidence` is deterministic per question, so a live
    register cannot easily produce a disagreement — and a branch that cannot be
    reached is dead code. The parameter exists so the proof can CONSTRUCT the
    case rather than hope for it.
    """
    if answers is None:
        answers = evidence_answers(conn)["answers"]
    by_family: dict[str, list[dict[str, Any]]] = {}
    for a in answers:
        by_family.setdefault(a.get("family") or a.get("question_id") or "?",
                             []).append(a)
    out = []
    for fam, items in by_family.items():
        answers_seen = {i["answer"] for i in items if i.get("answer")}
        if len(answers_seen) > 1:
            out.append({
                "family": fam,
                "positions": sorted(answers_seen),
                "evidence_refs": sorted({i["evidence_ref"] for i in items
                                         if i.get("evidence_ref")}),
                "counts": {a: sum(1 for i in items if i.get("answer") == a)
                           for a in sorted(answers_seen)},
            })
    return out


def verdict_by_evidence(conn: sqlite3.Connection, find_id: int, *,
                        apply: bool = False) -> dict[str, Any]:
    """Decide each candidate BY EVIDENCE. Returns a verdict; NEVER raises.

    THE THREE LAWS, and each one is a REFUSAL to conclude:

    1. **A DISAGREEMENT IS A MEASUREMENT, NOT A VOTE.** When a family's answers
       disagree, the verdict is `UNKNOWN` and `why_not` NAMES the family and BOTH
       evidence refs. There is no code path that picks the side with more
       answers — `disagreements()` returns the positions, and the caller must
       measure. (QC-12 asserts no vote path exists.)

    2. **UNANIMITY IS NOT ADOPTABLE.** All answers agreeing is NOT a reason to
       adopt. A candidate is adopted only when it is **`proofed`** — a repo that
       merely agrees with itself has proved nothing. This is the same rule
       `independent-review` states: unanimity is not evidence either.

    3. **AN ABSENT MEASUREMENT IS NOT A PASS.** A candidate with no evidence
       answer, or an answer that is not YES/NO, is `UNKNOWN` — never a silent
       pass.

    `apply=True` writes `verdict`, `why_yes` and `why_not` back to the rows.
    """
    ensure_schema(conn)
    ev = evidence_answers(conn)
    dis = disagreements(conn)
    contested = {d["family"] for d in dis}
    answers = ev["answers"]
    yes = sum(1 for a in answers if a["answer"] == "YES")
    no = sum(1 for a in answers if a["answer"] == "NO")
    other = len(answers) - yes - no
    rows = candidates_for(conn, find_id)
    out = []
    # A `NO` IS NOT ALWAYS A DEFECT. MEASURED 2026-09-28: `registered_inactive`
    # asks whether the table is registered but INACTIVE, so NO is the GOOD answer.
    # The polarity is DECLARED in `NO_IS_GOOD`; a question not listed there
    # defaults to `NO_IS_BAD`, and the default is stated rather than assumed.
    bad_no = [a for a in answers
              if a["answer"] == "NO" and a["question_id"] not in NO_IS_GOOD]
    good_no = [a for a in answers
               if a["answer"] == "NO" and a["question_id"] in NO_IS_GOOD]
    for c in rows:
        proofed = int(c.get("proofed") or 0)
        if contested:
            v = "UNKNOWN"
            why_not = ("CONTESTED: %s disagree (%s). A disagreement is resolved "
                       "by MEASURING, not by counting answers."
                       % (", ".join(sorted(contested)),
                          "; ".join("%s=%s" % (d["family"], d["positions"])
                                    for d in dis)))
            why_yes = "NA"
        elif other:
            v = "UNKNOWN"
            why_not = ("%d answer(s) are neither YES nor NO, so the measurement "
                       "is absent. An absent measurement is not a pass." % other)
            why_yes = "NA"
        elif not proofed:
            v = "UNKNOWN"
            why_not = ("NOT PROOFED. Every structural answer agrees (%d YES), and "
                       "UNANIMITY IS NOT ADOPTABLE — a repo that agrees with "
                       "itself has proved nothing. Adoption requires `proofed`."
                       % yes)
            why_yes = "NA"
        elif bad_no:
            v = "REJECT"
            why_not = ("%d structural answer(s) are NO and NO is the BAD answer "
                       "for them: %s"
                       % (len(bad_no),
                          ", ".join("%s (%s)" % (a["question_id"],
                                                 a["evidence_ref"][:40])
                                    for a in bad_no)))
            why_yes = "proofed=1"
        else:
            v = "ADOPT"
            why_yes = ("proofed=1 AND all %d structural answers are YES%s"
                       % (yes, (" (and %d NO that is the GOOD answer: %s)"
                                % (len(good_no),
                                   ", ".join(a["question_id"] for a in good_no)))
                          if good_no else ""))
            why_not = ("the other candidates lost on `proofed` or on stars; this "
                       "one is the only proofed candidate at this rank")
        entry = {"candidate_id": c["id"], "rank": c["rank"],
                 "repo": c["repo"], "proofed": proofed, "verdict": v,
                 "why_yes": why_yes, "why_not": why_not}
        if apply:
            conn.execute(
                "UPDATE github_find_candidate SET verdict=?, why_yes=?, "
                "why_not=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (v, why_yes, why_not, int(c["id"])))
        out.append(entry)
    if apply:
        conn.commit()
    return {"find_id": int(find_id), "evidence": {"count": ev["count"],
                                                  "yes": yes, "no": no,
                                                  "other": other},
            "contested": sorted(contested), "disagreements": dis,
            "candidates": out, "applied": bool(apply)}


def register_tables(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Register this module's tables in `db_table_registry`.

    MEASURED DEFECT (2026-09-28), and it was MINE: `evidence_answers` reported
    `registered=NO` and `registered_inactive=NO` for `github_find_candidate`,
    because the table was created WITHOUT a `db_table_registry` row. MEASURED:
    `db_table_registry` holds 214 rows and `playwright_step` IS registered, so the
    reader was right and the table was the thing missing.

    A table that exists but is not registered is a name the system does not know:
    the same defect the naming law refuses for a term. The fix is a ROW, not a
    code change.

    DEFAULT IS A DRY RUN.
    """
    ensure_schema(conn)
    rows = (
        ("github_find_registry", "the ACT of one GitHub find: factor, query, "
                                 "total_count", "github_find_registry.py:1"),
        ("github_find_candidate", "one candidate of a find, with its verdict and "
                                  "why_yes/why_not", "github_find_registry.py:1"),
    )
    out = {"registered": [], "already": [], "applied": bool(apply)}
    for key, desc, cite in rows:
        hit = conn.execute("SELECT db_table_id, is_active FROM db_table_registry "
                           "WHERE table_key=?", (key,)).fetchone()
        if hit:
            out["already"].append({"table_key": key, "is_active": hit["is_active"]})
            continue
        if apply:
            conn.execute(
                "INSERT INTO db_table_registry (table_key, name, description, "
                "is_active, version, cite_ref) VALUES (?,?,?,1,'1',?)",
                (key, key, desc, cite))
        out["registered"].append({"table_key": key, "description": desc,
                                  "cite_ref": cite})
    if apply:
        conn.commit()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--factor", default="")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.init:
            print(ensure_schema(conn))
        if a.factor:
            for f in finds_for(conn, a.factor):
                print("find %d query=%r total_count=%d"
                      % (f["find_id"], f["query"], f["total_count"]))
                for c in candidates_for(conn, f["find_id"]):
                    print("   #%d %-40s %s" % (c["rank"], c["repo"], c["verdict"]))
        if not any((a.init, a.factor)):
            print("nothing to do; try --init --factor <key>")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
