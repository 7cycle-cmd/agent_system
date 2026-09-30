// step-evidence.js — THE STEP EVIDENCE TABLE, in ONE place.
//
// THE HUMAN (2026-09-26): "image = pop-up evidence for
// http://127.0.0.1:18765/llm-tasks/evidence/step1_enviornment/ / that is the STEP
// for playwright http://127.0.0.1:18765/llm-tasks/playwright/enviornment for ID
// 1 / ... step proof -> STEP button -> onclick -> Evidence show and detail =
// pop-up evidence for .../step1_enviornment/".
//
// So the SAME pop-up is opened from TWO pages:
//   * the step-1 Environment page (the `Evidence` door), and
//   * the Playwright page (the `step proof` button).
//
// WHY THIS MODULE EXISTS. MEASURED: the table was rendered INSIDE `worker.js`
// (`envPopupHtml`, `f === 'evidence'`). A second copy in `playwright.js` would be
// TWO renderings of ONE fact — the exact defect `playwright.js:250-260` already
// records ("don't need to have 2 patterm for same thing"). So the markup is
// EXTRACTED here and both pages import it.
//
// THE DATA IS THE STEP GUIDE. `playwright_step` says what each step does, what
// WILL happen (`expect`) and how to PROOF it (`proof`); `playwright_step_run`
// says what ACTUALLY happened (`status`, `got`, `image_name`, `image_why`). The
// endpoint joins them, so the pop-up and the run cannot disagree.

const esc = (v) =>
  String(v == null ? '' : v)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

// THE METHOD BADGE. `hotkey` is BLUE and `coordinate` is PINK — the SAME colours
// the step-1 table already uses for those two doors (`hotkeyCell` /
// `coordinateCell`), so a reader learns the colour once.
export function methodCls(m) {
  return String(m) === 'hotkey'
    ? 'bg-blue-100 text-blue-800 border-blue-400'
    : String(m) === 'coordinate'
      ? 'bg-pink-100 text-pink-800 border-pink-400'
      : String(m) === 'window'
        ? 'bg-sky-100 text-sky-700 border-sky-300'
        : String(m) === 'session'
          ? 'bg-violet-100 text-violet-700 border-violet-300'
          : 'bg-soft text-muted border-line';
}

export function statusCls(st) {
  return String(st) === 'PASS'
    ? 'bg-emerald-100 text-emerald-800'
    : String(st) === 'FAIL' ? 'bg-rose-100 text-rose-800'
      : String(st) === 'UNKNOWN' ? 'bg-amber-100 text-amber-800'
        : 'bg-soft text-muted';
}

// THE STEP TABLE: `STEP | instruction | method | evidence`.
//
// `rows` is the `rows` array from `/api/environment/evidence_steps`.
export function stepEvidenceTable(rows) {
  const stRows = rows || [];
  if (!stRows.length) {
    return '<p class="mt-1 text-[11px] text-muted">' +
      'This environment declares no STEP guide.</p>';
  }
  return '<div class="mt-2 overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-[11px] text-muted"><tr>' +
    '<th class="px-2 py-1">STEP</th>' +
    '<th class="px-2 py-1">instruction</th>' +
    '<th class="px-2 py-1">method</th>' +
    '<th class="px-2 py-1">evidence</th></tr></thead><tbody>' +
    stRows.map((r) => {
      const st = String(r.last_status || 'NA');
      const imgName = r.image_name || '';
      const imgUrl = r.image_url || '';
      const imgPath = r.image_path || '';
      // THE STEP'S OWN PICTURE, 50x50 (the size the human set on 2026-09-26),
      // with the file location on hover.
      //
      // A MISSING PICTURE IS ATTRIBUTED. THE HUMAN (2026-09-26): every row read
      // "no image yet" and NOTHING said why. MEASURED: the capture swallowed its
      // exception, so a DEAD screenshotter looked identical to "this step has no
      // picture". The run now records `image_why`, so the badge carries the
      // REASON on hover.
      const imgWhy = r.image_why || '';
      const imgCell = (imgName && imgName !== 'NA' && imgUrl)
        ? '<span class="evid-hover inline-block">' +
          '<span class="mono text-[10px] text-blue-700 underline ' +
          'cursor-help">' + esc(imgName) + '</span>' +
          '<span class="evid-pop">' +
          '<img src="' + esc(imgUrl) + '" alt="" ' +
          'style="width:50px;height:50px;object-fit:contain" ' +
          'class="rounded border border-line bg-black">' +
          '<span class="mt-1 block mono text-[10px] text-muted ' +
          'break-all">' + esc(imgPath || imgName) + '</span>' +
          '</span></span>'
        : '<span class="rounded-full bg-amber-100 px-1.5 py-0.5 ' +
          'text-[10px] text-amber-800" title="' +
          esc(imgWhy || 'no reason recorded') + '">' +
          (imgWhy ? 'image failed' : 'no image yet') + '</span>' +
          (imgWhy
            ? '<div class="mt-0.5 max-w-[22rem] text-[10px] ' +
              'text-rose-700 break-all">' + esc(imgWhy) + '</div>'
            : '');
      // `expect` / `proof` live in the hover title, so the row is checkable
      // without widening the table.
      const tip = 'expect: ' + (r.expect || 'NA') +
        '\nproof: ' + (r.proof || 'NA') +
        '\ngot: ' + (r.last_got || 'NA');
      return '<tr class="border-b border-line align-top">' +
        '<td class="px-2 py-1 mono text-[11px] font-semibold">' +
        esc(r.step_no) + '</td>' +
        '<td class="px-2 py-1 text-[11px]" title="' + esc(tip) + '">' +
        '<span class="mono">' + esc(r.instruction || 'NA') + '</span>' +
        '<div class="text-[10px] text-muted">' +
        esc(r.step_key || '') + '</div></td>' +
        '<td class="px-2 py-1 text-[11px]">' +
        '<span class="rounded-full border px-1.5 py-0.5 text-[10px] ' +
        methodCls(r.method) + '">' + esc(r.method || 'NA') + '</span>' +
        '</td>' +
        '<td class="px-2 py-1 text-[11px]">' + imgCell +
        '<div class="mt-0.5"><span class="rounded-full px-1.5 py-0.5 ' +
        'text-[10px] ' + statusCls(st) + '">' + esc(st) + '</span></div>' +
        // THE `got` IS CAPPED, so one long sentence cannot stretch the table.
        // The full text is in the row's hover title.
        '<div class="mt-0.5 max-w-[22rem] text-[10px] text-muted ' +
        'break-all">' + esc(r.last_got || '') + '</div></td>' +
        '</tr>';
    }).join('') + '</tbody></table></div>';
}

// THE POP-UP BODY: the heading line + the table.
//
// `environmentId` is the key the endpoint is queried by; `payload` is its JSON.
export function stepEvidenceBody(environmentId, payload) {
  const sd = payload || {};
  const stRows = sd.rows || [];
  return '<div class="mt-3 rounded-lg border border-line p-3">' +
    '<div class="text-xs font-semibold">environment_id=' +
    esc(environmentId) + ' → Evidence (STEP)</div>' +
    '<p class="mt-0.5 text-[11px] text-muted">How this environment is ' +
    'processed, step by step, with each step\'s own proof. Source: ' +
    '<span class="mono">playwright_step</span> + ' +
    '<span class="mono">playwright_step_run</span>' +
    (sd.playwright_name
      ? ' · <span class="mono">' + esc(sd.playwright_name) + '</span>'
      : '') +
    (stRows.length ? ' · ' + esc(stRows.length) + ' step(s)' : '') +
    '</p>' +
    (stRows.length
      ? stepEvidenceTable(stRows)
      : '<p class="mt-1 text-[11px] text-muted">' +
        esc(sd.why || 'This environment declares no STEP guide.') + '</p>') +
    '</div>';
}

// THE WHOLE POP-UP: the fixed overlay + the panel + the Close button.
//
// `w-max max-w-[95vw]` is the width contract the human set on 2026-09-26
// ("pop-up width set be all content show without scroll bar").
export function stepEvidencePopupHtml(environmentId, payload, msg) {
  const body = payload
    ? stepEvidenceBody(environmentId, payload)
    : '<p class="mt-2 text-sm text-muted">' + esc(msg || 'Loading…') + '</p>';
  return '<div class="fixed inset-0 z-50 flex items-center justify-center ' +
    'bg-black/40" data-popup-close="1">' +
    '<div class="max-h-[90vh] w-max max-w-[95vw] overflow-auto rounded-xl ' +
    'border border-line bg-panel p-4">' +
    '<div class="flex items-center justify-between">' +
    '<h3 class="text-sm font-semibold">Environment #' + esc(environmentId) +
    ' <span class="text-muted font-normal">→ evidence</span></h3>' +
    '<button data-popup-close="1" class="rounded-lg border border-line ' +
    'px-2 py-0.5 text-xs hover:bg-soft">Close</button>' +
    '</div>' +
    body +
    '</div></div>';
}

// THE ONE FETCH. Both pages call this, so the URL cannot drift between them.
export async function fetchStepEvidence(environmentId) {
  const res = await fetch('/api/environment/evidence_steps?environment_id=' +
    Number(environmentId));
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error(data.error || ('HTTP ' + res.status));
  }
  return data;
}
