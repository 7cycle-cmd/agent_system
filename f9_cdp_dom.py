# -*- coding: utf-8 -*-
"""CDP: inspect DeepSeek DOM to find message elements + latest assistant message."""
import json
import urllib.request
import websocket

CDP = "http://127.0.0.1:9222"

def get(path):
    with urllib.request.urlopen(CDP + path, timeout=5) as r:
        return json.load(r)

def find_tab():
    for t in get("/json"):
        if t.get("type") == "page" and "deepseek" in t.get("url", "").lower():
            return t
    return None

def main():
    tab = find_tab()
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
    mid = [0]
    def send(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == mid[0]:
                return msg
    def evaluate(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("result", {}).get("value")

    # 1. Find candidate message containers: elements whose class contains 'message'
    info = evaluate("""(function(){
        var out = {};
        // collect class names of elements that look like message rows
        var all = document.querySelectorAll('div[class*="message"], div[class*="Message"], div[class*="msg"], div[class*="chat"]');
        var classes = {};
        all.forEach(function(el){
            var c = el.className && el.className.toString ? el.className.toString() : '';
            if (c) classes[c] = (classes[c]||0)+1;
        });
        out.classes = Object.keys(classes).slice(0, 40);
        // find the copy button (hover action bar) - look for svg/button with copy semantics
        var btns = document.querySelectorAll('button, [role="button"], div[class*="copy"], div[class*="Copy"]');
        var btnInfo = [];
        btns.forEach(function(b){
            var t = (b.getAttribute('aria-label')||'') + '|' + (b.title||'') + '|' + (b.className&&b.className.toString?b.className.toString().slice(0,60):'');
            if (t.indexOf('opy') >= 0 || t.indexOf('copy') >= 0) btnInfo.push(t);
        });
        out.copyBtns = btnInfo.slice(0, 10);
        return JSON.stringify(out);
    })()""")
    print("DOM probe:", info)
    ws.close()

if __name__ == "__main__":
    main()
