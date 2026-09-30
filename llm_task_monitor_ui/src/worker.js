/**
 * worker.js — the WORKER system page. Route: /llm-tasks/worker
 *
 * The user (2026-09-23):
 *   "problem is worker is unqiue system, and identity is another system"
 *   "have the worker ui at /llm-tasks/worker and /llm-tasks/identity"
 *
 * SSOT = /api/worker/list + /api/worker/get
 *
 * WHAT A WORKER IS, and what it is NOT (measured, not assumed):
 *   * a worker is a row in `worker_registry`, unique by `worker_key`
 *   * it is NOT `workers` — that is a HEARTBEAT NODE table whose only writer
 *     hardcodes `openclaw_worker_01` (`worker_heartbeat_service.py:38`). It
 *     answers "is the process alive", not "who is this worker".
 *   * it is NOT `consultant_team` — that is the consultant team.
 *
 * The page shows the register, and for each worker its 5W1H bindings, because
 * the JOIN to an identity is 5W1H and not a column.
 */

export const TABS = [
  // THE WORKER LIST IS THE SESSIONS. The user (2026-09-24):
  //   "this is not worker, this is who have identity to have task for"
  //   "worker = environment + identity + session"
  // MEASURED: `worker_registry` (6 rows) is a CAPABILITY EXECUTOR table
  // (`capability_ref` / `fallback_order`), NOT a worker. `identity_registry`
  // (53 rows) holds session + environment + identity -- THAT is the worker.
  // THE ASSIGNEE PICK. The user (2026-09-24):
  //   "environment setting Online + role-> onclick, so he will the one
  //    (identity) to have the task"
  //   "ui is wrong design , it should for role with enviornment not worker
  //    with enviornment"
  // The axis is role x environment, NOT session: a session list answers "which
  // sessions exist"; the question that decides whether a task can be given is
  // "which ROLE, in which ENVIRONMENT, is UP".
  { id: 'assignee', label: 'Assignee (role x env)' },
  // THE THREE-STEP PICK. The user (2026-09-24):
  //   "path -> .../evidence/step1_enviornment"
  //   "+ UI -> .../evidence/step2_worker"  "show all the LLM for user to select"
  //   "+ UI -> .../evidence/step3_tools"
  //   "show chat center / Task Center / QC Center (new) for user to select"
  //   "with bg-color : blue, other bg-color without"  "is tips for user,"
  { id: 'list', label: 'Step 1 — Environment' },
  { id: 'step2', label: 'Step 2 — LLM' },
  { id: 'step3', label: 'Step 3 — Tools' },
  { id: 'capability', label: 'Capability Executors' },
  { id: 'detail', label: 'Detail' },
  { id: 'right', label: 'Right / Edge' },
];

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

// THE STEP EVIDENCE TABLE IS SHARED. THE HUMAN (2026-09-26): the SAME pop-up is
// opened from the step-1 Environment page AND from the Playwright page's
// `step proof` button. MEASURED: the markup used to live HERE, so a second copy
// in `playwright.js` would be TWO renderings of ONE fact. It is extracted into
// `step-evidence.js` and imported by both pages.
import { stepEvidenceBody } from './step-evidence.js';

// The environment cell. `environments` is a LIST (a worker may run in more than
// one), so it is joined; an EMPTY list renders `—` rather than a blank cell.
function envCell(w) {
  const list = Array.isArray(w.environments) ? w.environments : [];
  if (!list.length) return '<span class="text-muted">—</span>';
  return list.map((e) =>
    '<span class="rounded-full border border-line px-2 py-1 mono text-[11px]">' +
    esc(e) + '</span>').join(' ');
}

export function mountWorker(root, state, helpers) {
  const toast = (helpers && helpers.toast) || (() => {});
  const s = state.worker || (state.worker = {
    tab: 'list', rows: [], loaded: false, msg: '', detail: null,
    rights: null, rightsLoaded: false, rightsMsg: '', workerModes: null,
    capRows: [], capLoaded: false, capMsg: '',
    // THE ASSIGNEE PICK. The user: "environment setting Online + role->
    // onclick, so he will the one (identity) to have the task".
    cands: [], candLoaded: false, candMsg: '', selected: null,
    // THE THREE-STEP PICK. The user:
    //   "onclick LLM = submit -> step 3"
    //   "with bg-color : blue, other bg-color without"
    flow: null, flowTips: null, flowSteps: [], flowLoaded: false,
    flowMsg: '',
    // THE POPUP. The user (2026-09-25):
    //   "onclick = detail for role"
    //   "pop-up for enviornment can edit to update the tabke too"
    //   "same function for step 2 , + buttom to + LLM"
    // `type` is 'env' (step 1) or 'llm' (step 2); `data` is the API payload.
    popup: null, popupMsg: '',
    // Channel status is a CHANNEL fact. It is NOT stored on the environment
    // page. THE HUMAN (2026-09-25): "this is enviornment element not channel
    // element" / "they are different, don't mix up".
  });

  // THE ASSIGNEE CANDIDATES: role x environment, each with its ENVIRONMENT
  // status. The user: "ui is wrong design , it should for role with enviornment
  // not worker with enviornment".
  async function refreshCandidates() {
    try {
      const res = await fetch('/api/assignee/candidates');
      const data = await res.json();
      s.cands = data.candidates || [];
      s.selected = data.selected || null;
      s.candMsg = data.ok ? '' : (data.error || 'assignee API error');
      s.candLoaded = true;
    } catch (e) {
      s.candMsg = 'Assignee API unavailable: ' + (e.message || e);
      s.candLoaded = true;
    }
    render();
  }

  // PICK an identity as the task assignee. The server REFUSES an unknown
  // identity, so a refusal is shown rather than silently ignored.
  async function pickAssignee(identityId) {
    try {
      const res = await fetch('/api/assignee/select', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identity_id: Number(identityId) }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        toast('Refused: ' + (data.why || data.error || ('HTTP ' + res.status)));
        return;
      }
      toast(data.why || 'Assignee selected');
      await refreshCandidates();
    } catch (e) {
      toast('Assignee API unavailable: ' + (e.message || e));
    }
  }

  // THE ENVIRONMENT LIST. The user (2026-09-24):
  //   "worker ID -> enviornment_id from working_enviornment"
  //   "remove llm / name / session"
  // MEASURED: `/api/environment/list` returns ONE row per `working_environment`,
  // keyed by `environment_id`. The 53 identities collapse into these rows, so
  // the page no longer carries a session id at all.
  async function refresh() {
    try {
      const res = await fetch('/api/environment/list');
      const data = await res.json();
      s.rows = data.rows || [];
      s.msg = data.ok ? '' : (data.error || 'environment API error');
      s.loaded = true;
    } catch (e) {
      s.msg = 'Environment API unavailable: ' + (e.message || e);
      s.loaded = true;
    }
    // No channel fetch. THE HUMAN (2026-09-25): the column on this page is an
    // environment element. Channel status stays on the channel, not here.
    render();
  }

  // The CAPABILITY EXECUTORS (`worker_registry`). Kept, because it is a real
  // register -- it answers "who CAN do this capability", which is NOT "which
  // worker".
  async function refreshCapability() {
    try {
      const res = await fetch('/api/worker/list');
      const data = await res.json();
      s.capRows = data.workers || [];
      s.capMsg = data.ok ? '' : (data.error || 'worker API error');
      s.capLoaded = true;
    } catch (e) {
      s.capMsg = 'Worker API unavailable: ' + (e.message || e);
      s.capLoaded = true;
    }
    render();
  }

  /**
   * THE EDGE. Read from /api/mode/rights — the SAME rows the gate reads, so the
   * published edge cannot be a comfortable lie. The SIX rights are the gate's
   * own list; the sixth (`how_to_proceed`) is the HELP, and it is the point of
   * this page: the worker reads the way forward BEFORE meeting a wall.
   */
  async function refreshRights() {
    try {
      const res = await fetch('/api/mode/rights');
      const data = await res.json();
      s.rights = data;
      s.rightsMsg = data.ok ? '' : (data.error || 'rights API error');
      s.rightsLoaded = true;
    } catch (e) {
      s.rightsMsg = 'Rights API unavailable: ' + (e.message || e);
      s.rightsLoaded = true;
    }
    render();
  }

  async function loadWorkerModes(key) {
    try {
      const res = await fetch('/api/worker/modes?worker_key=' + encodeURIComponent(key));
      s.workerModes = await res.json();
    } catch (e) {
      s.workerModes = { ok: false, error: String(e) };
    }
    render();
  }

  async function openDetail(key) {
    try {
      const res = await fetch('/api/worker/get?worker_key=' + encodeURIComponent(key));
      s.detail = await res.json();
    } catch (e) {
      s.detail = { ok: false, error: String(e) };
    }
    s.tab = 'detail';
    render();
  }

  // THE THREE-STEP PICK. The user (2026-09-24):
  //   "onclick LLM = submit -> step 3"
  //   "when step 1 = vscode, step 2 LLM for ... with bg-color : blue"
  //   "when step 2 = deepseekSeek V4.1, step 3 task center with bg-color : blue"
  //   "is tips for user,"
  //
  // THE BLUE HIGHLIGHT IS A TIP, AND IT IS DERIVED. A tip typed into the UI
  // would be a second copy of a fact the registers already hold, so the server
  // computes `tipped` from `llm_model` and `tool_center.role_key`.
  async function refreshFlow() {
    try {
      const res = await fetch('/api/pick/flow');
      const data = await res.json();
      s.flow = data.flow || null;
      s.flowTips = data.tips || null;
      s.flowSteps = data.steps || [];
      s.flowMsg = data.ok ? '' : (data.error || 'pick flow API error');
      s.flowLoaded = true;
    } catch (e) {
      s.flowMsg = 'Pick flow API unavailable: ' + (e.message || e);
      s.flowLoaded = true;
    }
    render();
  }

  // SET one step. The server REFUSES an unknown row, so a refusal is shown
  // rather than silently ignored.
  async function pickStep(step, value) {
    try {
      const res = await fetch('/api/pick/step', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ step: Number(step), value: value }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        toast('Refused: ' + (data.error || data.why || ('HTTP ' + res.status)));
        return;
      }
      toast('Step ' + step + ' picked');
      await refreshFlow();
    } catch (e) {
      toast('Pick flow API unavailable: ' + (e.message || e));
    }
  }

  // A TIP cell: BLUE when tipped, plain otherwise. The user:
  //   "with bg-color : blue, other bg-color without"
  function tipClass(tipped) {
    return tipped
      ? 'bg-blue-100 border-blue-400 text-blue-900'
      : 'bg-panel border-line text-ink';
  }

  // ================= THE POPUP (2026-09-25) =================
  // The user:
  //   "onclick = detail for role"
  //   "+ field action for onlick to step 2"
  //   "pop-up for enviornment can edit to update the tabke too"
  //   "same function for step 2 , + buttom to + LLM"
  //
  // ONE popup, TWO types:
  //   * `env` -- the step-1 environment: EDIT its 3-part path (updates the
  //     table via /api/environment/update) + the ROLE DETAIL (the full
  //     `role_registry` vocabulary; click a role to DECLARE the pair, click a
  //     declared pair to remove it) + the ACTION to step 2.
  //   * `llm` -- the step-2 model: EDIT the row (updates the table via
  //     /api/llm_model/update) or ADD a new one (`+ LLM`, via
  //     /api/llm_model/add) + the ACTION to step 3.
  //
  // The popup is a DETAIL/EDIT layer on top of the three-step pick: the pick
  // semantics are untouched, the popup just makes the DECISIONS visible and
  // editable where the user asked for them.

  async function openEnvPopup(environmentId, field) {
    s.popup = { type: 'env', environment_id: Number(environmentId),
                field: field || '', data: null, steps: null, msg: 'Loading…' };
    render();
    // THE EVIDENCE POP-UP READS THE STEP TABLES, NOT THE TARGET LIST.
    // THE HUMAN (2026-09-26): "evidence pop-up re-design / view is STEP for how
    // to process with evidence proof / table will be / STEP 1 instruction method
    // (hotkey / coordinate) evidence". MEASURED BEFORE: this branch rendered
    // `d.configure.rows` filtered to `field_type === 'coordinate'` -- a TARGET
    // list, which answers "which coordinate targets have a picture?", not "what
    // are the STEPS, how is each driven, and what is its proof?".
    //
    // The step data already exists (`playwright_step` + `playwright_step_run`),
    // so the pop-up is RE-POINTED at it. The other fields keep `role_detail`.
    const isEvidence = String(field || '') === 'evidence';
    const url = isEvidence
      ? '/api/environment/evidence_steps?environment_id=' + Number(environmentId)
      : '/api/environment/role_detail?environment_id=' + Number(environmentId);
    try {
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok || !data.ok) {
        s.popup.msg = data.error || ('HTTP ' + res.status);
      } else if (isEvidence) {
        s.popup.steps = data;
        s.popup.msg = '';
      } else {
        s.popup.data = data;
        s.popup.msg = '';
      }
    } catch (e) {
      s.popup.msg = (isEvidence ? 'evidence_steps' : 'role_detail') +
        ' API unavailable: ' + (e.message || e);
    }
    render();
  }

  async function openLlmPopup(llmId) {
    // `llmId` null/0 = ADD mode (the `+ LLM` button).
    s.popup = { type: 'llm', llm_id: llmId ? Number(llmId) : null,
                data: null, msg: 'Loading…' };
    render();
    if (!llmId) {
      s.popup.data = { model: null };
      s.popup.msg = '';
      render();
      return;
    }
    try {
      const res = await fetch('/api/llm_model/list');
      const data = await res.json();
      if (!res.ok || !data.ok) {
        s.popup.msg = data.error || ('HTTP ' + res.status);
      } else {
        const m = (data.models || []).find(
          (x) => Number(x.id) === Number(llmId));
        if (!m) {
          s.popup.msg = 'llm_id ' + llmId + ' is not in llm_model';
        } else {
          s.popup.data = { model: m };
          s.popup.msg = '';
        }
      }
    } catch (e) {
      s.popup.msg = 'llm_model API unavailable: ' + (e.message || e);
    }
    render();
  }

  function closePopup() {
    s.popup = null;
    render();
  }

  // SAVE the environment edit. The server REFUSES a missing part or an unknown
  // environment, so a refusal is shown, not silently ignored.
  async function saveEnvEdit() {
    const p = s.popup;
    if (!p || p.type !== 'env') return;
    const body = {
      environment_id: p.environment_id,
      kind: (root.querySelector('#pop-env-kind') || {}).value,
      product: (root.querySelector('#pop-env-product') || {}).value,
      surface: (root.querySelector('#pop-env-surface') || {}).value,
    };
    try {
      const res = await fetch('/api/environment/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        toast('Refused: ' + (data.error || ('HTTP ' + res.status)));
        return;
      }
      toast('Environment updated: ' + (data.display || ''));
      closePopup();
      await refresh();
    } catch (e) {
      toast('environment update unavailable: ' + (e.message || e));
    }
  }

  // DECLARE / REMOVE a role x environment pair from the popup. A role is a
  // DECISION the user makes here; the server REFUSES an unknown side.
  async function toggleRole(roleKey) {
    const p = s.popup;
    if (!p || p.type !== 'env') return;
    const declared = ((p.data && p.data.pairs && p.data.pairs.roles) || [])
      .some((r) => String(r.role_key) === String(roleKey));
    const url = declared ? '/api/role_environment/remove'
                         : '/api/role_environment/declare';
    try {
      const res = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_key: roleKey,
                                environment_id: p.environment_id }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        toast('Refused: ' + (data.error || ('HTTP ' + res.status)));
        return;
      }
      toast(declared ? 'Pair removed: ' + roleKey : 'Pair declared: ' + roleKey);
      await openEnvPopup(p.environment_id);
      await refresh();
    } catch (e) {
      toast('role_environment API unavailable: ' + (e.message || e));
    }
  }

  // SAVE the LLM edit, or ADD a new model (`+ LLM`).
  async function saveLlmEdit() {
    const p = s.popup;
    if (!p || p.type !== 'llm') return;
    const g = (id) => { const el = root.querySelector(id); return el ? el.value : ''; };
    const lc = (id) => { const el = root.querySelector(id); return el ? el.checked : false; };
    const body = {
      name: g('#pop-llm-name'),
      model_id: g('#pop-llm-model-id'),
      local: lc('#pop-llm-local'),
      description: g('#pop-llm-desc'),
    };
    const isAdd = !p.llm_id;
    if (isAdd) {
      body.llm_id = null;
    } else {
      body.llm_id = p.llm_id;
    }
    try {
      const res = await fetch(isAdd ? '/api/llm_model/add'
                                    : '/api/llm_model/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        toast('Refused: ' + (data.error || ('HTTP ' + res.status)));
        return;
      }
      toast(isAdd ? 'LLM added: ' + (data.name || '')
                  : 'LLM updated: ' + (data.name || ''));
      const newId = data.id != null ? data.id : p.llm_id;
      closePopup();
      await refreshFlow();
      if (s.tab === 'step2') render();
      return newId;
    } catch (e) {
      toast('llm_model API unavailable: ' + (e.message || e));
      return null;
    }
  }

  // THE ENVIRONMENT POPUP: edit + role detail + the action to step 2.
  function envPopupHtml() {
    const p = s.popup;
    if (!p) return '';
    // THE EVIDENCE POP-UP HAS ITS OWN PAYLOAD. THE HUMAN (2026-09-26): the
    // Evidence view is the STEP table, which comes from
    // `/api/environment/evidence_steps` -- NOT from `role_detail`. So when the
    // field is `evidence` the data is `p.steps`, and the environment row is
    // synthesised from the id the pop-up was opened with. Without this the
    // guard below would render "Loading…" forever, because `p.data` is null.
    const isEvidence = String(p.field || '') === 'evidence';
    const d = p.data || (isEvidence && p.steps
      ? { environment: { environment_id: p.environment_id } } : null);
    if (!d) {
      return '<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" ' +
        'data-popup-close="1">' +
        '<div class="w-full max-w-lg rounded-xl border border-line bg-panel p-4">' +
        '<h3 class="text-sm font-semibold">Environment #' +
        esc(p.environment_id) + '</h3>' +
        '<p class="mt-2 text-sm text-muted">' + esc(p.msg || 'Loading…') + '</p>' +
        '<button data-popup-close="1" class="mt-3 rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">Close</button>' +
        '</div></div>';
    }
    const env = d.environment || {};
    const declared = new Set(((d.pairs && d.pairs.roles) || [])
      .map((r) => String(r.role_key)));
    const rolePills = (d.role_registry || []).map((r) => {
      const on = declared.has(String(r.role_key));
      return '<button type="button" data-toggle-role="' + esc(r.role_key) + '" ' +
        'class="rounded-lg border p-2 text-left transition hover:opacity-90 ' +
        (on ? 'bg-blue-100 border-blue-400 text-blue-900'
           : 'bg-panel border-line text-ink') + '">' +
        '<div class="flex items-center justify-between gap-2">' +
        '<span class="text-xs font-semibold">' + esc(r.role_key) + '</span>' +
        '<span class="text-[11px]">' +
        (on ? 'DECLARED · click to remove' : 'click to declare') + '</span></div>' +
        '<div class="mt-1 text-[11px] opacity-80">' + esc(r.definition || '') +
        '</div></button>';
    }).join('');
    const field = (id, label, val) =>
      '<label class="block text-xs"><span class="text-muted">' + label +
      '</span><input id="' + id + '" value="' + esc(val || '') + '" ' +
      'class="mt-0.5 w-full rounded-lg border border-line bg-soft/40 px-2 py-1 text-xs"></label>';
    const kv = (label, value, cls) =>
      '<div class="flex gap-2 py-0.5 text-xs"><span class="w-28 shrink-0 ' +
      'text-muted">' + label + '</span><span class="' + (cls || '') + '">' +
      value + '</span></div>';
    const statusCls = String(env.status) === 'RUNNING'
      ? 'text-emerald-700 font-semibold'
      : String(env.status) === 'STOPPED' ? 'text-rose-700 font-semibold'
      : 'text-muted';
    // ONE FIELD PER POPUP.
    // THE HUMAN (2026-09-25): "user onlcick to value = configure/environment/
    // role/env/status / that means user is looking for this value!! / not to
    // have all !!!!". So the popup shows ONLY the field the user clicked.
    const f = String(p.field || '');
    let body = '';
    if (f === 'role') {
      body =
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">environment_id=' +
        esc(env.environment_id) + ' → role</div>' +
        kv('role', esc(env.role_key || 'UNASSIGNED'),
           String(env.role_key) === 'UNASSIGNED' ? 'text-muted' : 'font-semibold') +
        kv('declared', esc((env.role_declared || []).join(', ') || 'none')) +
        '</div>' +
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">Declare a role</div>' +
        '<p class="mt-0.5 text-[11px] text-muted">Vocabulary = ' +
        '<span class="mono">role_registry</span>. Click to declare, click a ' +
        'declared pair to remove it.</p>' +
        '<div class="mt-2 grid gap-2">' + rolePills + '</div>' +
        '</div>';
    } else if (f === 'environment') {
      body =
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">environment_id=' +
        esc(env.environment_id) + ' → environment</div>' +
        kv('kind', esc(env.kind || '-')) +
        kv('product', esc(env.product || '-')) +
        kv('surface', esc(env.surface || '-')) +
        kv('display', esc(env.display || '-')) +
        '</div>' +
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">Edit the environment (updates the table)</div>' +
        '<div class="mt-2 grid grid-cols-3 gap-2">' +
        field('pop-env-kind', 'kind', env.kind) +
        field('pop-env-product', 'product', env.product) +
        field('pop-env-surface', 'surface', env.surface) +
        '</div>' +
        '<button data-save-env="1" class="mt-2 rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">Save</button>' +
        (p.msg ? '<span class="ml-2 text-xs text-rose-600">' + esc(p.msg) + '</span>' : '') +
        '</div>';
    } else if (f === 'coordinate') {
      // THE COORDINATE TARGETS OF THIS ENVIRONMENT ONLY.
      // THE HUMAN (2026-09-25): "configure have name now -> rename ->
      // coordinate" and "both group_id group / type remove from the pop up
      // table and for coordinate, + center x,y".
      const coRows = ((d.configure && d.configure.rows) || [])
        .filter((r) => String(r.field_type) !== 'hotkey');
      // THE EVID IMAGE PER TARGET.
      // THE HUMAN (2026-09-25): "template_id | image name | label |
      // x1,y1 → x2,y2 | center x,y" / "image format =
      // EVID-task_proof-20260921-195257_full and save at
      // C:\projects\agent_system\evidence_final" / "can mouse over to have the
      // large image by 600*600 with info for file location".
      //
      // The image is looked up by the EVID naming convention in
      // `evidence_final/`. A target with NO image shows "no evidence yet" -- a
      // missing proof must be VISIBLE, not a broken image.
      const evid = (d.evidence && d.evidence.rows) || [];
      const evidByName = {};
      evid.forEach((e) => { evidByName[String(e.name)] = e; });
      const coBody = coRows.length
        ? '<div class="mt-2 overflow-x-auto"><table class="w-full text-left">' +
          '<thead class="bg-soft/60 text-[11px] text-muted"><tr>' +
          '<th class="px-2 py-1">template_id</th>' +
          '<th class="px-2 py-1">image</th>' +
          '<th class="px-2 py-1">label</th>' +
          '<th class="px-2 py-1">x1,y1 → x2,y2</th>' +
          '<th class="px-2 py-1">center x,y</th></tr></thead><tbody>' +
          coRows.map((r) => {
            const v = r.value;
            const rect = (v && typeof v === 'object')
              ? '<span class="mono">' + esc(v.x1) + ',' + esc(v.y1) + ' → ' +
                esc(v.x2) + ',' + esc(v.y2) + '</span>'
              : '<span class="rounded-full bg-amber-100 px-1.5 py-1 ' +
                'text-[10px] text-amber-800">NA</span>';
            const ctr = (v && typeof v === 'object')
              ? '<span class="mono">' + esc(v.cx) + ',' + esc(v.cy) + '</span>'
              : '<span class="text-muted">—</span>';
            const ev = evidByName[String(r.name)] || {};
            const imgName = ev.image_name || '';
            const imgUrl = ev.image_url || '';
            const imgPath = ev.image_path || '';
            // THE IMAGE CELL. Hovering shows the 50x50 preview + the file
            // location, so the proof can be checked without leaving the page.
            // THE HUMAN (2026-09-26): "image name -> image" and the pop-up
            // preview image size = 50*50.
            const imgCell = imgName
              ? '<span class="evid-hover inline-block">' +
                '<span class="mono text-[10px] text-blue-700 underline ' +
                'cursor-help">' + esc(imgName) + '</span>' +
                '<span class="evid-pop">' +
                '<img src="' + esc(imgUrl) + '" alt="" ' +
                'style="width:50px;height:50px;object-fit:contain" ' +
                'class="rounded border border-line bg-black">' +
                '<span class="mt-1 block mono text-[10px] text-muted ' +
                'break-all">' + esc(imgPath) + '</span>' +
                '</span></span>'
              : '<span class="rounded-full bg-amber-100 px-1.5 py-1 ' +
                'text-[10px] text-amber-800">no evidence yet</span>';
            return '<tr class="border-b border-line">' +
              '<td class="px-2 py-1 mono text-[11px]">' +
              esc(r.template_id) + '</td>' +
              '<td class="px-2 py-1 text-[11px]">' + imgCell + '</td>' +
              '<td class="px-2 py-1 mono text-[11px]">' + esc(r.name) +
              '</td>' +
              '<td class="px-2 py-1 text-[11px]">' + esc(r.label || '') +
              '</td>' +
              '<td class="px-2 py-1 text-[11px]">' + rect + '</td>' +
              '<td class="px-2 py-1 text-[11px]">' + ctr + '</td>' +
              '</tr>';
          }).join('') + '</tbody></table></div>'
        : '<p class="mt-1 text-[11px] text-muted">This environment declares ' +
          'no coordinate target.</p>';
      body =
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">environment_id=' +
        esc(env.environment_id) + ' → coordinate</div>' +
        '<p class="mt-0.5 text-[11px] text-muted">The coordinate targets of ' +
        '<span class="mono">environment_id=' + esc(env.environment_id) +
        '</span> ONLY. Source: ' +
        esc((d.configure && d.configure.source) || 'target_registry') + '</p>' +
        coBody +
        '</div>';
    } else if (f === 'evidence') {
      // THE STEP TABLE — how this environment is processed, and its proof.
      // THE HUMAN (2026-09-26): "evidence pop-up re-design / view is STEP for
      // how to process with evidence proof / table will be / STEP 1 instruction
      // method (hotkey / coordinate) evidence / STEP 2 ......".
      //
      // MEASURED BEFORE: this branch rendered a TARGET list
      // (`template_id | name | label | image`) from `d.configure.rows`. That
      // answers "which coordinate targets have a picture?" — NOT the human's
      // question, which is "what are the STEPS, how is each driven, and what is
      // its proof?".
      //
      // THE MARKUP IS SHARED, NOT OWNED HERE. THE HUMAN (2026-09-26): the SAME
      // pop-up is opened from the Playwright page's `step proof` button, so the
      // table lives in `step-evidence.js` and BOTH pages import it. A second
      // copy here would be two renderings of one fact.
      body = stepEvidenceBody(env.environment_id, s.popup.steps || {});
    } else if (f === 'status') {
      body =
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">environment_id=' +
        esc(env.environment_id) + ' → status</div>' +
        kv('status', esc(env.status || 'UNKNOWN'), statusCls) +
        kv('app', esc(env.status_app || '-')) +
        kv('instances', esc(env.status_count == null ? '-' : env.status_count)) +
        kv('source', esc(env.status_source || 'windows_task_manager:environment_id')) +
        '<div class="mt-1 text-[11px] text-muted">' + esc(env.status_why || '') +
        '</div>' +
        '</div>';
    } else if (f === 'hotkey') {
      // THE HOTKEY TARGETS OF THIS ENVIRONMENT ONLY.
      // THE HUMAN (2026-09-25): "onclick -> hotkey * enviornment_id show, not
      // hotkey * all enviornment_id show!!!".
      //
      // The rows come from `for_environment(environment_id)`, which is already
      // scoped to the groups THIS environment owns, so a hotkey owned by
      // another environment can never appear here.
      const hkRows = ((d.configure && d.configure.rows) || [])
        .filter((r) => String(r.field_type) === 'hotkey');
      const hkBody = hkRows.length
        ? '<div class="mt-2 overflow-x-auto"><table class="w-full text-left">' +
          '<thead class="bg-soft/60 text-[11px] text-muted"><tr>' +
          '<th class="px-2 py-1">template_id</th>' +
          '<th class="px-2 py-1">name</th>' +
          '<th class="px-2 py-1">label</th>' +
          '<th class="px-2 py-1">hotkey</th></tr></thead><tbody>' +
          hkRows.map((r) => {
            const v = r.value;
            const hk = (v && typeof v === 'object' && v.hotkey)
              ? '<span class="mono">' + esc(v.hotkey) + '</span>'
              : '<span class="rounded-full bg-amber-100 px-1.5 py-1 ' +
                'text-[10px] text-amber-800">NA</span>';
            return '<tr class="border-b border-line">' +
              '<td class="px-2 py-1 mono text-[11px]">' +
              esc(r.template_id) + '</td>' +
              '<td class="px-2 py-1 mono text-[11px]">' + esc(r.name) +
              '</td>' +
              '<td class="px-2 py-1 text-[11px]">' + esc(r.label || '') +
              '</td>' +
              '<td class="px-2 py-1 text-[11px]">' + hk + '</td>' +
              '</tr>';
          }).join('') + '</tbody></table></div>'
        : '<p class="mt-1 text-[11px] text-muted">This environment declares ' +
          'no hotkey target.</p>';
      body =
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">environment_id=' +
        esc(env.environment_id) + ' → hotkey</div>' +
        '<p class="mt-0.5 text-[11px] text-muted">The hotkey targets of ' +
        '<span class="mono">environment_id=' + esc(env.environment_id) +
        '</span> ONLY — not every environment\'s.</p>' +
        hkBody +
        '</div>';
    } else {
      // No field named: show the DOORS, so the user picks one.
      body =
        '<div class="mt-3 rounded-lg border border-line p-3">' +
        '<div class="text-xs font-semibold">environment_id=' +
        esc(env.environment_id) + '</div>' +
        '<p class="mt-0.5 text-[11px] text-muted">Pick a value to look at.</p>' +
        '<div class="mt-2 flex flex-wrap gap-2">' +
        ['role', 'environment', 'coordinate', 'status', 'hotkey',
         'evidence'].map((k) =>
          '<button type="button" data-env-field="' + k + '" data-env-id="' +
          esc(env.environment_id) + '" ' +
          'class="rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">' +
          esc(k) + '</button>').join('') +
        '</div></div>';
    }
    // THE POP-UP WIDTH FITS ITS CONTENT.
    // THE HUMAN (2026-09-26): "pop-up width set be all content show without
    // scroll bar". `w-max` sizes the panel to its widest child (the coordinate
    // table), so the table is never clipped and no horizontal scroll bar
    // appears; `max-w-[95vw]` is the only cap, for a very wide table.
    return '<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" ' +
      'data-popup-close="1">' +
      '<div class="max-h-[90vh] w-max max-w-[95vw] overflow-auto rounded-xl border border-line bg-panel p-4">' +
      '<div class="flex items-center justify-between">' +
      '<h3 class="text-sm font-semibold">Environment #' + esc(env.environment_id) +
      (f ? ' <span class="text-muted font-normal">→ ' + esc(f) + '</span>' : '') +
      '</h3>' +
      '<button data-popup-close="1" class="rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">Close</button>' +
      '</div>' +
      body +
      '</div></div>';
  }

  // THE LLM POPUP: edit the row (or add, in `+ LLM` mode) + the action to
  // step 3. The user: "same function for step 2 , + buttom to + LLM".
  function llmPopupHtml() {
    const p = s.popup;
    if (!p) return '';
    const m = (p.data && p.data.model) || null;
    const isAdd = !p.llm_id;
    const field = (id, label, val) =>
      '<label class="block text-xs"><span class="text-muted">' + label +
      '</span><input id="' + id + '" value="' + esc(val || '') + '" ' +
      'class="mt-0.5 w-full rounded-lg border border-line bg-soft/40 px-2 py-1 text-xs"></label>';
    return '<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/40" ' +
      'data-popup-close="1">' +
      '<div class="max-h-[85vh] w-full max-w-lg overflow-auto rounded-xl border border-line bg-panel p-4">' +
      '<div class="flex items-center justify-between">' +
      '<h3 class="text-sm font-semibold">' +
      (isAdd ? 'Add LLM' : 'LLM #' + esc(p.llm_id) +
        ' <span class="text-muted font-normal">' + esc(m ? m.name : '') + '</span>') +
      '</h3>' +
      '<button data-popup-close="1" class="rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">Close</button>' +
      '</div>' +
      '<div class="mt-3 rounded-lg border border-line p-3">' +
      '<div class="text-xs font-semibold">' +
      (isAdd ? 'New model (updates the table)' : 'Edit the model (updates the table)') +
      '</div>' +
      '<div class="mt-2 grid grid-cols-2 gap-2">' +
      field('pop-llm-name', 'name', m ? m.name : '') +
      field('pop-llm-model-id', 'model_id', m ? m.model_id : '') +
      '</div>' +
      '<label class="mt-2 flex items-center gap-2 text-xs">' +
      '<input id="pop-llm-local" type="checkbox"' +
      (m && (m.local === 1 || m.local === true) ? ' checked' : '') + '>' +
      '<span class="text-muted">local (on this machine)</span></label>' +
      '<div class="mt-2">' + field('pop-llm-desc', 'description', m ? m.description : '') + '</div>' +
      '<div class="mt-2 flex items-center gap-2">' +
      '<button data-save-llm="1" class="rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">' +
      (isAdd ? 'Add LLM' : 'Save') + '</button>' +
      (!isAdd ? '<button data-popup-step3="' + esc(p.llm_id) + '" ' +
        'class="rounded-lg bg-blue-100 border border-blue-400 px-3 py-1 text-xs text-blue-900 hover:opacity-90">Submit → Step 3</button>' : '') +
      (p.msg ? '<span class="text-xs text-rose-600">' + esc(p.msg) + '</span>' : '') +
      '</div>' +
      '</div>' +
      '</div></div>';
  }

  function popupHtml() {
    if (!s.popup) return '';
    return s.popup.type === 'env' ? envPopupHtml() : llmPopupHtml();
  }

  function step2Html() {
    const t = (s.flowTips && s.flowTips.step2) || null;
    const models = (t && t.models) || [];
    const picked = s.flow ? s.flow.llm_id : null;
    const env = (s.flowTips && s.flowTips.environment) || null;
    const head =
      '<div class="mb-3 rounded-xl border border-line bg-soft/50 p-3 text-sm">' +
      '<div class="flex items-center justify-between">' +
      '<span><span class="font-semibold">Step 2 — pick the LLM</span>' +
      (env ? '<span class="ml-2 text-muted">for ' + esc(env.display || '') +
        '</span>' : '') + '</span>' +
      // THE `+ LLM` BUTTON. The user (2026-09-25): "+ buttom to + LLM". It
      // opens the popup in ADD mode; the server REFUSES a duplicate.
      '<button type="button" data-open-llm-add="1" ' +
      'class="rounded-lg bg-blue-100 border border-blue-400 px-3 py-1 text-xs text-blue-900 hover:opacity-90">+ LLM</button>' +
      '</div>' +
      '<div class="mt-1 text-xs text-muted">A BLUE card is a TIP: the model is ' +
      'suited to the step-1 environment. Click a card for its DETAIL (edit the ' +
      'row, or submit and go to step 3).</div></div>';
    if (!models.length) {
      return head + '<p class="text-sm text-muted">No model is registered. ' +
        'SSOT = <span class="mono">llm_model</span>. Use <span class="mono">+ LLM</span> to add one.</p>';
    }
    const cards = models.map((m) => {
      const isPicked = picked != null && Number(picked) === Number(m.llm_id);
      return '<div class="rounded-xl border p-3 transition hover:opacity-90 ' +
        tipClass(m.tipped) + (isPicked ? ' ring-2 ring-accent' : '') + '">' +
        '<div class="flex items-center justify-between gap-2">' +
        '<button type="button" data-open-llm="' + esc(m.llm_id) + '" ' +
        'class="text-sm font-semibold text-left hover:underline">' +
        esc(m.name) + '</button>' +
        '<span class="text-xs">' + (m.tipped ? 'TIP' : '') +
        (isPicked ? ' · PICKED' : '') + '</span></div>' +
        '<div class="mono mt-0.5 text-xs opacity-80">' + esc(m.model_id) + '</div>' +
        '<div class="mt-1 text-xs opacity-80">' + esc(m.tip_why || '') +
        (m.role_key ? ' · role: ' + esc(m.role_key) : '') + '</div>' +
        '<button type="button" data-pick-llm="' + esc(m.llm_id) + '" ' +
        'class="mt-2 rounded-lg bg-blue-100 border border-blue-400 px-2 py-1 text-xs text-blue-900 hover:opacity-90">Submit → Step 3</button>' +
        '</div>';
    }).join('');
    return head + '<div class="grid gap-2 sm:grid-cols-2">' + cards + '</div>';
  }

  function step3Html() {
    const t = (s.flowTips && s.flowTips.step3) || null;
    const tools = (t && t.tools) || [];
    const picked = s.flow ? s.flow.tool_key : null;
    const llm = (s.flowTips && s.flowTips.llm) || null;
    const head =
      '<div class="mb-3 rounded-xl border border-line bg-soft/50 p-3 text-sm">' +
      '<span class="font-semibold">Step 3 — pick the tool</span>' +
      (llm ? '<span class="ml-2 text-muted">for ' + esc(llm.name) +
        (llm.role_key ? ' (role: ' + esc(llm.role_key) + ')' : '') +
        '</span>' : '') +
      '<div class="mt-1 text-xs text-muted">A BLUE card is a TIP: the tool ' +
      'serves the role the picked model is suited to. Click a card to submit.' +
      '</div></div>';
    if (!tools.length) {
      return head + '<p class="text-sm text-muted">No tool is registered. ' +
        'SSOT = <span class="mono">tool_center</span>.</p>';
    }
    const cards = tools.map((x) => {
      const isPicked = picked != null && String(picked) === String(x.tool_key);
      return '<button type="button" data-pick-tool="' + esc(x.tool_key) + '" ' +
        'class="w-full rounded-xl border p-3 text-left transition hover:opacity-90 ' +
        tipClass(x.tipped) + (isPicked ? ' ring-2 ring-accent' : '') + '">' +
        '<div class="flex items-center justify-between gap-2">' +
        '<span class="text-sm font-semibold">' + esc(x.name) + '</span>' +
        '<span class="text-xs">' + (x.tipped ? 'TIP' : '') +
        (isPicked ? ' · PICKED' : '') + '</span></div>' +
        '<div class="mt-0.5 text-xs opacity-80">role: ' + esc(x.role_key) +
        '</div>' +
        '<div class="mt-1 text-xs opacity-80">' + esc(x.tip_why || '') +
        '</div></button>';
    }).join('');
    return head + '<div class="grid gap-2 sm:grid-cols-3">' + cards + '</div>';
  }

  // THE ENVIRONMENT LIST. The user (2026-09-24):
  //   "path -> http://127.0.0.1:18765/llm-tasks/evidence/step1_enviornment"
  //   "and role still = UNASSIGNED and remove llm / name / session"
  //   "worker ID -> enviornment_id from working_enviornment"
  //
  // MEASURED DEFECT THIS FIXES: the page was a WORKER list -- 53 rows, one per
  // session. The user's unit is the ENVIRONMENT, and the 53 rows collapse to the
  // environments `working_environment` declares. So the key is `environment_id`,
  // and `name` / `session` / `llm` are GONE: `name` and `session` were both
  // derived from the SAME session id, and `llm` was UNASSIGNED for all 53.
  function listHtml() {
    if (!s.loaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.msg) return '<p class="text-sm text-rose-600">' + esc(s.msg) + '</p>';
    if (!s.rows.length) {
      return '<p class="text-sm text-muted">No environment declared yet. ' +
        'SSOT = <span class="mono">working_environment</span>.</p>';
    }
    const rows = s.rows.map((e) =>
      '<tr class="hover:bg-soft">' +
      // THE KEY IS `environment_id`, from `working_environment`. The user:
      //   "worker ID -> enviornment_id from working_enviornment"
      '<td class="px-3 py-2 mono text-xs">' + esc(e.environment_id) + '</td>' +
      // THE LOGO, its OWN column. THE HUMAN (2026-09-25): the column order is
      //   environment_id | logo | environment | role | status | hotkey |
      //   coordinate | action
      '<td class="px-3 py-2 text-xs">' + envLogo(e) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + envCell(e) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + roleCell(e) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + envStatusCell(e) + '</td>' +
      // THE HOTKEY BUTTON IS BLUE. THE HUMAN: "hotkey button = blue".
      '<td class="px-3 py-2 text-xs">' + hotkeyCell(e) + '</td>' +
      // THE COORDINATE BUTTON IS PINK. THE HUMAN: "coordinate button = pink".
      '<td class="px-3 py-2 text-xs">' + coordinateCell(e) + '</td>' +
      // THE EVIDENCE COLUMN. THE HUMAN (2026-09-26): "where is the Evidence
      // button? onclick = show" and the column order
      //   environment_id | logo | environment | role | status | hotkey |
      //   coordinate | Evidence | action
      // The DATA was already on the wire (`d.evidence.rows`); the DOOR was
      // missing. This is that door.
      '<td class="px-3 py-2 text-xs">' + evidenceCell(e) + '</td>' +
      // THE ACTION. The user (2026-09-25): "+ field action for onlick to step
      // 2". The ACTION is the explicit pick + go to step 2.
      '<td class="px-3 py-2 text-xs">' +
      '<button type="button" data-pick-env="' + esc(e.environment_id) + '" ' +
      'class="action-shimmer rounded-lg px-2 py-1 text-xs font-medium hover:opacity-90">Step 2 →</button>' +
      '</td>' +
      '</tr>').join('');
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">environment_id</th>' +
      '<th class="px-3 py-2">logo</th>' +
      '<th class="px-3 py-2">environment</th>' +
      '<th class="px-3 py-2">role</th>' +
      '<th class="px-3 py-2">status</th>' +
      '<th class="px-3 py-2">hotkey</th>' +
      '<th class="px-3 py-2">coordinate</th>' +
      // THE EVIDENCE HEADER. THE HUMAN (2026-09-26): the column order is
      //   environment_id | logo | environment | role | status | hotkey |
      //   coordinate | Evidence | action
      '<th class="px-3 py-2">Evidence</th>' +
      '<th class="px-3 py-2">action</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  }

  // THE EVIDENCE COLUMN. THE HUMAN (2026-09-26): "where is the Evidence
  // button? onclick = show".
  //
  // It is a DOOR, exactly like `coordinateCell` / `hotkeyCell`: it carries
  // `data-env-field="evidence"` + `data-env-id`, so the ONE delegated handler
  // opens the popup for THIS environment's evidence. AMBER, so it is not
  // confused with hotkey-blue or coordinate-pink.
  function evidenceCell(e) {
    return '<button type="button" data-env-field="evidence" ' +
      'data-env-id="' + esc(e.environment_id) + '" ' +
      'class="rounded-lg bg-amber-100 border border-amber-400 px-2 py-1 ' +
      'text-xs text-amber-900 hover:opacity-90" ' +
      'title="the EVID images of this environment only">' +
      'Evidence</button>';
  }

  // THE COORDINATE COLUMN. THE HUMAN (2026-09-25): "configure have name now ->
  // rename -> coordinate" and "coordinate button = pink".
  function coordinateCell(e) {
    return '<button type="button" data-env-field="coordinate" ' +
      'data-env-id="' + esc(e.environment_id) + '" ' +
      'class="rounded-lg bg-pink-100 border border-pink-400 px-2 py-1 ' +
      'text-xs text-pink-900 hover:opacity-90" ' +
      'title="the coordinate targets of this environment only">' +
      'coordinate</button>';
  }

  // THE HOTKEY COLUMN, its OWN button, BLUE.
  // THE HUMAN (2026-09-25): "hotkey has it own buttom not, don't need to mix
  // together!!" and "hotkey button = blue".
  function hotkeyCell(e) {
    return '<button type="button" data-env-field="hotkey" ' +
      'data-env-id="' + esc(e.environment_id) + '" ' +
      'class="rounded-lg bg-blue-100 border border-blue-400 px-2 py-1 ' +
      'text-xs text-blue-900 hover:opacity-90" ' +
      'title="the hotkey targets of this environment only">hotkey</button>';
  }

  // THE ENVIRONMENT CELL: the logo + the 3-part path. Clicking it opens a popup
  // for the ENVIRONMENT value only.
  function envCell(e) {
    return '<button type="button" data-env-field="environment" ' +
      'data-env-id="' + esc(e.environment_id) + '" ' +
      'class="inline-block py-1 text-left hover:underline">' + envLogo(e) + envPath(e) +
      envPage(e) + '</button>';
  }

  // THE ENVIRONMENT LOGO. THE HUMAN: "+ enviornment logo".
  // It comes from `working_environment.icon_url`. An environment with NO logo
  // renders NOTHING -- a default icon would claim a logo nobody registered.
  function envLogo(e) {
    const u = String(e.icon_url || '');
    if (!u) return '';
    return '<img src="' + esc(u) + '" alt="" ' +
      'class="inline-block h-4 w-4 mr-1 align-text-bottom rounded-sm" ' +
      'title="' + esc(e.product || '') + '">';
  }

  // THE LLM MODEL. `identity = session + LLM model`. The model is a FK to
  // `llm_model.id`; the NAME and the `local` flag are READ through the join.
  // An identity with NO model is `UNASSIGNED` -- a model is a DECISION, so it is
  // shown as unassigned rather than defaulted to something plausible.
  function llmCell(i) {
    const n = String(i.llm_name || '');
    if (!n) {
      return '<span class="rounded-full bg-soft px-2 py-1 text-xs text-muted" ' +
        'title="' + esc(i.llm_why || '') + '">UNASSIGNED</span>';
    }
    const local = i.llm_local === 1 || i.llm_local === true;
    return '<span class="rounded-full border border-line px-2 py-1 text-xs" ' +
      'title="' + esc((i.llm_model_id || '') + ' — ' + (i.llm_why || '')) + '">' +
      esc(n) + (local ? '<span class="ml-1 text-muted">local</span>' : '') +
      '</span>';
  }

  // THE PAGE an environment is on, when it is a browser page. The user
  // (2026-09-25): "enviornment : Google Chrome >
  // https://chat.deepseek.com/a/chat/s/7e589851-..." and
  // "enviornment : 豆包 Browser > 工作伙伴 > 软件研发小组 > 任務".
  //
  // The URL and the nav path are shown as SEPARATE lines under the 3-part key,
  // never merged into it: the key a reader groups by must stay stable, and a
  // path that grows a segment per site cannot be grouped.
  function envPage(e) {
    const url = String(e.url || '');
    const nav = String(e.nav_path || '');
    if (!url && !nav) return '';
    let out = '';
    if (nav) {
      out += '<div class="mt-0.5 text-[11px] text-muted" title="nav path">' +
        esc(nav) + '</div>';
    }
    if (url) {
      out += '<div class="mt-0.5"><a href="' + esc(url) + '" target="_blank" ' +
        'rel="noopener" class="mono inline-block py-1 text-[11px] text-blue-700 hover:underline" ' +
        'title="' + esc(url) + '">' + esc(url.length > 64
          ? url.slice(0, 61) + '…' : url) + '</a></div>';
    }
    return out;
  }

  // THE ROLE. The user: "role still = UNASSIGNED". A role is a DECISION, so it
  // is shown as unassigned rather than defaulted to something plausible.
  // `MIXED` is a REAL outcome: an environment whose identities disagree.
  function roleCell(i) {
    const k = String(i.role_key || 'UNASSIGNED');
    const open = ' data-env-field="role" data-env-id="' +
      esc(i.environment_id) + '" ';
    if (k === 'UNASSIGNED') {
      return '<button type="button"' + open +
        'class="rounded-full bg-soft px-2 py-1 text-xs text-muted hover:opacity-80">' +
        'UNASSIGNED</button>';
    }
    if (k === 'MIXED') {
      return '<button type="button"' + open +
        'class="rounded-full bg-amber-100 px-2 py-1 text-xs text-amber-800 hover:opacity-80" ' +
        'title="' + esc(JSON.stringify(i.role_tally || {})) + '">MIXED</button>';
    }
    return '<button type="button"' + open +
      'class="rounded-full border border-line px-2 py-1 text-xs hover:bg-soft">' +
      esc(k) + '</button>';
  }

  // The SESSION in PIECES. The user: "session ID, in piecs not in full".
  // A UUID is 5 groups; the FIRST group is the identity and the rest is noise,
  // so the first group is shown in full and the remainder is abbreviated.
  function sessionPieces(s) {
    const t = String(s || '');
    if (!t) return '<span class="text-muted">—</span>';
    const parts = t.split('-');
    if (parts.length < 2) return esc(t);
    return '<span title="' + esc(t) + '">' + esc(parts[0]) +
      '<span class="text-muted">-…-</span>' + esc(parts[parts.length - 1]) +
      '</span>';
  }

  // THE NAME, and WHERE IT CAME FROM. The user:
  //   "name and session be display name for respresentative"
  // MEASURED: the fallback used to be a truncated session id, so this column and
  // the `session` column showed the SAME fact. A DERIVED name is marked as
  // derived, so it is never mistaken for a human's own words.
  function nameCell(i) {
    const n = String(i.display_name || '');
    if (!n) return '<span class="text-muted">—</span>';
    const derived = String(i.name_source || '') === 'derived';
    return '<span title="' + esc(derived ? 'derived name (no register carries one)' : 'chat title') + '">' +
      esc(n) + '</span>';
  }

  // THE SESSION as its OWN display form: the id in PIECES plus the CHANNEL.
  // The server DERIVES `session_label`, so the UI and the API cannot disagree
  // about what the session column shows.
  function sessionLabel(i) {
    const s = String(i.session_label || '');
    if (!s) return sessionPieces(i.session_id);
    const at = s.lastIndexOf(' @ ');
    if (at < 0) return '<span title="' + esc(i.session_id || '') + '">' + esc(s) + '</span>';
    return '<span title="' + esc(i.session_id || '') + '">' + esc(s.slice(0, at)) +
      ' <span class="text-muted">@</span> ' + esc(s.slice(at + 3)) + '</span>';
  }

  // The ENVIRONMENT as a 3-PART PATH: KIND > PRODUCT > SURFACE.
  // The user: "in table can be IDE | VS code | chat" / "at display = IDE >
  // VS code > chat". The path is READ from `working_environment` server-side,
  // so the UI and the API cannot disagree about what the environment is.
  //
  // It accepts BOTH shapes: an environment row (kind/product/surface at the top
  // level) and an identity row (nested under `environment_path`).
  function envPath(i) {
    const ep = i.environment_path || i;
    if (!ep || !ep.kind) return '<span class="text-muted">—</span>';
    return '<span title="' + esc(ep.display || '') + '">' +
      esc(ep.kind) + ' <span class="text-muted">|</span> ' +
      esc(ep.product) + ' <span class="text-muted">|</span> ' +
      esc(ep.surface) + '</span>';
  }

  // ON / OFF / UNKNOWN, with the reason on hover. A worker with NO heartbeat is
  // UNKNOWN -- "can I give him a task NOW" is NOT measured for it.
  function liveCell(i) {
    const v = String(i.liveness || 'UNKNOWN');
    const cls = v === 'ON' ? 'bg-emerald-100 text-emerald-800'
      : v === 'OFF' ? 'bg-rose-100 text-rose-800'
      : 'bg-soft text-muted';
    return '<span class="rounded-full px-2 py-1 text-xs ' + cls + '" title="' +
      esc(i.liveness_why || '') + '">' + esc(v) + '</span>';
  }

  // THE ENVIRONMENT STATUS. The user:
  //   "status still = unknow"
  //   "status -> environment status"
  //   "has 20 instance? what is that?"
  // RUNNING / STOPPED / UNKNOWN, DERIVED from the app's own process count.
  // UNKNOWN is a REAL outcome: a channel with no `app` row has no app to ask
  // about, which is NOT the same as an app that is stopped.
  //
  // THE COUNT IS NOT THE LABEL. MEASURED: the 20 `Code` instances are VS Code's
  // Electron multi-process children (main / renderer / extension host / GPU /
  // utility), so `RUNNING (20)` reads as "20 VS Codes" when it is ONE. The count
  // is EVIDENCE, so it lives in the hover title, not in the cell.
  function envStatusCell(i) {
    const v = String(i.status || 'UNKNOWN');
    const cls = v === 'RUNNING' ? 'bg-emerald-100 text-emerald-800'
      : v === 'STOPPED' ? 'bg-rose-100 text-rose-800'
      : 'bg-soft text-muted';
    const why = String(i.status_why || '');
    const app = String(i.status_app || '');
    const n = i.status_count;
    const label = v;
    const title = (app ? app + ' — ' : '') + why +
      (n != null ? ' [' + n + ' process instance(s); an Electron app runs many]' : '');
    return '<button type="button" data-env-field="status" ' +
      'data-env-id="' + esc(i.environment_id) + '" ' +
      'class="rounded-full px-2 py-1 text-xs ' + cls + ' hover:opacity-80" ' +
      'title="' + esc(title) + '">' + esc(label) + '</button>';
  }

  // THE ENVIRONMENT'S OWN SERVER STATUS is NOT shown on this table.
  // THE HUMAN (2026-09-25): "remove channel ... status at the table". The
  // function is kept because the DETAIL popup still uses it; the step-1 table
  // does not.
  function environmentServerCell(e) {
    const v = String(e.server_status || 'UNKNOWN');
    const cls = v === 'ONLINE' ? 'bg-emerald-100 text-emerald-800'
      : v === 'OFFLINE' ? 'bg-rose-100 text-rose-800'
      : 'bg-soft text-muted';
    const title = 'environment ' + String(e.environment_id || '') + ' — ' +
      String(e.server_why || 'no server declared for this environment');
    return '<span class="rounded-full px-2 py-1 text-xs ' + cls +
      '" title="' + esc(title) + '">' + esc(v) + '</span>';
  }

  function shortId(s) {
    const t = String(s || '');
    return t.length > 8 ? t.slice(0, 8) + '…' : (t || '—');
  }

  // THE CAPABILITY EXECUTORS (`worker_registry`). A DIFFERENT thing from a
  // worker: it answers "who CAN do this capability".
  function capabilityHtml() {
    if (!s.capLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.capMsg) return '<p class="text-sm text-rose-600">' + esc(s.capMsg) + '</p>';
    if (!s.capRows.length) {
      return '<p class="text-sm text-muted">No capability executors registered.</p>';
    }
    const rows = s.capRows.map((w) =>
      '<tr class="cursor-pointer hover:bg-soft" data-worker-key="' + esc(w.worker_key) + '">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(w.worker_key) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(w.worker_type) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(w.capability_ref) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + envCell(w) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(w.fallback_order) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(w.status) + '</td>' +
      '<td class="px-3 py-2 text-xs" data-mode-cell="' + esc(w.worker_key) + '">…</td>' +
      '</tr>').join('');
    return '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">worker_key</th><th class="px-3 py-2">type</th>' +
      '<th class="px-3 py-2">capability</th><th class="px-3 py-2">environment</th>' +
      '<th class="px-3 py-2">order</th>' +
      '<th class="px-3 py-2">status</th><th class="px-3 py-2">mode applied</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  }

  /** Fill the `mode applied` column from `/api/worker/modes`, per worker. */
  async function fillModeColumn() {
    if (!root) return;
    for (const w of (s.rows || [])) {
      const cell = root.querySelector('[data-mode-cell="' +
        (window.CSS && CSS.escape ? CSS.escape(w.worker_key) : w.worker_key) + '"]');
      if (!cell) continue;
      try {
        const res = await fetch('/api/worker/modes?worker_key=' +
          encodeURIComponent(w.worker_key));
        const d = await res.json();
        const applied = (d.modes || []).filter((m) => m.is_applied)
          .map((m) => m.mode_key);
        cell.innerHTML = applied.length
          ? applied.map((m) => '<span class="rounded bg-soft px-1 mono">' + esc(m) + '</span>').join(' ')
          : '<span class="text-muted">none</span>';
      } catch (e) {
        cell.innerHTML = '<span class="text-rose-600">err</span>';
      }
    }
  }

  function detailHtml() {
    const d = s.detail;
    if (!d) return '<p class="text-sm text-muted">Pick a worker from the list.</p>';
    if (!d.ok) return '<p class="text-sm text-rose-600">' + esc(d.error) + '</p>';
    const w = d.worker;
    const kv = (k, v) => '<div class="flex gap-2 py-1"><span class="w-40 text-xs text-muted">' +
      esc(k) + '</span><span class="text-xs mono">' + esc(v) + '</span></div>';
    return '<div class="rounded-xl border border-line p-4">' +
      '<h3 class="text-sm font-semibold">' + esc(w.worker_key) + '</h3>' +
      kv('worker_id', w.worker_id) + kv('worker_type', w.worker_type) +
      kv('capability_ref', w.capability_ref) + kv('fallback_order', w.fallback_order) +
      kv('physical_path', w.physical_path) + kv('uses', w.uses_text) +
      kv('status', w.status) + kv('cite_ref', w.cite_ref) +
      '</div>' +
      '<p class="mt-3 text-xs text-muted">The JOIN to an identity is 5W1H, not a column. ' +
      'See <span class="mono">/llm-tasks/identity</span> for the pair view.</p>';
  }

  /**
   * THE EDGE TABLE. Rows = the SIX rights, columns = ask / plan / agent.
   * Nothing here is typed in: every value comes from `/api/mode/rights`, which
   * reads `mode_right_registry` — the same table the gate reads.
   *
   * The HELP row is rendered in FULL and first, because it is the answer to
   * "how to give help before complain not block the activity only".
   */
  function rightHtml() {
    if (!s.rightsLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.rightsMsg) {
      return '<p class="text-sm text-rose-600">' + esc(s.rightsMsg) + '</p>';
    }
    const m = s.rights.matrix || {};
    const keys = s.rights.right_keys || [];
    const modes = s.rights.modes || [];
    const cell = (v) => {
      if (!v) return '<span class="text-rose-600">MISSING — a DEFECT</span>';
      const t = esc(v.value_text === '' ? '(empty — writes nothing)' : v.value_text);
      return '<span title="' + esc(v.cite_ref) + '">' + t + '</span>';
    };
    const rows = keys.map((k) => {
      const help = k === 'how_to_proceed';
      const cells = modes.map((mo) =>
        '<td class="px-3 py-2 text-xs align-top ' +
        (help ? '' : 'mono') + '">' + cell((m[mo] || {})[k]) + '</td>').join('');
      return '<tr class="' + (help ? 'bg-soft' : '') + '">' +
        '<td class="px-3 py-2 text-xs mono font-semibold">' + esc(k) +
        (help ? ' <span class="text-[10px] text-muted">(THE HELP)</span>' : '') +
        '</td>' + cells + '</tr>';
    }).join('');
    const head = modes.map((mo) =>
      '<th class="px-3 py-2">' + esc(mo) + '</th>').join('');
    return '<div class="rounded-xl border border-line p-4">' +
      '<h3 class="text-sm font-semibold">The worker\'s edge, by mode</h3>' +
      '<p class="mt-1 text-xs text-muted">Read from <span class="mono">mode_right_registry</span> — ' +
      'the SAME rows the gate reads, so this page cannot disagree with enforcement. ' +
      'Hover a cell for its citation.</p>' +
      '<div class="mt-3 overflow-auto rounded-lg border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">right</th>' + head + '</tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div></div>' +
      workerModesHtml();
  }

  /**
   * "mode is apply for this worker or not" — the per-worker half. Shown when a
   * worker is selected; the answer is a JOIN row, not a guess.
   */
  function workerModesHtml() {
    const wm = s.workerModes;
    if (!wm) {
      const keys = (s.rows || []).map((w) =>
        '<button class="rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft" ' +
        'data-wm-key="' + esc(w.worker_key) + '">' + esc(w.worker_key) + '</button>').join(' ');
      return '<div class="mt-4 rounded-xl border border-line p-4">' +
        '<h3 class="text-sm font-semibold">Mode applies to which worker?</h3>' +
        '<p class="mt-1 text-xs text-muted">Pick a worker to read its <span class="mono">worker_mode</span> rows.</p>' +
        '<div class="mt-2 flex flex-wrap gap-2">' + keys + '</div></div>';
    }
    if (!wm.ok) return '<p class="mt-3 text-sm text-rose-600">' + esc(wm.error) + '</p>';
    const rows = (wm.modes || []).map((mo) =>
      '<tr><td class="px-3 py-1 text-xs mono">' + esc(mo.mode_key) + '</td>' +
      '<td class="px-3 py-1 text-xs">' +
      (mo.is_applied ? '<span class="text-emerald-700">applied</span>'
                     : '<span class="text-muted">not applied</span>') + '</td>' +
      '<td class="px-3 py-1 text-xs text-muted">' + esc(mo.why || '') + '</td></tr>').join('');
    return '<div class="mt-4 rounded-xl border border-line p-4">' +
      '<h3 class="text-sm font-semibold">Mode applicable to ' + esc(wm.worker_key) + '</h3>' +
      '<div class="mt-2 overflow-auto rounded-lg border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-1">mode</th><th class="px-3 py-1">applies</th>' +
      '<th class="px-3 py-1">why</th></tr></thead><tbody>' + rows + '</tbody></table></div></div>';
  }

  // THE ASSIGNEE PICK: role x environment, clickable.
  // The user (2026-09-24):
  //   "environment setting Online + role-> onclick, so he will the one
  //    (identity) to have the task"
  //   "ui is wrong design , it should for role with enviornment not worker
  //    with enviornment"
  //
  // THE AXIS IS role x environment, NOT session. A session list answers "which
  // sessions exist"; the question that decides whether a task can be given is
  // "which ROLE, in which ENVIRONMENT, is UP". Clicking a row SELECTS that
  // identity, and the selected identity is the one that RECEIVES the task.
  //
  // THE COUNT IS NOT SHOWN AS A BARE QUANTITY. The user: "has 20 instance? what
  // is that?" MEASURED: the 20 are VS Code's Electron multi-process children
  // (main / renderer / extension host / GPU / utility), so `RUNNING (20)` reads
  // as "20 VS Codes" when it is ONE. The count is EVIDENCE, so it lives in the
  // hover title, not in the cell.
  function assigneeHtml() {
    if (!s.candLoaded) return '<p class="text-sm text-muted">Loading…</p>';
    if (s.candMsg) return '<p class="text-sm text-rose-600">' + esc(s.candMsg) + '</p>';
    const sel = s.selected;
    const selBar = sel
      ? '<div class="mb-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">' +
        'Assignee: <span class="mono">identity #' + esc(sel.identity_id) + '</span>' +
        ' · role <span class="mono">' + esc(sel.role_key) + '</span>' +
        ' · ' + esc(sel.environment || sel.channel) +
        ' · <span class="mono">' + esc(sel.status) + '</span>' +
        ' <button id="assignee-clear" class="ml-2 rounded border border-emerald-300 px-2 py-1 text-xs hover:bg-emerald-100">Clear</button>' +
        '</div>'
      : '<div class="mb-3 rounded-xl border border-line bg-soft/50 px-3 py-2 text-sm text-muted">' +
        'No assignee selected — no identity will receive a task. Click a row to pick one.</div>';
    if (!s.cands.length) {
      return selBar + '<p class="text-sm text-muted">No candidates.</p>';
    }
    const rows = s.cands.map((c) => {
      const isSel = sel && Number(sel.identity_id) === Number(c.identity_id);
      const roleCls = c.role_assigned
        ? 'rounded-full border border-line px-2 py-1 text-xs'
        : 'rounded-full bg-soft px-2 py-1 text-xs text-muted';
      return '<tr class="cursor-pointer hover:bg-soft' +
        (isSel ? ' bg-emerald-50' : '') +
        '" data-pick-identity="' + esc(c.identity_id) + '">' +
        '<td class="px-3 py-2 text-xs">' + envStatusCell(c) + '</td>' +
        '<td class="px-3 py-2 text-xs">' + esc(c.environment || '—') + '</td>' +
        '<td class="px-3 py-2 text-xs"><span class="' + roleCls + '">' +
        esc(c.role_key) + '</span></td>' +
        '<td class="px-3 py-2 mono text-xs">' + esc(c.identity_id) + '</td>' +
        '<td class="px-3 py-2 mono text-xs">' + sessionPieces(c.session_id) + '</td>' +
        '<td class="px-3 py-2 text-xs">' +
        (isSel ? '<span class="rounded-full bg-emerald-100 px-2 py-1 text-xs text-emerald-800">SELECTED</span>'
               : '<span class="text-muted">click to pick</span>') + '</td>' +
        '</tr>';
    }).join('');
    return selBar +
      '<div class="overflow-auto rounded-xl border border-line">' +
      '<table class="min-w-full text-left"><thead class="bg-soft/80 text-[11px] uppercase text-muted">' +
      '<tr><th class="px-3 py-2">status</th><th class="px-3 py-2">environment</th>' +
      '<th class="px-3 py-2">role</th><th class="px-3 py-2">identity</th>' +
      '<th class="px-3 py-2">session</th><th class="px-3 py-2">pick</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>' +
      '<p class="mt-3 text-xs text-muted">The axis is <span class="mono">role x environment</span>, ' +
      'not session. Status is the ENVIRONMENT status (is the app running). ' +
      'A role is a DECISION: an identity with no role stays ' +
      '<span class="mono">UNASSIGNED</span>.</p>';
  }

  function render() {
    if (!root) return;
    root.innerHTML =
      '<div class="p-4">' +
      '<div class="flex items-center justify-between">' +
      '<div><h2 class="text-base font-semibold">Environment</h2>' +
      '<p class="mt-0.5 text-sm text-muted">The unit is the ENVIRONMENT. ' +
      'SSOT = <span class="mono">working_environment</span> via ' +
      '<span class="mono">/api/environment/list</span>. ' +
      'The key is <span class="mono">environment_id</span>. ' +
      'The capability executors are a DIFFERENT thing and have their own tab.</p></div>' +
      '<button id="worker-refresh" class="rounded-lg border border-line px-3 py-1 text-xs hover:bg-soft">Refresh</button>' +
      '</div>' +
      '<div class="mt-4">' +
      (s.tab === 'assignee' ? assigneeHtml()
        : s.tab === 'step2' ? step2Html()
        : s.tab === 'step3' ? step3Html()
        : s.tab === 'detail' ? detailHtml()
        : s.tab === 'right' ? rightHtml()
        : s.tab === 'capability' ? capabilityHtml()
        : listHtml()) + '</div>' +
      '</div>' +
      // THE POPUP, on top of everything (fixed overlay).
      popupHtml();

    const rb = root.querySelector('#worker-refresh');
    if (rb) rb.addEventListener('click', () => {
      if (s.tab === 'right') { refreshRights(); toast('Edge refreshed'); }
      else if (s.tab === 'capability') { refreshCapability(); toast('Capability executors refreshed'); }
      else if (s.tab === 'assignee') { refreshCandidates(); toast('Candidates refreshed'); }
      else if (s.tab === 'step2' || s.tab === 'step3') { refreshFlow(); toast('Flow refreshed'); }
      else { refresh(); toast('Environment refreshed'); }
    });
    root.querySelectorAll('[data-pick-identity]').forEach((el) => {
      el.addEventListener('click', () =>
        pickAssignee(el.getAttribute('data-pick-identity')));
    });
    // STEP 1: the ACTION button picks the environment and moves to step 2.
    // The user (2026-09-25): "+ field action for onlick to step 2".
    root.querySelectorAll('[data-pick-env]').forEach((el) => {
      el.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        await pickStep(1, Number(el.getAttribute('data-pick-env')));
        s.tab = 'step2';
        render();
      });
    });
    // STEP 1: EACH CELL opens the popup for ITS OWN field.
    // THE HUMAN (2026-09-25): "user onlcick to value = configure/environment/
    // role/env/status / that means user is looking for this value!! / not to
    // have all !!!!".
    root.querySelectorAll('[data-env-field]').forEach((el) => {
      el.addEventListener('click', (ev) => {
        ev.stopPropagation();
        openEnvPopup(el.getAttribute('data-env-id'),
                     el.getAttribute('data-env-field'));
      });
    });
    // STEP 2: the `Submit → Step 3` button submits the LLM and moves to
    // step 3. The user: "onclick LLM = submit -> step 3".
    root.querySelectorAll('[data-pick-llm]').forEach((el) => {
      el.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        await pickStep(2, Number(el.getAttribute('data-pick-llm')));
        s.tab = 'step3';
        render();
      });
    });
    // STEP 2: the card NAME opens the DETAIL popup (edit the row). The user:
    //   "same function for step 2"
    root.querySelectorAll('[data-open-llm]').forEach((el) => {
      el.addEventListener('click', () =>
        openLlmPopup(el.getAttribute('data-open-llm')));
    });
    // STEP 2: the `+ LLM` button opens the popup in ADD mode. The user:
    //   "+ buttom to + LLM"
    const addLlm = root.querySelector('[data-open-llm-add]');
    if (addLlm) addLlm.addEventListener('click', () => openLlmPopup(null));
    // THE POPUP: close (the overlay's dark area itself, or a Close button).
    // A click INSIDE the panel must NOT close it, so the overlay only closes
    // when it is the click target, not an ancestor of one.
    root.querySelectorAll('[data-popup-close]').forEach((el) => {
      el.addEventListener('click', (ev) => {
        if (el.tagName === 'BUTTON' || ev.target === el) closePopup();
      });
    });
    // THE POPUP: save the environment edit (updates the table).
    const saveEnv = root.querySelector('[data-save-env]');
    if (saveEnv) saveEnv.addEventListener('click', saveEnvEdit);
    // THE POPUP: declare / remove a role x environment pair.
    root.querySelectorAll('[data-toggle-role]').forEach((el) => {
      el.addEventListener('click', () =>
        toggleRole(el.getAttribute('data-toggle-role')));
    });
    // THE POPUP: save the LLM edit, or add a new model.
    const saveLlm = root.querySelector('[data-save-llm]');
    if (saveLlm) saveLlm.addEventListener('click', saveLlmEdit);
    // THE POPUP: submit the LLM and go to step 3 (from the LLM popup).
    const popStep3 = root.querySelector('[data-popup-step3]');
    if (popStep3) popStep3.addEventListener('click', async () => {
      const lid = Number(popStep3.getAttribute('data-popup-step3'));
      closePopup();
      await pickStep(2, lid);
      s.tab = 'step3';
      render();
    });
    // STEP 3: clicking a tool submits it.
    root.querySelectorAll('[data-pick-tool]').forEach((el) => {
      el.addEventListener('click', () =>
        pickStep(3, el.getAttribute('data-pick-tool')));
    });
    const cb = root.querySelector('#assignee-clear');
    if (cb) cb.addEventListener('click', async () => {
      try {
        await fetch('/api/assignee/clear', { method: 'POST' });
        toast('Assignee cleared');
        await refreshCandidates();
      } catch (e) { toast('Clear failed: ' + (e.message || e)); }
    });
    root.querySelectorAll('[data-worker-key]').forEach((el) => {
      el.addEventListener('click', () => openDetail(el.getAttribute('data-worker-key')));
    });
    root.querySelectorAll('[data-wm-key]').forEach((el) => {
      el.addEventListener('click', () => loadWorkerModes(el.getAttribute('data-wm-key')));
    });
  }

  render();
  // FIXED 2026-09-23 (the `mode applied` column was stuck on "…").
  //
  // The bug: `refresh()` is ASYNC and was called WITHOUT await, so the next
  // line `fillModeColumn()` ran while `s.rows` was still EMPTY. It looped over
  // nothing, so every `data-mode-cell` kept the "…" placeholder that `listHtml`
  // renders. The column only filled in on a LATER mount, when `s.loaded` was
  // already true — which is why it looked like a race and not a bug.
  //
  // The fix is ordering, not a retry: await the load, then fill.
  (async () => {
    if (!s.loaded) await refresh();
    if (s.tab === 'capability' && !s.capLoaded) await refreshCapability();
    if (s.tab === 'right' && !s.rightsLoaded) await refreshRights();
    if (s.tab === 'assignee' && !s.candLoaded) await refreshCandidates();
    // THE THREE-STEP PICK. The flow is needed by step 2 and step 3, and step 1
    // needs it too so a picked environment is visible.
    if (!s.flowLoaded) await refreshFlow();
    await fillModeColumn();
  })();
  return { render, refresh, refreshCapability, openDetail, refreshRights,
           loadWorkerModes, refreshCandidates, pickAssignee, refreshFlow,
           pickStep };
}
