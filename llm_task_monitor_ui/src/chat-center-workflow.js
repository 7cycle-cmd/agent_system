/**
 * chat-center-workflow.js — the TICKET + CHAT pairing view for Chat Center (Vue 3).
 *
 * WHY THIS EXISTS
 * ---------------
 * The user's flow is:
 *
 *     middleware (chat_center submit) opens a TICKET
 *       -> the ticket goes to a chat room to get a chat_id
 *       -> `chat_registry` pairs the two
 *
 * The 3-step flow page is scoped to ONE chat, and the Discovery List shows
 * `chat_center_message` rows. Neither answers "which ticket is being worked in
 * which chat room" — that is the pairing, and it had nowhere to appear.
 *
 * RENAMED from "case" (user, 2026-09-21: "why not name = chat_registry / not
 * easy for mis-understand / rename it now"). "Case" already means a FAULT case
 * (`fault_event.case_id`), a TDD case (`skill_contract_tdd_case.case_key`) and
 * a test case (`test_case_registry`) in this repo, so a reader could not tell
 * which one this was.
 *
 * This page shows every pairing with its ticket and its chat, so the relation
 * is visible in one place. It is a VIEW of the SSOT: every row comes from
 * `/api/chat_registry/list`, which reads `chat_registry` (agent.db). No new
 * table, and the UI never writes the tables directly — a row is created through
 * `POST /api/chat_registry/create`, so the one-row-per-ticket rule stays in ONE
 * place (`chat_registry_store`).
 *
 * Follows ui_skill: card -> detail modal, status badge, empty state, minimal
 * click cost, and the existing Tailwind vocabulary (rounded-2xl / border-line /
 * bg-panel / shadow-panel / text-ink / text-muted / text-accent / mono).
 */
import { createApp, reactive, computed } from 'vue';

const LIST_URL = '/api/chat_registry/list';
const CREATE_URL = '/api/chat_registry/create';

const STATUS_CLASSES = {
  open: 'bg-emerald-100 text-emerald-700',
  in_progress: 'bg-sky-100 text-sky-700',
  blocked: 'bg-amber-100 text-amber-700',
  closed: 'bg-slate-100 text-slate-600',
  cancelled: 'bg-rose-100 text-rose-700',
};

function statusClass(st) {
  return STATUS_CLASSES[String(st || '').toLowerCase()]
    || 'bg-slate-100 text-slate-600';
}

const ChatCenterWorkflow = {
  template: `
  <div class="mx-auto max-w-6xl">
    <!-- header -->
    <div class="mb-4 flex flex-wrap items-end justify-between gap-2">
      <div>
        <h2 class="text-lg font-semibold text-ink">Workflow — ticket + chat</h2>
        <p class="mt-0.5 text-sm text-muted">
          A row in <span class="mono">chat_registry</span> pairs a
          <b>ticket</b> with the <b>chat</b> it was discussed in ·
          <span class="mono">{{ rows.length }}</span> of
          <span class="mono">{{ total }}</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <input v-model="filter" placeholder="filter ticket / chat / key…"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />
        <button type="button" @click="load"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
        >{{ busy ? 'Loading…' : 'Refresh' }}</button>
      </div>
    </div>

    <div v-if="msg" class="mb-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">{{ msg }}</div>

    <!-- pairing cards -->
    <div class="grid gap-3 sm:grid-cols-2">
      <button v-for="r in shown" :key="r.chat_registry_id" type="button"
        :data-chat-register="r.chat_registry_id" @click="open(r)"
        class="w-full rounded-2xl border border-line bg-panel p-4 text-left shadow-panel transition hover:ring-2 hover:ring-accent cursor-pointer"
      >
        <div class="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
          <span class="rounded-full px-2 py-0.5 font-medium"
            :class="statusClass(r.status)">{{ r.status }}</span>
          <span class="mono text-muted">#{{ r.chat_registry_id }}</span>
          <span class="mono text-muted">{{ r.chat_key }}</span>
        </div>
        <div class="text-sm font-semibold text-ink">
          {{ (r.ticket && r.ticket.title) || r.chat_key }}</div>
        <div class="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
          <span class="mono text-accent">
            ticket #{{ r.ticket_id }}</span>
          <span v-if="r.ticket" class="text-muted">{{ r.ticket.service }}</span>
        </div>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <span v-if="r.chat_id" class="rounded-full bg-sky-100 px-2 py-0.5 text-sky-700">
            chat #{{ r.chat_id }}</span>
          <span v-else class="rounded-full bg-amber-100 px-2 py-0.5 text-amber-700">
            no chat yet</span>
          <span v-if="r.chat && r.chat.llm" class="mono text-muted">{{ r.chat.llm }}</span>
        </div>
        <div class="mt-3 text-right text-[11px] font-medium text-accent">
          click 睇詳情 →
        </div>
      </button>
    </div>

    <div v-if="!busy && !shown.length"
      class="rounded-2xl border border-line bg-panel p-6 text-center text-sm text-muted">
      <span v-if="filter">Nothing matches “{{ filter }}”.</span>
      <span v-else>無相關記錄。 No pairing yet — a row is created for a ticket
        through <span class="mono">POST /api/chat_registry/create</span>.</span>
    </div>

    <!-- detail modal (ui_skill: ✕ / backdrop / Escape) -->
    <div v-if="selected" id="chat-register-modal"
      :key="selected.chat_registry_id"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click="onBackdrop">
      <div class="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <div class="mb-3 flex items-start justify-between gap-3">
          <div>
            <div class="flex flex-wrap items-center gap-2 text-[11px]">
              <span class="rounded-full px-2 py-0.5 font-medium"
                :class="statusClass(selected.status)">{{ selected.status }}</span>
              <span class="mono text-muted">#{{ selected.chat_registry_id }}</span>
              <span class="text-muted">{{ selected.created_at }}</span>
            </div>
            <h3 class="mt-1 text-base font-semibold text-ink">
              {{ (selected.ticket && selected.ticket.title) || selected.chat_key }}</h3>
          </div>
          <button type="button" @click="close"
            class="shrink-0 rounded-lg border border-line px-2 py-1 text-sm hover:bg-soft">✕</button>
        </div>

        <div class="grid gap-2 text-xs sm:grid-cols-2">
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">chat_key</div>
            <div class="mono text-ink">{{ selected.chat_key }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">ticket_id</div>
            <div class="mono text-ink">#{{ selected.ticket_id }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">service</div>
            <div class="text-ink">{{ selected.service || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">opened_by</div>
            <div class="text-ink">{{ selected.opened_by || '—' }}</div></div>
        </div>

        <!-- the ticket -->
        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">ticket</div>
          <div v-if="selected.ticket" class="mt-2 grid gap-2 text-xs sm:grid-cols-2">
            <div><span class="text-muted">id：</span>
              <span class="mono text-ink">#{{ selected.ticket.id }}</span></div>
            <div><span class="text-muted">status：</span>
              <span class="rounded-full px-2 py-0.5"
                :class="statusClass(selected.ticket.status)">{{ selected.ticket.status }}</span></div>
            <div><span class="text-muted">service：</span>
              <span class="text-ink">{{ selected.ticket.service_name || selected.ticket.service }}</span></div>
            <div><span class="text-muted">title：</span>
              <span class="text-ink">{{ selected.ticket.title || '—' }}</span></div>
          </div>
          <div v-else class="mt-2 text-xs text-rose-600">
            The ticket this row names no longer exists.
          </div>
        </div>

        <!-- the chat -->
        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">chat room</div>
          <div v-if="selected.chat" class="mt-2 grid gap-2 text-xs sm:grid-cols-2">
            <div><span class="text-muted">chat_id：</span>
              <span class="mono text-ink">{{ selected.chat.id }}</span></div>
            <div><span class="text-muted">session_id：</span>
              <span class="mono break-all text-ink">{{ selected.chat.session_id }}</span></div>
            <div><span class="text-muted">sha256：</span>
              <span class="mono break-all text-ink">{{ selected.chat.sha256 }}</span></div>
            <div><span class="text-muted">llm：</span>
              <span class="text-ink">{{ selected.chat.llm || '—' }}</span></div>
          </div>
          <div v-else class="mt-2 text-xs text-muted">
            No chat linked yet. The ticket has not reached a chat room.
          </div>
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
      selected: null,
      busy: false,
      msg: '',
      msgOk: true,
    });

    const shown = computed(() => {
      const f = s.filter.trim().toLowerCase();
      if (!f) return s.rows;
      return s.rows.filter((r) => {
        const t = r.ticket || {};
        const c = r.chat || {};
        return [r.chat_key, r.status, r.service, r.ticket_id,
                r.chat_id, t.title, t.service, c.session_id]
          .map((v) => String(v == null ? '' : v).toLowerCase())
          .some((v) => v.includes(f));
      });
    });

    async function load() {
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch(LIST_URL + '?limit=500');
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.rows = data.rows || [];
        s.total = data.total || s.rows.length;
        s.msgOk = true;
      } catch (e) {
        s.msgOk = false;
        s.msg = 'chat_registry API unavailable: ' + (e.message || e);
      } finally {
        s.busy = false;
      }
    }

    async function open(row) {
      // Re-read ONE row so the modal shows the CURRENT ticket/chat, not the
      // snapshot the list was built from.
      try {
        const res = await fetch('/api/chat_registry/' +
          encodeURIComponent(row.chat_registry_id));
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.selected = data;
      } catch (e) {
        s.selected = row;
        s.msgOk = false;
        s.msg = 'Could not refresh #' + row.chat_registry_id + ': ' + (e.message || e);
      }
      escHandler = (e) => {
        if (e.key === 'Escape') close();
      };
      window.addEventListener('keydown', escHandler);
    }

    function close() {
      // DO NOT remove the modal node here. It is rendered by Vue's v-if, and
      // removing a Vue-managed node behind Vue's back makes Vue believe it still
      // exists, so a later open never re-creates it (measured in
      // chat-center-list.js: the second open silently failed). v-if already
      // guarantees a single node, and :key forces a clean rebuild.
      s.selected = null;
      if (escHandler) {
        window.removeEventListener('keydown', escHandler);
        escHandler = null;
      }
    }

    function onBackdrop(e) {
      if (e.target && e.target.id === 'chat-register-modal') close();
    }

    let escHandler = null;

    load();

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity, so `v-model` stops updating.
    // Object.assign onto the same proxy keeps the two-way binding intact.
    return Object.assign(s, {
      shown, statusClass, load, open, close, onBackdrop,
    });
  },
};

export function mountChatCenterWorkflow(root) {
  const app = createApp(ChatCenterWorkflow);
  app.mount(root);
  return app;
}