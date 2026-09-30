/**
 * mode-sessions.js — the REVIEW SURFACE for per-session mode + conversation env.
 *
 * Route: /llm-tasks/mode_sessions
 *
 * WHY THIS PAGE EXISTS (the user, 2026-09-23):
 *   "ui will report the current mode for each session too"
 *   "so i can get all the answer at ui to review too"
 *   "conversation id is help us to have environment checklist for vscode
 *    example : mode = plan, permission = autopilot...."
 *
 * MEASURED BEFORE IT EXISTED: `/api/mode/sessions` answered 200 with 73 sessions
 * and three distinct modes, while `grep 'api/mode/sessions'` over every UI source
 * returned ZERO matches. The data was reachable and the browser could not show
 * it. That gap is what this page closes.
 *
 * SSOT:
 *   /api/mode/sessions                   every chat + its mode
 *   /api/mode/session?session_id=        one chat
 *   /api/vscode_env/list                 every VS Code conversation (chat_main)
 *   /api/vscode_env/<id>/checklist       latest env per dimension
 *   /api/vscode_env/<id>/trace?dim=      the history of one dimension
 *
 * THE PATH IS `/api/vscode_env/*`, NOT `/api/conversation/*`. The bare word
 * `conversation` reads as the CHAT system, which is a DIFFERENT table set
 * (`chat_center_message`, `chat_identity_log`, `chat_registry`, ...) that happens
 * to key on the same session id. The terms are registered in
 * `terminology_registry` (`vscode_conversation` and `chat_system` are SIBLINGS).
 *
 * THE TWO RULES THE RENDERING MUST KEEP
 * 1. A missing mode renders the literal `no mode (FAULT)`. NEVER a blank cell:
 *    a blank reads as "no problem" and that is the failure this whole feature
 *    exists to prevent.
 * 2. The RUNG is always shown beside the mode, because `inputState.mode` LAGS
 *    the selector by one request (measured). A mode with no rung is ambiguous
 *    between "in force now" and "the last sent request".
 */

export const TABS = [{ id: 'sessions', label: 'Sessions' }];

import { fmtLocal } from './timefmt.js';

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

const RUNG_NOTE = {
  live: 'pending request — this is the mode IN FORCE NOW',
  committed: 'last sent request — no request is being composed',
};

function rungChip(rung) {
  if (!rung) return '';
  const live = rung === 'live';
  return '<span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' +
    (live ? 'bg-accent-soft text-accent' : 'bg-soft text-muted') + '">' +
    esc(rung) + '</span>';
}

function statusChip(status) {
  const map = {
    ok: 'bg-emerald-100 text-emerald-700',
    fault: 'bg-rose-100 text-rose-700',
    unknown: 'bg-amber-100 text-amber-700',
  };
  return '<span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' +
    (map[status] || map.unknown) + '">' + esc(status || 'unknown') + '</span>';
}

export function mountModeSessions(root, state, helpers) {
  const toast = (helpers && helpers.toast) || (() => {});
  const s = state.modeSessions || (state.modeSessions = {
    tab: 'sessions',
    sessions: [], sessionsTotal: 0, sessionsReturned: 0,
    sessionsTruncated: false, sessionsMsg: '', sessionsLoaded: false,
    // THE MEASURE KEY. The user: "need a list with status to help, this is
    // measure key!!". `liveCount` is how many sessions are live RIGHT NOW;
    // `liveIds` names them. `workspaces` is the human's word per surface.
    liveCount: null, liveIds: [], liveWindowS: null, workspaces: [],
    conversations: [], convTotal: 0, convReturned: 0, convTruncated: false,
    convMsg: '', convLoaded: false,
    selected: null, checklist: null, trace: null,
    raw: '', rawLabel: '', busy: false,
  });

  async function loadSessions() {
    try {
      const res = await fetch('/api/mode/sessions?limit=200');
      const d = await res.json();
      s.sessions = d.sessions || [];
      s.sessionsTotal = d.total || 0;
      s.sessionsReturned = d.returned || 0;
      s.sessionsTruncated = !!d.truncated;
      // THE MEASURE KEY, read from the SAME response -- never inferred here.
      s.liveCount = (d.live_count == null) ? null : d.live_count;
      s.liveIds = d.live_session_ids || [];
      s.liveWindowS = d.live_window_s == null ? null : d.live_window_s;
      s.workspaces = d.workspaces || [];
      s.sessionsMsg = d.ok ? '' : (d.error || 'mode sessions API error');
      s.sessionsLoaded = true;
    } catch (e) {
      s.sessionsMsg = 'Mode sessions API unavailable: ' + (e.message || e);
      s.sessionsLoaded = true;
    }
    render();
  }

  async function loadConversations() {
    try {
      const res = await fetch('/api/vscode_env/list?limit=200');
      const d = await res.json();
      s.conversations = d.conversations || [];
      s.convTotal = d.total || 0;
      s.convReturned = d.returned || 0;
      s.convTruncated = !!d.truncated;
      s.convMsg = d.ok ? '' : (d.error || 'conversation API error');
      s.convLoaded = true;
    } catch (e) {
      s.convMsg = 'Conversation API unavailable: ' + (e.message || e);
      s.convLoaded = true;
    }
    render();
  }

  async function openConversation(cid) {
    s.selected = cid;
    s.checklist = null;
    s.trace = null;
    s.busy = true;
    render();
    try {
      const res = await fetch('/api/vscode_env/' + encodeURIComponent(cid) +
        '/checklist');
      s.checklist = await res.json();
    } catch (e) {
      s.checklist = { ok: false, error: String(e) };
    }
    s.busy = false;
    render();
  }

  async function loadTrace(dim) {
    if (!s.selected) return;
    s.busy = true;
    render();
    try {
      const res = await fetch('/api/vscode_env/' + encodeURIComponent(s.selected) +
        '/trace?dim=' + encodeURIComponent(dim || ''));
      s.trace = await res.json();
    } catch (e) {
      s.trace = { ok: false, error: String(e) };
    }
    s.busy = false;
    render();
  }

  function showRaw(label, obj) {
    s.rawLabel = label;
    s.raw = JSON.stringify(obj, null, 2);
    render();
  }

  async function copyText(text, what) {
    try {
      await navigator.clipboard.writeText(text);
      toast(what + ' copied');
    } catch (e) {
      toast('Copy failed: ' + (e.message || e));
    }
  }

  function sessionsHtml() {
    if (!s.sessionsLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.sessionsMsg) {
      return '<p class="text-sm text-rose-600">' + esc(s.sessionsMsg) + '</p>';
    }
    if (!s.sessions.length) {
      return '<p class="text-sm text-muted">No chat sessions found.</p>';
    }
    const rows = s.sessions.map((x) => {
      const isFault = !x.mode;
      return '<tr class="cursor-pointer hover:bg-soft" data-ms-sid="' +
        esc(x.session_id) + '">' +
        '<td class="px-3 py-2 mono text-xs">' + esc(x.session_id) + '</td>' +
        '<td class="px-3 py-2">' +
        // RULE 1: a missing mode is the LOUD string, never a blank cell.
        (isFault
          ? '<span class="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-medium text-rose-700">no mode (FAULT)</span>'
          : '<span class="text-xs font-semibold">' + esc(x.mode) + '</span>') +
        '</td>' +
        '<td class="px-3 py-2">' + rungChip(x.rung) + '</td>' +
        // THE MEASURE KEY, per row. A LIVE session is running a turn, and a turn
        // runs proofs, and proofs take agent.db. A blank here would read as
        // "not live" -- so a non-live row says so explicitly.
        '<td class="px-3 py-2">' +
        (x.is_live
          ? '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">LIVE</span>'
          : '<span class="text-[11px] text-muted">idle</span>') +
        '</td>' +
        '<td class="px-3 py-2 text-xs mono">' +
        esc(x.live_mode || '-') + '</td>' +
        '<td class="px-3 py-2 text-[11px] text-muted" title="' +
        esc(RUNG_NOTE[x.rung] || '') + '">' + esc(x.scope || '-') + '</td>' +
        '<td class="px-3 py-2 text-[11px] text-muted mono">' +
        esc(x.last_activity || '-') + '</td>' +
        '<td class="px-3 py-2 text-[11px] text-muted">' +
        (x.age_sec == null ? '-' : esc(x.age_sec) + 's') + '</td>' +
        '</tr>';
    }).join('');
    return liveBannerHtml() +
      '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">session_id</th><th class="px-3 py-2">mode</th>' +
      '<th class="px-3 py-2">rung</th><th class="px-3 py-2">status</th>' +
      '<th class="px-3 py-2">live</th>' +
      '<th class="px-3 py-2">scope</th><th class="px-3 py-2">last activity</th>' +
      '<th class="px-3 py-2">age</th></tr></thead><tbody>' + rows +
      '</tbody></table></div>';
  }

  /**
   * THE LIVE BANNER — the human's instrument.
   *
   * The user (2026-09-27): "same workspace can be with many session! need a list
   * with status to help, this is measure key!!"
   *
   * WHY IT EXISTS. MEASURED: three AGENT sessions ran in ONE workspace and
   * polluted a measurement run -- `database is locked`, a proof that flipped
   * RED->GREEN between runs, and a `300.0s` timeout against a `timeout=90`
   * runner. `/api/mode/sessions` ALREADY answered with all three, but the page
   * showed no LIVE count, so neither the run nor the human could SEE it.
   *
   * IT IS NOT A REFUSAL. The human overruled an earlier plan that said "refuse a
   * second AGENT session in one workspace": "same workspace can be with many
   * session!" -- many sessions is a LEGITIMATE state. A refusal hides the state;
   * a banner exposes it.
   */
  function liveBannerHtml() {
    const n = s.liveCount;
    if (n == null) return '';
    const ids = (s.liveIds || []).map((x) => esc(String(x).slice(0, 8))).join(' · ');
    const busy = n > 1;
    const cls = busy
      ? 'border-amber-300 bg-amber-50 text-amber-800'
      : 'border-emerald-300 bg-emerald-50 text-emerald-800';
    return '<div class="mb-3 rounded-xl border ' + cls + ' px-3 py-2 text-xs">' +
      '<span class="font-semibold">LIVE SESSIONS: ' + esc(n) + '</span>' +
      (ids ? ' <span class="mono">(' + ids + ')</span>' : '') +
      (busy
        ? ' — <span class="font-medium">a run started now is measuring a BUSY ' +
          'workspace. A RED measured here is NOT evidence of a defect.</span>'
        : ' — <span class="font-medium">the workspace is quiet.</span>') +
      '</div>';
  }

  function conversationsHtml() {
    if (!s.convLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.convMsg) {
      return '<p class="text-sm text-rose-600">' + esc(s.convMsg) + '</p>';
    }
    if (!s.conversations.length) {
      return '<p class="text-sm text-muted">No VS Code conversations yet. One is ' +
        'created when a session is registered (chat_main).</p>';
    }
    const rows = s.conversations.map((c) =>
      '<tr class="cursor-pointer hover:bg-soft' +
      (String(s.selected) === String(c.vscode_conversation_id) ? ' bg-accent-soft' : '') +
      '" data-ms-cid="' + esc(c.vscode_conversation_id) + '">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(c.vscode_conversation_id) + '</td>' +
      '<td class="px-3 py-2 mono text-xs">' + esc(c.session_id) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(c.ide || '-') + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(c.llm || '-') + '</td>' +
      '<td class="px-3 py-2 text-xs text-muted mono">' + esc(fmtLocal(c.updated_at)) +
      '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(c.observations) + '</td>' +
      '</tr>').join('');
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">vscode_conversation_id</th><th class="px-3 py-2">session_id</th>' +
      '<th class="px-3 py-2">ide</th><th class="px-3 py-2">llm</th>' +
      '<th class="px-3 py-2">updated</th><th class="px-3 py-2">obs</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  function checklistHtml() {
    const ck = s.checklist;
    if (!s.selected) {
      return '<div class="rounded-xl border border-dashed border-line bg-soft/40 ' +
        'p-4 text-sm text-muted">Pick a conversation to see its environment ' +
        'checklist.</div>';
    }
    if (!ck) return '<p class="text-sm text-muted">Loading…</p>';
    if (!ck.ok) {
      return '<p class="text-sm text-rose-600">' + esc(ck.error) + '</p>';
    }
    const dims = ck.dimensions || [];
    const rows = dims.map((d) => {
      // RULE 1 again: an unmeasured dimension says so, it does not go blank.
      const val = d.measured
        ? '<span class="mono text-xs">' + esc(d.value_text) + '</span>'
        : '<span class="text-[11px] text-amber-700">not measured</span>';
      return '<tr class="border-t border-line align-top">' +
        '<td class="px-2 py-1.5 text-xs mono">' + esc(d.dim_key) +
        (d.is_required
          ? ' <span class="rounded bg-rose-50 px-1 text-[10px] text-rose-700">req</span>'
          : '') + '</td>' +
        '<td class="px-2 py-1.5">' + val + '</td>' +
        '<td class="px-2 py-1.5">' + statusChip(d.status) + '</td>' +
        '<td class="px-2 py-1.5 text-[11px] text-muted">' + esc(d.source || '-') +
        '</td>' +
        '<td class="px-2 py-1.5 text-[11px] text-muted" title="' + esc(d.why || '') +
        '">' + esc((d.why || '').slice(0, 70)) + '</td>' +
        '<td class="px-2 py-1.5">' +
        '<button type="button" data-ms-trace="' + esc(d.dim_key) +
        '" class="rounded border border-line px-1.5 py-0.5 text-[11px] hover:bg-soft">trace</button>' +
        '</td></tr>';
    }).join('');
    const missing = ck.missing || [];
    const banner = '<div class="mb-2 rounded-xl border px-3 py-2 text-sm ' +
      (ck.complete
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-amber-200 bg-amber-50 text-amber-800') + '">' +
      'complete = <b>' + esc(String(ck.complete)) + '</b>' +
      (missing.length
        ? ' · required but NEVER measured: <span class="mono">' +
          esc(missing.join(', ')) + '</span> — these are NAMED, not blanked'
        : '') +
      ((ck.faults || []).length
        ? ' · faults: <span class="mono text-rose-700">' +
          esc(ck.faults.join(', ')) + '</span>'
        : '') +
      '</div>';
    return banner + '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-2 py-2">dimension</th><th class="px-2 py-2">value</th>' +
      '<th class="px-2 py-2">status</th><th class="px-2 py-2">source</th>' +
      '<th class="px-2 py-2">why</th><th class="px-2 py-2"></th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  }

  function traceHtml() {
    if (!s.trace) return '';
    if (!s.trace.ok) {
      return '<p class="text-sm text-rose-600">' + esc(s.trace.error) + '</p>';
    }
    const items = s.trace.items || [];
    if (!items.length) {
      return '<p class="text-sm text-muted">No observations for ' +
        esc(s.trace.dim_key || 'any dimension') + '.</p>';
    }
    const rows = items.map((t) =>
      '<tr class="border-t border-line">' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(t.dim_key) + '</td>' +
      '<td class="px-2 py-1.5 text-xs mono">' + esc(t.value_text) + '</td>' +
      '<td class="px-2 py-1.5">' + statusChip(t.status) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted">' + esc(t.source) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted">' +
      (t.is_pending ? 'pending' : 'committed') + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted mono">' +
      esc(fmtLocal(t.evidence_at)) + '</td>' +
      '<td class="px-2 py-1.5 text-[11px] text-muted mono">' +
      esc(fmtLocal(t.observed_at)) + '</td>' +
      '</tr>').join('');
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-2 py-2">dim</th><th class="px-2 py-2">value</th>' +
      '<th class="px-2 py-2">status</th><th class="px-2 py-2">source</th>' +
      '<th class="px-2 py-2">rung</th><th class="px-2 py-2">evidence_at</th>' +
      '<th class="px-2 py-2">observed_at</th></tr></thead><tbody>' + rows +
      '</tbody></table></div>';
  }

  function render() {
    if (!root) return;
    root.innerHTML =
      '<div class="flex flex-wrap items-center justify-between gap-2">' +
      '<div><h2 class="text-lg font-semibold">Mode &amp; Conversation Review</h2>' +
      '<p class="mt-0.5 text-sm text-muted">One workspace, many chats. Each row ' +
      'reads its OWN session file, so two chats report two modes. ' +
      'SSOT = <span class="mono">mode_attest</span> + ' +
      '<span class="mono">conversation_store</span>.</p></div>' +
      '<div class="flex gap-2">' +
      '<button id="ms-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
      '</div></div>' +

      '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
      '<h3 class="text-sm font-semibold">Chat sessions &amp; their current mode</h3>' +
      // The cap is SHOWN. A capped list presented as the whole set is a lie about
      // what was measured.
      '<span class="text-[11px] text-muted mono">returned ' +
      esc(s.sessionsReturned) + ' / total ' + esc(s.sessionsTotal) +
      (s.sessionsTruncated ? ' · TRUNCATED' : ' · complete') + '</span></div>' +
      sessionsHtml() + '</section>' +

      '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
      '<h3 class="text-sm font-semibold">VS Code conversations (chat_main)</h3>' +
      '<span class="text-[11px] text-muted mono">returned ' +
      esc(s.convReturned) + ' / total ' + esc(s.convTotal) +
      (s.convTruncated ? ' · TRUNCATED' : ' · complete') + '</span></div>' +
      conversationsHtml() + '</section>' +

      '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold">Environment checklist' +
      (s.selected ? ' · conversation ' + esc(s.selected) : '') + '</h3>' +
      checklistHtml() +
      (s.trace ? '<h4 class="mt-3 text-sm font-semibold">Trace</h4>' + traceHtml() : '') +
      '</section>' +

      (s.raw
        ? '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
          '<h3 class="mb-2 text-sm font-semibold">Raw JSON · ' + esc(s.rawLabel) + '</h3>' +
          '<pre class="mono max-h-72 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs whitespace-pre-wrap">' +
          esc(s.raw) + '</pre></section>'
        : '');

    const rf = root.querySelector('#ms-refresh');
    if (rf) rf.addEventListener('click', () => { loadSessions(); loadConversations(); });

    root.querySelectorAll('[data-ms-sid]').forEach((tr) => {
      tr.addEventListener('click', async () => {
        const sid = tr.getAttribute('data-ms-sid');
        try {
          const res = await fetch('/api/mode/session?session_id=' +
            encodeURIComponent(sid));
          const d = await res.json();
          showRaw('session ' + sid, d);
        } catch (e) {
          toast('Fetch failed: ' + (e.message || e));
        }
      });
    });

    root.querySelectorAll('[data-ms-cid]').forEach((tr) => {
      tr.addEventListener('click', () =>
        openConversation(tr.getAttribute('data-ms-cid')));
    });

    root.querySelectorAll('[data-ms-trace]').forEach((btn) => {
      btn.addEventListener('click', () =>
        loadTrace(btn.getAttribute('data-ms-trace')));
    });
  }

  loadSessions();
  loadConversations();
}
