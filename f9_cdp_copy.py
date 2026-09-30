# -*- coding: utf-8 -*-
"""F9 CDP copy: grab latest DeepSeek assistant message via CDP, strip the
trailing '需要我幫你...？講聲，即刻寫...' offer line, write to clipboard.

Composition: F9 = Tool#1 (copy) + Tool#2 (env auto-check/launch) —
if CDP port 9222 is down, it auto-launches the CDP Chrome instance first.

Usage:
  python f9_cdp_copy.py            # ensure CDP + grab + strip + set clipboard
  python f9_cdp_copy.py --dry      # print what would be copied, no clipboard
  python f9_cdp_copy.py --raw      # no strip (debug)

Exit codes: 0 ok, 1 no CDP (even after auto-launch), 2 no deepseek tab,
            3 no assistant message.
"""
import re
import sys

import cdp_common

# Trailing offer pattern: "需要我幫你...？講聲，即刻寫..." (last line of reply)
STRIP_RE = re.compile(
    r"\n*\s*需要我幫你[^\n？?]*[？?]\s*講聲[^\n]*\s*$"
)

# JS: latest .ds-message that is an assistant message with non-empty text
GRAB_JS = """(function(){
    var msgs = Array.prototype.slice.call(document.querySelectorAll('.ds-message'));
    for (var i = msgs.length - 1; i >= 0; i--) {
        var m = msgs[i];
        var cls = m.className.toString();
        var isAsst = cls.indexOf('assistant') >= 0 || !!m.querySelector('.ds-assistant-message-main-content');
        if (isAsst) {
            var t = (m.innerText || '').trim();
            if (t.length > 0) return t;
        }
    }
    return null;
})()"""


def strip_offer(text):
    """Remove the trailing '需要我幫你...？講聲，即刻寫...' offer line."""
    m = STRIP_RE.search(text)
    if m:
        return text[:m.start()].rstrip()
    return text.rstrip()

def main():
    dry = "--dry" in sys.argv
    raw = "--raw" in sys.argv
    log = lambda msg: cdp_common.log(msg, "F9-CDP")

    # 1. ensure CDP is up (auto-launch if down) — Tool#2 composition
    ok, action = cdp_common.ensure_cdp("F9-CDP")
    if not ok:
        log("FAIL: CDP unreachable even after auto-launch")
        print("FAIL: CDP unreachable (auto-launch failed)")
        sys.exit(1)
    if action == "launched":
        log("CDP was down, auto-launched Chrome before copy")

    # 2. find DeepSeek tab
    try:
        tab = cdp_common.find_tab()
    except Exception as e:
        log("FAIL: CDP unreachable (%s)" % e)
        print("FAIL: CDP unreachable:", e)
        sys.exit(1)
    if not tab:
        log("FAIL: no DeepSeek tab")
        print("FAIL: no DeepSeek tab")
        sys.exit(2)

    # 3. grab latest assistant message
    text = cdp_common.cdp_evaluate(tab, GRAB_JS)
    if not text:
        log("FAIL: no assistant message found")
        print("FAIL: no assistant message")
        sys.exit(3)

    # 4. strip trailing offer
    final = text.rstrip() if raw else strip_offer(text)
    log("grabbed %d chars, after strip %d chars" % (len(text), len(final)))

    if dry:
        print("=== DRY RUN (first 200 / last 200) ===")
        print(final[:200])
        print("...")
        print(final[-200:])
        print("=== END ===")
        return

    # 5. set clipboard
    cdp_common.set_clipboard(final)
    log("OK: clipboard set (%d chars)" % len(final))
    print("OK: copied %d chars to clipboard" % len(final))

if __name__ == "__main__":
    main()
