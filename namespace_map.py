# -*- coding: utf-8 -*-
"""namespace_map.py — the STRUCTURAL facts that decide the capability model.

WHY THIS IS SEPARATE FROM THE DECISION
--------------------------------------
The user must choose how `capability` should be scoped (goal-oriented vs
subsystem-oriented vs a two-layer split). That is a SEMANTIC decision and this
module does NOT make it. Measured reason to keep them apart: I once tried to
derive capability membership from naming structure — a token-overlap rule
produced 25 cross-module routes of which 20 were FALSE POSITIVES. A heuristic
that guesses a semantic fact produces a confident wrong answer.

What this module does instead is produce the INPUT to that decision, and every
fact here is provable from the repo, so it is the same whichever option is
chosen:

  * each HTTP namespace, and how many routes it holds
  * the IMPLEMENTING FILE(S) of that namespace, with a path:line citation
  * whether that file is CITED BY a route in that namespace (the strongest
    structural link available: the file that literally serves the route)
  * namespaces that span MORE THAN ONE file (an argument against "1 file = 1
    capability")
  * namespaces with NO citable file (the risk surface for any model)
  * how many capabilities the module currently declares, so the mismatch is
    visible rather than asserted

`capability_binding.SUBJECT_KINDS` is `(route, file, module, function)` — there
is NO `namespace`. So a namespace-level capability must be declared by its FILE,
and `file` is therefore the load-bearing fact this module supplies.

Read-only. It writes no table and assigns no capability.
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

# Routes are read from the AST, not with ROUTE_RE. Measured defect (2026-09-21):
# a line regex read the string LITERAL `'@app.route("/api/case/'` inside a proof
# assertion -- a check that the route is GONE -- as a DECLARATION of /api/case.
# That made a phantom namespace with no implementing file whose "citation" was
# the assertion saying it does not exist. `ROUTE_RE`/`METHODS_RE` are kept for
# reference and for text that is not Python; `scan()` below does NOT use them.
import code_introspect as _ci

# same noise set as route_inventory: these cannot identify a namespace
_NOISE = frozenset({"api", "rest"})
_VERSION_SEG = re.compile(r"^v\d+$")

GENERIC_SEGMENTS = frozenset({
    "api", "v1", "v2", "v3", "get", "set", "list", "post", "put", "delete",
    "patch", "all", "new", "add", "update", "index", "none", "id",
})

# a route is a PAGE when it serves HTML rather than JSON (measured: this repo
# mixes both, and a page is not part of an HTTP API namespace)
_PAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".ico", ".css", ".js")
_PAGE_EXACT = frozenset({"", "settings", "step2", "step3", "step4",
                         "ide-control", "prompt-analyze"})


def log(msg: str) -> None:
    print("[namespace_map] %s" % msg, flush=True)


def namespace_of(path: str) -> str:
    """`/api/skills/<k>/draft` -> `/api/skills`. Keeps `/api/v1/*` distinct."""
    segs = [s for s in str(path or "").split("/") if s]
    if not segs:
        return "/"
    return "/" + "/".join(segs[:2])


def is_page(path: str) -> bool:
    p = str(path or "")
    if any(p.endswith(s) for s in _PAGE_SUFFIXES):
        return True
    if p.startswith("/static/"):
        return True
    return p.strip("/") in _PAGE_EXACT


def _py_files(root: Path) -> list[Path]:
    """The same file set `taxonomy_backfill.scan_routes()` scans.

    Kept identical on purpose. A map built from a NARROWER file set than the
    route pool manufactures false "no implementing file" rows — that mistake
    cost me a red proof earlier today, so the file set is matched, not guessed.
    Session scripts ARE included (they are part of the pool) and are then
    CLASSIFIED per route by `_is_session_script`, so a probe route is reported
    rather than silently dropped.
    """
    return [p for p in sorted(root.glob("*.py"))
            if not p.name.startswith("_diag") and not p.name.startswith("_smoke")]


def _is_session_script(name: str) -> bool:
    """A one-shot session/proof script, not a subsystem implementation.

    Measured reason: `/api/tasks` appeared to span THREE files
    (`_dbg_api.py`, `_proof_hardcode_scope.py`, `mouse_spot_helper.py`), which
    would have argued for a multi-file namespace. The first two are probe
    scripts that happen to declare a test route. Counting them as
    "implementation" inflates the multi-file count with test scaffolding — the
    same class of false signal that made 20 of 25 token-overlap matches wrong.

    They are NOT dropped: `session_script_routes` reports them, so the fact
    that a probe declares a route stays visible instead of disappearing.
    """
    return name.startswith("_")


def scan(root: Path | str | None = None) -> dict[str, Any]:
    """Map every namespace to its routes, its files, and its citations."""
    root = Path(root or BASE_DIR)
    ns_routes: dict[str, list[dict]] = defaultdict(list)
    for p in _py_files(root):
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        # Read ROUTES from the AST. A line regex cannot tell a decorator from a
        # string literal that mentions one, and a proof assertion mentioning a
        # route it says is DELETED must not create that route.
        try:
            routes = _ci.declared_routes(src)
        except Exception:
            routes = []
        for r in routes:
            path = r["path"]
            if not path.startswith("/"):
                continue
            # A path containing a literal ellipsis is a PLACEHOLDER inside a
            # message or a pattern (measured: `/api/...` from a log line), not a
            # declared route. Counting it created a phantom namespace with
            # "no implementing file", which would have read as a real risk.
            if "..." in path:
                continue
            ns = namespace_of(path)
            i = r["line"]
            ns_routes[ns].append(
                {"route": path, "namespace": ns, "methods": list(r["methods"]),
                 "file": p.name, "line": i, "is_page": is_page(path),
                 "is_session_script": _is_session_script(p.name),
                 "cite_ref": "%s:%d" % (p.name, i)})

    out: dict[str, Any] = {}
    for ns, routes in ns_routes.items():
        apis = [r for r in routes if not r["is_page"]]
        pages = [r for r in routes if r["is_page"]]
        # implementation files = non-session scripts only
        impl = sorted({r["file"] for r in routes if not r["is_session_script"]})
        sess = sorted({r["file"] for r in routes if r["is_session_script"]})
        cite = {r["file"]: "%s:%d" % (r["file"], r["line"]) for r in routes}
        dominant = (max(impl, key=lambda f: sum(
            1 for r in routes if r["file"] == f)) if impl else None)
        out[ns] = {
            "namespace": ns,
            "routes": len(routes),
            "api_routes": len(apis),
            "page_routes": len(pages),
            "files": impl,
            "file_count": len(impl),
            "session_script_routes": sorted({r["cite_ref"] for r in routes
                                             if r["is_session_script"]}),
            "spans_multiple_files": len(impl) > 1,
            "citations": {f: cite[f] for f in impl},
            "has_implementing_file": bool(impl),
            # the module that could OWN this namespace = the file with the most
            # routes here. A HYPOTHESIS, labelled as one, never a decision.
            "dominant_file_hypothesis": dominant,
            "dominant_citation": cite.get(dominant) if dominant else None,
            "sample_route": sorted(r["route"] for r in routes)[0] if routes else None,
        }
    return out


def summary(*, root: Path | str | None = None,
            db_path: Path | str | None = None) -> dict[str, Any]:
    """The measurements that make the capability decision checkable."""
    root = Path(root or BASE_DIR)
    ns = scan(root)
    api_ns = {k: v for k, v in ns.items() if v["api_routes"] > 0}
    multi = {k: v for k, v in api_ns.items() if v["spans_multiple_files"]}
    nofile = {k: v for k, v in api_ns.items() if not v["has_implementing_file"]}

    caps: list[str] = []
    mods: list[str] = []
    cap_per_mod: dict[str, int] = {}
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path or (BASE_DIR / "agent.db")))
        conn.row_factory = sqlite3.Row
        caps = [r["capability_key"] for r in conn.execute(
            "SELECT capability_key FROM capability_registry WHERE is_active=1")]
        mods = [r["module_key"] for r in conn.execute(
            "SELECT module_key FROM module_registry WHERE is_active=1")]
        for r in conn.execute(
                "SELECT m.module_key AS mk, COUNT(c.capability_id) AS n "
                "FROM module_registry m LEFT JOIN capability_registry c "
                "  ON c.module_id = m.module_id AND c.is_active = 1 "
                "WHERE m.is_active = 1 GROUP BY m.module_key"):
            cap_per_mod[r["mk"]] = r["n"]
        conn.close()
    except Exception as e:
        caps = ["<db unavailable: %s>" % e]

    # HOW CONCENTRATED is the HTTP surface in ONE file? This is the number that
    # decides the capability model, and it is the reason my previous proposal
    # ("capability = namespace, proved by its .py file") FAILED:
    #   28 of 30 namespaces have mouse_spot_helper.py as their dominant file,
    #   so a `file` binding would point 28 namespaces at ONE file and
    #   discriminate nothing.
    file_ns: dict[str, list[str]] = defaultdict(list)
    for k, v in api_ns.items():
        if v["dominant_file_hypothesis"]:
            file_ns[v["dominant_file_hypothesis"]].append(k)
    concentrated = {f: sorted(ns) for f, ns in file_ns.items() if len(ns) > 1}
    top = sorted(file_ns.items(), key=lambda kv: -len(kv[1]))[:3]

    return {
        "ok": True,
        "namespaces_total": len(ns),
        "namespaces_with_api_routes": len(api_ns),
        "api_routes_total": sum(v["api_routes"] for v in api_ns.values()),
        "namespaces_spanning_multiple_files": len(multi),
        "namespaces_without_implementing_file": len(nofile),
        "namespaces_also_served_by_a_session_script": sum(
            1 for v in api_ns.values() if v["session_script_routes"]),
        # THE DECIDING FACT
        "namespaces_per_dominant_file": {f: len(ns) for f, ns in top},
        "one_file_owns_n_namespaces": (len(top[0][1]) if top else None),
        "file_concentration": ("%d of %d namespaces are dominated by a single "
                               "file (%s)"
                               % (len(top[0][1]) if top else 0, len(api_ns),
                                  top[0][0] if top else "-")),
        "capabilities_declared": len(caps),
        "modules_declared": len(mods),
        "capabilities_per_module": cap_per_mod,
        "namespaces_per_capability": (
            round(len(api_ns) / len(caps), 2) if caps else None),
        # the arithmetic that shows the mismatch without judging it
        "gap_statement": (
            "%d API namespaces vs %d capabilities declared"
            % (len(api_ns), len(caps))),
        "span_multiple_files": {k: v["files"] for k, v in
                                sorted(multi.items(), key=lambda kv: -kv[1]["routes"])},
        "no_implementing_file": {k: v["routes"] for k, v in
                                 sorted(nofile.items(), key=lambda kv: -kv[1]["routes"])},
        "namespaces": {k: {
            "routes": v["routes"], "files": v["files"],
            "dominant_file_hypothesis": v["dominant_file_hypothesis"],
            "dominant_citation": v["dominant_citation"],
            "session_script_routes": v["session_script_routes"],
        } for k, v in sorted(api_ns.items(), key=lambda kv: -kv[1]["routes"])},
        "note": "FACTS only. This module does NOT assign capabilities and does "
                "NOT choose goal-oriented vs subsystem-oriented scoping.",
    }


def explain(res: dict) -> str:
    out = ["namespace map: %d namespaces (%d carrying API routes, %d routes)"
           % (res["namespaces_total"], res["namespaces_with_api_routes"],
              res["api_routes_total"])]
    out.append("  %s" % res["gap_statement"])
    out.append("  capabilities per module: %s" % res["capabilities_per_module"])
    out.append("  namespaces per capability: %s" % res["namespaces_per_capability"])
    out.append("")
    out.append("  FILE CONCENTRATION (the deciding fact): %s"
               % res["file_concentration"])
    out.append("    namespaces per dominant file: %s"
               % res["namespaces_per_dominant_file"])
    out.append("")
    out.append("  namespaces spanning MORE THAN ONE file: %d"
               % res["namespaces_spanning_multiple_files"])
    for k, v in list(res["span_multiple_files"].items())[:6]:
        out.append("    %-28s %s" % (k, v))
    out.append("")
    out.append("  namespaces with NO implementing file: %d"
               % res["namespaces_without_implementing_file"])
    for k, v in list(res["no_implementing_file"].items())[:6]:
        out.append("    %-28s %d route(s)" % (k, v))
    out.append("  namespaces ALSO served by a session/proof script: %d"
               % res["namespaces_also_served_by_a_session_script"])
    out.append("")
    out.append("  each namespace -> dominant file (a HYPOTHESIS) + its citation:")
    for k, v in list(res["namespaces"].items())[:30]:
        out.append("    %-22s %-24s %s"
                   % (k, v["dominant_file_hypothesis"] or "-",
                      (v["dominant_citation"] or "-")
                      + ("  (also %d script route(s))"
                         % len(v["session_script_routes"])
                         if v["session_script_routes"] else "")))
    return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root")
    args = ap.parse_args()
    res = summary(root=args.root)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return
    print(explain(res))


if __name__ == "__main__":
    main()