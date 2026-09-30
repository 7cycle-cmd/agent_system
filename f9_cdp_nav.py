# -*- coding: utf-8 -*-
"""CDP: navigate DeepSeek tab to share link, wait, dump page state."""
import json
import time
import urllib.request
import websocket

CDP = "http://127.0.0.1:9222"
SHARE_URL = "https://chat.deepseek.com/a/chat/s/7e589851-81bf-4e63-a3b6-205a3ee6a7fd"

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
    if not tab:
        print("NO DEEPSEEK TAB")
        return
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
        res = r.get("result", {}).get("result", {})
        return res.get("value")

    send("Page.enable")
    print("navigating to share link...")
    send("Page.navigate", {"url": SHARE_URL})
    time.sleep(8)
    title = evaluate("document.title")
    url = evaluate("location.href")
    print("title:", title)
    print("url:", url)
    # check for message content
    info = evaluate("""(function(){
        var body = document.body.innerText || '';
        return JSON.stringify({
            bodyLen: body.length,
            bodyHead: body.slice(0, 300),
            hasLogin: !!document.querySelector('input[type=password]'),
            iframes: document.querySelectorAll('iframe').length
        });
    })()""")
    print("page info:", info)
    ws.close()

if __name__ == "__main__":
    main()
