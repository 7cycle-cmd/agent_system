# -*- coding: utf-8 -*-
"""rate_limit_policy.py — the QUOTA POLICY, kept separate from the MECHANISM.

THE SHAPE IS THE STANDARD, NOT AN INVENTION
-------------------------------------------
`draft-ietf-httpapi-ratelimit-headers-11` §3.1 defines a quota policy as an ITEM
with parameters, and says a server MAY be governed by MORE THAN ONE:

    q   the quota (how many)
    qu  the quota unit (default "requests")
    w   the time window, in SECONDS
    pk  the partition key (what the quota is counted against)

This module stores exactly that, plus one field the standard implies but does not
name: `matchers`, which says WHICH requests consume this policy. Without it there
is one global counter and the strict layer protects nothing.

WHY THIS IS A SEPARATE MODULE AND NOT A LITERAL IN THE APP
---------------------------------------------------------
MEASURED 2026-09-29: `main_api.py` had 30 routes and 0 middleware, and NO number,
window or endpoint set was documented anywhere in the repo. A limit typed into a
middleware is a number with no unit and no cite — the defect
`factor_first_principle` refuses. Here the policy is DATA in `settings`, so it can
be read, cited, changed without a code change, and REPORTED.

TWO LAYERS, AND WHY ONE IS WRONG
--------------------------------
OWASP API4:2023: *"Rate limiting should be fine tuned based on the business needs.
Some API Endpoints might require stricter policies."*
MEASURED in this repo: `POST /api/worker/run/{task_id}` reaches
`worker_engine.run_task_worker` -> an Ollama HTTP call. One such request costs
thousands of times a `GET /api/coord`. A single shared counter therefore lets a
burst of cheap reads consume the expensive budget.

    expensive  q=5   w=60   the routes that reach an LLM / vision / worker
    read       q=60  w=60   everything else

`RATE_LIMIT_WINDOW_SEC = 60` in `skill_library_api` is the pre-existing window and
is kept as the floor; the per-policy `w` is authoritative.

WHY `Retry-After` IS DELAY-SECONDS AND NOT A TIMESTAMP
-----------------------------------------------------
IETF FAQ #5: delay-seconds *"aligns with Retry-After"* and does not rely on clock
synchronization, so it survives clock skew and adjustment. RFC 6585 §4 defines 429
with an `Retry-After` that MAY be included. So `retry_after_sec` below returns
**seconds to wait**, never an epoch time.

A POLICY THAT WAS NEVER DECIDED IS REPORTED, NOT GUESSED
--------------------------------------------------------
`load_policies()` returns the stored policy or the DECLARED DEFAULT, and the
default is labelled `source='declared_default'` so a reader can tell a chosen
number from a fallback. This module never invents a number to make a decision
possible, and it never replaces an UNUSABLE stored policy with a default.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

# The setting key. A JSON LIST of policy items, so a second policy is a data
# change and not a code change — and so the IETF's "more than one policy" is
# representable.
POLICY_SETTING_KEY = "rate_limit.policies"

# The DECLARED DEFAULT. Each number carries its unit and its reason; none of them
# is a bare literal.
#
# `q=5, w=60` for the expensive layer: Cloudflare's own login rule allows
# `4 requests / 1 minute` and its OTP rule `5 requests / 1 minute`, and a request
# that reaches a local 7B model costs about the same as one of those. `q=60, w=60`
# for reads: GitHub's UNAUTHENTICATED primary limit is `60 requests per hour` per
# IP, and this is a single-operator local tool whose watchdog polls every 15s, so
# 60 per MINUTE is deliberately looser than GitHub's per-HOUR.
#
# BOTH ARE OVERRIDABLE BY DATA. This is a starting point with a cite, not a truth.
DECLARED_DEFAULT: tuple[dict[str, Any], ...] = (
    {
        "policy": "expensive",
        "q": 5,
        "qu": "requests",
        "w": 60,
        "pk": "ip",
        "matchers": [
            "POST /api/worker/run/{task_id}",
        ],
        "count_on": "all",
        "why": ("reaches worker_engine.run_task_worker -> an Ollama call; "
                "Cloudflare's OTP rule is 5 requests / 1 minute"),
    },
    {
        "policy": "read",
        "q": 60,
        "qu": "requests",
        "w": 60,
        "pk": "ip",
        "matchers": ["*"],
        "count_on": "all",
        "why": ("GitHub's unauthenticated primary limit is 60 requests per HOUR "
                "per IP; this is 60 per MINUTE, chosen because the watchdog polls "
                "every 15s and a single-operator tool must not throttle itself"),
    },
)

QUOTA_UNITS = ("requests", "content-bytes", "concurrent-requests")

# The layers are checked in THIS order, strictest first, and a request that a
# strict layer governs never reaches a looser one — otherwise one expensive
# request would consume two budgets.
LAYER_ORDER = ("expensive", "read")

# `count_on` values. `all` counts every request that matched. The others exist
# because Cloudflare's credential-stuffing rule counts ONLY failed responses
# (`401`/`403`) so that legitimate users replaying a forgotten password are not
# throttled — and its REST rule counts only `403`/`404` as a bot signal. A limiter
# that cannot express that will either throttle real users or miss the attack.
COUNT_ON = ("all", "failed", "client_error")

# The status codes each `count_on` means, derived from HTTP semantics rather than
# restated per policy.
COUNT_ON_STATUS = {
    "failed": lambda s: s >= 400,
    "client_error": lambda s: 400 <= s < 500,
}


class PolicyError(Exception):
    """A policy that cannot be used. Raised, never silently defaulted."""


def _default_db_path() -> str:
    """The default database, taken from the module that owns it where possible."""
    try:
        import skill_library_api as api
        return str(getattr(api, "AGENT_DB_PATH", "agent.db"))
    except Exception:
        return "agent.db"


def _connect(db_path: Any = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or _default_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def validate_policy(p: dict[str, Any]) -> list[str]:
    """Every reason a policy item is unusable. Empty list means usable.

    A policy that cannot be validated is REFUSED rather than defaulted, because a
    defaulted quota is a quota nobody chose.
    """
    errs: list[str] = []
    name = str(p.get("policy") or "").strip()
    if not name:
        errs.append("needs a `policy` name (the IETF policy identifier)")
    # `q` MUST be a non-negative integer (IETF §3.1.1).
    q = p.get("q")
    if not isinstance(q, int) or isinstance(q, bool) or q < 0:
        errs.append("`q` must be a non-negative INTEGER (IETF 3.1.1), got %r" % (q,))
    # `qu` MUST be one of the registered units (IETF §3.1.2).
    qu = str(p.get("qu") or "requests")
    if qu not in QUOTA_UNITS:
        errs.append("`qu` must be one of %s (IETF 3.1.2), got %r"
                    % (list(QUOTA_UNITS), qu))
    # `w` MUST be a non-negative, NON-ZERO integer of seconds (IETF §3.1.3).
    w = p.get("w")
    if not isinstance(w, int) or isinstance(w, bool) or w <= 0:
        errs.append("`w` must be a non-zero INTEGER of seconds (IETF 3.1.3), got %r"
                    % (w,))
    if not str(p.get("pk") or "").strip():
        errs.append("needs a `pk` partition key — the quota has to be counted "
                    "against something (IETF 3.1.4)")
    mats = p.get("matchers")
    if not isinstance(mats, list) or not mats:
        errs.append("needs `matchers` — without it a policy has no scope and "
                    "would apply to every request")
    co = str(p.get("count_on") or "all")
    if co not in COUNT_ON:
        errs.append("`count_on` must be one of %s, got %r" % (list(COUNT_ON), co))
    return errs


def load_policies(db_path: Any = None,
                  conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Read the stored policies, or the DECLARED DEFAULT.

    Returns `{ok, source, policies, invalid, cite}`. `source` is one of:
      * `'settings'`         — a policy was stored and is valid
      * `'declared_default'` — nothing stored; the module's own default is used
      * `'invalid'`          — something stored and it is UNUSABLE (refused)
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        raw = None
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (POLICY_SETTING_KEY,)).fetchone()
            raw = row[0] if row else None
        except sqlite3.Error:
            raw = None
        if not raw:
            return {"ok": True, "source": "declared_default",
                    "policies": [dict(p) for p in DECLARED_DEFAULT],
                    "invalid": [],
                    "cite": ("rate_limit_policy.py:DECLARED_DEFAULT "
                             "(no `settings.%s` row)" % POLICY_SETTING_KEY)}
        try:
            items = json.loads(raw)
        except (TypeError, ValueError) as e:
            return {"ok": False, "source": "invalid", "policies": [],
                    "invalid": [{"policy": "?",
                                 "errors": ["stored value is not JSON: %s" % e]}],
                    "cite": "settings.%s" % POLICY_SETTING_KEY}
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list) or not items:
            return {"ok": False, "source": "invalid", "policies": [],
                    "invalid": [{"policy": "?",
                                 "errors": ["stored value is not a non-empty LIST "
                                            "of policy items (IETF 3.1 allows more "
                                            "than one)"]}],
                    "cite": "settings.%s" % POLICY_SETTING_KEY}
        invalid = []
        good = []
        for it in items:
            if not isinstance(it, dict):
                invalid.append({"policy": "?",
                                "errors": ["item is not an object"]})
                continue
            errs = validate_policy(it)
            if errs:
                invalid.append({"policy": it.get("policy", "?"), "errors": errs})
            else:
                good.append(dict(it))
        if invalid:
            # A stored policy that does not validate is REFUSED, not replaced by a
            # default: silently substituting a number for a chosen one is how a
            # limit stops meaning anything.
            return {"ok": False, "source": "invalid", "policies": good,
                    "invalid": invalid,
                    "cite": "settings.%s" % POLICY_SETTING_KEY}
        return {"ok": True, "source": "settings", "policies": good, "invalid": [],
                "cite": "settings.%s" % POLICY_SETTING_KEY}
    finally:
        if own:
            conn.close()


def rate_limit_max_window(db_path: Any = None,
                          conn: sqlite3.Connection | None = None) -> int:
    """The longest window any usable policy declares.

    `prune_rate_limit` uses this as its horizon. MEASURED reason: a pruner that
    used only the 60s constant would DELETE rows a longer-window policy still
    needs, and would therefore silently silence the stricter layer.
    """
    got = load_policies(db_path=db_path, conn=conn)
    wins = [int(p["w"]) for p in got["policies"] if isinstance(p.get("w"), int)]
    return max(wins) if wins else 0


def _match_route(route: str, path: str) -> bool:
    """Exact match, or a `{param}` segment matching exactly ONE path segment."""
    if route == path:
        return True
    if "{" not in route:
        return False
    r_seg = route.strip("/").split("/")
    p_seg = path.strip("/").split("/")
    if len(r_seg) != len(p_seg):
        return False
    return all((rs.startswith("{") and rs.endswith("}")) or rs == ps
               for rs, ps in zip(r_seg, p_seg))


def match_policy(policy: dict[str, Any], method: str, path: str) -> bool:
    """Does `policy` apply to this request?

    A matcher is `"METHOD /path"`, `"* /path"`, or `"*"`. The match is EXACT or a
    `{param}` segment — the route's DECLARED shape, so the matcher is written the
    way the route is written.

    MEASURED WARNING this implements (Cloudflare): *"A rule targeting
    `/validate/otp` will not match requests to `/api/otp/validate`."* There is no
    fuzzy fallback here on purpose: a matcher that guesses is a matcher that
    silently protects the wrong route.
    """
    m = str(method or "").upper()
    p = str(path or "")
    for raw in policy.get("matchers") or []:
        s = str(raw).strip()
        if s == "*":
            return True
        parts = s.split(" ", 1)
        if len(parts) != 2:
            continue
        verb, route = parts[0].upper(), parts[1].strip()
        if verb not in ("*", m):
            continue
        if _match_route(route, p):
            return True
    return False


def counts(policy: dict[str, Any], status_code: int | None) -> bool:
    """Does a request that matched consume quota?

    `all`          every match counts.
    `failed`       only HTTP >= 400 counts (Cloudflare's bot rule counts 403/404).
    `client_error` only 4xx counts (Cloudflare's credential-stuffing rule counts
                   401/403 so a legitimate user replaying a password is not
                   throttled).
    """
    co = str(policy.get("count_on") or "all")
    if co == "all":
        return True
    if status_code is None:
        # The response has not happened yet. A `count_on` policy cannot be
        # evaluated before the fact, and counting anyway would throttle the very
        # users the setting exists to protect. So it does not count.
        return False
    fn = COUNT_ON_STATUS.get(co)
    return bool(fn and fn(int(status_code)))


def decide_policy(method: str, path: str,
                  db_path: Any = None,
                  conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Which layer governs this request? Strictest first.

    Returns `{ok, policy, reason, cite}` or `{ok: False, ...}`. It picks the FIRST
    policy in `LAYER_ORDER` whose matcher applies, so an expensive route is
    governed by the strict layer alone.
    """
    got = load_policies(db_path=db_path, conn=conn)
    if not got["ok"]:
        return {"ok": False, "policy": None, "invalid": got["invalid"],
                "reason": "the stored policy is UNUSABLE and is not replaced by a "
                          "default: a defaulted quota is a quota nobody chose",
                "cite": got["cite"]}
    by_name = {p["policy"]: p for p in got["policies"]}
    for name in LAYER_ORDER:
        p = by_name.get(name)
        if p and match_policy(p, method, path):
            return {"ok": True, "policy": p,
                    "reason": "matched layer %r" % name,
                    "source": got["source"], "cite": got["cite"]}
    # Nothing matched. REPORT it rather than silently applying something: a policy
    # set that does not cover this route is a GAP in the policy, and a gap that is
    # filled by a default is a gap nobody can see.
    return {"ok": True, "policy": None,
            "reason": "no policy matches this route — a GAP in the policy set",
            "unmatched": True, "source": got["source"], "cite": got["cite"]}


def retry_after_sec(hits: int, q: int, w: int, oldest_ts: float,
                    now: float | None = None) -> int:
    """Seconds until the OLDEST hit in the window falls out of it.

    The sliding window frees capacity when the oldest counted hit expires, so that
    — and not "the window length" — is the honest wait. Returned as
    DELAY-SECONDS, never a timestamp (IETF FAQ #5: delay-seconds aligns with
    Retry-After and does not rely on clock synchronization).

    Always at least 1 when over quota: a `Retry-After: 0` invites an immediate
    retry, which is the thundering herd the IETF warns about in §8.5.
    """
    if hits <= q:
        return 0
    t = time.time() if now is None else now
    return max(1, int(round((oldest_ts + w) - t)))


def _oldest_hit(ip: str, policy: str, w: int,
                db_path: Any = None) -> float:
    """The oldest hit still inside the window — what `Retry-After` is derived from."""
    conn = _connect(db_path)
    try:
        cutoff = time.time() - w
        row = conn.execute(
            "SELECT MIN(hit_ts) FROM rate_limit "
            "WHERE ip = ? AND policy = ? AND hit_ts > ?",
            (str(ip), str(policy), cutoff)).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return time.time()


def decide(method: str, path: str, ip: str,
           db_path: Any = None,
           status_code: int | None = None) -> dict[str, Any]:
    """One rate-limit decision, and the RECORD of the hit when it counts.

    Returns `{ok, allowed, http_status, retry_after_sec, policy, hits, q, w,
    counted, reason, cite}`.

    `ok=False` means the policy itself is broken (see `load_policies`), which is a
    FAULT and must not be reported as "allowed" or as "limited": a limiter that
    cannot read its own policy cannot claim to be limiting.

    The RECORD happens here, not in the middleware, so that "count it" and "decide
    on it" cannot drift apart. A hit is recorded only when `counts()` says this
    request consumes quota.
    """
    sel = decide_policy(method, path, db_path=db_path)
    if not sel["ok"]:
        return {"ok": False, "allowed": None, "http_status": None,
                "retry_after_sec": None, "policy": None, "hits": None,
                "counted": False, "invalid": sel.get("invalid"),
                "reason": sel["reason"], "cite": sel["cite"]}
    p = sel["policy"]
    if p is None:
        return {"ok": True, "allowed": True, "http_status": None,
                "retry_after_sec": 0, "policy": None, "hits": 0,
                "counted": False, "reason": sel["reason"], "cite": sel["cite"],
                "unmatched": True}

    import skill_library_api as api
    q, w, name = int(p["q"]), int(p["w"]), str(p["policy"])

    counted = counts(p, status_code)
    if counted:
        api.record_rate_limit_hit(ip, policy=name, window_sec=w)

    hits = api.rate_limit_hits_in_window(ip, policy=name, window_sec=w)
    allowed = hits <= q
    out: dict[str, Any] = {
        "ok": True, "allowed": allowed, "policy": name, "hits": hits,
        "q": q, "w": w, "qu": p.get("qu", "requests"), "pk": p.get("pk", "ip"),
        "counted": counted, "cite": sel["cite"],
        "reason": "%d/%d within %ds" % (hits, q, w),
    }
    if not allowed:
        oldest = _oldest_hit(ip, name, w, db_path)
        # 429 Too Many Requests (RFC 6585 §4) with Retry-After in seconds.
        out["http_status"] = 429
        out["retry_after_sec"] = retry_after_sec(hits, q, w, oldest)
        out["reason"] = ("%d/%d within %ds — Retry-After %ds"
                         % (hits, q, w, out["retry_after_sec"]))
    else:
        out["http_status"] = None
        out["retry_after_sec"] = 0
    return out


def measure(db_path: Any = None) -> dict[str, Any]:
    """Report the policy set and its provenance. READ-ONLY."""
    got = load_policies(db_path=db_path)
    return {
        "ok": got["ok"],
        "source": got["source"],
        "cite": got["cite"],
        "layers": [p["policy"] for p in got["policies"]],
        "policies": got["policies"],
        "invalid": got["invalid"],
        "max_window_sec": rate_limit_max_window(db_path=db_path),
        "layer_order": list(LAYER_ORDER),
        "count_on_allowed": list(COUNT_ON),
        "quota_units_allowed": list(QUOTA_UNITS),
        "note": ("a number here is DATA with a cite; the module never invents one "
                 "to make a decision possible"),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--decide", default="", help="METHOD /path")
    ap.add_argument("--ip", default="127.0.0.1")
    ap.add_argument("--measure", action="store_true")
    args = ap.parse_args(argv)
    db = args.db or None
    if args.decide:
        parts = args.decide.split(" ", 1)
        verb, path = (parts + ["/"])[:2]
        res = decide(verb, path, args.ip, db_path=db)
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return 0
    res = measure(db_path=db)
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())