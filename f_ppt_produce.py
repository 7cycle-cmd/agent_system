# -*- coding: utf-8 -*-
"""f_ppt_produce.py — produce a PPT using the video-produce flow.

CAP.PPT.PRODUCE backend. The FLOW is the shared video-7-stage skeleton
(research -> proposal -> script -> scene_plan -> assets -> edit -> compose);
only the backend per stage differs (python-pptx instead of Remotion).

The capability row (flow_json + environment_json) is read from agent.db, so
the deck structure is DB-driven, not hard-coded here.

Usage:
  python f_ppt_produce.py --check          # deps + capability row present?
  python f_ppt_produce.py --build          # build the F9 deck
  python f_ppt_produce.py --build --out X  # custom output path

Exit codes: 0 ok, 1 missing dep, 2 capability not found, 3 no content.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(ROOT, "out", "F9_flow.pptx")
CAP_ID = "CAP.PPT.PRODUCE"

# ---- Content: the F9 flow, one row per step (research -> scene_plan) --------
# Each step carries precondition / action / proof — the same three lanes the
# video flow uses for shot cards, simplified for slides.
F9_STEPS = [
    {
        "n": 1, "stage": "research", "title": "Record clipboard sequence",
        "pre": "—",
        "act": "seqBefore := ClipSeq()",
        "proof": "A sequence number exists",
    },
    {
        "n": 2, "stage": "research", "title": "Write the start log",
        "pre": "—",
        "act": 'F9Log("F9 start (CDP, seq=...)")',
        "proof": "f9_log.txt has the line",
    },
    {
        "n": 3, "stage": "research", "title": "Show the user a tooltip",
        "pre": "—",
        "act": 'Tooltip("F9: CDP grabbing...")',
        "proof": "Tooltip visible on screen",
    },
    {
        "n": 4, "stage": "proposal", "title": "Ensure CDP is up",
        "pre": "chrome.exe exists",
        "act": 'cdp_common.ensure_cdp("F9-CDP")',
        "proof": "log: CDP already running / up after auto-launch",
    },
    {
        "n": 5, "stage": "proposal", "title": "Find the DeepSeek tab",
        "pre": "CDP is up",
        "act": "cdp_common.find_tab()",
        "proof": "Tab dict with webSocketDebuggerUrl",
    },
    {
        "n": 6, "stage": "script", "title": "Grab the latest assistant message",
        "pre": "Tab exists",
        "act": "cdp_evaluate(tab, GRAB_JS)",
        "proof": "Non-empty text returned",
    },
    {
        "n": 7, "stage": "script", "title": "Strip the trailing offer line",
        "pre": "Text present",
        "act": "strip_offer(text)",
        "proof": "Length shrinks (M < N)",
    },
    {
        "n": 8, "stage": "scene_plan", "title": "Write the clipboard",
        "pre": "Stripped text",
        "act": "cdp_common.set_clipboard(final)",
        "proof": "Clipboard content equals final",
    },
    {
        "n": 9, "stage": "scene_plan", "title": "Verify the clipboard changed",
        "pre": "—",
        "act": "seqAfter := ClipSeq(); compare",
        "proof": "seqAfter != seqBefore",
    },
    {
        "n": 10, "stage": "assets", "title": "Activate VS Code",
        "pre": "—",
        "act": 'WinActivate("ahk_exe Code.exe")',
        "proof": "VS Code is foreground",
    },
    {
        "n": 11, "stage": "edit", "title": "Paste",
        "pre": "VS Code foreground",
        "act": 'SendInput("^v")',
        "proof": "Text appears in the chat box",
    },
    {
        "n": 12, "stage": "compose", "title": "Close out",
        "pre": "—",
        "act": 'F9Log("Pasted into VS Code")',
        "proof": "Log line + tooltip clears after 2s",
    },
]

EXIT_CODES = [
    ("0", "ok"),
    ("1", "CDP unreachable (even after auto-launch)"),
    ("2", "no DeepSeek tab"),
    ("3", "no assistant message"),
]


def load_capability(cap_id: str = CAP_ID) -> dict:
    """Read the capability row so the deck follows the DB-driven flow."""
    import skill_contract_store as scs

    contract = scs.get_contract(cap_id)
    if not contract:
        return {}
    flow = contract.get("flow") or []
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            flow = []
    env = contract.get("environment") or {}
    if isinstance(env, str):
        try:
            env = json.loads(env)
        except Exception:
            env = {}
    return {"contract": contract, "flow": flow, "env": env}


def _add_title_slide(prs, title: str, subtitle: str):
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    slide.placeholders[1].text = subtitle
    return slide


def _add_bullets(prs, title: str, bullets: list[str]):
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    body = slide.placeholders[1].text_frame
    body.clear()
    for i, b in enumerate(bullets):
        p = body.paragraphs[0] if i == 0 else body.add_paragraph()
        p.text = b
        p.level = 0
    return slide


def _add_step_slide(prs, step: dict, total: int):
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Step %d/%d · %s" % (step["n"], total, step["title"])
    body = slide.placeholders[1].text_frame
    body.clear()
    rows = [
        ("stage", step["stage"]),
        ("precondition", step["pre"]),
        ("action", step["act"]),
        ("proof", step["proof"]),
    ]
    for i, (k, v) in enumerate(rows):
        p = body.paragraphs[0] if i == 0 else body.add_paragraph()
        p.text = "%s: %s" % (k, v)
        p.level = 0
    return slide


def build(out_path: str = DEFAULT_OUT) -> str:
    from pptx import Presentation
    from pptx.util import Pt

    cap = load_capability()
    if not cap:
        raise RuntimeError("capability %s not found in agent.db" % CAP_ID)
    flow = cap["flow"]
    env = cap["env"]

    prs = Presentation()

    # --- compose: title -----------------------------------------------------
    _add_title_slide(
        prs,
        "F9 — DeepSeek reply to VS Code",
        "Produced with the video-produce flow · backend: %s\n%s"
        % (env.get("backend", "python-pptx"), datetime.now().strftime("%Y-%m-%d %H:%M")),
    )

    # --- proposal: the flow itself -----------------------------------------
    _add_bullets(
        prs,
        "Flow: %s" % env.get("flow_ref", "video-7-stage"),
        [
            "%d. %s  →  %s" % (i + 1, st.get("stage"), st.get("backend"))
            for i, st in enumerate(flow)
        ]
        + [
            "",
            "Stage names are fixed. Only the backend changes per capability.",
            "This deck uses the PPT backend; the video backend is Remotion.",
        ],
    )

    # --- research: what F9 is ----------------------------------------------
    _add_bullets(
        prs,
        "What F9 does",
        [
            "One key: copy the latest DeepSeek assistant reply into VS Code.",
            "Reads the DOM over CDP — no coordinates, no hover, no scroll.",
            "Strips the trailing offer line before pasting.",
            "Composition: Tool #1 (copy) + Tool #2 (CDP env auto-launch).",
        ],
    )

    # --- scene_plan: one slide per step ------------------------------------
    for step in F9_STEPS:
        _add_step_slide(prs, step, len(F9_STEPS))

    # --- assets: exit codes -------------------------------------------------
    _add_bullets(
        prs,
        "Exit codes (f9_cdp_copy.py)",
        ["%s = %s" % (c, d) for c, d in EXIT_CODES],
    )

    # --- edit: key design decisions ----------------------------------------
    _add_bullets(
        prs,
        "Key design decisions",
        [
            "CDP over coordinate clicks: the copy button is hover-triggered and",
            "  moves with scroll, so fixed coordinates fail within seconds.",
            "ctypes SetClipboardData, not tkinter: Tk owns the clipboard and",
            "  loses the content when destroyed.",
            "Logic lives in cdp_common.py; entry scripts stay thin.",
        ],
    )

    # --- compose: proof -----------------------------------------------------
    _add_bullets(
        prs,
        "Proof",
        [
            "Clipboard sequence changes (seqBefore -> seqAfter).",
            "Log line: OK: CDP copy success.",
            "Content verified after paste.",
            "F6 self-debug: 0 AHK error dialogs.",
        ],
    )

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    prs.save(out_path)
    return out_path


def main() -> None:
    if "--check" in sys.argv:
        try:
            import pptx  # noqa: F401
        except ImportError:
            print("FAIL: python-pptx not installed")
            sys.exit(1)
        cap = load_capability()
        if not cap:
            print("FAIL: capability %s not found" % CAP_ID)
            sys.exit(2)
        print("OK: python-pptx present + %s found (flow_ref=%s backend=%s stages=%d)"
              % (CAP_ID, cap["env"].get("flow_ref"), cap["env"].get("backend"),
                 len(cap["flow"])))
        return

    if "--build" not in sys.argv:
        print("usage: f_ppt_produce.py --check | --build [--out PATH]")
        sys.exit(1)

    out = DEFAULT_OUT
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    try:
        path = build(out)
    except Exception as e:
        print("FAIL: %s: %s" % (type(e).__name__, e))
        sys.exit(3)
    size = os.path.getsize(path)
    print("OK: built %s (%d bytes, %d steps)" % (path, size, len(F9_STEPS)))


if __name__ == "__main__":
    main()
