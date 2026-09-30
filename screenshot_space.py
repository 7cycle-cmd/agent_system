# -*- coding: utf-8 -*-
"""The ONE screenshot entry point, in ONE coordinate space.

WHY THIS MODULE EXISTS (measured 2026-09-21)
--------------------------------------------
Two capture paths existed and produced DIFFERENT images:

    pyautogui.screenshot()                        -> 1920x1080  (1:1 with screen)
    mcp.screen_snapshot(maxWidth=1600)            -> 1600x900   (scale 1.2)

`f_perm_click.py` used OpenClaw FIRST and pyautogui as fallback, so the SAME
function returned an image in one space or the other depending on whether OpenClaw
happened to be up. `_LAST_SHOT_SOURCE` recorded which was used, but recording is
not prevention — a rect could still be applied to the wrong space, and that is the
same failure class as the measured 8px / 26px red-box offsets.

MEASURED, and the point the user made: 1600x900 was never a limitation.
`maxWidth` is an ARGUMENT:

    maxWidth=800  ->  800x450       maxWidth=1920 -> 1920x1080
    maxWidth=1600 -> 1600x900       maxWidth=None -> 1920x1080
    maxWidth=2000 -> 1920x1080      maxWidth=2560 -> 1920x1080

So 1920x1080 (the real screen) is reachable. Nobody had set it correctly — the
repo had three call sites at 1920 (f9_find_copy, f_mode_vision, f_model_vision) and
one at 1600 (f_perm_click). That is an inconsistency, not a design choice.

MEASURED order/quality (Phase C):
    pyautogui: 0.034s, 5/5, 1:1 with the screen, no window
    OpenClaw : 0.119s, 5/5, scaled unless maxWidth is set
`f_new_session.py:7` already recorded why pyautogui is preferred: "mcp_snapshot()
brings the OpenClaw window to the foreground on every call (popup bug, 2026-09-18).
pyautogui.screenshot() has no window and is faster." That reason had been recorded
but not applied at this call site.

SO: pyautogui FIRST (native, no window, faster), OpenClaw second with the width
DERIVED FROM THE SCREEN so it cannot come back scaled. Both sources are then in the
SAME space, and a mismatch is REFUSED rather than described.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))


def log(msg: str) -> None:
    print(msg, flush=True)


def screen_size() -> tuple[int, int]:
    """REAL screen pixels. Reads the metric rather than assuming a resolution."""
    u = ctypes.windll.user32
    return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


class SpaceError(RuntimeError):
    """A capture could not be produced in the ONE expected space.

    ONE error type for every refusal, so a caller cannot catch only some of the
    ways a capture fails.
    """


def _grab_pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = False
    return pyautogui.screenshot().convert("RGB")


def _grab_openclaw(max_width: int):
    """OpenClaw snapshot, with the width set to the SCREEN so it is not scaled."""
    import io

    import mcp_client
    from mcp_client import extract_image_bytes
    from PIL import Image

    client = mcp_client.McpClient(mcp_client.McpConfig.from_env())
    res = client.screen_snapshot({"screenIndex": 0, "maxWidth": int(max_width)})
    raw, err = extract_image_bytes(res)
    if not raw:
        raise SpaceError("OpenClaw returned no image bytes: %s" % err)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def grab(prefer: str = "local", *, allow_scaled: bool = False,
         out_path: Path | str | None = None) -> dict[str, Any]:
    """Capture the screen, in the ONE space, and PROVE it.

    Returns {ok, image, size, space, source, scale, attempts, error}.

    `prefer`:
      "local"    -> pyautogui first, OpenClaw fallback (DEFAULT)
      "openclaw" -> OpenClaw first, pyautogui fallback
      "only_local" / "only_openclaw" -> no fallback (a fallback is a DIFFERENT
                     instrument, so a provenance-sensitive caller must not get one
                     silently)

    `allow_scaled=False` (default) REFUSES an image that is not 1:1 with the screen.
    A scaled image is not "slightly worse"; it is a different coordinate space, and
    accepting it silently is how a rect lands in the wrong place while looking
    deliberate. Pass True only where the caller genuinely works in scaled space.
    """
    sw, sh = screen_size()
    out: dict[str, Any] = {"ok": False, "image": None, "size": None, "space": None,
                           "source": None, "scale": None, "screen": [sw, sh],
                           "attempts": [], "error": None}

    # Fonts/DPR read differently before and after pyautogui is imported, so the
    # awareness is pinned first and reported with the capture.
    try:
        import source_window as swin
        out["dpi"] = swin.pin_dpi_awareness()
    except Exception as e:
        out["dpi"] = {"pinned": False, "error": "%s: %s" % (type(e).__name__, e)}

    order = {
        "local": ("pyautogui", "openclaw"),
        "openclaw": ("openclaw", "pyautogui"),
        "only_local": ("pyautogui",),
        "only_openclaw": ("openclaw",),
    }.get(prefer)
    if not order:
        out["error"] = "prefer must be local | openclaw | only_local | only_openclaw"
        return out

    for which in order:
        try:
            if which == "pyautogui":
                img = _grab_pyautogui()
            else:
                # width DERIVED from the screen, so the result cannot come back
                # scaled by 1.2 as it did with a hard-coded 1600
                img = _grab_openclaw(sw)
            size = img.size
            scale = (sw / size[0], sh / size[1]) if size[0] and size[1] else (0, 0)
            is_native = size == (sw, sh)
            out["attempts"].append({"source": which, "size": list(size),
                                    "scale": [round(scale[0], 4), round(scale[1], 4)],
                                    "native": is_native})
            if not is_native and not allow_scaled:
                log("REFUSED %s capture %dx%d: not 1:1 with the screen %dx%d "
                    "(scale %.3f) — a scaled image is a DIFFERENT coordinate space"
                    % (which, size[0], size[1], sw, sh, scale[0]))
                continue
            out.update({"ok": True, "image": img, "size": list(size),
                        "space": "native" if is_native else "scaled",
                        "source": which, "scale": [scale[0], scale[1]]})
            if out_path:
                img.save(str(out_path))
                out["path"] = str(out_path)
            return out
        except Exception as e:
            out["attempts"].append({"source": which,
                                    "error": "%s: %s" % (type(e).__name__, e)})
            log("%s capture failed: %s: %s" % (which, type(e).__name__, e))

    out["error"] = ("no capture in the native %dx%d space; attempts=%s"
                    % (sw, sh, out["attempts"]))
    return out


def selftest() -> dict[str, Any]:
    """Prove BOTH sources land in the SAME space. Used by the proof script."""
    sw, sh = screen_size()
    res: dict[str, Any] = {"screen": [sw, sh], "sources": {}}
    for which, prefer in (("pyautogui", "only_local"), ("openclaw", "only_openclaw")):
        r = grab(prefer)
        res["sources"][which] = {
            "ok": r["ok"], "size": r["size"], "scale": r["scale"],
            "native": bool(r["size"] == [sw, sh]) if r["size"] else False,
            "error": r["error"],
        }
    sizes = [v["size"] for v in res["sources"].values() if v["size"]]
    res["same_space"] = bool(sizes) and len({tuple(s) for s in sizes}) == 1
    res["all_native"] = all(v["native"] for v in res["sources"].values())
    return res


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import json

    print(json.dumps(selftest(), indent=2, ensure_ascii=False))