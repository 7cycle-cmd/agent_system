"""Mouse coordinate helper + screenshot marker.

Hotkeys:
    F2  - print current mouse X, Y to console
    F3  - capture screenshot and mark last F2 position with a red cross
    Esc - exit

Usage:
    c:/projects/agent_system/.venv/Scripts/python.exe mouse_xy_tool.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pyautogui
from PIL import Image, ImageDraw
from pynput import keyboard

BASE_DIR = Path(__file__).resolve().parent
LAST_POS: tuple[int, int] | None = None
RUNNING = True


def show_xy():
    global LAST_POS
    x, y = pyautogui.position()
    LAST_POS = (x, y)
    print(f"Mouse X={x}, Y={y}")


def mark_screenshot():
    if LAST_POS is None:
        print("Press F2 first to record a position")
        return
    x, y = LAST_POS
    timestamp = int(time.time())
    path = BASE_DIR / f"screenshot_marked_{timestamp}.png"
    img = pyautogui.screenshot()
    draw = ImageDraw.Draw(img)
    # red cross
    draw.line([(x - 20, y), (x + 20, y)], fill="red", width=3)
    draw.line([(x, y - 20), (x, y + 20)], fill="red", width=3)
    # red circle
    draw.ellipse([(x - 10, y - 10), (x + 10, y + 10)], outline="red", width=2)
    img.save(path)
    print(f"Marked screenshot saved: {path}  (X={x}, Y={y})")


def on_press(key):
    global RUNNING
    try:
        if key == keyboard.Key.f2:
            show_xy()
        elif key == keyboard.Key.f3:
            mark_screenshot()
        elif key == keyboard.Key.esc:
            RUNNING = False
            return False
    except Exception as e:
        print(f"Error: {e}")
    return True


def main() -> int:
    print("Mouse XY tool started.")
    print("  F2  = show current X, Y")
    print("  F3  = capture screenshot with red cross at last F2 position")
    print("  Esc = exit")
    print()

    listener = keyboard.Listener(on_press=on_press)
    listener.start()

    try:
        while RUNNING:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        print("Exited.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
