import {
  createBlankRecord,
  deleteRecord,
  formatIdentityTrailer,
  listRecords,
  upsertRecord,
} from './storage.js';

const NAV = [
  { id: 'task-center', label: 'Task Center' },
  { id: 'watchdog', label: 'Watchdog' },
  { id: 'task-id-coding', label: 'Task ID Coding' },
  { id: 'skill-ssot', label: 'Skill Prompt SSOT' },
  { id: 'test-lib', label: 'Test Case Library' },
  { id: 'assets', label: 'Asset Registry' },
];

const TABS = [
  { id: 'analyze', label: 'Prompt Analyze' },
  { id: 'chat', label: 'Chat Box' },
  { id: 'result', label: 'Result Output' },
  { id: 'report', label: 'Task Report' },
];

const state = {
  nav: 'task-center',
  tab: 'analyze',
  leftOpen: true,
  rightOpen: true,
  query: '',
  catalogMode: 'server',
  selectedId: null,
  selectedServerId: null,
  draft: createBlankRecord(),
  status: {
    helperOk: false,
    ollamaOk: false,
    modelPresent: false,
    model: '',
    computerId: '',
    computerName: '',
    baseUrl: '',
    text: 'Checking…',
  },
  report: {
    tasks: [],
    totals: { count: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    by_model: [],
    models: [],
  },
  skill: {
    skillKey: 'mouse_spot_verify',
    skillKeys: ['mouse_spot_verify'],
    version: '',
    active: null,
    versions: [],
    latestTest: null,
    casesCount: null,
    cases: [],
    ideTargets: ['Visual Studio Code', 'Cursor', 'Work Buddy', 'Codex'],
    msg: '',
    log: '(Seed / Reload / Test output)',
  },
  watchdog: {
    running: false,
    pid: null,
    helperPid: null,
    lastEvent: null,
    events: [],
    logTail: [],
    selectedId: null,
    msg: '',
  },
  taskId: {
    root: '10',
    tab: 'list', // list | rule | generate | detail
    input: `F:
skill_prompt_load
skill_prompt_render
mouse_spot_verify
skill_prompt_test_100
skill_prompt_promote

A:
GET /api/skills
GET /api/skills/:id
POST /api/skills/:id/test
POST /api/skills/:id/activate
POST /api/analyze

T:
skill_prompt_ssot
skill_prompt_case
skill_prompt_test_run
skill_prompt_inference

D:
skill_key
version_label
prompt_text

J:
skill_prompt_regression_100

E:
skill_prompt_promoted
mouse_spot_verify_done`,
    output: '',
    msg: '',
    seededLines: [],
    records: [],
    selectedId: null,
    selected: null,
    dims: [],
  },
};

const TASK_ID_TABS = [
  { id: 'list', label: 'Task List' },
  { id: 'detail', label: 'Task Detail' },
  { id: 'rule', label: 'Coding Rule' },
  { id: 'generate', label: 'Generate IDs' },
];

function $(sel, root = document) {
  return root.querySelector(sel);
}

function toast(msg) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('opacity-0');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('opacity-0'), 1800);
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&' + 'amp;')
    .replace(/</g, '&' + 'lt;')
    .replace(/>/g, '&' + 'gt;')
    .replace(/"/g, '&' + 'quot;');
}

function fmtDuration(startedAt, endedAt, durationMs) {
  if (durationMs != null && !Number.isNaN(Number(durationMs))) {
    const ms = Number(durationMs);
    if (ms < 1000) return ms + ' ms';
    return (ms / 1000).toFixed(1) + ' s';
  }
  if (!startedAt || !endedAt) return '—';
  try {
    const ms = new Date(endedAt).getTime() - new Date(startedAt).getTime();
    if (Number.isNaN(ms)) return '—';
    if (ms < 1000) return ms + ' ms';
    return (ms / 1000).toFixed(1) + ' s';
  } catch {
    return '—';
  }
}

function filteredLocal() {
  const q = state.query.trim().toLowerCase();
  const all = listRecords();
  if (!q) return all;
  return all.filter((r) =>
    [r.id, r.name, r.task_id, r.writer, r.session_id, r.status, r.prompt_content]
      .join(' ')
      .toLowerCase()
      .includes(q)
  );
}

function filteredServer() {
  const q = state.query.trim().toLowerCase();
  const all = state.report.tasks || [];
  if (!q) return all;
  return all.filter((t) =>
    [t.id, t.task, t.model, t.model_label, t.status, t.result, t.writer, t.task_id, t.session_id, t.reason, t.target_name]
      .join(' ')
      .toLowerCase()
      .includes(q)
  );
}

function readFormIntoDraft() {
  state.draft = {
    ...state.draft,
    task_id: $('#f-task-id')?.value || '',
    writer: $('#f-writer')?.value || '',
    session_id: $('#f-session')?.value || '',
    context: $('#f-context')?.value || '',
    prompt_content: $('#f-prompt')?.value || '',
    result_content: $('#f-result')?.value || '',
    status: $('#f-status')?.value || state.draft.status || 'draft',
  };
}

function fillForm(rec, animate = true) {
  state.draft = createBlankRecord(rec);
  state.selectedId = rec.id;
  state.selectedServerId = null;
  const box = $('#workspace-body');
  if (box && animate) {
    box.classList.remove('fade-swap');
    void box.offsetWidth;
    box.classList.add('fade-swap');
  }
  if ($('#f-task-id')) $('#f-task-id').value = rec.task_id || '';
  if ($('#f-writer')) $('#f-writer').value = rec.writer || '';
  if ($('#f-session')) $('#f-session').value = rec.session_id || '';
  if ($('#f-context')) $('#f-context').value = rec.context || '';
  if ($('#f-prompt')) $('#f-prompt').value = rec.prompt_content || '';
  if ($('#f-result')) $('#f-result').value = rec.result_content || '';
  if ($('#f-status')) $('#f-status').value = rec.status || 'draft';
  renderRightList();
  renderStatusPills();
}

function loadServerTaskIntoWorkspace(task) {
  const rec = createBlankRecord({
    id: state.draft.id || undefined,
    task_id: String(task.task_id || task.id || ''),
    writer: task.writer || '',
    session_id: task.session_id || '',
    context: [task.task, task.target_name, task.model_label || task.model].filter(Boolean).join(' · '),
    prompt_content: state.draft.prompt_content || '',
    result_content: task.reason || task.error || task.result || '',
    status: String(task.status || task.result || 'done').toLowerCase(),
    name: task.task || task.id || 'Server task',
  });
  state.selectedServerId = task.id;
  state.selectedId = null;
  state.catalogMode = 'server';
  state.tab = 'report';
  state.draft = rec;
  mount(false);
  if ($('#f-result')) $('#f-result').value = rec.result_content || '';
  toast('Loaded ' + task.id);
}

function pill(ok, onLabel, offLabel) {
  if (ok) {
    return '<span class="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-100">' + onLabel + '</span>';
  }
  return '<span class="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700 ring-1 ring-rose-100">' + offLabel + '</span>';
}

function renderStatusPills() {
  const host = $('#status-pills');
  if (!host) return;
  const s = state.status;
  const wd = state.watchdog || {};
  const last = wd.lastEvent || {};
  const alertish = String(last.level || '') === 'alert' || String(last.kind || '').includes('down') || String(last.kind || '').includes('failed');
  host.innerHTML = [
    pill(s.helperOk, 'mouse_spot_helper ON', 'mouse_spot_helper OFF'),
    pill(!!wd.running, 'Watchdog ON', 'Watchdog OFF'),
    pill(s.ollamaOk, 'LLM ON', 'LLM OFF'),
    s.modelPresent
      ? '<span class="inline-flex items-center rounded-full bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent">' + esc(s.model || 'model') + '</span>'
      : '<span class="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">Model missing</span>',
    alertish && last.message
      ? '<span class="inline-flex max-w-[280px] items-center truncate rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-100" title="' +
        esc(last.message) +
        '">⚠ ' +
        esc(last.kind || 'alert') +
        '</span>'
      : '',
    s.computerId
      ? '<span class="hidden sm:inline-flex items-center rounded-full bg-soft px-2.5 py-1 text-xs font-medium text-muted">' + esc(s.computerId) + '</span>'
      : '',
  ].join('');
  const line = $('#status-line');
  if (line) line.textContent = s.text;
}

function badge(status) {
  const s = String(status || 'draft').toLowerCase();
  const map = {
    draft: 'bg-soft text-muted',
    ready: 'bg-accent-soft text-accent',
    done: 'bg-emerald-50 text-emerald-700',
    success: 'bg-emerald-50 text-emerald-700',
    error: 'bg-rose-50 text-rose-700',
    fail: 'bg-rose-50 text-rose-700',
    running: 'bg-amber-50 text-amber-700',
  };
  const cls = map[s] || map.draft;
  return '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ' + cls + '">' + esc(status || 'draft') + '</span>';
}

function renderRightList() {
  const host = $('#record-list');
  if (!host) return;

  const modeBar =
    '<div class="flex gap-1 border-b border-line p-2">' +
    '<button type="button" data-catalog="server" class="flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition ' +
    (state.catalogMode === 'server' ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
    '">Server tasks</button>' +
    '<button type="button" data-catalog="local" class="flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition ' +
    (state.catalogMode === 'local' ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
    '">Local drafts</button></div>';

  if (state.catalogMode === 'local') {
    const rows = filteredLocal();
    host.innerHTML =
      modeBar +
      (rows.length
        ? rows
            .map((r) => {
              const active = r.id === state.selectedId;
              return (
                '<button type="button" data-local-id="' +
                esc(r.id) +
                '" class="w-full text-left px-3 py-2.5 border-b border-line/80 transition hover:bg-soft/80 ' +
                (active ? 'bg-accent-soft border-l-2 border-l-accent' : 'border-l-2 border-l-transparent') +
                '"><div class="flex items-center justify-between gap-2"><span class="text-[11px] mono text-muted truncate">' +
                esc(r.id) +
                '</span>' +
                badge(r.status) +
                '</div><div class="mt-0.5 text-sm font-medium text-ink truncate">' +
                esc(r.name || r.task_id || 'Untitled') +
                '</div></button>'
              );
            })
            .join('')
        : '<div class="p-4 text-sm text-muted">No local drafts. Click <b>New</b>.</div>');
    return;
  }

  const rows = filteredServer();
  host.innerHTML =
    modeBar +
    (rows.length
      ? rows
          .map((t) => {
            const active = t.id === state.selectedServerId;
            const title = t.task || t.model_label || t.id;
            const sub = [t.model_label || t.model, t.writer, t.task_id].filter(Boolean).join(' · ');
            return (
              '<button type="button" data-server-id="' +
              esc(t.id) +
              '" class="w-full text-left px-3 py-2.5 border-b border-line/80 transition hover:bg-soft/80 ' +
              (active ? 'bg-accent-soft border-l-2 border-l-accent' : 'border-l-2 border-l-transparent') +
              '"><div class="flex items-center justify-between gap-2"><span class="text-[11px] mono text-muted truncate">' +
              esc(t.id) +
              '</span>' +
              badge(t.result || t.status) +
              '</div><div class="mt-0.5 text-sm font-medium text-ink truncate">' +
              esc(title) +
              '</div><div class="mt-0.5 text-[11px] text-muted truncate">' +
              esc(sub) +
              '</div><div class="mt-1 flex gap-2 text-[11px] mono text-muted"><span title="Prompt tokens">in ' +
              esc(t.prompt_tokens ?? 0) +
              '</span><span title="Output tokens">out ' +
              esc(t.completion_tokens ?? 0) +
              '</span><span title="Total tokens">Σ ' +
              esc(t.total_tokens ?? 0) +
              '</span></div></button>'
            );
          })
          .join('')
      : '<div class="p-4 text-sm text-muted">No server tasks yet. Run Analyze / Improve first.</div>');
}

function reportHtml() {
  const t = state.report.totals || {};
  const tasks = state.report.tasks || [];
  const byModel = state.report.by_model || [];

  return (
    '<div class="mx-auto flex max-w-5xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-2"><div><h2 class="text-lg font-semibold">Task Report</h2><p class="text-sm text-muted">Live history from helper · Refresh / auto 5s</p></div>' +
    '<button id="btn-clear-server" type="button" class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-100">Clear server history</button></div>' +
    '<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel"><div class="text-xs font-medium text-muted">Tasks</div><div class="mt-1 text-2xl font-semibold text-ink">' +
    esc(t.count || 0) +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel" title="Tokens sent to the model"><div class="text-xs font-medium text-muted">Prompt tok</div><div class="mt-1 text-2xl font-semibold text-ink">' +
    esc(t.prompt_tokens || 0) +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel" title="Tokens generated by the model"><div class="text-xs font-medium text-muted">Out tok</div><div class="mt-1 text-2xl font-semibold text-ink">' +
    esc(t.completion_tokens || 0) +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel" title="Prompt + Output"><div class="text-xs font-medium text-muted">Total tok</div><div class="mt-1 text-2xl font-semibold text-accent">' +
    esc(t.total_tokens || 0) +
    '</div></div></div>' +
    '<div class="grid gap-3 sm:grid-cols-2">' +
    (byModel
      .map(
        (m) =>
          '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel"><div class="font-medium text-ink">' +
          esc(m.label || m.model) +
          '</div><div class="mt-1 text-xs text-muted mono">' +
          esc(m.model) +
          '</div><div class="mt-2 text-sm text-muted">tasks ' +
          esc(m.count || 0) +
          ' · Σ ' +
          esc(m.total_tokens || 0) +
          '</div><div class="text-xs text-muted">in ' +
          esc(m.prompt_tokens || 0) +
          ' / out ' +
          esc(m.completion_tokens || 0) +
          '</div></div>'
      )
      .join('') ||
      '<div class="rounded-2xl border border-line bg-panel p-4 text-sm text-muted shadow-panel">No model stats yet.</div>') +
    '</div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"><div class="overflow-auto"><table class="min-w-full text-left text-sm"><thead class="bg-soft/80 text-xs uppercase tracking-wide text-muted"><tr>' +
    '<th class="px-3 py-2 font-semibold">Duration</th><th class="px-3 py-2 font-semibold">Model</th><th class="px-3 py-2 font-semibold">Task</th><th class="px-3 py-2 font-semibold">Status</th><th class="px-3 py-2 font-semibold">Result</th>' +
    '<th class="px-3 py-2 font-semibold" title="Prompt tokens">Prompt</th><th class="px-3 py-2 font-semibold" title="Output tokens">Out</th><th class="px-3 py-2 font-semibold" title="Total tokens">Total</th><th class="px-3 py-2 font-semibold">Detail</th>' +
    '</tr></thead><tbody>' +
    (tasks.length
      ? tasks
          .map((row) => {
            const active = row.id === state.selectedServerId;
            return (
              '<tr data-report-id="' +
              esc(row.id) +
              '" class="border-t border-line cursor-pointer transition hover:bg-soft/70 ' +
              (active ? 'bg-accent-soft/60' : '') +
              '"><td class="px-3 py-2 mono text-xs">' +
              esc(fmtDuration(row.started_at, row.ended_at, row.duration_ms)) +
              '</td><td class="px-3 py-2">' +
              esc(row.model_label || row.model || '—') +
              '</td><td class="px-3 py-2">' +
              esc(row.task || '—') +
              '</td><td class="px-3 py-2">' +
              badge(row.status) +
              '</td><td class="px-3 py-2">' +
              badge(row.result || '—') +
              '</td><td class="px-3 py-2 mono text-xs" title="Prompt tokens: sent to model">' +
              esc(row.prompt_tokens ?? 0) +
              '</td><td class="px-3 py-2 mono text-xs" title="Output tokens: model reply">' +
              esc(row.completion_tokens ?? 0) +
              '</td><td class="px-3 py-2 mono text-xs font-semibold" title="Total = Prompt + Out">' +
              esc(row.total_tokens ?? 0) +
              '</td><td class="px-3 py-2 max-w-[220px] truncate text-xs text-muted" title="' +
              esc(row.reason || row.error || '') +
              '">' +
              esc(row.reason || row.error || '—') +
              '</td></tr>'
            );
          })
          .join('')
      : '<tr><td colspan="9" class="px-3 py-8 text-center text-muted">No LLM tasks yet.</td></tr>') +
    '</tbody></table></div></div></div>'
  );
}

function skillHtml() {
  const s = state.skill;
  const a = s.active || {};
  const verOpts =
    '<option value="">active</option>' +
    (s.versions || [])
      .map((v) => {
        const lab = v.version_label || '';
        const sel = lab && lab === s.version ? ' selected' : '';
        return (
          '<option value="' +
          esc(lab) +
          '"' +
          sel +
          '>' +
          esc(lab) +
          (v.status ? ' · ' + esc(v.status) : '') +
          '</option>'
        );
      })
      .join('');
  const gate = s.latestTest
    ? s.latestTest.pass_gate
      ? 'PASS'
      : 'FAIL'
    : '—';
  const acc =
    s.latestTest && s.latestTest.accuracy_pct != null
      ? String(s.latestTest.accuracy_pct) + '%'
      : '—';
  return (
    '<div class="mx-auto flex max-w-4xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="flex flex-wrap items-start justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold">Skill Prompt SSOT</h2>' +
    '<p class="mt-1 text-sm text-muted">Versioned verify prompt · gold cases · Task Center 10.x · improve→draft only</p></div>' +
    '<div class="flex flex-wrap gap-2 text-xs">' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">' +
    esc(a.skill_key || s.skillKey) +
    '</span>' +
    '<span class="rounded-full bg-accent-soft px-2.5 py-1 text-accent mono">' +
    esc(a.version_label || '—') +
    '</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1">' +
    esc(a.status || '—') +
    '</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1">parser ' +
    esc(a.parser || 'result_yes_no') +
    '</span>' +
    (s.casesCount != null
      ? '<span class="rounded-full bg-soft px-2.5 py-1">gold ' + esc(s.casesCount) + '</span>'
      : '') +
    '<span class="rounded-full ' +
    (gate === 'PASS' ? 'bg-emerald-50 text-emerald-700' : gate === 'FAIL' ? 'bg-rose-50 text-rose-700' : 'bg-soft') +
    ' px-2.5 py-1">gate ' +
    esc(gate) +
    ' · acc ' +
    esc(acc) +
    '</span></div></div>' +
    '<div class="mt-4 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">Skill<select id="sk-pick" class="ml-1 min-w-[160px] rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    (function(){const keys=(state.skill.skillKeys&&state.skill.skillKeys.length)?state.skill.skillKeys:['mouse_spot_verify'];const cur=state.skill.skillKey||'mouse_spot_verify';return keys.map(k=>'<option value="'+esc(k)+'"'+(k===cur?' selected':'')+'>'+esc(k)+'</option>').join('');})() +
    '</select></label>' +
    '<button id="sk-new-skill" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="Create new skill template">+ New template</button>' +
    '<button id="sk-clone" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="New draft from template">From template</button>' +
    '<label class="text-xs font-medium text-muted">Version<select id="sk-ver" class="ml-1 min-w-[140px] rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    verOpts +
    '</select></label>' +
    '<button id="sk-reload" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Reload</button>' +
    '<button id="sk-seed" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Seed v1</button>' +
    '<button id="sk-seed-all" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="prompt + gold + Task Center">Seed all</button>' +
    '<button id="sk-task-lines" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Task IDs</button>' +
    '</div>' +
    '<label class="mt-4 block text-xs font-medium text-muted">Active / draft prompt template' +
    '<textarea id="sk-prompt" rows="10" class="mono mt-1 w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed outline-none focus:ring-2 focus:ring-accent" placeholder="{{target_name}} {{target_action}}">' +
    esc(a.prompt_text || '') +
    '</textarea></label>' +
    '<div class="mt-3 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">target<input id="sk-target" value="Visual Studio Code" class="ml-1 w-40 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm" /></label>' +
    '<label class="text-xs font-medium text-muted">action<input id="sk-action" value="" placeholder="optional" class="ml-1 w-28 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm" /></label>' +
    '<label class="text-xs font-medium text-muted">expected<select id="sk-expected" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm"><option value="NO" selected>NO</option><option value="YES">YES</option></select></label>' +
    '<label class="text-xs font-medium text-muted">runs<select id="sk-runs" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm"><option value="1">1</option><option value="5">5</option><option value="10" selected>10</option><option value="20">20</option><option value="100">100</option></select></label>' +
    '</div>' +
    '<div class="mt-3 flex flex-wrap gap-2">' +
    '<button id="sk-save-draft" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-600">Save draft</button>' +
    '<button id="sk-improve-draft" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="LLM improve → draft only">Improve→draft</button>' +
    '<button id="sk-test" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Run proof test</button>' +
    '<button id="sk-test-gold" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Test gold</button>' +
    '<button id="sk-promote" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Promote active</button>' +
    '<button id="sk-to-chat" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">To chat box</button>' +
    '<button id="sk-from-chat" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">From chat</button>' +
    '</div>' +
    '<div class="mt-3 rounded-xl border border-dashed border-line bg-soft/40 p-3">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Catalog case (IDE targets)</div>' +
    '<div class="flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">target<select id="sk-case-target" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    (state.skill.ideTargets||['Visual Studio Code','Cursor','Work Buddy','Codex']).map(function(n){return '<option value="'+esc(n)+'">'+esc(n)+'</option>';}).join('') +
    '</select></label>' +
    '<label class="text-xs font-medium text-muted">expected<select id="sk-case-expected" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm"><option value="NO" selected>NO</option><option value="YES">YES</option></select></label>' +
    '<button id="sk-add-case" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-600">+ Catalog case</button>' +
    '</div></div>' +
    '<div id="sk-msg" class="mt-2 text-sm text-muted">' +
    esc(s.msg || '') +
    '</div>' +
    '<pre id="sk-log" class="mono mt-3 max-h-64 overflow-auto rounded-xl border border-line bg-soft/80 p-3 text-xs leading-relaxed whitespace-pre-wrap">' +
    esc(s.log || '') +
    '</pre></div></div>'
  );
}

function setSkillMsg(msg, isErr) {
  state.skill.msg = msg || '';
  const el = $('#sk-msg');
  if (el) {
    el.textContent = msg || '';
    el.className = 'mt-2 text-sm ' + (isErr ? 'text-rose-700' : 'text-muted');
  }
  if (msg) toast(msg);
}

function setSkillLog(obj) {
  const text = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  state.skill.log = text;
  const el = $('#sk-log');
  if (el) el.textContent = text;
}

async function loadSkillPanel() {
  const skill = $('#sk-pick')?.value || state.skill.skillKey || 'mouse_spot_verify';
  const version = $('#sk-ver')?.value || state.skill.version || '';
  state.skill.skillKey = skill;
  state.skill.version = version;
  try {
    const q = version ? '?version=' + encodeURIComponent(version) : '';
    const res = await fetch('/api/skills/' + encodeURIComponent(skill) + q);
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'load failed');
    state.skill.active = data.active || null;
    state.skill.versions = data.versions || [];
    state.skill.latestTest = data.latest_test || null;
    try {
      const lr = await fetch('/api/skills');
      const ld = await lr.json();
      if (lr.ok && ld.ok) {
        const keys = ld.skill_keys || [];
        const fromRows = (ld.skills || []).map((r) => r.skill_key).filter(Boolean);
        state.skill.skillKeys = Array.from(new Set([...(keys || []), ...fromRows, skill]));
        if (ld.ide_targets && ld.ide_targets.length) state.skill.ideTargets = ld.ide_targets;
        const pick = $('#sk-pick');
        if (pick) {
          const cur = skill;
          pick.innerHTML = state.skill.skillKeys
            .map(
              (k) =>
                '<option value="' +
                esc(k) +
                '"' +
                (k === cur ? ' selected' : '') +
                '>' +
                esc(k) +
                '</option>'
            )
            .join('');
        }
      }
    } catch (_) {}
    if ($('#sk-prompt') && data.active && data.active.prompt_text != null) {
      $('#sk-prompt').value = data.active.prompt_text;
    }
    try {
      const cr = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/cases'
      );
      const cd = await cr.json();
      if (cr.ok && cd.ok) state.skill.casesCount = cd.count;
    } catch {
      /* ignore */
    }
    setSkillMsg(
      'Loaded ' + ((data.active && data.active.version_label) || skill)
    );
    if (data.latest_test) setSkillLog(data.latest_test);
    // refresh version select without full remount when possible
    const ver = $('#sk-ver');
    if (ver) {
      const cur = version;
      ver.innerHTML =
        '<option value="">active</option>' +
        (state.skill.versions || [])
          .map((v) => {
            const lab = v.version_label || '';
            return (
              '<option value="' +
              esc(lab) +
              '"' +
              (lab === cur ? ' selected' : '') +
              '>' +
              esc(lab) +
              (v.status ? ' · ' + esc(v.status) : '') +
              '</option>'
            );
          })
          .join('');
    }
  } catch (e) {
    setSkillMsg(String(e.message || e), true);
  }
}

function bindSkillPanel() {
  $('#sk-reload')?.addEventListener('click', () => loadSkillPanel());
  $('#sk-pick')?.addEventListener('change', () => {
    state.skill.version = '';
    if ($('#sk-ver')) $('#sk-ver').value = '';
    loadSkillPanel();
  });
  $('#sk-ver')?.addEventListener('change', () => {
    state.skill.version = $('#sk-ver')?.value || '';
    loadSkillPanel();
  });

  $('#sk-seed')?.addEventListener('click', async () => {
    setSkillMsg('Seeding…');
    try {
      const res = await fetch('/api/skills/seed', { method: 'POST' });
      const data = await res.json();
      if (!res.ok && !data.ok && !data.seeded) throw new Error(data.error || 'seed failed');
      setSkillLog(data);
      setSkillMsg('Seeded v1_strict');
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-seed-all')?.addEventListener('click', async () => {
    setSkillMsg('Seeding all (prompt + gold + Task Center)…');
    try {
      const res = await fetch('/api/skills/seed-all', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'seed-all failed');
      setSkillLog(data);
      const nGold = (data.gold && data.gold.seeded) || 0;
      setSkillMsg('Seed all ok · gold ' + nGold);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-task-lines')?.addEventListener('click', async () => {
    setSkillMsg('Loading Task IDs…');
    try {
      const res = await fetch('/api/skills/task-lines');
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'task-lines failed');
      setSkillLog('Root ' + (data.root || 10) + '\n' + (data.lines || []).join('\n'));
      setSkillMsg('Task IDs · ' + (data.lines || []).length + ' items');
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-save-draft')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const prompt_text = $('#sk-prompt')?.value || '';
    if (!prompt_text.trim()) return setSkillMsg('Prompt empty', true);
    let version_label = $('#sk-ver')?.value || '';
    const activeLab = state.skill.active && state.skill.active.version_label;
    if (!version_label || version_label === activeLab) {
      version_label = 'draft_' + new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
    }
    setSkillMsg('Saving ' + version_label + '…');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt_text,
          version_label,
          parser: 'result_yes_no',
          source: 'llm_tasks_spa',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'save failed');
      setSkillLog(data.skill || data);
      state.skill.version = version_label;
      setSkillMsg('Saved draft ' + version_label);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-improve-draft')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const prompt_text = ($('#sk-prompt')?.value || $('#f-prompt')?.value || '').trim();
    const btn = $('#sk-improve-draft');
    if (btn) btn.disabled = true;
    setSkillMsg('Improving → draft only (never auto-promote)…');
    try {
      const body = { source: 'llm_tasks_spa' };
      if (prompt_text) body.prompt_text = prompt_text;
      const res = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/improve-draft',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      );
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'improve failed');
      setSkillLog(data);
      const ver = data.version_label || (data.row && data.row.version_label) || '';
      if (data.row && data.row.prompt_text && $('#sk-prompt')) {
        $('#sk-prompt').value = data.row.prompt_text;
      }
      if (ver) state.skill.version = ver;
      setSkillMsg('Draft saved ' + ver + ' · promote still gated');
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $('#sk-test')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label) ||
      null;
    const runs = parseInt($('#sk-runs')?.value || '10', 10);
    const expected = $('#sk-expected')?.value || 'NO';
    const target_name = $('#sk-target')?.value || 'Visual Studio Code';
    const target_action = $('#sk-action')?.value || '';
    const btn = $('#sk-test');
    if (btn) btn.disabled = true;
    setSkillMsg('Running ' + runs + '-time proof test…');
    setSkillLog('Testing…');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version_label: version,
          runs,
          expected,
          target_name,
          target_action,
        }),
      });
      const data = await res.json();
      if (!res.ok && data.yes == null) throw new Error(data.error || 'test failed');
      setSkillLog(data);
      setSkillMsg(
        'Done · Yes ' +
          data.yes +
          ' · No ' +
          data.no +
          ' · acc ' +
          data.accuracy_pct +
          '% · gate ' +
          (data.pass_gate ? 'PASS' : 'FAIL'),
        !data.pass_gate
      );
      await refreshAll();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $('#sk-test-gold')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label) ||
      null;
    const runs = parseInt($('#sk-runs')?.value || '1', 10);
    const btn = $('#sk-test-gold');
    if (btn) btn.disabled = true;
    setSkillMsg('Running gold suite…');
    try {
      const res = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/test-gold',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version_label: version, runs_per_case: runs }),
        }
      );
      const data = await res.json();
      if (!res.ok && data.ok == null) throw new Error(data.error || 'test-gold failed');
      setSkillLog(data);
      setSkillMsg(
        'Gold · pass ' +
          (data.cases_passed || 0) +
          '/' +
          (data.cases_total || 0),
        !data.ok
      );
      await refreshAll();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $('#sk-promote')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label);
    if (!version) return setSkillMsg('Pick a version to promote', true);
    if (!confirm('Promote ' + version + ' to active?\nRequires pass_gate unless you force after.')) return;
    setSkillMsg('Promoting ' + version + '…');
    try {
      let res = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/activate',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version_label: version, require_pass_gate: true }),
        }
      );
      let data = await res.json();
      if (!res.ok || !data.ok) {
        if (!confirm((data.error || 'blocked') + '\n\nForce promote anyway?')) {
          setSkillMsg(data.error || 'blocked', true);
          return;
        }
        res = await fetch(
          '/api/skills/' + encodeURIComponent(skill) + '/activate',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              version_label: version,
              force: true,
              require_pass_gate: false,
            }),
          }
        );
        data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'force failed');
      }
      setSkillLog(data.active || data);
      state.skill.version = '';
      setSkillMsg('Active → ' + version);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-new-skill')?.addEventListener('click', async () => {
    const name = prompt('New skill_key (letters/digits/underscore):', 'ide_spot_verify');
    if (!name) return;
    const from = $('#sk-pick')?.value || 'mouse_spot_verify';
    setSkillMsg('Creating ' + name + '…');
    try {
      const res = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_key: name.trim(),
          from_skill: from,
          version_label: 'v1_draft',
          source: 'llm_tasks_spa',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'create failed');
      setSkillLog(data);
      state.skill.skillKey = name.trim();
      if (!state.skill.skillKeys.includes(name.trim())) state.skill.skillKeys.push(name.trim());
      setSkillMsg('Created template ' + name.trim());
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-clone')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const from_version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label) ||
      '';
    const version_label =
      'draft_' + new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
    setSkillMsg('Cloning ' + (from_version || 'active') + ' → ' + version_label + '…');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version_label,
          from_version: from_version || undefined,
          prompt_text: $('#sk-prompt')?.value || undefined,
          source: 'llm_tasks_spa_clone',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'clone failed');
      setSkillLog(data.skill || data);
      state.skill.version = version_label;
      setSkillMsg('Draft from template · ' + version_label);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-add-case')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const target_name = $('#sk-case-target')?.value || 'Visual Studio Code';
    const expected = $('#sk-case-expected')?.value || 'NO';
    setSkillMsg('Adding catalog case ' + target_name + '…');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_name,
          expected,
          target_action: 'open',
          source: 'llm_tasks_spa',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'case failed');
      setSkillLog(data.case || data);
      setSkillMsg('Catalog case · ' + target_name + ' · ' + expected);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-to-chat')?.addEventListener('click', () => {
    const text = $('#sk-prompt')?.value || '';
    state.draft.prompt_content = text;
    state.nav = 'task-center';
    state.tab = 'chat';
    mount(false);
    toast('Copied skill prompt → chat');
  });

  $('#sk-from-chat')?.addEventListener('click', () => {
    const text = state.draft.prompt_content || $('#f-prompt')?.value || '';
    if ($('#sk-prompt')) $('#sk-prompt').value = text;
    setSkillMsg('Loaded chat → skill editor');
  });
}


const TASK_ID_TYPE_ORDER = ['F', 'A', 'T', 'D', 'J', 'E'];
const TASK_ID_TYPE_LABEL = {
  F: 'Function',
  A: 'API',
  T: 'Table',
  D: 'Field',
  J: 'Job',
  E: 'Event',
};

const TASK_ID_RULE_TEXT =
  'Task ID coding rule\n' +
  'Format: {RootTaskId}.{GlobalSequenceNumber}\n' +
  'RootTaskId = main task number (e.g. 10).\n' +
  'Within one RootTaskId, F/A/T/D/J/E share ONE continuous global sequence.\n' +
  'Sequence never resets when type changes.\n' +
  '\n' +
  'Types: F=Function  A=API  T=Table  D=Field  J=Job  E=Event\n' +
  '\n' +
  'Rules:\n' +
  '1. Do NOT restart sequence when type changes.\n' +
  '2. Each line: {Root}.{seq} + item name.\n' +
  '3. Item type is metadata only; ID carries root + global seq.\n' +
  '4. Never renumber after delete — keep orphaned/skipped numbers.\n';

function parseGroupedTaskItems(text) {
  const groups = { F: [], A: [], T: [], D: [], J: [], E: [] };
  let cur = null;
  const lines = String(text || '').split(/\r?\n/);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('//')) continue;
    const header = line.match(/^(F|A|T|D|J|E)\s*(?:[=:：\-].*)?$/i);
    if (header) {
      cur = header[1].toUpperCase();
      continue;
    }
    const prefixed = line.match(/^(F|A|T|D|J|E)\s*[:：\-]\s*(.+)$/i);
    if (prefixed) {
      cur = prefixed[1].toUpperCase();
      const name = prefixed[2].trim();
      if (name) groups[cur].push(name);
      continue;
    }
    if (!cur) continue;
    // strip optional already-numbered prefix like 10.3 name
    const name = line.replace(/^\d+\.\d+\s+/, '').trim();
    if (name) groups[cur].push(name);
  }
  return groups;
}

function generateTaskIdList(root, groupedText) {
  const rootId = String(root || '10').trim() || '10';
  const groups = parseGroupedTaskItems(groupedText);
  const items = [];
  let seq = 0;
  const counts = {};
  for (const t of TASK_ID_TYPE_ORDER) {
    const names = groups[t] || [];
    counts[t] = names.length;
    for (const name of names) {
      seq += 1;
      items.push({
        id: rootId + '.' + seq,
        seq,
        type: t,
        type_label: TASK_ID_TYPE_LABEL[t] || t,
        name,
        line: rootId + '.' + seq + '  ' + name,
      });
    }
  }
  return {
    ok: true,
    root: rootId,
    count: items.length,
    counts,
    items,
    lines: items.map((x) => x.line),
    text: items.map((x) => x.line).join('\n'),
    rule: TASK_ID_RULE_TEXT,
  };
}

function taskIdStatusClass(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'pass' || s === 'done' || s === 'success') return 'text-emerald-700 bg-emerald-50';
  if (s === 'fail' || s === 'error') return 'text-rose-700 bg-rose-50';
  if (s === 'running') return 'text-amber-700 bg-amber-50';
  return 'text-slate-600 bg-soft';
}

function taskIdListHtml() {
  const t = state.taskId || {};
  const rows = t.records || [];
  const selected = t.selectedId;
  const body =
    rows.length === 0
      ? '<tr><td colspan="7" class="px-3 py-8 text-center text-sm text-muted">No Task ID records yet. Click <b>Seed Task Center</b> or <b>Refresh list</b>.</td></tr>'
      : rows
          .map((r) => {
            const id = String(r.task_id || '');
            const active = selected && String(selected) === id;
            const capLabel = r.capability || r.cap_id || r.cap_name || '—';
            return (
              '<tr data-tid-row="' +
              esc(id) +
              '" class="cursor-pointer border-t border-line hover:bg-accent-soft/40 ' +
              (active ? 'bg-accent-soft/70' : '') +
              '">' +
              '<td class="whitespace-nowrap px-3 py-2 text-xs text-muted mono">' +
              esc(r.date || '') +
              '</td>' +
              '<td class="whitespace-nowrap px-3 py-2 text-sm font-semibold mono text-accent">' +
              esc(id) +
              '</td>' +
              '<td class="px-3 py-2 text-sm">' +
              esc(r.channel || '') +
              '</td>' +
              '<td class="px-3 py-2 text-sm">' +
              esc(r.module || '') +
              '</td>' +
              '<td class="px-3 py-2 text-sm">' +
              '<div class="font-medium text-ink mono text-xs">' +
              esc(capLabel) +
              '</div>' +
              (r.feature_tag
                ? '<div class="mt-0.5 text-[10px] text-muted mono">' + esc(r.feature_tag) + '</div>'
                : '') +
              '</td>' +
              '<td class="px-3 py-2 text-sm">' +
              esc(r.task_name || r.title || '') +
              (r.item_type
                ? ' <span class="ml-1 rounded-full bg-soft px-1.5 py-0.5 text-[10px] mono text-muted">' +
                  esc(r.item_type) +
                  '</span>'
                : '') +
              '</td>' +
              '<td class="px-3 py-2"><span class="rounded-full px-2 py-0.5 text-xs ' +
              taskIdStatusClass(r.status) +
              '">' +
              esc(r.status || '') +
              '</span></td></tr>'
            );
          })
          .join('');

  return (
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 flex flex-wrap items-center justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold">Task ID records</h2>' +
    '<p class="text-sm text-muted">Root <span class="mono font-medium text-ink">' +
    esc(t.root || '10') +
    '</span> · Module → <b>Capability</b> → Worker · click row for detail</p></div>' +
    '<div class="flex flex-wrap gap-2">' +
    '<button id="tid-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh list</button>' +
    '<button id="tid-seed-tc" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-600">Seed Task Center</button>' +
    '</div></div>' +
    '<div id="tid-msg" class="mb-2 text-xs text-muted">' +
    esc(t.msg || '') +
    '</div>' +
    '<div class="overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left">' +
    '<thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">' +
    '<tr>' +
    '<th class="px-3 py-2 font-semibold">Date</th>' +
    '<th class="px-3 py-2 font-semibold">Task ID</th>' +
    '<th class="px-3 py-2 font-semibold">channel</th>' +
    '<th class="px-3 py-2 font-semibold">module</th>' +
    '<th class="px-3 py-2 font-semibold">Capability</th>' +
    '<th class="px-3 py-2 font-semibold">task name</th>' +
    '<th class="px-3 py-2 font-semibold">status</th>' +
    '</tr></thead><tbody id="tid-table-body">' +
    body +
    '</tbody></table></div></div>'
  );
}

function fieldCard(label, value) {
  return (
    '<div class="rounded-xl border border-line bg-soft/40 p-3">' +
    '<div class="text-[11px] uppercase tracking-wide text-muted">' +
    esc(label) +
    '</div>' +
    '<div class="mt-1 break-all text-sm font-medium text-ink mono">' +
    esc(value) +
    '</div></div>'
  );
}

function taskIdWorkersHtml(workers) {
  const list = Array.isArray(workers) ? workers : [];
  if (!list.length) {
    return (
      '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="text-sm font-semibold">Workers (executors under Capability)</h3>' +
      '<p class="mt-2 text-sm text-muted">No worker catalog rows for this capability yet. Capability defines the goal; Workers implement it with fallback_order.</p></div>'
    );
  }
  const rows = list
    .slice()
    .sort((a, b) => Number(a.fallback_order || 0) - Number(b.fallback_order || 0))
    .map(
      (w) =>
        '<tr class="border-t border-line">' +
        '<td class="px-3 py-1.5 mono text-xs font-medium">' +
        esc(w.worker_id || '') +
        '</td>' +
        '<td class="px-3 py-1.5 text-xs mono">' +
        esc(w.worker_type || '') +
        '</td>' +
        '<td class="px-3 py-1.5 text-sm">' +
        esc(w.description || '') +
        '</td>' +
        '<td class="px-3 py-1.5 text-xs text-center mono">' +
        esc(w.fallback_order ?? '') +
        '</td>' +
        '<td class="px-3 py-1.5"><span class="rounded-full px-2 py-0.5 text-xs ' +
        taskIdStatusClass(w.status) +
        '">' +
        esc(w.status || '') +
        '</span></td>' +
        '<td class="px-3 py-1.5 text-xs mono break-all">' +
        esc(w.physical_file_path || '') +
        '</td></tr>'
    )
    .join('');
  return (
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="text-sm font-semibold">Workers (executors under Capability)</h3>' +
    '<p class="mt-1 text-xs text-muted">Capability = goal/spec · Worker = how to achieve it · try by fallback_order</p>' +
    '<div class="mt-2 overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">worker_id</th><th class="px-3 py-2">type</th><th class="px-3 py-2">description</th>' +
    '<th class="px-3 py-2">order</th><th class="px-3 py-2">status</th><th class="px-3 py-2">path</th>' +
    '</tr></thead><tbody>' +
    rows +
    '</tbody></table></div></div>'
  );
}

function taskIdDetailHtml() {
  const t = state.taskId || {};
  const r = t.selected;
  if (!r) {
    return (
      '<div class="rounded-2xl border border-line bg-panel p-8 shadow-panel text-center">' +
      '<h2 class="text-lg font-semibold">Task Detail</h2>' +
      '<p class="mt-2 text-sm text-muted">Click a row in <b>Task List</b> to inspect one Task ID.</p>' +
      '<button id="tid-goto-list" type="button" class="mt-4 rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Back to Task List</button>' +
      '</div>'
    );
  }
  const dims = t.dims || [];
  const dimRows =
    dims.length === 0
      ? '<tr><td colspan="4" class="px-3 py-4 text-sm text-muted">No task_ssot dims</td></tr>'
      : dims
          .map(
            (d) =>
              '<tr class="border-t border-line">' +
              '<td class="px-3 py-1.5 mono text-xs">' +
              esc(d.dim_key) +
              '</td>' +
              '<td class="px-3 py-1.5 text-sm break-all">' +
              esc(d.value_text) +
              '</td>' +
              '<td class="px-3 py-1.5 text-xs text-muted">' +
              esc(d.value_type || '') +
              '</td>' +
              '<td class="px-3 py-1.5 text-xs text-muted">' +
              esc(d.source || '') +
              '</td></tr>'
          )
          .join('');

  return (
    '<div class="mx-auto flex max-w-4xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="flex flex-wrap items-start justify-between gap-2">' +
    '<div><div class="text-xs uppercase tracking-wide text-muted">Task ID</div>' +
    '<h2 class="text-xl font-semibold mono text-accent">' +
    esc(r.task_id) +
    '</h2>' +
    '<p class="mt-1 text-sm text-ink">' +
    esc(r.title || r.task_name || '') +
    '</p></div>' +
    '<div class="flex flex-wrap gap-2">' +
    '<button id="tid-goto-list" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Back</button>' +
    '<button id="tid-reload-one" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Reload</button>' +
    '</div></div>' +
    '<div class="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 text-sm">' +
    fieldCard('Date', r.date || r.updated_at || '') +
    fieldCard('Task ID', r.task_id || '') +
    fieldCard('Status', r.status || '') +
    fieldCard('channel', r.channel || r.channel_code || '') +
    fieldCard('module', r.module || r.module_code || '') +
    fieldCard('Capability', r.capability || r.cap_id || '') +
    fieldCard('cap_id', r.cap_id || '') +
    fieldCard('feature_tag', r.feature_tag || '') +
    fieldCard('task name', r.task_name || '') +
    fieldCard('item type', r.item_type || '') +
    fieldCard('db id', r.db_id || '') +
    fieldCard('parent', r.parent_task_id || '—') +
    '</div>' +
    (r.cap_description
      ? '<p class="mt-3 text-sm text-muted">' + esc(r.cap_description) + '</p>'
      : '') +
    '</div>' +
    taskIdWorkersHtml(r.workers || []) +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="text-sm font-semibold">Payload</h3>' +
    '<pre class="mono mt-2 max-h-56 overflow-auto rounded-xl border border-line bg-soft/60 p-3 text-xs whitespace-pre-wrap">' +
    esc(JSON.stringify(r.payload || {}, null, 2)) +
    '</pre></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="text-sm font-semibold">task_ssot dimensions</h3>' +
    '<div class="mt-2 overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-3 py-2">dim_key</th><th class="px-3 py-2">value</th><th class="px-3 py-2">type</th><th class="px-3 py-2">source</th>' +
    '</tr></thead><tbody>' +
    dimRows +
    '</tbody></table></div></div></div>'
  );
}

function taskIdRuleHtml() {
  return (
    '<div class="mx-auto max-w-3xl rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h2 class="text-lg font-semibold">Task ID Coding Rule</h2>' +
    '<p class="mt-1 text-sm text-muted">Format: <span class="mono font-semibold text-ink">{RootTaskId}.{GlobalSequenceNumber}</span></p>' +
    '<div class="mt-3 flex flex-wrap gap-2 text-xs">' +
    '<span class="rounded-full bg-accent-soft px-2.5 py-1 text-accent mono">F Function</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">A API</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">T Table</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">D Field</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">J Job</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">E Event</span></div>' +
    '<ol class="mt-4 list-decimal space-y-1 pl-5 text-sm text-muted">' +
    '<li>Do <b>not</b> restart sequence when type changes.</li>' +
    '<li>Each line: <span class="mono">{Root}.{seq}</span> + item name.</li>' +
    '<li>Type is metadata only; ID carries root + global seq.</li>' +
    '<li>Assigned IDs are stable — deletes leave gaps, no renumber.</li></ol>' +
    '<pre class="mono mt-4 max-h-80 overflow-auto rounded-xl border border-line bg-canvas p-3 text-xs whitespace-pre-wrap">' +
    esc(TASK_ID_RULE_TEXT) +
    '</pre></div>'
  );
}

function taskIdGenerateHtml() {
  const t = state.taskId || {};
  const out = t.output || '(Generate to see {Root}.{seq} list)';
  return (
    '<div class="mx-auto flex max-w-5xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h2 class="text-lg font-semibold">Generate Task IDs</h2>' +
    '<p class="mt-1 text-sm text-muted">Paste grouped names → continuous <span class="mono">{Root}.{seq}</span> list</p>' +
    '<div class="mt-4 grid gap-4 lg:grid-cols-2">' +
    '<div>' +
    '<div class="mb-2 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">RootTaskId' +
    '<input id="tid-root" value="' +
    esc(t.root || '10') +
    '" class="ml-1 w-24 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm mono" /></label>' +
    '<button id="tid-generate" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-600">Generate list</button>' +
    '<button id="tid-copy" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Copy output</button>' +
    '</div>' +
    '<label class="block text-xs font-medium text-muted">Grouped input (by type)' +
    '<textarea id="tid-input" rows="16" class="mono mt-1 w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed outline-none focus:ring-2 focus:ring-accent">' +
    esc(t.input || '') +
    '</textarea></label>' +
    '<p class="mt-1 text-[11px] text-muted">Headers: F: A: T: D: J: E: then one name per line.</p></div>' +
    '<div><div class="mb-2 flex items-center justify-between"><label class="text-xs font-medium text-muted">Numbered output</label>' +
    '<span id="tid-msg" class="text-xs text-muted">' +
    esc(t.msg || '') +
    '</span></div>' +
    '<textarea id="tid-output" rows="18" readonly class="mono w-full resize-y rounded-xl border border-line bg-soft/80 px-3 py-2 text-sm leading-relaxed outline-none">' +
    esc(out) +
    '</textarea></div></div></div></div>'
  );
}

function taskIdCodingHtml() {
  const tab = state.taskId?.tab || 'list';
  if (tab === 'detail') return taskIdDetailHtml();
  if (tab === 'rule') return taskIdRuleHtml();
  if (tab === 'generate') return taskIdGenerateHtml();
  return taskIdListHtml();
}

function setTaskIdMsg(msg, isErr, silent) {
  state.taskId.msg = msg || '';
  const el = $('#tid-msg');
  if (el) {
    el.textContent = msg || '';
    el.className = 'text-xs ' + (isErr ? 'text-rose-700' : 'text-muted');
  }
  if (msg && !silent) toast(msg);
}

async function loadTaskIdRecords(opts = {}) {
  const quiet = !!opts.quiet;
  if (!quiet) setTaskIdMsg('Loading Task ID records…', false, true);
  try {
    const root = state.taskId.root || '10';
    const res = await fetch('/api/skills/task-records?root=' + encodeURIComponent(root));
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'task-records failed');
    state.taskId.records = data.records || [];
    state.taskId.root = String(data.root || root);
    state.taskId.seededLines = (data.records || [])
      .filter((r) => String(r.task_id) !== String(data.root))
      .map((r) => String(r.task_id) + ' ' + (r.task_name || ''));
    state.taskId.output = state.taskId.seededLines.join('\n');
    setTaskIdMsg(
      (data.count || 0) + ' records · root ' + state.taskId.root + ' · agent.db dev_task',
      false,
      true
    );
    if (state.nav === 'task-id-coding' && (state.taskId.tab || 'list') === 'list') {
      const body = $('#workspace-body');
      if (body) {
        body.innerHTML = taskIdListHtml();
        bindTaskIdCodingPanel(false);
      }
    }
    return data;
  } catch (e) {
    setTaskIdMsg(String(e.message || e), true);
    return null;
  }
}

async function loadTaskIdDetail(taskId) {
  if (!taskId) return;
  setTaskIdMsg('Loading ' + taskId + '…', false, true);
  try {
    const res = await fetch(
      '/api/skills/task-records/' +
        encodeURIComponent(taskId) +
        '?root=' +
        encodeURIComponent(state.taskId.root || '10')
    );
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'task detail failed');
    state.taskId.selectedId = String(data.record?.task_id || taskId);
    state.taskId.selected = data.record || null;
    state.taskId.dims = data.dims || [];
    state.taskId.tab = 'detail';
    mount(false);
    toast('Opened ' + state.taskId.selectedId);
  } catch (e) {
    setTaskIdMsg(String(e.message || e), true);
  }
}

function bindTaskIdCodingPanel(autoLoad = true) {
  const syncFields = () => {
    if ($('#tid-root')) state.taskId.root = $('#tid-root').value || '10';
    if ($('#tid-input')) state.taskId.input = $('#tid-input').value || '';
    if ($('#tid-output')) state.taskId.output = $('#tid-output').value || '';
  };

  document.querySelectorAll('[data-tid-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.taskId.tab = btn.getAttribute('data-tid-tab') || 'list';
      mount(false);
    });
  });

  document.querySelectorAll('[data-tid-row]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const id = tr.getAttribute('data-tid-row');
      loadTaskIdDetail(id);
    });
  });

  $('#tid-refresh')?.addEventListener('click', () => loadTaskIdRecords());
  $('#tid-goto-list')?.addEventListener('click', () => {
    state.taskId.tab = 'list';
    mount(false);
  });
  $('#tid-reload-one')?.addEventListener('click', () => {
    if (state.taskId.selectedId) loadTaskIdDetail(state.taskId.selectedId);
  });

  $('#tid-generate')?.addEventListener('click', () => {
    syncFields();
    const result = generateTaskIdList(state.taskId.root, state.taskId.input);
    state.taskId.output = result.text || '';
    if ($('#tid-output')) $('#tid-output').value = state.taskId.output;
    const c = result.counts || {};
    setTaskIdMsg(
      'Root ' +
        result.root +
        ' · ' +
        result.count +
        ' items · F' +
        (c.F || 0) +
        ' A' +
        (c.A || 0) +
        ' T' +
        (c.T || 0) +
        ' D' +
        (c.D || 0) +
        ' J' +
        (c.J || 0) +
        ' E' +
        (c.E || 0)
    );
  });

  $('#tid-copy')?.addEventListener('click', async () => {
    const text = $('#tid-output')?.value || state.taskId.output || '';
    if (!text.trim()) return setTaskIdMsg('Nothing to copy', true);
    try {
      await navigator.clipboard.writeText(text);
      setTaskIdMsg('Copied numbered list');
    } catch {
      setTaskIdMsg('Copy failed', true);
    }
  });

  $('#tid-seed-tc')?.addEventListener('click', async () => {
    setTaskIdMsg('Seeding Task Center root 10…');
    try {
      const res = await fetch('/api/skills/seed-tasks', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'seed-tasks failed');
      setTaskIdMsg(
        'Seeded · created ' +
          (data.created_tasks || 0) +
          ' · updated ' +
          (data.updated_tasks || 0)
      );
      await loadTaskIdRecords({ quiet: true });
    } catch (e) {
      setTaskIdMsg(String(e.message || e), true);
    }
  });

  if (autoLoad && (state.taskId.tab || 'list') === 'list') {
    loadTaskIdRecords({ quiet: true });
  }
}

function levelBadge(level, kind) {
  const lv = String(level || kind || 'info').toLowerCase();
  if (lv === 'alert' || lv.includes('down') || lv.includes('fail')) {
    return '<span class="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700">ALERT</span>';
  }
  if (lv === 'warn' || lv.includes('restart')) {
    return '<span class="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">WARN</span>';
  }
  if (lv === 'ok' || lv.includes('ready') || lv.includes('recover') || lv.includes('up')) {
    return '<span class="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">OK</span>';
  }
  return '<span class="inline-flex items-center rounded-full bg-soft px-2 py-0.5 text-[11px] font-medium text-muted">INFO</span>';
}

function watchdogHtml() {
  const w = state.watchdog || {};
  const events = w.events || [];
  const selected =
    events.find((e) => e.id === w.selectedId) ||
    events[0] ||
    null;
  if (selected && !w.selectedId) state.watchdog.selectedId = selected.id;
  const shot = (selected && selected.screenshot) || {};
  const detail = (selected && selected.detail) || {};
  const logTail = (w.logTail || []).slice().reverse().join('\n') || '(no log yet)';

  const eventRows = events.length
    ? events
        .map((ev) => {
          const active = selected && ev.id === selected.id;
          const hasShot = !!(ev.screenshot && ev.screenshot.ok && ev.screenshot.url);
          return (
            '<button type="button" data-wd-id="' +
            esc(ev.id) +
            '" class="w-full border-b border-line px-3 py-2.5 text-left transition hover:bg-soft/80 ' +
            (active ? 'bg-accent-soft/70' : '') +
            '"><div class="flex items-center justify-between gap-2">' +
            levelBadge(ev.level, ev.kind) +
            '<span class="text-[11px] mono text-muted">' +
            esc(ev.local_time || ev.ts || '') +
            '</span></div><div class="mt-1 text-sm font-medium text-ink">' +
            esc(ev.kind || 'event') +
            (hasShot ? ' · 📷' : '') +
            '</div><div class="mt-0.5 line-clamp-2 text-xs text-muted">' +
            esc(ev.message || '') +
            '</div></button>'
          );
        })
        .join('')
    : '<div class="p-4 text-sm text-muted">No watchdog events yet. Alerts appear when mouse_spot_helper goes down / restarts.</div>';

  const helperPid =
    w.helperPid ||
    (detail && detail.helper_pid) ||
    (selected && selected.detail && selected.detail.helper_pid) ||
    null;
  const st = state.status || {};
  const llmOn = !!st.ollamaOk;
  const modelLabel = st.modelPresent ? st.model || 'model ok' : 'model missing';

  const scopeCard =
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">What watchdog monitors</div>' +
    '<div class="grid gap-3 sm:grid-cols-3">' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">1. mouse_spot_helper</div>' +
    '<div class="mt-1 text-xs text-muted">Primary keep-alive. Probes <span class="mono">:18765</span> every ~15s. If down → restart + alert + screenshot.</div>' +
    '<div class="mt-2">' +
    pill(!!st.helperOk, 'mouse_spot_helper ON', 'mouse_spot_helper OFF') +
    '</div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">2. LLM / Ollama</div>' +
    '<div class="mt-1 text-xs text-muted">Observed only when helper is up (status summary). <strong>Not restarted</strong> by watchdog if LLM is OFF.</div>' +
    '<div class="mt-2 flex flex-wrap gap-1.5">' +
    pill(llmOn, 'LLM ON', 'LLM OFF') +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted">' +
    esc(modelLabel) +
    '</span></div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">3. Process IDs</div>' +
    '<div class="mt-1 space-y-1 text-xs mono text-muted">' +
    '<div><span class="font-semibold text-ink">watchdog pid</span> ' +
    esc(w.pid || '—') +
    ' <span class="text-[11px]">(helper_watchdog.py)</span></div>' +
    '<div><span class="font-semibold text-ink">helper pid</span> ' +
    esc(helperPid || '—') +
    ' <span class="text-[11px]">(mouse_spot_helper.py)</span></div>' +
    '</div></div></div></div>';

  const detailBlock = selected
    ? '<div class="space-y-3">' +
      '<div class="flex flex-wrap items-center gap-2">' +
      levelBadge(selected.level, selected.kind) +
      '<span class="text-sm font-semibold text-ink">' +
      esc(selected.kind || '') +
      '</span><span class="text-xs mono text-muted">' +
      esc(selected.local_time || selected.ts || '') +
      '</span></div>' +
      '<p class="text-sm text-ink">' +
      esc(selected.message || '') +
      '</p>' +
      '<pre class="max-h-40 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs mono text-muted">' +
      esc(JSON.stringify(detail, null, 2)) +
      '</pre>' +
      (shot.ok && shot.url
        ? '<div><div class="mb-1 text-xs font-medium text-muted">Desktop screenshot at alert</div>' +
          '<a href="' +
          esc(shot.url) +
          '" target="_blank" rel="noopener" class="block overflow-hidden rounded-xl border border-line bg-soft">' +
          '<img src="' +
          esc(shot.url) +
          '" alt="watchdog screenshot" class="max-h-[420px] w-full object-contain bg-black/5" />' +
          '</a><div class="mt-1 text-[11px] text-muted mono">' +
          esc(shot.path || shot.url) +
          (shot.bytes ? ' · ' + esc(shot.bytes) + ' bytes' : '') +
          '</div></div>'
        : shot && shot.error
          ? '<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Screenshot failed: ' +
            esc(shot.error) +
            '</div>'
          : '<div class="rounded-xl border border-line bg-soft/60 p-3 text-sm text-muted">No screenshot for this event.</div>') +
      '</div>'
    : '<div class="text-sm text-muted">Select an event to see why the watchdog alerted and the desktop screenshot.</div>';

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold">Watchdog</h2>' +
    '<p class="text-sm text-muted">Keep-alive for <span class="mono">mouse_spot_helper :18765</span> · LLM status is reported, not restarted · screenshot evidence</p></div>' +
    '<div class="flex flex-wrap items-center gap-2">' +
    pill(!!w.running, 'Watchdog ON', 'Watchdog OFF') +
    pill(!!st.helperOk, 'mouse_spot_helper ON', 'mouse_spot_helper OFF') +
    pill(llmOn, 'LLM ON', 'LLM OFF') +
    (w.pid
      ? '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted" title="helper_watchdog.py process id">watchdog pid ' +
        esc(w.pid) +
        '</span>'
      : '') +
    (helperPid
      ? '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted" title="mouse_spot_helper.py process id">helper pid ' +
        esc(helperPid) +
        '</span>'
      : '') +
    '<button id="btn-wd-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div></div>' +
    (w.msg ? '<div class="text-sm text-muted">' + esc(w.msg) + '</div>' : '') +
    scopeCard +
    '<div class="grid gap-4 lg:grid-cols-5">' +
    '<div class="lg:col-span-2 overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">' +
    '<div class="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">Events</div>' +
    '<div id="wd-event-list" class="max-h-[560px] overflow-auto">' +
    eventRows +
    '</div></div>' +
    '<div class="lg:col-span-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Selected alert</div>' +
    detailBlock +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 flex items-center justify-between"><div class="text-xs font-semibold uppercase tracking-wide text-muted">helper_watchdog.log (tail)</div>' +
    '<span class="text-[11px] text-muted">auto-refresh with page</span></div>' +
    '<pre class="max-h-56 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs mono leading-relaxed text-ink">' +
    esc(logTail) +
    '</pre></div></div>'
  );
}

function workspaceHtml() {
  if (state.nav === 'watchdog') return watchdogHtml();
  if (state.nav === 'task-id-coding') return taskIdCodingHtml();
  if (state.nav === 'skill-ssot') return skillHtml();
  if (state.nav !== 'task-center') {
    const label = NAV.find((n) => n.id === state.nav)?.label || state.nav;
    return (
      '<div class="rounded-2xl border border-line bg-panel p-8 shadow-panel"><h2 class="text-lg font-semibold">' +
      esc(label) +
      '</h2><p class="mt-2 text-sm text-muted">Placeholder panel — connect helper APIs in a follow-up.</p></div>'
    );
  }

  if (state.tab === 'report') return reportHtml();

  const tabHint =
    state.tab === 'chat'
      ? 'Chat Box focuses the prompt area for iterative edits.'
      : state.tab === 'result'
        ? 'Result Output shows the latest analysis / improve output.'
        : 'Prompt Analyze holds task metadata and actions.';

  return (
    '<div class="mx-auto flex max-w-4xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-center justify-between gap-2"><p class="text-sm text-muted">' +
    tabHint +
    '</p><div class="flex flex-wrap gap-2">' +
    '<button id="btn-new" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">New</button>' +
    '<button id="btn-save" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-600">Save</button>' +
    '<button id="btn-delete" type="button" class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-100">Delete</button>' +
    '</div></div>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel ' +
    (state.tab === 'result' ? 'hidden' : '') +
    '"><div class="grid gap-3 sm:grid-cols-2">' +
    '<label class="block text-xs font-medium text-muted">task_id<input id="f-task-id" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" /></label>' +
    '<label class="block text-xs font-medium text-muted">writer<input id="f-writer" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" /></label>' +
    '<label class="block text-xs font-medium text-muted">session_id <span class="font-normal text-muted">(IDE session · empty until IDE works)</span><input id="f-session" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" placeholder="No IDE session yet" /></label>' +
    '<label class="block text-xs font-medium text-muted">status<select id="f-status" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent"><option value="draft">draft</option><option value="ready">ready</option><option value="running">running</option><option value="done">done</option><option value="error">error</option></select></label>' +
    '</div><p class="mt-2 text-[11px] text-muted">session_id = VS Code / Cursor / Work Buddy / Codex session. New tasks leave it blank; after IDE work, paste reply and Apply IDs.</p>' +
    '<label class="mt-3 block text-xs font-medium text-muted">context<input id="f-context" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" /></label></section>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel ' +
    (state.tab === 'result' ? 'hidden' : '') +
    '"><div class="mb-2 flex flex-wrap items-center justify-between gap-2"><h3 class="text-sm font-semibold">Prompt</h3><div class="flex flex-wrap items-center gap-2"><label class="text-[11px] text-muted">IDE target<select id="f-ide-target" class="ml-1 rounded-lg border border-line bg-canvas px-2 py-1 text-xs"><option>Visual Studio Code</option><option>Cursor</option><option>Work Buddy</option><option>Codex</option></select></label><span class="text-[11px] text-muted mono">monospace</span></div></div>' +
    '<textarea id="f-prompt" rows="12" placeholder="Paste prompt here…" class="mono w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed outline-none focus:ring-2 focus:ring-accent"></textarea>' +
    '<div class="mt-3 flex flex-wrap gap-2"><button id="btn-from-template" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">From IDE template</button><button id="btn-improve" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Improve</button><button id="btn-analyze" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Analyze</button><button id="btn-apply-ids" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="Parse session_id/task_id/writer from reply">Apply IDs from reply</button><button id="btn-apply" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Apply to chat</button><button id="btn-copy" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Copy result</button></div></section>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel ' +
    (state.tab === 'chat' ? 'hidden' : '') +
    '"><div class="mb-2 flex items-center justify-between"><h3 class="text-sm font-semibold">Result</h3><span class="text-[11px] text-muted">readonly</span></div>' +
    '<textarea id="f-result" rows="10" readonly class="mono w-full resize-y rounded-xl border border-line bg-soft/80 px-3 py-2 text-sm leading-relaxed text-ink outline-none"></textarea></section></div>'
  );
}

function shell() {
  return (
    '<div class="min-h-screen flex flex-col"><header class="sticky top-0 z-20 border-b border-line bg-panel/95 backdrop-blur-sm"><div class="mx-auto flex max-w-[1600px] flex-col gap-2 px-3 py-2.5 sm:px-4">' +
    '<div class="flex items-center gap-3"><button id="btn-left" type="button" class="rounded-lg border border-line px-2.5 py-1.5 text-sm text-muted hover:bg-soft lg:hidden">Menu</button>' +
    '<div class="flex min-w-0 flex-1 items-center gap-3"><h1 class="truncate text-base font-semibold tracking-tight sm:text-lg">LLM Task Monitor</h1></div>' +
    '<button id="btn-refresh" type="button" class="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium text-ink shadow-panel hover:bg-soft">Refresh</button>' +
    '<button id="btn-right" type="button" class="rounded-lg border border-line px-2.5 py-1.5 text-sm text-muted hover:bg-soft lg:hidden">Catalog</button></div>' +
    '<div class="flex flex-wrap items-center gap-2"><div id="status-pills" class="flex flex-wrap items-center gap-2"></div><div id="status-line" class="text-xs text-muted"></div></div></div></header>' +
    '<div class="mx-auto flex w-full max-w-[1600px] flex-1 overflow-hidden">' +
    '<aside id="left-nav" class="' +
    (state.leftOpen ? '' : 'hidden') +
    ' w-56 shrink-0 border-r border-line bg-panel lg:block"><nav class="flex h-full flex-col gap-1 p-3"><div class="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wide text-muted">Navigate</div>' +
    NAV.map(
      (n) =>
        '<button type="button" data-nav="' +
        n.id +
        '" class="nav-item rounded-xl px-3 py-2 text-left text-sm transition hover:bg-soft ' +
        (state.nav === n.id ? 'bg-accent-soft font-medium text-accent' : 'text-ink') +
        '">' +
        n.label +
        '</button>'
    ).join('') +
    '<div class="mt-auto rounded-xl border border-line bg-soft/60 p-3 text-xs text-muted">Light mode · server catalog + local drafts</div></nav></aside>' +
    '<main class="flex min-w-0 flex-1 flex-col bg-canvas"><div class="border-b border-line bg-panel px-3 py-2 sm:px-4"><div class="flex flex-wrap gap-1">' +
    (state.nav === 'task-id-coding'
      ? TASK_ID_TABS.map(
          (t) =>
            '<button type="button" data-tid-tab="' +
            t.id +
            '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
            ((state.taskId?.tab || 'list') === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
            '">' +
            t.label +
            '</button>'
        ).join('')
      : TABS.map(
          (t) =>
            '<button type="button" data-tab="' +
            t.id +
            '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
            (state.tab === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
            '">' +
            t.label +
            '</button>'
        ).join('')) +
    '</div></div><div id="workspace-body" class="fade-swap flex-1 overflow-auto p-3 sm:p-4">' +
    workspaceHtml() +
    '</div></main>' +
    '<aside id="right-list" class="' +
    (state.rightOpen ? 'flex' : 'hidden') +
    ' w-80 shrink-0 flex-col border-l border-line bg-panel lg:flex"><div class="border-b border-line p-3"><div class="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">Catalog</div>' +
    '<input id="search" type="search" placeholder="Search catalog…" class="w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none ring-accent focus:ring-2" value="' +
    esc(state.query) +
    '" /></div><div id="record-list" class="flex-1 overflow-auto"></div></aside></div>' +
    '<div id="toast" class="pointer-events-none fixed bottom-4 right-4 rounded-xl bg-ink px-3 py-2 text-sm text-white opacity-0 shadow-panel transition"></div></div>'
  );
}

async function refreshWatchdog(selectedKeep = true) {
  try {
    const res = await fetch('/api/watchdog/events?limit=80');
    const data = await res.json();
    const wd = data.watchdog || {};
    const prevSelected = selectedKeep ? state.watchdog.selectedId : null;
    state.watchdog.running = !!(wd.running || wd.ok);
    state.watchdog.pid = wd.pid || null;
    state.watchdog.lastEvent = wd.last_event || (data.events && data.events[0]) || null;
    state.watchdog.events = data.events || [];
    state.watchdog.logTail = data.log_tail || [];
    const last = state.watchdog.lastEvent || {};
    const lastDetail = last.detail || {};
    const fromEvents = (state.watchdog.events || [])
      .map((e) => (e && e.detail && e.detail.helper_pid) || null)
      .find((p) => p);
    state.watchdog.helperPid = lastDetail.helper_pid || fromEvents || state.watchdog.helperPid || null;
    if (prevSelected && state.watchdog.events.some((e) => e.id === prevSelected)) {
      state.watchdog.selectedId = prevSelected;
    } else if (!state.watchdog.selectedId && state.watchdog.events[0]) {
      state.watchdog.selectedId = state.watchdog.events[0].id;
    }
    state.watchdog.msg = '';
  } catch (e) {
    state.watchdog.running = false;
    state.watchdog.msg = 'Watchdog API unavailable: ' + (e.message || e);
  }
}

function bindWatchdogPanel() {
  $('#btn-wd-refresh')?.addEventListener('click', async () => {
    await refreshWatchdog(true);
    const body = $('#workspace-body');
    if (body && state.nav === 'watchdog') {
      body.innerHTML = watchdogHtml();
      bindWatchdogPanel();
    }
    renderStatusPills();
    toast('Watchdog refreshed');
  });
  $('#wd-event-list')?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-wd-id]');
    if (!btn) return;
    state.watchdog.selectedId = btn.getAttribute('data-wd-id');
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = watchdogHtml();
      bindWatchdogPanel();
    }
  });
}

async function refreshAll() {
  try {
    const res = await fetch('/api/system-status');
    const data = await res.json();
    const ol = data.ollama || {};
    const idn = data.identity || {};
    const helper = data.helper || {};
    const wd = data.watchdog || helper.watchdog || {};
    state.status.helperOk = !!(data.ok || helper.ok);
    state.status.ollamaOk = !!ol.ok;
    state.status.modelPresent = !!ol.model_present;
    state.status.model = ol.model || '';
    state.status.computerId = idn.computer_id || '';
    state.status.computerName = idn.computer_name || '';
    state.status.baseUrl = ol.base_url || '';
    state.status.text = (idn.computer_name || '-') + ' · ' + (idn.computer_id || '-') + ' · ' + (ol.base_url || '');
    state.watchdog.running = !!(wd.running || wd.ok);
    state.watchdog.pid = wd.pid || state.watchdog.pid;
    state.watchdog.lastEvent = wd.last_event || state.watchdog.lastEvent;
  } catch {
    state.status.helperOk = false;
    state.status.ollamaOk = false;
    state.status.modelPresent = false;
    state.status.text = 'mouse_spot_helper offline';
    state.watchdog.running = false;
  }

  try {
    const res = await fetch('/api/llm-tasks?limit=200');
    const data = await res.json();
    state.report.tasks = data.tasks || [];
    state.report.totals = data.totals || state.report.totals;
    state.report.by_model = data.by_model || [];
    state.report.models = data.models || [];
  } catch {
    /* keep previous */
  }

  if (state.nav === 'watchdog') {
    await refreshWatchdog(true);
  }

  renderStatusPills();
  renderRightList();
  if (state.tab === 'report' && state.nav === 'task-center') {
    const body = $('#workspace-body');
    if (body) body.innerHTML = reportHtml();
    bindReportOnly();
  }
  if (state.nav === 'watchdog') {
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = watchdogHtml();
      bindWatchdogPanel();
    }
  }
}

function bindReportOnly() {
  $('#btn-clear-server')?.addEventListener('click', async () => {
    if (!confirm('Clear all server LLM task history?')) return;
    await fetch('/api/llm-tasks/clear', { method: 'POST' });
    await refreshAll();
    toast('Server history cleared');
  });
  document.querySelectorAll('[data-report-id]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const id = tr.getAttribute('data-report-id');
      const task = state.report.tasks.find((x) => x.id === id);
      if (task) loadServerTaskIntoWorkspace(task);
    });
  });
}

function bind() {
  $('#btn-left')?.addEventListener('click', () => {
    state.leftOpen = !state.leftOpen;
    $('#left-nav')?.classList.toggle('hidden', !state.leftOpen);
  });
  $('#btn-right')?.addEventListener('click', () => {
    state.rightOpen = !state.rightOpen;
    const el = $('#right-list');
    if (!el) return;
    el.classList.toggle('hidden', !state.rightOpen);
    el.classList.toggle('flex', state.rightOpen);
  });
  $('#btn-refresh')?.addEventListener('click', async () => {
    await refreshAll();
    toast('Refreshed');
  });

  document.querySelectorAll('[data-nav]').forEach((btn) => {
    btn.addEventListener('click', () => {
      readFormIntoDraft();
      state.nav = btn.getAttribute('data-nav');
      mount(false);
    });
  });
  document.querySelectorAll('[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      readFormIntoDraft();
      state.tab = btn.getAttribute('data-tab');
      mount(false);
    });
  });

  $('#search')?.addEventListener('input', (e) => {
    state.query = e.target.value;
    renderRightList();
  });

  $('#record-list')?.addEventListener('click', (e) => {
    const cat = e.target.closest('[data-catalog]');
    if (cat) {
      state.catalogMode = cat.getAttribute('data-catalog');
      renderRightList();
      return;
    }
    const local = e.target.closest('[data-local-id]');
    if (local) {
      const id = local.getAttribute('data-local-id');
      const rec = listRecords().find((r) => r.id === id);
      if (rec) {
        state.tab = 'analyze';
        state.catalogMode = 'local';
        fillForm(rec, true);
        mount(false);
      }
      return;
    }
    const server = e.target.closest('[data-server-id]');
    if (server) {
      const id = server.getAttribute('data-server-id');
      const task = state.report.tasks.find((t) => t.id === id);
      if (task) loadServerTaskIntoWorkspace(task);
    }
  });

  $('#btn-new')?.addEventListener('click', () => {
    const rec = createBlankRecord({ status: 'draft', name: 'New record' });
    state.tab = 'analyze';
    state.catalogMode = 'local';
    fillForm(rec, true);
    mount(false);
    toast('New draft');
  });

  $('#btn-save')?.addEventListener('click', () => {
    readFormIntoDraft();
    const saved = upsertRecord(state.draft);
    state.draft = saved;
    state.selectedId = saved.id;
    state.catalogMode = 'local';
    renderRightList();
    toast('Saved to LocalStorage');
  });

  $('#btn-delete')?.addEventListener('click', () => {
    const id = state.selectedId || state.draft.id;
    if (!id) return;
    if (!confirm('Delete this local record?')) return;
    deleteRecord(id);
    const next = listRecords()[0] || createBlankRecord({ name: 'New record' });
    fillForm(next, true);
    mount(false);
    toast('Deleted');
  });

  $('#btn-copy')?.addEventListener('click', async () => {
    const text = $('#f-result')?.value || '';
    try {
      await navigator.clipboard.writeText(text);
      toast('Result copied');
    } catch {
      toast('Copy failed');
    }
  });

  $('#btn-apply')?.addEventListener('click', () => {
    const r = $('#f-result')?.value || '';
    if (!r) return toast('No result to apply');
    const p = $('#f-prompt');
    if (p) p.value = r;
    state.tab = 'chat';
    readFormIntoDraft();
    mount(false);
    toast('Applied to prompt');
  });

  $('#btn-from-template')?.addEventListener('click', async () => {
    readFormIntoDraft();
    const d = state.draft;
    const ide = $('#f-ide-target')?.value || 'Visual Studio Code';
    try {
      const res = await fetch('/api/prompt/from-template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: 'ide_work',
          goal: d.prompt_content || d.context || d.name || 'Complete this coding task',
          context: d.context || '',
          ide_target: ide,
          writer: d.writer,
          task_id: d.task_id,
          session_id: d.session_id || '',
        }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || 'template failed');
      const prompt = data.prompt || data.improved_prompt || '';
      if ($('#f-prompt')) $('#f-prompt').value = prompt;
      state.draft.prompt_content = prompt;
      if ($('#f-result')) {
        $('#f-result').value =
          (data.hint || 'Created from IDE template') +
          '\n\n' +
          (data.trailer || formatIdentityTrailer(data));
      }
      state.draft.result_content = $('#f-result')?.value || '';
      toast('Prompt from IDE template');
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  $('#btn-apply-ids')?.addEventListener('click', async () => {
    const text = ($('#f-result')?.value || $('#f-prompt')?.value || '').trim();
    if (!text) return toast('No reply/prompt to parse');
    try {
      const res = await fetch('/api/prompt/apply-ids', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || 'parse failed');
      const idn = data.identity || data;
      if (idn.session_id && $('#f-session')) $('#f-session').value = idn.session_id;
      if (idn.task_id && $('#f-task-id')) $('#f-task-id').value = idn.task_id;
      if (idn.writer && $('#f-writer')) $('#f-writer').value = idn.writer;
      readFormIntoDraft();
      toast(
        'Applied IDs · session=' +
          (idn.session_id || '(empty)') +
          ' · task=' +
          (idn.task_id || '(empty)') +
          ' · writer=' +
          (idn.writer || '(empty)')
      );
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  $('#btn-improve')?.addEventListener('click', async () => {
    readFormIntoDraft();
    const d = state.draft;
    try {
      const res = await fetch('/api/prompt/improve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: d.prompt_content,
          writer: d.writer,
          task_id: d.task_id,
          session_id: d.session_id,
          context: d.context,
          require_session: false,
        }),
      });
      const data = await res.json();
      let out =
        data.improved_prompt ||
        data.improved ||
        data.result ||
        data.text ||
        data.error ||
        JSON.stringify(data, null, 2);
      if (typeof out !== 'string') out = JSON.stringify(out, null, 2);
      const trailer = data.trailer || formatIdentityTrailer(data.identity || data);
      if (trailer && !String(out).includes('session_id:')) {
        out = String(out).replace(/\s*$/, '') + '\n\n' + trailer;
      }
      if ($('#f-result')) $('#f-result').value = out;
      state.draft.result_content = $('#f-result')?.value || '';
      state.draft.status = data.ok === false ? 'error' : 'done';
      if ($('#f-status')) $('#f-status').value = state.draft.status;
      state.tab = 'result';
      mount(false);
      await refreshAll();
      toast(data.ok === false ? 'Improve error' : 'Improved');
    } catch (err) {
      if ($('#f-result')) $('#f-result').value = String(err);
      toast('Improve failed');
    }
  });

  $('#btn-analyze')?.addEventListener('click', async () => {
    readFormIntoDraft();
    const d = state.draft;
    try {
      const res = await fetch('/api/prompt/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: d.prompt_content,
          writer: d.writer,
          task_id: d.task_id,
          session_id: d.session_id,
          context: d.context,
          require_session: false,
        }),
      });
      const data = await res.json();
      let out = data.notes || data.analysis || data.result || data.error || data;
      if (typeof out !== 'string') out = JSON.stringify(out, null, 2);
      const trailer = data.trailer || formatIdentityTrailer(data.identity || data);
      if (trailer && !String(out).includes('session_id:')) {
        out = String(out).replace(/\s*$/, '') + '\n\n' + trailer;
      }
      if ($('#f-result')) $('#f-result').value = out;
      state.draft.result_content = $('#f-result')?.value || '';
      state.draft.status = data.ok === false ? 'error' : 'done';
      state.tab = 'result';
      mount(false);
      await refreshAll();
      toast(data.ok === false ? 'Analyze error' : 'Analyzed');
    } catch (err) {
      if ($('#f-result')) $('#f-result').value = String(err);
      toast('Analyze failed');
    }
  });

  bindReportOnly();
}

function mount(fromBoot = true) {
  const app = document.getElementById('app');
  const keepDraft = { ...state.draft };
  app.innerHTML = shell();
  bind();
  renderStatusPills();
  renderRightList();
  if (state.nav === 'task-center' && state.tab !== 'report') {
    fillForm(keepDraft, fromBoot);
  }
  if (state.nav === 'skill-ssot') {
    bindSkillPanel();
    loadSkillPanel();
  }
  if (state.nav === 'watchdog') {
    bindWatchdogPanel();
    refreshWatchdog(true).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'watchdog') {
        body.innerHTML = watchdogHtml();
        bindWatchdogPanel();
      }
      renderStatusPills();
    });
  }
  refreshAll();
}

export function startApp() {
  if (!listRecords().length) {
    upsertRecord(
      createBlankRecord({
        name: 'Sample record',
        task_id: '',
        writer: '',
        status: 'draft',
        prompt_content: '',
        result_content: '',
      })
    );
  }
  const first = listRecords()[0];
  state.draft = createBlankRecord(first);
  state.selectedId = first.id;
  mount(true);
  setInterval(() => {
    refreshAll();
  }, 5000);
}