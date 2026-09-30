// consultant-center.js — the CONSULTANT CENTER.
//
// THE HUMAN (2026-09-28), verbatim:
//   "＋ ui at http://127.0.0.1:18765/llm-tasks/consultant / your ui team can
//    help you, it should not be a single ui"
//   "this is professional consultant team, + table by DB driven have scoring
//    system too, we can clearly define who is professional in target industry"
//
// THREE TABS, because the human said it is NOT a single UI:
//   * Build Steps — the 17-column standard, one row per build step
//   * Find        — factor -> best 3 -> compare, with why-yes / why-not
//   * Teams       — industry -> team -> skills, with the proofed/rating score
//
// THE ONE RULE THIS FILE FOLLOWS: it RENDERS what the API returns and INVENTS
// NOTHING. Every number on screen is read from a response, and every empty state
// NAMES the reason the API gave (`why`), because a bare "0" is the defect the
// `ui-standard` skill's rule 5 exists to catch.
//
// `UNMEASURED` IS NOT A PASS. A build step whose playwright guide is missing is
// rendered in its own colour with the title "no proof row recorded — this is NOT
// a pass", the same discipline the Factors (law table) tab uses.

const API = '/api/consultant';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

async function getJson(url, opts) {
  const r = await fetch(url, opts);
  const t = await r.text();
  try {
    return JSON.parse(t);
  } catch (e) {
    return { ok: false, code: 'BAD_JSON', raw: t.slice(0, 400) };
  }
}

// A NUMBER MUST SAY WHAT IT COUNTS. `ui-standard` rule 4: a rendered number that
// does not name its population is a number nobody can check. So every count goes
// through this, and the label is REQUIRED.
function num(value, label) {
  return '<span class="cc-num" title="' + esc(label) + '">' +
    esc(value) + '</span> <span class="cc-num-label">' + esc(label) + '</span>';
}

// A VERDICT IS COLOURED BY ITS OWN VALUE, and UNKNOWN is NOT green.
function verdictClass(v) {
  const s = String(v || '').toUpperCase();
  if (s === 'ADOPT') return 'cc-ok';
  if (s === 'REJECT') return 'cc-bad';
  return 'cc-unknown';
}

function emptyState(what, why) {
  return '<div class="cc-empty"><strong>No ' + esc(what) + '.</strong> ' +
    '<span>' + esc(why || 'the API returned no rows and gave no reason') +
    '</span></div>';
}

// CRITICAL: this list and `app.js`'s `CONSULTANT_CENTER_TABS` must AGREE. That
// one is what `parseRoutePath()` validates a URL segment against, so a tab that
// exists only here is unreachable by URL, and a tab that exists only there
// renders nothing. It is EXPORTED so `app.js` imports THIS list rather than
// keeping a second copy that can drift.
export const TABS = [
  { id: 'build-steps', label: 'Build Steps' },
  { id: 'step-walk', label: 'Step Walk' },
  { id: 'find', label: 'Find' },
  { id: 'teams', label: 'Teams' },
];

export function mountConsultantCenter(root, state, ctx) {
  const toast = (ctx && ctx.toast) || (() => {});
  const S = {
    tab: (state && state.consultantTab) || 'build-steps',
    overview: null,
    steps: null,
    walk: null,
    walkIndex: 0,
    finds: null,
    findResult: null,
    factor: '',
    error: null,
    busy: false,
  };

  async function load() {
    S.busy = true;
    S.error = null;
    render();
    try {
      if (S.tab === 'build-steps') {
        S.steps = await getJson(API + '/build-steps');
      } else if (S.tab === 'step-walk') {
        S.walk = await getJson(API + '/step-walk?i=' + S.walkIndex);
      } else if (S.tab === 'find') {
        S.finds = await getJson(API + '/finds');
      } else {
        S.overview = await getJson(API + '/overview');
      }
    } catch (e) {
      S.error = String((e && e.message) || e);
    }
    S.busy = false;
    render();
  }

  // ---- tab 1: the 17-column build-step standard -------------------------
  function buildStepsHtml() {
    const d = S.steps;
    if (!d) return '<div class="cc-loading">Loading build steps…</div>';
    if (d.ok === false) {
      return emptyState('build steps', d.code || d.reason || 'the API refused');
    }
    const cols = d.columns || [];
    const rows = d.steps || [];
    let h = '<div class="cc-head">' +
      '<h2>Build Step Standard</h2>' +
      '<p class="cc-sub">One row per build step. ' +
      num(d.count, 'steps in this view') + ' · ' +
      num(cols.length, 'columns in the view') + '</p></div>';
    if (!rows.length) {
      return h + emptyState('build steps',
        'the register is empty — a step needs a factor, a purpose and a ' +
        'citation, and inventing them would fabricate the standard');
    }
    h += '<div class="cc-scroll"><table class="cc-table"><thead><tr>';
    for (const c of cols) h += '<th>' + esc(c) + '</th>';
    h += '</tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr>';
      for (const c of cols) {
        const v = r[c];
        if (c === 'status') {
          h += '<td><span class="cc-pill ' +
            (v === 'done' ? 'cc-ok' : v === 'blocked' ? 'cc-bad' : 'cc-unknown') +
            '">' + esc(v) + '</span></td>';
        } else if (c === 'playwright' && (v === 'NA' || !v)) {
          // THE GUIDE IS THE PROGRAM. A step with no guide is UNMEASURED, and
          // UNMEASURED is NOT a pass — it is its own colour with its own title.
          h += '<td><span class="cc-pill cc-unknown" title="no proof row ' +
            'recorded — this is NOT a pass">UNMEASURED</span></td>';
        } else {
          h += '<td>' + esc(v == null ? 'NA' : v) + '</td>';
        }
      }
      h += '</tr>';
    }
    h += '</tbody></table></div>';
    const cov = d.coverage || {};
    if (cov.ratio != null) {
      h += '<p class="cc-sub">' + num(cov.ratio, 'coverage ratio') +
        (cov.na ? ' · ' + num(cov.na, 'steps declaring NA') : '') + '</p>';
    }
    return h;
  }

  // ---- tab 2: the step walk (ONE step at a time) -----------------------
  //
  // WHY THIS IS A SECOND VIEW AND NOT A SECOND SOURCE: it reads the SAME
  // register rows the Build Steps table reads, and shows ONE of them. A walk
  // that kept its own copy of the steps would be a second register, and the two
  // would drift — the exact defect the 17-column view exists to prevent.
  //
  // THE EVIDENCE CELL IS RENDERED FOR EVERY STEP, because a step whose evidence
  // is not on screen is a step nobody can check. `UNMEASURED` is NOT a pass.
  function stepWalkHtml() {
    const d = S.walk;
    if (!d) return '<div class="cc-loading">Loading the step walk…</div>';
    if (d.ok === false) {
      return emptyState('steps', d.code || d.reason || 'the API refused');
    }
    const total = Number(d.total || 0);
    let h = '<div class="cc-head"><h2>Step Walk</h2>' +
      '<p class="cc-sub">One step at a time, read from the same register the ' +
      'Build Steps table reads. ' + num(total, 'steps in this walk') +
      '</p></div>';
    if (!total) {
      return h + emptyState('steps',
        'the register is empty — a step needs a factor, a purpose and a ' +
        'citation, and inventing them would fabricate the standard');
    }
    const s = d.step || {};
    const i = Number(d.index || 0);
    h += '<div class="cc-row">' +
      '<button id="cc-walk-prev" class="cc-btn"' +
      (d.has_previous ? '' : ' disabled') + '>Previous</button>' +
      '<span class="cc-num-label">' + num(i + 1, 'step number') + ' / ' +
      num(total, 'steps in this walk') + '</span>' +
      '<button id="cc-walk-next" class="cc-btn"' +
      (d.has_next ? '' : ' disabled') + '>Next</button></div>';
    h += '<div class="cc-card"><div class="cc-card-head">' +
      '<strong>' + esc(s.name) + '</strong> · <code>' + esc(s.layer_key) +
      '</code> · <span class="cc-pill ' +
      (s.status === 'done' ? 'cc-ok' : s.status === 'blocked' ? 'cc-bad'
                           : 'cc-unknown') + '">' + esc(s.status) +
      '</span></div>';
    h += '<table class="cc-table"><tbody>';
    for (const c of (d.columns || [])) {
      const v = s[c];
      if (c === 'playwright' && (v === 'NA' || !v)) {
        h += '<tr><th>' + esc(c) + '</th><td><span class="cc-pill cc-unknown" ' +
          'title="no proof row recorded — this is NOT a pass">UNMEASURED' +
          '</span></td></tr>';
      } else {
        h += '<tr><th>' + esc(c) + '</th><td>' + esc(v == null ? 'NA' : v) +
          '</td></tr>';
      }
    }
    h += '</tbody></table></div>';
    return h;
  }

  // ---- tab 3: the find (factor -> best 3 -> compare) --------------------
  function findHtml() {
    let h = '<div class="cc-head"><h2>GitHub Find</h2>' +
      '<p class="cc-sub">A factor is the KEY. The find returns the best 3 and ' +
      'the verdict says why yes and why not.</p></div>';
    h += '<div class="cc-row">' +
      '<input id="cc-factor" class="cc-input" placeholder="factor_key" ' +
      'value="' + esc(S.factor) + '" />' +
      '<button id="cc-run" class="cc-btn">Run find</button></div>';
    if (S.findResult) {
      const r = S.findResult;
      if (r.ok === false) {
        h += emptyState('find', r.reason || r.code);
      } else {
        h += '<p class="cc-sub">query <code>' + esc(r.query) + '</code> · ' +
          num(r.total_count, 'repos matching the query') + ' · ' +
          num(r.returned, 'returned') + '</p>';
        h += '<div class="cc-scroll"><table class="cc-table"><thead><tr>' +
          '<th>rank</th><th>repo</th><th>stars</th><th>proofed</th>' +
          '<th>flag</th></tr></thead><tbody>';
        for (const c of (r.ranked || [])) {
          h += '<tr><td>' + esc(c.rank) + '</td><td>' + esc(c.repo) +
            '</td><td>' + esc(c.stars) + '</td><td>' +
            (c.proofed ? '<span class="cc-pill cc-ok">proofed</span>'
                       : '<span class="cc-pill cc-unknown">unproven</span>') +
            '</td><td>' + esc(c.flag || '') + '</td></tr>';
        }
        h += '</tbody></table></div>';
      }
    }
    const d = S.finds;
    if (!d) return h + '<div class="cc-loading">Loading recorded finds…</div>';
    if (d.ok === false) return h + emptyState('finds', d.code || d.reason);
    if (!(d.finds || []).length) {
      return h + emptyState('recorded finds',
        'no find has been recorded yet — run the seeder with --find --apply');
    }
    for (const f of d.finds) {
      h += '<div class="cc-card"><div class="cc-card-head">' +
        '<strong>' + esc(f.factor_key) + '</strong> · query <code>' +
        esc(f.query) + '</code> · ' +
        num(f.total_count, 'repos matching the query') + '</div>';
      h += '<table class="cc-table"><thead><tr><th>rank</th><th>repo</th>' +
        '<th>stars</th><th>proofed</th><th>verdict</th><th>why yes</th>' +
        '<th>why not</th></tr></thead><tbody>';
      for (const c of (f.candidates || [])) {
        h += '<tr><td>' + esc(c.rank) + '</td><td>' + esc(c.repo) + '</td>' +
          '<td>' + esc(c.stars) + '</td><td>' + esc(c.proofed) + '</td>' +
          '<td><span class="cc-pill ' + verdictClass(c.verdict) + '">' +
          esc(c.verdict) + '</span></td>' +
          '<td class="cc-why">' + esc(c.why_yes) + '</td>' +
          '<td class="cc-why">' + esc(c.why_not) + '</td></tr>';
      }
      h += '</tbody></table></div>';
    }
    return h;
  }

  // ---- tab 3: the teams (industry -> team -> skills + score) -----------
  function teamsHtml() {
    const d = S.overview;
    if (!d) return '<div class="cc-loading">Loading consultant teams…</div>';
    if (d.ok === false) return emptyState('teams', d.code || d.reason);
    let h = '<div class="cc-head"><h2>Consultant Teams</h2>' +
      '<p class="cc-sub">' + num(d.industry_count, 'industries registered') +
      '. A skill is ranked <strong>proofed first, then rating</strong> — a ' +
      'proven skill outranks a highly-rated unproven one.</p></div>';
    if (!(d.industries || []).length) {
      return h + emptyState('industries',
        'no industry has a source yet — an industry with no citation would be ' +
        'an invention');
    }
    for (const ind of d.industries) {
      h += '<div class="cc-card"><div class="cc-card-head">' +
        '<strong>' + esc(ind.industry_path) + '</strong> · ' +
        num(ind.team_count, 'teams in this industry') + '</div>' +
        '<p class="cc-sub">' + esc(ind.definition) + ' <code>' +
        esc(ind.cite_ref) + '</code></p>';
      for (const t of (ind.teams || [])) {
        h += '<div class="cc-team"><div class="cc-card-head">' +
          '<strong>' + esc(t.name) + '</strong> <code>' + esc(t.team_key) +
          '</code> · ' + num(t.skill_count, 'skills') + ' · ' +
          num(t.proofed_count, 'proofed skills') + '</div>' +
          '<p class="cc-sub">' + esc(t.description) + '</p>';
        if (!(t.skills || []).length) {
          h += emptyState('skills for ' + t.team_key, t.why);
        } else {
          h += '<table class="cc-table"><thead><tr><th>skill</th>' +
            '<th>question</th><th>answer key</th><th>proofed</th>' +
            '<th>rating</th><th>rating source</th><th>source ref</th>' +
            '</tr></thead><tbody>';
          for (const s of t.skills) {
            h += '<tr><td>' + esc(s.skill_key) + '</td><td>' +
              esc(s.question) + '</td><td>' + esc(s.answer_key) + '</td>' +
              '<td>' + (s.proofed
                ? '<span class="cc-pill cc-ok">proofed</span>'
                : '<span class="cc-pill cc-unknown">unproven</span>') +
              '</td><td>' + esc(s.rating) + '</td><td>' +
              esc(s.rating_source) + '</td><td><code>' +
              esc(s.source_ref) + '</code></td></tr>';
          }
          h += '</tbody></table>';
        }
        h += '</div>';
      }
      h += '</div>';
    }
    return h;
  }

  function render() {
    if (!root) return;
    let h = '<div class="cc-tabs">';
    for (const t of TABS) {
      h += '<button class="cc-tab' + (t.id === S.tab ? ' cc-tab-on' : '') +
        '" data-tab="' + esc(t.id) + '">' + esc(t.label) + '</button>';
    }
    h += '</div>';
    if (S.error) {
      h += '<div class="cc-error">' + esc(S.error) + '</div>';
    }
    if (S.tab === 'build-steps') h += buildStepsHtml();
    else if (S.tab === 'step-walk') h += stepWalkHtml();
    else if (S.tab === 'find') h += findHtml();
    else h += teamsHtml();
    root.innerHTML = h;
    wire();
  }

  function wire() {
    for (const b of root.querySelectorAll('.cc-tab')) {
      b.addEventListener('click', () => {
        S.tab = b.getAttribute('data-tab');
        if (state) state.consultantTab = S.tab;
        load();
      });
    }
    // THE WALK'S TWO CONTROLS. Each one moves the INDEX and re-reads, so the
    // step on screen is always the register's row at that index — never a copy
    // held in the browser.
    const prev = root.querySelector('#cc-walk-prev');
    if (prev) {
      prev.addEventListener('click', () => {
        if (S.walkIndex > 0) S.walkIndex -= 1;
        load();
      });
    }
    const next = root.querySelector('#cc-walk-next');
    if (next) {
      next.addEventListener('click', () => {
        const total = Number((S.walk && S.walk.total) || 0);
        if (S.walkIndex + 1 < total) S.walkIndex += 1;
        load();
      });
    }
    const run = root.querySelector('#cc-run');
    if (run) {
      run.addEventListener('click', async () => {
        const inp = root.querySelector('#cc-factor');
        S.factor = inp ? inp.value.trim() : '';
        if (!S.factor) {
          toast('a factor_key is required — the factor IS the key');
          return;
        }
        S.busy = true;
        S.findResult = await getJson(API + '/find/' +
          encodeURIComponent(S.factor));
        S.busy = false;
        render();
      });
    }
  }

  load();
}
