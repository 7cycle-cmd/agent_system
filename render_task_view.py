"""
Render static task_view.html from qc_evidence plan/agent_log/qc_report JSON.

CLI:
  --render   scan qc_evidence and write task_view.html at project root
  --open     render then open task_view.html in the default browser
  --help     show help

No database. No JavaScript. Does not modify qc_evidence artifacts.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
QC_EVIDENCE = BASE_DIR / "qc_evidence"
OUT_HTML = BASE_DIR / "task_view.html"
MARKER_PATH = QC_EVIDENCE / ".ingested_tasks.json"

FILE_RE = re.compile(r"^(plan|agent_log|qc_report)_(.+)\.json$", re.IGNORECASE)
KINDS = ("plan", "agent_log", "qc_report")


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"json_error: {e}"
    if not isinstance(data, dict):
        return None, "json_root_not_object"
    return data, None


def _load_marker_ids() -> set[str]:
    if not MARKER_PATH.is_file():
        return set()
    try:
        raw = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if isinstance(raw, list):
        return {str(x) for x in raw}
    if not isinstance(raw, dict):
        return set()
    ids = raw.get("task_ids") or raw.get("ingested") or []
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids}


def _discover_groups() -> dict[str, dict[str, Path]]:
    """Top-level qc_evidence only: kind -> JSON path per task_id."""
    groups: dict[str, dict[str, Path]] = {}
    if not QC_EVIDENCE.is_dir():
        return groups
    for p in QC_EVIDENCE.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json":
            continue
        if p.name.startswith("."):
            continue
        m = FILE_RE.match(p.name)
        if not m:
            continue
        kind = m.group(1).lower()
        tid = m.group(2)
        groups.setdefault(tid, {})[kind] = p
    return groups


def _md_path(kind: str, task_id: str) -> Path | None:
    p = QC_EVIDENCE / f"{kind}_{task_id}.md"
    return p if p.is_file() else None


def _normalize_files_modified(value: Any) -> list[str]:
    if value is None:
        return []
    out: list[str] = []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if not isinstance(value, list):
        return [str(value)]
    for item in value:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, dict):
            path = item.get("path") or item.get("file") or item.get("name")
            if path:
                out.append(str(path))
            else:
                out.append(json.dumps(item, ensure_ascii=False, default=str))
        else:
            out.append(str(item))
    # de-dupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _qc_summary(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "—"
    counts: Counter[str] = Counter()
    for it in items:
        if not isinstance(it, dict):
            counts["OTHER"] += 1
            continue
        r = str(it.get("result") or "OTHER").strip().upper() or "OTHER"
        counts[r] += 1
    parts = []
    for key in ("PASS", "FAIL", "SKIP"):
        if counts.get(key):
            parts.append(f"{key[0]}:{counts[key]}")
    other = sum(v for k, v in counts.items() if k not in ("PASS", "FAIL", "SKIP"))
    if other:
        parts.append(f"O:{other}")
    total = sum(counts.values())
    return f"{total} items (" + " ".join(parts) + ")" if parts else f"{total} items"


def _verdict_class(verdict: str) -> str:
    v = (verdict or "").strip().upper()
    if v == "PASS":
        return "verdict-pass"
    if v == "FAIL":
        return "verdict-fail"
    return "verdict-other"


def _rel_href(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(BASE_DIR.resolve())
    except ValueError:
        rel = path
    return str(rel).replace("\\", "/")


def _build_rows() -> list[dict[str, Any]]:
    ingested = _load_marker_ids()
    groups = _discover_groups()
    rows: list[dict[str, Any]] = []
    for task_id in sorted(groups.keys()):
        paths = groups[task_id]
        plan_data, plan_err = (None, None)
        agent_data, agent_err = (None, None)
        qc_data, qc_err = (None, None)
        if "plan" in paths:
            plan_data, plan_err = _load_json(paths["plan"])
        if "agent_log" in paths:
            agent_data, agent_err = _load_json(paths["agent_log"])
        if "qc_report" in paths:
            qc_data, qc_err = _load_json(paths["qc_report"])

        final_verdict = ""
        checklist_items: Any = None
        if isinstance(qc_data, dict):
            final_verdict = str(qc_data.get("final_verdict") or "").strip()
            checklist_items = qc_data.get("checklist_items")

        files_mod: list[str] = []
        if isinstance(agent_data, dict):
            files_mod = _normalize_files_modified(agent_data.get("files_modified"))

        kinds_present = sorted(paths.keys())
        incomplete = any(k not in paths for k in KINDS)
        load_errors = [e for e in (plan_err, agent_err, qc_err) if e]

        artifacts: list[dict[str, str]] = []
        for kind in KINDS:
            if kind in paths:
                artifacts.append(
                    {
                        "label": f"{kind}.json",
                        "href": _rel_href(paths[kind]),
                    }
                )
            md = _md_path(kind, task_id)
            if md is not None:
                artifacts.append(
                    {
                        "label": f"{kind}.md",
                        "href": _rel_href(md),
                    }
                )

        rows.append(
            {
                "task_id": task_id,
                "final_verdict": final_verdict or ("incomplete" if incomplete else "—"),
                "verdict_raw": final_verdict,
                "ingested": "yes" if task_id in ingested else "no",
                "files_modified": files_mod,
                "qc_summary": _qc_summary(checklist_items),
                "artifacts": artifacts,
                "kinds_present": kinds_present,
                "incomplete": incomplete,
                "load_errors": load_errors,
            }
        )
    return rows


def _render_html(rows: list[dict[str, Any]]) -> str:
    esc = html.escape
    body_rows: list[str] = []
    for r in rows:
        tid = esc(r["task_id"])
        verdict = esc(str(r["final_verdict"]))
        vclass = _verdict_class(str(r.get("verdict_raw") or r["final_verdict"]))
        ingested = esc(str(r["ingested"]))
        ing_class = "ingested-yes" if r["ingested"] == "yes" else "ingested-no"
        files = r["files_modified"] or []
        if files:
            files_html = "<ul class=\"file-list\">" + "".join(
                f"<li><code>{esc(f)}</code></li>" for f in files[:40]
            )
            if len(files) > 40:
                files_html += f"<li><em>+{len(files) - 40} more</em></li>"
            files_html += "</ul>"
        else:
            files_html = "<span class=\"muted\">—</span>"
        qc_sum = esc(str(r["qc_summary"]))
        links = []
        for a in r["artifacts"]:
            href = esc(a["href"], quote=True)
            label = esc(a["label"])
            links.append(f'<a href="{href}">{label}</a>')
        arts = " · ".join(links) if links else '<span class="muted">—</span>'
        note = ""
        if r.get("incomplete"):
            note = ' <span class="badge-incomplete">incomplete triple</span>'
        if r.get("load_errors"):
            note += f' <span class="badge-error">{esc("; ".join(r["load_errors"]))}</span>'

        body_rows.append(
            f"""
        <tr>
          <td class="tid"><code>{tid}</code>{note}</td>
          <td class="{vclass}">{verdict}</td>
          <td class="{ing_class}">{ingested}</td>
          <td class="files">{files_html}</td>
          <td class="qc">{qc_sum}</td>
          <td class="arts">{arts}</td>
        </tr>"""
        )

    generated_note = esc(f"{len(rows)} task(s) from qc_evidence JSON groups")
    table_body = "\n".join(body_rows) if body_rows else (
        '<tr><td colspan="6" class="muted">No plan/agent_log/qc_report JSON found.</td></tr>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Task view — qc_evidence</title>
  <style>
    :root {{
      --bg: #0f1419;
      --panel: #1a2332;
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --border: #2c3a4f;
      --pass: #1b7f4e;
      --pass-bg: #143d2a;
      --fail: #c62828;
      --fail-bg: #3d1515;
      --link: #6cb6ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
      padding: 1.5rem;
    }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.25rem; }}
    .sub {{ color: var(--muted); margin-bottom: 1.25rem; font-size: 0.9rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      font-size: 0.9rem;
    }}
    th, td {{
      border-bottom: 1px solid var(--border);
      padding: 0.65rem 0.75rem;
      vertical-align: top;
      text-align: left;
    }}
    th {{
      background: #121a26;
      color: var(--muted);
      font-weight: 600;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    tr:last-child td {{ border-bottom: none; }}
    code {{
      font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
      font-size: 0.85em;
    }}
    a {{ color: var(--link); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .verdict-pass {{
      color: #7dffa6;
      background: var(--pass-bg);
      font-weight: 700;
    }}
    .verdict-fail {{
      color: #ff8a80;
      background: var(--fail-bg);
      font-weight: 700;
    }}
    .verdict-other {{ color: var(--muted); }}
    .ingested-yes {{ color: #7dffa6; }}
    .ingested-no {{ color: var(--muted); }}
    .muted {{ color: var(--muted); }}
    .file-list {{ margin: 0; padding-left: 1.1rem; }}
    .file-list li {{ margin: 0.1rem 0; }}
    .badge-incomplete, .badge-error {{
      display: inline-block;
      font-size: 0.7rem;
      padding: 0.1rem 0.35rem;
      border-radius: 4px;
      margin-left: 0.35rem;
      font-weight: 600;
    }}
    .badge-incomplete {{ background: #3d3420; color: #ffcc66; }}
    .badge-error {{ background: var(--fail-bg); color: #ff8a80; }}
    .arts {{ white-space: normal; max-width: 18rem; }}
  </style>
</head>
<body>
  <h1>Task view</h1>
  <p class="sub">Static index of <code>qc_evidence/</code> plan / agent_log / qc_report triples. {generated_note}. No server required.</p>
  <table>
    <thead>
      <tr>
        <th>task_id</th>
        <th>final_verdict</th>
        <th>ingested</th>
        <th>modified files</th>
        <th>QC summary</th>
        <th>artifacts</th>
      </tr>
    </thead>
    <tbody>
{table_body}
    </tbody>
  </table>
</body>
</html>
"""


def cmd_render() -> int:
    rows = _build_rows()
    html_text = _render_html(rows)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUT_HTML),
                "task_count": len(rows),
                "task_ids": [r["task_id"] for r in rows],
            },
            indent=2,
        )
    )
    return 0


def cmd_open() -> int:
    rc = cmd_render()
    if rc != 0:
        return rc
    webbrowser.open(OUT_HTML.resolve().as_uri())
    print(json.dumps({"opened": str(OUT_HTML.resolve())}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render static task_view.html from qc_evidence JSON triples (no DB, no JS)."
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help="scan qc_evidence and write task_view.html at project root",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="render HTML then open task_view.html in the default browser",
    )
    args = parser.parse_args(argv)

    if args.open:
        return cmd_open()
    if args.render:
        return cmd_render()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
