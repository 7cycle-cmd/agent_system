# -*- coding: utf-8 -*-
"""f_env_preflight.py — confirm ALL preparation before a task, or refuse to start.

WHY THIS EXISTS
---------------
The user's requirement: "confirm all preparation 100% before task".

A preflight that only PRINTS is not a gate — the same defect `systematic_debug.py`
documents ("a rule in a table is not a gate"). So this has two modes:

  --check   ADVISORY (default). Reports every gap, exits 0. Safe to run anytime.
  --gate    BLOCKING. Exits non-zero until every REQUIRED check passes.

WHY TWO MODES
-------------
A hard gate is right before task dispatch and acceptance, but WRONG mid-debugging:
it would refuse to let you work on the very thing it reports as broken. So the
blocking behaviour is opt-in, and the default is honest reporting.

WHAT IT CHECKS (aggregated, not reinvented)
-------------------------------------------
Every check below already exists as a real function elsewhere. This file
AGGREGATES them; it does not re-implement them. Re-implementing would create a
second copy of each rule, and the two copies would drift — the failure mode
`skill_tdd_runner._probe_sdb` warns about.

  gpu / cuda        f_comfy_check.check_gpu + a real torch matmul
  ffmpeg            f_comfy_check.check_ffmpeg
  comfyui server    f_comfy_check.check_port_free (post-install semantics)
  comfyui model     f_video_produce.checkpoint_available
  contracts         skill_tdd_runner.run_contract for every active contract
  evidence root     no test noise (EVID-tdd_* / EVID-gate_*) in production
  cdp browser       cdp_common.cdp_ready + a real connect_over_cdp
  incident log      incident_detector.detect_all

Usage:
  python f_env_preflight.py                 # advisory report
  python f_env_preflight.py --gate          # blocking; exit 1 if not ready
  python f_env_preflight.py --json
  python f_env_preflight.py --only gpu,ffmpeg

Exit codes: 0 ready (or advisory), 1 NOT ready under --gate, 2 bad args.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Environment REQUIREMENTS, defined in ONE module so the report, the CLI and the
# target-capture route cannot drift apart (two copies of a rule is how a check
# stays green while the thing it checks is wrong).
import env_requirements as _envreq  # noqa: E402

check_dpi_pinned = _envreq.check_dpi_pinned
check_screen_space = _envreq.check_screen_space
check_source_window = _envreq.check_source_window

EXIT_OK = 0
EXIT_NOT_READY = 1
EXIT_BAD_ARGS = 2

# Checks that MUST pass for --gate to allow a task. Everything else is advisory:
# a missing optional tool should not block work, but a missing GPU should.
REQUIRED = {
    "gpu", "cuda", "ffmpeg", "comfyui_server", "comfyui_model",
    "contracts", "evidence_root",
    # LLM/queue checks (added 2026-09-20). A task cannot run without a reachable
    # helper, a readable queue, a reachable ollama and the ASSIGNED model present.
    # Screen + foreground are required too: env_task_proof Rule 3 — a same-size
    # image is not a same-content image, so an unproven foreground is exactly the
    # false-verdict shape.
    "helper", "queue_api", "queue_db", "ollama", "model_present",
    "screen", "foreground",
    # Environment REQUIREMENTS that must be MEASURED, not assumed (added
    # 2026-09-21). Each corresponds to a rule that was previously written down
    # and never enforced: DPI awareness arrived by accident from `import
    # pyautogui`; capture sources returned two coordinate spaces; a session's
    # source was never read when choosing the capture window.
    "dpi_pinned", "screen_space", "source_window",
}

# The model a queued task is assigned to when the caller names none.
DEFAULT_MODEL = "qwen2.5:7b-instruct"
HELPER_URL = "http://127.0.0.1:18765"
OLLAMA_URL = "http://127.0.0.1:11434"

# ---------------------------------------------------------------------------
# SCOPES — which checks a given KIND of task actually needs.
#
# WHY (measured 2026-09-20): the gate refused an LLM classify task because
# `ffmpeg` was missing. ffmpeg is a VIDEO dependency; it has nothing to do with
# an LLM task. That is a FALSE REFUSAL — the mirror image of a false pass, and
# just as damaging: it trains the reader to bypass the gate.
#
# So a task declares its scope, and only that scope's checks can block it.
# ---------------------------------------------------------------------------
SCOPES = {
    # An LLM task needs a reachable helper, a readable queue, a reachable model
    # server with the ASSIGNED model present, and a proven screen/foreground
    # (env_task_proof Rule 3). It does NOT need ffmpeg, CUDA or ComfyUI.
    "llm": {
        "helper", "queue_api", "queue_db", "ollama", "model_present",
        "screen", "foreground", "contracts", "evidence_root",
    },
    # A video task needs the GPU stack plus the LLM/queue plumbing.
    "video": {
        "gpu", "cuda", "ffmpeg", "comfyui_server", "comfyui_model",
        "contracts", "evidence_root", "helper", "queue_api", "queue_db",
        "ollama", "model_present", "screen", "foreground",
    },
    # A UI/vision task needs the screen + foreground + evidence root.
    "ui": {
        "screen", "foreground", "evidence_root", "helper", "cdp_browser",
        "dpi_pinned", "screen_space",
    },
    # The target-capture wizard: every step writes evidence, so the environment
    # must be proven BEFORE step 1. "desktop_pins" is deliberately NOT here —
    # a pin is an operator preference, and the route takes it explicitly
    # (`require_pins`) so the choice is visible rather than implied.
    "target_capture": {
        "helper", "evidence_root", "screen", "foreground",
        "dpi_pinned", "screen_space", "source_window",
    },
}
SCOPES["all"] = set(REQUIRED)


def _ok(name: str, detail: str, **extra) -> dict:
    return {"name": name, "ok": True, "detail": detail, **extra}


def _bad(name: str, detail: str, **extra) -> dict:
    return {"name": name, "ok": False, "detail": detail, **extra}


# ---------------------------------------------------------------------------
# individual checks — each returns a dict and NEVER raises
# ---------------------------------------------------------------------------

def check_gpu() -> dict:
    try:
        import f_comfy_check as fc
        r = fc.check_gpu()
        return _ok("gpu", r.get("detail", ""), raw=r) if r.get("ok") \
            else _bad("gpu", r.get("detail", ""), raw=r)
    except Exception as e:
        return _bad("gpu", "%s: %s" % (type(e).__name__, e))


def check_cuda() -> dict:
    """A real GPU op, not just is_available() — detection is not capability."""
    try:
        import subprocess
        py = r"C:\projects\ComfyUI\.venv\Scripts\python.exe"
        if not Path(py).is_file():
            return _bad("cuda", "ComfyUI venv python missing")
        code = ("import torch;a=torch.randn(256,256,device='cuda');"
                "b=torch.randn(256,256,device='cuda');"
                "print('OK',torch.__version__,float((a@b).sum()))")
        # CREATE_NO_WINDOW: the preflight is polled every 5s by the queue page,
        # so without this each poll spawns a console window (measured
        # 2026-09-20: a window popped up every 5 seconds, forever).
        p = subprocess.run(
            [py, "-c", code], capture_output=True, text=True, timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if p.returncode == 0 and "OK" in p.stdout:
            return _ok("cuda", p.stdout.strip()[:90])
        return _bad("cuda", (p.stdout + p.stderr).strip()[:160])
    except Exception as e:
        return _bad("cuda", "%s: %s" % (type(e).__name__, e))


def check_ffmpeg() -> dict:
    try:
        import f_comfy_check as fc
        r = fc.check_ffmpeg()
        return _ok("ffmpeg", r.get("detail", "")) if r.get("ok") \
            else _bad("ffmpeg", r.get("detail", ""))
    except Exception as e:
        return _bad("ffmpeg", "%s: %s" % (type(e).__name__, e))


def check_comfyui_server() -> dict:
    """Post-install semantics: a listening server is the DESIRED state."""
    try:
        import f_comfy_check as fc
        fc._EXPECT_LISTENING["value"] = True
        r = fc.check_port_free()
        fc._EXPECT_LISTENING["value"] = False
        return _ok("comfyui_server", r.get("detail", "")) if r.get("ok") \
            else _bad("comfyui_server", r.get("detail", ""))
    except Exception as e:
        return _bad("comfyui_server", "%s: %s" % (type(e).__name__, e))


def check_comfyui_model() -> dict:
    try:
        import f_video_produce as fv
        ok, detail = fv.checkpoint_available()
        return _ok("comfyui_model", detail) if ok else _bad("comfyui_model", detail)
    except Exception as e:
        return _bad("comfyui_model", "%s: %s" % (type(e).__name__, e))


def check_contracts() -> dict:
    """Every active contract's TDD cases must pass. This is the real gate."""
    try:
        import skill_contract_store as scs
        import skill_tdd_runner as runner

        contracts = scs.list_contracts()
        bad: list[str] = []
        total_cases = 0
        for c in contracts:
            cid = c.get("contract_id")
            if not cid:
                continue
            r = runner.run_contract(cid, record=False, verbose=False)
            n, tot = r.get("n_passed", 0), r.get("n_cases", 0)
            total_cases += tot
            if tot == 0 or n != tot:
                bad.append("%s %d/%d" % (cid, n, tot))
        if bad:
            return _bad("contracts", "%d contract(s) not green: %s"
                        % (len(bad), "; ".join(bad[:4])),
                        contracts=len(contracts), cases=total_cases)
        return _ok("contracts", "%d contracts, %d TDD cases all green"
                   % (len(contracts), total_cases),
                   contracts=len(contracts), cases=total_cases)
    except Exception as e:
        return _bad("contracts", "%s: %s" % (type(e).__name__, e))


def check_evidence_root() -> dict:
    """No test noise in the production evidence tree."""
    try:
        import evidence_store as es
        root = Path(es.EVIDENCE_ROOT)
        if not root.is_dir():
            return _bad("evidence_root", "evidence root missing: %s" % root)
        noise = [p.name for p in root.iterdir()
                 if p.is_dir() and p.name.startswith(("EVID-tdd_", "EVID-gate_"))]
        if noise:
            return _bad("evidence_root",
                        "%d test-noise folder(s): %s"
                        % (len(noise), ", ".join(noise[:3])), noise=noise)
        n = sum(1 for p in root.iterdir() if p.is_dir())
        return _ok("evidence_root", "%d folders, no test noise" % n, folders=n)
    except Exception as e:
        return _bad("evidence_root", "%s: %s" % (type(e).__name__, e))


def check_cdp_browser() -> dict:
    """The WORKING browser path: repo CDP on 9222 + a real connect_over_cdp.

    NOTE: the VS Code browser tool (connectOverCDP to VS Code's own endpoint)
    times out and its target is internal to VS Code, so it is NOT used here.
    This checks the path that actually works, so the result is actionable.
    """
    try:
        import cdp_common
        if not cdp_common.cdp_ready():
            return _bad("cdp_browser", "CDP port 9222 not answering")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(cdp_common.CDP, timeout=10000)
            pages = sum(len(c.pages) for c in b.contexts)
            b.close()
        return _ok("cdp_browser", "CDP 9222 + connect_over_cdp OK (%d pages)" % pages)
    except Exception as e:
        return _bad("cdp_browser", "%s: %s" % (type(e).__name__, str(e)[:120]))


def check_incidents() -> dict:
    """Report open incidents. Advisory: an incident does not block a task."""
    try:
        import incident_detector as det

        r = det.detect_all()
        if not r.get("ok"):
            return _bad("incidents", r.get("error", "detect_all failed"))
        n = r.get("count", 0)
        if n:
            return _bad("incidents", "%d task(s) require a postmortem" % n,
                        incidents=[i.get("task_id") for i in r.get("incidents", [])])
        return _ok("incidents", "%d tasks scanned, 0 incidents" % r.get("scanned", 0))
    except Exception as e:
        return _bad("incidents", "%s: %s" % (type(e).__name__, e))


# ---------------------------------------------------------------------------
# LLM / queue checks (added 2026-09-20)
#
# WHY: the gate confirmed the ComfyUI/GPU side but said nothing about whether an
# LLM task could actually run. A task enqueued into a dead helper or a missing
# model fails later, in another process, and the failure gets blamed on the
# model. These checks close that hole. Each MEASURES; none assumes.
# ---------------------------------------------------------------------------

def _http_json(url: str, timeout: float = 6.0):
    """GET a JSON endpoint. Returns (payload, error). Never raises."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def check_helper() -> dict:
    """The helper process must answer. Without it nothing can be enqueued."""
    payload, err = _http_json(HELPER_URL + "/api/health")
    if payload and err is None:
        return _ok("helper", "GET /api/health OK", raw=payload)
    return _bad("helper", "GET /api/health -> %s" % (err or "no payload"))


def check_queue_api() -> dict:
    """The queue endpoint the UI reads must answer with ok=True."""
    payload, err = _http_json(HELPER_URL + "/api/task_center/queue?limit=1")
    if payload and err is None and payload.get("ok") is True:
        ov = payload.get("overview") or {}
        return _ok("queue_api", "queue API OK, total=%s" % ov.get("total"),
                   overview=ov)
    return _bad("queue_api", "GET /api/task_center/queue -> %s"
                % (err or "ok!=True"))


def check_queue_db() -> dict:
    """The queue table must be readable. Measured, not assumed."""
    try:
        import skill_task_queue as stq
        ov = stq.overview()
        if not ov.get("ok"):
            return _bad("queue_db", "overview() not ok")
        return _ok("queue_db", "skill_task_queue total=%s" % ov.get("total"),
                   overview=ov)
    except Exception as e:
        return _bad("queue_db", "%s: %s" % (type(e).__name__, e))


def check_ollama() -> dict:
    """The local model server must be reachable and list at least one model."""
    payload, err = _http_json(OLLAMA_URL + "/api/tags")
    names = []
    if isinstance(payload, dict):
        names = [str(m.get("name") or "") for m in (payload.get("models") or [])]
    if names:
        return _ok("ollama", "%d model(s): %s" % (len(names), ", ".join(names)),
                   models=names)
    return _bad("ollama", "GET /api/tags -> %s" % (err or "no models"))


def check_model_present() -> dict:
    """The ASSIGNED model must be in the tag list — not just 'some model'.

    A gate that only checks 'ollama is up' passes while the assigned model is
    absent, and every task then fails at call time.
    """
    payload, err = _http_json(OLLAMA_URL + "/api/tags")
    names = []
    if isinstance(payload, dict):
        names = [str(m.get("name") or "") for m in (payload.get("models") or [])]
    if DEFAULT_MODEL in names:
        return _ok("model_present", "assigned model %r present" % DEFAULT_MODEL,
                   want=DEFAULT_MODEL, available=names)
    return _bad("model_present", "assigned model %r NOT in %s"
                % (DEFAULT_MODEL, names or (err or "[]")),
                want=DEFAULT_MODEL, available=names)


def check_screen() -> dict:
    """Real primary screen size in real pixels.

    SetProcessDPIAware() must come first or a DPI-scaled display reports the
    virtual (scaled) size and every stored coordinate is in the wrong space.
    """
    try:
        import ctypes
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        w = int(u.GetSystemMetrics(0))
        h = int(u.GetSystemMetrics(1))
        if w > 0 and h > 0:
            return _ok("screen", "screen=%dx%d" % (w, h), width=w, height=h)
        return _bad("screen", "GetSystemMetrics returned %sx%s" % (w, h))
    except Exception as e:
        return _bad("screen", "%s: %s" % (type(e).__name__, e))


def check_foreground() -> dict:
    """Which window is IN FRONT, with its process name.

    env_task_proof Rule 3: size and hash are file properties; they cannot say
    what was photographed. The foreground process is the field that catches a
    capture of the wrong window, so it is measured here too.
    """
    try:
        import ctypes
        import ctypes.wintypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return _bad("foreground", "GetForegroundWindow returned 0")
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        title = buf.value
        proc = "unknown"
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            pid = ctypes.wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if h:
                try:
                    size = ctypes.wintypes.DWORD(1024)
                    pbuf = ctypes.create_unicode_buffer(1024)
                    if k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                        proc = Path(pbuf.value).name
                finally:
                    k32.CloseHandle(h)
        except Exception:
            pass
        if proc in ("", "unknown"):
            return _bad("foreground", "foreground process unknown (title=%r)"
                        % title[:60])
        return _ok("foreground", "process=%s title=%r" % (proc, title[:60]),
                   process=proc, title=title, hwnd=int(hwnd))
    except Exception as e:
        return _bad("foreground", "%s: %s" % (type(e).__name__, e))


CHECKS = (
    ("gpu", check_gpu),
    ("cuda", check_cuda),
    ("ffmpeg", check_ffmpeg),
    ("comfyui_server", check_comfyui_server),
    ("comfyui_model", check_comfyui_model),
    ("contracts", check_contracts),
    ("evidence_root", check_evidence_root),
    ("cdp_browser", check_cdp_browser),
    ("incidents", check_incidents),
    ("helper", check_helper),
    ("queue_api", check_queue_api),
    ("queue_db", check_queue_db),
    ("ollama", check_ollama),
    ("model_present", check_model_present),
    ("screen", check_screen),
    ("foreground", check_foreground),
    # Environment REQUIREMENTS (2026-09-21). These READ the machine: the DPI
    # awareness is pinned rather than inherited, both capture sources must be in
    # ONE space, and a session's source must resolve to a window. Each returns ok
    # false so the caller REFUSES — the whole point is that a rule which is only
    # written down does not run.
    ("dpi_pinned", check_dpi_pinned),
    ("screen_space", check_screen_space),
    ("source_window", check_source_window),
)


def run_all(only: set[str] | None = None,
            scope: str | None = None) -> dict:
    """Run the checks. `scope` selects which checks may BLOCK.

    `only` restricts which checks RUN. `scope` restricts which of the run
    checks are REQUIRED — so an LLM task is not blocked by a video dependency.
    """
    if scope:
        if scope not in SCOPES:
            return {
                "ready": False,
                "checks": [],
                "failed": [],
                "blocking": ["unknown_scope"],
                "advisory": [],
                "required": [],
                "passed": 0,
                "total": 0,
                "scope": scope,
                "error": "unknown scope %r (known: %s)"
                         % (scope, ", ".join(sorted(SCOPES))),
            }
        required = set(SCOPES[scope])
    else:
        required = set(REQUIRED)

    results = []
    for name, fn in CHECKS:
        if only and name not in only:
            continue
        try:
            results.append(fn())
        except Exception as e:
            results.append(_bad(name, "check raised %s: %s" % (type(e).__name__, e)))

    failed = [r for r in results if not r["ok"]]
    blocking = [r for r in failed if r["name"] in required]
    advisory = [r for r in failed if r["name"] not in required]

    return {
        "ready": not blocking,
        "checks": results,
        "failed": [r["name"] for r in failed],
        "blocking": [r["name"] for r in blocking],
        "advisory": [r["name"] for r in advisory],
        "required": sorted(required),
        "passed": sum(1 for r in results if r["ok"]),
        "total": len(results),
        "scope": scope or "all",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Environment preflight")
    ap.add_argument("--gate", action="store_true",
                    help="BLOCKING: exit 1 unless every required check passes")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="",
                    help="comma-separated check names")
    ap.add_argument("--scope", default="",
                    help="which checks may BLOCK: %s" % ", ".join(sorted(SCOPES)))
    args = ap.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()} or None
    if only:
        unknown = only - {n for n, _ in CHECKS}
        if unknown:
            print("unknown check(s): %s" % ", ".join(sorted(unknown)))
            print("available: %s" % ", ".join(n for n, _ in CHECKS))
            return EXIT_BAD_ARGS

    scope = args.scope.strip() or None
    if scope and scope not in SCOPES:
        print("unknown scope: %s" % scope)
        print("available: %s" % ", ".join(sorted(SCOPES)))
        return EXIT_BAD_ARGS

    out = run_all(only, scope)
    out["mode"] = "gate" if args.gate else "check"

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        mode = "GATE (blocking)" if args.gate else "CHECK (advisory)"
        print("=== environment preflight — %s — scope=%s ==="
              % (mode, out.get("scope", "all")))
        for r in out["checks"]:
            mark = "PASS" if r["ok"] else ("FAIL" if r["name"] in REQUIRED
                                           else "WARN")
            print("  [%s] %-16s %s" % (mark, r["name"], r["detail"]))
        print()
        print("  %d/%d passed" % (out["passed"], out["total"]))
        if out["blocking"]:
            print("  BLOCKING: %s" % ", ".join(out["blocking"]))
        if out["advisory"]:
            print("  advisory: %s" % ", ".join(out["advisory"]))
        print()
        if out["ready"]:
            print("READY — all required preparation confirmed")
        else:
            print("NOT READY — %d required check(s) failing" % len(out["blocking"]))

    if args.gate and not out["ready"]:
        return EXIT_NOT_READY
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
