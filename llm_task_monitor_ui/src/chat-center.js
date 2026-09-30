/**
 * Chat Center — Vue 3 component (3-step flow)
 *
 * STEP 1: user submits content (big eye-catching chat box).
 * STEP 2: middleware -> chat ID + sha256 (must be resolved before answer).
 * STEP 3: answer -> post chat_id + sha256 -> output.
 *
 * Persistence: localStorage (active chat + history). Server (chat_center_message)
 * is the audit SSOT; localStorage keeps the UI state across reloads.
 *
 * Design follows ui_skill: rounded-2xl / border-line / bg-panel / shadow-panel,
 * text-ink (primary), text-muted (secondary), text-accent (emphasis), mono (ids).
 */
import { createApp, reactive, computed, watch, nextTick } from 'vue';
import { mountFlowSettingTable } from './flow-setting-table.js';

const LS_KEY = 'chat_center_state_v1';

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

/** sha256 hex via WebCrypto (display mirror; server value is authoritative). */
async function sha256Hex(text) {
  try {
    const buf = await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(text)
    );
    return [...new Uint8Array(buf)]
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  } catch (_) {
    return '';
  }
}

function newSessionId() {
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

const ChatCenter = {
  template: `
  <div class="mx-auto max-w-5xl">
    <!-- Header -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-semibold text-ink">Chat Center</h2>
        <p class="mt-0.5 text-sm text-muted">
          3-step flow · submit → middleware (chat id + sha256) → answer ·
          <span class="mono">chat_center_message</span>
        </p>
      </div>
      <button
        v-if="chatId"
        @click="startNewChat"
        class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
      >+ New chat</button>
    </div>

    <!-- Error / info bar -->
    <div v-if="msg" class="mb-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">
      {{ msg }}
    </div>

    <!-- ============ STEP 1: BIG CHAT BOX (key / eye-catching) ============ -->
    <section
      v-if="step === 1"
      class="rounded-3xl border-2 border-accent/30 bg-panel p-6 shadow-panel sm:p-10"
    >
      <div class="mb-6 text-center">
        <div class="text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
          有什麼我能幫你的嗎？
        </div>
        <p class="mt-2 text-sm text-muted">
          What can I help you with?
        </p>
      </div>

      <!-- Must-field gate: content required to start -->
      <div class="mx-auto max-w-3xl">
        <textarea
          ref="input"
          v-model="content"
          @keydown.ctrl.enter="submit"
          rows="4"
          placeholder="輸入你嘅內容… (Ctrl+Enter 送出)"
          class="w-full resize-none rounded-2xl border-2 border-line bg-canvas px-4 py-3 text-base text-ink outline-none transition focus:border-accent"
        ></textarea>

        <div class="mt-3 flex flex-wrap items-center justify-between gap-2">
          <span class="text-xs text-muted">
            {{ content.trim().length }} chars ·
            <span :class="content.trim() ? 'text-emerald-600' : 'text-rose-500'">
              {{ content.trim() ? 'ready' : 'content required' }}
            </span>
          </span>
          <div class="flex items-center gap-2">
            <button
              @click="startNewChat"
              class="rounded-xl border border-line bg-panel px-3 py-2 text-sm hover:bg-soft"
            >New chat</button>
            <button
              @click="submit"
              :disabled="!canSubmit"
              class="rounded-xl px-5 py-2.5 text-sm font-semibold text-white shadow-panel transition"
              :class="canSubmit ? 'bg-accent hover:opacity-90' : 'cursor-not-allowed bg-slate-300'"
            >
              {{ busy ? 'Submitting…' : 'Submit →' }}
            </button>
          </div>
        </div>
      </div>

      <!-- Identity will appear here once resolved -->
      <div v-if="chatId" class="mx-auto mt-6 max-w-3xl rounded-2xl border border-line bg-soft/40 p-4">
        <div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
          Chat identity (must field)
        </div>
        <div class="grid gap-1 text-xs sm:grid-cols-2">
          <div><span class="text-muted">chat_id (id)：</span>
            <span class="mono font-semibold text-ink">{{ chatId }}</span></div>
          <div><span class="text-muted">sha256：</span>
            <span class="mono break-all">{{ sha256 }}</span></div>
          <div><span class="text-muted">session_id：</span>
            <span class="mono break-all">{{ sessionId }}</span></div>
        </div>
      </div>
    </section>

    <!-- ============ STEP 2/3: LEFT (progress) + RIGHT (answer) ============ -->
    <div v-else class="grid gap-4 lg:grid-cols-2">
      <!-- LEFT: analyzing / progress (collapsible accordion) -->
      <section class="rounded-2xl border border-line bg-panel shadow-panel">
        <button
          type="button"
          @click="analysisOpen = !analysisOpen"
          class="flex w-full items-center justify-between gap-2 px-5 py-4 text-left"
        >
          <h3 class="flex items-center gap-2 text-sm font-semibold text-ink">
            <span v-if="step === 2">Analyzing, pls wait ......</span>
            <span v-else>Analysis result</span>
            <span v-if="step === 2" class="inline-block h-2 w-2 animate-pulse rounded-full bg-accent"></span>
          </h3>
          <span class="shrink-0 text-xs text-muted">
            {{ analysisOpen ? '▾ hide' : '▸ detail' }}
          </span>
        </button>

        <div v-show="analysisOpen" class="border-t border-line px-5 pb-5 pt-3">
          <div v-if="step === 2" class="mb-3 flex items-center gap-2 text-sm text-muted">
            <span class="inline-block h-2 w-2 animate-pulse rounded-full bg-accent"></span>
            Analyzing input content…
          </div>

          <div class="space-y-2">
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <span class="text-xs text-muted">1) catalog</span>
            <div class="mono text-sm text-ink">
              = {{ analysis.catalog ? analysis.catalog.id : '—' }},
              {{ analysis.catalog ? analysis.catalog.name : '—' }}
            </div>
          </div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <span class="text-xs text-muted">2) subcatalog</span>
            <div class="mono text-sm text-ink">
              = {{ analysis.subcatalog ? analysis.subcatalog.id : '—' }},
              {{ analysis.subcatalog ? analysis.subcatalog.name : '—' }}
            </div>
          </div>
          <div class="rounded-xl border border-line bg-soft/40 px-3 py-2">
            <span class="text-xs text-muted">3) skill</span>
            <div class="mono text-sm text-ink">
              = {{ analysis.skill && analysis.skill.id ? analysis.skill.id : '—' }},
              {{ analysis.skill && analysis.skill.name ? analysis.skill.name : '—' }}
            </div>
          </div>
        </div>

        <div class="mt-4 rounded-xl border border-line bg-canvas p-3">
            <div class="mb-1 text-xs text-muted">Your input</div>
            <div class="whitespace-pre-wrap text-sm text-ink">{{ content }}</div>
          </div>

          <div class="mt-3 text-xs text-muted">
            <div><span class="text-muted">chat_id (id)：</span>
              <span class="mono font-semibold text-ink">{{ chatId }}</span></div>
            <div class="mt-1"><span class="text-muted">sha256：</span>
              <span class="mono break-all">{{ sha256 }}</span></div>
          </div>
        </div>
      </section>

      <!-- RIGHT: answer chat box (user can type) -->
      <section class="rounded-2xl border border-line bg-panel p-5 shadow-panel">
        <h3 class="mb-3 text-sm font-semibold text-ink">Answer</h3>

        <!-- Answer history for this chat -->
        <div v-if="answers.length" class="mb-3 space-y-2">
          <div v-for="(a, i) in answers" :key="i"
            class="rounded-xl border border-line bg-soft/40 p-3">
            <div class="grid gap-1 text-xs">
              <div><span class="text-muted">who answer：</span>
                <span class="font-medium text-ink">{{ a.who || 'LLM' }}</span></div>
              <div><span class="text-muted">time：</span>
                <span class="text-ink">{{ a.time || '—' }}</span></div>
            </div>
            <div class="mt-2 rounded-lg border border-line bg-canvas px-3 py-2">
              <div class="text-xs text-muted">output</div>
              <div class="whitespace-pre-wrap text-lg font-semibold text-ink">
                {{ a.output }}
              </div>
            </div>
          </div>
        </div>
        <div v-else class="mb-3 rounded-xl border border-line bg-soft/40 p-4 text-sm text-muted">
          <span v-if="step === 2">
            Waiting for answer… type below, then post
            <span class="mono">chat_id</span> + <span class="mono">sha256</span>.
          </span>
          <span v-else>No answer yet — type below and send.</span>
        </div>

        <!-- Answer chat box: the user types here -->
        <div class="rounded-2xl border-2 border-line bg-canvas p-3 transition focus-within:border-accent">
          <textarea
            v-model="answerInput"
            @keydown.ctrl.enter="ask"
            rows="3"
            placeholder="Type your message here… (Ctrl+Enter to send)"
            class="w-full resize-none bg-transparent px-1 py-1 text-sm text-ink outline-none"
          ></textarea>
          <div class="mt-1 flex flex-wrap items-center justify-between gap-2">
            <span class="text-[11px] text-muted">
              {{ (answerInput || '').trim().length }} chars
            </span>
            <div class="flex items-center gap-2">
              <button
                v-if="step === 2"
                @click="ask"
                :disabled="busy"
                class="rounded-xl px-4 py-2 text-sm font-semibold text-white shadow-panel transition"
                :class="busy ? 'cursor-not-allowed bg-slate-300' : 'bg-accent hover:opacity-90'"
              >
                {{ busy ? 'Asking…' : 'Ask →' }}
              </button>
              <button
                v-else
                @click="ask"
                :disabled="busy || !(answerInput || '').trim()"
                class="rounded-xl px-4 py-2 text-sm font-semibold text-white shadow-panel transition"
                :class="(busy || !(answerInput || '').trim()) ? 'cursor-not-allowed bg-slate-300' : 'bg-accent hover:opacity-90'"
              >
                {{ busy ? 'Sending…' : 'Send →' }}
              </button>
            </div>
          </div>
        </div>

        <div class="mt-3">
          <button
            v-if="step === 3"
            @click="startNewChat"
            class="rounded-xl border border-line bg-panel px-4 py-2 text-sm hover:bg-soft"
          >+ Start new chat</button>
        </div>
      </section>
    </div>

    <!-- History (from DB) -->
    <section v-if="history.length" class="mt-4 rounded-2xl border border-line bg-panel p-5 shadow-panel">
      <h3 class="mb-3 text-sm font-semibold text-ink">History ({{ history.length }})</h3>
      <div class="space-y-2">
        <div v-for="h in history" :key="h.id"
          class="rounded-xl border border-line bg-soft/40 px-3 py-2">
          <div class="mb-1 flex flex-wrap items-center gap-2 text-[11px]">
            <span class="rounded-full px-2 py-0.5 font-medium"
              :class="h.role === 'Answer'
                ? 'bg-emerald-100 text-emerald-700'
                : 'bg-sky-100 text-sky-700'">{{ h.role }}</span>
            <span class="text-muted">{{ h.created_at }}</span>
            <span v-if="h.llm" class="mono text-muted">{{ h.llm }}</span>
          </div>
          <div class="whitespace-pre-wrap text-sm text-ink">{{ h.content }}</div>
        </div>
      </div>
    </section>

    <!-- Flow setting (DB-driven; click a question or value to drive the flow) -->
    <section class="mt-4">
      <div id="cc-flow-setting-table"></div>
    </section>
  </div>
  `,

  setup() {
    const persisted = loadState() || {};
    const s = reactive({
      step: persisted.step || 1,
      content: persisted.content || '',
      sessionId: persisted.sessionId || '',
      chatId: persisted.chatId || '',
      sha256: persisted.sha256 || '',
      chatHash: persisted.chatHash || '',
      analysis: persisted.analysis || { catalog: null, subcatalog: null, skill: null },
      answer: persisted.answer || { who: '', time: '', output: '' },
      history: persisted.history || [],
      // Collapsible progress panel + answer chat box (multi-turn).
      analysisOpen: persisted.analysisOpen !== false,
      answers: persisted.answers || [],
      answerInput: persisted.answerInput || '',
      busy: false,
      msg: '',
      msgOk: true,
    });

    function persist() {
      saveState({
        step: s.step,
        content: s.content,
        sessionId: s.sessionId,
        chatId: s.chatId,
        sha256: s.sha256,
        chatHash: s.chatHash,
        analysis: s.analysis,
        answer: s.answer,
        history: s.history,
        analysisOpen: s.analysisOpen,
        answers: s.answers,
        answerInput: s.answerInput,
      });
    }

    const canSubmit = computed(() => s.content.trim().length > 0 && !s.busy);

    async function fetchHistory() {
      if (!s.chatId) return;
      try {
        const res = await fetch(
          '/api/chat_center/history?chat_id=' + encodeURIComponent(s.chatId)
        );
        const data = await res.json();
        if (res.ok && data.ok) s.history = data.rows || [];
      } catch (_) {
        /* keep previous */
      }
    }

    async function submit() {
      if (!canSubmit.value) return;
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch('/api/chat_center/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: s.content, session_id: s.sessionId || undefined }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.msg = data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.sessionId = data.session_id || s.sessionId;
        s.chatId = data.chat_id;
        s.sha256 = data.sha256 || data.chat_hash || '';
        s.chatHash = data.chat_hash || '';
        s.analysis = data.analysis || s.analysis;
        s.answer = { who: '', time: '', output: '' };
        s.answers = [];
        s.answerInput = '';
        s.step = 2;
        s.analysisOpen = true;   // open while processing
        s.msg = 'Identity resolved — chat_id + sha256 ready.';
        s.msgOk = true;
        persist();
        await fetchHistory();
      } catch (e) {
        s.msg = 'Chat Center API unavailable: ' + (e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    async function ask() {
      if (!s.chatId) return;
      const text = (s.answerInput || '').trim() || '123';
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch('/api/chat_center/answer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: s.chatId,
            sha256: s.sha256,
            chat_hash: s.chatHash,
            session_id: s.sessionId,
            content: text,
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.msg = data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.answers.push({
          who: data.who || 'LLM',
          time: data.answered_at || '',
          output: data.answer || text,
        });
        s.answer = {
          who: data.who || 'LLM',
          time: data.answered_at || '',
          output: data.answer || text,
        };
        s.answerInput = '';
        s.step = 3;
        s.analysisOpen = false;  // auto-close when the answer arrives
        s.msgOk = true;
        s.msg = 'Answer received.';
        persist();
        await fetchHistory();
      } catch (e) {
        s.msg = 'Chat Center API unavailable: ' + (e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    function startNewChat() {
      s.step = 1;
      s.content = '';
      s.sessionId = newSessionId();
      s.chatId = '';
      s.sha256 = '';
      s.chatHash = '';
      s.analysis = { catalog: null, subcatalog: null, skill: null };
      s.answer = { who: '', time: '', output: '' };
      s.history = [];
      s.answers = [];
      s.answerInput = '';
      s.analysisOpen = true;
      s.msg = '';
      s.msgOk = true;
      persist();
    }

    if (!s.sessionId) s.sessionId = newSessionId();
    if (s.chatId) fetchHistory();

    // Persist as the user types so a host re-render cannot lose the draft.
    watch(() => s.content, () => persist());

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity (v-model stops updating).
    // Object.assign onto the same proxy keeps the two-way binding intact.
    return Object.assign(s, { canSubmit, submit, ask, startNewChat });
  },
};

export function mountChatCenter(el) {
  const app = createApp(ChatCenter);
  app.mount(el);
  // The flow-setting table is a SEPARATE Vue app mounted into a slot inside
  // this component's template. Mounting it after the parent app means the slot
  // element exists; the table owns its own subtree (ui_skill pitfall 4).
  nextTick(() => {
    const slot = document.getElementById('cc-flow-setting-table');
    if (slot && !slot.__vueMounted) {
      slot.__vueMounted = true;
      try {
        mountFlowSettingTable(slot, { flowKey: 'chat_center_identity' });
      } catch (e) {
        slot.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Flow table failed to mount: ' + (e.message || e) + '</div>';
      }
    }
  });
  return app;
}