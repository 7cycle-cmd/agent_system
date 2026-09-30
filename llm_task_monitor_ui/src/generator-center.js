// generator-center.js — the 4-STEP WIZARD.
//
// THE HUMAN (2026-09-27), verbatim:
//   "http://127.0.0.1:18765/llm-tasks/generator / UI re-design should be
//    step 1) select generator
//    step 2) skill + study + wording (selector * 3)
//    step 3) get prompt template with confirm buttom
//    step 4) confirm"
//
// WHAT WAS WRONG, MEASURED
// ------------------------
// The page rendered ONE flat row: [generator] [one select per declared selector]
// [Run]. It read `s.options` and `s.free_text` and NOTHING ELSE. So:
//
//   * `depends_on` was IGNORED — choosing a skill never re-fetched, so
//     `study_key` stayed at option_count=0 and the wizard could not be finished.
//   * `expands_to` was IGNORED — `wording` is N DIMENSIONS (4 for
//     `mouse_spot_verify`, 1 for `failure_classification`), not one value, so it
//     rendered as ONE empty select.
//   * there was no step 3 and no step 4.
//
// THE BACKEND WAS ALREADY DONE (a concurrent session): `registry(conn, values)`
// fills the dependent sources, and `/api/generator/preview` (writes NOTHING) and
// `/api/generator/confirm` (the write) exist. **So this file RENDERS what the
// registry already declares; it invents nothing.**
//
// THE ONE RULE THIS FILE FOLLOWS: the number of wording selects is DATA. It is
// `wording_dimensions`'s row count, never a literal 3.

const API = '/api/generator';

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

// ONE SELECTOR, RENDERED ONCE. MEASURED 2026-09-27: the step-2 markup built
// each selector inline, so a selector could only be drawn in the ONE place the
// loop happened to be. The human asked for a ROW PER THING (skill / study /
// wording), which needs the same markup in three different rows -- so it is a
// function, not a loop body.
function renderSelector(s, v) {
  // `depends_on` is READ, not assumed: a selector that is a PARENT of another
  // must re-fetch when it changes. Marked here, used in wire().
  const dep = s.depends_on ? ' data-depends="' + esc(s.depends_on) + '"' : '';
  let h = '<label class="text-sm"><span class="block text-muted">' + esc(s.label) +
    ' <span class="text-xs">(' + esc(s.source) + ')</span></span>';
  if (s.free_text) {
    h += '<input id="gc-v-' + esc(s.name) + '" data-name="' + esc(s.name) + '"' + dep +
      ' value="' + esc(v) + '" placeholder="free text" ' +
      'class="mt-1 rounded-lg border border-line bg-panel px-3 py-2 text-sm" />';
  } else {
    h += '<select id="gc-v-' + esc(s.name) + '" data-name="' + esc(s.name) + '"' + dep +
      ' class="mt-1 rounded-lg border border-line bg-panel px-3 py-2 text-sm">';
    h += '<option value="">-- select (' + s.option_count + ') --</option>';
    for (const o of (s.options || [])) {
      h += '<option value="' + esc(o.value) + '"' +
        (o.value === v ? ' selected' : '') + '>' + esc(o.label) + '</option>';
    }
    h += '</select>';
  }
  // AN EMPTY SELECTOR MUST SAY WHY. MEASURED 2026-09-27: the API emits `why`
  // when a dependent source returns 0 rows, but the UI dropped it, so the human
  // saw a bare `-- select (0) --` and had to ask "why". A `why` the UI drops is
  // the same defect one layer up.
  if (s.why) {
    h += '<span class="mt-1 block text-xs text-muted">' + esc(s.why) + '</span>';
  }
  return h + '</label>';
}

export function mountGeneratorCenter(root, state, ctx) {
  const toast = (ctx && ctx.toast) || (() => {});
  const S = {
    registry: null,
    key: (state && state.generatorKey) || '',
    values: {},          // selector name -> value
    dims: [],            // the wording DIMENSIONS of the chosen skill
    dimValues: {},       // dim_key -> wording_key
    preview: null,       // step 3 output
    result: null,        // step 4 output
    error: null,
    busy: false,
    step: 1,
  };

  function currentGen() {
    if (!S.registry) return null;
    return S.registry.generators.find((g) => g.key === S.key) || null;
  }

  // ---- the dependent re-fetch (B1) -------------------------------------
  // MEASURED: without this, `study_key` and `wording` stay EMPTY, because their
  // options come from the chosen `skill_key`. The server fills them; the UI must
  // ASK. A POST, because the parent set grows.
  async function refetch() {
    const out = await getJson(API + '/registry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: S.key, values: S.values }),
    });
    if (out.ok === false) { S.error = out.code || 'registry failed'; return; }
    const g = out.generator || null;
    if (!g) return;
    const i = S.registry.generators.findIndex((x) => x.key === g.key);
    if (i >= 0) S.registry.generators[i] = g;
    // the wording DIMENSIONS are the options of the `expands_to` selector
    const wsel = (g.selectors || []).find((s) => s.expands_to);
    S.dims = wsel ? (wsel.options || []).map((o) => o.value) : [];
    for (const k of Object.keys(S.dimValues)) {
      if (!S.dims.includes(k)) delete S.dimValues[k];
    }
  }

  function stepBar() {
    const steps = ['1 · Generator', '2 · Skill + Study + Wording',
                   '3 · Preview', '4 · Confirm'];
    let h = '<nav class="mt-4 flex flex-wrap items-center gap-2 text-sm">';
    steps.forEach((label, i) => {
      const n = i + 1;
      const on = S.step === n;
      h += '<button type="button" data-step="' + n + '" class="rounded-lg px-3 py-1 ' +
        (on ? 'bg-blue-600 text-white' : 'text-blue-700 hover:bg-soft') + '">' +
        esc(label) + '</button>';
      if (n < steps.length) h += '<span class="text-muted">›</span>';
    });
    h += '</nav>';
    return h;
  }

  function render() {
    const gens = (S.registry && S.registry.generators) || [];
    const g = currentGen();
    let html = '';
    html += '<div class="rounded-2xl border border-line bg-panel p-6 shadow-panel">';
    html += '<h2 class="text-lg font-semibold">Generator Center</h2>';
    html += '<p class="mt-1 text-sm text-muted">Four steps: pick a generator, ' +
      'pick skill + study + wording, preview the prompt, then confirm.</p>';
    html += stepBar();

    // ---- STEP 1 -------------------------------------------------------
    if (S.step === 1) {
      html += '<div class="mt-4">';
      html += '<label class="text-sm"><span class="block text-muted">Generator</span>';
      html += '<select id="gc-gen" class="mt-1 rounded-lg border border-line bg-panel px-3 py-2 text-sm">';
      html += '<option value="">-- select --</option>';
      for (const x of gens) {
        html += '<option value="' + esc(x.key) + '"' +
          (x.key === S.key ? ' selected' : '') + '>' + esc(x.label) + '</option>';
      }
      html += '</select></label>';
      if (g) html += '<p class="mt-2 text-xs text-muted">cite: ' + esc(g.cite) + '</p>';
      html += '<div class="mt-4"><button id="gc-next1" class="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white"' +
        (S.key ? '' : ' disabled') + '>Next</button></div>';
      html += '</div>';
    }

    // ---- STEP 2 -------------------------------------------------------
    // THREE ROWS, ONE PER THING (the human, 2026-09-27):
    //   row 1 = skill
    //   row 2 = study   (shown only when skill is not empty)
    //   row 3 = wording (shown only when skill is not empty)
    //
    // WHY ROWS AND NOT ONE FLEX ROW: MEASURED, the three selectors were laid
    // out in ONE wrapping flex row, so the wording dimensions (4 for
    // `logic_layer_judge`) pushed the layout around and the reader could not
    // tell which control belonged to which step. A row per thing makes the
    // DEPENDENCY visible: study and wording are BELOW skill because they
    // DEPEND on it.
    if (S.step === 2 && g) {
      const hasSkill = !!S.values.skill_key;
      const byName = {};
      for (const s of g.selectors) byName[s.name] = s;

      // ---- ROW 1: SKILL ------------------------------------------------
      const skillSel = byName.skill_key;
      html += '<div class="mt-4">';
      if (skillSel) {
        html += renderSelector(skillSel, S.values[skillSel.name] || '');
      }
      html += '</div>';

      // ---- ROW 2: STUDY (only when skill is chosen) --------------------
      const studySel = byName.study_key;
      if (studySel && hasSkill) {
        html += '<div class="mt-3">';
        html += renderSelector(studySel, S.values[studySel.name] || '');
        html += '</div>';
      }

      // ---- ROW 3: WORDING (only when skill is chosen) ------------------
      if (hasSkill) {
        html += '<div class="mt-3">';
        if (S.dims.length) {
          html += '<p class="text-sm text-muted">Wording — ' + S.dims.length +
            ' dimension(s) for this skill</p>';
          html += '<div class="mt-2 flex flex-wrap items-end gap-3">';
          const wsel = (g.selectors || []).find((s) => s.expands_to);
          for (const d of S.dims) {
            const dv = S.dimValues[d] || '';
            html += '<label class="text-sm"><span class="block text-muted">' + esc(d) + '</span>';
            html += '<select data-dim="' + esc(d) + '" class="mt-1 rounded-lg border border-line bg-panel px-3 py-2 text-sm">';
            html += '<option value="">-- any --</option>';
            const vals = ((wsel && wsel.dimension_values) || {})[d] || [];
            for (const o of vals) {
              html += '<option value="' + esc(o.value) + '"' +
                (o.value === dv ? ' selected' : '') + '>' + esc(o.label) + '</option>';
            }
            html += '</select></label>';
          }
          html += '</div>';
        } else {
          // AN EMPTY ROW MUST SAY WHY, exactly like an empty select.
          const wsel = (g.selectors || []).find((s) => s.expands_to);
          const why = (wsel && wsel.why) || 'this skill declares no wording dimensions';
          html += '<p class="text-sm text-muted">Wording — 0 dimension(s) for this skill</p>';
          html += '<span class="mt-1 block text-xs text-muted">' + esc(why) + '</span>';
        }
        html += '</div>';
      }

      html += '<div class="mt-4 flex gap-2">';
      html += '<button id="gc-back2" class="rounded-lg border border-line px-4 py-2 text-sm">Back</button>';
      html += '<button id="gc-preview" class="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white"' +
        (S.busy ? ' disabled' : '') + '>' + (S.busy ? 'Composing...' : 'Preview') + '</button>';
      html += '</div>';
    }

    // ---- STEP 3 -------------------------------------------------------
    if (S.step === 3) {
      const p = S.preview || {};
      html += '<div class="mt-4">';
      if (p.ok === false) {
        html += '<div class="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">' +
          esc(p.code || 'preview failed') + (p.message ? ': ' + esc(p.message) : '') + '</div>';
      } else {
        html += '<p class="text-sm text-muted">sha256 <span class="mono">' +
          esc(String(p.sha256 || '').slice(0, 32)) + '</span> · parts ' + esc(p.parts) +
          ' · writes ' + esc(p.writes) + '</p>';
        html += '<pre class="mt-3 max-h-[28rem] overflow-auto rounded-lg bg-slate-50 p-3 text-xs">' +
          esc(p.prompt_text || '') + '</pre>';
      }
      html += '<div class="mt-4 flex gap-2">';
      html += '<button id="gc-back3" class="rounded-lg border border-line px-4 py-2 text-sm">Back</button>';
      html += '<button id="gc-confirm" class="rounded-lg bg-emerald-600 px-4 py-2 text-sm text-white"' +
        (p.ok === false || S.busy ? ' disabled' : '') + '>' +
        (S.busy ? 'Writing...' : 'Confirm') + '</button>';
      html += '</div></div>';
    }

    // ---- STEP 4 -------------------------------------------------------
    if (S.step === 4) {
      const r = S.result || {};
      html += '<div class="mt-4">';
      html += '<p class="text-sm ' + (r.ok === false ? 'text-rose-700' : 'text-emerald-700') + '">' +
        'ok=' + esc(String(r.ok)) + (r.code ? ' code=' + esc(r.code) : '') + '</p>';
      if (r.prompt_key) {
        html += '<p class="mt-2 text-sm">written <span class="mono">' + esc(r.prompt_key) +
          '</span> version <span class="mono">' + esc(r.version_label || '') + '</span></p>';
      }
      html += '<pre class="mt-3 max-h-[28rem] overflow-auto rounded-lg bg-slate-50 p-3 text-xs">' +
        esc(JSON.stringify(r, null, 2)) + '</pre>';
      html += '<div class="mt-4"><button id="gc-restart" class="rounded-lg border border-line px-4 py-2 text-sm">Start again</button></div>';
      html += '</div>';
    }

    if (S.error) {
      html += '<div class="mt-3 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">' +
        esc(S.error) + '</div>';
    }
    html += '</div>';

    root.innerHTML = html;
    wire();
  }

  function wire() {
    root.querySelectorAll('[data-step]').forEach((el) => {
      el.addEventListener('click', () => {
        const n = Number(el.getAttribute('data-step'));
        if (n >= 3 && !S.preview) return;
        if (n === 4 && !S.result) return;
        S.step = n; render();
      });
    });

    const genSel = root.querySelector('#gc-gen');
    if (genSel) {
      genSel.addEventListener('change', async () => {
        S.key = genSel.value;
        S.values = {}; S.dimValues = {}; S.dims = [];
        S.preview = null; S.result = null; S.error = null;
        if (state) state.generatorKey = S.key;
        await refetch();
        render();
      });
    }

    const next1 = root.querySelector('#gc-next1');
    if (next1) next1.addEventListener('click', async () => {
      await refetch(); S.step = 2; render();
    });

    // a PARENT change re-fetches the dependent options (B1)
    root.querySelectorAll('[data-name]').forEach((el) => {
      el.addEventListener('change', async () => {
        const name = el.getAttribute('data-name');
        S.values[name] = el.value;
        S.preview = null; S.result = null;
        await refetch();
        render();
      });
    });

    root.querySelectorAll('[data-dim]').forEach((el) => {
      el.addEventListener('change', () => {
        S.dimValues[el.getAttribute('data-dim')] = el.value;
        S.preview = null; S.result = null;
      });
    });

    const back2 = root.querySelector('#gc-back2');
    if (back2) back2.addEventListener('click', () => { S.step = 1; render(); });
    const back3 = root.querySelector('#gc-back3');
    if (back3) back3.addEventListener('click', () => { S.step = 2; render(); });

    const pv = root.querySelector('#gc-preview');
    if (pv) pv.addEventListener('click', async () => {
      S.busy = true; S.error = null; render();
      const values = Object.assign({}, S.values, { dim_values: S.dimValues });
      const out = await getJson(API + '/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: S.key, values }),
      });
      S.busy = false; S.preview = out;
      if (out.ok === false) S.error = out.code + (out.message ? ': ' + out.message : '');
      S.step = 3; render();
    });

    const cf = root.querySelector('#gc-confirm');
    if (cf) cf.addEventListener('click', async () => {
      S.busy = true; S.error = null; render();
      const values = Object.assign({}, S.values, { dim_values: S.dimValues });
      const out = await getJson(API + '/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: S.key, values, version_label: 'dim_v1' }),
      });
      S.busy = false; S.result = out;
      if (out.ok === false) S.error = out.code + (out.message ? ': ' + out.message : '');
      S.step = 4; render();
      if (toast) toast(out.ok === false ? ('Confirm refused: ' + out.code) : 'Prompt written');
    });

    const rs = root.querySelector('#gc-restart');
    if (rs) rs.addEventListener('click', () => {
      S.preview = null; S.result = null; S.error = null; S.step = 1; render();
    });
  }

  (async () => {
    const rep = await getJson(API + '/registry');
    if (rep.ok === false) {
      S.error = rep.code || 'registry failed';
    } else {
      S.registry = rep;
      // THE REGISTRY IS PUBLISHED TO THE SHELL, so `tabsForNav('generator')` can
      // derive one tab per generator. MEASURED 2026-09-28: it returned `[]`, so
      // `/llm-tasks/generator/logic` matched no tab and the page rendered the
      // DEFAULT -- a URL that silently shows a different page.
      if (state) state.generatorRegistry = rep;
      // THE URL SEGMENT PICKS THE GENERATOR -- the key with the shared
      // `_generator` suffix removed (`logic` -> `logic_generator`). MEASURED
      // 2026-09-28: the key's LAST word is `generator` for ALL FIVE keys, so it
      // has no discriminating power and cannot be the segment.
      const seg = (state && state.generatorTab) || '';
      if (seg && seg !== 'run') {
        const keys = rep.generators.map((g) => String(g.key || ''));
        const stripped = keys.filter((k) => k.endsWith('_generator'))
          .map((k) => k.slice(0, -'_generator'.length));
        const allShare = stripped.length === keys.length && keys.length > 1;
        const hit = rep.generators.find((g) => {
          const k = String(g.key || '');
          return allShare && k.endsWith('_generator')
            ? k.slice(0, -'_generator'.length) === seg
            : k === seg;
        });
        if (hit) S.key = hit.key;
      }
      if (!S.key && rep.generators.length) S.key = rep.generators[0].key;
      await refetch();
    }
    render();
  })();

  render();
}

export const TABS = [{ id: 'run', label: 'Run' }];
