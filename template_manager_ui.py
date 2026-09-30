"""Shared HTML for prompt setting manager UI (/prompt/setting)."""

from __future__ import annotations

NAV_BAR_HTML = """
<nav class="top-nav" style="display:flex;gap:1rem;flex-wrap:wrap;margin-bottom:1.25rem;align-items:center;">
  <a href="/settings">Settings</a>
  <a href="/llm-tasks/prompt_setting">Prompt Setting</a>
  <a href="/llm-tasks">LLM Tasks</a>
</nav>
"""

SETTINGS_PAGE_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Settings — Success Target Table</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ font-family: system-ui, sans-serif; margin: 1.5rem; background:#121212; color:#e0e0e0; }}
    a {{ color:#4fc3f7; }}
    .sub {{ color:#9e9e9e; font-size:0.9rem; margin-bottom:1rem; }}
    table {{ width:100%; border-collapse:collapse; font-size:0.9rem; background:#1e1e1e; border:1px solid #333; border-radius:10px; overflow:hidden; }}
    th, td {{ border-bottom:1px solid #333; padding:0.5rem 0.6rem; text-align:left; vertical-align:middle; }}
    th {{ color:#aaa; font-weight:600; background:#252525; }}
    tr:last-child td {{ border-bottom:0; }}
    .mono {{ font-family: ui-monospace, monospace; }}
    .range {{ color:#a5d6a7; font-weight:600; }}
    .pill {{ display:inline-block; padding:0.1rem 0.5rem; border-radius:999px; background:#2e5d34; font-size:0.75rem; }}
    .pill.off {{ background:#5d4037; }}
    img.logo {{ width:28px; height:28px; object-fit:contain; border-radius:4px; background:#0d0d0d; }}
    button {{ background:#2b5278; color:#fff; border:0; border-radius:6px; padding:0.35rem 0.65rem; cursor:pointer; font-size:0.85rem; }}
    .msg {{ margin:0.5rem 0; font-size:0.9rem; color:#9e9e9e; }}
  </style>
</head>
<body>
  {NAV_BAR_HTML}
  <h1>Settings — Success Target Table</h1>
  <p class="sub">Proven click targets learned from py scripts. Same <code>target_id</code> with
  different X/Y = the learned <span class="range">range</span> (min–max). Written by
  <code>coord_store.record_success()</code> after each verified successful click.</p>
  <div style="margin-bottom:0.75rem;">
    <button onclick="load()">Refresh</button>
  </div>
  <div id="msg" class="msg"></div>
  <table>
    <thead>
      <tr>
        <th>id</th><th>target</th><th>action</th><th>logo url</th>
        <th>X (min–max)</th><th>Y (min–max)</th><th>active</th>
        <th>created_date</th><th>updated_date</th>
      </tr>
    </thead>
    <tbody id="tbody"><tr><td colspan="9" style="color:#9e9e9e;">loading…</td></tr></tbody>
  </table>
  <script>
    function esc(s) {{
      return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }}
    function fmtRange(min, max) {{
      return min === max ? String(min) : (min + '–' + max);
    }}
    async function load() {{
      const msg = document.getElementById('msg');
      try {{
        const r = await fetch('/api/coord/grouped');
        const rows = await r.json();
        const tb = document.getElementById('tbody');
        if (!Array.isArray(rows) || rows.length === 0) {{
          tb.innerHTML = '<tr><td colspan="9" style="color:#9e9e9e;">No success targets recorded yet.</td></tr>';
          msg.textContent = '0 targets';
          return;
        }}
        tb.innerHTML = rows.map(g =>
          '<tr>' +
          '<td class="mono">' + esc(g.target_id) + '</td>' +
          '<td>' + esc(g.target_name || g.target_id) +
            (g.count > 1 ? ' <span class="pill">' + g.count + ' pts</span>' : '') + '</td>' +
          '<td class="mono">' + esc(g.action) + '</td>' +
          '<td>' + (g.logo ? '<img class="logo" src="' + esc(g.logo) + '" title="' + esc(g.logo) + '">' : '<span style="color:#666">–</span>') + '</td>' +
          '<td class="mono range">' + fmtRange(g.x_min, g.x_max) + '</td>' +
          '<td class="mono range">' + fmtRange(g.y_min, g.y_max) + '</td>' +
          '<td>' + (g.active ? '<span class="pill">on</span>' : '<span class="pill off">off</span>') + '</td>' +
          '<td class="mono" style="color:#9e9e9e">' + esc(g.created_at || '') + '</td>' +
          '<td class="mono" style="color:#9e9e9e">' + esc(g.updated_at || '') + '</td>' +
          '</tr>'
        ).join('');
        msg.textContent = rows.length + ' target(s)';
      }} catch (e) {{
        msg.textContent = 'load failed: ' + e;
      }}
    }}
    load();
  </script>
</body>
</html>
"""

LLM_TEMPLATES_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Prompt Setting (navigator)</title>
  <style>
    :root { color-scheme: dark; }
    body { font-family: system-ui, sans-serif; margin: 0; padding: 1.25rem; background:#121212; color:#e0e0e0; }
    a { color:#4fc3f7; text-decoration:none; }
    a:hover { text-decoration:underline; }
    .top-nav { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; }
    h1 { margin:0 0 0.25rem; font-size:1.4rem; }
    .sub { color:#9e9e9e; margin-bottom:1rem; font-size:0.9rem; }
    .grid { display:grid; grid-template-columns: 1.2fr 1fr; gap:1rem; }
    @media (max-width: 960px) { .grid { grid-template-columns: 1fr; } }
    .card { background:#1e1e1e; border:1px solid #333; border-radius:10px; padding:1rem; }
    table { width:100%; border-collapse:collapse; font-size:0.9rem; }
    th, td { border-bottom:1px solid #333; padding:0.45rem 0.35rem; text-align:left; vertical-align:top; }
    th { color:#aaa; font-weight:600; }
    button, .btn {
      background:#2b5278; color:#fff; border:0; border-radius:6px;
      padding:0.35rem 0.65rem; cursor:pointer; font-size:0.85rem; margin:0 0.2rem 0.2rem 0;
    }
    button.danger { background:#8b3a3a; }
    button.secondary { background:#444; }
    button:disabled { opacity:0.5; cursor:not-allowed; }
    label { display:block; font-size:0.8rem; color:#aaa; margin:0.5rem 0 0.2rem; }
    input, textarea, select {
      width:100%; box-sizing:border-box; background:#121212; color:#eee;
      border:1px solid #444; border-radius:6px; padding:0.45rem 0.55rem; font-family:inherit;
    }
    textarea { min-height:120px; font-family:ui-monospace, monospace; font-size:0.85rem; }
    #previewOut { white-space:pre-wrap; background:#0d0d0d; border:1px solid #333; border-radius:6px;
      padding:0.75rem; min-height:140px; font-family:ui-monospace, monospace; font-size:0.8rem; }
    .msg { margin:0.5rem 0; font-size:0.9rem; }
    .msg.err { color:#ff8a80; }
    .msg.ok { color:#a5d6a7; }
    .pill { display:inline-block; padding:0.1rem 0.4rem; border-radius:999px; background:#333; font-size:0.75rem; }
    .pill.off { background:#5d4037; }
  </style>
</head>
<body>
  <nav class="top-nav">
    <a href="/settings">Settings</a>
    <a href="/llm-tasks/prompt_setting"><strong>Prompt Setting</strong></a>
    <a href="/llm-tasks">LLM Tasks</a>
  </nav>
  <h1>Prompt Setting Manager</h1>
  <p class="sub">Prompt Setting：設定 LLM 返回結果嘅格式規則（JSON / YESNO / 固定文字），唔包含任務內容。Prefer Task Monitor nav: <a href="/llm-tasks/prompt_setting">/llm-tasks/prompt_setting</a>. This page is a fallback. Scanner/worker read DB live; missing keys fall back to <code>verdict_3line</code>.</p>
  <div id="flash" class="msg"></div>

  <div class="grid">
    <section class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem;flex-wrap:wrap;">
        <h2 style="margin:0;font-size:1.1rem;">Prompt Settings</h2>
        <label style="display:flex;align-items:center;gap:0.4rem;margin:0;">
          <input type="checkbox" id="showAll" style="width:auto"> show inactive
        </label>
      </div>
      <div style="overflow:auto;margin-top:0.75rem;">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>Setting Key</th><th>Setting Name</th><th>Catalog</th><th>Updated</th><th>Act</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <h2 style="margin:0 0 0.5rem;font-size:1.1rem;" id="formTitle">New prompt setting</h2>
      <input type="hidden" id="editId" value="">
      <label>Setting Key</label>
      <input id="prompt_setting_key" placeholder="e.g. json_result">
      <label>Setting Name</label>
      <input id="name" placeholder="Display name">
      <label>description / sub-agent notes</label>
      <input id="description" placeholder="optional notes">
      <label>catalog_id</label>
      <input id="catalog_id" type="number" value="0" min="0">
      <label>instruction</label>
      <textarea id="instruction" placeholder="Format rules appended to skill prompt"></textarea>
      <div style="margin-top:0.75rem;">
        <button type="button" id="btnSave">Save</button>
        <button type="button" class="secondary" id="btnReset">Reset form</button>
      </div>

      <h2 style="margin:1.25rem 0 0.5rem;font-size:1.1rem;">Preview</h2>
      <label>sample task prompt</label>
      <textarea id="samplePrompt">Verify membership REST channel.</textarea>
      <div style="margin-top:0.5rem;">
        <button type="button" class="secondary" id="btnPreview">Preview final prompt</button>
      </div>
      <label>final_prompt</label>
      <div id="previewOut"></div>
    </section>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const flash = (msg, ok) => {
      const el = $('flash');
      el.textContent = msg || '';
      el.className = 'msg ' + (ok ? 'ok' : (msg ? 'err' : ''));
    };

    function formPayload() {
      return {
        prompt_setting_key: $('prompt_setting_key').value.trim(),
        name: $('name').value.trim(),
        description: $('description').value,
        catalog_id: Number($('catalog_id').value || 0),
        instruction: $('instruction').value,
      };
    }

    function fillForm(row) {
      $('editId').value = row && row.id != null ? String(row.id) : '';
      $('formTitle').textContent = row && row.id != null ? ('Edit #' + row.id) : 'New prompt setting';
      $('prompt_setting_key').value = row?.prompt_setting_key || '';
      $('prompt_setting_key').disabled = row?.prompt_setting_key === 'verdict_3line';
      $('name').value = row?.name || '';
      $('description').value = row?.description || '';
      $('catalog_id').value = row?.catalog_id != null ? row.catalog_id : 0;
      $('instruction').value = row?.instruction || '';
    }

    function resetForm() {
      fillForm(null);
      $('prompt_setting_key').disabled = false;
      flash('');
    }

    async function loadList() {
      const all = $('showAll').checked ? '?all=1' : '';
      const res = await fetch('/api/templates' + all);
      const data = await res.json();
      const rows = Array.isArray(data) ? data : (data.templates || []);
      const tb = $('tbody');
      tb.innerHTML = '';
      rows.forEach(r => {
        const tr = document.createElement('tr');
        const active = Number(r.is_active) === 1;
        tr.innerHTML = `
          <td>${r.id}</td>
          <td><code>${escapeHtml(r.prompt_setting_key || '')}</code></td>
          <td>${escapeHtml(r.name || '')}</td>
          <td>${r.catalog_id || 0}</td>
          <td>${escapeHtml(r.updated_at || r.created_at || '')}</td>
          <td>
            <span class="pill ${active ? '' : 'off'}">${active ? 'on' : 'off'}</span><br>
            <button type="button" data-act="edit" data-id="${r.id}">Edit</button>
            <button type="button" class="secondary" data-act="preview" data-id="${r.id}">Preview</button>
            <button type="button" class="danger" data-act="del" data-id="${r.id}" ${r.prompt_setting_key==='verdict_3line'?'disabled':''}>Delete</button>
          </td>`;
        tb.appendChild(tr);
      });
    }

    function escapeHtml(s) {
      const d = document.createElement('div');
      d.textContent = String(s);
      return d.innerHTML;
    }

    async function save() {
      flash('');
      const id = $('editId').value.trim();
      const body = formPayload();
      let res;
      if (id) {
        res = await fetch('/api/templates/' + id, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
      } else {
        res = await fetch('/api/templates', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        flash(data.detail || data.error || ('HTTP ' + res.status), false);
        return;
      }
      flash(id ? 'Updated.' : 'Created id=' + data.id, true);
      await loadList();
      if (data.id) fillForm(data);
    }

    async function preview(id) {
      flash('');
      const body = {
        sample_prompt: $('samplePrompt').value,
      };
      if (id) body.setting_id = Number(id);
      else {
        body.instruction = $('instruction').value;
      }
      const res = await fetch('/api/templates/preview', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        flash(data.detail || data.error || ('HTTP ' + res.status), false);
        return;
      }
      $('previewOut').textContent = data.final_prompt || '';
    }

    async function del(id) {
      if (!confirm('Soft-delete template #' + id + '?')) return;
      const res = await fetch('/api/templates/' + id, { method: 'DELETE' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        flash(data.detail || data.error || ('HTTP ' + res.status), false);
        return;
      }
      flash('Deleted (' + (data.mode || 'soft') + ').', true);
      resetForm();
      await loadList();
    }

    $('tbody').addEventListener('click', async (ev) => {
      const btn = ev.target.closest('button[data-act]');
      if (!btn) return;
      const id = btn.getAttribute('data-id');
      const act = btn.getAttribute('data-act');
      if (act === 'edit') {
        const res = await fetch('/api/templates/' + id);
        const data = await res.json();
        if (!res.ok) { flash(data.detail || data.error || 'load failed', false); return; }
        fillForm(data);
      } else if (act === 'preview') {
        const res = await fetch('/api/templates/' + id);
        const data = await res.json();
        if (res.ok) fillForm(data);
        await preview(id);
      } else if (act === 'del') {
        await del(id);
      }
    });

    $('btnSave').addEventListener('click', save);
    $('btnReset').addEventListener('click', resetForm);
    $('btnPreview').addEventListener('click', () => preview($('editId').value || null));
    $('showAll').addEventListener('change', loadList);
    loadList().catch(e => flash(String(e), false));
  </script>
</body>
</html>
"""
