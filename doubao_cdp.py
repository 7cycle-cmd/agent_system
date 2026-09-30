# -*- coding: utf-8 -*-
"""doubao_cdp.py — control 豆包's built-in Chromium over CDP.

WHY THIS EXISTS (measured 2026-09-23)
-------------------------------------
豆包 ships a FULL Chromium 147 and answers the standard DevTools Protocol:

    GET http://127.0.0.1:<port>/json/version  ->  HTTP 200
    { "Browser": "Chrome/147.0.7727.149", "Protocol-Version": "1.3",
      "V8-Version": "14.7.173.25", "webSocketDebuggerUrl": "ws://..." }

and Playwright connects to it:

    p.chromium.connect_over_cdp(url)  ->  CONNECTED: True, 4 pages

So the UI can be driven by the DOM instead of by pixels. That removes an entire
class of problems the pixel route had to work around:

    card / copy-icon rect   -> element.bounding_box() returns it
    star-row colour         -> read the DOM state
    scroll-to-bottom helper -> scrollTop / scrollHeight
    conversation end        -> read the DOM
    5-step navigation       -> click() on a selector
    Ctrl+A/Ctrl+C on a card -> not needed, read the DOM
    coordinate-space trap   -> no coordinates at all
    blue border needs focus -> no pixels at all

WHY THE SETTINGS ARE ENVIRONMENT VARIABLES
------------------------------------------
The user asked for this explicitly ("remember to have enviornment setting for
that"), and there is a hard reason too: `cdp_common.py:29` already hardcodes
`CDP_PORT = 9222` for the Chrome instance used by F5/F9. If 豆包 also took 9222,
`cdp_common.find_tab()` would hand a 豆包 tab to a DeepSeek tool. So the port is
a SETTING whose default is deliberately NOT 9222.

    DOUBAO_CDP_PORT      default 9333   (NOT 9222 — see above)
    DOUBAO_EXE           default %LOCALAPPDATA%\\Doubao\\Application\\app\\Doubao.exe
    DOUBAO_LAUNCH_ARGS   default --remote-debugging-port=<port> --remote-allow-origins=*
                                 --force-renderer-accessibility
    DOUBAO_CDP_TIMEOUT   default 25     seconds to wait for the port
    DOUBAO_CDP_URL       default http://127.0.0.1:<port>

WHY CDP IS OFF BY DEFAULT IN 豆包
---------------------------------
Measured: a default launch listens on 11128 and 49853, and NEITHER is CDP
(11128 closes the connection; 49853 returns HTTP 404). The flag is REQUIRED, so
this module must be able to LAUNCH 豆包 rather than assume a running instance is
controllable.

WHY launch_doubao() REFUSES TO KILL BY DEFAULT
----------------------------------------------
Closing the user's running app is destructive. The previous plan's Phase 0 was
blocked on exactly that decision, so it is an explicit parameter
(`kill_running=True`) and never a default. A tool that silently kills the user's
app to get a port is a tool that loses the user's work.

Exit codes for the CLI: 0 ok / 1 failed / 2 UNKNOWN (not proven).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
LOG_FILE = BASE / "doubao_cdp_log.txt"

# ---------------------------------------------------------------------------
# SETTINGS — every one overridable from the environment.
# ---------------------------------------------------------------------------
DEFAULT_PORT = 9333          # NOT 9222: that is cdp_common.py's Chrome port.
DEFAULT_TIMEOUT = 25.0
DEFAULT_EXE = str(
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Doubao" / "Application" / "app" / "Doubao.exe"
)


def port() -> int:
    try:
        return int(os.environ.get("DOUBAO_CDP_PORT", "").strip() or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def exe_path() -> str:
    return os.environ.get("DOUBAO_EXE", "").strip() or DEFAULT_EXE


def timeout_sec() -> float:
    try:
        return float(os.environ.get("DOUBAO_CDP_TIMEOUT", "").strip() or DEFAULT_TIMEOUT)
    except ValueError:
        return DEFAULT_TIMEOUT


def cdp_url() -> str:
    return (os.environ.get("DOUBAO_CDP_URL", "").strip()
            or "http://127.0.0.1:%d" % port())


def launch_args() -> list[str]:
    """The measured working flag set. Overridable as one string."""
    raw = os.environ.get("DOUBAO_LAUNCH_ARGS", "").strip()
    if raw:
        return raw.split()
    return [
        "--remote-debugging-port=%d" % port(),
        "--remote-allow-origins=*",
        "--force-renderer-accessibility",
    ]


def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except OSError:
        pass
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# CDP reachability
# ---------------------------------------------------------------------------

def cdp_ready(timeout: float = 3.0) -> bool:
    """True when the CDP HTTP endpoint answers on OUR port."""
    try:
        with urllib.request.urlopen(cdp_url() + "/json/version", timeout=timeout) as r:
            json.load(r)
            return True
    except Exception:
        return False


def version() -> dict:
    """The /json/version payload, or {} when unreachable."""
    try:
        with urllib.request.urlopen(cdp_url() + "/json/version", timeout=5) as r:
            return json.load(r)
    except Exception:
        return {}


def _wait_ready(timeout: float | None = None) -> bool:
    """Wait on a CONDITION (the port answers), never a fixed sleep.

    A fixed sleep is wrong in both directions and expires silently, letting the
    next step run against a browser that never became ready.
    """
    deadline = time.time() + (timeout if timeout is not None else timeout_sec())
    while time.time() < deadline:
        if cdp_ready():
            return True
        time.sleep(0.5)
    return cdp_ready()


def running_pids() -> list[int]:
    """PIDs of the 豆包 app shell (the process WITHOUT a --type= child flag)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='Doubao.exe'\" | "
             "Where-Object { $_.CommandLine -notmatch '--type=' } | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [int(x) for x in (out.stdout or "").split() if x.strip().isdigit()]
    except Exception:
        return []


def kill_doubao() -> int:
    """Stop every Doubao.exe. Returns how many were running before.

    DESTRUCTIVE. Only called from launch_doubao(kill_running=True).
    """
    before = len(running_pids())
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process Doubao -ErrorAction SilentlyContinue | "
             "Stop-Process -Force -ErrorAction SilentlyContinue"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log("kill_doubao FAIL: %s: %s" % (type(e).__name__, e))
    time.sleep(3.0)
    return before


def launch_doubao(*, kill_running: bool = False) -> dict:
    """Start 豆包 with the CDP flags. Returns {ok, action, detail, pids_before}.

    `kill_running=False` (the DEFAULT) REFUSES to close a running 豆包: closing
    the user's app is destructive, so it must be asked for. When a 豆包 is already
    running and kill_running is False, the result is a REFUSAL with the reason,
    not a silent kill.
    """
    exe = exe_path()
    out: dict = {"ok": False, "action": "failed", "detail": "",
                 "pids_before": running_pids()}
    if not Path(exe).is_file():
        out["detail"] = "豆包 exe not found: %s" % exe
        return out

    if out["pids_before"]:
        if not kill_running:
            out["detail"] = (
                "豆包 is already running (pids=%s) and kill_running=False. "
                "Refusing to close the user's app. Re-run with kill_running=True "
                "if closing it is intended." % out["pids_before"])
            return out
        killed = kill_doubao()
        out["killed"] = killed
        log("launch_doubao: stopped %d 豆包 process(es)" % killed)

    args = launch_args()
    try:
        subprocess.Popen([exe] + args, cwd=str(Path(exe).parent))
    except Exception as e:
        out["detail"] = "launch failed: %s: %s" % (type(e).__name__, e)
        return out

    log("launch_doubao: started with %s" % " ".join(args))
    if _wait_ready():
        out.update(ok=True, action="launched",
                   detail="CDP ready on %s" % cdp_url())
    else:
        out["detail"] = ("launched but CDP did not answer on %s within %.0fs"
                         % (cdp_url(), timeout_sec()))
    return out


def ensure_cdp(*, kill_running: bool = False) -> tuple[bool, str]:
    """Make sure CDP is up. Returns (ok, action).

    action: "already" (the port answered) | "launched" (we started it) | "failed".
    Same contract as `cdp_common.ensure_cdp` (cdp_common.py:93), so a caller
    reads both tools the same way.
    """
    if cdp_ready():
        return True, "already"
    r = launch_doubao(kill_running=kill_running)
    if r.get("ok"):
        return True, "launched"
    log("ensure_cdp FAIL: %s" % r.get("detail"))
    return False, "failed"


# ---------------------------------------------------------------------------
# Playwright over CDP
# ---------------------------------------------------------------------------

def connect():
    """Connect Playwright to 豆包's CDP. Raises when unreachable."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url())
    except Exception:
        pw.stop()
        raise
    return pw, browser


def pages(browser) -> list[dict]:
    """Every page as {index, url, title}. Never raises on a single bad page."""
    out: list[dict] = []
    for ci, ctx in enumerate(browser.contexts):
        for pi, pg in enumerate(ctx.pages):
            try:
                url = pg.url
            except Exception:
                url = ""
            try:
                title = pg.title()
            except Exception:
                title = ""
            out.append({"index": len(out), "context": ci, "page": pi,
                        "url": url, "title": title})
    return out


def find_page(browser, substr: str):
    """The first page whose URL contains `substr` (case-insensitive), or None."""
    s = (substr or "").lower()
    for ctx in browser.contexts:
        for pg in ctx.pages:
            try:
                if s in (pg.url or "").lower():
                    return pg
            except Exception:
                continue
    return None


def evaluate(page, expr: str):
    """Runtime.evaluate with returnByValue. Raises on a page that refuses."""
    return page.evaluate(expr)


def bounding_box(page, selector: str) -> dict:
    """The rect of the FIRST element matching `selector`.

    Returns {ok, rect:[x1,y1,x2,y2], width, height, error}. A selector that
    matches NOTHING is a REFUSAL with a reason — never a zero rect, because a
    zero rect is a plausible-looking measurement of nothing.
    """
    out: dict = {"ok": False, "rect": None, "width": None, "height": None,
                 "error": None, "selector": selector}
    try:
        el = page.query_selector(selector)
    except Exception as e:
        out["error"] = "query failed: %s: %s" % (type(e).__name__, e)
        return out
    if el is None:
        out["error"] = "selector matched nothing: %r" % selector
        return out
    try:
        bb = el.bounding_box()
    except Exception as e:
        out["error"] = "bounding_box failed: %s: %s" % (type(e).__name__, e)
        return out
    if not bb:
        out["error"] = "element has no box (hidden or zero-size): %r" % selector
        return out
    x1 = int(round(bb["x"]))
    y1 = int(round(bb["y"]))
    x2 = int(round(bb["x"] + bb["width"]))
    y2 = int(round(bb["y"] + bb["height"]))
    if x2 <= x1 or y2 <= y1:
        out["error"] = "degenerate rect %s for %r" % ([x1, y1, x2, y2], selector)
        return out
    out.update(ok=True, rect=[x1, y1, x2, y2], width=x2 - x1, height=y2 - y1)
    return out


def click(page, selector: str) -> dict:
    """Click the first element matching `selector`. Refuses when it matches none."""
    out: dict = {"ok": False, "error": None, "selector": selector}
    try:
        el = page.query_selector(selector)
    except Exception as e:
        out["error"] = "query failed: %s: %s" % (type(e).__name__, e)
        return out
    if el is None:
        out["error"] = "selector matched nothing: %r" % selector
        return out
    try:
        el.click(timeout=8000)
        out["ok"] = True
    except Exception as e:
        out["error"] = "click failed: %s: %s" % (type(e).__name__, e)
    return out


def scroll_state(page, selector: str = "") -> dict:
    """The scroll state of `selector` (or the document). No pixels involved.

    Returns {ok, scroll_top, scroll_height, client_height, at_bottom, error}.
    `at_bottom` is arithmetic, so it cannot be misread the way a circle glyph can.
    """
    out: dict = {"ok": False, "error": None, "selector": selector}
    expr = """(sel) => {
        const el = sel ? document.querySelector(sel) : document.scrollingElement;
        if (!el) return null;
        return {top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight};
    }"""
    try:
        r = page.evaluate(expr, selector or "")
    except Exception as e:
        out["error"] = "evaluate failed: %s: %s" % (type(e).__name__, e)
        return out
    if not r:
        out["error"] = "no scrollable element for %r" % (selector or "document")
        return out
    top, height, client = int(r["top"]), int(r["height"]), int(r["client"])
    out.update(ok=True, scroll_top=top, scroll_height=height,
               client_height=client,
               at_bottom=(top + client) >= (height - 2))
    return out


def dump_elements(page, *, limit: int = 400) -> list[dict]:
    """Every element that looks INTERACTIVE, with its rect and a selector hint.

    WHY this exists: the user asked for "ui for all the element list". A list of
    what the page actually contains is what turns "guess a selector" into
    "measure a selector" — and a guessed selector is the DOM equivalent of a
    hardcoded coordinate.

    Each row: {tag, role, text, aria, cls, id, rect, clickable}.
    """
    expr = """(limit) => {
        const out = [];
        const all = document.querySelectorAll('*');
        for (const el of all) {
            if (out.length >= limit) break;
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute('role') || '';
            const aria = el.getAttribute('aria-label') || '';
            const txt = (el.innerText || '').trim().slice(0, 60);
            const cls = (el.className && typeof el.className === 'string')
                        ? el.className.slice(0, 80) : '';
            const id = el.id || '';
            const clickable = (tag === 'button' || tag === 'a' || tag === 'input'
                || role === 'button' || role === 'tab' || role === 'menuitem'
                || role === 'listitem' || role === 'option'
                || el.getAttribute('tabindex') !== null);
            if (!clickable && !txt && !aria) continue;
            out.push({tag, role, text: txt, aria, cls, id,
                      rect: [Math.round(r.left), Math.round(r.top),
                             Math.round(r.right), Math.round(r.bottom)],
                      clickable});
        }
        return out;
    }"""
    try:
        return page.evaluate(expr, limit) or []
    except Exception as e:
        log("dump_elements FAIL: %s: %s" % (type(e).__name__, e))
        return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_check() -> int:
    print("DOUBAO_CDP_PORT   : %d" % port())
    print("DOUBAO_CDP_URL    : %s" % cdp_url())
    print("DOUBAO_EXE        : %s (exists=%s)" % (exe_path(), Path(exe_path()).is_file()))
    print("DOUBAO_LAUNCH_ARGS: %s" % " ".join(launch_args()))
    print("DOUBAO_CDP_TIMEOUT: %.0fs" % timeout_sec())
    print("cdp_ready         : %s" % cdp_ready())
    v = version()
    if v:
        print("browser           : %s" % v.get("Browser"))
        print("protocol          : %s" % v.get("Protocol-Version"))
    print("running pids      : %s" % running_pids())
    print("collision check   : cdp_common.py uses 9222; ours is %d -> %s"
          % (port(), "OK" if port() != 9222 else "COLLISION"))
    return 0


def cmd_pages() -> int:
    ok, action = ensure_cdp()
    print("ensure_cdp: ok=%s action=%s" % (ok, action))
    if not ok:
        return 1
    pw, b = connect()
    try:
        for p in pages(b):
            print("  [%d] %s" % (p["index"], p["url"]))
            print("      title=%r" % p["title"])
    finally:
        b.close()
        pw.stop()
    return 0


def cmd_dump_dom(substr: str, out_path: str) -> int:
    ok, action = ensure_cdp()
    if not ok:
        print("ensure_cdp failed")
        return 1
    pw, b = connect()
    try:
        pg = find_page(b, substr)
        if pg is None:
            print("no page matching %r" % substr)
            return 2
        els = dump_elements(pg)
        print("page: %s" % pg.url)
        print("elements: %d" % len(els))
        if out_path:
            Path(out_path).write_text(
                json.dumps({"url": pg.url, "elements": els},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            print("written: %s" % out_path)
        for e in els[:60]:
            print("  %-8s role=%-10s %-40r rect=%s%s"
                  % (e["tag"], e["role"], e["text"][:40], e["rect"],
                     " CLICK" if e["clickable"] else ""))
    finally:
        b.close()
        pw.stop()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Control 豆包's built-in Chromium over CDP.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--pages", action="store_true")
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--kill-running", action="store_true",
                    help="allow launch to close a running 豆包 (destructive)")
    ap.add_argument("--dump-dom", metavar="URL_SUBSTR", nargs="?", const="doubao-chat")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.check:
        return cmd_check()
    if args.launch:
        r = launch_doubao(kill_running=args.kill_running)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if args.pages:
        return cmd_pages()
    if args.dump_dom:
        return cmd_dump_dom(args.dump_dom, args.out)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
