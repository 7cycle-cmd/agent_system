/**
 * Evidence Center — QC evidence with URL-addressable detail.
 *
 * Routes:
 *   /llm-tasks/evidence              list of every stored evidence
 *   /llm-tasks/evidence/<evidence_id>  one record, human-readable
 *
 * Detail is URL-addressable on purpose: a verdict can be REPORTED AS A LINK
 * instead of pasted text, e.g.
 *   http://127.0.0.1:18765/llm-tasks/evidence/EVID-perm_default-20260919-221649
 * A reviewer opens it and sees the image + the report. That is the whole point.
 */

import { fmtLocal } from './timefmt.js';

const V_BADGE = {
  PASS: { bg: 'rgba(16,185,129,.14)', fg: '#047857', label: 'PASS' },
  FAIL: { bg: 'rgba(244,63,94,.14)', fg: '#be123c', label: 'FAIL' },
  UNKNOWN: { bg: 'rgba(107,114,128,.16)', fg: '#4b5563', label: 'UNKNOWN' },
};

export function evBadge(verdict) {
  const b = V_BADGE[verdict] || V_BADGE.UNKNOWN;
  return (
    '<span style="background:' + b.bg + ';color:' + b.fg +
    ';border-radius:999px;padding:2px 9px;font-size:11px;font-weight:600">' +
    b.label +
    '</span>'
  );
}

function fileUrl(id, name) {
  return (
    '/api/evidence/' + encodeURIComponent(id) + '/file/' + encodeURIComponent(name)
  );
}

/** One-line prompt preview for the report table. */
function shortPrompt(p) {
  const s = String(p || '').replace(/\s+/g, ' ').trim();
  if (!s) return '—';
  return s.length > 70 ? s.slice(0, 70) + '…' : s;
}

/* ------------------------------------------------------------------ list -- */

export function evidenceListHtml(state, esc) {
  const e = state.evidence || {};
  const rows = e.rows || [];

  if (e.msg) {
    return (
      '<div class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">' +
      esc(e.msg) +
      '</div>'
    );
  }

  if (!rows.length) {
    return (
      '<div class="mx-auto max-w-3xl rounded-2xl border border-line bg-panel p-8 text-center shadow-panel">' +
      '<h2 class="text-lg font-semibold text-ink">No evidence yet</h2>' +
      '<p class="mt-2 text-sm text-muted">Run a verification to create one:</p>' +
      '<pre class="mono mt-3 inline-block rounded-xl border border-line bg-soft px-4 py-2 text-xs text-ink">' +
      'python f_perm_click.py --verify-area default</pre>' +
      '<p class="mt-4 text-xs text-muted">Root: <span class="mono">' +
      esc(e.root || '-') +
      '</span></p></div>'
    );
  }

  const filter = esc(e.filter || '');
  const trs = rows
    .map(
      (r) =>
        '<tr class="cursor-pointer border-b border-line transition hover:bg-soft" data-ev-open="' +
        esc(r.evidence_id) + '">' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(fmtLocal(r.created_at)) + '</td>' +
        '<td class="mono px-3 py-2 text-xs text-ink">' + esc(r.evidence_id) + '</td>' +
        '<td class="mono px-3 py-2 text-xs text-muted">' + esc(r.task_id || '—') + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(shortPrompt(r.prompt)) + '</td>' +
        '<td class="px-3 py-2">' + evBadge(r.verdict) + '</td>' +
        '<td class="px-3 py-2 text-xs font-medium text-accent">open →</td></tr>'
    )
    .join('');

  const nPass = rows.filter((r) => r.verdict === 'PASS').length;
  const nFail = rows.filter((r) => r.verdict === 'FAIL').length;
  const nUnk = rows.length - nPass - nFail;

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold text-ink">Evidence Center</h2>' +
    '<p class="mt-0.5 text-sm text-muted">QC evidence with images + reports · ' +
    '<span class="mono">' + esc(e.root || '-') + '</span></p></div>' +
    '<div class="flex flex-wrap items-center gap-2">' +
    '<span class="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">' + nPass + ' pass</span>' +
    '<span class="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700">' + nFail + ' fail</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs font-medium text-muted">' + nUnk + ' unknown</span>' +
    '<input id="ev-filter" placeholder="filter target…" value="' + filter +
    '" class="rounded-xl border border-line px-3 py-1.5 text-sm" />' +
    '<button id="btn-ev-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div></div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">date</th><th class="px-3 py-2">test id</th>' +
    '<th class="px-3 py-2">task id</th><th class="px-3 py-2">prompt</th>' +
    '<th class="px-3 py-2">result</th><th class="px-3 py-2"></th>' +
    '</tr></thead><tbody>' + trs + '</tbody></table></div></div>' +
    '<p class="text-xs text-muted">Click a row to open the full record. The URL is ' +
    'shareable — paste it to report a verdict by link.</p></div>'
  );
}

/* ------------------------------------------------------------- LLM 100 -- */

/**
 * LLM 100 proof — the model's OWN streak evidence.
 *
 * Distinct from the contract gate streak: here every round is a real model
 * answer compared against an oracle, so the streak is a capability proof.
 * SSOT = llm_100_run table via /api/evidence/llm-100.
 */
export function llm100Html(state, esc) {
  const d = state.evidence.llm100;
  if (!d) {
    return '<div class="mx-auto max-w-3xl p-6 text-sm text-muted">Loading LLM 100…</div>';
  }
  if (d.ok === false) {
    return (
      '<div class="mx-auto max-w-3xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">' +
      'Cannot load LLM 100: ' + esc(d.error || 'unknown error') + '</div>'
    );
  }
  if (!d.exists) {
    return (
      '<div class="mx-auto max-w-3xl rounded-2xl border border-line bg-panel p-8 text-center shadow-panel">' +
      '<h2 class="text-lg font-semibold text-ink">No LLM 100 runs yet</h2>' +
      '<p class="mt-2 text-sm text-muted">Run the harness to create proof:</p>' +
      '<pre class="mono mt-3 inline-block rounded-xl border border-line bg-soft px-4 py-2 text-xs text-ink">' +
      'python phone_100_proof_runner.py</pre></div>'
    );
  }

  const runs = d.runs || [];
  const rounds = d.rounds || [];

  const runRows = runs.length
    ? runs
        .map(
          (r) =>
            '<tr class="border-b border-line">' +
            '<td class="mono px-3 py-2 text-xs text-ink">' + esc(r.ref_tag) + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">v' + esc(r.rule_version) + '</td>' +
            '<td class="mono px-3 py-2 text-xs text-ink">' + esc(r.model || '-') + '</td>' +
            '<td class="px-3 py-2 text-xs">' + esc(r.rounds) + '</td>' +
            '<td class="px-3 py-2 text-xs text-emerald-700">' + esc(r.wins) + '</td>' +
            '<td class="px-3 py-2 text-xs text-rose-700">' + esc(r.losses) + '</td>' +
            '<td class="px-3 py-2 text-xs font-semibold text-ink">' +
            esc(r.current_streak) + '/' + esc(r.target) + '</td>' +
            '<td class="px-3 py-2">' +
            (r.qualified
              ? '<span class="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">QUALIFIED</span>'
              : '<span class="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">in progress</span>') +
            '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="8" class="px-3 py-4 text-center text-xs text-muted">無相關記錄。</td></tr>';

  const roundRows = rounds.length
    ? rounds
        .map(
          (r) =>
            '<tr class="border-b border-line">' +
            '<td class="px-3 py-1.5 text-xs text-muted">' + esc(r.round_no) + '</td>' +
            '<td class="mono px-3 py-1.5 text-xs text-ink">' + esc(r.value) + '</td>' +
            '<td class="px-3 py-1.5 text-xs">' + esc(r.oracle_answer) + '</td>' +
            '<td class="px-3 py-1.5 text-xs">' + esc(r.llm_answer) + '</td>' +
            '<td class="px-3 py-1.5 text-xs font-semibold ' +
            (r.win ? 'text-emerald-700">✓ win' : 'text-rose-700">✗ loss') +
            '</td>' +
            '<td class="px-3 py-1.5 text-xs text-muted">' +
            esc(r.failure_reason || '—') + '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="6" class="px-3 py-4 text-center text-xs text-muted">無相關記錄。</td></tr>';

  const nQual = runs.filter((r) => r.qualified).length;

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold text-ink">LLM 100 Proof</h2>' +
    '<p class="mt-0.5 text-sm text-muted">Real model answers vs oracle · target ' +
    esc(d.target) + ' consecutive wins · SSOT <span class="mono">llm_100_run</span></p></div>' +
    '<div class="flex flex-wrap items-center gap-2">' +
    '<span class="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">' +
    nQual + ' qualified</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs font-medium text-muted">' +
    runs.length + ' runs</span>' +
    '<button id="btn-llm100-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div></div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">ref_tag</th><th class="px-3 py-2">ver</th>' +
    '<th class="px-3 py-2">model</th><th class="px-3 py-2">rounds</th>' +
    '<th class="px-3 py-2">wins</th><th class="px-3 py-2">losses</th>' +
    '<th class="px-3 py-2">streak</th><th class="px-3 py-2">status</th>' +
    '</tr></thead><tbody>' + runRows + '</tbody></table></div></div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">' +
    '<div class="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-muted">Recent rounds</div>' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">#</th><th class="px-3 py-2">value</th>' +
    '<th class="px-3 py-2">oracle</th><th class="px-3 py-2">llm</th>' +
    '<th class="px-3 py-2">result</th><th class="px-3 py-2">failure</th>' +
    '</tr></thead><tbody>' + roundRows + '</tbody></table></div></div>' +
    '<p class="text-xs text-muted">This is the model capability proof. It is ' +
    'separate from the contract gate streak computed by <span class="mono">_run_p2_streak.py</span>.</p>' +
    '</div>'
  );
}

/* ---------------------------------------------------------------- detail -- */

export function evidenceDetailHtml(state, esc) {
  const e = state.evidence || {};
  const id = e.detailId || '';
  const d = e.detail;

  if (!d) {
    return '<div class="mx-auto max-w-3xl p-6 text-sm text-muted">Loading ' +
      esc(id) + '…</div>';
  }
  if (d.ok === false) {
    return (
      '<div class="mx-auto max-w-3xl rounded-2xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700">' +
      'Cannot load ' + esc(id) + ': ' + esc(d.error || 'unknown error') +
      '</div>'
    );
  }

  const c = d.classify || {};
  const vl = c.vl || {};
  const q = c.questions || {};
  const edges = c.edges || [];
  const prov = c.provenance || {};
  const rc = c.root_cause || {};
  const files = Object.keys(d.files || {});
  const hasOverlay = files.includes('overlay.png');
  const hasVl = files.includes('overlay_vl.png');
  const hasShot = files.includes('shot.png');
  const hasClassify = files.includes('classify.json');
  const label = c.label || '';
  const taskId = c.task_id || d.task_id || '';

  // Root cause: a bare FAIL is a dead end, so surface WHY + the next action.
  const RC_STYLE = {
    pass: { bg: 'rgba(16,185,129,.12)', fg: '#047857' },
    source_suspect: { bg: 'rgba(245,158,11,.16)', fg: '#b45309' },
    geometry_fail: { bg: 'rgba(244,63,94,.12)', fg: '#be123c' },
    text_mismatch: { bg: 'rgba(244,63,94,.12)', fg: '#be123c' },
    box_not_seen: { bg: 'rgba(107,114,128,.16)', fg: '#4b5563' },
    no_vl_answer: { bg: 'rgba(107,114,128,.16)', fg: '#4b5563' },
    unknown: { bg: 'rgba(107,114,128,.16)', fg: '#4b5563' },
  };
  const rcStyle = RC_STYLE[rc.category] || RC_STYLE.unknown;
  const rootCauseCard = rc.category
    ? '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Why this verdict</div>' +
      '<div class="flex flex-wrap items-center gap-2">' +
      '<span style="background:' + rcStyle.bg + ';color:' + rcStyle.fg +
      ';border-radius:999px;padding:3px 10px;font-size:12px;font-weight:600">' +
      esc(rc.category) + '</span>' +
      '<span class="text-sm text-ink">' + esc(rc.action || '') + '</span></div></div>'
    : '';

  // Provenance: without it a FAIL cannot be re-inspected.
  const provRows = [
    ['source', prov.source],
    ['captured at', prov.captured_at],
    ['image size', prov.width && prov.height ? prov.width + '×' + prov.height : null],
    ['real screen', (prov.real_screen || []).join('×') || null],
    ['bytes', prov.bytes],
    ['sha256', prov.sha256 ? String(prov.sha256).slice(0, 24) + '…' : null],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '');
  const provCard = provRows.length
    ? '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Capture provenance</div>' +
      '<table class="w-full text-left"><tbody>' +
      provRows.map(([k, v]) =>
        '<tr class="border-b border-line"><td class="px-2 py-1 text-xs text-muted">' + esc(k) +
        '</td><td class="mono px-2 py-1 text-xs text-ink">' + esc(v) + '</td></tr>'
      ).join('') +
      '</tbody></table>' +
      (prov.source === 'unknown' || !prov.sha256
        ? '<p class="mt-2 text-xs text-amber-700">Provenance incomplete — treat this verdict as UNKNOWN, not FAIL.</p>'
        : '') +
      '</div>'
    : '';

  const edgeRows = edges.length
    ? edges
        .map(
          (ed) =>
            '<tr class="border-b border-line"><td class="mono px-3 py-1.5 text-xs text-ink">' +
            esc(ed.edge) + '</td>' +
            '<td class="px-3 py-1.5 text-xs text-muted">' + esc(ed.candidate) + '</td>' +
            '<td class="px-3 py-1.5 text-xs text-muted">' +
            esc(ed.detected === null || ed.detected === undefined ? '—' : ed.detected) + '</td>' +
            '<td class="px-3 py-1.5 text-xs text-muted">' +
            esc(ed.delta === null || ed.delta === undefined ? '—' : ed.delta) + '</td>' +
            '<td class="px-3 py-1.5 text-xs font-semibold text-ink">' + esc(ed.verdict) + '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="5" class="px-3 py-4 text-center text-xs text-muted">no edge data</td></tr>';

  const qCard = (key, label) => {
    const it = q[key] || {};
    return (
      '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
      '<div class="text-[11px] uppercase tracking-wide text-muted">' + esc(label) + '</div>' +
      '<div class="mt-1 text-sm font-semibold text-ink">' + esc(it.answer || '—') + '</div>' +
      '<div class="mt-1 text-xs text-muted">' + esc(it.reason || 'no reason recorded') + '</div></div>'
    );
  };

  // The prompt is stored with the record. Older records predate it, so fall
  // back to the current template with an explicit warning rather than showing
  // nothing (a blank prompt is what made this page unreadable).
  const promptQ1 = c.prompt_q1 || '';
  const promptQ2 = c.prompt_q2 || '';
  const promptStored = !!(promptQ1 || promptQ2);
  const promptCard =
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
    '<div class="text-xs font-semibold uppercase tracking-wide text-muted">Prompt sent to the VL</div>' +
    '<span class="rounded-full ' +
    (promptStored ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700') +
    ' px-2 py-0.5 text-[11px] font-medium">' +
    (promptStored ? 'stored with this record' : 'not stored (older record)') +
    '</span></div>' +
    (promptStored
      ? (promptQ1
          ? '<div class="mt-2 text-[11px] font-semibold uppercase tracking-wide text-muted">Q1</div>' +
            '<pre class="mono mt-1 max-h-40 overflow-auto rounded-xl border border-line bg-soft/60 p-3 text-xs leading-relaxed whitespace-pre-wrap">' +
            esc(promptQ1) + '</pre>'
          : '') +
        (promptQ2
          ? '<div class="mt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">Q2</div>' +
            '<pre class="mono mt-1 max-h-40 overflow-auto rounded-xl border border-line bg-soft/60 p-3 text-xs leading-relaxed whitespace-pre-wrap">' +
            esc(promptQ2) + '</pre>'
          : '')
      : '<p class="text-xs text-amber-700">This record was written before the prompt ' +
        'was stored. The template below is the CURRENT one and may differ from ' +
        'what was actually sent.</p>' +
        '<pre class="mono mt-2 max-h-40 overflow-auto rounded-xl border border-line bg-soft/60 p-3 text-xs leading-relaxed whitespace-pre-wrap">' +
        esc('You are a strict visual inspector.\n' +
            'The image has a RED BOX drawn on it. The red box marks the region of interest.\n' +
            'Question: does the text inside the RED BOX read "' + (label || '(no label)') + '"?\n' +
            'Rules:\n' +
            '  - Judge ONLY what is inside the red box. Ignore everything outside it.\n' +
            '  - If the red box is empty, covers the wrong thing, or the text is unreadable,\n' +
            '    answer NO.\n' +
            '  - Do not guess. Partial or similar text is NOT a match.\n' +
            'Output exactly two lines:\n' +
            'Result: [YES / NO]\n' +
            'Reason: one short sentence.') +
        '</pre>') +
    '<div class="mt-3 rounded-xl border border-line bg-soft/40 p-3">' +
    '<div class="text-[11px] font-semibold uppercase tracking-wide text-muted">Output format</div>' +
    '<pre class="mono mt-1 text-xs text-ink">' +
    esc(c.output_format || 'Result: [YES / NO]\nReason: one short sentence') +
    '</pre></div></div>';

  // A folder with no classify.json is a capture-only record. Say so plainly
  // instead of rendering empty sections that look like a broken page.
  const captureOnlyBanner = hasClassify
    ? ''
    : '<div class="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">' +
      '<b>Capture-only record.</b> This folder has no <span class="mono">classify.json</span>, ' +
      'so there is no verdict, no edge data and no VL answer. ' +
      'It holds the raw screenshot' +
      (files.includes('qc_binding.json') ? ' and a QC binding record' : '') +
      '. Nothing is missing — this record was never classified.</div>';

  const imgCard = (name, title, note, present) => {
    if (!present) {
      return (
        '<div class="rounded-2xl border border-dashed border-line bg-panel p-4">' +
        '<div class="text-xs font-semibold uppercase tracking-wide text-muted">' + esc(title) + '</div>' +
        '<div class="mt-3 rounded-xl bg-soft p-6 text-center text-xs text-muted">' +
        esc(name) + ' not stored</div></div>'
      );
    }
    const u = fileUrl(id, name);
    return (
      '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">' + esc(title) + '</div>' +
      '<a href="' + u + '" target="_blank" rel="noopener">' +
      '<img src="' + u + '" alt="' + esc(name) +
      '" class="w-full rounded-xl border border-line bg-black/5" /></a>' +
      '<p class="mt-2 text-xs text-muted">' + esc(note) + '</p></div>'
    );
  };

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-start justify-between gap-3">' +
    '<div class="min-w-0"><h2 class="mono truncate text-base font-semibold text-ink">' + esc(id) + '</h2>' +
    '<div class="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-muted">' +
    evBadge(c.verdict) +
    '<span>' + esc(label || '(no label)') + '</span>' +
    '<span class="text-xs">· task: <span class="mono">' + esc(taskId || '—') + '</span></span>' +
    '<span class="text-xs">· edges_all_pass: <b>' + (c.edges_all_pass ? 'yes' : 'no') + '</b></span>' +
    '<span class="text-xs">· model: <span class="mono">' + esc(vl.model || '-') + '</span></span>' +
    '</div></div>' +
    '<div class="flex flex-wrap gap-2">' +
    '<button id="btn-ev-back" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">← All evidence</button>' +
    '<button id="btn-ev-copy" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Copy URL</button>' +
    '<a href="' + fileUrl(id, 'report.md') + '" target="_blank" rel="noopener" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">report.md</a>' +
    '<a href="' + fileUrl(id, 'classify.json') + '" target="_blank" rel="noopener" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">classify.json</a>' +
    '</div></div>' +
    (c.error
      ? '<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">' + esc(c.error) + '</div>'
      : '') +
    captureOnlyBanner +
    rootCauseCard +
    // The raw screenshot is the source of truth: show it FIRST, full width, so
    // "what was actually captured" is never a question.
    imgCard('shot.png', 'Raw screenshot — what was captured',
            'The unmodified capture. Every other image below is derived from this one.',
            hasShot) +
    '<div class="grid gap-4 lg:grid-cols-2">' +
    imgCard('overlay.png', 'Annotated — for a human',
            'Follow each red line across the whole image: it should cut the target row boundary.', hasOverlay) +
    imgCard('overlay_vl.png', 'Text-free — what the VL read',
            'No annotation text, so it cannot be mistaken for the contents of the box.', hasVl) +
    '</div>' +
    promptCard +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">The two questions</div>' +
    '<div class="grid gap-3 sm:grid-cols-2">' +
    qCard('q1_red_box_present', 'Q1 — is there a red box?') +
    qCard('q2_text_in_box', 'Q2 — inside the box reads "' + (label || '(no label)') + '"?') +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Edge verdicts</div>' +
    '<div class="overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">edge</th><th class="px-3 py-2">candidate</th>' +
    '<th class="px-3 py-2">detected</th><th class="px-3 py-2">delta px</th>' +
    '<th class="px-3 py-2">verdict</th></tr></thead>' +
    '<tbody>' + edgeRows + '</tbody></table></div></div>' +
    provCard +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Files in this evidence folder (' +
    files.length + ')</div>' +
    '<div class="flex flex-wrap gap-2">' +
    (files.length
      ? files
          .map(
            (n) =>
              '<a href="' + fileUrl(id, n) + '" target="_blank" rel="noopener" ' +
              'class="mono rounded-lg border border-line bg-soft px-2 py-1 text-xs text-ink hover:bg-white">' +
              esc(n) + '</a>'
          )
          .join('')
      : '<span class="text-xs text-muted">no files</span>') +
    '</div></div></div>'
  );
}