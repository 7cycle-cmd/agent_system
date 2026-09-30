/**
 * Capability Center — Vue 3 component
 *
 * CORE IDEA: one FLOW, many BACKENDS.
 *   The video-produce flow (research -> proposal -> script -> scene_plan ->
 *   assets -> edit -> compose) is the skeleton. A capability only swaps the
 *   BACKEND per stage (python-pptx for PPT, Remotion for video).
 *   `flow_ref` on each capability is the machine-readable proof of this:
 *   two capabilities sharing `flow_ref` share the flow.
 *
 * The skill's job is to KEEP ASKING (interview-driven), not to hard-code steps.
 * The Interview tab surfaces those questions per stage.
 *
 * Design follows ui_skill: rounded-2xl / border-line / bg-panel / shadow-panel,
 * text-ink (primary), text-muted (secondary), text-accent (emphasis), mono (ids).
 */
import { createApp, reactive, computed } from 'vue';

const LS_KEY = 'capability_center_state_v1';

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

const CapabilityCenter = {
  template: `
  <div class="mx-auto max-w-6xl">
    <!-- Header -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-semibold text-ink">Capability Center</h2>
        <p class="mt-0.5 text-sm text-muted">
          One <b>flow</b>, many <b>backends</b> ·
          <span class="mono">flow_ref</span> proves two capabilities share the flow ·
          skill = interview-driven
        </p>
      </div>
      <button
        @click="refresh"
        class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
      >Refresh</button>
    </div>

    <!-- Tab bar (owned here so the tab state lives in one place) -->
    <div class="mb-4 flex flex-wrap gap-2">
      <button
        v-for="t in tabs"
        :key="t.id"
        @click="tab = t.id; persist()"
        type="button"
        class="tab-btn rounded-lg px-3 py-1.5 text-sm transition"
        :class="tab === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft'"
      >{{ t.label }}</button>
    </div>

    <!-- Error / info bar -->
    <div v-if="msg" class="mb-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">
      {{ msg }}
    </div>

    <!-- ============ TAB 1: CAPABILITY LIST ============ -->
    <section v-if="tab === 'list'" class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Capabilities ({{ list.length }})</h3>
        <p class="mt-0.5 text-xs text-muted">
          Capability = goal/spec · flow_ref = which flow it runs · backend = how it renders
        </p>
      </div>
      <div v-if="!list.length" class="px-5 py-8 text-center text-sm text-muted">
        No capabilities yet. Seed <span class="mono">CAP.PPT.PRODUCE</span> to start.
      </div>
      <div v-else class="overflow-auto">
        <table class="min-w-full text-left">
          <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
            <tr>
              <th class="px-4 py-2 font-semibold">cap_id</th>
              <th class="px-4 py-2 font-semibold">name</th>
              <th class="px-4 py-2 font-semibold">flow_ref</th>
              <th class="px-4 py-2 font-semibold">backend</th>
              <th class="px-4 py-2 font-semibold">status</th>
              <th class="px-4 py-2 font-semibold">streak</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in list" :key="c.contract_id"
              @click="select(c.contract_id)"
              class="cursor-pointer border-t border-line hover:bg-accent-soft/40"
              :class="selectedId === c.contract_id ? 'bg-accent-soft/70' : ''">
              <td class="px-4 py-2 mono text-xs font-semibold text-accent">
                {{ c.contract_id }}
              </td>
              <td class="px-4 py-2 text-sm text-ink">{{ c.skill_key }}</td>
              <td class="px-4 py-2 mono text-xs">
                <span class="rounded-full bg-soft px-2 py-0.5">{{ c.flow_ref || '—' }}</span>
              </td>
              <td class="px-4 py-2 mono text-xs">{{ c.backend || '—' }}</td>
              <td class="px-4 py-2">
                <span class="rounded-full px-2 py-0.5 text-xs"
                  :class="c.status === 'active'
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-slate-100 text-slate-600'">{{ c.status }}</span>
              </td>
              <td class="px-4 py-2 mono text-xs">
                {{ c.current_streak }}/{{ c.target_streak }}
                <span v-if="c.streak_qualified" class="ml-1 text-emerald-600">✅</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- ============ TAB 2: FLOW DETAIL ============ -->
    <section v-else-if="tab === 'flow'" class="space-y-4">
      <div class="rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 class="text-sm font-semibold text-ink">
              Flow: <span class="mono text-accent">{{ detail.flow_ref || '—' }}</span>
            </h3>
            <p class="mt-0.5 text-xs text-muted">
              Stage names are FIXED. Only the backend row changes per capability —
              that is what "flow not change" means.
            </p>
          </div>
          <select v-model="selectedId" @change="loadDetail"
            class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm">
            <option v-for="c in list" :key="c.contract_id" :value="c.contract_id">
              {{ c.contract_id }}
            </option>
          </select>
        </div>

        <div v-if="!detail.stages.length" class="py-6 text-center text-sm text-muted">
          Select a capability to see its flow.
        </div>
        <div v-else class="overflow-auto">
          <table class="min-w-full text-left">
            <thead class="bg-soft/80 text-[11px] uppercase tracking-wide text-muted">
              <tr>
                <th class="px-3 py-2 font-semibold">#</th>
                <th class="px-3 py-2 font-semibold">stage</th>
                <th class="px-3 py-2 font-semibold">backend</th>
                <th class="px-3 py-2 font-semibold">skill asks</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(st, i) in detail.stages" :key="st.stage"
                class="border-t border-line">
                <td class="px-3 py-2 mono text-xs text-muted">{{ i + 1 }}</td>
                <td class="px-3 py-2 mono text-sm font-semibold text-ink">{{ st.stage }}</td>
                <td class="px-3 py-2">
                  <span class="rounded-full bg-accent-soft px-2 py-0.5 mono text-xs text-accent">
                    {{ st.backend }}
                  </span>
                </td>
                <td class="px-3 py-2 text-xs text-muted">
                  <span v-for="(q, qi) in (st.ask || [])" :key="qi"
                    class="mr-1 inline-block rounded bg-soft px-1.5 py-0.5">{{ q }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Side-by-side backend comparison: same stages, different backends -->
      <div v-if="compare.length > 1" class="rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <h3 class="text-sm font-semibold text-ink">Same flow, different backends</h3>
        <p class="mt-0.5 text-xs text-muted">
          Capabilities sharing <span class="mono">flow_ref</span> have identical stage names.
        </p>
        <div class="mt-3 grid gap-3 sm:grid-cols-2">
          <div v-for="c in compare" :key="c.contract_id"
            class="rounded-xl border border-line bg-soft/40 p-3">
            <div class="mono text-xs font-semibold text-accent">{{ c.contract_id }}</div>
            <div class="mt-1 text-xs text-muted">backend: <span class="mono">{{ c.backend }}</span></div>
            <div class="mt-2 flex flex-wrap gap-1">
              <span v-for="st in c.stages" :key="st.stage"
                class="rounded bg-panel px-1.5 py-0.5 mono text-[10px]">{{ st.stage }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ TAB 3: INTERVIEW ============ -->
    <section v-else-if="tab === 'interview'" class="rounded-2xl border border-line bg-panel shadow-panel">
      <div class="border-b border-line px-5 py-4">
        <h3 class="text-sm font-semibold text-ink">Interview</h3>
        <p class="mt-0.5 text-xs text-muted">
          The skill keeps asking. Answer per stage; answers feed the review log.
        </p>
      </div>
      <div v-if="!detail.stages.length" class="px-5 py-8 text-center text-sm text-muted">
        Select a capability first (Capability List tab).
      </div>
      <div v-else class="divide-y divide-line">
        <div v-for="(st, i) in detail.stages" :key="st.stage" class="px-5 py-4">
          <div class="mb-2 flex items-center gap-2">
            <span class="mono text-xs text-muted">{{ i + 1 }}</span>
            <span class="mono text-sm font-semibold text-ink">{{ st.stage }}</span>
            <span class="rounded-full bg-soft px-2 py-0.5 mono text-[10px] text-muted">
              {{ st.backend }}
            </span>
          </div>
          <div v-for="(q, qi) in (st.ask || [])" :key="qi" class="mb-2">
            <label class="mb-1 block text-xs text-muted">{{ q }}</label>
            <input
              v-model="answers[st.stage + '::' + qi]"
              @change="persist"
              type="text"
              :placeholder="'answer for ' + st.stage + '…'"
              class="w-full rounded-xl border border-line bg-canvas px-3 py-1.5 text-sm text-ink outline-none focus:border-accent"
            />
          </div>
        </div>
      </div>
      <div class="border-t border-line px-5 py-3">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span class="text-xs text-muted">
            {{ answeredCount }} / {{ questionCount }} answered
          </span>
          <div class="flex items-center gap-2">
            <span v-if="submitMsg" class="text-xs"
              :class="submitOk ? 'text-emerald-600' : 'text-rose-600'">
              {{ submitMsg }}
            </span>
            <button
              @click="submitInterview"
              :disabled="busy || !questionCount"
              class="rounded-xl px-4 py-2 text-sm font-semibold text-white shadow-panel transition"
              :class="(busy || !questionCount) ? 'cursor-not-allowed bg-slate-300' : 'bg-accent hover:opacity-90'"
            >{{ busy ? 'Saving…' : 'Submit interview →' }}</button>
          </div>
        </div>
        <p class="mt-2 text-[11px] text-muted">
          Writes to <span class="mono">skill_contract_review_log</span> ·
          gap = unanswered · revision = answers · all answered = pass (streak +1)
        </p>
      </div>
    </section>
    <!-- ============ TAB 4: SLIDE ROOM ============ -->
    <section v-else-if="tab === 'slide'" class="space-y-4">
      <!-- Catalog view -->
      <div v-if="!deck" class="rounded-2xl border border-line bg-panel shadow-panel">
        <div class="border-b border-line px-5 py-4">
          <h3 class="text-sm font-semibold text-ink">Slide Room</h3>
          <p class="mt-0.5 text-xs text-muted">
            Pick a deck, then Play to present it slide by slide.
          </p>
        </div>
        <div v-if="!decks.length" class="px-5 py-8 text-center text-sm text-muted">
          No decks yet.
        </div>
        <div v-else class="divide-y divide-line">
          <div v-for="d in decks" :key="d.deck_id"
            class="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
            <div>
              <div class="mono text-xs font-semibold text-accent">{{ d.deck_id }}</div>
              <div class="mt-0.5 text-sm font-medium text-ink">{{ d.title }}</div>
              <div class="mt-0.5 text-xs text-muted">{{ d.description }}</div>
            </div>
            <div class="flex items-center gap-2">
              <button @click="openDeck(d.deck_id)"
                class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">
                Open
              </button>
              <button @click="openDeck(d.deck_id).then(play)"
                class="rounded-xl bg-accent px-4 py-1.5 text-sm font-semibold text-white hover:opacity-90">
                ▶ Play
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Player view -->
      <div v-else class="rounded-2xl border border-line bg-panel shadow-panel">
        <div class="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3">
          <div>
            <div class="mono text-xs font-semibold text-accent">{{ deck.deck_id }}</div>
            <div class="text-sm font-medium text-ink">{{ deck.title }}</div>
          </div>
          <div class="flex items-center gap-2">
            <button @click="play"
              class="rounded-xl bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:opacity-90">
              ▶ Play
            </button>
            <button @click="closeDeck"
              class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">
              ← Catalog
            </button>
          </div>
        </div>

        <!-- The slide -->
        <div class="px-6 py-8">
          <div class="mx-auto max-w-3xl rounded-2xl border-2 border-line bg-canvas p-8 shadow-panel">
            <div class="mb-1 flex items-center gap-2">
              <span class="rounded-full bg-soft px-2 py-0.5 mono text-[10px] text-muted">
                {{ currentSlide.stage || '—' }}
              </span>
              <span class="rounded-full bg-soft px-2 py-0.5 mono text-[10px] text-muted">
                {{ currentSlide.kind || 'content' }}
              </span>
            </div>
            <h3 class="mt-2 text-2xl font-semibold text-ink">{{ currentSlide.title }}</h3>
            <ul class="mt-5 space-y-2">
              <li v-for="(b, bi) in (currentSlide.bullets || [])" :key="bi"
                class="text-base leading-relaxed text-ink"
                :class="b.startsWith('  ') ? 'pl-6 text-muted' : ''">
                {{ b }}
              </li>
            </ul>
          </div>
        </div>

        <!-- Nav -->
        <div class="flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3">
          <button @click="prev" :disabled="slideIdx === 0"
            class="rounded-xl border border-line bg-panel px-4 py-2 text-sm hover:bg-soft"
            :class="slideIdx === 0 ? 'cursor-not-allowed opacity-40' : ''">
            ◀ Back
          </button>
          <span class="mono text-sm text-muted">
            {{ slideIdx + 1 }} / {{ (deck.slides || []).length }}
          </span>
          <button @click="next" :disabled="slideIdx >= (deck.slides || []).length - 1"
            class="rounded-xl border border-line bg-panel px-4 py-2 text-sm hover:bg-soft"
            :class="slideIdx >= (deck.slides || []).length - 1 ? 'cursor-not-allowed opacity-40' : ''">
            Next ▶
          </button>
        </div>
      </div>
    </section>
  </div>

  <!-- ============ PLAY OVERLAY (full screen) ============ -->
  <div v-if="playing && deck"
    class="fixed inset-0 z-50 flex flex-col bg-canvas"
    @keydown.esc="exitPlay" tabindex="0" ref="overlay">
    <div class="flex items-center justify-between px-6 py-3">
      <span class="mono text-xs text-muted">{{ deck.deck_id }}</span>
      <span class="mono text-xs text-muted">{{ slideIdx + 1 }} / {{ (deck.slides || []).length }}</span>
      <button @click="exitPlay"
        class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">
        ✕ Exit (Esc)
      </button>
    </div>
    <div class="flex flex-1 items-center justify-center px-10 pb-6">
      <div class="w-full max-w-4xl">
        <div class="mb-2 flex items-center gap-2">
          <span class="rounded-full bg-soft px-2 py-0.5 mono text-[10px] text-muted">
            {{ currentSlide.stage || '—' }}
          </span>
        </div>
        <h2 class="text-4xl font-semibold text-ink">{{ currentSlide.title }}</h2>
        <ul class="mt-8 space-y-3">
          <li v-for="(b, bi) in (currentSlide.bullets || [])" :key="bi"
            class="text-xl leading-relaxed text-ink"
            :class="b.startsWith('  ') ? 'pl-8 text-muted' : ''">
            {{ b }}
          </li>
        </ul>
      </div>
    </div>
    <div class="flex items-center justify-center gap-4 px-6 py-5">
      <button @click="prev" :disabled="slideIdx === 0"
        class="rounded-xl border border-line bg-panel px-6 py-2.5 text-base hover:bg-soft"
        :class="slideIdx === 0 ? 'cursor-not-allowed opacity-40' : ''">
        ◀ Back
      </button>
      <button @click="next" :disabled="slideIdx >= (deck.slides || []).length - 1"
        class="rounded-xl bg-accent px-6 py-2.5 text-base font-semibold text-white hover:opacity-90"
        :class="slideIdx >= (deck.slides || []).length - 1 ? 'cursor-not-allowed opacity-40' : ''">
        Next ▶
      </button>
    </div>
  </div>
  `,

  setup() {
    const persisted = loadState() || {};
    const tabs = [
      { id: 'list', label: 'Capability List' },
      { id: 'flow', label: 'Flow Detail' },
      { id: 'interview', label: 'Interview' },
      { id: 'slide', label: 'Slide Room' },
    ];
    const s = reactive({
      tabs,
      tab: persisted.tab || 'list',
      decks: [],
      deck: null,
      slideIdx: 0,
      playing: false,
      list: [],
      selectedId: persisted.selectedId || '',
      detail: { contract_id: '', flow_ref: '', backend: '', stages: [] },
      compare: [],
      answers: persisted.answers || {},
      busy: false,
      submitMsg: '',
      submitOk: true,
      msg: '',
      msgOk: true,
    });

    function persist() {
      saveState({
        tab: s.tab,
        selectedId: s.selectedId,
        answers: s.answers,
      });
    }

    const questionCount = computed(() =>
      s.detail.stages.reduce((n, st) => n + (st.ask || []).length, 0)
    );
    const currentSlide = computed(() => {
      const slides = (s.deck && s.deck.slides) || [];
      return slides[s.slideIdx] || { title: '', bullets: [], stage: '', kind: '' };
    });
    const answeredCount = computed(() =>
      s.detail.stages.reduce(
        (n, st) =>
          n +
          (st.ask || []).filter((_, qi) => (s.answers[st.stage + '::' + qi] || '').trim())
            .length,
        0
      )
    );

    async function refresh() {
      s.msg = '';
      try {
        const res = await fetch('/api/capability_center/list');
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.msg = data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.list = data.rows || [];
        if (!s.selectedId && s.list.length) s.selectedId = s.list[0].contract_id;
        await loadDetail();
        s.msgOk = true;
      } catch (e) {
        s.msg = 'Capability Center API unavailable: ' + (e.message || e);
        s.msgOk = false;
      }
    }

    async function loadDetail() {
      if (!s.selectedId) return;
      try {
        const res = await fetch(
          '/api/capability_center/' + encodeURIComponent(s.selectedId)
        );
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.msg = data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.detail = data.row || s.detail;
        s.compare = data.compare || [];
        persist();
      } catch (e) {
        s.msg = 'Capability Center API unavailable: ' + (e.message || e);
        s.msgOk = false;
      }
    }

    function select(id) {
      s.selectedId = id;
      persist();
      loadDetail();
    }

    // ---- Slide Room -------------------------------------------------------
    async function loadCatalog() {
      try {
        const res = await fetch('/api/capability_center/slides');
        const data = await res.json();
        if (res.ok && data.ok) s.decks = data.decks || [];
      } catch (_) {
        /* keep previous */
      }
    }

    async function openDeck(deckId) {
      try {
        const res = await fetch(
          '/api/capability_center/slides/' + encodeURIComponent(deckId)
        );
        const data = await res.json();
        if (res.ok && data.ok) {
          s.deck = data.deck;
          s.slideIdx = 0;
        }
      } catch (_) {
        /* keep previous */
      }
    }

    function closeDeck() {
      s.deck = null;
      s.slideIdx = 0;
      s.playing = false;
    }

    function next() {
      const n = ((s.deck && s.deck.slides) || []).length;
      if (s.slideIdx < n - 1) s.slideIdx += 1;
    }

    function prev() {
      if (s.slideIdx > 0) s.slideIdx -= 1;
    }

    function play() {
      if (s.deck) s.playing = true;
    }

    function exitPlay() {
      s.playing = false;
    }

    function newTraceId() {
      try {
        if (crypto.randomUUID) return crypto.randomUUID();
      } catch (_) {
        /* fall through */
      }
      return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        const v = c === 'x' ? r : (r & 0x3) | 0x8;
        return v.toString(16);
      });
    }

    async function submitInterview() {
      if (!s.selectedId || s.busy) return;
      s.busy = true;
      s.submitMsg = '';
      try {
        const res = await fetch('/api/capability_center/interview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            contract_id: s.selectedId,
            answers: s.answers,
            trace_id: newTraceId(),
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.submitMsg = data.error || 'HTTP ' + res.status;
          s.submitOk = false;
          return;
        }
        s.submitOk = true;
        s.submitMsg =
          'log #' + data.log_id + ' · ' + data.answered + '/' + data.total +
          ' answered · ' + data.streak_result +
          ' · streak ' + data.current_streak + '/' + data.target_streak;
        // Refresh the list so the streak column reflects the new value.
        await refresh();
      } catch (e) {
        s.submitMsg = 'Capability Center API unavailable: ' + (e.message || e);
        s.submitOk = false;
      } finally {
        s.busy = false;
      }
    }

    refresh();
    loadCatalog();

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity (v-model stops updating).
    return Object.assign(s, {
      questionCount,
      answeredCount,
      currentSlide,
      refresh,
      loadDetail,
      select,
      submitInterview,
      loadCatalog,
      openDeck,
      closeDeck,
      next,
      prev,
      play,
      exitPlay,
      persist,
    });
  },
};

export function mountCapabilityCenter(el) {
  const app = createApp(CapabilityCenter);
  app.mount(el);
  return app;
}
