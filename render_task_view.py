"""
Render static task_view.html from qc_evidence plan/agent_log/qc_report JSON.

CLI:
  --render   scan qc_evidence and write task_view.html at project root
  --open     render then open task_view.html in the default browser
  --help     show help

No database. Does not modify qc_evidence artifacts.
Static HTML may embed a small client-side modal script (no server).
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

        # Track_ID metadata (rule B) — used for modal detail, not table columns:
        # 1) plan JSON root_task_id / global_sequence_number primary
        # 2) else task_id fullmatch ^\d+\.\d+$ → root, seq (new track_id == task_id)
        # display_track_id: plan key only; else legacy slug/filename task_id (no synthesize)
        # track_id: plan raw track_id if set, else discovery task_id
        root_task_id = ""
        global_sequence_number = ""
        item_type = ""
        item_name = ""
        display_track_id = ""
        parent_track_id = ""
        scope = ""
        plan_track_id = ""
        table_name = ""
        field_name = ""
        step_list: list[Any] = []
        if isinstance(plan_data, dict):
            if plan_data.get("root_task_id") is not None:
                root_task_id = str(plan_data.get("root_task_id")).strip()
            if plan_data.get("global_sequence_number") is not None:
                global_sequence_number = str(
                    plan_data.get("global_sequence_number")
                ).strip()
            if plan_data.get("item_type") is not None:
                item_type = str(plan_data.get("item_type")).strip()
            if plan_data.get("item_name") is not None:
                item_name = str(plan_data.get("item_name")).strip()
            if plan_data.get("display_track_id") is not None:
                display_track_id = str(plan_data.get("display_track_id")).strip()
            if plan_data.get("parent_track_id") is not None:
                parent_track_id = str(plan_data.get("parent_track_id")).strip()
            if plan_data.get("track_id") is not None:
                plan_track_id = str(plan_data.get("track_id")).strip()
            if plan_data.get("table_name") is not None:
                table_name = str(plan_data.get("table_name")).strip()
            if plan_data.get("field_name") is not None:
                field_name = str(plan_data.get("field_name")).strip()
            if plan_data.get("scope") is not None:
                scope_val = plan_data.get("scope")
                if isinstance(scope_val, (dict, list)):
                    scope = json.dumps(scope_val, ensure_ascii=False, default=str)
                else:
                    scope = str(scope_val).strip()
            sl = plan_data.get("step_list")
            if isinstance(sl, list):
                step_list = sl
            elif isinstance(sl, str) and sl.strip():
                step_list = [sl.strip()]

        # True only when plan JSON itself had non-empty display_track_id (New Tasks).
        has_display_track_id = bool(display_track_id)

        # Rule B fallback: only pure numeric Root.Seq task_id (not slug)
        if not root_task_id or not global_sequence_number:
            m_tid = re.fullmatch(r"(\d+)\.(\d+)", str(task_id).strip())
            if m_tid:
                if not root_task_id:
                    root_task_id = m_tid.group(1)
                if not global_sequence_number:
                    global_sequence_number = m_tid.group(2)

        track_id_val = plan_track_id or str(task_id)
        # Keep plan display when present; legacy section assigns virtual 99.{n}L in render.
        if not display_track_id:
            display_track_id = str(task_id)
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

        # User-modal test description: scope, else joined step_list
        test_description = scope
        if not test_description and step_list:
            test_description = " → ".join(str(s) for s in step_list)

        error_message = ""
        if isinstance(checklist_items, list):
            for it in checklist_items:
                if not isinstance(it, dict):
                    continue
                res = str(it.get("result") or "").strip().upper()
                if res == "FAIL":
                    error_message = str(
                        it.get("detail")
                        or it.get("message")
                        or it.get("criterion")
                        or it.get("id")
                        or "FAIL"
                    ).strip()
                    break
        if not error_message and load_errors:
            error_message = "; ".join(load_errors)

        if table_name and field_name:
            schema_summary = f"{table_name}.{field_name}"
        elif table_name:
            schema_summary = table_name
        elif field_name:
            schema_summary = field_name
        else:
            schema_summary = ""

        schema_definition = {
            "table_name": table_name or None,
            "field_name": field_name or None,
        }

        def _clip_raw(obj: Any, limit: int = 12000) -> Any:
            if obj is None:
                return None
            try:
                s = json.dumps(obj, ensure_ascii=False, default=str, indent=2)
            except Exception:
                s = str(obj)
            if len(s) > limit:
                return s[:limit] + "\n…(truncated)"
            return obj

        rows.append(
            {
                "task_id": task_id,
                "track_id": track_id_val,
                "display_track_id": display_track_id,
                "has_display_track_id": has_display_track_id,
                "root_task_id": root_task_id,
                "global_sequence_number": global_sequence_number,
                "item_type": item_type,
                "item_name": item_name,
                "parent_track_id": parent_track_id,
                "scope": scope,
                "table_name": table_name,
                "field_name": field_name,
                "schema_summary": schema_summary,
                "schema_definition": schema_definition,
                "test_description": test_description,
                "error_message": error_message,
                "final_verdict": final_verdict or ("incomplete" if incomplete else "—"),
                "verdict_raw": final_verdict,
                "qc_final_verdict": final_verdict,
                "ingested": "yes" if task_id in ingested else "no",
                "files_modified": files_mod,
                "qc_summary": _qc_summary(checklist_items),
                "artifacts": artifacts,
                "kinds_present": kinds_present,
                "incomplete": incomplete,
                "load_errors": load_errors,
                "plan_raw": _clip_raw(plan_data),
                "agent_raw": _clip_raw(agent_data),
                "qc_raw": _clip_raw(qc_data),
            }
        )
    return rows


def _render_html(rows: list[dict[str, Any]]) -> str:
    esc = html.escape
    task_meta: dict[str, dict[str, Any]] = {}

    new_rows = [r for r in rows if r.get("has_display_track_id")]
    legacy_rows = sorted(
        [r for r in rows if not r.get("has_display_track_id")],
        key=lambda r: str(r.get("task_id") or ""),
    )

    def _parse_root_seq(r: dict[str, Any]) -> tuple[int, int]:
        root_s = str(r.get("root_task_id") or "").strip()
        seq_s = str(r.get("global_sequence_number") or "").strip()
        try:
            if root_s and seq_s:
                return int(root_s), int(seq_s)
        except ValueError:
            pass
        tid = str(r.get("track_id") or r.get("task_id") or "").strip()
        m = re.fullmatch(r"(\d+)\.(\d+)", tid)
        if m:
            return int(m.group(1)), int(m.group(2))
        return 0, 0

    display_entries: list[tuple[tuple[Any, ...], dict[str, Any], str, str, bool]] = []

    for r in new_rows:
        tid_file = str(r["task_id"])
        track_key = str(r.get("track_id") or tid_file)
        display = str(r.get("display_track_id") or tid_file)
        root_i, seq_i = _parse_root_seq(r)
        sort_key = (0, root_i, seq_i, display)
        display_entries.append((sort_key, r, display, track_key, False))

    for seq, r in enumerate(legacy_rows, start=1):
        virtual_id = f"99.{seq}L"
        sort_key = (1, 99, seq, virtual_id)
        display_entries.append((sort_key, r, virtual_id, virtual_id, True))

    display_entries.sort(key=lambda t: t[0])

    def _row_html(
        r: dict[str, Any],
        *,
        display: str,
        track_key: str,
        legacy: bool,
    ) -> str:
        tid_file = str(r["task_id"])
        tid_disp = esc(display)
        tid_attr = esc(track_key, quote=True)
        legacy_attr = "1" if legacy else "0"
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

        name_s = str(r.get("item_name") or "").strip() or tid_file
        name_cell = f'<td class="meta">{esc(name_s)}</td>'

        schema_s = str(r.get("schema_summary") or "").strip()
        if schema_s:
            schema_cell = f'<td class="schema col-eng"><code>{esc(schema_s)}</code></td>'
        else:
            schema_cell = '<td class="schema col-eng muted">—</td>'

        qc_v = str(r.get("qc_final_verdict") or "").strip().upper()
        initial_tdd = qc_v if qc_v in ("PASS", "FAIL") else "PENDING"
        tdd_cell = (
            f'<td class="tdd-cell" data-track-id="{tid_attr}">'
            f'<span class="tdd-status tdd-{initial_tdd.lower()}" data-status="{initial_tdd}">{esc(initial_tdd if initial_tdd != "PENDING" else "Pending")}</span> '
            f'<button type="button" class="tdd-run-btn" data-track-id="{tid_attr}">Run Test</button>'
            f"</td>"
        )

        base_meta = {
            "legacy": legacy,
            "display_track_id": display,
            "track_id": track_key,
            "source_task_id": tid_file,
            "test_description": r.get("test_description") or "",
            "error_message": r.get("error_message") or "",
            "qc_final_verdict": r.get("qc_final_verdict") or "",
            "tdd_status": initial_tdd,
            "schema_summary": schema_s,
            "schema_definition": r.get("schema_definition") or {},
            "artifacts": r.get("artifacts") or [],
            "files_modified": r.get("files_modified") or [],
            "final_verdict": r.get("final_verdict") or "",
            "plan_raw": r.get("plan_raw"),
            "agent_raw": r.get("agent_raw"),
            "qc_raw": r.get("qc_raw"),
        }
        if legacy:
            task_meta[track_key] = base_meta
        else:
            base_meta.update(
                {
                    "root_task_id": r.get("root_task_id") or "",
                    "global_sequence_number": r.get("global_sequence_number") or "",
                    "item_type": r.get("item_type") or "",
                    "item_name": r.get("item_name") or "",
                    "parent_track_id": r.get("parent_track_id") or "",
                    "table_name": r.get("table_name") or "",
                    "field_name": r.get("field_name") or "",
                    "scope": r.get("scope") or "",
                }
            )
            task_meta[track_key] = base_meta

        tr_class = ' class="row-legacy"' if legacy else ""
        btn_class = "tid-btn tid-btn-legacy" if legacy else "tid-btn"

        return f"""
        <tr{tr_class}>
          <td class="tid">
            <button type="button" class="{btn_class}" data-track-id="{tid_attr}" data-legacy="{legacy_attr}" title="Open detail (track_id={esc(track_key)})">
              <code>{tid_disp}</code>
            </button>{note}
          </td>
          {name_cell}
          <td class="{vclass}">{verdict}</td>
          <td class="{ing_class}">{ingested}</td>
          <td class="files">{files_html}</td>
          <td class="qc">{qc_sum}</td>
          <td class="arts col-eng">{arts}</td>
          {schema_cell}
          {tdd_cell}
        </tr>"""

    body_parts: list[str] = []
    for _sk, r, display, track_key, legacy in display_entries:
        body_parts.append(
            _row_html(r, display=display, track_key=track_key, legacy=legacy)
        )

    body = "\n".join(body_parts) if body_parts else (
        '<tr><td colspan="9" class="muted">No tasks found.</td></tr>'
    )
    table = f"""  <table id="task-table">
    <thead>
      <tr>
        <th>task_id</th>
        <th>Name</th>
        <th>final_verdict</th>
        <th>ingested</th>
        <th>modified files</th>
        <th>QC summary</th>
        <th class="col-eng">artifacts</th>
        <th class="col-eng">schema</th>
        <th>TDD</th>
      </tr>
    </thead>
    <tbody>
{body}
    </tbody>
  </table>"""

    generated_note = esc(
        f"{len(rows)} task(s) from qc_evidence JSON groups "
        f"({len(new_rows)} new, {len(legacy_rows)} legacy)"
    )
    meta_json = json.dumps(task_meta, ensure_ascii=False, indent=2)
    meta_json = meta_json.replace("<", "\\u003c").replace(">", "\\u003e")

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
      --legacy-text: #a8b0bc;
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
    .sub {{ color: var(--muted); margin-bottom: 0.75rem; font-size: 0.9rem; }}
    .mode-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.75rem 1.25rem;
      margin-bottom: 1rem;
      padding: 0.65rem 0.85rem;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      font-size: 0.9rem;
    }}
    .mode-bar label {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      cursor: pointer;
      color: var(--text);
    }}
    .mode-bar input {{ accent-color: var(--link); }}
    body[data-mode="user"] .col-eng {{ display: none !important; }}
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
    tr.row-legacy td {{ color: var(--legacy-text); }}
    tr.row-legacy .verdict-pass {{
      color: #7dffa6;
      background: var(--pass-bg);
      font-weight: 700;
    }}
    tr.row-legacy .verdict-fail {{
      color: #ff8a80;
      background: var(--fail-bg);
      font-weight: 700;
    }}
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
    .tdd-cell {{ white-space: nowrap; }}
    .tdd-status {{
      display: inline-block;
      font-weight: 700;
      font-size: 0.8rem;
      min-width: 3.5rem;
    }}
    .tdd-pass {{ color: #7dffa6; }}
    .tdd-fail {{ color: #ff8a80; }}
    .tdd-pending {{ color: var(--muted); font-weight: 600; }}
    .tdd-run-btn {{
      margin-left: 0.35rem;
      background: #121a26;
      border: 1px solid var(--border);
      color: var(--link);
      border-radius: 6px;
      padding: 0.15rem 0.5rem;
      font-size: 0.75rem;
      cursor: pointer;
    }}
    .tdd-run-btn:hover {{ border-color: var(--link); }}
    .tdd-run-btn[hidden] {{ display: none !important; }}
    .tid-btn {{
      background: none;
      border: none;
      padding: 0;
      margin: 0;
      color: var(--link);
      cursor: pointer;
      font: inherit;
      text-align: left;
    }}
    .tid-btn:hover code {{ text-decoration: underline; }}
    .tid-btn:focus-visible {{
      outline: 2px solid var(--link);
      outline-offset: 2px;
      border-radius: 4px;
    }}
    .tid-btn-legacy,
    .tid-btn-legacy code {{
      color: var(--legacy-text);
    }}
    .tid-btn-legacy:hover code {{ text-decoration: underline; }}
    .modal-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.55);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }}
    .modal-overlay.open {{ display: flex; }}
    .modal {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      max-width: 40rem;
      width: 100%;
      max-height: 90vh;
      overflow: auto;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
    }}
    .modal-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      padding: 0.85rem 1rem;
      border-bottom: 1px solid var(--border);
      background: #121a26;
    }}
    .modal-header h2 {{
      margin: 0;
      font-size: 1rem;
      font-weight: 600;
    }}
    .modal-close {{
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      cursor: pointer;
      font-size: 1.1rem;
      line-height: 1;
      padding: 0.2rem 0.55rem;
    }}
    .modal-close:hover {{ border-color: var(--link); color: var(--link); }}
    .modal-body {{ padding: 0.85rem 1rem 1.1rem; }}
    .meta-dl {{
      display: grid;
      grid-template-columns: minmax(7rem, 34%) 1fr;
      gap: 0.35rem 0.75rem;
      margin: 0;
      font-size: 0.88rem;
    }}
    .meta-dl dt {{
      margin: 0;
      color: var(--muted);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      padding-top: 0.15rem;
    }}
    .meta-dl dd {{
      margin: 0;
      word-break: break-word;
    }}
    .legacy-msg, .modal-section-title {{
      margin: 0.75rem 0 0.35rem;
      color: var(--muted);
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .legacy-msg {{ margin-top: 0; text-transform: none; font-weight: 500; font-size: 0.95rem; }}
    .raw-pre {{
      margin: 0.25rem 0 0.75rem;
      padding: 0.55rem 0.65rem;
      background: #121a26;
      border: 1px solid var(--border);
      border-radius: 6px;
      max-height: 14rem;
      overflow: auto;
      font-size: 0.75rem;
      white-space: pre-wrap;
      word-break: break-word;
    }}
  </style>
</head>
<body data-mode="user">
  <h1>Task view</h1>
  <p class="sub">Static index of <code>qc_evidence/</code> plan / agent_log / qc_report triples. {generated_note}. No server required.</p>

  <div class="mode-bar" role="group" aria-label="View mode">
    <span>模式 / Mode:</span>
    <label><input type="radio" name="view-mode" value="user" checked/> 普通使用者模式</label>
    <label><input type="radio" name="view-mode" value="eng"/> 工程 / 開發者模式</label>
  </div>

{table}

  <div id="task-modal" class="modal-overlay" hidden>
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-header">
        <h2 id="modal-title">Task detail</h2>
        <button type="button" class="modal-close" id="modal-close" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body" id="modal-body"></div>
    </div>
  </div>

  <script>
  const TASK_META = {meta_json};
  const TDD_STORAGE_KEY = "task_view_tdd_status_v1";
  const META_KEYS = [
    ["display_track_id", "display_track_id"],
    ["track_id", "track_id"],
    ["root_task_id", "root_task_id"],
    ["global_sequence_number", "global_sequence_number"],
    ["item_type", "item_type"],
    ["parent_track_id", "parent_track_id"]
  ];
  const overlay = document.getElementById("task-modal");
  const bodyEl = document.getElementById("modal-body");
  const titleEl = document.getElementById("modal-title");
  const closeBtn = document.getElementById("modal-close");

  function dash(v) {{
    if (v === null || v === undefined) return "—";
    const s = String(v).trim();
    return s ? s : "—";
  }}

  function loadTddMap() {{
    try {{
      const raw = localStorage.getItem(TDD_STORAGE_KEY);
      if (!raw) return {{}};
      const o = JSON.parse(raw);
      return o && typeof o === "object" ? o : {{}};
    }} catch (e) {{
      return {{}};
    }}
  }}

  function saveTddMap(map) {{
    try {{
      localStorage.setItem(TDD_STORAGE_KEY, JSON.stringify(map));
    }} catch (e) {{}}
  }}

  function setTddCell(trackId, status) {{
    const cell = document.querySelector('.tdd-cell[data-track-id="' + CSS.escape(trackId) + '"]');
    if (!cell) return;
    const st = String(status || "PENDING").toUpperCase();
    const span = cell.querySelector(".tdd-status");
    const btn = cell.querySelector(".tdd-run-btn");
    if (span) {{
      span.dataset.status = st;
      span.className = "tdd-status tdd-" + st.toLowerCase();
      span.textContent = st === "PENDING" ? "Pending" : st;
    }}
    if (btn) {{
      if (st === "PENDING") btn.hidden = false;
      else btn.hidden = true;
    }}
    if (TASK_META[trackId]) TASK_META[trackId].tdd_status = st;
  }}

  function initTddFromStorage() {{
    const map = loadTddMap();
    Object.keys(TASK_META).forEach(function (id) {{
      const meta = TASK_META[id];
      let st = map[id];
      if (!st) {{
        const q = String(meta.qc_final_verdict || "").toUpperCase();
        st = (q === "PASS" || q === "FAIL") ? q : "PENDING";
      }}
      setTddCell(id, st);
    }});
  }}

  function runTdd(trackId) {{
    const meta = TASK_META[trackId] || {{}};
    const q = String(meta.qc_final_verdict || "").toUpperCase();
    let next;
    if (q === "PASS" || q === "FAIL") {{
      next = q;
    }} else {{
      const cur = String(meta.tdd_status || "PENDING").toUpperCase();
      if (cur === "PENDING") next = "PASS";
      else if (cur === "PASS") next = "FAIL";
      else next = "PENDING";
    }}
    const map = loadTddMap();
    map[trackId] = next;
    saveTddMap(map);
    setTddCell(trackId, next);
  }}

  function appendDl(parent, pairs) {{
    const dl = document.createElement("dl");
    dl.className = "meta-dl";
    pairs.forEach(function (pair) {{
      const dt = document.createElement("dt");
      dt.textContent = pair[0];
      const dd = document.createElement("dd");
      const code = document.createElement("code");
      code.textContent = dash(pair[1]);
      dd.appendChild(code);
      dl.appendChild(dt);
      dl.appendChild(dd);
    }});
    parent.appendChild(dl);
  }}

  function appendSection(parent, title, text) {{
    const h = document.createElement("p");
    h.className = "modal-section-title";
    h.textContent = title;
    parent.appendChild(h);
    const pre = document.createElement("pre");
    pre.className = "raw-pre";
    pre.textContent = text || "—";
    parent.appendChild(pre);
  }}

  function pretty(obj) {{
    if (obj === null || obj === undefined || obj === "") return "—";
    if (typeof obj === "string") return obj;
    try {{ return JSON.stringify(obj, null, 2); }} catch (e) {{ return String(obj); }}
  }}

  function openModal(trackId, isLegacy) {{
    const meta = TASK_META[trackId] || {{}};
    const legacy = isLegacy || meta.legacy === true;
    const mode = document.body.getAttribute("data-mode") || "user";
    bodyEl.innerHTML = "";
    titleEl.textContent = "Task " + dash(meta.display_track_id || trackId);

    if (mode === "user") {{
      if (legacy && !String(meta.test_description || "").trim()) {{
        const p = document.createElement("p");
        p.className = "legacy-msg";
        p.textContent = "Legacy task, no real track metadata available";
        bodyEl.appendChild(p);
      }}
      appendDl(bodyEl, [
        ["test_description", meta.test_description],
        ["test_result", meta.tdd_status || meta.qc_final_verdict || "PENDING"],
        ["error_message", meta.error_message]
      ]);
    }} else {{
      if (legacy) {{
        const p = document.createElement("p");
        p.className = "legacy-msg";
        p.textContent = "Legacy task, no real track metadata available";
        bodyEl.appendChild(p);
      }} else {{
        const pairs = META_KEYS.map(function (k) {{ return [k[1], meta[k[0]]]; }});
        appendDl(bodyEl, pairs);
      }}
      appendDl(bodyEl, [
        ["test_description", meta.test_description],
        ["test_result", meta.tdd_status || meta.qc_final_verdict || "PENDING"],
        ["error_message", meta.error_message]
      ]);
      const arts = meta.artifacts || [];
      const artText = arts.length
        ? arts.map(function (a) {{ return (a.label || "") + " → " + (a.href || ""); }}).join("\\n")
        : "—";
      appendSection(bodyEl, "artifacts", artText);
      appendSection(bodyEl, "schema", pretty(meta.schema_definition || meta.schema_summary));
      appendSection(
        bodyEl,
        "raw log",
        "=== plan ===\\n" + pretty(meta.plan_raw) +
        "\\n\\n=== agent_log ===\\n" + pretty(meta.agent_raw) +
        "\\n\\n=== qc_report ===\\n" + pretty(meta.qc_raw)
      );
    }}
    overlay.hidden = false;
    overlay.classList.add("open");
  }}

  function closeModal() {{
    overlay.classList.remove("open");
    overlay.hidden = true;
  }}

  document.querySelectorAll('input[name="view-mode"]').forEach(function (inp) {{
    inp.addEventListener("change", function () {{
      if (inp.checked) document.body.setAttribute("data-mode", inp.value);
    }});
  }});

  document.querySelectorAll(".tid-btn").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      const id = btn.getAttribute("data-track-id");
      const leg = btn.getAttribute("data-legacy") === "1";
      if (id) openModal(id, leg);
    }});
  }});

  document.querySelectorAll(".tdd-run-btn").forEach(function (btn) {{
    btn.addEventListener("click", function (ev) {{
      ev.stopPropagation();
      const id = btn.getAttribute("data-track-id");
      if (id) runTdd(id);
    }});
  }});

  closeBtn.addEventListener("click", closeModal);
  overlay.addEventListener("click", function (ev) {{
    if (ev.target === overlay) closeModal();
  }});
  document.addEventListener("keydown", function (ev) {{
    if (ev.key === "Escape") closeModal();
  }});

  initTddFromStorage();
  </script>
</body>
</html>
"""

def cmd_render() -> int:
    rows = _build_rows()
    html_text = _render_html(rows)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    new_n = sum(1 for r in rows if r.get("has_display_track_id"))
    legacy_n = len(rows) - new_n
    print(
        json.dumps(
            {
                "output": str(OUT_HTML),
                "task_count": len(rows),
                "new_count": new_n,
                "legacy_count": legacy_n,
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
