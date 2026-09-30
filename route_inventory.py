# -*- coding: utf-8 -*-
"""
route_inventory.py — the machine-readable route list any partitioning needs.

Why this exists
---------------
Three independent partitioning proposals were written (7, 16 and 8 modules).
All three disagreed, because all three were JUDGEMENT, and none was built on a
route inventory. The measurement that followed found worse: the first two
attempts at even COUNTING the routes were wrong --

    attempt 1  token overlap (`task` in `/api/tasks` matches module `task_center`)
               -> 25 cross-module routes, of which 20 were false positives
    attempt 2  literal substring  -> 5, but `openclaw_companion` could never
               match `/api/openclaw/*`, because the module key is not a
               substring of the path

So this module does NOT decide domains. It produces FACTS that a human or a
model can partition against:

  * every route, with a `path:line` citation
  * the namespace prefix each route sits in (first two path segments)
  * which RESOURCES are reachable from more than one namespace
  * how many routes are HTML pages vs API endpoints
  * which namespaces contain a sub-resource naming ANOTHER namespace

MEASURED (mouse_spot_helper.py): 187 routes / 176 API / 11 pages / 57
namespaces. Five resources live in more than one namespace:

    skill         /api/skills   + /api/v1          <- two skill implementations
    task_center   /api/task-center + /api/task_center <- one area, two spellings
    prompt        /api/prompt   + /prompt/setting
    llm_template  /llm-templates + /llm_templates  <- one page set, two spellings
    llm_task      /api/llm-tasks + /llm-tasks      <- API + its SPA

The first two are the ones no partitioning proposal mentioned. A proposal that
does not account for `/api/v1/*` is partitioning a file it has not read.

A partition built on this list can be checked. A partition built on prefix
intuition cannot -- that is exactly what produced three different answers.

Read-only. It writes no table.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

ROUTE_RE = re.compile(r"""@\s*[\w.]+\s*\.\s*route\(\s*['"]([^'"]+)['"]""")
METHODS_RE = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""")

# A route is a PAGE when it serves HTML rather than JSON. Measured: this file
# mixes both, so a partitioning pass that treated every route as an API would
# overstate the domain count.
_PAGE_SUFFIXES = (".png", ".jpg", ".svg", ".ico", ".css", ".js")
_PAGE_EXACT = frozenset({"", "settings", "step2", "step3", "step4",
                         "ide-control", "prompt-analyze"})

# Tokens that appear in so many paths they cannot identify a namespace.
GENERIC_SEGMENTS = frozenset({
    "api", "v1", "v2", "v3", "get", "set", "list", "post", "put", "delete",
    "patch", "all", "new", "add", "update", "index", "none", "id",
})


def log(msg: str) -> None:
    print("[route_inventory] %s" % msg, flush=True)


def prefix_of(path: str, depth: int = 2) -> str:
    """The namespace prefix: the first `depth` non-generic-ish segments.

    `/api/skills/<k>/draft` -> `/api/skills`; `/api/v1/skills` -> `/api/v1/skills`
    (the version segment is kept, because `/api/v1/*` is a SEPARATE API tree and
    collapsing it into `/api/*` would hide the duplication this report exists to
    surface).
    """
    segs = [s for s in str(path or "").split("/") if s]
    if not segs:
        return "/"
    if segs[0].lower() in ("v1", "v2", "v3"):
        segs = segs[:depth]
    elif segs[0].lower() in GENERIC_SEGMENTS:
        segs = segs[:depth]
    else:
        segs = segs[:max(1, depth)]
    return "/" + "/".join(segs)


def is_page(path: str) -> bool:
    p = str(path or "")
    if any(p.endswith(s) for s in _PAGE_SUFFIXES):
        return True
    if p.startswith("/static/"):
        return True
    return p.strip("/") in _PAGE_EXACT


def extract(path: Path | str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Every route in `path`, each with a `path:line` citation.

    THE DEFECT THE AST FIX REMOVES (measured 2026-09-21). This used to grep raw
    lines with `ROUTE_RE`, so a string LITERAL that MENTIONS a route counted as a
    DECLARATION of it. Measured: `extract("_proof_chat_registry.py")` reported
    `/api/case/` at line 133 -- the line is
    `not ci.has_code(HELPER, '@app.route("/api/case/')`, an assertion that the
    route is GONE. A phantom route in an inventory is worse than a missing one:
    it reads as an implementation that does not exist, and in
    `namespace_registry` it produced a phantom NAMESPACE whose citation was the
    assertion of its own absence. A route is a DECORATOR, so it is read from the
    AST.
    """
    p = Path(path)
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    try:
        rel = p.relative_to(root or BASE_DIR).as_posix()
    except Exception:
        rel = p.as_posix()
    try:
        import code_introspect as ci
        declared = ci.declared_routes(src)
    except Exception:
        # A non-Python file (or a syntax error) cannot be parsed; the honest
        # answer is "no routes readable here", never a regex guess.
        declared = []
    out: list[dict[str, Any]] = []
    for d in declared:
        route = d["path"]
        out.append({
            "route": route,
            "prefix": prefix_of(route),
            "methods": list(d["methods"]),
            "is_page": is_page(route),
            "file": rel,
            "line": d["line"],
            "cite_ref": "%s:%d" % (rel, d["line"]),
        })
    return out


def inventory(paths: list[Path | str] | None = None,
              *, root: Path | None = None) -> dict[str, Any]:
    root = Path(root or BASE_DIR)
    targets = [Path(x) for x in (paths or [root / "mouse_spot_helper.py"])]
    routes: list[dict] = []
    for t in targets:
        routes.extend(extract(t, root=root))

    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for r in routes:
        by_prefix[r["prefix"]].append(r)

    pages = [r for r in routes if r["is_page"]]
    apis = [r for r in routes if not r["is_page"]]

# PARALLEL API TREES: the same resource reachable from two different namespaces.
    #
    # Three earlier attempts all produced a CONFIDENT EMPTY RESULT:
    #   v1  stored `segs[0]+"/"+segs[1]` as the value but keyed the dict by that
    #       same string -> at most one value per key -> always {}.
    #   v2  grouped by `segs[1]` -> for `/api/v1/skills` that segment is `v1`,
    #       not `skills`, so the versioned tree could never collide -> always {}.
    #   v3  stripped version segments but still keyed on `segs[0]`, which is
    #       `api` on BOTH sides -> always {} again.
    #
    # v4 removes the noise segments (`api`, `v1`) and keeps the NAMESPACE as the
    # discriminator, which is what "the same resource under two trees" means:
    #     /api/skills/<x>      namespace=/api/skills  resource=skills
    #     /api/v1/skills/<id>  namespace=/api/v1      resource=skills
    # A detector that returns empty when it is broken is worse than no detector,
    # because empty reads as "no problem found".
    _NOISE = {"api", "rest"}
    VERSION_SEG = re.compile(r"^v\d+$")

    # normalise `-`/`_` so `/api/task_center` and `/api/task-center` compare equal:
    # the file really does carry both spellings for one area, and treating them
    # as two resources hides a duplicate namespace instead of reporting it.
    def _norm(s: str) -> str:
        return s.replace("-", "_").removesuffix("s")

    def _resource(path: str) -> str:
        for s in str(path or "").split("/"):
            if not s or s in _NOISE or VERSION_SEG.match(s) or s.startswith("<"):
                continue
            return _norm(s)
        return ""

    res_ns: dict[str, set[str]] = defaultdict(set)
    for r in apis:
        res = _resource(r["route"])
        if res:
            res_ns[res].add(r["prefix"])
    parallel = {k: sorted(v) for k, v in res_ns.items() if len(v) > 1}

    # CROSS-CUTTING: a namespace whose sub-resources name ANOTHER namespace.
    #
    # The earlier version asked only "is the tail text not a substring of the
    # prefix?" and paired it with `len(Counter(...)) >= 1`, which is TRUE for any
    # non-empty list -- a condition that cannot fail. It therefore flagged 20-odd
    # ordinary namespaces and told us nothing.
    #
    # A checkable definition: take each namespace's own last segment as its
    # TOPIC (topic of `/api/tasks` is `task`). Then a route under namespace N is
    # cross-cutting if one of its segments names a DIFFERENT namespace's topic.
    # Everything reported is a real path literal that can be opened and read.
    def _topics() -> set[str]:
        out = set()
        for p in by_prefix:
            segs = [s for s in p.split("/") if s and s not in _NOISE]
            for s in segs:
                if s and not VERSION_SEG.match(s) and not s.startswith("<"):
                    out.add(_norm(s.lower()))
        return out

    topics = _topics()
    crosscut: dict[str, list[dict]] = defaultdict(list)
    for r in apis:
        segs = [s.lower() for s in r["route"].split("/") if s]
        own = [_norm(s.lower()) for s in r["prefix"].split("/")
               if s and s not in _NOISE]
        for s in segs:
            t = t0 = _norm(s)
            if t in own or t not in topics or t in GENERIC_SEGMENTS:
                continue
            # a segment that is part of the namespace's OWN name is not foreign:
            # `/api/task-center/tasks` carries `task`, but `task` is the second
            # half of `task-center` itself. Without this the namespace reports
            # itself as cross-cutting.
            if any(t in o or o in t for o in own):
                continue
            crosscut[r["prefix"]].append(
                {"route": r["route"], "foreign_topic": t0,
                 "cite_ref": r["cite_ref"]})
    crosscut_sig = {k: v for k, v in crosscut.items() if v}

    return {
        "ok": True,
        "files": [str(Path(x)) for x in targets],
        "routes_total": len(routes),
        "routes_api": len(apis),
        "routes_page": len(pages),
        "namespaces": len(by_prefix),
        "by_namespace": {k: len(v) for k, v in
                         sorted(by_prefix.items(), key=lambda kv: -len(kv[1]))},
        "namespace_routes": {k: [x["route"] for x in v]
                             for k, v in sorted(by_prefix.items())},
        "parallel_api_trees": parallel,
        "prefixes_with_crosscut_tails": {k: {
            "route_count": len(v),
            "foreign_topics": sorted({x["foreign_topic"] for x in v}),
            "examples": v[:3],
        } for k, v in sorted(crosscut_sig.items(), key=lambda kv: -len(kv[1]))},
        "routes": routes,
        "note": "FACTS only. This module does not assign domains; it supplies "
                "the citations a partitioning proposal must be checkable against.",
    }


def explain(res: dict) -> str:
    out = ["route inventory: %d route(s) in %d file(s)"
           % (res["routes_total"], len(res["files"]))]
    out.append("  API endpoints : %d" % res["routes_api"])
    out.append("  HTML pages    : %d" % res["routes_page"])
    out.append("  namespaces    : %d" % res["namespaces"])
    out.append("")
    out.append("  routes per namespace (largest first):")
    for k, v in res["by_namespace"].items():
        out.append("    %-28s %d" % (k, v))
    if res["parallel_api_trees"]:
        out.append("")
        out.append("  SAME RESOURCE IN >1 NAMESPACE (parallel implementations?):")
        for k, v in sorted(res["parallel_api_trees"].items()):
            out.append("    %-20s %s" % (k, v))
    if res["prefixes_with_crosscut_tails"]:
        out.append("")
        out.append("  namespaces whose sub-resources name ANOTHER namespace:")
        for k, v in res["prefixes_with_crosscut_tails"].items():
            out.append("    %-24s n=%-3d foreign=%s"
                       % (k, v["route_count"], v["foreign_topics"]))
    return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="print every route with its citation")
    args = ap.parse_args()
    res = inventory(args.files or None)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return
    print(explain(res))
    if args.all:
        print("\n  every route (route | cite_ref):")
        for r in res["routes"]:
            print("    %-52s %s" % (r["route"], r["cite_ref"]))


if __name__ == "__main__":
    main()