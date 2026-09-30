/**
 * chat-center-setting.js — Chat Center "Setting" tab (2-step communication UI).
 *
 * Route: /llm-tasks/chat_center/setting
 *
 * STEP 1 — QUESTION side (BLUE background)
 *   left  : chat ID · SHA256 · from (source dropdown) · SESSION ID (Ctrl+Alt+T)
 *   right : chat box (human input / F9 paste) · title · question · Submit
 * STEP 2 — ANSWER side (PINK background), a MIRROR of STEP 1
 *   left  : chat ID · SHA256 · from · SESSION ID (echo of STEP 1)
 *   right : answer chat box · Submit
 *
 * Submit on STEP 1 is gated: every field must be non-empty before STEP 2 opens.
 *
 * Data sources (all DB-driven, no hard-coded lists):
 *   GET  /api/sources          -> the "from" dropdown (source table)
 *   GET  /api/templates        -> worker_identity row (SESSION ID template)
 *   POST /api/chat_center/submit -> resolve chat_id + sha256 (STEP 1)
 *   POST /api/chat_center/answer -> store the Answer row (STEP 2)
 *
 * Follows ui_skill: rounded-2xl / border-line / bg-panel / shadow-panel,
 * text-ink (primary), text-muted (secondary), text-accent (emphasis), mono (ids).
 */
import { createApp, reactive, computed, watch, nextTick } from 'vue';
import { mountPromptTemplateTable } from './prompt-template-table.js';

const LS_KEY = 'chat_center_setting_v1';
const TEMPLATE_KEY = 'worker_identity';

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

const ChatCenterSetting = {
  template: `
  <div class="mx-auto max-w-6xl">
    <!-- Header -->
    <div class="mb-4 flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 class="text-lg font-semibold text-ink">Chat Center · Setting</h2>
        <p class="mt-0.5 text-sm text-muted">
          STEP 1 Question (blue) → STEP 2 Answer (pink) ·
          <span class="mono">source</span> + <span class="mono">worker_identity</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <span class="rounded-full px-2.5 py-1 text-xs font-medium"
          :class="step === 1 ? 'bg-sky-100 text-sky-700' : 'bg-pink-100 text-pink-700'">
          STEP {{ step }} · {{ step === 1 ? 'QUESTION' : 'ANSWER' }}
        </span>
        <button
          v-if="step === 2"
          type="button"
          @click="backToStep1"
          class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft"
        >← Back to STEP 1</button>
      </div>
    </div>

    <!-- Error / info bar -->
    <div v-if="msg" class="mb-3 rounded-xl border px-3 py-2 text-sm"
      :class="msgOk
        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-rose-200 bg-rose-50 text-rose-700'">
      {{ msg }}
    </div>

    <!-- ==================== STEP 1: QUESTION (BLUE) ==================== -->
    <section
      v-if="step === 1"
      class="rounded-3xl border-2 border-sky-300 bg-sky-50 p-5 shadow-panel sm:p-6"
    >
      <div class="grid gap-4 lg:grid-cols-2">
        <!-- LEFT: identity -->
        <div class="rounded-2xl border border-sky-200 bg-white/70 p-4">
          <h3 class="mb-3 text-sm font-semibold text-ink">Identity</h3>

          <label class="mb-3 block">
            <span class="text-xs font-medium text-muted">chat ID</span>
            <input
              v-model="chatId"
              readonly
              placeholder="(resolved on submit)"
              class="mono mt-1 w-full rounded-xl border border-line bg-soft/40 px-3 py-2 text-sm text-ink outline-none"
            />
          </label>

          <label class="mb-3 block">
            <span class="text-xs font-medium text-muted">SHA256</span>
            <input
              v-model="sha256"
              readonly
              placeholder="(resolved on submit)"
              class="mono mt-1 w-full break-all rounded-xl border border-line bg-soft/40 px-3 py-2 text-xs text-ink outline-none"
            />
          </label>

          <label class="mb-3 block">
            <span class="text-xs font-medium text-muted">from (source)</span>
            <select
              v-model="sourceKey"
              class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            >
              <option value="">— select source —</option>
              <option v-for="s in sources" :key="s.source_key" :value="s.source_key">
                {{ s.name }} ({{ s.kind }}){{ s.hotkey ? ' · ' + s.hotkey : '' }}
              </option>
            </select>
            <span v-if="selectedSource" class="mt-1 block text-[11px] text-muted">
              <span v-if="selectedSource.url" class="mono break-all">{{ selectedSource.url }}</span>
              <span v-else>no url</span>
              <span v-if="selectedSource.hotkey"> · hotkey <span class="mono">{{ selectedSource.hotkey }}</span></span>
            </span>
          </label>

          <label class="mb-1 block">
            <span class="text-xs font-medium text-muted">
              SESSION ID
              <span class="ml-1 rounded bg-soft px-1.5 py-0.5 text-[10px] text-muted">Ctrl+Alt+T</span>
            </span>
            <div class="mt-1 flex items-center gap-2">
              <input
                v-model="sessionId"
                readonly
                class="mono w-full rounded-xl border border-line bg-soft/40 px-3 py-2 text-xs text-ink outline-none"
              />
              <button
                type="button"
                @click="newSession"
                class="shrink-0 rounded-xl border border-line bg-panel px-3 py-2 text-xs hover:bg-soft"
              >New</button>
            </div>
          </label>

          <div class="mt-3 rounded-xl border border-sky-200 bg-white/60 p-3">
            <div class="mb-1 flex items-center justify-between gap-2">
              <span class="text-xs font-semibold text-muted">
                SESSION ID template
                <span class="mono">({{ templateKey }})</span>
              </span>
              <button
                type="button"
                @click="loadTemplate"
                class="rounded-lg border border-line bg-panel px-2 py-0.5 text-[11px] hover:bg-soft"
              >Reload</button>
            </div>
            <div v-if="templateText"
              class="mono max-h-32 overflow-auto whitespace-pre-wrap text-[11px] text-ink">{{ templateText }}</div>
            <div v-else class="text-[11px] text-muted">
              Template not loaded — check <span class="mono">format_templates</span> row
              <span class="mono">{{ templateKey }}</span>.
            </div>
          </div>
        </div>

        <!-- RIGHT: question -->
        <div class="rounded-2xl border border-sky-200 bg-white/70 p-4">
          <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 class="text-sm font-semibold text-ink">Question</h3>
            <!-- 2 modes: prompt template (pick from table) / current (active text) -->
            <div class="flex items-center gap-1 rounded-xl border border-line bg-panel p-0.5">
              <button
                type="button"
                @click="rightMode = 'template'"
                class="rounded-lg px-2.5 py-1 text-[11px] font-medium transition"
                :class="rightMode === 'template' ? 'bg-accent text-white' : 'text-muted hover:bg-soft'"
              >prompt template</button>
              <button
                type="button"
                @click="rightMode = 'current'"
                class="rounded-lg px-2.5 py-1 text-[11px] font-medium transition"
                :class="rightMode === 'current' ? 'bg-accent text-white' : 'text-muted hover:bg-soft'"
              >current</button>
            </div>
          </div>

          <!-- MODE: prompt template — the DB-driven table -->
          <div v-if="rightMode === 'template'" class="mb-3">
            <!-- auto-detect suggestion: matched from the question text -->
            <div v-if="suggestion" class="mb-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span class="text-[11px] text-amber-800">
                  suggested template:
                  <span class="mono font-semibold">{{ suggestion.prompt_setting_key }}</span>
                  <span class="text-amber-700">({{ suggestion.name }})</span>
                </span>
                <button
                  type="button"
                  @click="useSuggestion"
                  class="rounded-lg bg-accent px-2 py-0.5 text-[11px] font-medium text-white hover:opacity-90"
                >Use</button>
              </div>
            </div>
            <div id="cc-setting-template-table"></div>
          </div>

          <!-- MODE: current — the active template text, editable inline -->
          <div v-else class="mb-3">
            <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
              <span class="text-xs font-medium text-muted">
                current template
                <span class="mono">({{ templateKey }})</span>
              </span>
              <div class="flex items-center gap-1">
                <button
                  type="button"
                  @click="loadTemplate"
                  class="rounded-lg border border-line bg-panel px-2 py-0.5 text-[11px] hover:bg-soft"
                >Reload</button>
                <button
                  type="button"
                  @click="saveTemplate"
                  :disabled="busy || !templateText.trim()"
                  class="rounded-lg px-2 py-0.5 text-[11px] font-medium text-white"
                  :class="(busy || !templateText.trim()) ? 'cursor-not-allowed bg-slate-300' : 'bg-accent hover:opacity-90'"
                >Save</button>
              </div>
            </div>
            <textarea
              v-model="templateText"
              rows="6"
              class="mono w-full resize-y rounded-xl border border-line bg-panel px-3 py-2 text-[11px] text-ink outline-none focus:border-accent"
            ></textarea>
            <div class="mt-1 text-[11px] text-muted">
              <span v-if="templateId" class="mono">id={{ templateId }}</span>
              <span v-else>template not found</span>
            </div>
          </div>

          <label class="mb-3 block">
            <span class="text-xs font-medium text-muted">title</span>
            <input
              v-model="title"
              placeholder="short title for this chat"
              class="mt-1 w-full rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            />
          </label>

          <label class="mb-2 block">
            <span class="text-xs font-medium text-muted">question</span>
            <textarea
              v-model="question"
              @keydown.ctrl.enter="submitStep1"
              rows="6"
              placeholder="輸入你嘅問題… (Ctrl+Enter 送出)"
              class="mt-1 w-full resize-none rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            ></textarea>
          </label>

          <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span class="text-[11px] text-muted">{{ question.trim().length }} chars</span>
            <button
              type="button"
              @click="copyFromChat"
              class="rounded-lg border border-line bg-panel px-2 py-1 text-[11px] hover:bg-soft"
            >Copy from chat ID</button>
          </div>

          <div class="flex items-center justify-between gap-2">
            <span class="text-[11px]"
              :class="canSubmitStep1 ? 'text-emerald-600' : 'text-rose-500'">
              {{ canSubmitStep1 ? 'all fields ready' : missingFields }}
            </span>
            <button
              type="button"
              @click="submitStep1"
              :disabled="!canSubmitStep1 || busy"
              class="rounded-xl px-5 py-2.5 text-sm font-semibold text-white shadow-panel transition"
              :class="(canSubmitStep1 && !busy) ? 'bg-accent hover:opacity-90' : 'cursor-not-allowed bg-slate-300'"
            >
              {{ busy ? 'Submitting…' : 'Submit → STEP 2' }}
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- ==================== STEP 2: ANSWER (PINK) ==================== -->
    <section
      v-else
      class="rounded-3xl border-2 border-pink-300 bg-pink-50 p-5 shadow-panel sm:p-6"
    >
      <div class="grid gap-4 lg:grid-cols-2">
        <!-- LEFT: identity (echo of STEP 1) -->
        <div class="rounded-2xl border border-pink-200 bg-white/70 p-4">
          <h3 class="mb-3 text-sm font-semibold text-ink">Identity</h3>

          <div class="mb-3">
            <span class="text-xs font-medium text-muted">chat ID</span>
            <div class="mono mt-1 rounded-xl border border-line bg-soft/40 px-3 py-2 text-sm text-ink">
              {{ chatId || '—' }}
            </div>
          </div>

          <div class="mb-3">
            <span class="text-xs font-medium text-muted">SHA256</span>
            <div class="mono mt-1 break-all rounded-xl border border-line bg-soft/40 px-3 py-2 text-xs text-ink">
              {{ sha256 || '—' }}
            </div>
          </div>

          <div class="mb-3">
            <span class="text-xs font-medium text-muted">from (source)</span>
            <div class="mt-1 rounded-xl border border-line bg-soft/40 px-3 py-2 text-sm text-ink">
              {{ selectedSource ? selectedSource.name + ' (' + selectedSource.kind + ')' : '—' }}
            </div>
          </div>

          <div class="mb-3">
            <span class="text-xs font-medium text-muted">SESSION ID</span>
            <div class="mono mt-1 break-all rounded-xl border border-line bg-soft/40 px-3 py-2 text-xs text-ink">
              {{ sessionId || '—' }}
            </div>
          </div>

          <div class="rounded-xl border border-pink-200 bg-white/60 p-3">
            <div class="mb-1 text-xs font-semibold text-muted">Question (echo)</div>
            <div class="text-sm font-medium text-ink">{{ title || '(no title)' }}</div>
            <div class="mt-1 whitespace-pre-wrap text-xs text-muted">{{ question || '—' }}</div>
          </div>
        </div>

        <!-- RIGHT: answer -->
        <div class="rounded-2xl border border-pink-200 bg-white/70 p-4">
          <h3 class="mb-3 text-sm font-semibold text-ink">Answer</h3>

          <div v-if="answers.length" class="mb-3 space-y-2">
            <div v-for="(a, i) in answers" :key="i"
              class="rounded-xl border border-line bg-soft/40 p-3">
              <div class="grid gap-1 text-xs">
                <div><span class="text-muted">who answer：</span>
                  <span class="font-medium text-ink">{{ a.who || 'LLM' }}</span></div>
                <div><span class="text-muted">time：</span>
                  <span class="text-ink">{{ a.time || '—' }}</span></div>
              </div>
              <div class="mt-2 whitespace-pre-wrap rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink">
                {{ a.output }}
              </div>
            </div>
          </div>
          <div v-else class="mb-3 rounded-xl border border-line bg-soft/40 p-4 text-sm text-muted">
            No answer yet — type below and submit.
          </div>

          <label class="mb-2 block">
            <span class="text-xs font-medium text-muted">answer</span>
            <textarea
              v-model="answerInput"
              @keydown.ctrl.enter="submitStep2"
              rows="6"
              placeholder="輸入答案… (Ctrl+Enter 送出)"
              class="mt-1 w-full resize-none rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink outline-none focus:border-accent"
            ></textarea>
          </label>

          <div class="flex items-center justify-between gap-2">
            <span class="text-[11px] text-muted">{{ answerInput.trim().length }} chars</span>
            <button
              type="button"
              @click="submitStep2"
              :disabled="!canSubmitStep2 || busy"
              class="rounded-xl px-5 py-2.5 text-sm font-semibold text-white shadow-panel transition"
              :class="(canSubmitStep2 && !busy) ? 'bg-accent hover:opacity-90' : 'cursor-not-allowed bg-slate-300'"
            >
              {{ busy ? 'Sending…' : 'Submit answer' }}
            </button>
          </div>
        </div>
      </div>
    </section>
  </div>
  `,

  setup() {
    const persisted = loadState() || {};
    const s = reactive({
      step: persisted.step || 1,
      chatId: persisted.chatId || '',
      sha256: persisted.sha256 || '',
      chatHash: persisted.chatHash || '',
      sessionId: persisted.sessionId || '',
      sourceKey: persisted.sourceKey || '',
      title: persisted.title || '',
      question: persisted.question || '',
      answerInput: persisted.answerInput || '',
      answers: persisted.answers || [],
      sources: [],
      templateText: '',
      templateKey: TEMPLATE_KEY,
      templateId: null,
      templateRows: [],
      rightMode: persisted.rightMode || 'template', // template | current
      busy: false,
      msg: '',
      msgOk: true,
    });

    function persist() {
      saveState({
        step: s.step,
        chatId: s.chatId,
        sha256: s.sha256,
        chatHash: s.chatHash,
        sessionId: s.sessionId,
        sourceKey: s.sourceKey,
        title: s.title,
        question: s.question,
        answerInput: s.answerInput,
        answers: s.answers,
        rightMode: s.rightMode,
      });
    }

    const selectedSource = computed(
      () => s.sources.find((x) => x.source_key === s.sourceKey) || null
    );

    // STEP 1 gate: every HUMAN-ENTERED field must be non-empty.
    // chat ID + SHA256 are OUTPUTS of the submit (resolved by the middleware),
    // so they are NOT part of the gate — requiring them would deadlock the form.
    const canSubmitStep1 = computed(
      () =>
        !!s.sourceKey.trim() &&
        !!s.sessionId.trim() &&
        !!s.title.trim() &&
        !!s.question.trim()
    );

    const missingFields = computed(() => {
      const miss = [];
      if (!s.sourceKey.trim()) miss.push('from');
      if (!s.sessionId.trim()) miss.push('SESSION ID');
      if (!s.title.trim()) miss.push('title');
      if (!s.question.trim()) miss.push('question');
      return miss.length ? 'missing: ' + miss.join(', ') : '';
    });

    const canSubmitStep2 = computed(
      () => !!s.chatId.trim() && !!s.answerInput.trim()
    );

    async function loadSources() {
      try {
        const res = await fetch('/api/sources');
        const data = await res.json();
        if (res.ok && data.ok) s.sources = data.rows || [];
      } catch (_) {
        /* keep previous */
      }
    }

    async function loadTemplate() {
      try {
        const res = await fetch('/api/templates');
        const data = await res.json();
        const rows = Array.isArray(data) ? data : data.rows || [];
        s.templateRows = rows;
        const row = rows.find((r) => r.prompt_setting_key === TEMPLATE_KEY);
        s.templateText = row ? String(row.instruction || '') : '';
        s.templateId = row ? row.id : null;
      } catch (_) {
        /* keep previous */
      }
    }

    /**
     * AUTO-DETECT: suggest a template from the question text.
     *
     * Scores each template by how many of its key/name tokens appear in the
     * question (case-insensitive). A template whose key is named in the question
     * wins outright. Returns null when nothing matches, so the UI shows no
     * suggestion rather than a wrong one.
     */
    const suggestion = computed(() => {
      const q = (s.question || '').toLowerCase();
      if (!q.trim() || !s.templateRows.length) return null;
      let best = null;
      let bestScore = 0;
      for (const r of s.templateRows) {
        const key = String(r.prompt_setting_key || '').toLowerCase();
        const name = String(r.name || '').toLowerCase();
        let score = 0;
        if (key && q.includes(key)) score += 10;
        for (const tok of key.split(/[_\s-]+/).filter((t) => t.length > 2)) {
          if (q.includes(tok)) score += 2;
        }
        for (const tok of name.split(/[_\s-]+/).filter((t) => t.length > 2)) {
          if (q.includes(tok)) score += 1;
        }
        if (score > bestScore) {
          bestScore = score;
          best = r;
        }
      }
      return bestScore > 0 ? best : null;
    });

    /** Apply the suggested template to the current editor. */
    function useSuggestion() {
      const r = suggestion.value;
      if (!r) return;
      s.templateKey = r.prompt_setting_key;
      s.templateId = r.id;
      s.templateText = String(r.instruction || '');
      s.rightMode = 'current';
      s.msg = 'Applied suggested template #' + r.id + ' (' + r.prompt_setting_key + ').';
      s.msgOk = true;
    }

    /** Save the inline-edited "current" template back to format_templates. */
    async function saveTemplate() {
      if (!s.templateId || !s.templateText.trim() || s.busy) return;
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch('/api/templates/' + s.templateId, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ instruction: s.templateText }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          s.msg = data.detail || data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.msg = 'Template #' + s.templateId + ' saved.';
        s.msgOk = true;
      } catch (e) {
        s.msg = String(e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    /**
     * FLOW → SETTING handoff. The flow page links here with
     * ?flow_key=...&step=N; resolve that step and prefill the question +
     * template, so clicking a flow step actually drives this page.
     */
    async function applyFlowStepFromUrl() {
      const params = new URLSearchParams(window.location.search);
      const flowKey = params.get('flow_key');
      const stepNo = params.get('step');
      if (!flowKey || !stepNo) return;
      try {
        const res = await fetch(
          '/api/flow_settings/run?flow_key=' + encodeURIComponent(flowKey) +
          '&step_no=' + encodeURIComponent(stepNo)
        );
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.msg = 'Flow step not applied: ' + (data.error || 'HTTP ' + res.status);
          s.msgOk = false;
          return;
        }
        if (data.question) s.question = data.question;
        if (data.template) {
          s.templateKey = data.template.prompt_setting_key;
          s.templateId = data.template.id;
          s.templateText = String(data.template.instruction || '');
          // Show the template that was just loaded, so the step is visible.
          // Consistent with the Use button and the table's pick handler.
          s.rightMode = 'current';
        }
        s.msg = 'Flow step ' + data.step_no + ' applied (' + (data.action || '—') + ').';
        s.msgOk = true;
        persist();
      } catch (e) {
        s.msg = 'Flow step not applied: ' + (e.message || e);
        s.msgOk = false;
      }
    }

    /** Mount the shared prompt-template table into the "template" mode slot. */
    function mountTemplateTable() {
      const el = document.getElementById('cc-setting-template-table');
      if (!el || el.__vueMounted) return;
      el.__vueMounted = true;
      try {
        mountPromptTemplateTable(el, {
          identityOnly: true,
          pickable: true,
          // Picking a row loads its instruction into the "current" editor and
          // switches to that mode, so the user sees what they just picked.
          onPick: (row) => {
            s.templateKey = row.prompt_setting_key;
            s.templateId = row.id;
            s.templateText = String(row.instruction || '');
            s.rightMode = 'current';
            s.msg = 'Loaded template #' + row.id + ' (' + row.prompt_setting_key + ').';
            s.msgOk = true;
          },
        });
      } catch (e) {
        el.innerHTML =
          '<div class="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-700">' +
          'Template table failed to mount: ' + (e.message || e) + '</div>';
      }
    }

    function newSession() {
      s.sessionId = newSessionId();
      persist();
    }

    /** Fill title + question from the resolved chat content (STEP 1 helper). */
    function copyFromChat() {
      if (!s.question.trim() && s.title.trim()) s.question = s.title;
      if (!s.title.trim() && s.question.trim()) {
        s.title = s.question.trim().split('\n')[0].slice(0, 80);
      }
      persist();
    }

    async function submitStep1() {
      if (!canSubmitStep1.value || s.busy) return;
      s.busy = true;
      s.msg = '';
      try {
        const res = await fetch('/api/chat_center/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: s.question,
            session_id: s.sessionId || undefined,
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          s.msg = data.error || 'HTTP ' + res.status;
          s.msgOk = false;
          return;
        }
        s.sessionId = data.session_id || s.sessionId;
        s.chatId = String(data.chat_id ?? '');
        s.sha256 = data.sha256 || data.chat_hash || '';
        s.chatHash = data.chat_hash || '';
        s.step = 2;
        s.msg = 'Identity resolved — STEP 2 (Answer) ready.';
        s.msgOk = true;
        persist();
      } catch (e) {
        s.msg = 'Chat Center API unavailable: ' + (e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    async function submitStep2() {
      if (!canSubmitStep2.value || s.busy) return;
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
            content: s.answerInput,
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
          output: data.answer || s.answerInput,
        });
        s.answerInput = '';
        s.msg = 'Answer stored.';
        s.msgOk = true;
        persist();
      } catch (e) {
        s.msg = 'Chat Center API unavailable: ' + (e.message || e);
        s.msgOk = false;
      } finally {
        s.busy = false;
      }
    }

    function backToStep1() {
      s.step = 1;
      persist();
    }

    if (!s.sessionId) s.sessionId = newSessionId();
    loadSources();
    loadTemplate().then(() => applyFlowStepFromUrl());

    // The template table lives in a v-if slot, so mount it AFTER the DOM exists
    // and re-mount whenever the mode flips back to 'template'.
    watch(
      () => s.rightMode,
      async () => {
        if (s.rightMode === 'template') {
          await nextTick();
          mountTemplateTable();
        }
      },
      { immediate: true }
    );

    // Persist as the user types so a host re-render cannot lose the draft.
    watch(
      () => [s.title, s.question, s.answerInput, s.sourceKey, s.rightMode],
      () => persist()
    );

    // NOTE: do NOT spread `s` ({ ...s }) — spreading a reactive() proxy copies
    // primitive snapshots and breaks reactivity (v-model stops updating).
    // Object.assign onto the same proxy keeps the two-way binding intact.
    return Object.assign(s, {
      selectedSource,
      canSubmitStep1,
      canSubmitStep2,
      missingFields,
      suggestion,
      useSuggestion,
      loadSources,
      loadTemplate,
      saveTemplate,
      applyFlowStepFromUrl,
      mountTemplateTable,
      newSession,
      copyFromChat,
      submitStep1,
      submitStep2,
      backToStep1,
    });
  },
};

export function mountChatCenterSetting(el) {
  const app = createApp(ChatCenterSetting);
  app.mount(el);
  return app;
}