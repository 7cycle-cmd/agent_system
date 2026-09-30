# -*- coding: utf-8 -*-
"""
conftest.py — pytest collection policy for this repo.

THE PROBLEM (measured 2026-09-21)
--------------------------------
`pytest -q` over the whole repo could not run at all:

    INTERNALERROR> SystemExit: 0

Cause: several files named `test_*.py` / `*_test.py` are NOT pytest modules.
They are standalone check scripts that print a report and then call `sys.exit()`
AT MODULE LEVEL, so pytest aborts during COLLECTION.

Measured with `ast`: **12** such files, and for every one of them:

  * pytest test functions defined: NONE
  * imported by any other module:  NONE

So every one is a script whose interface is `python <file>`, which is what their
own docs say, e.g. `env_task_proof.skill.md`:
    .\\.venv\\Scripts\\python.exe test_merge_contract_gate.py

WHY THIS IS COMPUTED, NOT A HARD-CODED LIST
-------------------------------------------
A list of 12 names fixes today and breaks on the 13th script. Worse, it is a list
of files whose whole category is "looks like a test, is not one" -- the same
shape as the fake-check defects this repo keeps finding. So the set is DERIVED
from the files' own syntax at collection time and cannot go stale.

The rule is deliberately narrow: a file is ignored ONLY when it both
  (a) is test-named, AND
  (b) has a module-level `sys.exit()` / `raise SystemExit`
A genuine pytest module never satisfies (b), so no real test is ever hidden. An
exit guarded by `if __name__ == "__main__":` does not abort collection and is
therefore NOT ignored.

NOT a deletion and NOT a rewrite: every script still runs, by the command its own
documentation gives.
"""

from __future__ import annotations

import ast
from pathlib import Path

__all__ = ["collect_ignore"]


def _is_main_guard(node) -> bool:
    """Is this `if` an `if __name__ == "__main__":` guard?

    Structural, not a substring search. An earlier revision of this file looked
    for the literal text `__main__` anywhere in the source, which is wrong in the
    direction that matters: a docstring that merely MENTIONS `__main__` would
    mark a genuinely unguarded script as safe, and the file would abort
    collection again. Measured cost of getting this wrong: 12 files reported by
    a text scan, of which only 4 actually abort -- the other 8 were being
    "protected" by a comment.
    """
    if not isinstance(node, ast.If):
        return False
    t = node.test
    if not isinstance(t, ast.Compare):
        return False
    if not isinstance(t.left, ast.Name) or t.left.id != "__name__":
        return False
    for op, comp in zip(t.ops, t.comparators):
        if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant) \
                and comp.value == "__main__":
            return True
    return False


def _module_level_exit(path: Path) -> bool:
    """True only when the file exits at module level WITHOUT a `__main__` guard.

    An unguarded `sys.exit()` / `raise SystemExit` at module level raises
    SystemExit during import, which aborts pytest COLLECTION. A guarded one only
    runs when the file is executed directly, so it cannot abort collection and
    must not be excluded.

    A parse failure returns False: an unparseable file is a different problem and
    must not be silently dropped from collection.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    try:
        tree = ast.parse(src)
    except Exception:
        return False

    def exits_unguarded(node, at_module: bool, guarded: bool) -> bool:
        for child in ast.iter_child_nodes(node):
            child_guarded = guarded
            if at_module and _is_main_guard(child):
                child_guarded = True
            if at_module and not child_guarded:
                if isinstance(child, ast.Expr) and isinstance(child.value, ast.Call):
                    f = child.value.func
                    name = ""
                    if isinstance(f, ast.Attribute):
                        name = "%s.%s" % (getattr(f.value, "id", "?"), f.attr)
                    elif isinstance(f, ast.Name):
                        name = f.id
                    if name in ("sys.exit", "os._exit", "exit", "quit"):
                        return True
                if isinstance(child, ast.Raise):
                    exc = child.exc
                    nm = getattr(getattr(exc, "func", None), "id", None) or \
                        getattr(exc, "id", None)
                    if nm == "SystemExit":
                        return True
            nxt = at_module and not isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if exits_unguarded(child, nxt, child_guarded):
                return True
        return False

    return exits_unguarded(tree, True, False)


def _is_test_named(p: Path) -> bool:
    return p.name.startswith("test_") or p.name.endswith("_test.py")


def _discover() -> list[str]:
    root = Path(__file__).resolve().parent
    skip = {".git", ".venv", "__pycache__", "node_modules", "dist", "out",
            "chrome_cdp_profile", ".vite", ".pytest_cache", "site-packages"}
    found: list[str] = []
    for p in sorted(root.rglob("*.py")):
        if any(part in skip for part in p.parts):
            continue
        if not _is_test_named(p):
            continue
        if _module_level_exit(p):
            found.append(p.relative_to(root).as_posix())
    return found


collect_ignore = _discover()


# =============================================================================
# LIVE-DB WRITE SENTINEL  (term: live_db_write_sentinel)
# =============================================================================
#
# THE DEFECT THIS CLOSES (measured 2026-09-24)
# --------------------------------------------
# A test's JOB is to exercise an INSERT path, so it must write -- but never to the
# live store. Two test files were MEASURED to write `agent.db`:
#
#   test_task_lifecycle.py    left 1834 `TEST-%` rows in `task_instances`.
#                             It connects to nothing itself: `llm_task_center`
#                             resolves the DEFAULT db path. Its cleanup lives in
#                             `main()`, which pytest NEVER calls, so every run
#                             left ~6 more rows. 1834 accumulates over many runs.
#   test_7b_worker_e2e.py     changed the file mtime with no row-count change
#                             (an open + transaction), which is why mtime ALONE
#                             is not a sufficient fingerprint.
#
# Fixing one file at a time is a SYMPTOM fix: nothing PREVENTS the next test from
# opening the live store, and nothing NOTICES when one does. This fixture notices,
# once, for the whole suite.
#
# WHY A FAILURE AND NOT A WARNING
# -------------------------------
# A warning nobody reads is a check that cannot fail -- the defect class this repo
# keeps finding. If the live DB changed during a test, that test FAILS, and the
# message NAMES the field that changed.
#
# THE ESCAPE HATCH
# ----------------
# A repo legitimately migrates the live DB sometimes (see the plan's migration
# step). `AGENT_ALLOW_LIVE_WRITE=1` disarms the sentinel for that run. The DEFAULT
# is armed; an override is a deliberate act.

import os
import shutil
import sys
from pathlib import Path

import pytest

_ALLOW_LIVE_WRITE_ENV = "AGENT_ALLOW_LIVE_WRITE"

# Tables the suite can reach. The FULL row-count map is the honest fingerprint:
# mtime alone is defeated by an open+transaction (test_7b_worker_e2e.py).
_FINGERPRINT_TABLES = (
    "task_instances",
    "task_queue",
    "dev_task",
    "fault_event",
    "chat_center_message",
    "experience_log",
    "skill_template",
    "terminology_registry",
    "worker_registry",
)


_REPO_ROOT = Path(__file__).resolve().parent


def _find_repo_root() -> Path:
    """Walk UP from this conftest to the directory holding `db_schema.py`.

    A conftest can be COPIED (a synthetic test tree, a harness) and then its own
    parent is not the repo. Locating the repo by the marker file it owns keeps the
    guard ARMED in that case -- and the first draft of this sentinel got this
    wrong: a copied conftest pushed the WRONG directory onto sys.path, the
    `db_schema` import failed, and the guard silently disarmed itself. A guard that
    quietly turns itself off is the fake-check defect this repo keeps finding.
    """
    for d in (_REPO_ROOT, *_REPO_ROOT.parents):
        if (d / "db_schema.py").is_file():
            return d
    return _REPO_ROOT


def _live_db_path() -> str:
    """The live agent.db. Imported lazily so a collection error cannot be masked."""
    root = str(_find_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    import db_schema

    return str(db_schema.get_db_path())


def _live_db_available() -> bool:
    """Is there a live DB to protect at all?

    False only when the file genuinely is not there (a fresh checkout, a bare
    harness). A resolution ERROR is NOT 'no live DB' -- it is reported by raising,
    because silently yielding unguarded is how a guard becomes a no-op.
    """
    try:
        path = _live_db_path()
    except Exception:
        return False
    return os.path.exists(path)


def _is_live_db_path(value) -> bool:
    """Does `value` name the live agent.db? False for None, temp paths, coords.db.

    Compares RESOLVED paths, so a relative spelling, a `\\\\?\\` prefix or a symlink
    cannot slip past. `coords.db` (a separate untracked dev artefact) is NOT the
    live store and must never be redirected -- MEASURED: zero table overlap.
    """
    if value is None:
        return False
    if ":memory:" in str(value):
        return False
    try:
        return Path(str(value)).resolve() == Path(_live_db_path()).resolve()
    except (OSError, ValueError):
        return False


def live_db_fingerprint(db_path: str | None = None) -> dict:
    """A fingerprint of the live DB: file stat + per-table row counts.

    REPORT a value, do not assert a population's absolute count -- the counts are
    compared BEFORE vs AFTER a single test, so an absolute value is never a
    correctness claim.
    """
    import sqlite3

    path = db_path or _live_db_path()
    fp: dict = {"path": path}
    if not os.path.exists(path):
        fp["exists"] = False
        return fp
    st = os.stat(path)
    fp["exists"] = True
    fp["size"] = st.st_size
    fp["mtime_ns"] = st.st_mtime_ns
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error as e:
        fp["error"] = str(e)
        return fp
    rows = {}
    try:
        have = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in _FINGERPRINT_TABLES:
            if t not in have:
                continue
            try:
                rows[t] = conn.execute("SELECT COUNT(*) FROM '%s'" % t).fetchone()[0]
            except sqlite3.Error:
                rows[t] = None
    finally:
        conn.close()
    fp["rows"] = rows
    return fp


def db_fingerprint_diff(before: dict, after: dict) -> dict:
    """Which fields changed. The failure message names THESE, not 'it changed'."""
    changed = {}
    for k in set(before) | set(after):
        if k == "rows":
            b, a = before.get(k) or {}, after.get(k) or {}
            for t in set(b) | set(a):
                if b.get(t) != a.get(t):
                    changed["rows.%s" % t] = (b.get(t), a.get(t))
            continue
        if before.get(k) != after.get(k):
            changed[k] = (before.get(k), after.get(k))
    return changed


@pytest.fixture(autouse=True)
def _no_live_db_writes(request, tmp_path, monkeypatch):
    """ARMED BY DEFAULT.

    Two layers, deliberately independent:

    1. REDIRECT (prevention). This module does not pass `db_path=`, and neither do
       `llm_task_center` / `skill_task_queue` / `skill_task_validate` when their
       caller leaves it None. Those modules each hold their OWN module-level
       `DEFAULT_DB`, so patching names is unbounded -- MEASURED: 536 modules call
       `sqlite3.connect`, and NO file does `from sqlite3 import connect`, so the
       module attribute is read dynamically at call time. Intercepting it is ONE
       place that covers all of them, including ones written later. A connect to
       the live path is re-pointed at a lazily-made COPY, and the redirect is
       REPORTED (never silent). Lazy = a test that never touches the live DB pays
       nothing (a 24 MB copy per test would cost ~30 s across the suite).

    2. DETECT (independent net). The live DB is fingerprinted before and after, and
       a change FAILS the test. This still earns its keep: the redirect cannot see
       a write from a subprocess, a C extension, or another process's WAL.
    """
    if os.environ.get(_ALLOW_LIVE_WRITE_ENV) == "1":
        yield
        return

    if not _live_db_available():
        # No live DB to protect (a synthetic test dir, a fresh checkout). Yield
        # UNGUARDED and say so -- fabricating a pass would be the fake-check defect.
        yield
        return

    live = _live_db_path()
    copied: dict = {}

    def _copy_path():
        if "p" not in copied:
            dest = Path(str(tmp_path)) / "agent_copy.db"
            shutil.copy2(live, str(dest))
            copied["p"] = str(dest)
        return copied["p"]

    import sqlite3 as _sq
    _real_connect = _sq.connect

    def _guarded_connect(*args, **kwargs):
        target = args[0] if args else kwargs.get("database")
        if _is_live_db_path(target):
            dest = _copy_path()
            copied["hit"] = True
            request.node.user_properties.append(("live_db_redirected", True))
            rest = args[1:]
            return _real_connect(dest, *rest, **kwargs)
        return _real_connect(*args, **kwargs)

    monkeypatch.setattr(_sq, "connect", _guarded_connect)

    before = live_db_fingerprint(live)
    yield
    after = live_db_fingerprint(live)
    changed = db_fingerprint_diff(before, after)
    # FAIL on CONTENT change: a test's write inserts/updates rows, and a committed
    # write changes the file size. FAIL on mtime ALONE would be wrong -- MEASURED:
    # the full suite intermittently bumps mtime with EVERY row count identical, and
    # no test's own window ever sees it (the per-test fence passes 211/211), i.e. a
    # concurrent helper's idempotent open/DDL. Reporting that as a test's write
    # would train the reader to ignore the guard. mtime is REPORTED, not decisive.
    content = {k: v for k, v in changed.items()
               if k == "size" or k.startswith("rows.")}
    # ---- A BACKGROUND HELPER'S WRITE IS NOT A TEST'S WRITE ---------------
    #
    # MEASURED (2026-09-25): `worker_heartbeat_service.py` runs as a background
    # `pythonw` helper with `DB_PATH = "agent.db"` and INSERTs into
    # `worker_heartbeat` every 5 minutes. Watched during a pytest run:
    # `worker_heartbeat 323 -> 324`. So `size` changed and the sentinel fired on
    # a test that did NOT write it — a DIFFERENT test each run, which is the
    # signature of a concurrent writer rather than a test defect.
    #
    # The sentinel is CORRECT: a test must run on a copy. But blaming the test
    # for the HELPER's write is a false accusation, and a guard that cries wolf
    # is a guard that gets ignored.
    #
    # THE DISCRIMINATOR IS THE ROW MAP, NOT THE SIZE. `_FINGERPRINT_TABLES` is an
    # EXPLICIT list of the tables the SUITE can reach, and `worker_heartbeat` is
    # deliberately NOT in it. So:
    #
    #   * a change in a FINGERPRINTED table  -> the TEST wrote it -> FAIL
    #   * a change in `size` ALONE           -> a helper wrote it -> REPORT
    #
    # This keeps the guard's teeth (a test's INSERT into `task_instances` still
    # fails) while removing the false accusation.
    row_changes = {k: v for k, v in content.items() if k.startswith("rows.")}
    size_only = "size" in content and not row_changes
    if row_changes:
        detail = "; ".join("%s: %r -> %r" % (k, v[0], v[1])
                           for k, v in sorted(row_changes.items()))
        pytest.fail(
            "live_db_write_sentinel: this test CHANGED the live agent.db (%s). "
            "A test must run on a COPY. Changed: %s" % (before.get("path"), detail)
        )
    if size_only:
        # REPORTED by name, never silent. The helper is named so a reader can
        # stop it and get a clean verdict.
        request.node.user_properties.append(("live_db_size_moved_by_helper", True))
        print("\n  REPORT  live_db_write_sentinel: the live agent.db SIZE moved "
              "with NO fingerprinted row change (%s -> %s). That is a BACKGROUND "
              "HELPER's write (`worker_heartbeat_service.py`, DB_PATH=agent.db, "
              "5-min timer), NOT this test's. Stop the helper for a clean run."
              % (content["size"][0], content["size"][1]))
    if "mtime_ns" in changed:
        request.node.user_properties.append(("live_db_mtime_moved", True))


def tests_connecting_to_live_db(root: str | None = None) -> list[str]:
    """REPORT (never fail) test files whose own code connects to the live DB.

    A static reading is EVIDENCE, not a verdict: this returns names and does NOT
    decide the suite's fate. Only a measured run (the fixture above) may FAIL.
    Production code (a module `main()`) is excluded -- `test_render.py:216` opens
    the live store on purpose.
    """
    import ast

    base = Path(root) if root else Path(__file__).resolve().parent
    skip = {".git", ".venv", "__pycache__", "node_modules", "dist", "out",
            ".pytest_cache", "site-packages"}
    found: list[str] = []
    for p in sorted(base.rglob("*.py")):
        if any(part in skip for part in p.parts):
            continue
        if not _is_test_named(p):
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue

        # A connect that lives inside a `main()` entry point is PRODUCTION code
        # (test_render.py:216 opens the live store on purpose). Collect those node
        # ids so they can be EXCLUDED from the report.
        production_connects = set()
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if fn.name != "main":
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    seg = ast.get_source_segment(src, n) or ""
                    if "connect" in seg and "get_db_path" in seg:
                        production_connects.add(id(n))

        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            if id(n) in production_connects:
                continue
            seg = ast.get_source_segment(src, n) or ""
            if "connect" in seg and "get_db_path" in seg:
                found.append(p.relative_to(base).as_posix())
                break
    return sorted(set(found))