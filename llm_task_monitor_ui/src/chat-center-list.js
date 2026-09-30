/**
 * chat-center-list.js — "Discovery List" tab for Chat Center.
 *
 * WHY THIS EXISTS
 * ---------------
 * `chat_center_message` gained title / event / evidence_ref / rule_version /
 * measured_effect so a DISCOVERY is a first-class, listable record. The existing
 * Chat Center UI is a 3-step Q/A flow scoped to ONE chat, so a row written by a
 * script could not be seen or checked at all. This tab lists every row with its
 * citation, so the record can be confirmed in the browser.
 *
 * Follows ui_skill: card -> detail modal, status badge, empty state,
 * minimal click cost (one click shows everything, no navigation), and the
 * existing Tailwind vocabulary (rounded-2xl / border-line / bg-panel /
 * shadow-panel / text-ink / text-muted / text-accent / mono).
 */
import { createApp, reactive, computed } from 'vue';

const HISTORY_URL = '/api/chat_center/history';

/** Badge colours per `event`. Unknown events fall back to neutral. */
const EVENT_CLASSES = {
  discovery: 'bg-emerald-100 text-emerald-700',
  lesson: 'bg-amber-100 text-amber-700',
  answer: 'bg-sky-100 text-sky-700',
  question: 'bg-slate-100 text-slate-700',
};

/**
 * Badge colours per WORKFLOW `status`.
 *
 * `status` is a DIFFERENT axis from `event`: `event` says what KIND of record
 * this is (question / answer / discovery / lesson), `status` says where the
 * WORK stands (done / ask / progressive / QC). Both are shown, because a
 * discovery can be finished or still waiting on a decision.
 *
 * `ask` is amber on purpose: it is the one state that needs a HUMAN. A reader
 * scanning the list should see the blocked items without opening them.
 */
const STATUS_CLASSES = {
  // THE HUMAN'S TURN LIFECYCLE (2026-09-25):
  //   "default = draft -> send to logic generator ... -> pending"
  //   "status : draft / penping / progressing / completed"
  //
  // MEASURED, and this is the defect: `draft` is the MOST COMMON value in
  // `chat_center_message` (1899 rows) and it was the ONLY one with no colour.
  draft: 'bg-soft text-muted',
  pending: 'bg-amber-100 text-amber-800',
  progressing: 'bg-sky-100 text-sky-700',
  completed: 'bg-emerald-100 text-emerald-700',
  // The four that already existed (164 rows use them; they cannot be dropped).
  done: 'bg-emerald-100 text-emerald-700',
  ask: 'bg-amber-100 text-amber-800',
  progressive: 'bg-sky-100 text-sky-700',
  qc: 'bg-violet-100 text-violet-700',
};

function eventClass(ev) {
  return EVENT_CLASSES[String(ev || '').toLowerCase()] || 'bg-slate-100 text-slate-600';
}

function statusClass(st) {
  return STATUS_CLASSES[String(st || '').toLowerCase()] || 'bg-slate-100 text-slate-600';
}

function preview(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return t.length > 180 ? t.slice(0, 180) + '…' : t;
}

const DiscoveryList = {
  template: `
  <div class="mx-auto max-w-6xl">
    <div class="mb-4 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 class="text-lg font-semibold text-ink">Discovery List</h2>
        <p class="mt-0.5 text-sm text-muted">
          Every <span class="mono">chat_center_message</span> row ·
          <span class="mono">{{ rows.length }}</span> of
          <span class="mono">{{ total }}</span> · a finding must carry an
          <span class="mono">evidence_ref</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <input
          v-model="filter"
          placeholder="filter title / ref / content…"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
        />
        <button
          type="button"
          @click="load"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
        >{{ busy ? 'Loading…' : 'Refresh' }}</button>
      </div>
    </div>

    <div v-if="msg" class="mb-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">{{ msg }}</div>

    <div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">
      <table class="w-full border-collapse text-left text-sm">
        <thead class="bg-soft/60 text-[11px] uppercase tracking-wide text-muted">
          <tr>
            <th class="px-3 py-2 font-semibold">#</th>
            <th class="px-3 py-2 font-semibold">Event</th>
            <th class="px-3 py-2 font-semibold">Status</th>
            <th class="px-3 py-2 font-semibold">Title</th>
            <th class="px-3 py-2 font-semibold">Evidence</th>
            <th class="px-3 py-2 font-semibold">Skill</th>
            <th class="px-3 py-2 font-semibold whitespace-nowrap">When</th>
            <th class="px-3 py-2"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in shown"
            :key="r.id"
            :data-discovery="r.id"
            class="border-t border-line align-top transition hover:bg-soft/50"
          >
            <td class="mono px-3 py-2 text-muted">#{{ r.id }}</td>
            <td class="px-3 py-2">
              <span class="rounded-full px-2 py-0.5 text-[11px] font-medium"
                :class="eventClass(r.event)">{{ r.event || r.role || '-' }}</span>
            </td>
            <td class="px-3 py-2">
              <span v-if="r.status" class="rounded-full px-2 py-0.5 text-[11px] font-medium"
                :class="statusClass(r.status)">{{ r.status }}</span>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <td class="max-w-[28rem] px-3 py-2">
              <div class="font-semibold text-ink">{{ r.title || '(no title)' }}</div>
              <div class="mt-0.5 line-clamp-2 text-xs text-muted">{{ preview(r.content) }}</div>
            </td>
            <td class="px-3 py-2 whitespace-nowrap">
              <span v-if="r.evidence_ref" class="font-medium text-emerald-600"
                :title="r.evidence_ref">cited</span>
              <span v-else class="font-medium text-rose-500">no ref</span>
            </td>
            <td class="mono px-3 py-2 text-xs text-muted">{{ r.skill_name || '-' }}</td>
            <td class="px-3 py-2 whitespace-nowrap text-[11px] text-muted">
              {{ r.created_at || '-' }}
            </td>
            <td class="px-3 py-2 text-right">
              <button
                type="button"
                class="rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft"
                @click="open(r)"
              >view</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="!busy && !shown.length"
      class="mt-3 rounded-2xl border border-line bg-panel p-6 text-center text-sm text-muted">
      <span v-if="filter">No row matches "{{ filter }}".</span>
      <span v-else>Nothing written to
        <span class="mono">chat_center_message</span> yet.</span>
    </div>

    <!-- detail modal (ui_skill: ✕ / backdrop / Escape) -->
    <div v-if="selected" id="discovery-modal" :key="selected.id"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click="onBackdrop">
      <div class="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <div class="mb-3 flex items-start justify-between gap-3">
          <div>
            <div class="flex flex-wrap items-center gap-2 text-[11px]">
              <span class="rounded-full px-2 py-0.5 font-medium"
                :class="eventClass(selected.event)">{{ selected.event || selected.role }}</span>
              <span v-if="selected.status" class="rounded-full px-2 py-0.5 font-medium"
                :class="statusClass(selected.status)">{{ selected.status }}</span>
              <span class="mono text-muted">#{{ selected.id }}</span>
              <span class="text-muted">{{ selected.created_at }}</span>
            </div>
            <h3 class="mt-1 text-base font-semibold text-ink">
              {{ selected.title || '(no title)' }}
            </h3>
          </div>
          <button type="button" @click="close"
            class="shrink-0 rounded-lg border border-line px-2 py-1 text-sm hover:bg-soft">✕</button>
        </div>

        <div class="grid gap-2 text-xs sm:grid-cols-2">
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">chat_id</div>
            <div class="mono text-ink">{{ selected.chat_id ?? '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">role</div>
            <div class="text-ink">{{ selected.role || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">status</div>
            <div class="text-ink">{{ selected.status || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2 sm:col-span-2">
            <div class="text-muted">session_id</div>
            <div class="mono break-all text-ink">{{ selected.session_id || '—' }}</div></div>
          <div v-if="selected.skill_id || selected.skill_name"
            class="rounded-xl border border-line bg-soft/40 px-3 py-2 sm:col-span-2">
            <div class="text-muted">skill</div>
            <div class="mono text-ink">{{ selected.skill_id || '—' }}<span v-if="selected.skill_name"> · {{ selected.skill_name }}</span></div></div>
          <div v-if="selected.rule_version"
            class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">rule_version</div>
            <div class="mono text-ink">{{ selected.rule_version }}</div></div>
        </div>

        <div class="mt-3 rounded-xl border px-3 py-2"
          :class="selected.evidence_ref
            ? 'border-emerald-200 bg-emerald-50'
            : 'border-rose-200 bg-rose-50'">
          <div class="text-xs font-semibold"
            :class="selected.evidence_ref ? 'text-emerald-700' : 'text-rose-700'">
            evidence_ref {{ selected.evidence_ref ? '' : '— MISSING (citation discipline)' }}
          </div>
          <div class="mono mt-1 break-all text-xs text-ink">{{ selected.evidence_ref || '—' }}</div>
        </div>

        <div v-if="selected.measured_effect" class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">measured_effect</div>
          <div class="whitespace-pre-wrap text-xs text-ink">{{ selected.measured_effect }}</div>
        </div>

        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">content</div>
          <div class="whitespace-pre-wrap text-sm text-ink">{{ selected.content || '—' }}</div>
        </div>
      </div>
    </div>
  </div>
  `,

  setup() {
    const s = reactive({
      rows: [],
      total: 0,
      filter: '',
      busy: false,
      msg: '',
      msgOk: true,
      selected: null,
    });

    const shown = computed(() => {
      const f = s.filter.trim().toLowerCase();
      if (!f) return s.rows;
      return s.rows.filter((r) =>
        ['title', 'evidence_ref', 'content', 'skill_name', 'event', 'status', 'id']
          .map((k) => String(r[k] == null ? '' : r[k]).toLowerCase())
          .some((v) => v.includes(f))
      );
    });

    async function load() {
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch(HISTORY_URL + '?limit=200');
        const data = await res.json();
        if (!res.ok || !data.ok) {
          throw new Error((data && data.error) || ('HTTP ' + res.status));
        }
        // Newest first: the API returns oldest-first, and a discovery list is
        // read from the top.
        s.rows = (data.rows || []).slice().reverse();
        s.total = data.total || s.rows.length;
        s.msgOk = true;
      } catch (e) {
        s.msgOk = false;
        s.msg = 'History API unavailable: ' + (e.message || e);
      } finally {
        s.busy = false;
      }
    }

    let escHandler = null;

    function onBackdrop(e) {
      if (e.target && e.target.id === 'discovery-modal') close();
    }

    function open(row) {
      // DO NOT remove the modal node here. It is rendered by Vue's v-if, and
      // removing a Vue-managed node behind Vue's back makes Vue believe it still
      // exists, so a later open never re-creates it (measured: the second open
      // silently failed). ui_skill's "防重疊 remove" advice is for IMPERATIVE
      // DOM popups (openTaskDetailPopup), not for a v-if modal. v-if already
      // guarantees a single node, and the :key forces a clean rebuild.
      s.selected = row;
      escHandler = (e) => {
        if (e.key === 'Escape') close();
      };
      window.addEventListener('keydown', escHandler);
    }

    function close() {
      s.selected = null;
      if (escHandler) {
        window.removeEventListener('keydown', escHandler);
        escHandler = null;
      }
    }

    load();

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // its properties and breaks two-way binding, so `rows`/`total`/`filter`
    // stayed at their initial values and the header rendered "0 of 0" while the
    // cards were present. Object.assign onto the SAME proxy keeps reactivity.
    // (Same pitfall and fix as chat-center.js.)
    return Object.assign(s, { shown, load, open, close, onBackdrop, preview,
                              eventClass, statusClass });
  },
};

export function mountChatCenterList(el) {
  const app = createApp(DiscoveryList);
  app.mount(el);
  return app;
}
