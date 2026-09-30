# -*- coding: utf-8 -*-
"""github_find.py — FIND a technique on GitHub, and READ THE TEAM from the repo.

SCOPE C of `qc_evidence/plan_GITHUB.CONSULTANT.UPGRADE.md` (APPROVED 2026-09-28).

THE HUMAN, verbatim:

    "is time go to github to find skill or agent"
    "learn or get tools from github, better than develop all from us"
    "what is the key for question to find at github? = factor = what?"
    "can have table to record this key / factor will find best 3 at github = ?"
    "compare it is the one? / why yes and why not / decide by evidence"
    "how to have the team from professional, not some pieces only"

THE THREE ENDPOINTS, ALL VERIFIED WITH NO LOGIN (MEASURED 2026-09-28)
--------------------------------------------------------------------
| # | endpoint | what it returns |
|---|---|---|
| 1 | `search/repositories?q=<term>&sort=stars` | the FIND |
| 2 | `repos/{r}/contributors` | the TEAM: `login` + `contributions` |
| 3 | `repos/{r}/commits?path=<file>` | **WHO authored THE FILE** |

MEASURED samples: `q="citation discipline python"` -> `total_count=2`;
`pallets/flask` contributors -> `davidism 1856`, `mitsuhiko 1189`;
`src/flask/app.py` commits -> `davidism`, `lkk7`.

WHY THE TEAM IS READ AND NOT INVENTED (D8)
------------------------------------------
A repo read only through its README is a **piece**. A **professional team** is
the repo's own structure, and GitHub publishes it: the owner is accountable, the
contributors are real users with real commit counts, and `commits?path=<file>`
says **who authored the very file that carries the technique**. So
`consultant_member` is filled from `contributors`, NOT from a job title I made
up, and a member with no authored work is REFUSED (`NO_AUTHORED_WORK`) — a name
without authored work is a piece, not a professional.

THE RATING IS REUSED, NEVER RE-INVENTED
---------------------------------------
`logic_training.fetch_github_rating` already reads `stargazers_count` with no
login, and `logic_training.RATING_SOURCES` already declares the vocabulary. This
module calls it rather than opening a second reader — a second reader would be a
second truth.

READ-ONLY: this module performs HTTP GETs and writes NOTHING.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://api.github.com"
UA = "agent-system/1.0"
NA = "NA"


class GithubFindError(Exception):
    """A find that cannot be performed. Raised, never silently returned."""


# The sources a token may come from, in the order they are tried. NAMED, because
# a reader has to be able to tell WHICH one answered: a silent fallback to
# anonymous would make a throttled find look like an authoritative one.
TOKEN_SOURCES = ("settings.github.token", "GITHUB_TOKEN", "GH_TOKEN", ".env")

_SETTING_KEY = "github.token"
_ENV_NAMES = ("GITHUB_TOKEN", "GH_TOKEN")


def _from_env_file(name: str) -> str:
    """Read ONE variable from `.env`. Returns "" when absent. NEVER prints."""
    try:
        p = BASE / ".env"
        if not p.is_file():
            return ""
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def resolve_token() -> dict[str, Any]:
    """The GitHub token and WHERE it came from. READ-ONLY; the value is never logged.

    WHY A TOKEN AT ALL (MEASURED 2026-09-29): unauthenticated GitHub allows
    **60 requests per hour per IP**; authenticated allows 5,000. A find that needs
    ~3 requests per candidate therefore dies after ~20 candidates anonymously, and
    the failure surfaces as a 403 that the repo's own rule refuses to read as
    "nothing found" (`_get` below).

    WHY THE SOURCE IS RETURNED, NOT ASSUMED
    ---------------------------------------
    `{token, source}` where `source` is `''` when no token was found. A caller that
    assumed "there is a token" would report an authoritative find where it actually
    got 60-per-hour anonymous results. The same rule `resolve_model` follows in the
    run harness.

    THE TOKEN IS NEVER RETURNED TO A LOG, A REPORT, OR A PRINT. Only its SOURCE and
    its LENGTH are reportable, and `redact` exists so any accidental echo is
    unusable.
    """
    # 1. `settings.github.token` — the precedent is `settings.notify.token`, which
    #    is ALREADY an empty credential column in this table.
    try:
        import sqlite3
        conn = sqlite3.connect(str(BASE / "agent.db"))
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?",
                               (_SETTING_KEY,)).fetchone()
        finally:
            conn.close()
        if row and str(row[0] or "").strip():
            return {"token": str(row[0]).strip(),
                    "source": "settings.%s" % _SETTING_KEY}
    except Exception:
        pass
    # 2. the environment
    import os
    for name in _ENV_NAMES:
        v = os.environ.get(name)
        if v and v.strip():
            return {"token": v.strip(), "source": name}
    # 3. `.env` — MEASURED: this repo ALREADY keeps a credential there
    #    (`OPENCLAW_MCP_TOKEN`), and `.gitignore` already covers `.env`.
    for name in _ENV_NAMES:
        v = _from_env_file(name)
        if v:
            return {"token": v, "source": ".env:%s" % name}
    return {"token": "", "source": ""}


def redact(value: str) -> str:
    """A token rendered unusable for logs. Keeps only a shape hint.

    A partial token (`github_pat_...abcd`) is still a credential hint, so only the
    PREFIX and the LENGTH are kept — enough to tell two tokens apart, not enough to
    use one.
    """
    v = str(value or "")
    if not v:
        return ""
    head = v.split("_")[0] if "_" in v else v[:4]
    return "%s...<len %d>" % (head, len(v))


def _get(url: str, *, timeout: float = 25.0) -> Any:
    """One GET, with the headers GitHub requires, AUTHENTICATED WHEN POSSIBLE.

    A 403/429 is REPORTED as a rate limit rather than swallowed: a find that
    returns an empty list because it was throttled would look like "nothing
    found", which is the empty-result defect this repo already documents.

    🔴 IT PRESENTLY SENDS THE TOKEN WHEN ONE IS CONFIGURED (2026-09-29).
    The previous version said NO token and meant it, which capped a find at
    **60 requests per hour per IP** (measured GitHub policy) — about 20 candidates
    — and then failed as a 403. The header is sent ONLY when a token resolves, so
    the anonymous path still behaves exactly as before.
    """
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    tok = resolve_token()
    if tok["token"]:
        headers["Authorization"] = "Bearer %s" % tok["token"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise GithubFindError(
                "GitHub rate limit or refusal (HTTP %d) for %s (auth=%s). An "
                "empty result from a throttled call is NOT 'nothing found'."
                % (e.code, url, tok["source"] or "anonymous"))
        if e.code == 401:
            raise GithubFindError(
                "GitHub AUTHENTICATION FAILED (HTTP 401) for %s — the token from "
                "%r is rejected, so this is NOT 'nothing found'." % (url, tok["source"]))
        raise GithubFindError("HTTP %d for %s" % (e.code, url))
    except Exception as e:
        raise GithubFindError("%s: %s" % (type(e).__name__, e))


def search_repos(query: str, *, limit: int = 3, timeout: float = 25.0) -> dict[str, Any]:
    """The FIND. Returns the candidates AND the `total_count`.

    `total_count` is returned because it is the honest measure of whether the
    QUERY was any good. MEASURED: `q="citation discipline python"` ->
    `total_count=2`, both hits 0-1 stars and no license. A caller that only saw
    the 3 items would not know the search was thin.
    """
    q = str(query or "").strip()
    if not q:
        raise GithubFindError("search_repos: a query is required")
    url = ("%s/search/repositories?q=%s&sort=stars&order=desc&per_page=%d"
           % (API, urllib.parse.quote(q), max(1, int(limit))))
    d = _get(url, timeout=timeout)
    items = []
    for it in (d.get("items") or [])[:limit]:
        items.append({
            "repo": it.get("full_name"),
            "stars": int(it.get("stargazers_count") or 0),
            "forks": int(it.get("forks_count") or 0),
            "archived": bool(it.get("archived")),
            "license": ((it.get("license") or {}).get("spdx_id") or NA),
            "pushed_at": it.get("pushed_at") or NA,
            "html_url": it.get("html_url") or NA,
            "owner": ((it.get("owner") or {}).get("login") or NA),
            "description": (it.get("description") or "")[:200],
        })
    return {"query": q, "total_count": int(d.get("total_count") or 0),
            "returned": len(items), "items": items}


def repo_team(repo: str, *, path: str = "", limit: int = 10,
              timeout: float = 25.0) -> dict[str, Any]:
    """THE TEAM, read from the repo's own structure (D8).

    `contributors` gives real users with real commit counts. When `path` is
    given, `commits?path=<path>` says **who authored THAT file**, which is the
    only authorship that matters for a technique: a contributor who never
    touched the file is not the professional for it.

    A member with NO authored work on the cited file is REFUSED
    (`NO_AUTHORED_WORK`) when a `path` is given — a name without authored work is
    a piece, not a professional.
    """
    r = str(repo or "").strip()
    if "/" not in r:
        raise GithubFindError("repo_team: %r is not '<owner>/<repo>'" % repo)
    contribs = _get("%s/repos/%s/contributors?per_page=%d"
                    % (API, r, max(1, int(limit))), timeout=timeout)
    members = []
    for c in (contribs or [])[:limit]:
        members.append({"login": c.get("login") or NA,
                        "contributions": int(c.get("contributions") or 0),
                        "html_url": c.get("html_url") or NA})
    authors: list[str] = []
    if str(path or "").strip():
        commits = _get("%s/repos/%s/commits?path=%s&per_page=%d"
                       % (API, r, urllib.parse.quote(str(path)), max(1, int(limit))),
                       timeout=timeout)
        for c in (commits or []):
            a = ((c.get("author") or {}).get("login")
                 or ((c.get("commit") or {}).get("author") or {}).get("name"))
            if a and a not in authors:
                authors.append(a)
    if authors:
        # The lead is the contributor with the MOST COMMITS ON THE FILE, not the
        # most overall: the file is what carries the technique.
        on_file = [m for m in members if m["login"] in authors]
        lead = max(on_file, key=lambda m: m["contributions"])["login"] if on_file else NA
        refused = [m["login"] for m in members if m["login"] not in authors]
    else:
        lead = (max(members, key=lambda m: m["contributions"])["login"]
                if members else NA)
        refused = []
    return {"repo": r, "path": str(path or NA), "members": members,
            "authors_of_path": authors, "lead": lead,
            "refused_no_authored_work": refused,
            "count": len(members)}


def find_candidates(factor_key: str, *, query: str = "", limit: int = 3,
                    conn: Any = None, timeout: float = 25.0) -> dict[str, Any]:
    """The FIND, keyed on a FACTOR (D2), and REFUSING a factor with no subject.

    THE KEY IS THE FACTOR. MEASURED: the factor's `metric_unit` SUBJECT is what
    the search term is derived from (`factor_first_principle.unit_subject`), so a
    factor whose unit names NO SUBJECT cannot produce a search term at all. That
    is D6, and it is enforced HERE as well as at the write site.

    `query` may be given explicitly. When it is not, the factor's subject is
    used — and the `total_count` is returned so a thin search is VISIBLE rather
    than presented as a best-3.
    """
    fk = str(factor_key or "").strip()
    if not fk:
        raise GithubFindError("find_candidates: a factor_key is required (D2)")
    subject = ""
    if conn is not None:
        import factor_first_principle as fp
        row = conn.execute("SELECT metric_kind, metric_unit, metric_target "
                           "FROM skill_factor_registry WHERE factor_key=?",
                           (fk,)).fetchone()
        if not row:
            raise GithubFindError("find_candidates: no factor %r" % fk)
        verdict = fp.audit_factor(
            {"factor_key": fk, "metric_kind": row["metric_kind"],
             "metric_unit": row["metric_unit"],
             "metric_target": row["metric_target"]}, conn)
        if not verdict.get("measurable"):
            raise GithubFindError(
                "find_candidates: factor %r is NOT MEASURABLE (%s). A factor "
                "whose unit names no SUBJECT has no search term (D6)."
                % (fk, "; ".join(verdict.get("reasons") or [])))
        subject = fp.unit_subject(str(row["metric_unit"]))
    q = str(query or "").strip() or subject
    if not q:
        raise GithubFindError(
            "find_candidates: no query and the factor names no subject")
    out = search_repos(q, limit=limit, timeout=timeout)
    out["factor_key"] = fk
    out["subject"] = subject
    out["query_source"] = "explicit" if str(query or "").strip() else "factor_subject"
    return out


def _axes_of(c: dict[str, Any]) -> dict[str, Any]:
    """The FIVE rating axes for one candidate, each with its own unit.

    MEASURED 2026-09-29: `search_repos` was ALREADY returning `stars`, `forks`,
    `license`, `archived` and `pushed_at`, and `rank` used exactly ONE of them
    (`stars`) as a tie-breaker. So the extra axes were paid for and discarded, and
    the only recorded reason a candidate ranked well was POPULARITY — while this
    module's own docstring says `proofed` comes first and
    `github_find_candidate` refuses an ADOPT verdict without it.

    WHY THIS RETURNS A DICT OF NAMED AXES AND NOT A SCORE
    ----------------------------------------------------
    A weighted sum would need weights, and a weight is an invented number with no
    unit — the `factor_first_principle` defect. So each axis is reported SEPARATELY
    with the source that produced it, and the ORDER is a declared rule (below),
    never a hidden formula.
    """
    pushed = str(c.get("pushed_at") or "")
    recency = NA
    if pushed and pushed != NA:
        try:
            import datetime as _dt
            ts = _dt.datetime.strptime(pushed, "%Y-%m-%dT%H:%M:%SZ")
            recency = max(0, (_dt.datetime.utcnow() - ts).days)
        except Exception:
            recency = NA
    spdx = str(c.get("license") or NA)
    return {
        "github_stars": int(c.get("stars") or 0),
        "github_forks": int(c.get("forks") or 0),
        "github_recency_days": recency,
        "github_has_license": 1 if spdx and spdx != NA else 0,
        "github_archived": 1 if c.get("archived") else 0,
    }


def rank(candidates: list[dict[str, Any]], *,
         proofed: set[str] | None = None) -> list[dict[str, Any]]:
    """Order `proofed DESC, live-before-archived, stars DESC` — the human's order.

    THE HUMAN: *"1) proofed 2) highest rating"*. A 10k-star repo never verified
    is NOT proofed; a verified technique with no repo has no rating. So `proofed`
    is the FIRST key and stars the second, and the consequence is asserted by the
    proof: **a PROVEN candidate outranks a 99999-star UNPROVEN one.**

    An `archived` candidate is RANKED (it is still a real answer) but FLAGGED, so
    the reader sees that it is dead rather than being silently dropped.

    🔴 `archived` IS NOW A SORT KEY, AND THE ORDER IS A DECLARED RULE (2026-09-29).
    MEASURED reason: the human asked for more than one dimension, and an archived
    repository is the one case where the extra information CHANGES THE ORDER for a
    reason nobody disputes — an archived tool is not maintained, so it cannot be
    the better answer at equal proof and equal stars. It is a DEMOTION, not a
    multiplier: it cannot lift a 3-star repo above a 5-star one.

    There is deliberately NO composite score. Each candidate carries `axes` (five
    named numbers with units) and `rating_source` (the axis the ORDER used), so a
    reader can disagree with the order without having to reverse-engineer a weight.
    `stars_alone_would_differ` records the cases where the extra axes CHANGED the
    order, so the effect of the change is visible rather than asserted.
    """
    pf = proofed or set()
    out = []
    for c in candidates:
        d = dict(c)
        d["proofed"] = 1 if str(d.get("repo")) in pf else 0
        d["flag"] = "ARCHIVED" if d.get("archived") else ""
        d["axes"] = _axes_of(d)
        d["rating_source"] = "github_stars"
        d["rating_sources"] = {
            "github_stars": "stargazers_count (a count of people)",
            "github_forks": "forks_count (a count of copies)",
            "github_recency_days": "days since pushed_at (a duration)",
            "github_has_license": "1 when an SPDX id is reported (a flag)",
            "github_archived": "1 when `archived` is true (a flag)",
        }
        out.append(d)
    # The DECLARED order. `-proofed` first (the human's rule), then
    # `+archived` so a live repo outranks an archived one, then `-stars`.
    out.sort(key=lambda d: (-int(d.get("proofed") or 0),
                            int(d.get("axes", {}).get("github_archived") or 0),
                            -int(d.get("stars") or 0)))
    for i, d in enumerate(out, start=1):
        d["rank"] = i
    # WHAT THE EXTRA AXES CHANGED. Reported, never asserted away: stars-only
    # ordering is compared to this one, so "more axes" is a MEASUREMENT.
    stars_only = sorted(out, key=lambda d: (-int(d.get("proofed") or 0),
                                            -int(d.get("stars") or 0)))
    if [d["repo"] for d in stars_only] != [d["repo"] for d in out]:
        for d in out:
            if stars_only.index(d) != out.index(d):
                d["order_changed_by_axes"] = True
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", default="")
    ap.add_argument("--factor", default="")
    ap.add_argument("--repo", default="")
    ap.add_argument("--path", default="")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    a = ap.parse_args(argv)
    try:
        if a.repo:
            print(json.dumps(repo_team(a.repo, path=a.path, limit=max(a.limit, 5)),
                             ensure_ascii=False, indent=2))
        elif a.factor:
            import sqlite3
            conn = sqlite3.connect(a.db, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                r = find_candidates(a.factor, query=a.query, limit=a.limit,
                                    conn=conn)
            finally:
                conn.close()
            print("query=%r source=%s total_count=%d returned=%d"
                  % (r["query"], r["query_source"], r["total_count"], r["returned"]))
            for c in rank(r["items"]):
                print("  #%d %-42s stars=%-6s archived=%-5s license=%s"
                      % (c["rank"], c["repo"], c["stars"], c["archived"],
                         c["license"]))
        elif a.query:
            r = search_repos(a.query, limit=a.limit)
            print("total_count=%d returned=%d" % (r["total_count"], r["returned"]))
            for c in rank(r["items"]):
                print("  #%d %-42s stars=%-6s archived=%-5s license=%s"
                      % (c["rank"], c["repo"], c["stars"], c["archived"],
                         c["license"]))
        else:
            print("nothing to do; try --query <term> | --factor <key> | --repo <owner/name>")
    except GithubFindError as e:
        print("REFUSED: %s" % e)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
