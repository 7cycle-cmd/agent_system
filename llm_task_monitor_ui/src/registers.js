/**
 * registers.js — skill / study / prompt / workflow registers (Vue 3).
 *
 * WHY THIS EXISTS
 * ---------------
 * The formula is
 *     prompt   = skill + study + wording
 *     workflow = ordered sequence of INDEPENDENT prompts
 * but the UI only ever showed `format_templates` (coords.db), which conflates
 * all three axes into one row. So a prompt could not be SEEN as a composition,
 * and a workflow had nowhere to appear at all.
 *
 * This view shows the chain the registers actually store:
 *     skill -> study -> prompt -> workflow (ordered steps)
 *
 * It is a VIEW of the SSOT, not a new store: every row comes from
 * /api/registers/*, which reads agent.db. No new table, no new endpoint.
 *
 * Follows ui_skill: card -> detail, status badge, empty state, minimal click
 * cost, and the existing Tailwind vocabulary (rounded-2xl / border-line /
 * bg-panel / shadow-panel / text-ink / text-muted / text-accent / mono).
 */
import { createApp, reactive, computed } from 'vue';

const API = '/api/registers';

const Registers = {
  template: `
  <div class="space-y-4">
    <!-- header -->
    <div class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <h3 class="text-sm font-semibold text-ink">Registers</h3>
          <p class="mt-0.5 text-[11px] text-muted">
            <span class="mono">prompt = skill + study + wording</span> ·
            <span class="mono">workflow = ordered prompts</span> · DB-driven
          </p>
        </div>
        <div class="flex items-center gap-2">
          <span v-if="orphans" class="rounded-lg border px-2 py-1 text-[11px]"
            :class="orphans.ok
              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
              : 'border-rose-200 bg-rose-50 text-rose-700'">
            {{ orphans.ok ? 'no orphans' : 'ORPHANS' }}
          </span>
          <button type="button" @click="load"
            class="rounded-xl border border-line bg-panel px-3 py-1.5 text-xs hover:bg-soft">
            {{ busy ? 'Loading…' : 'Refresh' }}</button>
        </div>
      </div>

      <div v-if="msg" class="mx-4 mt-3 rounded-xl border px-3 py-2 text-xs"
        :class="msgOk
          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
          : 'border-rose-200 bg-rose-50 text-rose-700'">{{ msg }}</div>

      <!-- counts -->
      <div class="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4">
        <div v-for="c in counts" :key="c.label"
          class="rounded-xl border border-line bg-soft/50 px-3 py-2">
          <div class="text-[11px] uppercase tracking-wide text-muted">{{ c.label }}</div>
          <div class="mono text-lg font-semibold text-ink">{{ c.value }}</div>
        </div>
      </div>
    </div>

    <!-- the chain -->
    <div class="grid gap-4 lg:grid-cols-2">
      <!-- skills -->
      <section class="rounded-2xl border border-line bg-panel shadow-panel">
        <div class="border-b border-line px-4 py-2.5">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-muted">
            Skills <span class="mono">({{ skills.length }})</span></h4>
        </div>
        <div v-if="!skills.length" class="px-4 py-6 text-center text-xs text-muted">No skills.</div>
        <ul v-else class="divide-y divide-line">
          <li v-for="s in skills" :key="s.skill_id"
            class="flex items-center justify-between gap-2 px-4 py-2.5">
            <div class="min-w-0">
              <div class="mono truncate text-sm text-ink">{{ s.skill_key }}</div>
              <div class="truncate text-[11px] text-muted">{{ s.name }}</div>
            </div>
            <span class="mono shrink-0 rounded-lg border border-line bg-soft px-2 py-0.5 text-[10px] text-muted">
              {{ s.parser || '—' }}</span>
          </li>
        </ul>
      </section>

      <!-- studies -->
      <section class="rounded-2xl border border-line bg-panel shadow-panel">
        <div class="border-b border-line px-4 py-2.5">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-muted">
            Studies <span class="mono">({{ studies.length }})</span></h4>
        </div>
        <div v-if="!studies.length" class="px-4 py-6 text-center text-xs text-muted">No studies.</div>
        <ul v-else class="divide-y divide-line">
          <li v-for="s in studies" :key="s.study_id" class="px-4 py-2.5">
            <div class="mono truncate text-sm text-ink">{{ s.study_key }}</div>
            <div class="mt-0.5 text-[11px] text-muted">
              skill <span class="mono text-accent">{{ s.skill_key }}</span>
              · <span class="mono">{{ fieldCount(s) }}</span> fields
            </div>
          </li>
        </ul>
      </section>

      <!-- prompts -->
      <section class="rounded-2xl border border-line bg-panel shadow-panel">
        <div class="border-b border-line px-4 py-2.5">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-muted">
            Prompts <span class="mono">({{ prompts.length }})</span></h4>
        </div>
        <div v-if="!prompts.length" class="px-4 py-6 text-center text-xs text-muted">No prompts.</div>
        <ul v-else class="divide-y divide-line">
          <li v-for="p in prompts" :key="p.prompt_id" class="px-4 py-2.5">
            <div class="mono truncate text-sm text-ink">{{ p.prompt_key }}</div>
            <div class="mt-0.5 text-[11px] text-muted">
              skill <span class="mono text-accent">{{ p.skill_key }}</span>
              · study <span class="mono text-accent">{{ p.study_key }}</span>
              · tpl <span class="mono">{{ p.template_id ?? '—' }}</span>
            </div>
          </li>
        </ul>
      </section>

      <!-- workflows -->
      <section class="rounded-2xl border border-line bg-panel shadow-panel">
        <div class="border-b border-line px-4 py-2.5">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-muted">
            Workflows <span class="mono">({{ workflows.length }})</span></h4>
        </div>
        <div v-if="!workflows.length" class="px-4 py-6 text-center text-xs text-muted">No workflows.</div>
        <ul v-else class="divide-y divide-line">
          <li v-for="w in workflows" :key="w.workflow_id" class="px-4 py-2.5">
            <button type="button" class="w-full text-left" @click="openWorkflow(w.workflow_key)">
              <div class="mono truncate text-sm text-ink">{{ w.workflow_key }}</div>
              <div class="truncate text-[11px] text-muted">{{ w.name }}</div>
            </button>
          </li>
        </ul>
      </section>
    </div>

    <!-- workflow detail: the ordered steps -->
    <section v-if="detail" class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="flex items-center justify-between border-b border-line px-4 py-2.5">
        <h4 class="text-xs font-semibold uppercase tracking-wide text-muted">
          Workflow <span class="mono text-accent">{{ detail.workflow_key }}</span> — ordered steps</h4>
        <button type="button" @click="detail = null"
          class="rounded-lg border border-line px-2 py-0.5 text-[11px] hover:bg-soft">close</button>
      </div>
      <div class="overflow-auto">
        <table class="min-w-full text-left text-sm">
          <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th class="px-3 py-2 font-semibold">step</th>
              <th class="px-3 py-2 font-semibold">prompt</th>
              <th class="px-3 py-2 font-semibold">skill</th>
              <th class="px-3 py-2 font-semibold">study</th>
              <th class="px-3 py-2 font-semibold">tpl</th>
              <th class="px-3 py-2 font-semibold">final</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            <tr v-for="s in detail.steps" :key="s.step_no">
              <td class="mono px-3 py-2 text-muted">{{ s.step_no }}</td>
              <td class="mono px-3 py-2 text-ink">{{ s.prompt_key }}</td>
              <td class="mono px-3 py-2 text-accent">{{ s.skill_key }}</td>
              <td class="mono px-3 py-2 text-accent">{{ s.study_key }}</td>
              <td class="mono px-3 py-2 text-muted">{{ s.template_id ?? '—' }}</td>
              <td class="px-3 py-2">
                <span v-if="s.is_final"
                  class="rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700">final</span>
                <span v-else class="text-[11px] text-muted">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>`,
  setup() {
    const s = reactive({
      skills: [],
      studies: [],
      prompts: [],
      workflows: [],
      orphans: null,
      detail: null,
      busy: false,
      msg: '',
      msgOk: true,
    });

    async function get(path) {
      const r = await fetch(API + path);
      if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status);
      return r.json();
    }

    async function load() {
      s.busy = true;
      s.msg = '';
      try {
        const [skills, studies, prompts, workflows, orphans] = await Promise.all([
          get('/skills'),
          get('/studies'),
          get('/prompts'),
          get('/workflows'),
          get('/verify'),
        ]);
        s.skills = skills;
        s.studies = studies;
        s.prompts = prompts;
        s.workflows = workflows;
        s.orphans = orphans;
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Load failed: ' + (e.message || e);
      } finally {
        s.busy = false;
      }
    }

    async function openWorkflow(key) {
      try {
        s.detail = await get('/workflows/' + encodeURIComponent(key));
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Workflow load failed: ' + (e.message || e);
      }
    }

    function fieldCount(study) {
      try {
        const f = JSON.parse(study.fields_json || '[]');
        return Array.isArray(f) ? f.length : 0;
      } catch {
        return 0;
      }
    }

    const counts = computed(() => [
      { label: 'skills', value: s.skills.length },
      { label: 'studies', value: s.studies.length },
      { label: 'prompts', value: s.prompts.length },
      { label: 'workflows', value: s.workflows.length },
    ]);

    load();

    return Object.assign(s, { load, openWorkflow, fieldCount, counts });
  },
};

export function mountRegisters(el) {
  const app = createApp(Registers);
  app.mount(el);
  return app;
}
