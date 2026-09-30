# -*- coding: utf-8 -*-
"""F5 CDP env check: ensure the CDP Chrome instance is running.

Tool #2 in hotkey_tools.md — TOOL.F5.CDP.ENV
  - port 9222 already answering  -> OK (action=already)
  - port down                    -> auto-launch CDP Chrome, wait, verify
  - still down                   -> FAIL (exit 1)

Usage:
  python f5_cdp_env.py            # check + auto-launch if needed
  python f5_cdp_env.py --check    # check only, never launch

Exit codes: 0 ok, 1 CDP down (and auto-launch failed or --check).
"""
import sys

import cdp_common


def main():
    check_only = "--check" in sys.argv

    if check_only:
        ok = cdp_common.cdp_ready()
        cdp_common.log("F5 check-only: ready=%s" % ok, "F5-ENV")
        print("CDP ready" if ok else "CDP DOWN")
        sys.exit(0 if ok else 1)

    ok, action = cdp_common.ensure_cdp("F5-ENV")
    if not ok:
        print("FAIL: CDP down, auto-launch failed")
        sys.exit(1)

    # extra evidence: list tabs so the log shows the instance is real
    try:
        tabs = [t.get("url", "") for t in cdp_common.get("/json")
                if t.get("type") == "page"]
        cdp_common.log("OK: CDP env up (action=%s, %d page tab(s): %s)"
                       % (action, len(tabs), "; ".join(tabs)[:200]), "F5-ENV")
    except Exception as e:
        cdp_common.log("OK: CDP env up (action=%s, tab list err: %s)"
                       % (action, e), "F5-ENV")
    print("OK: CDP env up (action=%s)" % action)
    sys.exit(0)


if __name__ == "__main__":
    main()
