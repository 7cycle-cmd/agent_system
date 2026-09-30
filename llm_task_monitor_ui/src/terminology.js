/**
 * terminology.js — the REVIEW SURFACE for the PHASED terminology sweep.
 *
 * Route: /llm-tasks/terminology
 *
 * WHY THIS PAGE EXISTS (the user, 2026-09-23):
 *   "for whole site and by phase (volume control) and we can test 7B performance
 *    too with ui report"
 *
 * MEASURED VOLUME: 109 tables, 215 routes, 717 modules -> ~1000+ names. One
 * unbounded run is neither reviewable nor safe, so a phase processes AT MOST its
 * `cap`. THE CAP IS RENDERED, because a capped list shown as the whole set is a
 * lie about what was measured.
 *
 * THE DIVISION OF LABOUR THIS PAGE MUST NOT BLUR
 *   the 7B            drafts term_key + definition        (language)
 *   terminology_cite  verifies or SUPPLIES the cite_ref   (determinism)
 *   add_term          REFUSES an unciteable citation      (the gate)
 *
 * MEASURED: the 7B's citations were NOT usable — it put a refusal in the
 * `cite_ref` field twice and invented a filename once. So the 7B is NEVER asked
 * for a citation, and the page reports `supplied_citations` as the 7B's REAL
 * gap rather than hiding it inside an "accepted" count.
 *
 * THE RENDERING RULES
 * 1. A missing report renders the literal `no report (FAULT)`. NEVER a blank
 *    panel: a blank reads as "no problem", which is the failure this feature
 *    exists to prevent.
 * 2. The CONTROL is shown beside the accepted count. Without it, "accepted"
 *    cannot be told apart from "always says yes".
 * 3. `truncated` is shown whenever the scope is larger than the cap.
 *
 * SSOT:
 *   /api/terminology/phases                  every phase + cap + progress
 *   /api/terminology/sweep/<phase_key>       one phase's recorded history
 *   /api/terminology/sweep/<phase_key>/run   run one phase (capped)
 *   /api/terminology/7b-report               the 7B's measured performance
 */

export const TABS = [
  { id: 'sweep', label: 'Sweep' },
  { id: 'catalog', label: 'Catalog' },
];

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

function outcomeChip(outcome) {
  const map = {
    accepted: 'bg-emerald-100 text-emerald-700',
    refused: 'bg-rose-100 text-rose-700',
    skipped: 'bg-soft text-muted',
    unknown: 'bg-amber-100 text-amber-700',
  };
  return '<span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' +
    (map[outcome] || map.unknown) + '">' + esc(outcome || 'unknown') + '</span>';
}

function controlChip(control) {
  if (!control || !control.ran) {
    return '<span class="rounded-full bg-soft px-2 py-0.5 text-[11px] ' +
      'font-medium text-muted">control not run</span>';
  }
  return control.pass
    ? '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] ' +
      'font-medium text-emerald-700">control PASS (refused)</span>'
    : '<span class="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] ' +
      'font-medium text-rose-700">control FAIL (invented a term)</span>';
}

export function mountTerminology(root, state, helpers) {
  const toast = (helpers && helpers.toast) || (() => {});
  const s = state.terminology || (state.terminology = {
    tab: 'sweep',
    phases: [], phasesMsg: '', phasesLoaded: false,
    selected: null, report: null,
    perf: null, perfMsg: '', perfLoaded: false,
    catalog: null, catalogMsg: '', catalogLoaded: false,
    generator: null, generatorMsg: '', generatorLoaded: false,
    rubbish: null, rubbishMsg: '', rubbishLoaded: false,
    raw: '', rawLabel: '', busy: false,
  });

  async function loadPhases() {
    try {
      const res = await fetch('/api/terminology/phases');
      const d = await res.json();
      s.phases = d.phases || [];
      s.phasesMsg = d.ok ? '' : (d.error || 'terminology phases API error');
      s.phasesLoaded = true;
    } catch (e) {
      s.phasesMsg = 'Terminology phases API unavailable: ' + (e.message || e);
      s.phasesLoaded = true;
    }
    render();
  }

  async function loadPerf() {
    try {
      const res = await fetch('/api/terminology/7b-report');
      const d = await res.json();
      s.perf = d;
      s.perfMsg = d.ok ? '' : (d.error || '7B report API error');
      s.perfLoaded = true;
    } catch (e) {
      s.perfMsg = '7B report API unavailable: ' + (e.message || e);
      s.perfLoaded = true;
    }
    render();
  }

  async function loadCatalog() {
    try {
      const res = await fetch('/api/terminology/catalog');
      const d = await res.json();
      s.catalog = d;
      s.catalogMsg = d.ok ? '' : (d.error || 'catalog API error');
      s.catalogLoaded = true;
    } catch (e) {
      s.catalogMsg = 'Catalog API unavailable: ' + (e.message || e);
      s.catalogLoaded = true;
    }
    render();
  }

  // THE GENERATOR CHECK — does every term_key EQUAL the name its catalog path
  // derives? A MISMATCH is shown WITH both values, so a reader can see WHICH
  // convention the row is in. This is the human's complaint made visible:
  // "have same language, not different language in different place".
  async function loadGenerator() {
    try {
      const res = await fetch('/api/terminology/generator/check');
      const d = await res.json();
      s.generator = d;
      s.generatorMsg = d.ok ? '' : (d.error || 'generator API error');
      s.generatorLoaded = true;
    } catch (e) {
      s.generatorMsg = 'Generator API unavailable: ' + (e.message || e);
      s.generatorLoaded = true;
    }
    render();
  }

  // THE RUBBISH PANEL — the definitions that SAY NOTHING.
  //
  // THE HUMAN: "you love rubbish? taskbar_vscode_app!!!????" / "or you need to
  // have helper to cleanup or rubbish definition".
  //
  // THE RENDERING RULE: each row shows the RULE that fired, so a reader looks in
  // the right place. A row that only said "rubbish" would hide which of the four
  // rules caught it.
  async function loadRubbish() {
    try {
      const res = await fetch('/api/terminology/rubbish');
      const d = await res.json();
      s.rubbish = d;
      s.rubbishMsg = d.ok ? '' : (d.error || 'rubbish API error');
      s.rubbishLoaded = true;
    } catch (e) {
      s.rubbishMsg = 'Rubbish API unavailable: ' + (e.message || e);
      s.rubbishLoaded = true;
    }
    render();
  }

  async function openPhase(phaseKey) {
    s.selected = phaseKey;
    s.report = null;
    s.busy = true;
    render();
    try {
      const res = await fetch('/api/terminology/sweep/' +
        encodeURIComponent(phaseKey));
      s.report = await res.json();
    } catch (e) {
      s.report = { ok: false, error: String(e) };
    }
    s.busy = false;
    render();
  }

  async function runPhase(phaseKey, apply) {
    s.busy = true;
    render();
    try {
      const res = await fetch('/api/terminology/sweep/' +
        encodeURIComponent(phaseKey) + '/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply: !!apply }),
      });
      const d = await res.json();
      if (!d.ok) {
        toast('Run failed: ' + (d.error || 'unknown'));
      } else {
        toast('Phase ' + phaseKey + ': accepted=' + d.accepted +
          ' refused=' + d.refused + ' (cap ' + d.cap + ')');
      }
      showRaw('run ' + phaseKey, d);
    } catch (e) {
      toast('Run failed: ' + (e.message || e));
    }
    s.busy = false;
    await loadPhases();
    await loadPerf();
    if (s.selected === phaseKey) await openPhase(phaseKey);
  }

  function showRaw(label, obj) {
    s.rawLabel = label;
    s.raw = JSON.stringify(obj, null, 2);
    render();
  }

  function phasesHtml() {
    if (!s.phasesLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.phasesMsg) {
      return '<p class="text-sm text-rose-600">' + esc(s.phasesMsg) + '</p>';
    }
    if (!s.phases.length) {
      return '<p class="text-sm text-muted">No sweep phases registered.</p>';
    }
    const rows = s.phases.map((p) =>
      '<tr class="cursor-pointer hover:bg-soft' +
      (s.selected === p.phase_key ? ' bg-accent-soft' : '') +
      '" data-tm-phase="' + esc(p.phase_key) + '">' +
      '<td class="px-3 py-2 text-xs font-semibold">' + esc(p.display_name) +
      '</td>' +
      '<td class="px-3 py-2 text-xs mono">' + esc(p.scope) + '</td>' +
      // THE CAP IS THE VOLUME CONTROL — it is never hidden.
      '<td class="px-3 py-2 text-xs mono">' + esc(p.cap) + '</td>' +
      '<td class="px-3 py-2 text-xs mono">' + esc(p.scope_total) + '</td>' +
      '<td class="px-3 py-2 text-xs mono">' + esc(p.done) +
      (p.dry_only
        ? ' <span class="rounded bg-soft px-1 text-[10px] text-muted" ' +
          'title="asked in a dry run only — NOT registered">+' +
          esc(p.dry_only) + ' dry</span>'
        : '') + '</td>' +
      '<td class="px-3 py-2 text-xs mono">' + esc(p.remaining) + '</td>' +
      '<td class="px-3 py-2">' +
      (p.truncated
        ? '<span class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] ' +
          'font-medium text-amber-800">TRUNCATED</span>'
        : '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] ' +
          'font-medium text-emerald-700">fits</span>') +
      '</td>' +
      '<td class="px-3 py-2 text-[11px] text-muted" title="' + esc(p.why) +
      '">' + esc((p.why || '').slice(0, 60)) + '</td>' +
      '</tr>').join('');
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">phase</th><th class="px-3 py-2">scope</th>' +
      '<th class="px-3 py-2">cap</th><th class="px-3 py-2">scope total</th>' +
      '<th class="px-3 py-2">done</th><th class="px-3 py-2">remaining</th>' +
      '<th class="px-3 py-2">volume</th><th class="px-3 py-2">why</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function reportHtml() {
    if (!s.selected) {
      return '<div class="rounded-xl border border-dashed border-line ' +
        'bg-soft/40 p-4 text-sm text-muted">Pick a phase to see its recorded ' +
        'history.</div>';
    }
    const r = s.report;
    if (!r) return '<p class="text-sm text-muted">Loading…</p>';
    if (!r.ok) {
      // RULE 1: a missing report is NAMED, never blank.
      return '<p class="text-sm text-rose-600">no report (FAULT) · ' +
        esc(r.error || 'unknown') + '</p>';
    }
    const items = r.items || [];
    const rows = items.map((it) =>
      '<tr class="border-t border-line">' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(it.name) + '</td>' +
      '<td class="px-2 py-1.5">' + outcomeChip(it.outcome) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-rose-700 mono">' +
      esc(it.refusal_code || '-') + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted mono">' +
      esc(it.term_id == null ? '-' : it.term_id) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted mono">' +
      (it.llm_ms == null ? '-' : esc(it.llm_ms) + 'ms') + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted">' +
      (it.cite_supplied
        ? '<span class="text-amber-700">supplied by logic generator</span>'
        : 'from the 7B') + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted mono" title="' +
      esc(it.cite_ref || '') + '">' + esc((it.cite_ref || '-').slice(0, 46)) +
      '</td>' +
      '</tr>').join('');
    const codes = Object.entries(r.by_refusal_code || {});
    return '<div class="mb-2 flex flex-wrap gap-2 text-[11px]">' +
      '<span class="rounded-full bg-soft px-2 py-0.5 mono">rows ' +
      esc(r.rows) + '</span>' +
      Object.entries(r.by_outcome || {}).map(([k, v]) =>
        '<span class="rounded-full bg-soft px-2 py-0.5 mono">' + esc(k) +
        ' ' + esc(v) + '</span>').join('') +
      '<span class="rounded-full bg-amber-50 px-2 py-0.5 mono text-amber-800">' +
      'citations supplied by logic generator ' + esc(r.names_supplied) +
      ' name(s) · ' + esc(r.supplied_citations) + ' row(s)</span>' +
      '<span class="rounded-full bg-soft px-2 py-0.5 mono">7B p50 ' +
      esc(r.llm_ms_p50 == null ? '-' : r.llm_ms_p50 + 'ms') + ' · max ' +
      esc(r.llm_ms_max == null ? '-' : r.llm_ms_max + 'ms') + '</span>' +
      (codes.length
        ? '<span class="rounded-full bg-rose-50 px-2 py-0.5 mono text-rose-700">' +
          esc(codes.map(([k, v]) => k + ' ' + v).join(' · ')) + '</span>'
        : '') +
      '</div>' +
      (items.length
        ? '<div class="overflow-auto rounded-xl border border-line">' +
          '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
          '<tr><th class="px-2 py-2">name</th><th class="px-2 py-2">outcome</th>' +
          '<th class="px-2 py-2">refusal</th><th class="px-2 py-2">term_id</th>' +
          '<th class="px-2 py-2">7B ms</th><th class="px-2 py-2">citation</th>' +
          '<th class="px-2 py-2">cite_ref</th></tr></thead><tbody>' + rows +
          '</tbody></table></div>'
        : '<p class="text-sm text-muted">This phase has not been run yet.</p>');
  }

  function perfHtml() {
    if (!s.perfLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.perfMsg) {
      return '<p class="text-sm text-rose-600">no report (FAULT) · ' +
        esc(s.perfMsg) + '</p>';
    }
    const p = s.perf || {};
    const cards = [
      ['model', p.model || '-'],
      ['7B calls', p.llm_calls == null ? '-' : p.llm_calls],
      ['latency p50', p.llm_ms_p50 == null ? '-' : p.llm_ms_p50 + ' ms'],
      ['latency max', p.llm_ms_max == null ? '-' : p.llm_ms_max + ' ms'],
      ['rows recorded', p.total_rows == null ? '-' : p.total_rows],
      // The DISTINCT-NAME count is the honest size of the gap: the row count
      // above double-counts a dry run followed by an apply run.
      ['citation gap (names)', p.names_supplied == null ? '-' :
        p.names_supplied],
      ['citation rows', p.supplied_citations == null ? '-' :
        p.supplied_citations],
    ].map(([k, v]) =>
      '<div class="rounded-xl border border-line bg-soft/40 px-3 py-2">' +
      '<div class="text-[10px] uppercase text-muted">' + esc(k) + '</div>' +
      '<div class="text-sm font-semibold mono">' + esc(v) + '</div></div>')
      .join('');
    const outcomes = Object.entries(p.by_outcome || {});
    const codes = Object.entries(p.by_refusal_code || {});
    const phaseRows = (p.phases || []).map((x) =>
      '<tr class="border-t border-line">' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(x.phase_key) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(x.cap) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(x.rows) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' +
      esc((x.by_outcome || {}).accepted || 0) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' +
      esc((x.by_outcome || {}).refused || 0) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(x.supplied_citations) +
      (x.names_supplied == null ? '' :
        ' (' + esc(x.names_supplied) + ')') +
      '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' +
      esc(x.llm_ms_p50 == null ? '-' : x.llm_ms_p50 + 'ms') + '</td>' +
      '</tr>').join('');
    return '<div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">' +
      cards + '</div>' +
      '<div class="mt-3 flex flex-wrap gap-2 text-[11px]">' +
      outcomes.map(([k, v]) =>
        '<span class="rounded-full bg-soft px-2 py-0.5 mono">' + esc(k) +
        ' ' + esc(v) + '</span>').join('') +
      (codes.length
        ? '<span class="rounded-full bg-rose-50 px-2 py-0.5 mono text-rose-700">' +
          esc(codes.map(([k, v]) => k + ' ' + v).join(' · ')) + '</span>'
        : '') +
      '</div>' +
      '<p class="mt-2 text-[11px] text-muted">' + esc(p.note || '') + '</p>' +
      (phaseRows
        ? '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
          '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
          '<tr><th class="px-2 py-2">phase</th><th class="px-2 py-2">cap</th>' +
          '<th class="px-2 py-2">rows</th><th class="px-2 py-2">accepted</th>' +
          '<th class="px-2 py-2">refused</th>' +
          '<th class="px-2 py-2">supplied</th>' +
          '<th class="px-2 py-2">p50</th></tr></thead><tbody>' + phaseRows +
          '</tbody></table></div>'
        : '');
  }

  // ---------------------------------------------------------------------
  // CATALOG tab — the catalog VIEW over terminology_registry.
  //
  // THE RENDERING RULE THAT MATTERS: an unregistered name is rendered as a
  // VISIBLE GAP, never as blank. A blank row reads as "nothing to see", which
  // is the exact failure this tab exists to prevent.
  // ---------------------------------------------------------------------
  function catalogHtml() {
    if (!s.catalogLoaded) {
      return '<p class="text-sm text-muted">loading catalog…</p>';
    }
    if (s.catalogMsg) {
      return '<p class="text-sm text-rose-700">' + esc(s.catalogMsg) + '</p>';
    }
    const c = s.catalog || {};
    const root = c.root;
    const kids = c.children || [];
    const gap = c.unregistered || [];
    // THE WHOLE SUBTREE. MEASURED 2026-09-27: the panel showed DIRECT CHILDREN
    // ONLY, so `taskbar_vscode`'s 2 children were INVISIBLE and a reader could
    // not tell whether the group holds an APP, a browser, or nothing.
    const subtree = c.subtree || [];

    const rootHtml = root
      ? '<div class="rounded-xl border border-line bg-soft/60 p-3">' +
        '<div class="flex flex-wrap items-center gap-2">' +
        '<span class="rounded-full bg-panel px-2 py-0.5 text-[11px] mono">' +
        esc(root.term_key) + '</span>' +
        '<span class="rounded-full bg-panel px-2 py-0.5 text-[11px] mono">' +
        'term_id ' + esc(root.term_id) + '</span>' +
        '<span class="rounded-full bg-panel px-2 py-0.5 text-[11px] mono">' +
        esc(root.term_kind) + '</span>' +
        '<span class="rounded-full bg-panel px-2 py-0.5 text-[11px] mono">' +
        'parent_term_id NULL (a catalog root)</span>' +
        '</div>' +
        '<p class="mt-2 text-xs text-muted">' + esc(root.definition) + '</p>' +
        '<p class="mt-1 text-[11px] text-muted mono">' +
        esc(root.cite_ref) + '</p></div>'
      : '<p class="text-sm text-rose-700">no catalog root — ' +
        esc(c.error || 'unknown') + '</p>';

    // THE SUBTREE ROWS, INDENTED BY DEPTH. A grandchild is indented under its
    // parent, so a reader SEES what a group contains. The indent is the depth
    // times 14px, and the depth is DERIVED (never stored).
    const subRows = subtree.map((r) => {
      const pad = (r.depth || 0) * 14;
      const isGroup = r.node_kind === 'group';
      return '<tr class="border-t border-line">' +
        '<td class="px-2 py-1.5 text-xs mono">' +
        '<span style="display:inline-block;width:' + pad + 'px"></span>' +
        (r.depth ? '<span class="text-muted">└ </span>' : '') +
        esc(r.term_key) + '</td>' +
        '<td class="px-2 py-1.5 text-[11px] mono text-muted">' +
        esc(r.depth) + '</td>' +
        '<td class="px-2 py-1.5">' +
        (isGroup
          ? '<span class="rounded bg-indigo-100 px-1.5 py-0.5 text-[11px] ' +
            'mono text-indigo-700">group · ' + esc(r.children) + '</span>'
          : '<span class="rounded bg-soft px-1.5 py-0.5 text-[11px] mono ' +
            'text-muted">leaf</span>') + '</td>' +
        '<td class="px-2 py-1.5 text-[11px] mono text-muted">' +
        esc(r.term_id) + '</td></tr>';
    }).join('');

    const kidRows = kids.map((k) =>
      '<tr class="border-t border-line">' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(k.term_key) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(k.term_kind) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(k.taxonomy_level) + '</td>' +
      '<td class="px-2 py-1.5 text-xs">' + esc(k.definition) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] mono text-muted">' +
      esc(k.cite_ref) + '</td></tr>').join('');
    // THE GAP. Each unregistered name is a row that says so, in red.
    const gapRows = gap.map((g) =>
      '<tr class="border-t border-line bg-rose-50">' +
      '<td class="px-2 py-1.5 text-xs mono text-rose-700">' + esc(g.name) +
      '</td>' +
      '<td class="px-2 py-1.5 text-xs text-rose-700">' + esc(g.label) + '</td>' +
      '<td class="px-2 py-1.5 text-xs text-rose-700" colspan="3">' +
      'NO TERM — this name is undefined</td></tr>').join('');

    return rootHtml +
      '<div class="mt-3 flex flex-wrap gap-2 text-[11px]">' +
      '<span class="rounded-full bg-soft px-2 py-0.5 mono">' +
      esc(kids.length) + ' direct entr' + (kids.length === 1 ? 'y' : 'ies') +
      '</span>' +
      '<span class="rounded-full bg-soft px-2 py-0.5 mono">' +
      esc(subtree.length) + ' in the whole subtree (depth ' +
      esc(c.max_depth || 0) + ')</span>' +
      '<span class="rounded-full ' +
      (gap.length ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700') +
      ' px-2 py-0.5 mono">' + esc(gap.length) + ' unregistered name(s)</span>' +
      '</div>' +
      // THE SUBTREE FIRST: it is the answer to "what does this group contain?".
      '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left">' +
      '<thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-2 py-2">term_key (whole subtree)</th>' +
      '<th class="px-2 py-2">depth</th><th class="px-2 py-2">node kind</th>' +
      '<th class="px-2 py-2">term_id</th></tr></thead><tbody>' +
      (subRows ||
        '<tr><td class="px-2 py-2 text-xs text-muted" colspan="4">' +
        'no entries</td></tr>') +
      '</tbody></table></div>' +
      '<p class="mt-2 text-[11px] text-muted">The WHOLE SUBTREE, indented by ' +
      '<span class="mono">depth</span>. MEASURED 2026-09-27: the panel showed ' +
      'DIRECT CHILDREN ONLY, so <span class="mono">taskbar_vscode</span>\'s 2 ' +
      'children were invisible. <span class="mono">depth</span> is DERIVED from ' +
      '<span class="mono">parent_term_id</span>, never stored.</p>' +
      '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left">' +
      '<thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-2 py-2">term_key</th><th class="px-2 py-2">kind</th>' +
      '<th class="px-2 py-2">layer</th><th class="px-2 py-2">definition</th>' +
      '<th class="px-2 py-2">cite_ref</th></tr></thead><tbody>' +
      (kidRows || gapRows ||
        '<tr><td class="px-2 py-2 text-xs text-muted" colspan="5">' +
        'no entries</td></tr>') +
      (kidRows && gapRows ? gapRows : '') +
      '</tbody></table></div>' +
      '<p class="mt-2 text-[11px] text-muted">A CATALOG is a term whose ' +
      'children are its entries (<span class="mono">parent_term_id</span>). ' +
      'A LAYER is what KIND of thing a term is ' +
      '(<span class="mono">taxonomy_level</span>). Two hierarchies, one job ' +
      'each — so no third table is needed.</p>';
  }

  // THE NODE KIND BADGE — is this row a GROUP or a LEAF?
  //
  // THE HUMAN: "i have taskbar_vscode and taskbar_vscode_app" / "which is
  // rubbish" / "why taskbar_vscode not = taskbar_vscode_app".
  //
  // MEASURED: both carried term_kind='entity', so a reader saw two identical
  // rows. The kind is DERIVED from children > 0 (never stored), and this badge
  // is what makes the two rows distinguishable AT A GLANCE.
  //
  // A GROUP is indigo and says how many children it holds; a LEAF is muted. The
  // colour is the point: a reader scanning the column sees the containers.
  function nodeKindBadge(r) {
    const k = (r && r.node_kind) || '';
    const n = (r && r.children) || 0;
    if (k === 'group') {
      return '<span class="rounded bg-indigo-100 px-1.5 py-0.5 text-[11px] ' +
        'mono text-indigo-700" title="it has children">group · ' +
        esc(n) + '</span>';
    }    if (k === 'leaf') {
      return '<span class="rounded bg-soft px-1.5 py-0.5 text-[11px] mono ' +
        'text-muted" title="it has no children">leaf</span>';
    }
    return '<span class="text-[11px] text-muted">—</span>';
  }

  // ---------------------------------------------------------------------
  // THE GENERATOR — the name DERIVED from the catalog path.
  //
  // THE HUMAN: "you are not helping to have standardize for terminology
  // generator ? with catalog > subcatalog" / "example taskbar > widgets ,
  // terminology generator = taskbar_widgets" / "have same language, not
  // different language in different place".
  //
  // THE RENDERING RULE: a MISMATCH shows BOTH the current name and the derived
  // one, so a reader can see WHICH convention the row is in. A row that only
  // said "mismatch" would hide the thing the human complained about.
  // ---------------------------------------------------------------------
  function generatorHtml() {
    if (!s.generatorLoaded) {
      return '<p class="text-sm text-muted">loading generator check…</p>';
    }
    if (s.generatorMsg) {
      return '<p class="text-sm text-rose-700">' + esc(s.generatorMsg) + '</p>';
    }
    const g = s.generator || {};
    const rows = g.rows || [];
    const bad = g.mismatches || [];
    const drift = g.drift || {};

    const chips =
      '<div class="flex flex-wrap gap-2 text-[11px]">' +
      '<span class="rounded-full bg-soft px-2 py-0.5 mono">' +
      esc(g.checked || 0) + ' term(s) checked</span>' +
      '<span class="rounded-full ' +
      (bad.length ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700') +
      ' px-2 py-0.5 mono">' + esc(bad.length) + ' name mismatch(es)</span>' +
      '<span class="rounded-full ' +
      ((drift.only_in_target || []).length
        ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700') +
      ' px-2 py-0.5 mono">' + esc((drift.only_in_target || []).length) +
      ' target_template drift</span>' +
      '</div>';

    // THE COLUMN ORDER MUST MATCH THE HEADER. MEASURED DEFECT (2026-09-27):
    // the header read `name | path (segments) | derived name` while the body
    // emitted `term_key | derived | path` -- so the "path" column showed the
    // NAME and the "derived name" column showed the PATH. A table whose header
    // and body disagree is worse than no table: the reader trusts the header.
    const badRows = bad.map((m) =>
      '<tr class="border-t border-line bg-rose-50">' +
      '<td class="px-2 py-1.5 text-xs mono text-rose-700">' + esc(m.term_key) +
      '</td>' +
      '<td class="px-2 py-1.5 text-[11px] mono text-rose-700">' +
      esc((m.path || []).join(' > ')) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono text-rose-700">' + esc(m.derived) +
      '</td>' +
      '<td class="px-2 py-1.5">' + nodeKindBadge(m) + '</td></tr>').join('');

    const okRows = rows.filter((r) => r.match).map((r) =>
      '<tr class="border-t border-line">' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(r.term_key) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] mono text-muted">' +
      esc((r.path || []).join(' > ')) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono text-muted">' + esc(r.derived) +
      '</td>' +
      '<td class="px-2 py-1.5">' + nodeKindBadge(r) + '</td></tr>').join('');

    return chips +
      '<p class="mt-2 text-[11px] text-muted">A child\'s ' +
      '<span class="mono">term_key</span> MUST START WITH its parent\'s ' +
      '<span class="mono">term_key</span> + <span class="mono">_</span>. ' +
      'The catalog path is the SSOT; the name is a PROJECTION of it, so the ' +
      'name cannot drift from the catalog.</p>' +
      '<p class="mt-1 text-[11px] text-muted">' +
      '<span class="rounded bg-indigo-100 px-1.5 py-0.5 mono text-indigo-700">' +
      'group</span> = it HAS children (a container); ' +
      '<span class="rounded bg-soft px-1.5 py-0.5 mono">leaf</span> = it has ' +
      'none. The kind is DERIVED from <span class="mono">children &gt; 0</span>, ' +
      'never stored, so it cannot drift.</p>' +
      '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left">' +
      '<thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-2 py-2">name (the full key)</th>' +
      '<th class="px-2 py-2">path (segments)</th>' +
      '<th class="px-2 py-2">derived name</th>' +
      '<th class="px-2 py-2">node kind</th></tr></thead><tbody>' +
      (badRows || '') + (okRows || '') +
      (!rows.length
        ? '<tr><td class="px-2 py-2 text-xs text-muted" colspan="4">' +
          'no terms</td></tr>' : '') +
      '</tbody></table></div>';
  }

  // ---------------------------------------------------------------------
  // THE RUBBISH PANEL — the definitions that SAY NOTHING.
  //
  // THE HUMAN: "you love rubbish? taskbar_vscode_app!!!????" / "or you need to
  // have helper to cleanup or rubbish definition".
  //
  // THE RENDERING RULE: each row shows the RULE that fired, so a reader looks in
  // the right place. A row that only said "rubbish" would hide which of the four
  // rules caught it.
  // ---------------------------------------------------------------------
  function rubbishHtml() {
    if (!s.rubbishLoaded) {
      return '<p class="text-sm text-muted">loading rubbish scan…</p>';
    }
    if (s.rubbishMsg) {
      return '<p class="text-sm text-rose-700">' + esc(s.rubbishMsg) + '</p>';
    }
    const r = s.rubbish || {};
    const rows = r.rows || [];
    const byRule = r.by_rule || {};
    const rules = r.rules_applied || [];

    const chips =
      '<div class="flex flex-wrap gap-2 text-[11px]">' +
      '<span class="rounded-full bg-soft px-2 py-0.5 mono">' +
      esc(r.checked || 0) + ' term(s) checked</span>' +
      '<span class="rounded-full ' +
      (rows.length ? 'bg-rose-100 text-rose-700' : 'bg-emerald-100 text-emerald-700') +
      ' px-2 py-0.5 mono">' + esc(rows.length) + ' rubbish</span>' +
      rules.map((k) =>
        '<span class="rounded-full bg-soft px-2 py-0.5 mono">' + esc(k) +
        ' ' + esc(byRule[k] || 0) + '</span>').join('') +
      '</div>';

    const badRows = rows.map((x) =>
      '<tr class="border-t border-line bg-rose-50">' +
      '<td class="px-2 py-1.5 text-xs mono text-rose-700">' + esc(x.term_key) +
      '</td>' +
      '<td class="px-2 py-1.5 text-xs mono text-rose-700">' + esc(x.rule) +
      '</td>' +
      '<td class="px-2 py-1.5 text-xs text-rose-700">' +
      esc((x.definition || '').slice(0, 80)) + '</td></tr>').join('');

    return chips +
      '<p class="mt-2 text-[11px] text-muted">A definition must say what the ' +
      'thing IS. The four rules are MEASURED, not a taste judgement: ' +
      '<span class="mono">empty</span>, ' +
      '<span class="mono">restates_the_name</span>, ' +
      '<span class="mono">boilerplate</span>, ' +
      '<span class="mono">too_short</span> (below 20 chars — the shortest real ' +
      'definition is 64).</p>' +
      '<p class="mt-1 text-[11px] text-muted">This panel WRITES NOTHING. A ' +
      'cleanup that rewrites definitions it did not write is a second author; ' +
      'the human decides.</p>' +
      (rows.length
        ? '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
          '<table class="min-w-full text-left">' +
          '<thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
          '<tr><th class="px-2 py-2">term_key</th>' +
          '<th class="px-2 py-2">rule</th>' +
          '<th class="px-2 py-2">definition</th></tr></thead><tbody>' +
          badRows + '</tbody></table></div>'
        : '');
  }

  function render() {
    if (!root) return;
    const tab = s.tab || 'sweep';
    const tabBar = TABS.map((t) =>
      '<button type="button" data-tm-tab="' + esc(t.id) + '" class="rounded-xl ' +
      'px-3 py-1.5 text-sm ' +
      (t.id === tab ? 'bg-panel border border-line font-medium'
                    : 'text-muted hover:bg-soft') + '">' +
      esc(t.label) + '</button>').join('');

    const body = tab === 'catalog'
      ? '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
        '<h3 class="text-sm font-semibold">Catalog · ' +
        esc((s.catalog && s.catalog.root_key) || 'taskbar') + '</h3>' +
        '<span class="text-[11px] text-muted mono">' +
        'parent_term_id · taxonomy_level</span></div>' +
        catalogHtml() + '</section>' +

        '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
        '<h3 class="text-sm font-semibold">Generator · name derived from the ' +
        'catalog path</h3>' +
        '<span class="text-[11px] text-muted mono">' +
        'terminology_generator</span></div>' +
        generatorHtml() + '</section>' +

        '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
        '<h3 class="text-sm font-semibold">Rubbish definitions</h3>' +
        '<span class="text-[11px] text-muted mono">' +
        'terminology_rubbish</span></div>' +
        rubbishHtml() + '</section>'
      : '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
        '<h3 class="text-sm font-semibold">Phases &amp; volume control</h3>' +
        '<span class="text-[11px] text-muted mono">' +
        esc(s.phases.length) + ' phase(s)</span></div>' +
        phasesHtml() + '</section>' +

        '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
        '<h3 class="text-sm font-semibold">Phase history' +
        (s.selected ? ' · ' + esc(s.selected) : '') + '</h3>' +
        (s.selected
          ? '<div class="flex gap-2">' +
            '<button id="tm-dry" type="button" class="rounded-xl border border-line bg-panel px-3 py-1 text-xs hover:bg-soft">Dry run</button>' +
            '<button id="tm-apply" type="button" class="rounded-xl border border-line bg-panel px-3 py-1 text-xs hover:bg-soft">Run &amp; apply</button>' +
            '</div>'
          : '') +
        '</div>' +
        reportHtml() + '</section>' +

        '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<h3 class="mb-2 text-sm font-semibold">7B performance (measured)</h3>' +
        perfHtml() + '</section>';

    root.innerHTML =
      '<div class="flex flex-wrap items-center justify-between gap-2">' +
      '<div><h2 class="text-lg font-semibold">Terminology</h2>' +
      '<p class="mt-0.5 text-sm text-muted">The whole site\'s names, registered ' +
      'in BOUNDED PHASES. The 7B drafts the language; ' +
      '<span class="mono">terminology_cite</span> supplies and verifies the ' +
      'citation; <span class="mono">add_term</span> refuses an unciteable one.</p>' +
      '</div>' +
      '<div class="flex gap-2">' +
      '<button id="tm-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
      '</div></div>' +

      '<div class="mt-3 flex gap-1 rounded-xl bg-soft/60 p-1">' + tabBar + '</div>' +

      body +

      (s.raw
        ? '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
          '<h3 class="mb-2 text-sm font-semibold">Raw JSON · ' + esc(s.rawLabel) +
          '</h3>' +
          '<pre class="mono max-h-72 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs whitespace-pre-wrap">' +
          esc(s.raw) + '</pre></section>'
        : '');

    const rf = root.querySelector('#tm-refresh');
    if (rf) rf.addEventListener('click', () => {
      if (tab === 'catalog') { loadCatalog(); loadGenerator(); loadRubbish(); }
      else { loadPhases(); loadPerf(); }
    });
    const dry = root.querySelector('#tm-dry');
    if (dry) dry.addEventListener('click', () => runPhase(s.selected, false));
    const ap = root.querySelector('#tm-apply');
    if (ap) ap.addEventListener('click', () => runPhase(s.selected, true));

    root.querySelectorAll('[data-tm-tab]').forEach((b) => {
      b.addEventListener('click', () => {
        s.tab = b.getAttribute('data-tm-tab');
        render();
        if (s.tab === 'catalog') {
          if (!s.catalogLoaded) loadCatalog();
          if (!s.generatorLoaded) loadGenerator();
          if (!s.rubbishLoaded) loadRubbish();
        }
      });
    });

    root.querySelectorAll('[data-tm-phase]').forEach((tr) => {
      tr.addEventListener('click', () =>
        openPhase(tr.getAttribute('data-tm-phase')));
    });
  }

  loadPhases();
  loadPerf();
  loadCatalog();
  loadGenerator();
  loadRubbish();
}
