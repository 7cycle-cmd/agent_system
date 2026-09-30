/**
 * Ticket Center — Vue 3 component
 *
 * CORE IDEA: a TICKET traces how an ENTITY progresses.
 *   "eumu" = ENTITY. `R-1-75-1` = {LETTER}-{table_id}-{row_id}-{version}
 *   = letter R, table 1 (code_registry), row 75, version 1. The 3-part form
 *   {LETTER}-{ref_id}-{version} is the OLD one (2026-09-27). The id is
 *   VERIFIED by the API against the registers, so
 *   the UI never decides whether an id is valid — it shows the refusal reason.
 *
 * FOUR PAGES, one per table:
 *   Tickets       ticket          the current state of each entity's work
 *   Ticket Center ticket_center   the SERVICE registry
 *   Entity        (a JOIN)        every ticket for one entity
 *   Progress      ticket_event    the APPEND-ONLY history of one ticket
 *
 * Design follows ui_skill: rounded-2xl / border-line / bg-panel / shadow-panel,
 * text-ink (primary), text-muted (secondary), text-accent (emphasis), mono (ids).
 *
 * THE UI NEVER WRITES `ticket` DIRECTLY. A transition goes through
 * POST /api/ticket_center/transition, so the status machine and the event append
 * stay in ONE place (ticket_store). A second write path here would let the UI
 * bypass the transition rules.
 */
import { createApp, reactive, computed } from 'vue';

const LS_KEY = 'ticket_center_state_v1';

const LIST_URL = '/api/ticket_center/list';
const SERVICES_URL = '/api/ticket_center/services';
const MODULES_URL = '/api/ticket_center/modules';
const DETAIL_URL = '/api/ticket_center/';
const ENTITY_URL = '/api/ticket_center/module/';
const TRANSITION_URL = '/api/ticket_center/transition';
const CREATE_URL = '/api/ticket_center/create';
const MAP_URL = '/api/ticket_center/map';

const TABS = [
  { id: 'tickets', label: 'Tickets' },
  { id: 'detail', label: 'Detail' },
  { id: 'services', label: 'Ticket Center' },
  { id: 'module', label: 'Module' },
  { id: 'progress', label: 'Progress' },
];

// The status vocabulary, mirrored from ticket_store.STATUSES for DISPLAY only.
// The API is the authority: an illegal transition is refused there and the
// reason is shown, so this list cannot drift into a second set of rules.
const STATUSES = ['open', 'in_progress', 'blocked', 'closed', 'cancelled'];

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

function saveState(s) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(s));
  } catch (_) {
    /* quota / disabled storage — UI still works in-memory */
  }
}

const TicketCenter = {
  template: `
  <div class="mx-auto max-w-6xl">
    <!-- Header -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-semibold text-ink">Ticket Center</h2>
        <p class="mt-0.5 text-sm text-muted">
          A <b>ticket</b> traces how an <b>entity</b> progresses ·
          <span class="mono">T-10-1</span> = table 10, version 1 ·
          the id is <b>verified</b>, not merely shaped
        </p>
      </div>
      <button @click="refresh"
        class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
      >Refresh</button>
      <!-- GET TICKET. A ticket is FOR A SERVICE (user, 2026-09-21: "ticket is
           for which services provide to / no related too other, don't mix
           up"), so the pop-up asks for a service and, optionally, a MODULE
           (where in the system it happens). It does NOT ask for an entity id:
           an entity names a thing, and mixing the two is what the user
           rejected. -->
      <button @click="openGetTicket"
        class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
      >+ Get ticket</button>
    </div>

    <!-- Tab bar -->
    <div class="mb-4 flex flex-wrap gap-2">
      <button v-for="t in tabs" :key="t.id" type="button"
        @click="tab = t.id; persist()"
        class="rounded-lg px-3 py-1.5 text-sm transition"
        :class="tab === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft'"
      >{{ t.label }}</button>
    </div>

    <!-- Message bar -->
    <div v-if="msg" class="mb-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">
      {{ msg }}
    </div>

    <!-- ============ PAGE 1: TICKETS ============ -->
    <section v-if="tab === 'tickets'"
      class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="flex flex-wrap items-center gap-2 border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Tickets ({{ shown.length }})</h3>
        <input v-model="filter" placeholder="filter title / service / status"
          class="ml-auto w-64 rounded-xl border border-line bg-canvas px-3 py-1.5 text-sm" />
        <select v-model="fService"
          class="rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">
          <option value="">all services</option>
          <option v-for="s in services" :key="s.service" :value="s.service">
            {{ s.service }}</option>
        </select>
        <select v-model="fStatus"
          class="rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">
          <option value="">all statuses</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
        <label class="flex items-center gap-1 py-1 text-xs text-muted">
          <input type="checkbox" v-model="activeOnly" /> active only</label>
      </div>
      <div v-if="!shown.length" class="px-5 py-8 text-center text-sm text-muted">
        No tickets. A ticket is <b>for a service</b> — open one with
        <b>Get ticket</b>, or it is raised by <span class="mono">ticket_store</span>.
      </div>
      <div v-else class="overflow-auto">
        <table class="min-w-full text-left">
          <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th class="px-4 py-2 font-semibold">id</th>
              <th class="px-4 py-2 font-semibold">service</th>
              <th class="px-4 py-2 font-semibold">title</th>
              <th class="px-4 py-2 font-semibold">status</th>
              <th class="px-4 py-2 font-semibold">active</th>
              <th class="px-4 py-2 font-semibold">created</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in shown" :key="r.id" @click="open(r.id)"
              class="cursor-pointer border-t border-line hover:bg-accent-soft/40">
              <td class="px-4 py-2 mono text-xs text-muted">#{{ r.id }}</td>
              <td class="px-4 py-2 mono text-xs font-semibold text-accent">
                {{ r.service }}</td>
              <td class="px-4 py-2 text-sm text-ink">{{ r.title || '—' }}</td>
              <td class="px-4 py-2">
                <span class="rounded-full px-2 py-0.5 text-xs"
                  :class="statusClass(r.status)">{{ r.status }}</span></td>
              <td class="px-4 py-2 text-xs"
                :class="r.is_active ? 'text-emerald-700' : 'text-muted'">
                {{ r.is_active ? 'yes' : 'no' }}</td>
              <td class="px-4 py-2 text-xs text-muted">{{ r.created_at }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============ PAGE 2: DETAIL (what a ticket IS, + open one) ============ -->
    <section v-if="tab === 'detail'"
      class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Ticket detail</h3>
        <p class="mt-0.5 text-xs text-muted">
          A ticket is <b>for a service</b>, and a <b>module</b> says where in the
          system it happens. Load one by id, or open a new one with
          <b>Get ticket</b>.
        </p>
        <div class="mt-3 flex flex-wrap gap-2">
          <input v-model="detailId" placeholder="ticket id" @keyup.enter="loadDetail"
            class="w-40 rounded-xl border border-line bg-canvas px-3 py-1.5 mono text-sm" />
          <button @click="loadDetail"
            class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
          >Load</button>
          <button @click="openGetTicket"
            class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
          >+ Get ticket</button>
        </div>
      </div>
      <div v-if="detail" class="px-5 py-4">
        <div class="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span class="rounded-full px-2 py-0.5"
            :class="statusClass(detail.ticket.status)">{{ detail.ticket.status }}</span>
          <span class="mono text-accent">{{ detail.ticket.service }}</span>
          <span class="mono text-muted">#{{ detail.ticket.id }}</span>
          <span v-for="m in (detail.modules || [])" :key="m.module_id"
            class="rounded-full bg-sky-100 px-2 py-0.5 text-sky-700">
            {{ m.module_key }}</span>
        </div>
        <div class="grid gap-2 text-xs sm:grid-cols-2">
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">title</div>
            <div class="text-ink">{{ detail.ticket.title || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">opened_by</div>
            <div class="text-ink">{{ detail.ticket.opened_by || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">module (where it happens)</div>
            <div class="mono text-ink">
              {{ (detail.modules || []).map(m => m.module_key).join(', ') || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">created</div>
            <div class="text-ink">{{ detail.ticket.created_at }}</div></div>
        </div>
        <!-- MAP A MODULE. Separate from the ticket, because they are separate
             facts: the ticket says WHICH SERVICE, the mapping says WHERE. -->
        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">map a module</div>
          <div class="mt-2 flex flex-wrap gap-2">
            <select v-model="mapModule"
              class="rounded-xl border border-line bg-panel px-2 py-1.5 text-sm">
              <option value="">— pick a module —</option>
              <option v-for="m in modules" :key="m.module_id" :value="m.module_key">
                {{ m.module_key }} · {{ m.name }}</option>
            </select>
            <button @click="doMap('map')" :disabled="!mapModule"
              class="rounded-xl bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90"
            >Map</button>
            <button @click="doMap('unmap')" :disabled="!mapModule"
              class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
            >Unmap</button>
          </div>
        </div>
        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">
            history ({{ detail.event_count }})</div>
          <ol class="mt-2 border-l border-line pl-4">
            <li v-for="(e, i) in detail.events" :key="i" class="mb-2 text-xs">
              <span class="mono text-muted">{{ e.from_status }}</span>
              <span class="mx-1 text-muted">→</span>
              <span class="mono font-semibold text-ink">{{ e.to_status }}</span>
              <span class="ml-2 text-muted">by {{ e.actor }}</span>
              <div v-if="e.note && e.note !== 'NA'" class="text-ink">{{ e.note }}</div>
            </li>
          </ol>
        </div>
        <div v-if="detail.chats.length"
          class="mt-3 rounded-xl border border-line bg-soft/40 px-3 py-2">
          <div class="text-xs font-semibold text-muted">linked chats</div>
          <div class="mono mt-1 text-xs text-ink">
            <span v-for="c in detail.chats" :key="c.chat_id"
              class="mr-2 rounded-full bg-canvas px-2 py-0.5">#{{ c.chat_id }}</span>
          </div>
        </div>
      </div>
      <div v-else class="px-5 py-8 text-center text-sm text-muted">
        No ticket loaded. Enter an id above, or open one with
        <b>Get ticket</b>.
      </div>
    </section>

    <!-- ============ PAGE 3: TICKET CENTER (service registry) ============ -->
    <section v-if="tab === 'services'"
      class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Services ({{ services.length }})</h3>
        <p class="mt-0.5 text-xs text-muted">
          A <b>registry</b>, not free text: an unknown service is REFUSED, so a
          typo cannot create a service nobody owns.
        </p>
      </div>
      <div class="overflow-auto">
        <table class="min-w-full text-left">
          <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th class="px-4 py-2 font-semibold">id</th>
              <th class="px-4 py-2 font-semibold">service</th>
              <th class="px-4 py-2 font-semibold">name</th>
              <th class="px-4 py-2 font-semibold">description</th>
              <th class="px-4 py-2 font-semibold">tickets</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in services" :key="s.id" class="border-t border-line">
              <td class="px-4 py-2 mono text-xs text-muted">#{{ s.id }}</td>
              <td class="px-4 py-2 mono text-xs font-semibold text-accent">
                {{ s.service }}</td>
              <td class="px-4 py-2 text-sm text-ink">{{ s.name }}</td>
              <td class="px-4 py-2 text-xs text-muted">{{ s.description || '—' }}</td>
              <td class="px-4 py-2 text-sm text-ink">{{ countFor(s.service) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============ PAGE 4: ENTITY ============ -->
    <section v-if="tab === 'module'"
      class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Module lookup</h3>
        <p class="mt-0.5 text-xs text-muted">
          Enter a module key (e.g. <span class="mono">task_center</span>). A
          module says <b>where</b> a ticket happens; a bad key is REFUSED, not
          shown as "no tickets".
        </p>
        <div class="mt-3 flex flex-wrap gap-2">
          <input v-model="entityQuery" placeholder="task_center"
            @keyup.enter="lookupEntity"
            class="w-56 rounded-xl border border-line bg-canvas px-3 py-1.5 mono text-sm" />
          <button @click="lookupEntity"
            class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
          >Look up</button>
        </div>
      </div>
      <div v-if="entityResult" class="px-5 py-4">
        <div class="mb-3 rounded-xl border border-line bg-soft/40 px-3 py-2 text-xs">
          <span class="mono text-accent">{{ entityResult.module }}</span>
          <span class="ml-2 text-muted">· {{ entityResult.count }} ticket(s)</span>
        </div>
        <div v-if="!entityResult.tickets.length"
          class="rounded-xl border border-line bg-canvas px-3 py-4 text-center text-sm text-muted">
          This module is VALID but has no ticket mapped to it yet.
        </div>
        <table v-else class="min-w-full text-left">
          <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th class="px-4 py-2 font-semibold">id</th>
              <th class="px-4 py-2 font-semibold">service</th>
              <th class="px-4 py-2 font-semibold">title</th>
              <th class="px-4 py-2 font-semibold">status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in entityResult.tickets" :key="r.id" @click="open(r.id)"
              class="cursor-pointer border-t border-line hover:bg-accent-soft/40">
              <td class="px-4 py-2 mono text-xs text-muted">#{{ r.id }}</td>
              <td class="px-4 py-2 text-sm text-ink">{{ r.service }}</td>
              <td class="px-4 py-2 text-sm text-ink">{{ r.title || '—' }}</td>
              <td class="px-4 py-2">
                <span class="rounded-full px-2 py-0.5 text-xs"
                  :class="statusClass(r.status)">{{ r.status }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============ PAGE 5: PROGRESS ============ -->
    <section v-if="tab === 'progress'"
      class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Progress</h3>
        <p class="mt-0.5 text-xs text-muted">
          The <b>append-only</b> history of one ticket — how it moved, in order.
          A single boolean could not answer this.
        </p>
        <div class="mt-3 flex flex-wrap gap-2">
          <input v-model="progressId" placeholder="ticket id" @keyup.enter="loadProgress"
            class="w-40 rounded-xl border border-line bg-canvas px-3 py-1.5 mono text-sm" />
          <button @click="loadProgress"
            class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
          >Load</button>
        </div>
      </div>
      <div v-if="progress" class="px-5 py-4">
        <div class="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span class="rounded-full px-2 py-0.5"
            :class="statusClass(progress.ticket.status)">{{ progress.ticket.status }}</span>
          <span class="mono text-accent">{{ progress.ticket.service }}</span>
          <span class="mono text-muted">#{{ progress.ticket.id }}</span>
          <span class="text-muted">· {{ progress.event_count }} event(s)</span>
        </div>
        <ol class="relative border-l border-line pl-5">
          <li v-for="(e, i) in progress.events" :key="i" class="mb-4">
            <span class="absolute -left-[5px] mt-1.5 h-2 w-2 rounded-full bg-accent"></span>
            <div class="text-xs">
              <span class="mono text-muted">{{ e.from_status }}</span>
              <span class="mx-1 text-muted">→</span>
              <span class="mono font-semibold text-ink">{{ e.to_status }}</span>
              <span class="ml-2 text-muted">by {{ e.actor }}</span>
              <span class="ml-2 text-muted">{{ e.created_at }}</span>
            </div>
            <div v-if="e.note && e.note !== 'NA'" class="mt-0.5 text-xs text-ink">
              {{ e.note }}</div>
            <div v-if="e.cite_ref && e.cite_ref !== 'NA'"
              class="mono mt-0.5 text-[11px] text-muted">{{ e.cite_ref }}</div>
          </li>
        </ol>
        <div v-if="progress.chats.length" class="mt-3 rounded-xl border border-line bg-soft/40 px-3 py-2">
          <div class="text-xs font-semibold text-muted">linked chats</div>
          <div class="mono mt-1 text-xs text-ink">
            <span v-for="c in progress.chats" :key="c.chat_id"
              class="mr-2 rounded-full bg-canvas px-2 py-0.5">#{{ c.chat_id }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ GET TICKET POP-UP (ui_skill: ✕ / backdrop / Escape) ============ -->
    <div v-if="getOpen" id="get-ticket-modal"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click="onGetBackdrop">
      <div class="max-h-[85vh] w-full max-w-lg overflow-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <div class="mb-3 flex items-start justify-between gap-3">
          <div>
            <h3 class="text-base font-semibold text-ink">Get ticket</h3>
            <p class="mt-0.5 text-xs text-muted">
              A ticket is <b>for a service</b>. A <b>module</b> is optional and
              says where in the system it happens. The two are separate — an
              entity id names a <i>thing</i> and is not asked for here.
            </p>
          </div>
          <button type="button" @click="closeGetTicket"
            class="shrink-0 rounded-lg border border-line px-2 py-1 text-sm hover:bg-soft">✕</button>
        </div>

        <label class="block text-xs font-medium text-muted">service
          <select v-model="getService"
            class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm">
            <option value="">— pick a service —</option>
            <option v-for="s in services" :key="s.service" :value="s.service">
              {{ s.service }} · {{ s.name }}</option>
          </select>
        </label>

        <label class="mt-3 block text-xs font-medium text-muted">module (optional — where it happens)
          <select v-model="getModule"
            class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm">
            <option value="">— no module —</option>
            <option v-for="m in modules" :key="m.module_id" :value="m.module_key">
              {{ m.module_key }} · {{ m.name }}</option>
          </select>
        </label>

        <label class="mt-3 block text-xs font-medium text-muted">title (optional)
          <input v-model="getTitle" placeholder="what this ticket is for"
            class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm" />
        </label>

        <div v-if="getMsg" class="mt-3 rounded-xl border px-3 py-2 text-xs"
          :class="getMsgOk
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-rose-200 bg-rose-50 text-rose-700'">{{ getMsg }}</div>

        <div class="mt-4 flex justify-end gap-2">
          <button type="button" @click="closeGetTicket"
            class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Cancel</button>
          <button type="button" @click="doGetTicket"
            :disabled="!canGetTicket"
            class="rounded-xl px-4 py-1.5 text-sm font-semibold text-white transition"
            :class="canGetTicket ? 'bg-accent hover:opacity-90' : 'cursor-not-allowed bg-slate-300'"
          >{{ getBusy ? 'Opening…' : 'Open ticket' }}</button>
        </div>
      </div>
    </div>

    <!-- ============ DETAIL POP-UP (ui_skill: ✕ / backdrop / Escape) ============ -->
    <div v-if="selected" id="ticket-modal" :key="selected.ticket.id"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      @click="onBackdrop">
      <div class="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <div class="mb-3 flex items-start justify-between gap-3">
          <div>
            <div class="flex flex-wrap items-center gap-2 text-[11px]">
              <span class="rounded-full px-2 py-0.5 font-medium"
                :class="statusClass(selected.ticket.status)">{{ selected.ticket.status }}</span>
              <span class="mono text-muted">#{{ selected.ticket.id }}</span>
              <span class="text-muted">{{ selected.ticket.created_at }}</span>
            </div>
            <h3 class="mt-1 text-base font-semibold text-ink">
              {{ selected.ticket.title || '(no title)' }}</h3>
          </div>
          <button type="button" @click="close"
            class="shrink-0 rounded-lg border border-line px-2 py-1 text-sm hover:bg-soft">✕</button>
        </div>

        <div class="grid gap-2 text-xs sm:grid-cols-2">
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">service</div>
            <div class="text-ink">{{ selected.ticket.service }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">module (where it happens)</div>
            <div class="mono text-ink">
              {{ (selected.modules || []).map(m => m.module_key).join(', ') || '—' }}</div></div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <div class="text-muted">opened_by</div>
            <div class="text-ink">{{ selected.ticket.opened_by || '—' }}</div></div>
        </div>

        <!-- TRANSITION. The rules live in ticket_store; an illegal move is a 400
             and the reason is shown, because a status that did not change and a
             status that was REFUSED look identical otherwise. -->
        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">transition</div>
          <div class="mt-2 flex flex-wrap gap-2">
            <select v-model="toStatus"
              class="rounded-xl border border-line bg-panel px-2 py-1.5 text-sm">
              <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
            </select>
            <input v-model="actor" placeholder="actor (required)"
              class="w-40 rounded-xl border border-line bg-panel px-3 py-1.5 text-sm" />
            <input v-model="note" placeholder="note"
              class="w-56 rounded-xl border border-line bg-panel px-3 py-1.5 text-sm" />
            <button @click="doTransition"
              class="rounded-xl bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90"
            >Apply</button>
          </div>
        </div>

        <div class="mt-3 rounded-xl border border-line bg-canvas p-3">
          <div class="text-xs font-semibold text-muted">
            history ({{ selected.event_count }})</div>
          <ol class="mt-2 border-l border-line pl-4">
            <li v-for="(e, i) in selected.events" :key="i" class="mb-2 text-xs">
              <span class="mono text-muted">{{ e.from_status }}</span>
              <span class="mx-1 text-muted">→</span>
              <span class="mono font-semibold text-ink">{{ e.to_status }}</span>
              <span class="ml-2 text-muted">by {{ e.actor }}</span>
              <div v-if="e.note && e.note !== 'NA'" class="text-ink">{{ e.note }}</div>
            </li>
          </ol>
        </div>
      </div>
    </div>
  </div>
  `,

  setup() {
    const saved = loadState() || {};
    // THE URL IS THE AUTHORITY for which tab is shown. Measured 2026-09-21:
    // navigating to /llm-tasks/ticket_center/detail rendered the TICKETS page,
    // because the component read its tab from localStorage only. The URL said
    // `detail` and the page showed `tickets` -- a deep link that silently lands
    // somewhere else. `app.js` parses the segment into `state.ticketCenter.tab`
    // and passes it here, so the URL wins over the stored value.
    const urlTab = (window.__ticketCenterTab || '').trim();
    const s = reactive({
      tab: urlTab || saved.tab || 'tickets',
      rows: [],
      services: [],
      modules: [],
      filter: '',
      fService: '',
      fStatus: '',
      activeOnly: false,
      entityQuery: '',
      entityResult: null,
      mapModule: '',
      progressId: '',
      progress: null,
      detailId: '',
      detail: null,
      // Get ticket pop-up. The SERVICE is required (a ticket is for a service);
      // the MODULE is optional and says where in the system it happens. No
      // entity id: an entity names a thing, and mixing the two is what the
      // user rejected (2026-09-21).
      getOpen: false,
      getService: '',
      getModule: '',
      getTitle: '',
      getBusy: false,
      getMsg: '',
      getMsgOk: true,
      selected: null,
      toStatus: 'in_progress',
      actor: '',
      note: '',
      msg: '',
      msgOk: true,
    });

    const shown = computed(() => {
      const f = s.filter.trim().toLowerCase();
      return s.rows.filter((r) => {
        if (s.fService && r.service !== s.fService) return false;
        if (s.fStatus && r.status !== s.fStatus) return false;
        if (s.activeOnly && !r.is_active) return false;
        if (!f) return true;
        return ['title', 'service', 'status', 'id']
          .map((k) => String(r[k] == null ? '' : r[k]).toLowerCase())
          .some((v) => v.includes(f));
      });
    });

    function persist() {
      saveState({ tab: s.tab });
    }

    function statusClass(st) {
      if (st === 'closed') return 'bg-slate-100 text-slate-600';
      if (st === 'cancelled') return 'bg-rose-100 text-rose-700';
      if (st === 'blocked') return 'bg-amber-100 text-amber-700';
      if (st === 'in_progress') return 'bg-sky-100 text-sky-700';
      return 'bg-emerald-100 text-emerald-700';
    }

    function countFor(service) {
      return s.rows.filter((r) => r.service === service).length;
    }

    async function load() {
      s.msg = '';
      try {
        const [lr, sr, mr] = await Promise.all([
          fetch(LIST_URL + '?limit=500'),
          fetch(SERVICES_URL),
          fetch(MODULES_URL),
        ]);
        const ld = await lr.json();
        const sd = await sr.json();
        const md = await mr.json();
        if (!lr.ok || !ld.ok) throw new Error(ld.error || ('HTTP ' + lr.status));
        s.rows = ld.rows || [];
        s.services = (sd && sd.ok && sd.rows) || [];
        s.modules = (md && md.ok && md.rows) || [];
        s.msgOk = true;
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Ticket API unavailable: ' + (e.message || e);
      }
    }

    async function open(id) {
      try {
        const res = await fetch(DETAIL_URL + id);
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.selected = data;
        s.toStatus = 'in_progress';
        s.actor = '';
        s.note = '';
        escHandler = (e) => {
          if (e.key === 'Escape') close();
        };
        window.addEventListener('keydown', escHandler);
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Could not load ticket #' + id + ': ' + (e.message || e);
      }
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
      if (e.target && e.target.id === 'ticket-modal') close();
    }

    async function doTransition() {
      if (!s.selected) return;
      try {
        const res = await fetch(TRANSITION_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ticket_id: s.selected.ticket.id,
            to_status: s.toStatus,
            actor: s.actor,
            note: s.note,
            cite_ref: 'ticket_center_ui',
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.msgOk = true;
        s.msg = 'Ticket #' + data.ticket_id + ': ' + data.from + ' → ' + data.to;
        await open(s.selected.ticket.id);
        await load();
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Transition refused: ' + (e.message || e);
      }
    }

    async function lookupEntity() {
      const q = s.entityQuery.trim();
      if (!q) return;
      try {
        const res = await fetch(ENTITY_URL + encodeURIComponent(q));
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.entityResult = data;
        s.msgOk = true;
        s.msg = '';
      } catch (e) {
        s.entityResult = null;
        s.msgOk = false;
        s.msg = 'Module refused: ' + (e.message || e);
      }
    }

    async function loadProgress() {
      const id = String(s.progressId).trim();
      if (!id) return;
      try {
        const res = await fetch(DETAIL_URL + encodeURIComponent(id));
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.progress = data;
        s.msgOk = true;
        s.msg = '';
      } catch (e) {
        s.progress = null;
        s.msgOk = false;
        s.msg = 'Could not load progress: ' + (e.message || e);
      }
    }

    async function loadDetail() {
      const id = String(s.detailId).trim();
      if (!id) return;
      try {
        const res = await fetch(DETAIL_URL + encodeURIComponent(id));
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.detail = data;
        s.msgOk = true;
        s.msg = '';
      } catch (e) {
        s.detail = null;
        s.msgOk = false;
        s.msg = 'Could not load detail: ' + (e.message || e);
      }
    }

    // ---- Get ticket pop-up -------------------------------------------------
    // The SERVICE is required (a ticket is FOR a service). The module is
    // OPTIONAL. There is no entity id to validate: a ticket no longer carries
    // one, so the pop-up cannot mix the two up. The API is still the authority
    // on the service and the module, so a refusal is SHOWN rather than
    // prevented here.
    const canGetTicket = computed(
      () => s.getService.trim().length > 0 && !s.getBusy
    );

    function openGetTicket() {
      s.getOpen = true;
      s.getMsg = '';
      s.getMsgOk = true;
      getEsc = (e) => {
        if (e.key === 'Escape') closeGetTicket();
      };
      window.addEventListener('keydown', getEsc);
    }

    function closeGetTicket() {
      // Do NOT remove the modal node: it is rendered by Vue's v-if, and
      // removing a Vue-managed node behind Vue's back makes Vue believe it
      // still exists, so a later open never re-creates it.
      s.getOpen = false;
      if (getEsc) {
        window.removeEventListener('keydown', getEsc);
        getEsc = null;
      }
    }

    function onGetBackdrop(e) {
      if (e.target && e.target.id === 'get-ticket-modal') closeGetTicket();
    }

    async function doGetTicket() {
      if (!canGetTicket.value) return;
      s.getBusy = true;
      s.getMsg = '';
      try {
        const res = await fetch(CREATE_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            service: s.getService,
            module: s.getModule,
            title: s.getTitle.trim(),
            opened_by: 'ticket_center_ui',
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.getMsgOk = true;
        s.getMsg = data.created
          ? 'Ticket #' + data.ticket_id + ' opened for service ' + data.service + '.'
          : 'Ticket #' + data.ticket_id + ' already existed for service ' + data.service + '.';
        s.getService = '';
        s.getModule = '';
        s.getTitle = '';
        await load();
        s.detailId = String(data.ticket_id);
        await loadDetail();
        s.tab = 'detail';
        persist();
        // Close on SUCCESS only. The detail page now shows the ticket, so
        // leaving the pop-up open would hide the result behind the thing that
        // produced it. A REFUSAL keeps the pop-up open so the reason stays
        // visible next to the fields that caused it.
        closeGetTicket();
        s.msgOk = true;
        s.msg = 'Ticket #' + data.ticket_id + ' '
          + (data.created ? 'opened' : 'already existed')
          + ' for service ' + data.service + '.';
      } catch (e) {
        s.getMsgOk = false;
        s.getMsg = 'Ticket refused: ' + (e.message || e);
      } finally {
        s.getBusy = false;
      }
    }

    // ---- map / unmap a module ---------------------------------------------
    // The mapping is its OWN operation, because a ticket and a module are
    // separate facts: the ticket says WHICH SERVICE, the mapping says WHERE.
    async function doMap(action) {
      if (!s.detail || !s.mapModule) return;
      try {
        const res = await fetch(MAP_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ticket_id: s.detail.ticket.id,
            module: s.mapModule,
            action: action,
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.msgOk = true;
        s.msg = action === 'unmap'
          ? (data.removed ? 'Unmapped ' + data.module + '.'
                          : 'No active mapping to ' + data.module + '.')
          : (data.created ? 'Mapped ' + data.module + '.'
                          : 'Already mapped to ' + data.module + '.');
        await loadDetail();
        s.mapModule = '';
      } catch (e) {
        s.msgOk = false;
        s.msg = 'Map refused: ' + (e.message || e);
      }
    }

    let escHandler = null;
    let getEsc = null;

    load();

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity, so `v-model` stops updating and
    // a tab click does not re-render. Measured in capability-center.js, which
    // carries the same warning. Object.assign onto the reactive object keeps the
    // proxy identity, so every binding stays live.
    return Object.assign(s, {
      tabs: TABS, statuses: STATUSES,
      shown, canGetTicket,
      persist, statusClass, countFor,
      refresh: load, open, close, onBackdrop, doTransition,
      lookupEntity, loadProgress, loadDetail, doMap,
      openGetTicket, closeGetTicket, onGetBackdrop, doGetTicket,
    });
  },
};

export function mountTicketCenter(root) {
  const app = createApp(TicketCenter);
  app.mount(root);
  return app;
}