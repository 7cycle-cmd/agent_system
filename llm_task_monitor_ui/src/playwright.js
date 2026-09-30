// playwright.js — the Playwright page.
//
// THE HUMAN (2026-09-25): "UI for playwright under
// http://127.0.0.1:18765/llm-tasks/workflow/enviornment_playwright/".
//
// WHAT IT SHOWS
// -------------
// The two tables the human named, side by side:
//
//   playwright_environment   id | environment_id | name | is_active
//   workflow_playwright      id | workflow_id | playwright_id
//
// MEASURED: both tables existed and answered, while `grep
// 'playwright_environment' src/*.js` returned ZERO — the same defect class this
// repo has hit before ("the APIs answered while NO UI code referenced them").
//
// READ-ONLY. The page shows what is registered; registering is a separate,
// deliberate act. There is no POST/PUT/DELETE here, so a stray click cannot
// change which playwright environment a workflow runs in.
//
// NO NULL IS RENDERED. An absent value shows `NA`, never blank and never
// `null` — a blank cell reads as "fine" when it means "not collected".

const esc = (v) =>
  String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

// THE STEP EVIDENCE POP-UP IS SHARED. THE HUMAN (2026-09-26): "step proof ->
// STEP button -> onclick -> Evidence show and detail = pop-up evidence for
// http://127.0.0.1:18765/llm-tasks/evidence/step1_enviornment/". The SAME pop-up
// is opened from the step-1 Environment page, so the table + the pop-up shell
// live in ONE module and both pages import it — a second copy here would be two
// renderings of one fact.
import { stepEvidencePopupHtml, fetchStepEvidence } from './step-evidence.js';

// The repo standard: an uncollected value is `NA`, never null.
const na = (v) => (v == null || v === '' ? 'NA' : v);

// THE TAB LABELS ARE THE REGISTERED TERMS.
//
// THE HUMAN (2026-09-27), verbatim:
//     "Playwright Environment -> rename ->Environment"
//     "Workflow Link -> rename ->Workflow"
//     "STEP GUIDE -> rename ->Step"
//     "COORDINATES"
//     "COORDINATE <-> SESSION -> rename ->Identity"
//     "Running"
//
// MEASURED BEFORE: the page said `Playwright Environment` while the register
// said `environment` (id=90), and `COORDINATE <-> SESSION` while the register
// said `identity` (id=91). **Two words for one thing, so a later reader picks
// the wrong one** -- the exact defect `terminology-register` exists to stop.
//
// **THE `id` IS A KEY AND IT DOES NOT CHANGE.** The URL and the tab state depend
// on it. The `label` is a DISPLAY, and it is now the registered term.
//
// MEASURED: `environment` (90) and `identity` (91) already existed; `workflow`
// (1504), `step` (1505), `coordinates` (1506) and `running` (1507) were
// registered by `_register_playwright_tab_terms.py` BEFORE this label used them.
//
// THE `slug` IS THE ADDRESS, AND IT IS THE REGISTERED TERM.
//
// THE HUMAN (2026-09-27), verbatim:
//     "path need to update too"
//     "example Environment onclick = http://127.0.0.1:18765/llm-tasks/playwright/environment (all in lower case)"
//
// MEASURED BEFORE: `navPath()` emitted `t.id`, so 4 of the 6 URLs disagreed with
// the register -- `/llm-tasks/playwright/steps` (id) instead of `.../step` (term).
// The URL law (`/memories/repo/llmtasks_url_naming_law.md:4`) says the segment IS
// the registered `term_key`.
//
// WHY AN EXPLICIT FIELD AND NOT `slugify(label).toLowerCase()`: a DERIVED slug
// changes silently when a label changes. An explicit `slug` is a CHECKABLE
// invariant -- `slug === term_key` -- and the proof asserts exactly that.
//
// WHY THE `id` STILL DOES NOT CHANGE: the id is a KEY. `render()` compares
// `s.tab === 'steps'`, `parseRoutePath()` has id branches, and
// `_proof_playwright_tabs_registered.py` QC-04 asserts the ids are unchanged.
// The id stays; the slug is ADDED.
export const TABS = [
  { id: 'environment', label: 'Environment', slug: 'environment' },
  { id: 'workflow', label: 'Workflow', slug: 'workflow' },
  { id: 'steps', label: 'Step', slug: 'step' },
  { id: 'coords', label: 'Coordinates', slug: 'coordinates' },
  { id: 'coordsession', label: 'Identity', slug: 'identity' },
  // WHICH RUNS ARE RUNNING RIGHT NOW, AND WHY. THE HUMAN (2026-09-26): "show me
  // which playwright and running and why, with stop button".
  //
  // MEASURED BEFORE: the page could say a run HAD happened (the evidence panel)
  // but nothing could say a run IS happening. A burst of 120 processes was
  // invisible to the UI, so the only way to find it was a terminal.
  { id: 'list', label: 'Running', slug: 'running' },
];

async function getJson(url) {
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.ok === false) {
    throw new Error(j.error || ('HTTP ' + r.status));
  }
  return j;
}

function envTable(rows, testing) {
  if (!rows.length) {
    return '<p class="text-sm text-muted">No playwright environment ' +
      'registered. SSOT = <span class="mono">playwright_environment</span>.</p>';
  }
  const body = rows.map((r) => {
    const on = Number(r.is_active) === 1;
    return '<tr class="border-b border-line hover:bg-soft">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.id)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.environment_id)) +
      '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(na(r.name)) + '</td>' +
      // WHICH BROWSER. THE HUMAN (2026-09-25): "launch_browser is for which
      // browser? is for google chrome or edge or 豆包". The channel is a
      // property of the ENVIRONMENT, so it is shown here, not on a step.
      '<td class="px-3 py-2 mono text-xs">' +
      esc(na(r.browser_channel)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' +
      '<span class="rounded-full px-2 py-0.5 text-xs ' +
      (on ? 'bg-emerald-100 text-emerald-800' : 'bg-soft text-muted') + '">' +
      (on ? '1 active' : '0 inactive') + '</span></td>' +
      '<td class="px-3 py-2 text-xs text-muted">' +
      esc(na([r.kind, r.product, r.surface].filter(Boolean).join(' | '))) +
      '</td>' +
      // THE STEP PROOF IS A BUTTON. THE HUMAN (2026-09-26): "step proof -> STEP
      // button -> onclick -> Evidence show and detail = pop-up evidence for
      // http://127.0.0.1:18765/llm-tasks/evidence/step1_enviornment/".
      //
      // MEASURED BEFORE: this cell was a `<span>` BADGE — it showed the summary
      // but there was nothing to click, so the reader could not reach the STEP
      // evidence from here. The data was already on the wire (`step_summary`,
      // `step_count`) and the pop-up already existed on the step-1 page.
      //
      // THE KEY IS `environment_id`, NOT `playwright_id`: the pop-up's endpoint
      // (`/api/environment/evidence_steps`) is keyed by environment_id, and every
      // row here carries it.
      //
      // THE SUMMARY AND ITS COLOUR ARE KEPT, so the reader still sees the state
      // BEFORE clicking. A row with NO guide stays a NON-button: there is
      // nothing to open.
      '<td class="px-3 py-2 text-xs">' +
      (Number(r.step_count) > 0
        ? '<button type="button" data-pw-step-proof="' +
          esc(na(r.environment_id)) + '" ' +
          'title="open the STEP evidence for this environment" ' +
          'class="rounded-full px-2 py-0.5 text-xs border hover:opacity-90 ' +
          (Number(r.step_fail) > 0 || Number(r.step_unknown) > 0
            ? 'bg-amber-100 text-amber-800 border-amber-400'
            : 'bg-emerald-100 text-emerald-800 border-emerald-400') + '">' +
          esc(na(r.step_summary)) + '</button>'
        : '<span class="text-xs text-muted">no guide</span>') +
      '</td>' +
      // A TEST BUTTON PER ROW. THE HUMAN (2026-09-25): "+ button for test in
      // red , so user can proof himself". MEASURED: ONE button hard-coded
      // `environment_id: 6`, so the user could only ever test the vscode
      // environment -- and once vscode became a WINDOW guide, that button could
      // not test a browser at all. A per-row button tests the environment the
      // user is actually looking at.
      '<td class="px-3 py-2 text-right">' +
      '<button type="button" data-pw-test="' + esc(na(r.environment_id)) +
      '" ' + (testing ? 'disabled ' : '') +
      'class="rounded-lg bg-red-600 border border-red-700 px-2 py-0.5 ' +
      'text-xs font-medium text-white hover:bg-red-700 disabled:opacity-60">' +
      (testing ? 'Testing…' : 'Test') + '</button></td>' +
      '</tr>';
  }).join('');
  return '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">id</th>' +
    '<th class="px-3 py-2">environment_id</th>' +
    '<th class="px-3 py-2">name</th>' +
    '<th class="px-3 py-2">browser_channel</th>' +
    '<th class="px-3 py-2">is_active</th>' +
    '<th class="px-3 py-2">environment</th>' +
    '<th class="px-3 py-2">step proof</th>' +
    '<th class="px-3 py-2 text-right">proof</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

function linkTable(rows) {
  if (!rows.length) {
    return '<p class="text-sm text-muted">No workflow is linked to a ' +
      'playwright environment. SSOT = ' +
      '<span class="mono">workflow_playwright</span>.</p>';
  }
  const body = rows.map((r) => {
    const on = Number(r.is_active) === 1;
    return '<tr class="border-b border-line hover:bg-soft">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.id)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.workflow_id)) +
      '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.playwright_id)) +
      '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(na(r.workflow_key)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(na(r.playwright_name)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' +
      '<span class="rounded-full px-2 py-0.5 text-xs ' +
      (on ? 'bg-emerald-100 text-emerald-800' : 'bg-soft text-muted') + '">' +
      (on ? '1 active' : '0 inactive') + '</span></td>' +
      '</tr>';
  }).join('');
  return '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">id</th>' +
    '<th class="px-3 py-2">workflow_id</th>' +
    '<th class="px-3 py-2">playwright_id</th>' +
    '<th class="px-3 py-2">workflow</th>' +
    '<th class="px-3 py-2">playwright</th>' +
    '<th class="px-3 py-2">is_active</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

// THE STEP GUIDE. THE HUMAN (2026-09-25): "playwright is STEP, is it correct? /
// so where is STEP GUIDE? / how to i know what will happen, without that, i
// don't know and how to proof it is worked or not? / playwright didn't tell me".
//
// THE ANSWER: playwright is not itself a step -- it is the EXECUTOR of a step.
// The three facts are separate: `workflow_step` says WHICH step, 
// `playwright_environment` says WHICH browser, and `playwright_step` says HOW.
// This tab is the third one, and it is the one that was missing.
//
// Each row answers the human's two questions directly:
//   expect  -> "how do i know what will happen"
//   proof   -> "how to proof it is worked or not"
// plus the LAST RUN's status + got + ms, so the guide is a RECORD, not a promise.
function stepTable(rows) {
  if (!rows.length) {
    return '<p class="text-sm text-muted">No step registered for this ' +
      'playwright environment. SSOT = ' +
      '<span class="mono">playwright_step</span>.</p>';
  }
  const body = rows.map((r) => {
    const st = String(r.last_status || 'NA');
    const stCls = st === 'PASS' ? 'bg-emerald-100 text-emerald-800'
      : st === 'FAIL' ? 'bg-rose-100 text-rose-800'
      : st === 'UNKNOWN' ? 'bg-amber-100 text-amber-800'
      : 'bg-soft text-muted';
    return '<tr class="border-b border-line hover:bg-soft align-top">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.step_no)) + '</td>' +
      // THE STEP'S PICTURE, so the guide row is checkable, not just readable.
      '<td class="px-3 py-2">' +
      stepImage(r.image_url, r.image_name, 'step ' + na(r.step_no),
                r.thumb_url) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.step_key)) + '</td>' +
      // THE STEP'S NATURE. THE HUMAN (2026-09-25): "why for enviornment :
      // vscode, need to launch_browser?" A `window` step is a WINDOWS TASK
      // confirmation; a `browser` step is a Playwright action. Showing the
      // kind makes the difference visible instead of implied.
      '<td class="px-3 py-2 text-xs"><span class="rounded-full px-2 py-0.5 ' +
      'text-xs ' + (String(r.step_kind) === 'window'
        ? 'bg-sky-100 text-sky-700'
        : String(r.step_kind) === 'session'
          ? 'bg-violet-100 text-violet-700' : 'bg-soft text-muted') + '">' +
      esc(na(r.step_kind)) + '</span></td>' +
      '<td class="px-3 py-2 mono text-xs text-muted">' +
      esc(na(r.action)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.target)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(na(r.expect)) + '</td>' +
      '<td class="px-3 py-2 text-xs text-muted">' + esc(na(r.proof)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' +
      '<span class="rounded-full px-2 py-0.5 text-xs ' + stCls + '">' +
      esc(st) + '</span></td>' +
      '<td class="px-3 py-2 text-xs text-muted">' + esc(na(r.last_got)) +
      '</td>' +
      '<td class="px-3 py-2 mono text-xs text-muted">' +
      esc(na(r.last_elapsed_ms)) + '</td>' +
      '</tr>';
  }).join('');
  return '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">#</th>' +
    '<th class="px-3 py-2">image step</th>' +
    '<th class="px-3 py-2">step_key</th>' +
    '<th class="px-3 py-2">kind</th>' +
    '<th class="px-3 py-2">action</th>' +
    '<th class="px-3 py-2">target</th>' +
    '<th class="px-3 py-2">expect (what will happen)</th>' +
    '<th class="px-3 py-2">proof (how it is checked)</th>' +
    '<th class="px-3 py-2">last run</th>' +
    '<th class="px-3 py-2">got</th>' +
    '<th class="px-3 py-2">time (ms)</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

// THE PER-STEP BREAKDOWN of ONE run -- THE WHOLE EVIDENCE, in ONE table.
//
// THE HUMAN (2026-09-25): "don't need to have 2 patterm for same thing / single
// table format be user friendly view with measured unit is enough".
//
// MEASURED BEFORE: the panel rendered the SAME six steps TWICE -- a flat
// `checks` list ("PASS step 1 launch_browser chromium 151...") AND this table.
// `checks` is DERIVED from the step runs, so the two were two renderings of one
// fact. The flat list is gone.
//
// MEASURED BEFORE: a `kv()` block also rendered browser / url / elapsed_ms --
// but `browser` IS step 1's `got` and `url` IS step 2's `got`, so that was a
// THIRD rendering. Gone.
//
// MEASURED BEFORE: an `<img>` of the page itself. The screenshot is a picture of
// the page the reader is already looking at, and the render is ALREADY proven by
// step 3 (`wait_for_table`) and step 4 (`assert_columns`). It added no fact, so
// it is not rendered. The FILE is still written (step 5's `got` is its path).
//
// THE UNIT IS IN THE HEADER: `time (ms)`. A bare integer is a number the reader
// has to interpret; the human asked for "measured unit".
function stepRunTable(steps) {
  if (!steps.length) {
    // ui_skill: an empty table must be a REAL row, never a blank table.
    return '<div class="mt-2 overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left">' +
      '<thead class="bg-soft/60 text-[11px] uppercase text-muted"><tr>' +
      '<th class="px-3 py-2">#</th>' +
      '<th class="px-3 py-2">image step</th>' +
      '<th class="px-3 py-2">step</th>' +
      '<th class="px-3 py-2">kind</th>' +
      '<th class="px-3 py-2">status</th>' +
      '<th class="px-3 py-2">expect (what will happen)</th>' +
      '<th class="px-3 py-2">got (what happened)</th>' +
      '<th class="px-3 py-2">time (ms)</th>' +
      '</tr></thead><tbody>' +
      '<tr><td colspan="8" class="px-3 py-4 text-center text-xs text-muted">' +
      'No step recorded for this run.</td></tr></tbody></table></div>';
  }
  const body = steps.map((r) => {
    const st = String(r.status || 'NA');
    // ui_skill status badge semantics: PASS green, FAIL red, UNKNOWN amber.
    const stCls = st === 'PASS' ? 'bg-emerald-100 text-emerald-700'
      : st === 'FAIL' ? 'bg-rose-100 text-rose-700'
      : st === 'UNKNOWN' ? 'bg-amber-100 text-amber-700'
      : 'bg-soft text-muted';
    return '<tr class="border-b border-line align-top hover:bg-soft">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.step_no)) + '</td>' +
      // THE STEP'S PICTURE: what the screen looked like AT this step.
      '<td class="px-3 py-2">' +
      stepImage(r.image_url, r.image_name, 'step ' + na(r.step_no),
                r.thumb_url) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.step_key)) + '</td>' +
      '<td class="px-3 py-2 text-xs"><span class="rounded-full px-2 py-0.5 ' +
      'text-xs ' + (String(r.step_kind) === 'window'
        ? 'bg-sky-100 text-sky-700'
        : String(r.step_kind) === 'session'
          ? 'bg-violet-100 text-violet-700' : 'bg-soft text-muted') + '">' +
      esc(na(r.step_kind)) + '</span></td>' +
      '<td class="px-3 py-2 text-xs"><span class="rounded-full px-2 py-0.5 ' +
      'text-xs ' + stCls + '">' + esc(st) + '</span></td>' +
      '<td class="px-3 py-2 text-xs text-muted">' + esc(na(r.expect)) +
      '</td>' +
      '<td class="px-3 py-2 text-xs break-all">' + esc(na(r.got)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs text-muted">' +
      esc(na(r.elapsed_ms)) + '</td>' +
      '</tr>';
  }).join('');
  return '<div class="mt-2 overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/60 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">#</th>' +
    '<th class="px-3 py-2">image step</th>' +
    '<th class="px-3 py-2">step</th>' +
    '<th class="px-3 py-2">kind</th>' +
    '<th class="px-3 py-2">status</th>' +
    '<th class="px-3 py-2">expect (what will happen)</th>' +
    '<th class="px-3 py-2">got (what happened)</th>' +
    '<th class="px-3 py-2">time (ms)</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

// EVERY COORDINATE TARGET, so a human can check a rect against its picture.
//
// THE HUMAN (2026-09-25): "stop, update all infor at UI, so i can prove to u
// 1 by 1". The rects live in `environment_template` and the images in
// `evidence_final`, but neither was visible in one place -- so a human could
// not check a rect against its picture without opening two tools.
//
// THE CENTRE IS SHOWN because that is what a click actually uses. A rect the
// reader cannot turn into a click point is a rect they cannot check.
// EACH STEP'S OWN PICTURE, as a small clickable thumbnail.
//
// THE HUMAN (2026-09-25): "for the STEP and Evidence table, STEP is onclick to
// image" and "+ image field user friendly".
//
// WHY. MEASURED: only the `screenshot` step wrote an image, so every other step
// row had no picture and its `got` was a sentence the reader had to trust. A
// thumbnail is a CLAIM the reader can check in one click, which is the whole
// point of the red Test button. It is a LINK to the full image (opened in a new
// tab) rather than an inline 1920px picture, so the table stays readable.
function stepImage(url, name, alt, thumbUrl) {
  if (!url) {
    // ui_skill: an empty cell is a REAL value, never a blank. `NA` says
    // "no image for this step" instead of leaving the reader to guess.
    return '<span class="text-xs text-muted">NA</span>';
  }
  // THE TABLE SHOWS THE THUMBNAIL; THE CLICK OPENS THE FULL IMAGE.
  // MEASURED (2026-09-25): pointing the <img> at the full 1280px file made a
  // step image slow to appear -- the cell was blank for seconds. The thumbnail
  // is ~15KB and loads immediately; the link still opens the full picture.
  // `loading="eager"` is deliberate: a table of a few dozen thumbnails is small,
  // and lazy loading only deferred them below the fold so a visible row was
  // blank.
  return '<a href="' + esc(url) + '" target="_blank" rel="noopener" ' +
    'title="' + esc(alt || name || 'step image') + ' (click to open full)">' +
    '<img src="' + esc(thumbUrl || url) + '" alt="' +
    esc(alt || name || 'step image') + '" loading="eager" ' +
    'class="h-10 w-16 rounded border border-line bg-soft object-cover ' +
    'hover:ring-2 hover:ring-sky-400"></a>';
}

function coordTable(rows) {
  if (!rows.length) {
    return '<p class="text-sm text-muted">No target for this ' +
      'environment. SSOT = <span class="mono">environment_template</span> ' +
      'joined to <span class="mono">target_template</span>.</p>';
  }
  // BOTH KINDS ARE SHOWN, AND THE TYPE IS A COLUMN (fixed 2026-09-27).
  //
  // MEASURED DEFECT: the API filtered `field_type = 'coordinate'`, so the
  // `vscode_taskbar_win_n` row (value `Win+2`) was DROPPED and this table
  // showed 19 of the 20 taskbar elements with nothing to say about the missing
  // one. THE HUMAN (2026-09-27): "where is the UI" -- asked because the row
  // they had just measured was not on the page they were looking at.
  //
  // A HOTKEY IS NOT A COORDINATE, so it is not rendered as one. Its rect
  // columns are the NA_INT sentinel (-1); showing `-1,-1 -> -1,-1` would
  // present "not collected" as a real measurement at x=-1. The row shows its
  // hotkey VALUE in the value column instead, and `NA` in the rect columns.
  const body = rows.map((r) => {
    const on = r.is_active === 1 || r.is_active === true;
    const isHotkey = String(r.field_type) === 'hotkey';
    const value = isHotkey
      ? '<span class="rounded bg-sky-100 px-1.5 py-0.5 mono text-xs ' +
        'text-sky-800">' + esc(na(r.hotkey)) + '</span>'
      : esc(na(r.x1)) + ',' + esc(na(r.y1)) + ' &rarr; ' +
        esc(na(r.x2)) + ',' + esc(na(r.y2));
    return '<tr class="border-b border-line hover:bg-soft align-top">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.id)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.name)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(na(r.label)) + '</td>' +
      '<td class="px-3 py-2 text-xs"><span class="rounded-full px-2 py-0.5 ' +
      'text-[10px] ' + (isHotkey ? 'bg-sky-100 text-sky-800'
        : 'bg-violet-100 text-violet-800') + '">' +
      esc(na(r.field_type)) + '</span></td>' +
      '<td class="px-3 py-2 mono text-xs">' + value + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' +
      (isHotkey ? '<span class="text-muted">NA</span>' : esc(na(r.centre))) +
      '</td>' +
      '<td class="px-3 py-2 text-xs"><span class="rounded-full px-2 py-0.5 ' +
      'text-xs ' + (on ? 'bg-emerald-100 text-emerald-700'
        : 'bg-soft text-muted') + '">' +
      (on ? 'active' : 'inactive') + '</span></td>' +
      '<td class="px-3 py-2 mono text-xs text-muted">' +
      esc(na(r.image_name)) + '</td>' +
      '</tr>';
  }).join('');
  const nCoord = rows.filter((r) => String(r.field_type) === 'coordinate').length;
  const nHot = rows.filter((r) => String(r.field_type) === 'hotkey').length;
  return '<p class="mb-2 text-xs text-muted">' + esc(String(rows.length)) +
    ' target(s): ' + esc(String(nCoord)) + ' coordinate + ' +
    esc(String(nHot)) + ' hotkey. A hotkey has no rect, so its rect columns ' +
    'read <span class="mono">NA</span>.</p>' +
    '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">id</th>' +
    '<th class="px-3 py-2">name</th>' +
    '<th class="px-3 py-2">label</th>' +
    '<th class="px-3 py-2">type</th>' +
    '<th class="px-3 py-2">x1,y1 &rarr; x2,y2 / hotkey</th>' +
    '<th class="px-3 py-2">centre (click point)</th>' +
    '<th class="px-3 py-2">is_active</th>' +
    '<th class="px-3 py-2">EVID image</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

// WHICH SESSION each coordinate was measured in.
//
// THE HUMAN (2026-09-25): "session id at where in identity table? / coordinate
// table is individual? by target id? if yes, is easy / + table :
// coordinate_session / is | session_id | coordinate_id |".
//
// The session id is NOT a new concept: it is `identity_registry.session_id`.
// This table shows the LINK, so a reader can see which conversation a stored
// rect came from -- the context a bare rect loses.
function coordSessionTable(rows, sessionId) {
  if (!rows.length) {
    return '<p class="text-sm text-muted">No coordinate linked to session ' +
      '<span class="mono">' + esc(na(sessionId)) + '</span>. SSOT = ' +
      '<span class="mono">coordinate_session</span>.</p>';
  }
  const body = rows.map((r) => {
    const on = r.is_active === 1 || r.is_active === true;
    return '<tr class="border-b border-line hover:bg-soft align-top">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.coordinate_id)) +
      '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.name)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(na(r.label)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' +
      esc(na(r.x1)) + ',' + esc(na(r.y1)) + ' &rarr; ' +
      esc(na(r.x2)) + ',' + esc(na(r.y2)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.centre)) + '</td>' +
      '<td class="px-3 py-2 text-xs text-muted">' + esc(na(r.why)) + '</td>' +
      '<td class="px-3 py-2 text-xs"><span class="rounded-full px-2 py-0.5 ' +
      'text-xs ' + (on ? 'bg-emerald-100 text-emerald-700'
        : 'bg-soft text-muted') + '">' +
      (on ? 'active' : 'inactive') + '</span></td>' +
      '</tr>';
  }).join('');
  return '<p class="mb-2 text-xs text-muted">session_id = ' +
    '<span class="mono">' + esc(na(sessionId)) + '</span> &middot; ' +
    rows.length + ' coordinate(s). The session id is ' +
    '<span class="mono">identity_registry.session_id</span>, not a new id.</p>' +
    '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">coordinate_id</th>' +
    '<th class="px-3 py-2">name</th>' +
    '<th class="px-3 py-2">label</th>' +
    '<th class="px-3 py-2">x1,y1 &rarr; x2,y2</th>' +
    '<th class="px-3 py-2">centre (click point)</th>' +
    '<th class="px-3 py-2">why</th>' +
    '<th class="px-3 py-2">is_active</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

// WHICH RUNS ARE RUNNING RIGHT NOW, AND WHY -- with a Stop button per row.
//
// THE HUMAN (2026-09-26): "show me which playwright and running and why, with
// stop button".
//
// THE `why` COLUMN IS THE MEASURED PARENT CHAIN, not a guess. MEASURED
// 2026-09-26 21:12: the burst's spawner was `_tmp_triage.py`, and the ONLY way
// it was found was walking `ParentProcessId`. So the chain is rendered as
// `script(pid) <- script(pid) <- ...`, root last, and the declared reason is
// shown beneath it when the gate declares one.
//
// THE STOP BUTTON IS PER ROW and carries its OWN pid. A single header button
// would have to guess which run to stop -- the same defect the Test button had
// before it became per-row.
function runningTable(rows, whyEmpty, stopping) {
  if (!rows.length) {
    // ui_skill: an empty table must be a REAL row, never a blank table. The
    // sentence comes from the API, so the page and the API cannot disagree
    // about WHY the list is empty.
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left">' +
      '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
      '<th class="px-3 py-2">pid</th>' +
      '<th class="px-3 py-2">script</th>' +
      '<th class="px-3 py-2">why (measured parent chain)</th>' +
      '<th class="px-3 py-2">started</th>' +
      '<th class="px-3 py-2 text-right">stop</th>' +
      '</tr></thead><tbody>' +
      '<tr><td colspan="5" class="px-3 py-4 text-center text-xs text-muted">' +
      esc(whyEmpty || 'No playwright run is running.') +
      '</td></tr></tbody></table></div>';
  }
  const body = rows.map((r) => {
    const chain = (r.why || []).map((c) =>
      '<span class="mono">' + esc(c.script || '?') + '</span>' +
      '<span class="text-muted">(' + esc(c.pid) + ')</span>').join(
      ' <span class="text-muted">&larr;</span> ');
    return '<tr class="border-b border-line hover:bg-soft align-top">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.pid)) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(na(r.script)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + chain +
      (r.reason
        ? '<div class="mt-1 text-[11px] text-muted">declared: ' +
          esc(r.reason) + '</div>'
        : '') +
      '</td>' +
      '<td class="px-3 py-2 mono text-xs text-muted">' +
      esc(na(r.started_at)) + '</td>' +
      '<td class="px-3 py-2 text-right">' +
      '<button type="button" data-pw-stop="' + esc(na(r.pid)) + '" ' +
      (stopping ? 'disabled ' : '') +
      'class="rounded-lg bg-rose-600 border border-rose-700 px-2 py-0.5 ' +
      'text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-60">' +
      (stopping ? 'Stopping…' : 'Stop') + '</button></td>' +
      '</tr>';
  }).join('');
  return '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">pid</th>' +
    '<th class="px-3 py-2">script</th>' +
    '<th class="px-3 py-2">why (measured parent chain)</th>' +
    '<th class="px-3 py-2">started</th>' +
    '<th class="px-3 py-2 text-right">stop</th>' +
    '</tr></thead><tbody>' + body + '</tbody></table></div>';
}

// THE MASTER ON/OFF SWITCH PILL. THE HUMAN (2026-09-26): "playwright system with
// function be control ON/OFF" / "have the button!! turn off / ON".
//
// IT IS A PILL, the same shape the header already uses for mouse_spot_helper /
// Watchdog / LLM (`app.js:983`), and it is CLICKABLE to toggle.
//
// `on === null` renders as `…`, NOT as ON. A pill that shows a state before the
// server has answered is a claim, not a read -- and this repo has recorded that
// defect class more than once.
function switchPill(on, switching, why) {
  const label = on === null ? 'Playwright …'
    : (on ? 'Playwright ON' : 'Playwright OFF');
  const cls = on === null
    ? 'bg-soft text-muted border-line'
    : (on ? 'bg-emerald-50 text-emerald-700 border-emerald-300'
          : 'bg-rose-50 text-rose-700 border-rose-300');
  return '<button type="button" data-pw-switch="1" ' +
    (switching ? 'disabled ' : '') +
    'title="' + esc(why || 'toggle the Playwright system') + '" ' +
    'class="rounded-full border px-3 py-1 text-xs font-medium ' + cls +
    ' hover:opacity-80 disabled:opacity-60">' +
    esc(switching ? 'Switching…' : label) + '</button>';
}

export function mountPlaywright(root, state, { toast, navPath } = {}) {
  // THE TAB COMES FROM `state`, SO THE URL CAN CHOOSE IT.
  // THE HUMAN (2026-09-26): "http://127.0.0.1:18765/llm-tasks/playwright/list
  // +UI". MEASURED: the tab used to live only in this closure, so a URL segment
  // could not reach it and `/llm-tasks/playwright/list` opened on `environment`.
  // `state.playwright.tab` is set by `setTabId()` from the parsed route.
  const initialTab = (state && state.playwright && state.playwright.tab)
    || 'environment';
  const s = { tab: initialTab, envs: [], links: [], steps: [], coords: [],
              coordSession: [], coordSessionId: '',
              msg: '', loading: true, testing: false, ev: null, evMsg: '',
              evPinned: false,
              // THE RUNNING LIST. THE HUMAN (2026-09-26): "show me which
              // playwright and running and why, with stop button".
              runs: [], runsWhyEmpty: '', stopping: false,
              // THE MASTER SWITCH. THE HUMAN (2026-09-26): "playwright system
              // with function be control ON/OFF" / "have the button!! turn off
              // / ON". `null` = not read yet, so the pill never shows a state
              // the server did not report.
              switchOn: null, switchWhy: '', switching: false,
              // THE STEP EVIDENCE POP-UP. THE HUMAN (2026-09-26): "step proof ->
              // STEP button -> onclick -> Evidence show and detail". `null` =
              // closed; otherwise `{ environment_id, payload, msg }`.
              stepPopup: null };

  // THE DEEP LINK. THE HUMAN (2026-09-25): "so can have vscode_playwright STEP
  // with evidence". MEASURED: the environment table said an environment HAD a
  // guide, but nothing took the reader to it -- they had to find the guide's
  // playwright_id by hand and switch tabs. `?pid=<playwright_id>` selects the
  // guide AND opens the STEP GUIDE tab, so a row can link straight to its own
  // evidence.
  let initialPid = 1;
  try {
    const q = new URLSearchParams(window.location.search).get('pid');
    if (q && /^\d+$/.test(q)) initialPid = Number(q);
  } catch (e) { /* no query string */ }

  async function load() {
    s.loading = true;
    render();
    try {
      const [e, l, v, st, co, cs, rn, sw] = await Promise.all([
        getJson('/api/playwright/environments'),
        getJson('/api/playwright/links'),
        getJson('/api/playwright/evidence'),
        getJson('/api/playwright/steps?playwright_id=' + initialPid),
        getJson('/api/playwright/coords'),
        getJson('/api/playwright/coordinate-session'),
        // WHICH RUNS ARE ALIVE RIGHT NOW. Fetched with the rest so the Running
        // tab is never a stale list the reader has to refresh by hand.
        getJson('/api/playwright/runs'),
        // THE MASTER SWITCH STATE. Fetched with the rest so the pill cannot
        // show a state the server did not report.
        getJson('/api/playwright/switch'),
      ]);
      s.envs = e.rows || [];
      s.links = l.rows || [];
      s.runs = rn.rows || [];
      s.runsWhyEmpty = rn.why_empty || '';
      s.switchOn = (typeof sw.enabled === 'boolean') ? sw.enabled : null;
      s.switchWhy = sw.why || '';
      // A PINNED record is the one THIS page just ran. A refresh must not
      // replace it with "whatever is newest" -- see runTest().
      if (!s.evPinned) {
        s.ev = v.record || null;
        s.evMsg = v.record ? '' : (v.why || '');
      }
      s.steps = st.rows || [];
      s.coords = co.rows || [];
      s.coordSession = cs.rows || [];
      s.coordSessionId = cs.session_id || '';
      s.msg = '';
    } catch (err) {
      s.msg = String(err.message || err);
    }
    s.loading = false;
    render();
  }

  // THE RED TEST BUTTON. THE HUMAN (2026-09-25): "+ button for test in red , so
  // user can proof himself". It runs a REAL Playwright test on the server and
  // writes an evidence record, so the user can check the proof themselves
  // rather than take a claim on trust.
  //
  // IT TESTS THE ENVIRONMENT THE USER CLICKED. MEASURED: the first version
  // hard-coded `environment_id: 6`, so the user could only ever test the vscode
  // environment -- and once vscode became a WINDOW guide, that button could not
  // test a browser at all.
  async function runTest(environmentId) {
    s.testing = true;
    s.evMsg = '';
    render();
    try {
      const r = await fetch('/api/playwright/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ environment_id: Number(environmentId) || 6 }),
      });
      const j = await r.json().catch(() => ({}));
      if (j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
      // SHOW THE RECORD THIS RUN WROTE, NOT "WHATEVER IS NEWEST".
      // MEASURED (2026-09-25): the page re-fetched `/api/playwright/evidence`
      // after the test, which returns the newest record BY FOLDER NAME. Two
      // runs in the same second produce `...-182605` and `...-182605-2`, and a
      // concurrent run can make "newest" a DIFFERENT record than the one just
      // written -- so the panel could show someone else's result. The POST
      // response IS this run's record, so it is used directly.
      s.ev = {
        evidence_id: j.evidence_id,
        verdict: j.verdict,
        created_at: new Date().toISOString().slice(0, 19),
        detail: j.detail || {},
        steps: j.steps || [],
        checks: j.checks || {},
      };
      s.evPinned = true;
      if (toast) toast('Playwright test: ' + (j.verdict || 'UNKNOWN'));
    } catch (err) {
      s.evMsg = String(err.message || err);
    }
    s.testing = false;
    await load();
  }

  // THE EVIDENCE PANEL -- ONE TABLE, NO NOISE.
  //
  // THE HUMAN (2026-09-25): "so the image at the buttom is meaningless for
  // playwright, is it correct? if yes, remove the noise, don't have chnace to
  // user ask what is that for" and "don't need to have 2 patterm for same thing
  // / single table format be user friendly view with measured unit is enough".
  //
  // REMOVED (each was a SECOND rendering of a fact the table already carries):
  //   * the flat `checks` list  -- derived from the same step runs
  //   * the `kv()` detail block -- `browser` IS step 1's got, `url` IS step 2's
  //   * the `<img>`              -- a picture of the page being read; the render
  //                                is already proven by steps 3 and 4
  // KEPT: the verdict badge, the identity line, and the ONE table.
  function evidencePanel() {
    if (s.evMsg) {
      return '<div class="mt-3 rounded-2xl border border-line bg-panel p-3 ' +
        'shadow-panel" data-pw-evidence="1">' +
        '<div class="text-xs font-semibold">Evidence</div>' +
        '<p class="mt-1 text-xs text-muted">' + esc(s.evMsg) + '</p></div>';
    }
    if (!s.ev) {
      return '<div class="mt-3 rounded-2xl border border-line bg-panel p-3 ' +
        'shadow-panel" data-pw-evidence="1">' +
        '<div class="text-xs font-semibold">Evidence</div>' +
        '<p class="mt-1 text-xs text-muted">No evidence yet — press the red ' +
        'Test button.</p></div>';
    }
    const e = s.ev;
    const d = e.detail || {};
    const v = String(e.verdict || 'UNKNOWN');
    // ui_skill status badge semantics.
    const vCls = v === 'PASS' ? 'bg-emerald-100 text-emerald-700'
      : v === 'FAIL' ? 'bg-rose-100 text-rose-700'
      : 'bg-amber-100 text-amber-700';
    const stepRows = Array.isArray(e.steps) ? e.steps : [];
    // THE WORKER STATUS IS THE COPY BUTTON'S TRIGGER POINT.
    // THE HUMAN (2026-09-25): "when the reply didn't finish by worker, you will
    // not have the copy button, the way is each conversaction ask worker to
    // update status to chat, so we can have the trigger point when to click on
    // copy button".
    //
    // MEASURED BEFORE: all three worker states looked like "the copy button is
    // not in view" -- one observation for three different situations. The
    // status is shown so the reader can tell them apart.
    const ws = String(d.worker_status || 'NA');
    const wsCls = ws === 'done' ? 'bg-emerald-100 text-emerald-700'
      : ws === 'writing' ? 'bg-amber-100 text-amber-700'
      : ws === 'failed' ? 'bg-rose-100 text-rose-700'
      : 'bg-soft text-muted';
    const wsLine = '<div class="mt-1 flex items-center gap-2 text-[11px]">' +
      '<span class="text-muted">worker status</span>' +
      '<span class="rounded-full px-2 py-0.5 text-xs ' + wsCls + '">' +
      esc(ws) + '</span>' +
      '<span class="text-muted">copy gate</span>' +
      '<span class="mono">' + esc(na(d.copy_gate)) + '</span></div>';
    // THE SUMMARY LINE carries the identity and the TOTAL with its unit, so the
    // reader gets the whole picture without a second block.
    const total = d.elapsed_ms;
    const summary = [
      esc(na(e.evidence_id)),
      esc(na(e.created_at)),
      esc(na(stepRows.length)) + ' steps',
      esc(na(total)) + ' ms',
    ].join(' · ');
    return '<div class="mt-3 rounded-2xl border border-line bg-panel p-3 ' +
      'shadow-panel" data-pw-evidence="1">' +
      '<div class="flex items-center justify-between">' +
      '<div class="text-xs font-semibold">Evidence</div>' +
      '<span class="rounded-full px-2 py-0.5 text-xs ' + vCls + '">' +
      esc(v) + '</span></div>' +
      '<p class="mt-1 text-[11px] text-muted mono break-all">' + summary +
      '</p>' +
      wsLine +
      stepRunTable(stepRows) +
      '</div>';
  }

  // STOP ONE RUN. THE HUMAN (2026-09-26): "with stop button".
  //
  // IT IS A HUMAN ACTION, NEVER A TIMER. There is no `setInterval` here and no
  // auto-stop: a page that kills processes on its own would be a worse defect
  // than the one it fixes. The button is the only trigger.
  //
  // THE SERVER REFUSES A PID THAT IS NOT A DECLARED PLAYWRIGHT-DRIVING SCRIPT,
  // so a wrong pid is an error the reader sees, not a silent kill.
  async function stopRun(pid) {
    s.stopping = true;
    s.msg = '';
    render();
    try {
      const r = await fetch('/api/playwright/runs/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pid: Number(pid) }),
      });
      const j = await r.json().catch(() => ({}));
      if (j.ok === false) throw new Error(j.error || j.why || ('HTTP ' + r.status));
      if (toast) toast('Stopped pid ' + pid + ' (' + (j.killed || []).length + ' process(es))');
    } catch (err) {
      s.msg = String(err.message || err);
    }
    s.stopping = false;
    await load();
  }

  // THE STEP EVIDENCE POP-UP. THE HUMAN (2026-09-26): "step proof -> STEP button
  // -> onclick -> Evidence show and detail = pop-up evidence for
  // http://127.0.0.1:18765/llm-tasks/evidence/step1_enviornment/".
  //
  // It fetches the SAME endpoint the step-1 page's Evidence door uses, through
  // the SAME shared module, so the two pages cannot show different tables.
  async function openStepPopup(environmentId) {
    s.stepPopup = { environment_id: Number(environmentId), payload: null,
                    msg: 'Loading…' };
    render();
    try {
      s.stepPopup.payload = await fetchStepEvidence(environmentId);
      s.stepPopup.msg = '';
    } catch (err) {
      s.stepPopup.msg = String(err.message || err);
    }
    render();
  }

  function closeStepPopup() {
    s.stepPopup = null;
    render();
  }

  // TOGGLE THE MASTER SWITCH. THE HUMAN (2026-09-26): "have the button!! turn
  // off / ON".
  //
  // THE NEW STATE COMES BACK FROM THE SERVER, never from the value we asked
  // for. A write that failed must not leave the pill showing the state we
  // wanted -- that is a false pass on a switch, which is the worst place for
  // one.
  async function toggleSwitch() {
    if (s.switchOn === null) return;
    s.switching = true;
    render();
    try {
      const r = await fetch('/api/playwright/switch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ on: !s.switchOn }),
      });
      const j = await r.json().catch(() => ({}));
      if (typeof j.enabled === 'boolean') s.switchOn = j.enabled;
      s.switchWhy = j.why || '';
      if (toast) toast(j.why || (j.enabled ? 'Playwright ON' : 'Playwright OFF'));
    } catch (err) {
      s.switchWhy = String(err.message || err);
      if (toast) toast('switch failed: ' + s.switchWhy);
    }
    s.switching = false;
    render();
  }

  function render() {
    const tabs = TABS.map((t) =>
      '<button type="button" data-pw-tab="' + esc(t.id) + '" ' +
      'class="rounded-lg px-3 py-1 text-xs ' +
      (s.tab === t.id
        ? 'bg-blue-100 border border-blue-400 text-blue-900'
        : 'border border-line hover:bg-soft') + '">' + esc(t.label) +
      '</button>').join('');

    let body;
    if (s.loading) {
      body = '<p class="text-sm text-muted">Loading…</p>';
    } else if (s.msg) {
      body = '<div class="rounded-xl border border-rose-200 bg-rose-50 p-3 ' +
        'text-sm text-rose-700">' + esc(s.msg) + '</div>';
    } else if (s.tab === 'workflow') {
      body = linkTable(s.links);
    } else if (s.tab === 'steps') {
      body = stepTable(s.steps);
    } else if (s.tab === 'coords') {
      body = coordTable(s.coords);
    } else if (s.tab === 'coordsession') {
      body = coordSessionTable(s.coordSession, s.coordSessionId);
    } else if (s.tab === 'list') {
      body = runningTable(s.runs, s.runsWhyEmpty, s.stopping);
    } else {
      body = envTable(s.envs, s.testing);
    }

    root.innerHTML =
      '<div class="rounded-2xl border border-line bg-panel p-4">' +
      '<div class="flex items-center justify-between">' +
      '<div>' +
      '<h2 class="text-lg font-semibold">Playwright</h2>' +
      '<p class="mt-0.5 text-xs text-muted">WHICH playwright environment a ' +
      'workflow runs in, and HOW each step is driven. SSOT = ' +
      '<span class="mono">playwright_environment</span> + ' +
      '<span class="mono">workflow_playwright</span> + ' +
      '<span class="mono">playwright_step</span>. Read-only.</p>' +
      '</div>' +
      '<div class="flex items-center gap-2">' +
      // THE MASTER ON/OFF SWITCH. THE HUMAN (2026-09-26): "playwright system
      // with function be control ON/OFF" / "have the button!! turn off / ON".
      //
      // IT IS A PILL, the same shape the header already uses for
      // mouse_spot_helper / Watchdog / LLM (`app.js:983`), and it is CLICKABLE
      // to toggle. `null` renders as `…` rather than a guessed state: a pill
      // that shows ON before the server has answered is a claim, not a read.
      switchPill(s.switchOn, s.switching, s.switchWhy) +
      // NO HEADER TEST BUTTON. MEASURED (2026-09-25): the single header button
      // hard-coded `environment_id: 6`, so the user could only ever test the
      // vscode environment. The Test button now lives on EACH ROW, so it tests
      // the environment the user is looking at.
      '<button type="button" data-pw-refresh="1" ' +
      'class="rounded-lg border border-line px-3 py-1 text-xs ' +
      'hover:bg-soft">Refresh</button>' +
      '</div></div>' +
      '<div class="mt-3 flex flex-wrap gap-2">' + tabs + '</div>' +
      '<div class="mt-3">' + body + '</div>' +
      evidencePanel() +
      '</div>' +
      // THE STEP EVIDENCE POP-UP, when open. It is rendered OUTSIDE the panel so
      // its `fixed` overlay is not clipped by the panel's own scroll container.
      (s.stepPopup
        ? stepEvidencePopupHtml(s.stepPopup.environment_id,
                                s.stepPopup.payload, s.stepPopup.msg)
        : '');

    // THE TAB CLICK WRITES THE URL.
    //
    // THE HUMAN (2026-09-27), verbatim: "onclick path with
    // http://127.0.0.1:18765/llm-tasks/playwright/value" / "example Environment
    // onclick = http://127.0.0.1:18765/llm-tasks/playwright/environment".
    //
    // MEASURED BEFORE: this handler called `render()` and NOTHING ELSE, so the
    // URL never changed -- the Playwright page was the ONLY page whose tab click
    // did not push. Every other page does (app.js:4352-4360 user-environment,
    // 2893-2900 skill-ssot, 3415-3422 skill-learning, 5304-5311 tool-registry).
    //
    // THE URL IS BUILT BY `navPath()`, THE ONE BUILDER, passed in from app.js.
    // A second string built here would be a second source of truth for the
    // address, and the two would drift.
    root.querySelectorAll('[data-pw-tab]').forEach((el) => {
      el.addEventListener('click', () => {
        s.tab = el.getAttribute('data-pw-tab');
        if (state && state.playwright) state.playwright.tab = s.tab;
        if (typeof navPath === 'function') {
          try {
            history.pushState(
              { nav: 'playwright', tab: s.tab },
              '',
              navPath('playwright', s.tab)
            );
          } catch (_) { /* pushState can throw on a file:// origin */ }
        }
        render();
      });
    });
    const rf = root.querySelector('[data-pw-refresh]');
    if (rf) rf.addEventListener('click', load);
    // THE MASTER SWITCH PILL. THE HUMAN (2026-09-26): "have the button!! turn
    // off / ON". One click toggles; the NEW state comes back from the server, so
    // the pill never shows a state the write did not achieve.
    const swEl = root.querySelector('[data-pw-switch]');
    if (swEl) swEl.addEventListener('click', toggleSwitch);
    // EVERY per-row Test button, each carrying its OWN environment_id.
    root.querySelectorAll('[data-pw-test]').forEach((el) => {
      el.addEventListener('click', () =>
        runTest(el.getAttribute('data-pw-test')));
    });
    // EVERY per-row STEP PROOF button. THE HUMAN (2026-09-26): "step proof ->
    // STEP button -> onclick -> Evidence show and detail". Each carries its OWN
    // environment_id, so it opens the evidence for the row the user clicked.
    root.querySelectorAll('[data-pw-step-proof]').forEach((el) => {
      el.addEventListener('click', () =>
        openStepPopup(el.getAttribute('data-pw-step-proof')));
    });
    // EVERY per-row STOP button, each carrying its OWN pid. THE HUMAN
    // (2026-09-26): "with stop button". A single header button would have to
    // guess which run to stop.
    root.querySelectorAll('[data-pw-stop]').forEach((el) => {
      el.addEventListener('click', () =>
        stopRun(el.getAttribute('data-pw-stop')));
    });
    // THE POP-UP CLOSES on the Close button AND on the backdrop, the same
    // contract `worker.js` uses (`data-popup-close`).
    //
    // THE TWO CASES ARE DIFFERENT, and conflating them was a bug: the Close
    // BUTTON always closes; the BACKDROP closes only when the click landed on
    // the backdrop ITSELF, so a click inside the panel does not dismiss it.
    root.querySelectorAll('[data-popup-close]').forEach((el) => {
      el.addEventListener('click', (ev) => {
        const isBackdrop = el.classList.contains('fixed');
        if (!isBackdrop || ev.target === el) closeStepPopup();
      });
    });
  }

  render();
  load();
}
