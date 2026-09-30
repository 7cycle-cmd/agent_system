# -*- coding: utf-8 -*-
"""CDP probe: list tabs, find DeepSeek, dump latest assistant message structure."""
import json
import sys
import urllib.request

CDP = "http://127.0.0.1:9222"

def get(path):
    with urllib.request.urlopen(CDP + path, timeout=5) as r:
        return json.load(r)

def main():
    ver = get("/json/version")
    print("CDP OK:", ver.get("Browser"))
    tabs = get("/json")
    ds_tab = None
    for t in tabs:
        if t.get("type") != "page":
            continue
        title = t.get("title", "")
        url = t.get("url", "")
        print("  [%s] %s | %s" % (t["id"][:8], title[:60], url[:70]))
        if "deepseek" in url.lower():
            ds_tab = t
    if not ds_tab:
        print("NO DEEPSEEK TAB FOUND")
        sys.exit(1)
    print("DeepSeek tab:", ds_tab["id"])
    print("WS URL:", ds_tab.get("webSocketDebuggerUrl"))

if __name__ == "__main__":
    main()
