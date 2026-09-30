"""Watch ./skills for *.skill.md changes and re-run test_render.py (debounced).

Note: repo root has watchdog.py (app health). This script imports the PyPI
`watchdog` package from site-packages by temporarily dropping the project
root from sys.path.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import subprocess
from datetime import datetime
from pathlib import Path

# Root-cause cp950 emoji crash: force UTF-8 stdout/stderr so emoji prints
# never raise UnicodeEncodeError, regardless of PYTHONIOENCODING env.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from skill_field_registry import scan_all_skill_fields

ROOT = Path(__file__).resolve().parent
SKILL_FOLDER = (ROOT / "skills").resolve()
TARGET_SCRIPT = (ROOT / "test_render.py").resolve()
DEBOUNCE_DELAY = 0.5  # trailing quiet window (seconds)
HEARTBEAT_INTERVAL = 60  # seconds between heartbeat writes


def _import_pypi_watchdog():
    """Import PyPI watchdog, avoiding shadow by local watchdog.py."""
    root_s = str(ROOT)
    saved = list(sys.path)
    try:
        sys.path = [
            p
            for p in sys.path
            if p not in ("", root_s) and str(Path(p).resolve()) != root_s
        ]
        # Drop any half-imported local module
        for k in list(sys.modules):
            if k == "watchdog" or k.startswith("watchdog."):
                del sys.modules[k]
        from watchdog.events import FileSystemEventHandler  # type: ignore
        from watchdog.observers import Observer  # type: ignore

        return Observer, FileSystemEventHandler
    finally:
        sys.path = saved


Observer, FileSystemEventHandler = _import_pypi_watchdog()


def _is_skill_md(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".skill.md")


def _run_with_retry(fn, attempts: int = 3, base_delay: float = 1.0):
    """Run fn with retry + exponential backoff on sqlite 'database is locked'.

    Returns fn() result, or None if all attempts fail.
    """
    import sqlite3
    import time

    last_err = None
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last_err = e
            delay = base_delay * (2 ** i)
            print(f"⚠️  database locked（{e}），{delay:.0f}s 後重試（{i + 1}/{attempts}）")
            time.sleep(delay)
    print(f"❌ database locked 重試 {attempts} 次後仍失敗: {last_err}")
    return None


class SkillFileHandler(FileSystemEventHandler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending_name = ""

    def on_modified(self, event):  # noqa: N802
        self._schedule(event)

    def on_created(self, event):  # noqa: N802
        self._schedule(event)

    def on_deleted(self, event):  # noqa: N802
        self._schedule(event)

    def on_moved(self, event):  # noqa: N802
        self._schedule(event)

    def _schedule(self, event) -> None:
        if getattr(event, "is_directory", False):
            return
        src = getattr(event, "src_path", None) or ""
        dest = getattr(event, "dest_path", None) or ""
        paths = [Path(src)] if src else []
        if dest:
            paths.append(Path(dest))
        skill_paths = [p for p in paths if _is_skill_md(p)]
        if not skill_paths:
            return

        name = skill_paths[0].name
        with self._lock:
            self._pending_name = name
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_DELAY, self._run_render)
            self._timer.daemon = True
            self._timer.start()

    def _run_render(self) -> None:
        with self._lock:
            name = self._pending_name or "*.skill.md"
            self._timer = None
        print(f"\n📝 偵測到變動: {name} → 開始處理（防抖 {DEBOUNCE_DELAY}s）")
        # Step1：更新 field_registry schema
        try:
            scan_all_skill_fields()
            print("✅ field_registry schema updated")
        except Exception as e:
            print(f"❌ scan_all_skill_fields 失敗: {e}")
        # Step1.5：同步 skill → dev_task（新增/更新/刪除）
        # 用 retry + backoff 處理 SQLite "database is locked"（並發寫入場景）
        def _sync_once():
            import sqlite3
            from db_schema import get_db_path
            from test_render import sync_skills_to_dev_tasks

            conn = sqlite3.connect(get_db_path())
            conn.row_factory = sqlite3.Row
            try:
                return sync_skills_to_dev_tasks(conn, str(SKILL_FOLDER))
            finally:
                conn.close()

        sync_result = _run_with_retry(_sync_once, attempts=3, base_delay=1.0)
        if sync_result is not None:
            print(
                f"✅ skill→dev_task 同步: created={sync_result['created']} "
                f"skipped={sync_result['skipped']} total={sync_result['total']}"
            )
        else:
            print("❌ skill→dev_task 同步失敗（重試 3 次後仍失敗）")
        # Step2：原有流程，重新渲染HTML（--no-sync：Step1.5 已同步，避免雙重 sync）
        try:
            proc = subprocess.run(
                [sys.executable, str(TARGET_SCRIPT), "--no-sync"],
                cwd=str(ROOT),
                check=False,
            )
            if proc.returncode == 0:
                print("✅ 渲染完成，HTML檔已更新！")
            else:
                print(f"❌ test_render.py exit code {proc.returncode}")
        except Exception as e:
            print(f"❌ 執行 test_render.py 失敗: {e}")


def main() -> int:
    if not SKILL_FOLDER.is_dir():
        print(f"❌ skills folder not found: {SKILL_FOLDER}")
        return 1
    if not TARGET_SCRIPT.is_file():
        print(f"❌ missing {TARGET_SCRIPT}")
        return 1

    # 寫 PID file（健康檢查用）
    pid_file = ROOT / "watch_skills.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    # 心跳 file（定期更新 timestamp，證明 watcher 仲生）
    hb_file = ROOT / "watch_skills_heartbeat.txt"

    def _heartbeat() -> None:
        try:
            hb_file.write_text(
                f"alive {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} pid={os.getpid()}",
                encoding="utf-8",
            )
        except Exception as e:
            print(f"⚠️  heartbeat 寫入失敗: {e}")

    event_handler = SkillFileHandler()
    observer = Observer()
    # Skill Library: watch all nested folders for *.skill.md
    observer.schedule(event_handler, str(SKILL_FOLDER), recursive=True)
    observer.start()
    print(f"👀 監聽器已啟動（trailing 防抖 {DEBOUNCE_DELAY}s，recursive=True）")
    print(f"監控資料夾：{SKILL_FOLDER} (含子資料夾)")
    print(f"渲染腳本：{TARGET_SCRIPT}")
    print(f"Python：{sys.executable}")
    print(f"PID file: {pid_file}")
    print(f"Heartbeat file: {hb_file}")
    print("等候 skill 檔變動... (按 Ctrl+C 停止監聽)")
    _heartbeat()
    try:
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            _heartbeat()
    except KeyboardInterrupt:
        print("\n停止監聽…")
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
