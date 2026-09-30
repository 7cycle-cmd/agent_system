"""render_outcome_table.py — the ONE choke point for Markdown table rendering.

WHY THIS EXISTS (2026-09-26)
----------------------------
The human asked for every outcome / QC verdict table to stop being hand-written
Markdown and instead be rendered from structured data, with cell escaping built
in, so that a backtick, a `|` or a newline can never break a table again.

MEASURED FIRST, and the measurement changed the design:

  * There was **no renderer** to fix. `grep write_text` over `scripts/**` finds
    no writer of `plan_*.md`; the §9 tables are hand-written Markdown.
  * The REAL defect was on the READER: `plan_gate.plan_allowlist()` scanned the
    WHOLE file for `^\\s*\\|\\s*`([^`]+)`\\s*\\|`, so **every** table row whose
    first cell was backticked became an allowlist entry. MEASURED: **88 of the
    206 plans** in `qc_evidence/` were polluted — a §9 OUTCOME row silently
    AUTHORISED a write the plan never listed. That is a privilege escalation.
    It is fixed in `scripts/plan_gate.py` (Phase 1), on the reader.

This module is Phase 2: the writer-side choke point, so a NEW table cannot
re-introduce the shape. It does NOT touch the 194 existing ALLOWLIST tables or
the ~102 existing OUTCOME tables — those are APPROVED audit evidence and
rewriting them would destroy provenance.

DESIGN DECISIONS (each one measured, not assumed)
-------------------------------------------------
1. **EVERY cell is escaped; there is no per-column register.** A number or an
   enum contains no `|`, backtick, newline, `<`, `>` or `${`, so escaping is a
   NO-OP for them. Escaping everything IS the single choke point. Binding the
   decision to `field_registry` was rejected: that table has no `free_text`
   column (`coord_store.py:59`) and is keyed by `task_id` (a skill
   task), not by an outcome-table column — a category error.

2. **Backslash is escaped FIRST.** This is an addition to the requested list,
   and it is required for correctness: without it, a cell containing `\\|`
   becomes `\\\\|`, which Markdown renders as a literal backslash followed by an
   UNESCAPED pipe — i.e. the cell still breaks. Escaping `\\` -> `\\\\` first
   makes `\\|` -> `\\\\\\|`, which renders as the literal text `\\|`.

3. **Newline -> `<br>` is applied AFTER `<`/`>` are escaped**, so the `<br>` we
   insert is not itself turned into `&lt;br&gt;`.

4. **Nothing is written to `audit_trace`.** Repo law: "Harvest experience from
   audit only; never mutate `audit_trace`."

5. **This is Markdown only.** The HTML/SPA tables are a separate system with
   their own escaping (`_tmp_render_html_fn.py:83` `_cell()`, `db_browser.py`
   uses `html.escape` throughout). The two do not share code.

USAGE
-----
    from render_outcome_table import render_outcome_table

    md = render_outcome_table(
        headers=["QC", "Verdict", "Evidence"],
        rows=[{"QC": "QC-01", "Verdict": "PASS", "Evidence": "AST scan"}],
    )
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

__all__ = ["escape_md_cell", "render_outcome_table"]


def escape_md_cell(value: Any) -> str:
    """Escape one value for a Markdown table cell.

    `None` becomes `""` (NOT the string `"None"`). Everything else is `str()`-ed
    and escaped so it cannot break the row or the column:

    | input | output | why |
    |---|---|---|
    | `\\` | `\\\\` | else `\\|` renders as a literal backslash + a cell break |
    | `\\|` | `\\\\\\|` | the column separator |
    | `` ` `` | `` \\` `` | a backtick can open a code span that swallows the row |
    | `<` | `&lt;` | raw HTML is rendered by VS Code's Markdown preview |
    | `>` | `&gt;` | same |
    | newline | `<br>` | a newline ends the row |
    | `${` | `\\${` | template-literal injection if the cell is ever embedded in JS |

    A number or an enum passes through UNCHANGED — escaping is a no-op for them,
    which is why no per-column register is needed.
    """
    if value is None:
        return ""
    s = str(value)
    # 1. backslash FIRST, so the escapes we add below are not themselves escaped
    s = s.replace("\\", "\\\\")
    # 2. the column separator
    s = s.replace("|", "\\|")
    # 3. the code-span delimiter
    s = s.replace("`", "\\`")
    # 4. raw HTML
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    # 5. a newline ends the row. AFTER 4, so our own `<br>` is not escaped.
    s = s.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")
    # 6. template-literal injection
    s = s.replace("${", "\\${")
    return s


def render_outcome_table(
    headers: Sequence[str],
    rows: Iterable[Mapping[str, Any] | Sequence[Any]],
) -> str:
    """Render structured data as a complete Markdown table.

    `headers` is the column list, e.g. `["QC", "Verdict", "Evidence"]`.
    Each row is either a mapping keyed by header, or a sequence in header order.

    EVERY cell — including the headers — goes through `escape_md_cell`, so a
    toxic value cannot break the table. The output is deterministic: the same
    input always produces the same string.

    Raises `ValueError` when `headers` is empty: a table with no columns is not
    a table, and silently returning `""` would hide the caller's bug.
    """
    headers = list(headers)
    if not headers:
        raise ValueError("headers must not be empty")

    escaped_headers = [escape_md_cell(h) for h in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]

    for row in rows:
        if isinstance(row, Mapping):
            cells = [row.get(h, "") for h in headers]
        else:
            cells = list(row)
            if len(cells) != len(headers):
                raise ValueError(
                    "row has %d cell(s) but there are %d header(s)"
                    % (len(cells), len(headers)))
        lines.append("| " + " | ".join(escape_md_cell(c) for c in cells) + " |")

    return "\n".join(lines)
