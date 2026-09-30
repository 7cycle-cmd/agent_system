# -*- coding: utf-8 -*-
"""Reverse of F9: paste the clipboard into the DeepSeek chat input box.

F9 goes DeepSeek -> VS Code. This tool goes VS Code -> DeepSeek:
  ^!d = f_copy_reply.py --copy-only  (VS Code reply -> clipboard)
      + f_deepseek_paste.py          (clipboard -> DeepSeek input box)

WHY CDP Input.insertText (not pyautogui click + Ctrl+V):
  - no mouse, no coordinates -> survives window moves / resizes
  - Input.insertText fires a REAL input event, so DeepSeek's React
    controlled textarea updates. Setting el.value directly does NOT work
    (React never sees the change and the send button stays disabled).

Usage:
  python f_deepseek_paste.py            # clipboard -> DeepSeek input
  python f_deepseek_paste.py --dry      # show what would be pasted
  python f_deepseek_paste.py --check    # CDP up + input element found?

Exit codes: 0 ok, 1 CDP unreachable (even after auto-launch), 2 no DeepSeek
            tab, 3 empty clipboard, 4 no input element, 5 verify mismatch.
"""
import sys

import cdp_common

# Focus the DeepSeek message box. DeepSeek uses a <textarea>; fall back to the
# last contenteditable element if the markup changes.
FOCUS_JS = """(function(){
    var el = document.querySelector('textarea');
    if (!el) {
        var eds = document.querySelectorAll('[contenteditable="true"]');
        el = eds.length ? eds[eds.length - 1] : null;
    }
    if (!el) return null;
    el.focus();
    return el.tagName + '|' + (el.className || '').toString().slice(0, 60);
})()"""

# Read the input box back for verification (length proof).
READ_JS = """(function(){
    var el = document.querySelector('textarea');
    if (!el) {
        var eds = document.querySelectorAll('[contenteditable="true"]');
        el = eds.length ? eds[eds.length - 1] : null;
    }
    if (!el) return null;
    return (el.value !== undefined ? el.value : el.innerText) || '';
})()"""


def main():
    dry = "--dry" in sys.argv
    check = "--check" in sys.argv
    log = lambda msg: cdp_common.log(msg, "DS-PASTE")

    # 1. ensure CDP is up (auto-launch if down) — same composition as F9
    ok, action = cdp_common.ensure_cdp("DS-PASTE")
    if not ok:
        log("FAIL: CDP unreachable even after auto-launch")
        print("FAIL: CDP unreachable (auto-launch failed)")
        sys.exit(1)
    if action == "launched":
        log("CDP was down, auto-launched Chrome before paste")

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

    # 3. focus the input box (PROOF: element must exist before we act)
    target = cdp_common.cdp_evaluate(tab, FOCUS_JS)
    if not target:
        log("FAIL: no input element (textarea / contenteditable) found")
        print("FAIL: no DeepSeek input element")
        sys.exit(4)
    log("input element focused: %s" % target)

    if check:
        print("OK: CDP up + input element found (%s)" % target)
        return

    # 4. read clipboard
    text = cdp_common.read_clipboard_text()
    if not text or not text.strip():
        log("FAIL: clipboard empty")
        print("FAIL: clipboard empty")
        sys.exit(3)
    log("clipboard %d chars" % len(text))

    if dry:
        print("=== DRY RUN (first 200 / last 200) ===")
        print(text[:200])
        print("...")
        print(text[-200:])
        print("=== END ===")
        return

    # 5. insert text as a REAL input event (React sees it)
    cdp_common.cdp_send(tab, "Input.insertText", {"text": text})

    # 6. VERIFY: read the input box back and compare length
    got = cdp_common.cdp_evaluate(tab, READ_JS)
    got_len = len(got or "")
    if got_len < len(text):
        log("FAIL: verify mismatch (want %d chars, input has %d)"
            % (len(text), got_len))
        print("FAIL: verify mismatch (want %d, got %d)" % (len(text), got_len))
        sys.exit(5)
    log("OK: pasted %d chars into DeepSeek input (verified)" % got_len)
    print("OK: pasted %d chars into DeepSeek input" % got_len)


if __name__ == "__main__":
    main()
