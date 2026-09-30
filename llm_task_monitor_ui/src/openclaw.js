/**
 * OpenClaw settings page — /llm-tasks/setting/openclaw
 *
 * Shows three things side by side, because the failure mode is a CONTRADICTION:
 * a tool can fail while its config reads true. Showing only one hides it.
 *
 * Observed 2026-09-19/20: `NodeScreenEnabled=true` and
 * `ScreenRecordingConsentGiven=true`, yet the MCP server was not even listening
 * (SYN_SENT on 127.0.0.1:8765, WinError 10061). A settings-only view would say
 * "all good"; a tool-only view would say "capture is broken". Both together say
 * "the server is down".
 *
 * ui_field_builder rules: read-only page -> a Refresh button only; the error
 * state names the exact cause; never auto-write a security setting.
 */

const STATE_BADGE = {
  working: { bg: 'rgba(16,185,129,.14)', fg: '#047857', label: 'working' },
  failing: { bg: 'rgba(244,63,94,.14)', fg: '#be123c', label: 'FAILING' },
  present: { bg: 'rgba(59,130,246,.14)', fg: '#1d4ed8', label: 'present' },
  missing: { bg: 'rgba(107,114,128,.16)', fg: '#4b5563', label: 'missing' },
};

function badge(state) {
  const b = STATE_BADGE[state] || STATE_BADGE.missing;
  return (
    '<span style="background:' + b.bg + ';color:' + b.fg +
    ';border-radius:999px;padding:2px 9px;font-size:11px;font-weight:600">' +
    b.label + '</span>'
  );
}

function boolChip(v) {
  const unset = v === '<unset>' || v === undefined || v === null;
  const on = v === true;
  const bg = unset
    ? 'rgba(107,114,128,.16)'
    : on ? 'rgba(16,185,129,.14)' : 'rgba(244,63,94,.14)';
  const fg = unset ? '#4b5563' : on ? '#047857' : '#be123c';
  const label = unset ? 'unset' : on ? 'true' : 'false';
  return (
    '<span class="mono" style="background:' + bg + ';color:' + fg +
    ';border-radius:6px;padding:1px 7px;font-size:11px">' + label + '</span>'
  );
}

export function openclawHtml(state, esc) {
  const d = state.openclaw || {};
  if (!d.loaded) {
    return '<div class="mx-auto max-w-6xl p-6 text-sm text-muted">Probing OpenClaw…</div>';
  }

  const s = d.settings || {};
  const p = d.probe || {};
  const sum = d.summary || {};
  const caps = d.capabilities || [];
  const rt = d.runtime || {};

  // A dead server is a distinct, actionable state — not a generic error.
  // NOTE: the report itself returns ok=true even when the probe fails (the
  // report generated; the SERVER did not answer). So the probe verdict, not
  // the report flag, decides whether the server is reachable.
  const probeOk = !(p && p.ok === false);
  const serverDown =
    !d.ok || !probeOk || String(d.error || '').includes('10061');
  const errPanel = serverDown
    ? '<div class="rounded-2xl border border-rose-300 bg-rose-50 p-4">' +
      '<div class="text-sm font-semibold text-rose-900">OpenClaw MCP server is not reachable</div>' +
      '<p class="mono mt-1 text-xs text-rose-800">' +
      esc(d.error || (p && p.error) || '10061 connection refused') + '</p>' +
      '<p class="mt-2 text-xs text-rose-800">settings.json may still read ' +
      '<span class="mono">true</span> — the config file is not the server. ' +
      'Press <b>Connect</b> to launch OpenClawTray and re-probe, or start it ' +
      'yourself and confirm <b>Local MCP Server ON</b>.</p>' +
      (rt.exe
        ? '<p class="mono mt-2 text-[11px] text-rose-700">exe: ' + esc(rt.exe) +
          ' · process: ' + esc(rt.process && rt.process.running ? 'running' : 'not running') +
          ' · port ' + esc(rt.host) + ':' + esc(rt.port) + ' ' +
          esc(rt.port_open ? 'open' : 'closed') + '</p>'
        : '<p class="mt-2 text-xs text-rose-800">OpenClawTray executable not found — ' +
          'set <span class="mono">OPENCLAW_EXE</span> or install OpenClaw Companion.</p>') +
      '</div>'
    : '';

  const strip =
    '<div class="flex flex-wrap items-center gap-2">' +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs text-muted">' +
    esc(sum.tool_count || 0) + ' tools</span>' +
    '<span class="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">' +
    esc(sum.capabilities_working || 0) + ' working</span>' +
    (sum.capabilities_failing
      ? '<span class="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700">' +
        esc(sum.capabilities_failing) + ' failing</span>'
      : '') +
    '<button id="btn-oc-refresh" type="button" ' +
    'class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    (serverDown
      ? '<button id="btn-oc-connect" type="button" ' +
        'class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">' +
        'Connect</button>'
      : '') +
    '</div>';

  const failing = caps.filter((c) => c.state === 'failing');
  const contradiction = failing.length && !serverDown
    ? '<div class="rounded-2xl border border-amber-300 bg-amber-50 p-4">' +
      '<div class="text-sm font-semibold text-amber-900">' + failing.length +
      ' capability failing although its gate is enabled</div>' +
      failing
        .map(
          (c) => '<div class="mt-2 text-xs text-amber-800"><b>' + esc(c.name) +
            '</b> — ' + esc(c.note || 'no detail') + '</div>'
        )
        .join('') +
      '<p class="mt-3 text-xs text-amber-700">Tool fails while the setting reads ' +
      '<span class="mono">true</span> — the setting alone is not the cause.</p></div>'
    : '';

  const capRows = caps
    .map(
      (c) =>
        '<tr class="border-b border-line align-top">' +
        '<td class="px-3 py-2 text-sm font-medium text-ink">' + esc(c.name) + '</td>' +
        '<td class="px-3 py-2">' + badge(c.state) + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(c.why) + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(c.in_server) + '/' +
        esc(c.tools.length) + ' in server</td>' +
        '<td class="mono px-3 py-2 text-[11px] text-muted">' + esc(c.gate) + '</td></tr>'
    )
    .join('');

  const groups = s.groups || {};
  const settingsRows = Object.keys(groups)
    .map(
      (g) =>
        '<div class="rounded-xl border border-line bg-soft/40 p-3">' +
        '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">' +
        esc(g) + '</div>' +
        Object.keys(groups[g])
          .map(
            (k) =>
              '<div class="flex items-center justify-between gap-3 py-1">' +
              '<span class="mono text-xs text-ink">' + esc(k) + '</span>' +
              boolChip(groups[g][k]) + '</div>'
          )
          .join('') +
        '</div>'
    )
    .join('');

  const probeRows = (p.results || [])
    .map(
      (r) =>
        '<tr class="border-b border-line">' +
        '<td class="mono px-3 py-1.5 text-xs text-ink">' + esc(r.tool) + '</td>' +
        '<td class="px-3 py-1.5">' + badge(r.ok ? 'working' : 'failing') + '</td>' +
        '<td class="px-3 py-1.5 text-xs text-muted">' +
        esc(r.ms === null || r.ms === undefined ? '—' : r.ms + ' ms') + '</td>' +
        '<td class="px-3 py-1.5 text-xs text-muted">' +
        esc((r.detail || '').slice(0, 120)) + '</td></tr>'
    )
    .join('');

  const toolChips = (p.tools || [])
    .map(
      (t) =>
        '<span class="mono rounded-lg border border-line bg-soft px-2 py-1 text-[11px] text-ink">' +
        esc(t) + '</span>'
    )
    .join('');

  // Full inventory: every tool is a BUTTON that opens a detail modal. The old
  // version rendered names only, which hid the description and the input schema
  // — i.e. it hid the fact that screen.snapshot captures your screen.
  const details = (d.tool_details && d.tool_details.tools) || [];
  const detailByName = {};
  details.forEach((t) => { detailByName[t.name] = t; });
  const sideEffectCount = details.filter((t) => t.side_effect).length;

  const toolButtons = (p.tools || [])
    .map((name) => {
      const t = detailByName[name] || {};
      const se = t.side_effect;
      const cls = se
        ? 'border-amber-300 bg-amber-50 text-amber-800'
        : 'border-line bg-soft text-ink';
      return (
        '<button type="button" data-oc-tool="' + esc(name) + '" ' +
        'class="mono rounded-lg border px-2 py-1 text-[11px] text-left hover:ring-2 hover:ring-accent ' + cls + '" ' +
        'title="' + esc(se ? 'SIDE EFFECT: ' + t.side_effect_reason : (t.description || name)) + '">' +
        esc(name) + (se ? ' ⚠' : '') + '</button>'
      );
    })
    .join('');

  const specPanel =
    '<details class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<summary class="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted">' +
    'Agent Skills spec (open source) — the format this checklist follows</summary>' +
    '<div class="mt-3 flex flex-col gap-3 text-xs text-muted">' +
    '<p>Source: <span class="mono">github.com/anthropics/skills</span> (Apache-2.0) · ' +
    'spec: <span class="mono">agentskills.io/specification</span>. ' +
    'A skill is a folder with a <span class="mono">SKILL.md</span> containing YAML ' +
    'frontmatter plus Markdown instructions.</p>' +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60"><tr>' +
    '<th class="px-3 py-2">field</th><th class="px-3 py-2">required</th>' +
    '<th class="px-3 py-2">constraint</th></tr></thead><tbody>' +
    [
      ['name', 'yes', 'max 64 chars; lowercase letters, numbers, hyphens; must match the parent directory name'],
      ['description', 'yes', 'max 1024 chars; what it does AND when to use it'],
      ['license', 'no', 'license name, or a reference to a bundled license file'],
      ['compatibility', 'no', 'max 500 chars; environment requirements'],
      ['metadata', 'no', 'arbitrary string→string map'],
      ['allowed-tools', 'no', 'space-separated pre-approved tools (experimental)'],
    ]
      .map(
        (r) =>
          '<tr class="border-b border-line">' +
          '<td class="mono px-3 py-2 text-ink">' + esc(r[0]) + '</td>' +
          '<td class="px-3 py-2">' + esc(r[1]) + '</td>' +
          '<td class="px-3 py-2">' + esc(r[2]) + '</td></tr>'
      )
      .join('') +
    '</tbody></table></div>' +
    '<p>Optional directories: <span class="mono">scripts/</span> (executable code), ' +
    '<span class="mono">references/</span> (docs loaded on demand), ' +
    '<span class="mono">assets/</span> (templates, images, data). ' +
    'Keep <span class="mono">SKILL.md</span> under 500 lines and move detail into ' +
    'referenced files — agents load skills progressively.</p>' +
    '</div></details>';

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold text-ink">OpenClaw</h2>' +
    '<p class="mt-0.5 text-sm text-muted">Local MCP server · settings + live ' +
    'capability probe · <span class="mono">' +
    esc(s.path || 'settings.json not found') + '</span></p></div>' +
    strip + '</div>' +
    errPanel + contradiction +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">' +
    'Capabilities — config vs reality</div>' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">capability</th><th class="px-3 py-2">state</th>' +
    '<th class="px-3 py-2">why it matters</th><th class="px-3 py-2">tools</th>' +
    '<th class="px-3 py-2">gate (settings key)</th></tr></thead>' +
    '<tbody>' + capRows + '</tbody></table></div></div>' +
    '<div class="grid gap-4 lg:grid-cols-2">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">' +
    'Settings on disk (' + esc(s.key_count || 0) + ' keys)</div>' +
    '<div class="flex flex-col gap-3">' +
    (settingsRows || '<p class="text-xs text-muted">settings.json not found</p>') +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">' +
    'Live probe</div>' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">tool</th><th class="px-3 py-2">result</th>' +
    '<th class="px-3 py-2">time</th><th class="px-3 py-2">detail</th>' +
    '</tr></thead><tbody>' + probeRows + '</tbody></table></div>' +
    '<p class="mt-3 text-xs text-muted">A probe calls the real tool. ' +
    'Read-only: this page never writes settings.</p></div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-1 flex flex-wrap items-center justify-between gap-2">' +
    '<div class="text-xs font-semibold uppercase tracking-wide text-muted">' +
    'All ' + esc((p.tools || []).length) + ' MCP tools — click any tool for full detail</div>' +
    (sideEffectCount
      ? '<span class="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700">' +
        esc(sideEffectCount) + ' with side effects — never auto-probed</span>'
      : '') +
    '</div>' +
    '<p class="mb-3 text-xs text-muted">Each tool shows its description, full input ' +
    'schema (parameters, types, required, defaults) and whether calling it has a ' +
    'side effect. ⚠ marks a tool that captures, records, plays audio, notifies or ' +
    'executes — those are never used as health probes.</p>' +
    '<div class="flex flex-wrap gap-1.5">' + toolButtons + '</div></div>' +
    specPanel +
    '<p class="text-xs text-muted">Safe Actions: <b>Refresh</b> re-probes the ' +
    'server. Changing a setting is a user action — this page is read-only.</p></div>'
  );
}

/** Full-detail modal for one MCP tool. */
export function openMcpToolModal(name, state, esc) {
  const old = document.getElementById('oc-tool-modal');
  if (old) old.remove();
  const details = (state.openclaw && state.openclaw.tool_details) || {};
  const t = ((details.tools || []).find((x) => x.name === name)) || { name: name };
  const params = t.params || [];
  const paramRows = params.length
    ? params
        .map(
          (p) =>
            '<tr class="border-b border-line align-top">' +
            '<td class="mono px-3 py-2 text-xs text-ink">' + esc(p.name) +
            (p.aliases && p.aliases.length
              ? '<span class="ml-1 text-muted">/ ' + esc(p.aliases.join(' / ')) + '</span>'
              : '') +
            (p.required
              ? '<span class="ml-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-medium text-rose-700">required</span>'
              : '') + '</td>' +
            '<td class="mono px-3 py-2 text-xs text-muted">' + esc(p.type) + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' + esc(p.description || '-') + '</td>' +
            '<td class="mono px-3 py-2 text-xs text-muted">' +
            esc(p.default === undefined || p.default === null ? '-' : String(p.default)) +
            (p.enum ? '<div class="mt-1">enum: ' + esc(p.enum.join(' | ')) + '</div>' : '') +
            '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="4" class="px-3 py-4 text-center text-xs text-muted">No parameters.</td></tr>';

  // This server returns an empty inputSchema and documents args in prose, so
  // say where the parameters came from instead of implying a schema existed.
  const paramSource = params.length && params[0].source === 'description prose'
    ? '<p class="mt-2 text-[11px] text-amber-700">Parsed from the tool description — ' +
      'this server returns an empty <span class="mono">inputSchema</span> and documents ' +
      'its arguments as <span class="mono">Args: name (type, required)</span> in prose.</p>'
    : '';

  const sePanel = t.side_effect
    ? '<div class="rounded-xl border border-amber-300 bg-amber-50 p-3">' +
      '<div class="text-xs font-semibold text-amber-900">⚠ Side effect — never auto-probed</div>' +
      '<p class="mt-1 text-xs text-amber-800">' + esc(t.side_effect_reason || 'has a side effect') + '</p>' +
      '<p class="mt-1 text-xs text-amber-700">Calling this tool changes something the user can ' +
      'see or feel. It is excluded from the health probe on purpose.</p></div>'
    : '<div class="rounded-xl border border-emerald-200 bg-emerald-50 p-3">' +
      '<div class="text-xs font-semibold text-emerald-900">Safe to probe</div>' +
      '<p class="mt-1 text-xs text-emerald-800">No capture / record / audio / notify / exec ' +
      'side effect detected.</p></div>';

  const ann = t.annotations || {};
  const annKeys = Object.keys(ann);
  const annPanel = annKeys.length
    ? '<div class="rounded-xl border border-line bg-soft/40 p-3">' +
      '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Annotations</div>' +
      annKeys
        .map(
          (k) =>
            '<div class="flex items-center justify-between gap-3 py-0.5">' +
            '<span class="mono text-xs text-ink">' + esc(k) + '</span>' +
            '<span class="mono text-xs text-muted">' + esc(String(ann[k])) + '</span></div>'
        )
        .join('') + '</div>'
    : '';

  const el = document.createElement('div');
  el.id = 'oc-tool-modal';
  el.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4';
  el.innerHTML =
    '<div class="max-h-[85vh] w-full max-w-3xl overflow-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">' +
    '<div class="mb-3 flex items-start justify-between gap-3">' +
    '<div><div class="mono text-sm font-semibold text-ink">' + esc(t.name) + '</div>' +
    '<p class="mt-1 text-xs text-muted">' + esc(t.description || 'No description provided by the server.') + '</p></div>' +
    '<button id="oc-tool-close" type="button" class="rounded-lg border border-line px-2 py-1 text-sm hover:bg-soft">✕</button>' +
    '</div>' +
    sePanel +
    '<div class="mt-3 rounded-xl border border-line bg-panel p-3">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Parameters (' +
    esc(params.length) + ')</div>' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">name</th><th class="px-3 py-2">type</th>' +
    '<th class="px-3 py-2">description</th><th class="px-3 py-2">default</th>' +
    '</tr></thead><tbody>' + paramRows + '</tbody></table></div>' +
    paramSource + '</div>' +
    (annPanel ? '<div class="mt-3">' + annPanel + '</div>' : '') +
    '<details class="mt-3 rounded-xl border border-line bg-soft/40 p-3">' +
    '<summary class="cursor-pointer text-xs font-semibold uppercase tracking-wide text-muted">' +
    'Raw input schema (JSON)</summary>' +
    '<pre class="mono mt-2 max-h-64 overflow-auto text-[11px] leading-relaxed">' +
    esc(JSON.stringify(t.input_schema || {}, null, 2)) + '</pre></details>' +
    '</div>';

  const close = () => {
    el.remove();
    document.removeEventListener('keydown', onKey);
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  document.addEventListener('keydown', onKey);
  document.body.appendChild(el);
  document.getElementById('oc-tool-close')?.addEventListener('click', close);
}

/** Compact pill for the global status row — makes a dead server visible everywhere. */
export function openclawPill(state, esc) {
  const o = state.openclaw || {};
  if (!o.loaded) return '';
  const down = !o.ok || (o.probe && o.probe.ok === false);
  const failing = (o.summary || {}).capabilities_failing || 0;
  const tone = down
    ? 'bg-rose-50 text-rose-700'
    : failing
      ? 'bg-amber-50 text-amber-700'
      : 'bg-emerald-50 text-emerald-700';
  const label = down
    ? 'OpenClaw DOWN'
    : failing
      ? 'OpenClaw ' + failing + ' failing'
      : 'OpenClaw MCP ON';
  const title = down
    ? ((o.probe && o.probe.error) || o.error || 'server unreachable')
    : (o.summary || {}).tool_count + ' tools';
  return (
    '<button type="button" data-nav="openclaw" title="' + esc(title) +
    '" class="rounded-full px-2.5 py-1 text-xs font-medium ' + tone + '">' +
    esc(label) + '</button>'
  );
}