"""Generic OpenClaw + Playwright DB-driven task runner.

Reads steps from the `browser_task_steps` table (grouped by task_id, ordered by
step_order) and executes them sequentially. Reusable across many tasks — just
insert a new task_id's steps into the DB, no code changes needed.

Supported actions:
    navigate            -> page.goto(target_ref)
    click               -> target_ref (CSS/ref) first, else x,y coordinates
    type                -> fill target_ref with input_text
    pause               -> human intervention (e.g. captcha); press Enter to continue
    wait                -> sleep(wait_condition seconds)
    screenshot          -> save page screenshot to target_ref path
    vision_captcha_solve-> local VL model auto-solves grid image CAPTCHA
                           (target_ref = captcha grid locator, input_text = VL prompt)
                           3 retries, fail-safe fallback to manual pause

Usage:
    python browser_task_runner.py <task_id> [--headed]
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any

DB_PATH = "agent.db"

# Local VL model API (Ollama OpenAI-compatible endpoint, qwen2.5vl:7b)
VL_API_URL = "http://127.0.0.1:11434/v1/chat/completions"
VL_MODEL = "qwen2.5vl:7b"


def vl_image_infer(image_b64: str, prompt: str) -> dict:
    """Call local VL model; expect JSON {"boxes": [[x1,y1,x2,y2],...]} in the reply."""
    import requests

    payload = {
        "model": VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": 0.0,
    }
    resp = requests.post(VL_API_URL, json=payload, timeout=120)
    resp.raise_for_status()
    raw_text = resp.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        raise Exception(f"VL model returned no valid JSON: {raw_text[:300]}")
    return json.loads(match.group(0))


def vl_text_infer(image_b64: str, prompt: str) -> str:
    """Call local VL model, return the raw text reply (no JSON parsing)."""
    import requests

    payload = {
        "model": VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": 0.0,
    }
    resp = requests.post(VL_API_URL, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def capture_element_screenshot(page: Any, locator: Any) -> str:
    """Screenshot the given element, return base64 PNG."""
    return base64.b64encode(locator.screenshot()).decode("utf-8")


def split_grid_3x3(image_b64: str) -> list[str]:
    """Split a 3x3 grid image into 9 cell images (reading order 1-9), base64."""
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(base64.b64decode(image_b64)))
    w, h = img.size
    cw, ch = w / 3, h / 3
    cells = []
    for row in range(3):
        for col in range(3):
            cell = img.crop((col * cw, row * ch, (col + 1) * cw, (row + 1) * ch))
            # Upscale 3x so the 7B VL model can see details clearly
            cell = cell.resize((cell.width * 3, cell.height * 3), Image.LANCZOS)
            buf = io.BytesIO()
            cell.save(buf, format="PNG")
            cells.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
    return cells


def load_task(task_id: str) -> list[dict[str, Any]]:
    """Load steps for a task, ordered by step_order."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT * FROM browser_task_steps "
            "WHERE task_id=? ORDER BY step_order ASC",
            (task_id,),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _click(page: Any, step: dict[str, Any]) -> None:
    """Click by target_ref (CSS/ref) first, else x,y coordinates."""
    ref = (step.get("target_ref") or "").strip()
    if ref:
        page.locator(ref).click(timeout=10000)
    else:
        x = int(step.get("x") or 0)
        y = int(step.get("y") or 0)
        page.mouse.click(x, y)


def _click_begin_if_present(page: Any) -> None:
    """Click the Amazon CAPTCHA '开始'/'Begin' button to reveal the grid."""
    for sel in (
        "button.amzn-captcha-verify-button",
        "button:has-text('开始')",
        "button:has-text('Begin')",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                print(f"  clicked begin button: {sel}")
                page.wait_for_timeout(2500)
                return
        except Exception:
            continue


def _vision_captcha_solve(page: Any, step: dict[str, Any]) -> None:
    """Auto-solve the Amazon 3x3 grid image CAPTCHA via local VL model.

    Flow:
      1. click '开始'/'Begin' to reveal the grid (idempotent)
      2. screenshot the canvas, split into 9 cells, ask VL each cell YES/NO
      3. click each matching cell center on the canvas, then click '确认'
    target_ref = locator of the captcha modal (default div.amzn-captcha-modal)
    input_text = VL prompt (e.g. "find all hats, return JSON cells only")
    3 retries; on total failure fall back to a manual pause (fail-safe).
    """
    modal = page.locator(step.get("target_ref") or "div.amzn-captcha-modal")
    max_retry = 3
    solved = False
    for attempt in range(1, max_retry + 1):
        try:
            # Stage 1: reveal the grid (no-op if already shown)
            _click_begin_if_present(page)
            modal.wait_for(state="visible", timeout=15000)
            page.wait_for_timeout(1500)

            # Read the target object from the <em> tag (e.g. "椅子" = chairs)
            target = modal.locator("em").first.inner_text(timeout=5000).strip()
            print(f"  CAPTCHA attempt {attempt}/{max_retry}: target={target!r}")
            # Screenshot the canvas only (pure 3x3 grid), split into 9 cells
            canvas = page.locator("canvas").first
            grid_b64 = capture_element_screenshot(page, canvas)
            cell_imgs = split_grid_3x3(grid_b64)
            # Ask the VL each cell individually: YES or NO
            cells = []
            for i, cell_b64 in enumerate(cell_imgs, start=1):
                ans = vl_text_infer(
                    cell_b64,
                    f"呢张图入面有冇「{target}」？只回答 YES 或者 NO，唔好其他文字",
                )
                raw = ans.upper()
                yes = "YES" in raw and "NO" not in raw.replace("YES", "")
                print(f"    cell {i}: {'YES' if yes else 'no'} ({ans.strip()[:40]!r})")
                if yes:
                    cells.append(i)
            if not cells:
                print("  VL found no cells, retrying")
                continue
            print(f"  VL selected cells: {cells}")
            # The grid is a canvas; numbered buttons are hidden overlays.
            # Click the canvas at each selected cell's center (3x3, reading order).
            box = canvas.bounding_box()
            cw, ch = box["width"] / 3, box["height"] / 3
            for n in cells:
                n = int(n)
                col, row = (n - 1) % 3, (n - 1) // 3
                cx = box["x"] + (col + 0.5) * cw
                cy = box["y"] + (row + 0.5) * ch
                page.mouse.click(cx, cy)
                time.sleep(0.4)
            # Stage 3: confirm the selection
            page.locator(
                "button:has-text('确认'), button:has-text('Confirm'), "
                "button:has-text('验证'), button:has-text('Verify')"
            ).first.click(timeout=8000)
            page.wait_for_timeout(3000)
            # Verify: modal gone (or form visible) = solved
            if modal.count() == 0 or not modal.is_visible():
                print("  CAPTCHA solved: modal dismissed")
                solved = True
                break
            print("  CAPTCHA rejected, new challenge loaded, retrying")
        except Exception as e:
            print(f"  CAPTCHA attempt {attempt} failed: {e}")
            time.sleep(1.5)
    if not solved:
        print("  CAPTCHA auto-solve failed, falling back to manual pause")
        input(f"CAPTCHA auto-solve failed: {step.get('remark') or ''}\n"
              f"Solve it manually, then press Enter to continue...")


def hf_ping_precheck() -> tuple[bool, str]:
    """hf_registry 執行前先 ping，偵測 403 限流 (cooldown).

    return (ok: bool, msg: str)
    """
    try:
        import requests

        # 簡單輕量 HEAD 測試，唔消耗大量流量
        resp = requests.head("https://huggingface.co", timeout=8)
        if resp.status_code == 403:
            return False, "HF 403 Forbidden，處於 cooldown 限流，暫時唔可以執行 hf_registry"
        return True, "HF 連線正常，可以執行註冊"
    except Exception as e:
        return False, f"HF 連線異常：{e}"


def _load_env() -> None:
    """Load .env into os.environ (without printing secrets)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(env_path):
        return
    for line in open(env_path, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def hf_registry(headed: bool = True) -> int:
    """Run the HuggingFace registration flow via Playwright.

    Reads HF_EMAIL / HF_USERNAME / HF_PASSWORD from .env. If credentials are
    present, auto-fills the join form and auto-solves the Amazon CAPTCHA via the
    local VL model. Otherwise opens the join page for manual registration.

    Returns process exit code (0 = ok, 1 = failed).
    """
    _load_env()
    email = os.environ.get("HF_EMAIL", "").strip()
    username = os.environ.get("HF_USERNAME", "").strip()
    password = os.environ.get("HF_PASSWORD", "").strip()

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            page = browser.new_page()
            page.goto("https://huggingface.co/join", wait_until="networkidle")
            page.wait_for_timeout(3000)

            if email and username and password:
                # Auto-fill the join form
                page.locator("input[name='email']").first.fill(email, timeout=10000)
                page.locator("input[name='username']").first.fill(username, timeout=10000)
                page.locator("input[name='password']").first.fill(password, timeout=10000)
                print("HF join form filled from .env")

                # Auto-solve the Amazon CAPTCHA via local VL model
                _vision_captcha_solve(
                    page,
                    {
                        "target_ref": "div.amzn-captcha-modal",
                        "input_text": "find all target objects, return JSON cells only",
                        "remark": "HF registration CAPTCHA",
                    },
                )

                # Submit the form
                page.locator(
                    "button[type='submit'], button:has-text('Create Account'), "
                    "button:has-text('注册'), button:has-text('创建账户')"
                ).first.click(timeout=10000)
                page.wait_for_timeout(5000)
                print("HF registration submitted")
            else:
                print("HF_EMAIL / HF_USERNAME / HF_PASSWORD 未喺 .env 提供 — 手動註冊模式")
                print("請喺瀏覽器完成註冊，完成後返嚟呢個 terminal 撳 Enter...")
                input("完成註冊後撳 Enter 繼續...")

            browser.close()
        print("hf_registry 完成")
        return 0
    except Exception as e:
        print(f"hf_registry 失敗：{type(e).__name__}: {e}")
        return 1


def run_task(task_id: str, headless: bool = True) -> None:
    """Execute all steps for a task via Playwright."""
    steps = load_task(task_id)
    if not steps:
        raise Exception(f"task {task_id} not found in browser_task_steps")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        for step in steps:
            action = (step.get("action_type") or "").strip()
            order = step.get("step_order")
            remark = step.get("remark") or ""
            print(f"Step {order}: {action}" + (f" ({remark})" if remark else ""))

            if action == "navigate":
                page.goto(step.get("target_ref"), wait_until="networkidle")
            elif action == "click":
                _click(page, step)
            elif action == "type":
                ref = (step.get("target_ref") or "").strip()
                text = step.get("input_text") or ""
                if ref:
                    page.locator(ref).fill(text)
                else:
                    page.keyboard.type(text)
            elif action == "pause":
                input(f"PAUSE: {remark or 'human intervention needed'}, "
                      f"press Enter to continue...")
            elif action == "wait":
                time.sleep(float(step.get("wait_condition") or 1))
            elif action == "screenshot":
                path = step.get("target_ref") or f"shot_{task_id}_{order}.png"
                page.screenshot(path=path)
            elif action == "vision_captcha_solve":
                _vision_captcha_solve(page, step)
            else:
                print(f"  [warn] unknown action_type: {action!r}")

        browser.close()
    print(f"Task {task_id} completed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python browser_task_runner.py <task_id> [--headed]")
        print("       python browser_task_runner.py hf_registry [--headed]")
        sys.exit(1)
    sub = sys.argv[1]
    headed = "--headed" in sys.argv

    if sub == "hf_registry":
        # HF 前置 ping 檢查：403 cooldown 時攔截，唔執行註冊
        ok, msg = hf_ping_precheck()
        print(msg)
        if not ok:
            sys.exit(1)
        # 執行正式 HF 註冊流程
        sys.exit(hf_registry(headed=headed))

    run_task(sub, headless=not headed)