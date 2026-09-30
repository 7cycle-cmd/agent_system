/**
 * flow-setting-table.js — Chat Center FLOW table (Vue 3).
 *
 * WHY THIS EXISTS
 * ---------------
 * The Chat Center has TWO UIs at TWO URLs (user spec 2026-09-20):
 *   /llm-tasks/chat_center/setting  -> prompt-template SETTING (pick a template)
 *   /llm-tasks/chat_center/flow     -> the FLOW table (this component)
 *
 * A flow is an ordered list of steps. Each step carries the QUESTION to ask and
 * the VALUE it produces, plus where to go next. Clicking a QUESTION or a VALUE
 * drives the flow: it selects that step and advances the cursor, so the user can
 * walk the flow by clicking instead of typing. The ACTION column is the hook the
 * API will execute later (wired in a follow-up).
 *
 * Rows live in `flow_setting` (agent.db) and are served by /api/flow_settings.
 *
 * Follows ui_skill: card -> detail modal, status badge, empty state, minimal
 * click cost, and the existing Tailwind vocabulary.
 */
import { createApp, reactive, computed } from 'vue';

const API = '/api/flow_settings';

const FlowSettingTable = {
  props: {
    // Restrict to one flow. Empty = every flow.
    flowKey: { type: String, default: '' },
    // Called with (step, kind) when a question/value cell is clicked.
    onStep: { type: Function, default: null },
  },
  emits: ['step'],
  template: `
  <div class="rounded-2xl border border-line bg-panel shadow-panel">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
      <div>
        <h3 class="text-sm font-semibold text-ink">Flow setting</h3>
        <p class="mt-0.5 text-[11px] text-muted">
          <span class="mono">flow_setting</span> ·
          <span class="mono">{{ shown.length }}</span> steps ·
          click a <b>question</b> or <b>value</b> to drive the flow
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span v-if="cursor" class="rounded-full bg-accent/10 px-2.5 py-1 text-[11px] font-medium text-accent">
          at step {{ cursor }}
        </span>
        <button type="button" @click="load"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-xs hover:bg-soft">
          {{ busy ? 'Loading…' : 'Refresh' }}</button>
        <button type="button" @click="startCreate"
          class="rounded-xl bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
          + New step</button>
      </div>
    </div>

    <div v-if="msg" class="mx-4 mt-3 rounded-xl border px-3 py-2 text-xs"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">{{ msg }}</div>

    <div class="overflow-auto">
      <table class="min-w-full text-left text-sm">
        <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
          <tr>
            <th class="px-3 py-2 font-semibold">flow</th>
            <th class="px-3 py-2 font-semibold">step</th>
            <th class="px-3 py-2 font-semibold">question</th>
            <th class="px-3 py-2 font-semibold">value</th>
            <th class="px-3 py-2 font-semibold">next</th>
            <th class="px-3 py-2 font-semibold">action</th>
            <th class="px-3 py-2 font-semibold"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in shown" :key="r.id"
            class="border-b border-line/80"
            :class="Number(r.step_no) === cursor ? 'bg-accent/5' : ''">
            <td class="px-3 py-2 text-[11px] mono text-muted">{{ r.flow_key }}</td>
            <td class="px-3 py-2 text-xs mono font-semibold text-ink">{{ r.step_no }}</td>
            <td class="px-3 py-2">
              <button type="button" @click="clickStep(r, 'question')"
                class="w-full rounded-lg px-2 py-1 text-left text-sm text-ink transition hover:bg-soft hover:ring-1 hover:ring-accent">
                {{ r.question || '—' }}
              </button>
            </td>
            <td class="px-3 py-2">
              <button type="button" @click="clickStep(r, 'value')"
                class="w-full rounded-lg px-2 py-1 text-left text-xs mono text-accent transition hover:bg-soft hover:ring-1 hover:ring-accent">
                {{ r.value || '—' }}
              </button>
            </td>
            <td class="px-3 py-2 text-xs mono text-muted">{{ r.next_step ?? '—' }}</td>
            <td class="px-3 py-2 text-[11px] mono text-muted">{{ r.action || '—' }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              <button type="button" @click="runStep(r)"
                class="mr-1 rounded-lg bg-accent px-2 py-0.5 text-xs font-medium text-white hover:opacity-90">Run</button>
              <button type="button" @click="startEdit(r)"
                class="mr-1 rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">Edit</button>
              <button type="button" @click="remove(r)"
                class="rounded-lg border border-rose-200 bg-rose-50 px-2 py-0.5 text-xs text-rose-700 hover:bg-rose-100">
                Delete</button>
            </td>
          </tr>
          <tr v-if="!shown.length">
            <td colspan="7" class="px-3 py-6 text-center text-xs text-muted">
              無相關記錄。 No flow steps.
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- editor modal (ui_skill: ✕ / backdrop / Escape) -->
    <div v-if="editing" :key="editing.id || 'new'"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click="onBackdrop">
      <div class="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <div class="mb-3 flex items-start justify-between gap-3">
          <h3 class="text-base font-semibold text-ink">
            {{ editing.id ? 'Edit step #' + editing.id : 'New flow step' }}
          </h3>
          <button type="button" @click="closeEdit"
            class="rounded-lg border border-line px-2 py-0.5 text-sm hover:bg-soft">✕</button>
        </div>

        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block">
            <span class="text-xs font-medium text-muted">flow_key</span>
            <input v-model="editing.flow_key"
              class="mono mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-muted">step_no</span>
            <input v-model.number="editing.step_no" type="number"
              class="mono mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          </label>
        </div>

        <label class="mt-3 block">
          <span class="text-xs font-medium text-muted">question</span>
          <input v-model="editing.question"
            class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
        </label>

        <label class="mt-3 block">
          <span class="text-xs font-medium text-muted">value</span>
          <input v-model="editing.value"
            class="mono mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
        </label>

        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <label class="block">
            <span class="text-xs font-medium text-muted">next_step</span>
            <input v-model.number="editing.next_step" type="number"
              class="mono mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-muted">action</span>
            <input v-model="editing.action"
              class="mono mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          </label>
        </div>

        <label class="mt-3 block">
          <span class="text-xs font-medium text-muted">description</span>
          <input v-model="editing.description"
            class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
        </label>

        <div class="mt-4 flex items-center justify-end gap-2">
          <button type="button" @click="closeEdit"
            class="rounded-xl border border-line bg-panel px-4 py-2 text-sm hover:bg-soft">Cancel</button>
          <button type="button" @click="save" :disabled="busy"
            class="rounded-xl px-4 py-2 text-sm font-semibold text-white shadow-panel"
            :class="busy ? 'cursor-not-allowed bg-slate-300' : 'bg-accent hover:opacity-90'">
            {{ busy ? 'Saving…' : 'Save' }}
          </button>
        </div>
      </div>
    </div>
  </div>
  `,

  setup(props, { emit }) {
    const s = reactive({
      rows: [],
      cursor: null,
      editing: null,
      busy: false,
      msg: '',
      msgOk: true,
    });

    const shown = computed(() => {
      if (!props.flowKey) return s.rows;
      return s.rows.filter((r) => r.flow_key === props.flowKey);
    });

    async function load() {
      s.busy = true;
      try {
        const url = API + (props.flowKey ? '?flow_key=' + encodeURIComponent(props.flowKey) : '');
        const res = await fetch(url);
        const data = await res.json();
        s.rows = data.rows || [];
      } catch (e) {
        s.msg = 'Load failed: ' + (e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    /** Clicking a question or value drives the flow: select + advance. */
    function clickStep(r, kind) {
      s.cursor = Number(r.step_no);
      s.msg = 'Step ' + r.step_no + ' selected via ' + kind + ' (' + (r.value || '—') + ').';
      s.msgOk = true;
      if (typeof props.onStep === 'function') props.onStep(r, kind);
      emit('step', r, kind);
    }

    /**
     * Run this step: hand off to the Setting page with the step in the URL, so
     * the flow actually DRIVES the setting UI instead of only selecting a row.
     * A new tab keeps the flow table visible for the next step.
     */
    function runStep(r) {
      const url = '/llm-tasks/chat_center/setting?flow_key=' +
        encodeURIComponent(r.flow_key) + '&step=' + encodeURIComponent(r.step_no);
      window.open(url, '_blank');
    }

    function startCreate() {
      s.editing = {
        id: null,
        flow_key: props.flowKey || 'chat_center_identity',
        step_no: (shown.value.length || 0) + 1,
        question: '',
        value: '',
        next_step: null,
        action: '',
        description: '',
      };
      s.msg = '';
    }

    function startEdit(r) {
      s.editing = {
        id: r.id,
        flow_key: r.flow_key,
        step_no: Number(r.step_no),
        question: r.question || '',
        value: r.value || '',
        next_step: r.next_step == null ? null : Number(r.next_step),
        action: r.action || '',
        description: r.description || '',
      };
      s.msg = '';
    }

    function closeEdit() {
      s.editing = null;
    }

    function onBackdrop(e) {
      if (e.target === e.currentTarget) closeEdit();
    }

    async function save() {
      const e = s.editing;
      if (!e) return;
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch(API, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            flow_key: e.flow_key,
            step_no: Number(e.step_no),
            question: e.question,
            value: e.value,
            next_step: e.next_step === null || e.next_step === '' ? null : Number(e.next_step),
            action: e.action,
            description: e.description,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          s.msg = data.detail || data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.msg = (data.action === 'updated' ? 'Updated' : 'Created') + ' step ' + e.step_no + '.';
        s.msgOk = true;
        closeEdit();
        await load();
      } catch (err) {
        s.msg = String(err.message || err);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    async function remove(r) {
      if (!confirm('Delete flow step ' + r.flow_key + ' #' + r.step_no + '?')) return;
      s.busy = true;
      try {
        const res = await fetch(API + '/' + r.id, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          s.msg = data.detail || data.error || 'HTTP ' + res.status;
          s.msgOk = false;
        } else {
          s.msg = 'Deleted step ' + r.step_no + '.';
          s.msgOk = true;
          await load();
        }
      } catch (e) {
        s.msg = String(e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    load();

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity. Object.assign keeps binding.
    return Object.assign(s, {
      shown,
      load,
      clickStep,
      runStep,
      startCreate,
      startEdit,
      closeEdit,
      onBackdrop,
      save,
      remove,
    });
  },
};

export function mountFlowSettingTable(el, props = {}) {
  const app = createApp(FlowSettingTable, props);
  app.mount(el);
  return app;
}

export default FlowSettingTable;
