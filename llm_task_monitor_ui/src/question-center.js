// question-center.js — the QUESTION CENTER. A question is a STEP, and steps
// MOVE: one direction NARROWS, the other RESEARCHES.
//
// THE HUMAN (2026-09-27), verbatim:
//   "question flow can narrow area -> to the point, that is the way to have work
//    for LLM ( not 7B only)"
//   "narrow area -> to the point = proof"
//   "point -> collect evidence -> expend area = research"
//   "Flow = Question by 1 -> 2 or 1 -> 2 -> 3 or 1 -> 2 -> 3 -> 4, when flow = 1,
//    flow = 0, without flow"
//   "+ UI for http://127.0.0.1:18765/llm-tasks/question"
//   "point = can measured unit, all can be tracable"
//
// WHAT THIS PAGE IS, IN ONE SENTENCE
// -----------------------------------
// It shows, per REGISTRY, the questions that registry's own shape demands, and
// the result of asking them: an AREA of applicable questions, narrowed to the
// POINT that did not conform — every point item carrying a MEASURED unit and a
// TRACEABLE evidence ref.
//
// THE THREE THINGS THIS PAGE MUST NEVER DO (each one was a MEASURED defect in the
// modules behind it, so the rule is stated where the rendering happens):
//
//   1. Never show an AREA that includes a question that CANNOT apply. A question
//      answered from a FILE is INAPPLICABLE for a TABLE; counted in, it would
//      inflate the area and make every narrowing look more successful than it
//      was. The API already excludes them; this file DISPLAYS the exclusion.
//   2. Never show a point item WITHOUT its evidence ref. `<no path>` is not a
//      ref. An item a worker cannot open can only be skipped, and a skipped item
//      inside a "point" silently shrinks the work.
//   3. Never render a raw DB column name as a label. `coords`, `status`,
//      `session_id` are the human's own examples of what a BAD header looks like
//      (the `ui-standard` rule), so every header here is a plain phrase.
//
// IT REUSES WHAT ALREADY EXISTS: the questions come from
// `question_generator.questions_for_registry` and the direction from
// `research_direction`. This file RENDERS; it produces nothing.

const API = '/api/question';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

async function getJson(url, opts) {
  const r = await fetch(url, opts);
  const t = await r.text();
  try { return JSON.parse(t); } catch (e) { return { ok: false, code: 'BAD_JSON', raw: t.slice(0, 400) }; }
}

// A COUNT IS NEVER A BARE NUMBER. Every number the page shows carries its UNIT
// and its POPULATION, because "narrowed 41 -> 5" is unreadable without knowing
// that 41 counts QUESTIONS and 5 counts QUESTIONS THAT FAILED.
function num(value, unit, population) {
  const v = (value === null || value === undefined) ? '—' : value;
  return '<span class="font-semibold">' + esc(v) + '</span>' +
    '<span class="ml-1 text-xs text-muted">' + esc(unit) + '</span>' +
    (population ? '<span class="block text-xs text-muted">' + esc(population) + '</span>' : '');
}

// A TRACEABILITY CHIP. `true` is green, `false` is red, and anything else is
// amber — an UNKNOWN must not read as a pass.
function traceChip(t) {
  if (t === true) return '<span class="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">traceable</span>';
  if (t === false) return '<span class="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-800">not traceable</span>';
  return '<span class="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">unknown</span>';
}

// THE DIRECTION CHIPS. Two directions, and they are OPPOSITE, so they are shown
// with opposite arrows rather than two words that look alike.
function dirChip(direction) {
  if (direction === 'narrow') {
    return '<span class="rounded bg-sky-100 px-2 py-0.5 text-xs text-sky-800">\u2192 narrow</span>';
  }
  if (direction === 'research') {
    return '<span class="rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-800">\u2190 research</span>';
  }
  return '<span class="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">' + esc(direction || '—') + '</span>';
}

export function mountQuestionCenter(root, state, ctx) {
  const toast = (ctx && ctx.toast) || (() => {});
  const S = {
    tab: (state && state.questionTab) || 'registries',
    overview: null,     // /api/question/registry
    table: null,        // the chosen registry
    detail: null,       // /api/question/registry/<table>
    narrow: null,       // /api/question/narrow result
    frontiers: null,    // /api/question/frontiers
    samples: null,      // /api/question/samples
    error: null,
    busy: false,
  };

  function tabsHtml() {
    const t = [
      { id: 'registries', label: 'Registries' },
      { id: 'points', label: 'Points' },
      { id: 'samples', label: 'Samples' },
      { id: 'frontiers', label: 'Research Frontiers' },
    ];
    return '<div class="mb-4 flex gap-2">' + t.map((x) =>
      '<button data-qtab="' + esc(x.id) + '" class="rounded-lg border px-3 py-1.5 text-sm ' +
      (S.tab === x.id ? 'border-sky-300 bg-sky-50 text-sky-800' : 'border-line bg-panel text-muted') +
      '">' + esc(x.label) + '</button>').join('') + '</div>';
  }

  // ---- TAB 1: the registries and their question counts --------------------
  function renderRegistries() {
    const o = S.overview;
    if (!o) return '<p class="text-sm text-muted">Loading…</p>';
    let h = '<div class="mb-4 grid grid-cols-3 gap-3">';
    h += '<div class="rounded-xl border border-line bg-panel p-3 text-sm">' +
      num(o.registry_count, 'registries', 'declared in db_table_registry') + '</div>';
    h += '<div class="rounded-xl border border-line bg-panel p-3 text-sm">' +
      num(o.question_total, 'questions', 'total demanded by the specable registries') + '</div>';
    h += '<div class="rounded-xl border border-line bg-panel p-3 text-sm">' +
      num(o.refused, 'registries', 'that could NOT be specced — named below, never omitted') + '</div>';
    h += '</div>';

    // THE REFUSALS ARE SHOWN FIRST AND IN FULL. A registry the system cannot
    // ask about is the single most important row on this page.
    const refused = (o.results || []).filter((r) => !r.ok);
    if (refused.length) {
      h += '<h3 class="mb-2 text-sm font-semibold text-rose-800">Could not be asked about (' + refused.length + ')</h3>';
      h += '<div class="mb-5 overflow-hidden rounded-xl border border-rose-200"><table class="w-full text-sm">';
      h += '<thead class="bg-rose-50 text-left text-xs text-rose-800"><tr>' +
        '<th class="px-3 py-2">Registry</th><th class="px-3 py-2">Why it could not be questioned</th></tr></thead><tbody>';
      for (const r of refused) {
        h += '<tr class="border-t border-rose-100">' +
          '<td class="px-3 py-2 font-mono text-xs">' + esc(r.table) + '</td>' +
          '<td class="px-3 py-2 text-xs">' + esc(r.code) + ' — ' + esc(r.reason) + '</td></tr>';
      }
      h += '</tbody></table></div>';
    }

    const ok = (o.results || []).filter((r) => r.ok);
    h += '<h3 class="mb-2 text-sm font-semibold">Registries that CAN be asked about (' + ok.length + ')</h3>';
    h += '<div class="overflow-hidden rounded-xl border border-line"><table class="w-full text-sm">';
    h += '<thead class="bg-slate-50 text-left text-xs text-muted"><tr>' +
      '<th class="px-3 py-2">Registry</th>' +
      '<th class="px-3 py-2">Questions</th>' +
      '<th class="px-3 py-2">Area (askable)</th>' +
      '<th class="px-3 py-2">Layers</th>' +
      '<th class="px-3 py-2">Evidence</th>' +
      '<th class="px-3 py-2"></th></tr></thead><tbody>';
    for (const r of ok) {
      // A registry whose questions are not fully traceable is MARKED HERE, not
      // only in the detail view: the point of the overview is to make the problem
      // findable without opening every row.
      h += '<tr class="border-t border-line hover:bg-slate-50">' +
        '<td class="px-3 py-2 font-mono text-xs">' + esc(r.table) + '</td>' +
        '<td class="px-3 py-2">' + num(r.count, 'questions', '') + '</td>' +
        '<td class="px-3 py-2">' + num(r.area, 'questions', 'excluding ' + (r.inapplicable || []).length + ' that cannot apply') + '</td>' +
        '<td class="px-3 py-2 text-xs">' + esc((r.layers || []).join(', ') || '—') + '</td>' +
        '<td class="px-3 py-2">' + traceChip(r.fully_traceable) + '</td>' +
        '<td class="px-3 py-2"><button data-qtable="' + esc(r.table) + '" class="rounded border border-line px-2 py-1 text-xs">Open \u2192</button></td>' +
        '</tr>';
    }
    h += '</tbody></table></div>';
    return h;
  }

  // ---- TAB 2: one registry, and the NARROW result -------------------------
  function renderPoints() {
    if (!S.table) return '<p class="text-sm text-muted">Choose a registry on the Registries tab.</p>';
    let h = '<div class="mb-3 flex items-center gap-3">' +
      '<button data-qback="1" class="rounded border border-line px-2 py-1 text-xs">\u2190 Registries</button>' +
      '<span class="font-mono text-sm">' + esc(S.table) + '</span></div>';

    if (S.error) {
      h += '<div class="mb-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">' +
        esc(S.error) + '</div>';
    }
    const n = S.narrow;
    if (n) {
      // THE TWO NUMBERS THAT ARE THE WHOLE PAGE, each with its unit and its
      // population on the same line.
      h += '<div class="mb-4 grid grid-cols-3 gap-3">';
      h += '<div class="rounded-xl border border-line bg-panel p-3 text-sm">AREA<br>' +
        num(n.area_size, n.unit, 'every applicable question this registry demands') + '</div>';
      h += '<div class="rounded-xl border border-line bg-panel p-3 text-sm">POINT<br>' +
        num(n.point_size, n.unit, 'questions that did NOT conform — what is left to fix') + '</div>';
      h += '<div class="rounded-xl border border-line bg-panel p-3 text-sm">DIRECTION<br>' +
        dirChip(n.direction) + '<span class="block text-xs text-muted">' + esc(n.meaning) + '</span>' +
        '<span class="mt-1 block">' + traceChip(n.fully_traceable) + '</span></div>';
      h += '</div>';

      // THE POINT — every item with its column, its answer and its EVIDENCE REF.
      // An empty point is a SUCCESS and is stated as one, not left blank.
      if (n.point_size === 0) {
        h += '<div class="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">' +
          'Nothing is left to fix: all ' + esc(n.area_size) + ' applicable question(s) conformed.</div>';
      } else {
        h += '<h3 class="mb-2 text-sm font-semibold">The point \u2014 ' + esc(n.point_size) + ' question(s) did not conform</h3>';
        h += '<div class="mb-4 overflow-hidden rounded-xl border border-line"><table class="w-full text-sm">';
        h += '<thead class="bg-slate-50 text-left text-xs text-muted"><tr>' +
          '<th class="px-3 py-2">Question</th><th class="px-3 py-2">Column it tests</th>' +
          '<th class="px-3 py-2">Layer</th><th class="px-3 py-2">Answer</th>' +
          '<th class="px-3 py-2">Evidence to re-run</th></tr></thead><tbody>';
        for (const p of n.point) {
          h += '<tr class="border-t border-line">' +
            '<td class="px-3 py-2 font-mono text-xs">' + esc(p.question_id) + '</td>' +
            '<td class="px-3 py-2 font-mono text-xs">' + esc(p.column || '\u2014') + '</td>' +
            '<td class="px-3 py-2 text-xs">' + esc(p.layer) + '</td>' +
            '<td class="px-3 py-2 text-xs">' + esc(p.got) + '</td>' +
            '<td class="px-3 py-2 font-mono text-xs">' + esc(p.evidence_ref || '\u2014') +
            ' ' + traceChip(p.traceable) + '</td></tr>';
        }
        h += '</tbody></table></div>';
      }

      // THE QUESTIONS THAT WERE SKIPPED, NAMED. Excluded from the area, and the
      // reason is shown so the exclusion cannot be mistaken for an absence.
      const skipped = n.skipped || [];
      if (skipped.length) {
        h += '<h3 class="mb-2 text-sm font-semibold">Not asked of this subject (' + skipped.length + ')</h3>';
        h += '<div class="mb-4 overflow-hidden rounded-xl border border-amber-200"><table class="w-full text-sm">';
        h += '<thead class="bg-amber-50 text-left text-xs text-amber-800"><tr>' +
          '<th class="px-3 py-2">Question</th><th class="px-3 py-2">Why it does not apply</th></tr></thead><tbody>';
        for (const s of skipped) {
          h += '<tr class="border-t border-amber-100">' +
            '<td class="px-3 py-2 font-mono text-xs">' + esc(s.question_id) + '</td>' +
            '<td class="px-3 py-2 text-xs">' + esc(s.skip_reason) + '</td></tr>';
        }
        h += '</tbody></table></div>';
      }
    }

    // THE QUESTION LIST — the full area, so a reader can see what WAS asked.
    const d = S.detail;
    if (d && d.questions) {
      h += '<h3 class="mb-2 text-sm font-semibold">All questions for this registry (' + esc(d.count) + ')</h3>';
      h += '<div class="overflow-hidden rounded-xl border border-line"><table class="w-full text-sm">';
      h += '<thead class="bg-slate-50 text-left text-xs text-muted"><tr>' +
        '<th class="px-3 py-2">Question id</th><th class="px-3 py-2">Column</th>' +
        '<th class="px-3 py-2">Layer</th><th class="px-3 py-2">Applicable</th>' +
        '<th class="px-3 py-2">Evidence</th></tr></thead><tbody>';
      for (const q of d.questions) {
        h += '<tr class="border-t border-line">' +
          '<td class="px-3 py-2 font-mono text-xs">' + esc(q.question_id) + '</td>' +
          '<td class="px-3 py-2 font-mono text-xs">' + esc(q.column || '\u2014') + '</td>' +
          '<td class="px-3 py-2 text-xs">' + esc(q.layer) + '</td>' +
          '<td class="px-3 py-2 text-xs">' + (q.applicable
            ? '<span class="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">yes</span>'
            : '<span class="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">no \u2014 needs a file</span>') + '</td>' +
          '<td class="px-3 py-2 font-mono text-xs">' + esc(q.evidence_ref || '\u2014') + '</td></tr>';
      }
      h += '</tbody></table></div>';
    }
    return h;
  }

  // ---- TAB 3: the research frontiers --------------------------------------
  function renderFrontiers() {
    const f = S.frontiers;
    if (!f) return '<p class="text-sm text-muted">Loading…</p>';
    if (!f.frontiers || !f.frontiers.length) {
      return '<div class="rounded-xl border border-line bg-panel p-4 text-sm text-muted">' +
        'No research frontier has been recorded yet. A frontier is what a RESEARCH ' +
        'leaves behind: the point it started at, the evidence it collected, the area ' +
        'that opened, and the NARROW question that would close it.</div>';
    }
    let h = '<div class="overflow-hidden rounded-xl border border-line"><table class="w-full text-sm">';
    h += '<thead class="bg-slate-50 text-left text-xs text-muted"><tr>' +
      '<th class="px-3 py-2">Point</th><th class="px-3 py-2">Evidence</th>' +
      '<th class="px-3 py-2">Expanded area</th><th class="px-3 py-2">Closed by</th>' +
      '<th class="px-3 py-2">Direction</th></tr></thead><tbody>';
    for (const r of f.frontiers) {
      h += '<tr class="border-t border-line">' +
        '<td class="px-3 py-2 font-mono text-xs">' + esc(r.point_key) + '</td>' +
        '<td class="px-3 py-2 font-mono text-xs">' + esc(r.evidence_ref) + '</td>' +
        '<td class="px-3 py-2 text-xs">' + esc(r.expanded_area) + '</td>' +
        '<td class="px-3 py-2 font-mono text-xs">' + esc(r.closing_flow_key) + '</td>' +
        '<td class="px-3 py-2">' + dirChip(r.direction) + '</td></tr>';
    }
    h += '</tbody></table></div>';
    return h;
  }

  // ---- TAB 4: the SAMPLES a worker can COPY -------------------------------
  //
  // THE HUMAN (2026-09-28): "is time to upgrade question flow!! summary
  // experience, how can we have template or sample for that to help worker".
  //
  // MEASURED: the samples existed in `pattern_template` but NO ROUTE SERVED
  // THEM, so a worker could not see them. A template nobody can read is not a
  // template.
  //
  // THE THREE THINGS THIS TAB MUST NEVER DO:
  //   1. Never render a sample WITHOUT its `why`. The `why` carries the
  //      MEASUREMENT (balanced accuracy, per-class recall, repeated runs) — a
  //      sample without it is an opinion, and the whole point is that the
  //      experience is measured.
  //   2. Never render a sample WITHOUT its `example`. The example is the
  //      WORKING question a worker copies; a description of one cannot be
  //      copied.
  //   3. Never hide an INCOMPLETE sample. It is reported by name, because a
  //      silently skipped sample shrinks what a worker can learn from.
  function renderSamples() {
    const s = S.samples;
    if (!s) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.ok === false) {
      return '<div class="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">' +
        esc(s.code || 'samples failed') + '</div>';
    }
    let h = '<p class="mb-3 text-sm text-muted">' +
      num(s.count, 'samples', 'a worker can copy when writing a question') +
      '</p>';
    if (!s.samples || !s.samples.length) {
      return h + '<div class="rounded-xl border border-line bg-panel p-4 text-sm text-muted">' +
        'No sample has been recorded for this subject yet. A sample is a rule a ' +
        'worker can COPY, with the measurement that justifies it.</div>';
    }
    for (const r of s.samples) {
      const tier = r.tier === 'must'
        ? '<span class="rounded bg-rose-100 px-2 py-0.5 text-xs text-rose-800">must</span>'
        : '<span class="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">recommended</span>';
      h += '<div class="mb-3 rounded-xl border border-line bg-panel p-4">';
      h += '<div class="mb-2 flex items-center gap-2">' + tier +
        '<span class="font-mono text-sm font-semibold">' + esc(r.item_kind) + '</span></div>';
      h += '<div class="mb-2 text-sm"><span class="text-muted">rule</span> ' +
        esc(r.rule) + '</div>';
      h += '<div class="mb-2 text-sm"><span class="text-muted">why</span> ' +
        esc(r.why) + '</div>';
      h += '<pre class="mb-2 overflow-x-auto rounded-lg bg-slate-50 p-3 text-xs">' +
        esc(r.example) + '</pre>';
      h += '<div class="text-xs text-muted">cite ' + esc(r.cite_ref) + '</div>';
      h += '</div>';
    }
    if (s.incomplete_count) {
      h += '<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">' +
        num(s.incomplete_count, 'incomplete samples',
            'missing rule/why/example/cite — named, never hidden') + ': ' +
        s.incomplete.map((r) => esc(r.item_kind)).join(', ') + '</div>';
    }
    return h;
  }

  function render() {
    let body;
    if (S.tab === 'registries') body = renderRegistries();
    else if (S.tab === 'points') body = renderPoints();
    else if (S.tab === 'samples') body = renderSamples();
    else body = renderFrontiers();
    root.innerHTML =
      '<div class="p-4">' +
      '<h2 class="mb-1 text-lg font-semibold">Question Center</h2>' +
      '<p class="mb-4 text-sm text-muted">A question is a step, and a step MOVES: ' +
      '<b>narrow</b> takes an area to the point (proof); <b>research</b> takes a ' +
      'point and expands it into an area. Every point item carries a measured unit ' +
      'and a traceable evidence reference.</p>' +
      tabsHtml() + body + '</div>';
    wire();
  }

  function wire() {
    root.querySelectorAll('[data-qtab]').forEach((el) => {
      el.addEventListener('click', async () => {
        S.tab = el.getAttribute('data-qtab');
        if (state) state.questionTab = S.tab;
        if (S.tab === 'frontiers' && !S.frontiers) await loadFrontiers();
        if (S.tab === 'samples' && !S.samples) await loadSamples();
        render();
      });
    });
    root.querySelectorAll('[data-qtable]').forEach((el) => {
      el.addEventListener('click', async () => {
        S.table = el.getAttribute('data-qtable');
        S.tab = 'points';
        if (state) state.questionTab = 'points';
        await openTable(S.table);
        render();
      });
    });
    const back = root.querySelector('[data-qback]');
    if (back) back.addEventListener('click', () => {
      S.tab = 'registries'; if (state) state.questionTab = S.tab; render();
    });
  }

  async function openTable(table) {
    S.busy = true; S.error = null;
    S.detail = await getJson(API + '/registry/' + encodeURIComponent(table));
    if (S.detail.ok === false) S.error = S.detail.reason || S.detail.code || 'could not load';
    S.narrow = await getJson(API + '/narrow', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ table }),
    });
    if (S.narrow.ok === false && S.narrow.reason) S.error = S.narrow.reason;
    S.busy = false;
  }

  async function loadFrontiers() {
    S.frontiers = await getJson(API + '/frontiers');
    if (S.frontiers.ok === false) S.error = S.frontiers.code || 'frontiers failed';
  }

  async function loadSamples() {
    S.samples = await getJson(API + '/samples');
    if (S.samples.ok === false) S.error = S.samples.code || 'samples failed';
  }

  (async () => {
    S.overview = await getJson(API + '/registry');
    if (S.overview.ok === false) S.error = S.overview.code || 'registry failed';
    render();
  })();

  render();
}

export const TABS = [
  { id: 'registries', label: 'Registries' },
  { id: 'points', label: 'Points' },
  { id: 'samples', label: 'Samples' },
  { id: 'frontiers', label: 'Research Frontiers' },
];
