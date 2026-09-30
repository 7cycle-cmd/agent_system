"""Scan *.skill.md files (YAML frontmatter) into task dicts for _render_html.

Optional: pip install pyyaml  (stdlib key:value fallback if missing)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore
    _HAS_YAML = False

from coord_store import (
    get_catalog_by_id,
    get_format_template_by_id,
    get_format_template_by_key,
    get_subcatalog_by_id,
)

_FRONTMATTER_RE = re.compile(
    r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$",
    re.DOTALL,
)
_TRACK_ID_RE = re.compile(r"^(\d+)\.(\d+)([A-Za-z]?)$")


def _parse_simple_frontmatter(yaml_str: str) -> dict[str, Any]:
    """Minimal key: value parser when PyYAML is unavailable."""
    meta: dict[str, Any] = {}
    for line in yaml_str.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if not key:
            continue
        meta[key] = val
    return meta


def _load_meta(yaml_str: str) -> dict[str, Any]:
    if _HAS_YAML:
        data = yaml.safe_load(yaml_str)  # type: ignore[union-attr]
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"frontmatter must be a mapping, got {type(data).__name__}")
        return data
    return _parse_simple_frontmatter(yaml_str)


def _normalize_artifacts(raw: Any, default_name: str) -> list[Any]:
    if raw is None or raw == "":
        return [default_name]
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else [default_name]
    return [raw]


def _normalize_str_list(raw: Any) -> list[Any]:
    """Normalize a frontmatter list field (e.g. modified_files) to a list."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        return [s] if s else []
    return [raw]


def _normalize_str(raw: Any, default: str = "") -> str:
    if raw is None:
        return default
    s = str(raw).strip()
    return s if s else default


def _sort_key(task: dict[str, Any]) -> tuple[Any, ...]:
    tid = str(task.get("task_id") or "")
    m = _TRACK_ID_RE.fullmatch(tid.strip())
    if m:
        return (0, int(m.group(1)), int(m.group(2)), m.group(3) or "", tid)
    return (1, 0, 0, "", tid)


def scan_skill_folder(
    folder_path: str | Path,
    *,
    recursive: bool = True,
) -> list[dict[str, Any]]:
    """
    Scan folder for *.skill.md with YAML frontmatter.
    Default recursive=True (Skill Library subfolders via rglob).
    Returns list[dict] suitable for _render_html(rows=...).
    """
    p = Path(folder_path)
    if not p.is_dir():
        print(f"⚠️  folder not found: {p}", file=sys.stderr)
        return []

    skill_files = sorted(p.rglob("*.skill.md") if recursive else p.glob("*.skill.md"))
    tasks: list[dict[str, Any]] = []

    for file in skill_files:
        try:
            raw_text = file.read_text(encoding="utf-8")
        except OSError as e:
            print(f"⚠️  {file.name}: read failed: {e}", file=sys.stderr)
            continue

        match = _FRONTMATTER_RE.match(raw_text)
        if not match:
            print(f"⚠️  {file.name}: 搵唔到 YAML frontmatter，跳過", file=sys.stderr)
            continue

        yaml_str, prompt_text = match.groups()
        try:
            meta = _load_meta(yaml_str)
        except Exception as e:
            print(f"⚠️  {file.name} YAML解析失敗：{e}", file=sys.stderr)
            continue

        task_id = meta.get("task_id")
        if task_id is None or str(task_id).strip() == "":
            print(f"⚠️  {file.name}: 缺少 task_id，跳過", file=sys.stderr)
            continue

        artifacts = _normalize_artifacts(meta.get("artifacts", ""), file.name)
        modified_files = _normalize_str_list(meta.get("modified_files"))

        # catalog / subcatalog: ID -> DB lookup; fallback to direct names (old format)
        catalog_id = meta.get("catalog_id") or 0
        subcatalog_id = meta.get("subcatalog_id") or 0
        catalog_name = _normalize_str(meta.get("catalog_name"))
        subcatalog_name = _normalize_str(meta.get("subcatalog_name"))
        if catalog_id:
            cat = get_catalog_by_id(int(catalog_id))
            if cat:
                catalog_name = cat["name"]
        if subcatalog_id:
            subcat = get_subcatalog_by_id(int(subcatalog_id))
            if subcat:
                subcatalog_name = subcat["name"]

        # prompt_setting: frontmatter id -> DB; fallback verdict_3line
        template = None
        raw_setting_id = meta.get("prompt_setting_id")
        if raw_setting_id not in (None, ""):
            try:
                template = get_format_template_by_id(int(raw_setting_id))
            except (TypeError, ValueError):
                template = None
        if not template:
            template = get_format_template_by_key("verdict_3line")

        task: dict[str, Any] = {
            "task_id": str(task_id).strip(),
            "name": meta.get("name"),
            "schema": meta.get("schema", "") or "",
            "artifacts": artifacts,
            "prompt": (prompt_text or "").strip(),
            "final_verdict": _normalize_str(meta.get("final_verdict"), "incomplete"),
            "qc_summary": _normalize_str(meta.get("qc_summary"), "Pending run test"),
            "reason": _normalize_str(meta.get("reason"), ""),
            "ingested": _normalize_str(meta.get("ingested"), "no"),
            "modified_files": modified_files,
            "files_modified": modified_files,
            "skill_path": str(file),
            "catalog_id": int(catalog_id),
            "subcatalog_id": int(subcatalog_id),
            "catalog_name": catalog_name,
            "subcatalog_name": subcatalog_name,
            "prompt_setting_id": int(template["id"]) if template else 0,
            "prompt_setting_key": (
                str(template.get("prompt_setting_key") or "")
                if template
                else "verdict_3line"
            ),
            "prompt_setting_instruction": (
                str(template.get("instruction") or "") if template else ""
            ),
        }
        tasks.append(task)

    tasks.sort(key=_sort_key)
    return tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan *.skill.md into task list")
    parser.add_argument(
        "folder",
        nargs="?",
        default="./skills",
        help="skill folder (default: ./skills)",
    )
    parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=True,
        help="scan subdirectories (default: on)",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="only scan the top-level folder",
    )
    args = parser.parse_args(argv)
    recursive = False if args.no_recursive else True
    task_list = scan_skill_folder(args.folder, recursive=recursive)
    print(f"✅ 掃描到 {len(task_list)} 個 skill")
    for t in task_list:
        print(f" - {t['task_id']} | {t.get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
