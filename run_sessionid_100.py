# -*- coding: utf-8 -*-
"""SESSION_ID extraction 100-streak test — TEXT 7B (qwen2.5:7b-instruct).

Simulates the method-1 copy: the FULL reply text is already captured
(clipboard copy button). The LLM is TEXT (not vision) — it must extract
the SESSION_ID UUID from the reply text.

Each round: fresh random UUID embedded in a realistic reply -> ask the
text 7B -> verify the extracted UUID matches. Streak resets on any miss.
Stops at 100 consecutive wins, then reports:
  1) average time per call
  2) token spend per call (prompt + completion, from API usage field)

Usage: python run_sessionid_100.py
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:18803/v1/chat/completions"
MODEL = "qwen2.5:7b-instruct"
TARGET = 100
UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def make_reply(sid: str) -> str:
    """Realistic reply shape (same as the live worker-identity reply)."""
    return (
        "\n\nI'm **GitHub Copilot**, running on the model **Qwen: Qwen3.8 27B**.\n\n"
        "Here's the metadata header you asked for:\n\n"
        "```\n"
        f"SESSION_ID: {sid}\n"
        "TASK_ID:\n"
        "CHAT_ID:\n"
        "MODEL: Qwen: Qwen3.8 27B\n"
        "```\n\n"
        "Note: `SESSION_ID` is a freshly generated UUID for this reply "
        "(per the F1 rule, a new one is generated each time). `TASK_ID` and "
        "`CHAT_ID` are left blank since none were provided in your message."
    )


PROMPT = (
    "Below is a chat reply. Extract the SESSION_ID (a UUID) from it. "
    "Reply with ONLY the UUID, nothing else, no markdown, no explanation.\n\n"
    "<<<REPLY\n{text}\nREPLY>>>"
)


def ask(text: str) -> tuple[str, float, dict]:
    body = json.dumps(
        {
            "model": MODEL,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": PROMPT.format(text=text)}],
        }
    ).encode()
    req = urllib.request.Request(
        BASE, data=body, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    dt = time.time() - t0
    content = d["choices"][0]["message"]["content"].strip()
    return content, dt, d.get("usage", {})


def main() -> None:
    streak = 0
    total = 0
    times: list[float] = []
    ptok: list[int] = []
    ctok: list[int] = []
    while streak < TARGET:
        total += 1
        sid = str(uuid.uuid4())
        reply = make_reply(sid)
        try:
            content, dt, usage = ask(reply)
        except Exception as e:
            print(f"[{total}] ERROR {type(e).__name__}: {e} — streak reset")
            streak = 0
            continue
        times.append(dt)
        ptok.append(usage.get("prompt_tokens", 0))
        ctok.append(usage.get("completion_tokens", 0))
        m = UUID_RE.search(content)
        ok = bool(m) and m.group(0).lower() == sid.lower()
        if ok:
            streak += 1
            if streak % 10 == 0 or streak == TARGET:
                print(
                    f"[{total}] streak={streak} last={dt:.2f}s "
                    f"ptok={usage.get('prompt_tokens')} ctok={usage.get('completion_tokens')}"
                )
        else:
            print(f"[{total}] FAIL expected={sid} got={content[:80]!r} — streak reset")
            streak = 0
    n = len(times)
    print("=" * 60)
    print(f"100-streak ACHIEVED in {total} calls")
    print(
        f"1) average time per call: {sum(times) / n:.2f}s "
        f"(min {min(times):.2f}, max {max(times):.2f})"
    )
    print(
        f"2) tokens/call: prompt avg {sum(ptok) / n:.0f}, "
        f"completion avg {sum(ctok) / n:.0f}, "
        f"total avg {(sum(ptok) + sum(ctok)) / n:.0f}"
    )
    print(f"   100 calls ≈ {100 * (sum(ptok) + sum(ctok)) / n:.0f} tokens total")


if __name__ == "__main__":
    main()
