/**
 * identity.js — the IDENTITY system page. Route: /llm-tasks/identity
 *
 * The user (2026-09-23):
 *   "identity is 5W1H, who = session ID + worker ID required
 *    what = get workflow id / workflow tell you how to have chat ID"
 *   "have the worker ui at /llm-tasks/worker and /llm-tasks/identity"
 *
 * SSOT = /api/identity/list + /api/identity/get + /api/worker_identity/5w1h
 *
 * WHAT AN IDENTITY IS:
 *   * `who`  = session_id AND worker_id, BOTH required (composite)
 *   * `what` = workflow_id — the workflow is what PRODUCES the chat id
 *   * `chat_id` is the workflow's OUTPUT, so it is NULL until the workflow runs.
 *     A NULL here is a REAL state ("not yet produced"), never a fake value.
 *
 * The 5W1H panel shows all SIX dimensions, each with its source. An unbound
 * dimension is shown as UNBOUND — never given a generic question.
 */

export const TABS = [
  { id: 'list', label: 'Identity List' },
  { id: 'detail', label: 'Detail' },
  { id: '5w1h', label: '5W1H Join' },
  { id: 'confirm', label: 'Confirm' },
  { id: 'send', label: 'Send' },
];

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

export function mountIdentity(root, state, helpers) {
  const toast = (helpers && helpers.toast) || (() => {});
  const s = state.identity || (state.identity = {
    tab: 'list', rows: [], loaded: false, msg: '', detail: null, join: null,
    confirms: [], confirmsLoaded: false, confirmsMsg: '',
    sendPlan: null, sendMsg: '', sendArmed: false,
  });

  async function refresh() {
    try {
      const res = await fetch('/api/identity/list');
      const data = await res.json();
      s.rows = data.identities || [];
      s.msg = data.ok ? '' : (data.error || 'identity API error');
      s.loaded = true;
    } catch (e) {
      s.msg = 'Identity API unavailable: ' + (e.message || e);
      s.loaded = true;
    }
    render();
  }

  async function openDetail(key) {
    try {
      const res = await fetch('/api/identity/get?identity_key=' + encodeURIComponent(key));
      s.detail = await res.json();
    } catch (e) {
      s.detail = { ok: false, error: String(e) };
    }
    s.tab = 'detail';
    render();
  }

  async function openJoin(key) {
    const d = s.detail && s.detail.identity;
    const wkey = (d && d.worker_key) || '';
    try {
      const res = await fetch('/api/worker_identity/5w1h?identity_key=' +
        encodeURIComponent(key) + '&worker_key=' + encodeURIComponent(wkey));
      s.join = await res.json();
    } catch (e) {
      s.join = { ok: false, error: String(e) };
    }
    s.tab = '5w1h';
    render();
  }

  function listHtml() {
    if (!s.loaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.msg) return '<p class="text-sm text-rose-600">' + esc(s.msg) + '</p>';
    if (!s.rows.length) {
      return '<p class="text-sm text-muted">No identities yet. Open one with ' +
        '<span class="mono">POST /api/identity/open</span>.</p>';
    }
    const rows = s.rows.map((i) =>
      '<tr class="cursor-pointer hover:bg-soft" data-identity-key="' + esc(i.identity_key) + '">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(i.session_id) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(i.worker_id) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(i.workflow_id) + '</td>' +
      '<td class="px-3 py-2 text-xs">' +
      (i.chat_id == null
        ? '<span class="text-muted">NULL (not produced)</span>'
        : esc(i.chat_id)) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(i.channel) + '</td>' +
      '</tr>').join('');
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">session_id (who 1)</th><th class="px-3 py-2">worker_id (who 2)</th>' +
      '<th class="px-3 py-2">workflow_id (what)</th><th class="px-3 py-2">chat_id (output)</th>' +
      '<th class="px-3 py-2">channel</th></tr></thead><tbody>' + rows +
      '</tbody></table></div>';
  }

  function detailHtml() {
    const d = s.detail;
    if (!d) return '<p class="text-sm text-muted">Pick an identity from the list.</p>';
    if (!d.ok) return '<p class="text-sm text-rose-600">' + esc(d.error) + '</p>';
    const i = d.identity;
    const kv = (k, v) => '<div class="flex gap-2 py-1"><span class="w-40 text-xs text-muted">' +
      esc(k) + '</span><span class="text-xs mono">' + esc(v) + '</span></div>';
    return '<div class="rounded-xl border border-line p-4">' +
      '<h3 class="text-sm font-semibold">' + esc(i.identity_key) + '</h3>' +
      kv('session_id (who 1)', i.session_id) +
      kv('worker_id (who 2)', i.worker_id) +
      kv('workflow_id (what)', i.workflow_id) +
      kv('chat_id (output)', i.chat_id == null ? 'NULL — not produced yet' : i.chat_id) +
      kv('channel', i.channel) + kv('step_no', i.step_no) +
      kv('cite_ref', i.cite_ref) +
      '</div>' +
      '<button id="identity-join" class="mt-3 rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">' +
      'Show 5W1H join</button>';
  }

  function joinHtml() {
    const j = s.join;
    if (!j) return '<p class="text-sm text-muted">Open an identity, then press "Show 5W1H join".</p>';
    if (!j.ok) return '<p class="text-sm text-rose-600">' + esc(j.error) + '</p>';
    const rows = (j.dimensions || []).map((d) =>
      '<tr><td class="px-3 py-2 text-xs mono">' + esc(d.dimension_key) + '</td>' +
      '<td class="px-3 py-2 text-xs">' +
      (d.bound ? esc(d.binding_text)
               : '<span class="text-rose-600">UNBOUND — no wording registered</span>') + '</td>' +
      '<td class="px-3 py-2 text-xs mono">' + esc(d.source) + '</td></tr>').join('');
    return '<div class="rounded-xl border border-line p-4">' +
      '<div class="text-xs"><b>who</b> = session <span class="mono">' + esc(j.who.session_id) +
      '</span> + worker <span class="mono">' + esc(j.who.worker_id) + '</span> (both required)</div>' +
      '<div class="mt-1 text-xs"><b>what</b> = workflow <span class="mono">' + esc(j.what.workflow_id) +
      '</span> — the workflow produces the chat id</div>' +
      '<div class="mt-1 text-xs"><b>chat_id</b> = ' +
      (j.chat_id == null ? '<span class="text-muted">NULL (not produced)</span>' : esc(j.chat_id)) +
      '</div>' +
      '<div class="mt-1 text-xs text-muted">bound ' + esc(j.bound_count) + ' / ' + esc(j.total) + '</div>' +
      '</div>' +
      '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">dimension</th><th class="px-3 py-2">binding</th>' +
      '<th class="px-3 py-2">source</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  async function refreshConfirms() {
    try {
      const res = await fetch('/api/session_confirm/list');
      const data = await res.json();
      s.confirms = data.confirms || [];
      s.confirmsMsg = data.ok ? '' : (data.error || 'confirm API error');
      s.confirmsLoaded = true;
    } catch (e) {
      s.confirmsMsg = 'Confirm API unavailable: ' + (e.message || e);
      s.confirmsLoaded = true;
    }
    render();
  }

  /**
   * THE 6-LINE RECORD. Each line shows the ASKED value and the ECHOED value
   * side by side, plus whether they match. Line 6 (CONFIRM) is the chat's OWN
   * verdict, so it has no "asked" side — that is the template's shape, not a
   * missing value.
   *
   * The field names come from the API (which parses the template), so changing
   * the template changes this table with no code change.
   */
  function confirmHtml() {
    if (!s.confirmsLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.confirmsMsg) {
      return '<p class="text-sm text-rose-600">' + esc(s.confirmsMsg) + '</p>';
    }
    if (!s.confirms.length) {
      return '<p class="text-sm text-muted">No confirm records yet. ' +
        'Open one with <span class="mono">POST /api/session_confirm/request</span>.</p>';
    }
    const badge = (v) => {
      const cls = v === 'CONFIRM' ? 'bg-emerald-100 text-emerald-800'
        : v === 'MISMATCH' ? 'bg-rose-100 text-rose-800'
        : 'bg-amber-100 text-amber-800';
      return '<span class="rounded px-2 py-0.5 text-[11px] ' + cls + '">' + esc(v) + '</span>';
    };
    return s.confirms.map((cf) => {
      const lines = (cf.lines || []).map((ln) => {
        const m = ln.match === true ? '<span class="text-emerald-700">YES</span>'
          : ln.match === false ? '<span class="text-rose-700">NO</span>'
          : '<span class="text-muted">—</span>';
        return '<tr>' +
          '<td class="px-3 py-1 text-xs mono">' + esc(ln.field) + '</td>' +
          '<td class="px-3 py-1 text-xs mono">' + esc(ln.asked || '—') + '</td>' +
          '<td class="px-3 py-1 text-xs mono">' + esc(ln.echoed || '—') + '</td>' +
          '<td class="px-3 py-1 text-xs">' + m + '</td></tr>';
      }).join('');
      return '<div class="mb-4 rounded-xl border border-line p-3">' +
        '<div class="flex items-center gap-2">' + badge(cf.verdict) +
        '<span class="mono text-xs">' + esc(cf.confirm_key) + '</span>' +
        '<span class="text-xs text-muted">session ' + esc(cf.session_id) + '</span>' +
        '</div>' +
        '<div class="mt-2 overflow-auto rounded-lg border border-line">' +
        '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
        '<tr><th class="px-3 py-1">#</th><th class="px-3 py-1">asked</th>' +
        '<th class="px-3 py-1">echoed</th><th class="px-3 py-1">match</th></tr>' +
        '</thead><tbody>' + lines + '</tbody></table></div>' +
        (cf.evidence_ref && cf.evidence_ref !== 'NA'
          ? '<div class="mt-1 text-[11px] text-muted">evidence: ' +
            esc(cf.evidence_ref) + '</div>' : '') +
        '</div>';
    }).join('');
  }

  /**
   * THE OUTBOUND LEG. Pressing "Plan send" calls /api/session_send/plan, which
   * EXECUTES NOTHING and returns the exact commands. Only a SECOND press
   * (armed) calls /api/session_send/execute with confirm:true.
   *
   * TWO presses, deliberately. A send pastes into a real chat on the user's
   * desktop, so it is never one click — the same rule as the Screen Watch
   * Control tab (a rose button, not the accent one).
   */
  async function planSend() {
    const d = s.detail && s.detail.identity;
    if (!d) return;
    try {
      const res = await fetch('/api/session_send/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel: s.sendChannel || 'doubao',
          session_id: d.session_id,
        }),
      });
      s.sendPlan = await res.json();
      s.sendArmed = false;
      s.sendMsg = '';
    } catch (e) {
      s.sendPlan = { ok: false, error: String(e) };
    }
    render();
  }

  async function executeSend() {
    const d = s.detail && s.detail.identity;
    if (!d || !s.sendPlan || !s.sendPlan.ok) return;
    if (!s.sendArmed) {          // FIRST press only ARMS; it does not send
      s.sendArmed = true;
      s.sendMsg = 'Armed — press again to actually send.';
      render();
      return;
    }
    try {
      const res = await fetch('/api/session_send/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          channel: s.sendChannel || 'doubao',
          session_id: d.session_id,
          confirm: true,
        }),
      });
      const out = await res.json();
      s.sendMsg = out.ok
        ? 'Sent.'
        : ('NOT sent: ' + (out.error || 'unknown'));
    } catch (e) {
      s.sendMsg = 'NOT sent: ' + (e.message || e);
    }
    s.sendArmed = false;
    render();
  }

  function sendHtml() {
    const p = s.sendPlan;
    const btn = p && p.ok
      ? '<button id="send-execute" class="rounded-lg px-3 py-1 text-xs ' +
        (s.sendArmed ? 'bg-rose-600 text-white' : 'bg-accent text-white') + '">' +
        (s.sendArmed ? 'Confirm send (2nd press)' : 'Plan send') + '</button>'
      : '<button id="send-plan" class="rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">' +
        'Plan send</button>';
    const steps = (p && p.steps ? p.steps : []).map((st) =>
      '<tr><td class="px-3 py-1 text-xs">' + esc(st.step) + '</td>' +
      '<td class="px-3 py-1 text-xs mono">' + esc(st.command) + '</td>' +
      '<td class="px-3 py-1 text-xs text-muted">' + esc(st.why) + '</td></tr>').join('');
    return '<div class="rounded-xl border border-line p-4">' +
      '<h3 class="text-sm font-semibold">Outbound — ask the chat for its identity</h3>' +
      '<p class="mt-1 text-xs text-muted">Advisory by default: "Plan send" executes NOTHING. ' +
      'A send pastes into a real chat on your desktop, so it needs a SECOND press.</p>' +
      '<div class="mt-2 flex items-center gap-2">' +
      '<select id="send-channel" class="rounded border border-line px-2 py-1 text-xs">' +
      '<option value="doubao"' + ((s.sendChannel || 'doubao') === 'doubao' ? ' selected' : '') + '>豆包</option>' +
      '<option value="vscode"' + (s.sendChannel === 'vscode' ? ' selected' : '') + '>VS Code</option>' +
      '</select>' + btn + '</div>' +
      (s.sendMsg ? '<div class="mt-2 text-xs">' + esc(s.sendMsg) + '</div>' : '') +
      (p && p.ok
        ? '<div class="mt-3 overflow-auto rounded-lg border border-line">' +
          '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
          '<tr><th class="px-3 py-1">step</th><th class="px-3 py-1">command</th>' +
          '<th class="px-3 py-1">why</th></tr></thead><tbody>' + steps + '</tbody></table></div>' +
          '<div class="mt-2 text-[11px] text-muted">' + esc(p.note || '') + '</div>'
        : (p && p.error ? '<div class="mt-2 text-xs text-rose-600">' + esc(p.error) + '</div>' : '')) +
      '</div>';
  }

  function render() {
    if (!root) return;
    root.innerHTML =
      '<div class="p-4">' +
      '<div class="flex items-center justify-between">' +
      '<div><h2 class="text-base font-semibold">Identity</h2>' +
      '<p class="mt-0.5 text-sm text-muted">The IDENTITY system — who is present. ' +
      '<span class="mono">who</span> = session_id + worker_id (both required); ' +
      '<span class="mono">what</span> = workflow_id; <span class="mono">chat_id</span> is the workflow\'s output.</p></div>' +
      '<button id="identity-refresh" class="rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">Refresh</button>' +
      '</div>' +
      '<div class="mt-4">' +
      (s.tab === 'detail' ? detailHtml()
        : s.tab === '5w1h' ? joinHtml()
        : s.tab === 'confirm' ? confirmHtml()
        : s.tab === 'send' ? sendHtml()
        : listHtml()) +
      '</div></div>';

    const rb = root.querySelector('#identity-refresh');
    if (rb) rb.addEventListener('click', () => {
      if (s.tab === 'confirm') { refreshConfirms(); toast('Confirms refreshed'); }
      else { refresh(); toast('Identity refreshed'); }
    });
    const ch = root.querySelector('#send-channel');
    if (ch) ch.addEventListener('change', () => {
      s.sendChannel = ch.value; s.sendArmed = false; render();
    });
    const pb = root.querySelector('#send-plan');
    if (pb) pb.addEventListener('click', planSend);
    const eb = root.querySelector('#send-execute');
    if (eb) eb.addEventListener('click', executeSend);
    const jb = root.querySelector('#identity-join');
    if (jb) jb.addEventListener('click', () => {
      const d = s.detail && s.detail.identity;
      if (d) openJoin(d.identity_key);
    });
    root.querySelectorAll('[data-identity-key]').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.getAttribute('data-identity-key')));
    });
  }

  render();
  if (!s.loaded) refresh();
  if (s.tab === 'confirm' && !s.confirmsLoaded) refreshConfirms();
  return { render, refresh, openDetail, openJoin, refreshConfirms };
}
