/**
 * Screen Watch — Vue 3 component
 *
 * THE FLOW (user, 2026-09-22):
 *   "5 mins take screenshot and LLM 7B will anayle the photo, if need help, he
 *    can control openclaw or playwright to have the work at my computer"
 *   "target -> screenshot and LLM 7B anayle -> prompt for openclaw or playwright"
 *
 * FOUR PAGES:
 *   Runs     screen_watch_run   every capture+analysis cycle, newest first
 *   Detail   one run in full    the image, the analysis, BOTH decision layers
 *   Targets  Playwright vs OpenClaw, each with its own availability
 *   Control  plan a dispatch, and confirm it
 *
 * THE TWO DECISION LAYERS ARE SHOWN SEPARATELY. `model_says` is what the 7B
 * answered; `rule_says` is what the rule did with it. A single verdict would
 * hide which layer decided — and when a false positive happens, that is the
 * first thing you need to know.
 *
 * THE UI NEVER ACTS BY ITSELF. `POST /api/screen_watch/dispatch` defaults to
 * `confirm: false`, so pressing the button PLANS and does not act. The user's
 * machine is not a sandbox, so acting needs a second, explicit press.
 *
 * Design follows ui_skill: rounded-2xl / border-line / bg-panel / shadow-panel,
 * text-ink (primary), text-muted (secondary), text-accent (emphasis), mono (ids).
 */
import { createApp, reactive, computed } from 'vue';

const LS_KEY = 'screen_watch_state_v1';

const STATUS_URL = '/api/screen_watch/status';
const RUNS_URL = '/api/screen_watch/runs';
const RUN_URL = '/api/screen_watch/run/';
const RUN_NOW_URL = '/api/screen_watch/run';
const TARGETS_URL = '/api/screen_watch/targets';
const DISPATCH_URL = '/api/screen_watch/dispatch';

const TABS = [
  { id: 'runs', label: 'Runs' },
  { id: 'detail', label: 'Detail' },
  { id: 'targets', label: 'Targets' },
  { id: 'control', label: 'Control' },
];

function loadState() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (d && typeof d === 'object') return d;
  } catch (_) {
    /* ignore corrupt state */
  }
  return null;
}

function esc(v) {
  return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Severity -> a colour. `NA` is deliberately grey, not green: an unreadable
// severity is UNKNOWN, and unknown must never look like "fine".
function sevClass(s) {
  const v = String(s || '').toLowerCase();
  if (v === 'critical') return 'bg-rose-100 text-rose-800';
  if (v === 'high') return 'bg-orange-100 text-orange-800';
  if (v === 'medium') return 'bg-amber-100 text-amber-800';
  if (v === 'low') return 'bg-emerald-50 text-emerald-700';
  return 'bg-soft text-muted';
}

// The decision -> a colour. `act` is the only one that leads to an action.
function decClass(d) {
  const v = String(d || '');
  if (v === 'act') return 'bg-rose-100 text-rose-800';
  if (v === 'no-action') return 'bg-soft text-muted';
  return 'bg-soft text-muted';
}

const ScreenWatch = {
  template: `
  <div class="mx-auto max-w-6xl p-6">
    <div class="flex flex-wrap items-center gap-2">
      <h2 class="text-lg font-semibold text-ink">Screen Watch</h2>
      <span class="rounded-full bg-soft px-2.5 py-1 text-xs text-muted">
        every {{ (st.interval_sec || 0) }}s</span>
      <span class="rounded-full bg-soft px-2.5 py-1 text-xs text-muted">
        floor {{ st.severity_floor || '-' }}</span>
      <span class="rounded-full bg-soft px-2.5 py-1 text-xs text-muted">
        repeat {{ st.repeat_required || '-' }}</span>
      <span class="rounded-full bg-soft px-2.5 py-1 text-xs text-muted">
        {{ st.runs || 0 }} runs · {{ st.acted || 0 }} acted</span>
      <button @click="refresh" type="button"
        class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">
        Refresh</button>
      <button @click="runNow" type="button" :disabled="busy"
        class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
        {{ busy ? 'Running…' : 'Run now' }}</button>
    </div>

    <div v-if="msg" class="mt-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                    : 'border-rose-300 bg-rose-50 text-rose-800'">
      {{ msg }}</div>

    <div class="mt-4 flex flex-wrap gap-1 border-b border-line">
      <button v-for="t in tabs" :key="t.id" @click="tab = t.id; persist()"
        type="button"
        class="rounded-t-lg px-3 py-2 text-sm"
        :class="tab === t.id ? 'bg-panel font-medium text-ink border border-b-0 border-line'
                             : 'text-muted hover:text-ink'">
        {{ t.label }}</button>
    </div>

    <!-- ============================ RUNS ============================ -->
    <div v-if="tab === 'runs'" class="mt-4 rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-4 py-3 text-sm font-semibold text-ink">
        Runs ({{ runs.length }})</div>
      <div v-if="!runs.length" class="px-4 py-6 text-sm text-muted">
        No runs yet. Press <b>Run now</b> to capture and analyse once.</div>
      <table v-else class="w-full text-left">
        <thead class="text-xs text-muted">
          <tr class="border-b border-line">
            <th class="px-4 py-2">#</th><th class="px-4 py-2">When</th>
            <th class="px-4 py-2">Severity</th><th class="px-4 py-2">Model</th>
            <th class="px-4 py-2">Rule</th><th class="px-4 py-2">Decision</th>
            <th class="px-4 py-2">UI state</th>
          </tr></thead>
        <tbody>
          <tr v-for="r in runs" :key="r.run_id"
            class="cursor-pointer border-b border-line align-top hover:bg-soft"
            @click="open(r.run_id)">
            <td class="mono px-4 py-2 text-xs">{{ r.run_id }}</td>
            <td class="px-4 py-2 text-xs text-muted">{{ r.captured_at }}</td>
            <td class="px-4 py-2">
              <span class="rounded-full px-2 py-0.5 text-xs" :class="sevClass(r.severity)">
                {{ r.severity }}</span></td>
            <td class="px-4 py-2 text-xs text-muted">{{ r.model_says }}</td>
            <td class="px-4 py-2 text-xs text-muted">{{ r.rule_says }}</td>
            <td class="px-4 py-2">
              <span class="rounded-full px-2 py-0.5 text-xs" :class="decClass(r.decision)">
                {{ r.decision }}</span></td>
            <td class="px-4 py-2 text-xs text-muted">{{ r.ui_state }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- =========================== DETAIL =========================== -->
    <div v-if="tab === 'detail'" class="mt-4">
      <div v-if="!detail" class="rounded-2xl border border-line bg-panel px-4 py-6 text-sm text-muted shadow-panel">
        Pick a run on the <b>Runs</b> page.</div>
      <div v-else class="rounded-2xl border border-line bg-panel p-4 shadow-panel">
        <div class="flex flex-wrap items-center gap-2">
          <span class="mono text-sm text-ink">run {{ detail.run_id }}</span>
          <span class="rounded-full px-2 py-0.5 text-xs" :class="sevClass(detail.severity)">
            {{ detail.severity }}</span>
          <span class="rounded-full px-2 py-0.5 text-xs" :class="decClass(detail.decision)">
            {{ detail.decision }}</span>
          <span class="text-xs text-muted">{{ detail.captured_at }}</span>
        </div>

        <div class="mt-3 grid gap-3 md:grid-cols-2">
          <div>
            <div class="text-xs font-semibold text-muted">Screenshot</div>
            <div v-if="detail.image_path" class="mono mt-1 break-all text-[11px] text-muted">
              {{ detail.image_path }}</div>
            <div v-else class="mt-1 text-xs text-muted">no image</div>
          </div>
          <div>
            <div class="text-xs font-semibold text-muted">Model</div>
            <div class="mono mt-1 text-xs text-ink">{{ detail.model }}</div>
            <div class="text-[11px] text-muted">source: {{ detail.model_source }}</div>
          </div>
        </div>

        <div class="mt-4 rounded-xl border border-line bg-soft p-3">
          <div class="text-xs font-semibold text-muted">What the 7B saw</div>
          <p class="mt-1 text-sm text-ink">{{ detail.ui_state }}</p>
          <p class="mt-1 text-xs text-muted">likely cause: {{ detail.likely_cause }}</p>
          <p class="mt-1 text-xs text-muted">reason: {{ detail.reason }}</p>
          <p class="mt-1 text-xs text-muted">confidence: {{ detail.confidence }}</p>
        </div>

        <div class="mt-3 grid gap-3 md:grid-cols-2">
          <div class="rounded-xl border border-line p-3">
            <div class="text-xs font-semibold text-muted">Layer 1 — the model</div>
            <div class="mono mt-1 text-sm text-ink">{{ detail.model_says }}</div>
          </div>
          <div class="rounded-xl border border-line p-3">
            <div class="text-xs font-semibold text-muted">Layer 2 — the rule</div>
            <div class="mono mt-1 text-sm text-ink">{{ detail.rule_says }}</div>
          </div>
        </div>
        <p class="mt-2 text-xs text-muted">{{ detail.decision_why }}</p>

        <div v-if="detail.dispatch_plan && detail.dispatch_plan.order"
          class="mt-3 rounded-xl border border-line p-3">
          <div class="text-xs font-semibold text-muted">Dispatch plan</div>
          <p class="mt-1 text-xs text-muted">{{ detail.dispatch_plan.why }}</p>
          <div v-for="o in detail.dispatch_plan.order" :key="o.target"
            class="mt-1 text-xs">
            <span class="mono">{{ o.target }}</span>
            <span class="ml-2 rounded-full px-2 py-0.5"
              :class="o.available ? 'bg-emerald-50 text-emerald-700' : 'bg-soft text-muted'">
              {{ o.available ? 'available' : 'unavailable' }}</span>
            <span class="ml-2 text-muted">{{ o.why }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- =========================== TARGETS =========================== -->
    <div v-if="tab === 'targets'" class="mt-4 rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-4 py-3 text-sm font-semibold text-ink">
        Dispatch targets</div>
      <p class="px-4 pt-3 text-xs text-muted">
        OpenClaw is <b>optional</b>. Playwright is PRIMARY for browser work
        (selectors, auto-wait, an actionability check before every action);
        OpenClaw is the ONLY target that can drive a desktop app, so it is the
        fallback for browsers and the only choice for a desktop window.</p>
      <table class="mt-2 w-full text-left">
        <thead class="text-xs text-muted">
          <tr class="border-b border-line">
            <th class="px-4 py-2">Target</th><th class="px-4 py-2">Kind</th>
            <th class="px-4 py-2">Available</th><th class="px-4 py-2">Why</th>
          </tr></thead>
        <tbody>
          <tr v-for="t in targets" :key="t.target" class="border-b border-line align-top">
            <td class="mono px-4 py-2 text-sm text-ink">{{ t.target }}</td>
            <td class="px-4 py-2 text-xs text-muted">{{ t.kind }}</td>
            <td class="px-4 py-2">
              <span class="rounded-full px-2 py-0.5 text-xs"
                :class="t.ok ? 'bg-emerald-50 text-emerald-700' : 'bg-soft text-muted'">
                {{ t.ok ? 'yes' : 'no' }}</span></td>
            <td class="px-4 py-2 text-xs text-muted">{{ t.why }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- =========================== CONTROL =========================== -->
    <div v-if="tab === 'control'" class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">
      <div class="text-sm font-semibold text-ink">Control</div>
      <p class="mt-1 text-xs text-muted">
        Planning is free. <b>Acting needs a second press</b> — the user's machine
        is not a sandbox, so the first press only shows the plan.</p>

      <div class="mt-3 flex flex-wrap items-end gap-2">
        <label class="text-xs text-muted">Run
          <input v-model.number="ctl.runId" type="number" min="1"
            class="mono ml-1 w-24 rounded-lg border border-line bg-panel px-2 py-1 text-sm text-ink" />
        </label>
        <label class="text-xs text-muted">Target
          <select v-model="ctl.target"
            class="ml-1 rounded-lg border border-line bg-panel px-2 py-1 text-sm text-ink">
            <option value="">(auto)</option>
            <option v-for="t in targets" :key="t.target" :value="t.target">{{ t.target }}</option>
          </select>
        </label>
        <button @click="planDispatch" type="button" :disabled="busy"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft disabled:opacity-50">
          Plan</button>
        <button @click="confirmDispatch" type="button" :disabled="busy || !plan"
          class="rounded-xl bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50">
          Confirm &amp; act</button>
      </div>

      <div v-if="plan" class="mt-3 rounded-xl border border-line bg-soft p-3">
        <div class="text-xs font-semibold text-muted">Plan</div>
        <p class="mt-1 text-xs text-muted">{{ plan.why }}</p>
        <p class="mt-1 text-xs text-ink">chosen: <span class="mono">{{ plan.chosen || '-' }}</span></p>
        <div v-for="o in (plan.order || [])" :key="o.target" class="mt-1 text-xs">
          <span class="mono">{{ o.target }}</span>
          <span class="ml-2 rounded-full px-2 py-0.5"
            :class="o.available ? 'bg-emerald-50 text-emerald-700' : 'bg-soft text-muted'">
            {{ o.available ? 'available' : 'unavailable' }}</span>
          <span class="ml-2 text-muted">{{ o.why }}</span>
        </div>
      </div>
    </div>
  </div>`,
  setup() {
    const saved = loadState() || {};
    // The URL segment WINS over localStorage. app.js parses the route and hands
    // the tab in through `window.__screenWatchTab`, so there is ONE parser
    // (`parseRoutePath`) rather than a second one here that can drift.
    const urlTab = (typeof window !== 'undefined' && window.__screenWatchTab) || '';
    const validTab = TABS.some((t) => t.id === urlTab);
    const s = reactive({
      tab: validTab ? urlTab : (saved.tab || 'runs'),
      st: {}, runs: [], targets: [], detail: null, plan: null,
      ctl: { runId: saved.runId || null, target: saved.target || '' },
      busy: false, msg: '', msgOk: true,
    });

    function persist() {
      try {
        localStorage.setItem(LS_KEY, JSON.stringify({
          tab: s.tab, runId: s.ctl.runId, target: s.ctl.target,
        }));
      } catch (_) { /* ignore */ }
    }

    async function get(url) {
      const r = await fetch(url);
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) {
        throw new Error(d.error || ('HTTP ' + r.status));
      }
      return d;
    }

    async function load() {
      try {
        const [st, runs, tg] = await Promise.all([
          get(STATUS_URL), get(RUNS_URL + '?limit=100'), get(TARGETS_URL),
        ]);
        s.st = st;
        s.runs = runs.rows || [];
        s.targets = tg.rows || [];
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Load failed: ' + (e.message || e);
      }
    }

    async function open(runId) {
      try {
        const d = await get(RUN_URL + runId);
        s.detail = d.run;
        s.tab = 'detail';
        persist();
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Detail failed: ' + (e.message || e);
      }
    }

    async function runNow() {
      s.busy = true;
      s.msg = '';
      try {
        const r = await fetch(RUN_NOW_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: false }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) throw new Error(d.error || ('HTTP ' + r.status));
        s.msgOk = true;
        s.msg = 'Run ' + d.run_id + ': ' + d.analysis.severity + ' / '
          + d.decision.decision + ' — ' + d.decision.why;
        await load();
        if (d.run_id) await open(d.run_id);
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Run failed: ' + (e.message || e);
      } finally {
        s.busy = false;
      }
    }

    async function postDispatch(confirm) {
      s.busy = true;
      s.msg = '';
      try {
        const r = await fetch(DISPATCH_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            run_id: s.ctl.runId, target: s.ctl.target, confirm,
          }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || d.ok === false) throw new Error(d.error || ('HTTP ' + r.status));
        s.plan = d.plan || null;
        s.msgOk = true;
        s.msg = d.why || (confirm ? 'acted' : 'planned');
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Dispatch failed: ' + (e.message || e);
      } finally {
        s.busy = false;
      }
    }

    const planDispatch = () => postDispatch(false);
    const confirmDispatch = () => postDispatch(true);

    load();

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity, so `v-model` stops updating and
    // a tab click does not re-render. Measured in capability-center.js and
    // ticket-center.js, which carry the same warning. Object.assign onto the
    // reactive object keeps the proxy identity, so every binding stays live.
    return Object.assign(s, {
      tabs: TABS, esc, sevClass, decClass,
      persist, refresh: load, open, runNow,
      planDispatch, confirmDispatch,
    });
  },
};

export function mountScreenWatch(root) {
  const app = createApp(ScreenWatch);
  app.mount(root);
  return app;
}
