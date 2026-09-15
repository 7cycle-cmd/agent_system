"""Find Doubao AI icon on screen using OpenClaw Companion snapshot + pyautogui.

Usage:
    c:/projects/agent_system/.venv/Scripts/python.exe find_doubao.py
    c:/projects/agent_system/.venv/Scripts/python.exe find_doubao.py --click
    c:/projects/agent_system/.venv/Scripts/python.exe find_doubao.py --template doubao_icon.png

If no template is provided, this script will use the Companion screen.snapshot
to capture the current screen, then ask you to crop the Doubao icon region.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure project root on path for mcp_client import
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from mcp_client import McpClient, McpConfig, extract_image_bytes  # noqa: E402


def get_snapshot_path() -> Path:
    """Use Companion MCP screen.snapshot to capture current screen."""
    client = McpClient(McpConfig.from_env())
    result = client.call_tool("screen.snapshot", {})
    data, existing_path = extract_image_bytes(result)
    if existing_path and Path(existing_path).is_file():
        return Path(existing_path)
    if not data:
        raise RuntimeError("screen.snapshot returned no image bytes")
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="oc_snapshot_")
    os.close(fd)
    path = Path(tmp)
    path.write_bytes(data)
    return path


def save_template_from_snapshot(snapshot_path: Path, output_path: Path) -> Path:
    """Interactive crop: show snapshot and let user pick icon region."""
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("Pillow is required for cropping") from e

    img = Image.open(snapshot_path)
    print(f"Snapshot size: {img.size}")
    print(
        "Enter crop box as: left top right bottom (e.g. 1800 1020 1860 1060)"
    )
    print("Tip: use an image viewer to estimate coordinates, then type them here.")
    box_str = input("crop box: ").strip()
    if not box_str:
        raise RuntimeError("No crop box provided")
    parts = [int(x.strip()) for x in box_str.split()]
    if len(parts) != 4:
        raise ValueError("crop box must have 4 integers")
    left, top, right, bottom = parts
    cropped = img.crop((left, top, right, bottom))
    cropped.save(output_path)
    print(f"Saved template to {output_path} ({cropped.size})")
    return output_path


def find_icon(template_path: Path, confidence: float = 0.55) -> tuple[int, int] | None:
    """Return center (x, y) of template on current screen, or None."""
    import pyautogui

    if not template_path.is_file():
        raise FileNotFoundError(f"Template not found: {template_path}")

    location = pyautogui.locateOnScreen(
        str(template_path),
        confidence=confidence,
        grayscale=True,
    )
    if location is None:
        return None
    x, y = pyautogui.center(location)
    return int(x), int(y)


def main() -> int:
    parser = argparse.ArgumentParser(description="Find Doubao AI icon on screen")
    parser.add_argument(
        "--template",
        default=str(BASE_DIR / "doubao_icon.png"),
        help="Path to template PNG (default: doubao_icon.png)",
    )
    parser.add_argument(
        "--create-template",
        action="store_true",
        help="Capture snapshot and interactively create template",
    )
    parser.add_argument(
        "--click",
        action="store_true",
        help="Click the icon if found",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.55,
        help="Template matching confidence (0-1, default 0.55)",
    )
    args = parser.parse_args()

    template_path = Path(args.template)

    if args.create_template or not template_path.is_file():
        print("Creating template from Companion screen.snapshot...")
        snapshot = get_snapshot_path()
        print(f"Snapshot saved: {snapshot}")
        save_template_from_snapshot(snapshot_path=snapshot, output_path=template_path)

    print(f"Looking for Doubao icon with template: {template_path}")
    pos = find_icon(template_path, confidence=args.confidence)
    if pos is None:
        print("Icon not found. Try lowering --confidence or recapturing template.")
        return 1

    x, y = pos
    print(f"Found Doubao icon at center: X={x}, Y={y}")

    if args.click:
        import pyautogui

        pyautogui.click(x, y)
        print(f"Clicked at ({x}, {y})")
        time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
