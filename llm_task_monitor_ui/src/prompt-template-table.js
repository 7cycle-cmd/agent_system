/**
 * prompt-template-table.js — DB-driven prompt-template table (Vue 3).
 *
 * WHY THIS EXISTS
 * ---------------
 * The Chat Center Setting page needs the prompt templates to be EDITABLE in the
 * UI ("table setting in ui, so user can have that easy"). The rows live in
 * `format_templates` (coords.db) and are already served by /api/templates
 * (GET / POST / PUT / DELETE) — so this component is a second VIEW of the same
 * SSOT, not a new store. No new endpoint, no new table.
 *
 * Two consumers mount it:
 *   - /llm-tasks/chat_center/setting  (STEP 1 right side, "prompt template" mode)
 *   - /llm-tasks/chat_center/flow     (the 3-step flow page)
 *
 * Follows ui_skill: card -> detail modal, status badge, empty state, minimal
 * click cost, and the existing Tailwind vocabulary (rounded-2xl / border-line /
 * bg-panel / shadow-panel / text-ink / text-muted / text-accent / mono).
 */
import { createApp, reactive, computed } from 'vue';

const API = '/api/templates';

/** The two Chat Center identity templates (user spec 2026-09-20). */
export const IDENTITY_KEYS = ['worker_identity', 'worker_identity_confirm'];

const PromptTemplateTable = {
  props: {
    // When true, only the identity pair is listed (Chat Center context).
    identityOnly: { type: Boolean, default: false },
    // When true, each row gets a "Use" button.
    pickable: { type: Boolean, default: false },
    // Callback invoked with the picked row. A prop (not just an emit) because
    // this component is mounted imperatively, where a parent cannot bind @pick.
    onPick: { type: Function, default: null },
  },
  emits: ['pick'],
  template: `
  <div class="rounded-2xl border border-line bg-panel shadow-panel">
    <div class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
      <div>
        <h3 class="text-sm font-semibold text-ink">Prompt templates</h3>
        <p class="mt-0.5 text-[11px] text-muted">
          <span class="mono">format_templates</span> ·
          <span class="mono">{{ shown.length }}</span> of
          <span class="mono">{{ rows.length }}</span> rows · DB-driven
        </p>
      </div>
      <div class="flex items-center gap-2">
        <label v-if="!identityOnly" class="flex items-center gap-1 py-1 text-[11px] text-muted">
          <input type="checkbox" v-model="showAll" class="h-3 w-3" /> show inactive
        </label>
        <button
          type="button"
          @click="load"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-xs hover:bg-soft"
        >{{ busy ? 'Loading…' : 'Refresh' }}</button>
        <button
          type="button"
          @click="startCreate"
          class="rounded-xl bg-accent px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
        >+ New</button>
      </div>
    </div>

    <div v-if="msg" class="mx-4 mt-3 rounded-xl border px-3 py-2 text-xs"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">{{ msg }}</div>

    <!-- table -->
    <div class="overflow-auto">
      <table class="min-w-full text-left text-sm">
        <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
          <tr>
            <th class="px-3 py-2 font-semibold">id</th>
            <th class="px-3 py-2 font-semibold">key</th>
            <th class="px-3 py-2 font-semibold">name</th>
            <th class="px-3 py-2 font-semibold">mode</th>
            <th class="px-3 py-2 font-semibold">active</th>
            <th class="px-3 py-2 font-semibold">updated</th>
            <th class="px-3 py-2 font-semibold"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in shown" :key="r.id" class="border-b border-line/80">
            <td class="px-3 py-2 text-xs mono text-muted">{{ r.id }}</td>
            <td class="px-3 py-2 text-xs mono text-ink">{{ r.prompt_setting_key }}</td>
            <td class="px-3 py-2 text-sm text-ink">{{ r.name }}</td>
            <td class="px-3 py-2 text-xs mono text-muted">{{ r.mode || 'ask' }}</td>
            <td class="px-3 py-2">
              <span class="rounded-full px-2 py-0.5 text-[10px]"
                :class="Number(r.is_active) === 1
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-amber-100 text-amber-700'">
                {{ Number(r.is_active) === 1 ? 'on' : 'off' }}
              </span>
            </td>
            <td class="px-3 py-2 text-[11px] text-muted">{{ r.updated_at || r.created_at || '' }}</td>
            <td class="px-3 py-2 whitespace-nowrap">
              <button v-if="pickable" type="button" @click="pick(r)"
                class="mr-1 rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">Use</button>
              <button type="button" @click="startEdit(r)"
                class="mr-1 rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">Edit</button>
              <button type="button" @click="remove(r)"
                :disabled="r.prompt_setting_key === 'verdict_3line'"
                class="rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700 hover:bg-rose-100 disabled:opacity-40">
                Delete</button>
            </td>
          </tr>
          <tr v-if="!shown.length">
            <td colspan="7" class="px-3 py-6 text-center text-xs text-muted">
              無相關記錄。 No prompt template rows.
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
            {{ editing.id ? 'Edit template #' + editing.id : 'New template' }}
          </h3>
          <button type="button" @click="closeEdit"
            class="rounded-lg border border-line px-2 py-0.5 text-sm hover:bg-soft">✕</button>
        </div>

        <div class="grid gap-3 sm:grid-cols-2">
          <label class="block">
            <span class="text-xs font-medium text-muted">prompt_setting_key</span>
            <input v-model="editing.prompt_setting_key"
              :disabled="editing.prompt_setting_key === 'verdict_3line'"
              class="mono mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent disabled:bg-soft/40" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-muted">name</span>
            <input v-model="editing.name"
              class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          </label>
          <label class="block">
            <span class="text-xs font-medium text-muted">mode</span>
            <select v-model="editing.mode"
              class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent">
              <option value="ask">ask</option>
              <option value="plan">plan</option>
              <option value="agent">agent</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs font-medium text-muted">catalog_id</span>
            <input v-model.number="editing.catalog_id" type="number"
              class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
          </label>
        </div>

        <label class="mt-3 block">
          <span class="text-xs font-medium text-muted">description</span>
          <input v-model="editing.description"
            class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent" />
        </label>

        <label class="mt-3 block">
          <span class="text-xs font-medium text-muted">instruction</span>
          <textarea v-model="editing.instruction" rows="10"
            class="mono mt-1 w-full resize-y rounded-xl border border-line bg-panel px-3 py-2 text-xs text-ink outline-none focus:border-accent"></textarea>
        </label>

        <label class="mt-3 flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" v-model="editing.is_active" class="h-3 w-3" /> is_active
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
      showAll: false,
      editing: null,
      busy: false,
      msg: '',
      msgOk: true,
    });

    const shown = computed(() => {
      let rows = s.rows;
      if (props.identityOnly) {
        rows = rows.filter((r) => IDENTITY_KEYS.includes(r.prompt_setting_key));
      }
      return rows;
    });

    async function load() {
      s.busy = true;
      try {
        const res = await fetch(API + (s.showAll ? '?all=1' : ''));
        const data = await res.json();
        s.rows = Array.isArray(data) ? data : data.rows || [];
      } catch (e) {
        s.msg = 'Load failed: ' + (e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    function startCreate() {
      s.editing = {
        id: null,
        prompt_setting_key: '',
        name: '',
        description: '',
        instruction: '',
        mode: 'ask',
        catalog_id: 0,
        is_active: true,
      };
      s.msg = '';
    }

    function startEdit(r) {
      s.editing = {
        id: r.id,
        prompt_setting_key: r.prompt_setting_key,
        name: r.name,
        description: r.description || '',
        instruction: r.instruction || '',
        mode: r.mode || 'ask',
        catalog_id: Number(r.catalog_id || 0),
        is_active: Number(r.is_active) === 1,
      };
      s.msg = '';
    }

    function closeEdit() {
      s.editing = null;
    }

    /** Row picked: notify the parent (prop callback) and the emit listener. */
    function pick(r) {
      if (typeof props.onPick === 'function') props.onPick(r);
      emit('pick', r);
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
        const body = {
          prompt_setting_key: e.prompt_setting_key,
          name: e.name,
          description: e.description,
          instruction: e.instruction,
          mode: e.mode,
          catalog_id: Number(e.catalog_id || 0),
          is_active: e.is_active ? 1 : 0,
        };
        const res = await fetch(e.id ? API + '/' + e.id : API, {
          method: e.id ? 'PUT' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          s.msg = data.detail || data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.msg = e.id ? 'Updated #' + e.id : 'Created #' + (data.id ?? '');
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
      if (!confirm('Soft-delete template #' + r.id + ' (' + r.prompt_setting_key + ')?')) return;
      s.busy = true;
      try {
        const res = await fetch(API + '/' + r.id, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          s.msg = data.detail || data.error || 'HTTP ' + res.status;
          s.msgOk = false;
        } else {
          s.msg = 'Deleted (' + (data.mode || 'soft') + ').';
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
      startCreate,
      startEdit,
      closeEdit,
      onBackdrop,
      pick,
      save,
      remove,
    });
  },
};

export function mountPromptTemplateTable(el, props = {}) {
  const app = createApp(PromptTemplateTable, props);
  app.mount(el);
  return app;
}

export default PromptTemplateTable;
