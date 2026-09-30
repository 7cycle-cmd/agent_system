# -*- coding: utf-8 -*-
"""CDP: find latest assistant message text via DOM."""
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

    info = evaluate("""(function(){
        var msgs = Array.prototype.slice.call(document.querySelectorAll('.ds-message'));
        var out = {count: msgs.length, items: []};
        msgs.forEach(function(m, i){
            var cls = m.className.toString();
            var isAsst = cls.indexOf('assistant') >= 0 || !!m.querySelector('.ds-assistant-message-main-content');
            var isUser = cls.indexOf('user') >= 0;
            var txt = (m.innerText || '').trim();
            out.items.push({
                i: i,
                cls: cls.slice(0, 80),
                isAsst: isAsst,
                isUser: isUser,
                len: txt.length,
                head: txt.slice(0, 60),
                tail: txt.slice(-60)
            });
        });
        return JSON.stringify(out);
    })()""")
    data = json.loads(info)
    print("total .ds-message:", data["count"])
    for it in data["items"]:
        tag = "ASST" if it["isAsst"] else ("USER" if it["isUser"] else "???")
        print("  [%d] %s len=%d head=%r tail=%r" % (it["i"], tag, it["len"], it["head"], it["tail"]))
    ws.close()

if __name__ == "__main__":
    main()
