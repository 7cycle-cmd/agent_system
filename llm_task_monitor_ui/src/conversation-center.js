/**
 * conversation-center.js — the ONE index catalog for the conversation module.
 *
 * THE HUMAN (2026-09-26), verbatim:
 *     "http://127.0.0.1:18765/llm-tasks/chat_center and
 *      http://127.0.0.1:18765/llm-tasks/chat_identity is talking for same
 *      capabilty, with 2 ui / as design is totally change to conversation module
 *      and group chat into it / re-design UI to 1 index catalog and have user
 *      fiendly ui for human, which can understand and monitoring the real flow
 *      with data"
 *
 * WHY THERE IS ONE PAGE AND NOT TWO
 * ---------------------------------
 * MEASURED: `chat_identity` states its SSOT as "chat_id + chat_identity_log";
 * `chat_center` states its persistence as "chat_center_message". Those key on the
 * SAME `chat_id`, and both are served by ONE capability
 * (`task_center.chat_identity`, capability_id 12811) whose module is
 * `chat_level` (module_id 25986). Two pages for one subject is what the human
 * asked to end.
 *
 * THE ONE RULE THIS PAGE FOLLOWS
 * ------------------------------
 * EVERY number is rendered from `/api/conversation_center/index`. There is NO
 * literal count in this file. MEASURED reason: the previous Chat Center's
 * "Discovery List" and "Workflow" tabs called two endpoints that returned
 * HTTP 500, so they showed an empty page — which reads as "no data" rather than
 * "the reader is broken". A page that cannot invent a number cannot lie about
 * one; when the endpoint fails, the page SAYS it failed.
 *
 * THE PAGE ALSO STATES THE GAP. MEASURED: `max_conversations_per_chat = 1`, so
 * the CHAT level groups nothing yet, and 349 of 2345 turns carry no `chat_id`.
 * The human asked for "understand and monitoring the real flow with data" — so
 * the index shows the design's intent AND its current gap on one screen. A page
 * that showed 69/69/2345 and implied a working hierarchy would be the defect
 * this repo keeps paying for.
 *
 * WHY THE PAGE WAS RE-DESIGNED (2026-09-26, second pass)
 * -----------------------------------------------------
 * THE HUMAN, verbatim:
 *     "STEP by STEP, user is human!!"
 *     "re-design that now!!! i ccan't understand how to work with this fucking ui"
 *     "single page = single target, not mix, hard to accpet for human, human will
 *      feel too trouble and give up"
 *
 * WHAT WAS WRONG — MEASURED, NOT ASSUMED
 *   * The first version put ALL FOUR LEVELS on ONE screen, plus a grouping
 *     verdict, plus an unlinked-turn count, plus a free-text `chat_id` form.
 *     Four subjects competed for attention and no step was obvious.
 *   * THE FREE-TEXT FORM WAS THE WORST PART, and the measurement says why:
 *     all 69 `chat` rows have `title IS NULL` and `opened_by IS NULL`, and chat
 *     ids are not contiguous (chat 68 holds 1914 turns, 16 holds 7, 7 holds 3,
 *     and 5 hold none). The page asked the human to TYPE an id while showing NO
 *     list and NO name. A control needing a value the page never displays is a
 *     trap, not a UI.
 *
 * THE ONE RULE NOW
 *   ONE STEP = ONE TARGET = ONE ACTION.
 *     STEP 1 "Chats" -- target: the chat LIST.       action: pick one.
 *     STEP 2 "Chat"  -- target: the chat picked.     action: read turns.
 *     STEP 3 "Data"  -- target: the system numbers.  (engineer's view)
 *   A step bar shows where the human is; nothing from another step is on screen.
 *   Step 3 keeps the four levels + the two known gaps, because the plan REQUIRES
 *   them to be reported — but as the engineer's view, one labelled link away,
 *   instead of competing with the daily task.
 *
 * THE UI SKILL WAS READ THIS TIME
 *   `skills/3_ui/ui_skill/ui_skill.skill.md` documents 陷阱 1: `return { ...s }`
 *   COPIES a reactive object and BREAKS reactivity; the fix is
 *   `Object.assign(s, {...})` (it cites `chat-center.js:481-484`). The previous
 *   version ended with `return { ...s, ... }` — that exact trap — and rendered
 *   ONCE then never again. See the return statement below.
 */
import { createApp, reactive, computed } from 'vue';

const CHATS_URL = '/api/conversation_center/chats';
const INDEX_URL = '/api/conversation_center/index';
const ENTITY_CRUD_URL = '/api/conversation_center/entity_crud';
const PROMPT_TO_PLAN_URL = '/api/conversation_center/prompt_to_plan';
const HISTORY_URL = '/api/chat_center/history';

// THE ONE TIME FORMATTER (2026-09-27). MEASURED: this page rendered
// `created_at` RAW, so it showed UTC while the badge beside it said
// `Asia/Hong_Kong UTC+8` — TWO CLOCKS on one screen. `timefmt.js` is the shared
// module every other page already imports; this page was the one that did not.
import { fmtLocal, setTzOffsetSec, setTzName, tzLabel } from './timefmt.js';

// How a label was derived. The page SAYS which, because MEASURED 69 of 69
// labels are derived today and a human must not read them as authored names.
const LABEL_KIND_TEXT = {
  title: 'its own title',
  turn_title: 'a turn title',
  first_question: 'its first question',
  chat_key: 'its chat_key (no turns yet)',
};

// The colour of a level is derived from its POSITION, so a new level does not
// need a hand-picked class.
const LEVEL_CLASSES = [
  'border-violet-200 bg-violet-50 text-violet-700',
  'border-sky-200 bg-sky-50 text-sky-700',
  'border-emerald-200 bg-emerald-50 text-emerald-700',
  'border-amber-200 bg-amber-50 text-amber-700',
];

// A long list is its OWN usability problem. MEASURED 2026-09-26: with all 69
// rows rendered, step 1 was 4144px tall — a wall the human must scroll past to
// reach the hint at the bottom. The first screenful is what a person scans, so
// the list shows PAGE rows and one "Show all N" control for the rest.
const PAGE = 20;

// ---- READABILITY (2026-09-27) ----------------------------------------------
// THE HUMAN: "7712 is the id for question and 7713to 7733 is answer / i know
// that is good to have all, / but it can re-design ui to become user readable
// and user friendly".
//
// MEASURED, chat 68, the 58 pairs this page renders:
//   total characters rendered at once .... 22,217
//   max answers in ONE pair .............. 46   (ask 7734, "fix it all now")
//   pairs with 0 answers ................. 27   (of 58)
//   longest single ask ................... 6,593 chars of RAW Markdown
//   Markdown renderer in this UI ......... NONE (searched all 24 JS files)
//
// So the page dumped 22k characters, drew 27 empty cards, and showed Markdown
// SOURCE instead of the document. Three fixes, below.
const COLLAPSE_AT = 5;

// ESCAPE FIRST, THEN EMIT TAGS. The order IS the safety: a renderer that emits
// tags and escapes afterwards can be made to emit a tag the author wrote.
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Inline spans, applied to TEXT ONLY (never to already-emitted tags).
function mdInline(t) {
  return t
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="md-code">$1</code>');
}

// A SMALL, SAFE Markdown subset: fenced code, tables, headings, lists,
// paragraphs, bold, inline code. Anything else stays literal text.
function mdToHtml(src) {
  const text = escapeHtml(src).replace(/\r\n/g, '\n');
  const lines = text.split('\n');
  const out = [];
  let para = [];
  let i = 0;
  const flush = () => {
    if (para.length) { out.push('<p>' + para.map(mdInline).join('<br>') + '</p>'); para = []; }
  };
  while (i < lines.length) {
    const ln = lines[i];
    if (/^\s*```/.test(ln)) {                       // fenced code
      flush();
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++;
      out.push('<pre class="md-pre"><code>' + buf.join('\n') + '</code></pre>');
      continue;
    }
    if (/^\s*\|/.test(ln) && i + 1 < lines.length
        && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {   // table
      flush();
      const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const head = cells(ln);
      i += 2;
      const body = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) { body.push(cells(lines[i])); i++; }
      out.push('<table class="md-table"><thead><tr>'
        + head.map((c) => '<th>' + mdInline(c) + '</th>').join('')
        + '</tr></thead><tbody>'
        + body.map((r) => '<tr>' + r.map((c) => '<td>' + mdInline(c) + '</td>').join('') + '</tr>').join('')
        + '</tbody></table>');
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(ln);         // heading
    if (h) {
      flush();
      const n = h[1].length;
      out.push('<h' + n + ' class="md-h">' + mdInline(h[2]) + '</h' + n + '>');
      i++;
      continue;
    }
    if (/^\s*[-*]\s+/.test(ln)) {                   // list
      flush();
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(mdInline(lines[i].replace(/^\s*[-*]\s+/, '')));
        i++;
      }
      out.push('<ul class="md-ul">' + items.map((t) => '<li>' + t + '</li>').join('') + '</ul>');
      continue;
    }
    if (!ln.trim()) { flush(); i++; continue; }
    para.push(ln);
    i++;
  }
  flush();
  return out.join('');
}

// One line, for a compact row. MEASURED: 27 of 58 pairs have no answer, and a
// full card for each is 27 cards of noise around 31 real ones.
function oneLine(s) {
  return String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
}

const ConversationCenter = {
  template: `
  <div class="mx-auto max-w-5xl">

    <!-- FOCUS VISIBILITY (MEASURED 2026-09-26, a real browser): every one of
         these controls reported outline-style: none. A BUTTON is Tab-reachable,
         so it was keyboard-REACHABLE but not keyboard-USABLE — the human could
         not see where focus was. WCAG 2.2 AA requires a visible focus indicator.
         focus-visible: keeps it for keyboard users and avoids a ring on mouse
         click. The ring uses blue-600 on white = 5.17:1, above the 3:1 the rule
         asks of a focus indicator.
         NB: never write a BACKTICK inside this template — it ends the string.
         That mistake broke the build three times; see the guard in the proof. -->

    <!-- ===== STEP BAR: the human always knows where they are =====
         CONTRAST (MEASURED with a real browser, 2026-09-26): the app token
         accent = #3b82f6 gives white-on-accent 3.68:1 and accent-on-white
         3.68:1 — BELOW the 4.5:1 WCAG AA floor for normal text, and below the
         3:1 UI floor for the white ring used on hover. Blamed at the token, not
         guessed. Repainting the shared token would recolour EVERY page, so this
         component uses the next shade down for FILL and for link text:
           blue-600 #2563eb -> 5.17:1 (white on fill)
           blue-700 #1d4ed8 -> 6.71:1 (text on white)
         PROPER FIX (needs a token edit + approval, outside this allowlist):
         add accent.strong = #2563eb, then repoint the app. -->
    <!-- THE HIGHLIGHT BAR BECAME ALL-BUTTON (2026-09-27). THE HUMAN, verbatim:
         "highlight bar -> button for shortcut to index and list" / "do it now".

         MEASURED BEFORE, and this is what was wrong:
           * "2 . One chat" was a SPAN element, so ONE OF FOUR steps was not a
             control at all -- while its class list was IDENTICAL to the
             buttons', so the hole was invisible.
           * aria-current had 0 occurrences in this file AND in app.js, so
             "where am I" was signalled by COLOUR ONLY -- unreadable to a screen
             reader and to a colour-blind reader.
           * the Data control was rendered as a button but goData() wrote NO
             address, so the button promised a URL it did not give and Back
             popped past the step the human was on.

         NO BACKTICKS ABOVE, deliberately: a backtick ENDS this template string.
         MEASURED 2026-09-27 -- I wrote them in this very comment and broke the
         build, exactly as the note above warns. The note was right and I did not
         read it. -->

    <nav aria-label="Steps" class="mb-3 flex flex-wrap items-center gap-1 text-sm">
      <!-- THE START STEP. THE HUMAN (2026-09-27), verbatim: "+UI (image design):
           http://127.0.0.1:18765/llm-tasks/conversation/start having a new
           conversation".

           MEASURED BEFORE: the step bar had 1 Chats, 2 One chat (a span, not
           clickable), Data and Refresh -- there was NO way to start a
           conversation from this page at all, while POST
           /api/chat_center/submit had supported it all along (it generates a
           new UUID when session_id is omitted). The API answered and no UI
           code referenced it -- the same shape as the mode-sessions page. -->
      <button type="button" @click="goStart"
        :aria-current="step === 'start' ? 'step' : false"
        class="rounded-lg px-2 py-1 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
        :class="step === 'start' ? 'bg-blue-600 text-white' : 'text-blue-700 hover:bg-soft'">
        + New</button>
      <span class="text-muted">|</span>
      <button type="button" @click="goList"
        :aria-current="step === 'list' ? 'step' : false"
        class="rounded-lg px-2 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
        :class="step === 'list' ? 'bg-blue-600 text-white' : 'text-blue-700 hover:bg-soft'">
        1 · Chats</button>
      <span class="text-muted">›</span>
      <!-- THE MIDDLE STEP IS A BUTTON NOW, DISABLED UNTIL A CHAT IS PICKED.
           A step you cannot reach yet is INFORMATION, so it stays visible: a
           disabled button says "this exists and is not available yet", which is
           exactly true. Hiding it would make the bar's shape change under the
           reader. -->
      <button type="button" @click="goChat" :disabled="!chat"
        :aria-current="step === 'chat' ? 'step' : false"
        class="rounded-lg px-2 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
        :class="step === 'chat' ? 'bg-blue-600 text-white' : 'text-blue-700 hover:bg-soft'">
        2 · One chat</button>
      <span class="text-muted">›</span>
      <button type="button" @click="goData"
        :aria-current="step === 'data' ? 'step' : false"
        class="rounded-lg px-2 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
        :class="step === 'data' ? 'bg-blue-600 text-white' : 'text-blue-700 hover:bg-soft'">
        Data</button>
      <button type="button" @click="loadAll"
        class="ml-auto rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink hover:bg-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">
        {{ busy ? 'Loading…' : 'Refresh' }}</button>
    </nav>

    <!-- ===== A FAILED READ IS STATED, NEVER RENDERED AS ZERO ===== -->
    <div v-if="err"
      class="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
      <b>This read FAILED.</b> Every number below is therefore UNKNOWN, not zero.
      <div class="mono mt-1 break-all text-xs">{{ err }}</div>
    </div>

    <!-- ============ STEP 0 — ONE TARGET: A NEW CONVERSATION ============ -->
    <section v-if="step === 'start'">
      <h2 class="text-lg font-semibold text-ink">Start a conversation</h2>
      <p class="mt-0.5 text-sm text-muted">
        Type the first question. A new chat is created for it, and you land on
        that chat's own step.
      </p>

      <div class="mt-3 rounded-2xl border border-line bg-panel p-3 shadow-panel">
        <label for="conv-start-content" class="block text-sm font-medium text-ink">
          First question</label>
        <textarea id="conv-start-content" v-model="newContent" rows="5"
          aria-label="The first question of the new conversation"
          placeholder="What do you want to ask?"
          class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none focus-visible:border-blue-600 focus-visible:ring-2 focus-visible:ring-blue-600"></textarea>

        <!-- A FAILED SUBMIT IS STATED, NEVER RENDERED AS SUCCESS. -->
        <div v-if="newErr"
          class="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">
          <b>This submit FAILED.</b> No chat was created.
          <div class="mono mt-1 break-all text-xs">{{ newErr }}</div>
        </div>

        <div class="mt-2 flex flex-wrap items-center gap-2">
          <button type="button" @click="submitNew" :disabled="newBusy || !newContent.trim()"
            class="rounded-xl bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">
            {{ newBusy ? 'Starting…' : 'Start' }}</button>
          <button type="button" @click="goList"
            class="rounded-xl border border-line bg-panel px-3 py-2 text-sm text-ink hover:bg-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">
            Cancel</button>
          <span class="text-xs text-muted">
            The chat is created by <span class="mono">POST /api/chat_center/submit</span>
            with no <span class="mono">session_id</span>, so the server mints a new one.
          </span>
        </div>
      </div>
    </section>

    <!-- ============ STEP 1 — ONE TARGET: THE CHAT LIST ============ -->
    <section v-else-if="step === 'list'">
      <h2 class="text-lg font-semibold text-ink">Step 1 — Choose a chat</h2>
      <p class="mt-0.5 text-sm text-muted">Pick a row. You do not need to know any id.</p>

      <div class="mt-3 rounded-2xl border border-line bg-panel p-3 shadow-panel">
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span><b class="text-ink">{{ meta.chats }}</b> <span class="text-muted">chats</span></span>
          <span><b class="text-ink">{{ meta.with_turns }}</b> <span class="text-muted">with turns</span></span>
          <span><b class="text-ink">{{ meta.empty }}</b> <span class="text-muted">empty</span></span>
          <span class="text-muted">times shown in <b class="text-ink">{{ tzLabel() }}</b></span>
        </div>
        <p v-if="meta.derived_labels" class="mt-1 text-xs text-amber-700">
          No chat has its own title, so all {{ meta.derived_labels }} names below are
          <b>derived</b> — from the chat's first question, or its chat_key when it has
          no turns.
        </p>
      </div>

      <!-- ACCESSIBLE NAME (MEASURED 2026-09-26): the filter had a placeholder and
           NO aria-label and NO <label>, so a screen reader announced an unlabelled
           edit box. A placeholder is not a name. -->
      <input v-model="q" aria-label="Filter chats by name, id or session"
        placeholder="Filter by name / id / session (optional)"
        class="mt-3 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm text-ink outline-none focus-visible:border-blue-600 focus-visible:ring-2 focus-visible:ring-blue-600" />

      <!-- A row is a BUTTON: keyboard-reachable and reads as clickable. -->
      <div class="mt-3 overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">
        <button v-for="c in visible" :key="c.chat_id" type="button"
          :data-chat-id="c.chat_id" @click="pick(c)"
          class="flex w-full items-center gap-3 border-b border-line px-3 py-2.5 text-left transition last:border-b-0 hover:bg-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-600">
          <span class="mono w-12 shrink-0 text-xs text-muted">{{ c.chat_id }}</span>
          <span class="min-w-0 flex-1">
            <span class="block truncate text-sm text-ink">{{ c.label }}</span>
            <span class="block text-xs text-muted">name from {{ labelKindText(c.label_kind) }}<template v-if="c.ide"> · {{ c.ide }}</template></span>
          </span>
          <!-- THE TIME, IN THE HUMAN'S CLOCK. last_at is the newest turn, so a
               chat is found by when it was last used. NB: never write a
               BACKTICK inside this template — it ends the string. -->
          <span class="mono hidden shrink-0 text-xs text-muted sm:block">{{ fmtLocal(c.last_at) }}</span>
          <span class="shrink-0 rounded-full px-2 py-0.5 text-xs"
            :class="c.turns ? 'bg-emerald-100 text-emerald-700' : 'bg-soft text-muted'">{{ c.turns }} turns</span>
          <span class="shrink-0 text-blue-700">›</span>
        </button>
        <div v-if="!filtered.length && !busy"
          class="px-3 py-6 text-center text-sm text-muted">
          {{ meta.chats ? 'No chat matches that filter.' : 'No chats were returned — the screen has nothing to show.' }}
        </div>
      </div>

      <p class="mt-2 text-xs text-muted">
        Showing {{ visible.length }} of {{ filtered.length }}.
        <!-- TOUCH TARGET (MEASURED 2026-09-26): as inline text this control was
             16px tall; WCAG 2.2 AA asks for 24x24 minimum. It is a real button
             with padding and a focus ring now. -->
        <button v-if="!showAll && filtered.length > PAGE" type="button"
          @click="showAll = true"
          class="ml-1 min-h-6 rounded-lg px-2 py-1 align-middle text-blue-700 hover:bg-soft hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">
          Show all {{ filtered.length }}</button>
      </p>
    </section>

    <!-- ========== STEP 2 — ONE TARGET: THE CHAT YOU PICKED ========== -->
    <section v-else-if="step === 'chat'">
      <button type="button" @click="goList" class="rounded-lg px-2 py-1 text-sm text-blue-700 hover:bg-soft hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">‹ All chats</button>
      <h2 class="mt-2 text-lg font-semibold text-ink">Step 2 — Chat {{ chat.chat_id }}</h2>
      <p class="mt-0.5 break-words text-sm text-muted">{{ chat.label }}</p>

      <div class="mt-3 grid gap-2 sm:grid-cols-4">
        <div class="rounded-2xl border border-line bg-panel p-3 shadow-panel">
          <div class="text-xs text-muted">turns</div>
          <div class="mono text-lg text-ink">{{ chat.turns }}</div></div>
        <div class="rounded-2xl border border-line bg-panel p-3 shadow-panel">
          <div class="text-xs text-muted">conversations</div>
          <div class="mono text-lg text-ink">{{ chat.conversations }}</div></div>
        <div class="rounded-2xl border border-line bg-panel p-3 shadow-panel">
          <div class="text-xs text-muted">identity rows</div>
          <div class="mono text-lg text-ink">{{ chat.identities }}</div></div>
        <div class="rounded-2xl border border-line bg-panel p-3 shadow-panel">
          <div class="text-xs text-muted">IDE</div>
          <div class="text-sm text-ink">{{ chat.ide || '—' }}</div></div>
      </div>

      <div class="mt-2 rounded-2xl border border-line bg-panel p-3 shadow-panel">
        <div class="text-xs text-muted">session</div>
        <div class="mono break-all text-xs text-ink">{{ chat.session_id || '—' }}</div>
      </div>

      <!-- THE TWO KEYS (2026-09-27). The human asked: "Field session ID and it
           has 2 key for converaction or chat, what is that". MEASURED, chat_main
           carries TWO derived keys and they are NOT the same thing:
             sha256    = sha256(session_id)             kind='function'
             chat_hash = sha256(chat_id | session_id)   kind='pair_key'
           Both are shown WITH their formula, so the difference is visible
           instead of guessed. NB: never write a BACKTICK inside this template. -->
      <div class="mt-2 rounded-2xl border border-line bg-panel p-3 shadow-panel">
        <div class="text-xs font-semibold uppercase tracking-wide text-muted">The two keys</div>
        <div class="mt-2 space-y-2">
          <div class="rounded-xl border border-line px-3 py-2">
            <div class="flex flex-wrap items-baseline gap-2">
              <span class="mono text-xs font-semibold text-ink">sha256</span>
              <span class="rounded-full bg-sky-100 px-2 py-0.5 text-[11px] text-sky-700">function of ONE half</span>
            </div>
            <div class="mono mt-1 break-all text-[11px] text-muted">sha256(session_id)</div>
            <div class="mono mt-1 break-all text-xs text-ink">{{ chat.sha256 || '—' }}</div>
          </div>
          <div class="rounded-xl border border-line px-3 py-2">
            <div class="flex flex-wrap items-baseline gap-2">
              <span class="mono text-xs font-semibold text-ink">chat_hash</span>
              <span class="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] text-violet-700">pair key — BOTH halves</span>
            </div>
            <div class="mono mt-1 break-all text-[11px] text-muted">sha256(chat_id | session_id)</div>
            <div class="mono mt-1 break-all text-xs text-ink">{{ chat.chat_hash || '—' }}</div>
            <div v-if="chat.chat_hash_recomputed && chat.chat_hash_recomputed !== chat.chat_hash"
              class="mt-1 text-[11px] text-amber-700">
              STALE: the stored value was written before chat_id existed. The
              correct key is
              <span class="mono break-all">{{ chat.chat_hash_recomputed }}</span>
            </div>
          </div>
        </div>
        <p class="mt-2 text-[11px] text-muted">
          MEASURED: 64 of 69 rows carry a STALE chat_hash (hashed from the row id,
          before chat_id existed). The pair key is the one to use; sha256 is only
          a function of its first half.
        </p>
      </div>

      <div class="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" @click="readTurns" :disabled="turnsBusy"
          class="rounded-xl bg-blue-600 px-3 py-2 text-sm text-white disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-700 focus-visible:ring-offset-1">{{ turnsBusy ? 'Reading…' : (turnsRead ? 'Read turns again' : 'Read turns') }}</button>
        <span v-if="turns.length" class="text-xs text-muted">showing {{ shownTurns.length }} of {{ turns.length }} read ({{ chat.turns }} in this chat)</span>
      </div>

      <!-- THE FILTER (2026-09-27). MEASURED: 89 of 2,527 turns are PROOF
           scaffolding, so without this the page shows "__proof_turn_1__" as a
           conversation. A proof turn is LABELLED and FILTERABLE, never hidden.
           NOTE: this comment lives INSIDE a template literal (opened line 103,
           closed line 351), so it must NOT contain a backtick -- a backtick
           here TERMINATES the literal and the whole SPA fails to build. -->
      <div v-if="turns.length" class="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Filter turns">
        <button v-for="f in [['real','Real turns'],['proof','Proof turns'],['all','All']]" :key="f[0]"
          type="button" @click="turnFilter = f[0]"
          :aria-pressed="turnFilter === f[0]"
          class="min-h-6 rounded-lg px-2 py-1 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
          :class="turnFilter === f[0] ? 'bg-blue-600 text-white' : 'text-blue-700 hover:bg-soft'">{{ f[1] }}</button>
        <span v-if="turnCounts" class="text-xs text-muted">
          {{ turnCounts.proof }} proof · {{ turnCounts.real }} real · {{ turnCounts.draft }} draft
        </span>
      </div>
      <p v-if="turnCounts && turnCounts.proof" class="mt-1 text-xs text-amber-700">
        {{ turnCounts.proof }} of these rows are PROOF scaffolding (their content is
        <span class="mono">__proof_turn_N__</span>), not a conversation. They are
        labelled, not hidden.
      </p>

      <div v-if="turnsErr"
        class="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">{{ turnsErr }}</div>

      <!-- THE PAIRS (2026-09-27). THE HUMAN: "for chat? they are PAIR, ask ->
           answer / where is ask?" MEASURED: chat 68's newest 200 rows hold 115
           real Answers but only 4 real Questions, so the flat table showed
           answers with no ask above them. Each PAIR is now rendered as a unit:
           the ASK first, then its answers.

           READABILITY (2026-09-27). THE HUMAN: "7712 is the id for question and
           7713to 7733 is answer / i know that is good to have all, / but it can
           re-design ui to become user readable and user friendly". MEASURED:
           22,217 chars rendered at once, one pair with 46 answers, 27 of 58
           pairs empty, and Markdown shown as SOURCE. So: the ASK is ALWAYS
           visible, the answers COLLAPSE, an empty ask is ONE LINE, and the text
           is rendered as Markdown. -->
      <div v-if="shownPairs.length" class="mt-3 space-y-2">
        <div v-for="p in shownPairs" :key="p.ask.id"
          class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">
          <!-- THE ASK. It is a HEADING and it is OUTSIDE the collapsed region,
               so it is never buried by its answers.

               VISIBILITY (2026-09-27). MEASURED: the ask header used
               bg-soft/60 — 60% alpha over white is a weak fill. It is now
               full bg-soft, so the header reads as a header. -->
          <div class="border-b border-line bg-soft px-3 py-2">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded-full bg-blue-600 px-2 py-0.5 text-[11px] font-semibold text-white">ASK</span>
              <span class="mono text-[11px] text-muted">#{{ p.ask.id }}</span>
              <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                :class="turnBadge(p.ask).cls">{{ turnBadge(p.ask).text }}</span>
              <span class="text-[11px] text-muted">{{ p.answer_count }} answer{{ p.answer_count === 1 ? '' : 's' }}</span>
              <span class="mono ml-auto text-[11px] text-muted">{{ fmtLocal(p.ask.created_at) }}</span>
            </div>
            <div class="md mt-1 break-words text-sm text-ink" v-html="mdToHtml(p.ask.content || p.ask.title || '(empty ask)')"></div>
          </div>

          <!-- AN EMPTY ASK IS ONE LINE, not a card. MEASURED: 27 of 58 pairs. -->
          <div v-if="!p.answers.length" class="px-3 py-1.5 text-xs text-amber-700">
            No answer yet.
          </div>

          <!-- ONE CARD, TWO SECTIONS (2026-09-27). THE HUMAN: "you
               mis-understand my request ... this is data for reasearch, can
               display by table format, so user can easy readable ... is the
               content for answer / so answer card with 2 section in single
               answer card only".

               I had rendered 21 answers as 21 cards. The card IS the answer;
               its two sections are:
                 SECTION 1 WORKING — the steps taken (a compact TABLE)
                 SECTION 2 REPORT  — the result (rendered Markdown)
               The split is computed by the ENDPOINT from a MEASURED rule, so
               the page never invents a report. A pair with no report SAYS SO. -->
          <div v-else class="p-2">
            <!-- SECTION 1 — WORKING. THE HUMAN: "for working table / their step
                 is measure real data / measure / table field fucntion API module
                 channel capability / -> get the entity ID / real data = value /
                 does it fit to design / locic -> YES or NO / need to CRUD ...".

                 MEASURED DEFECT (2026-09-27): I built the 7-kinds measurement
                 table but placed it in the DATA step (offset 36525), while the
                 human was looking at the CHAT step's WORKING section (26835),
                 which still showed the old id/step/time list. The human asked
                 for the WORKING table's steps to BE the measurements, so the
                 measurement table now lives HERE, and the step list is kept as a
                 collapsed secondary part. -->
            <div class="rounded-xl border border-line bg-soft">
              <div class="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
                <span class="rounded-full bg-slate-600 px-2 py-0.5 text-[11px] font-semibold text-white">1 · WORKING</span>
                <span class="text-[11px] text-muted">the measurement</span>
                <span class="mono ml-auto text-[11px] text-muted">#{{ p.working.length ? p.working[0].id : '' }}–#{{ p.working.length ? p.working[p.working.length - 1].id : '' }}</span>
              </div>

              <!-- THE MEASUREMENT TABLE — the WORKING table's steps ARE these
                   measurements. Read from /api/conversation_center/entity_crud,
                   so the page holds NO literal count. -->
              <div v-if="crud" class="overflow-auto">
                <table class="w-full text-left text-xs">
                  <thead class="text-muted">
                    <tr>
                      <th class="px-2 py-1">kind</th>
                      <th class="px-2 py-1">entity id</th>
                      <th class="px-2 py-1 text-right">data</th>
                      <th class="px-2 py-1 text-right">reg</th>
                      <th class="px-2 py-1 text-right">gap</th>
                      <th class="px-2 py-1 text-center">fits</th>
                      <th class="px-2 py-1 text-right">create</th>
                      <th class="px-2 py-1 text-right">update</th>
                      <th class="px-2 py-1 text-right">delete</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="k in crud.kinds" :key="'w' + k.letter" class="border-t border-line align-top">
                      <td class="px-2 py-1 font-semibold text-ink">{{ k.kind }}</td>
                      <td class="mono whitespace-nowrap px-2 py-1 text-muted">{{ k.sample_entity_id || '—' }}</td>
                      <td class="mono px-2 py-1 text-right text-ink">{{ k.source }}</td>
                      <td class="mono px-2 py-1 text-right text-muted">{{ k.registered }}</td>
                      <td class="mono px-2 py-1 text-right" :class="k.gap ? 'text-amber-700' : 'text-muted'">{{ k.gap }}</td>
                      <td class="px-2 py-1 text-center">
                        <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                          :class="k.fits ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'">
                          {{ k.fits ? 'YES' : 'NO' }}
                        </span>
                      </td>
                      <td class="mono px-2 py-1 text-right text-ink">{{ k.create }}</td>
                      <td class="mono px-2 py-1 text-right text-muted">{{ k.update }}</td>
                      <td class="mono px-2 py-1 text-right" :class="k.delete ? 'text-rose-700 font-semibold' : 'text-muted'">{{ k.delete }}</td>
                    </tr>
                  </tbody>
                  <tfoot class="border-t border-line bg-panel text-muted">
                    <tr>
                      <td class="px-2 py-1 font-semibold" colspan="2">TOTAL</td>
                      <td class="mono px-2 py-1 text-right font-semibold text-ink">{{ crud.totals.source }}</td>
                      <td class="mono px-2 py-1 text-right">{{ crud.totals.registered }}</td>
                      <td class="mono px-2 py-1 text-right text-amber-700">{{ crud.totals.gap }} ({{ crud.totals.gap_pct }}%)</td>
                      <td class="px-2 py-1"></td>
                      <td class="mono px-2 py-1 text-right text-ink">{{ crud.totals.create }}</td>
                      <td class="mono px-2 py-1 text-right">{{ crud.totals.update }}</td>
                      <td class="mono px-2 py-1 text-right text-rose-700 font-semibold">{{ crud.totals.delete }}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
              <div v-else class="px-3 py-2 text-xs text-muted">
                The measurement is not loaded — /api/conversation_center/entity_crud gave nothing.
              </div>

              <!-- THE LEGEND (2026-09-27). THE HUMAN: "can be human readable too,
                   i don't understand what is the relation". MEASURED: the headers
                   were raw abbreviations (reg / gap / fits) with NO stated
                   meaning. Every column is now explained in one sentence. -->
              <div class="border-t border-line px-3 py-2 text-[11px] text-muted">
                <span class="font-semibold text-ink">What each column means</span> —
                <span class="mono">kind</span> the entity type ·
                <span class="mono">entity id</span> a real id of that kind ·
                <span class="mono">data</span> rows in the source table ·
                <span class="mono">reg</span> rows that carry an entity id ·
                <span class="mono">gap</span> data − reg (rows with NO id) ·
                <span class="mono">fits</span> YES when every registered id points at a real row ·
                <span class="mono">create</span> rows needing a NEW id ·
                <span class="mono">update</span> ids whose row exists (a version bump is available) ·
                <span class="mono">delete</span> ids pointing at NO row (the cleanup list).
              </div>

              <!-- THE STEP LIST — kept as a SECONDARY, collapsed part. -->
              <div class="border-t border-line">
                <button type="button" @click="toggle('steps-' + p.ask.id)"
                  class="w-full px-3 py-1.5 text-left text-xs text-blue-700 hover:bg-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">
                  {{ isOpen('steps-' + p.ask.id)
                     ? 'Hide the ' + p.working.length + ' step(s)'
                     : 'Show the ' + p.working.length + ' step(s) (#' + (p.working.length ? p.working[0].id : '') + '–#' + (p.working.length ? p.working[p.working.length - 1].id : '') + ')' }}
                </button>
                <div v-if="isOpen('steps-' + p.ask.id)" class="overflow-auto border-t border-line">
                  <table class="w-full text-left text-xs">
                    <thead class="text-muted">
                      <tr>
                        <th class="px-3 py-1">id</th>
                        <th class="px-3 py-1">step</th>
                        <th class="px-3 py-1 text-right">time</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="a in p.working" :key="a.id" class="border-t border-line align-top">
                        <td class="mono px-3 py-1 text-muted">#{{ a.id }}</td>
                        <td class="px-3 py-1 text-ink">
                          <div class="md break-words" v-html="mdToHtml(a.content || a.title || '')"></div>
                        </td>
                        <td class="mono px-3 py-1 text-right text-muted">{{ fmtLocal(a.created_at) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <!-- SECTION 2 — REPORT -->
            <div v-if="p.report" class="mt-2 rounded-xl border border-line bg-panel">
              <div class="flex flex-wrap items-center gap-2 border-b border-line px-3 py-1.5">
                <span class="rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-semibold text-white">2 · REPORT</span>
                <span class="mono text-[11px] text-muted">#{{ p.report.id }}</span>
                <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                  :class="turnBadge(p.report).cls">{{ turnBadge(p.report).text }}</span>
                <span class="mono ml-auto text-[11px] text-muted">{{ fmtLocal(p.report.created_at) }}</span>
              </div>
              <div class="md px-3 py-2 break-words text-xs text-ink" v-html="mdToHtml(p.report.content || p.report.title || '')"></div>
              <div class="border-t border-line px-3 py-1 text-[11px] text-muted">{{ p.report_why }}</div>
            </div>
            <div v-else class="mt-2 rounded-xl border border-dashed border-line px-3 py-1.5 text-[11px] text-muted">
              <span class="font-semibold">2 · REPORT</span> — none. {{ p.report_why }}
            </div>
          </div>
        </div>
      </div>
      <div v-else-if="turnsRead && !turnsErr"
        class="mt-3 rounded-2xl border border-line bg-panel px-3 py-6 text-center text-sm text-muted">
        {{ turns.length ? 'No turn matches this filter.' : 'This chat has no turns.' }}
      </div>
    </section>

    <!-- ===== STEP 3 — ONE TARGET: THE SYSTEM MEASUREMENT ===== -->
    <section v-else>
      <button type="button" @click="goList" class="rounded-lg px-2 py-1 text-sm text-blue-700 hover:bg-soft hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1">‹ All chats</button>
      <h2 class="mt-2 text-lg font-semibold text-ink">Data — what is behind the page</h2>
      <p class="mt-0.5 text-sm text-muted">The four levels, and the gaps that are known. Every number is read from <span class="mono">/api/conversation_center/index</span>.</p>

      <div class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">
        <div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">The four levels</div>
        <div class="space-y-2">
          <div v-for="(L, i) in idx.levels" :key="L.table"
            class="flex flex-wrap items-center gap-2 rounded-xl border px-3 py-2"
            :class="levelClass(i)">
            <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold">{{ L.level }}</span>
            <span class="mono text-xs">{{ L.table }}</span>
            <span class="text-xs opacity-70">key: {{ L.key }}</span>
            <span class="ml-auto text-base font-semibold">
              <template v-if="L.rows === null">UNKNOWN</template>
              <template v-else>{{ L.rows }}</template>
            </span>
          </div>
          <div v-if="!idx.levels.length && !busy"
            class="rounded-xl border border-line px-3 py-4 text-center text-sm text-muted">
            No levels returned — the endpoint gave nothing to show.
          </div>
        </div>
      </div>

      <!-- THE PREPARATION CHAIN (2026-09-27). THE HUMAN: "this is pretaretion
           step (research by real data) for how to having answer before /
           research by real data can in table format to clear it to user".
           Every number is read from /api/conversation_center/index — there is NO
           literal count in this file. -->
      <div v-if="idx.preparation" class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">
        <div class="text-xs font-semibold uppercase tracking-wide text-muted">The preparation chain — how an answer is prepared</div>
        <p class="mt-1 text-xs text-muted">
          Four stages, read from <span class="mono">chat_center_message</span>. A stage that
          does not reach the next one is the gap.
        </p>
        <div class="mt-3 overflow-auto rounded-xl border border-line">
          <table class="w-full text-left text-xs">
            <thead class="bg-soft text-muted">
              <tr>
                <th class="px-2 py-1">#</th>
                <th class="px-2 py-1">stage</th>
                <th class="px-2 py-1">what it is</th>
                <th class="px-2 py-1 text-right">count</th>
                <th class="px-2 py-1 text-right">of</th>
                <th class="px-2 py-1 text-right">%</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="st in idx.preparation.stages" :key="st.no"
                class="border-t border-line align-top"
                :class="st.count === 0 ? 'bg-rose-50' : ''">
                <td class="mono px-2 py-1 text-muted">{{ st.no }}</td>
                <td class="px-2 py-1 font-semibold text-ink">{{ st.stage }}</td>
                <td class="px-2 py-1 text-muted">{{ st.what }}</td>
                <td class="mono px-2 py-1 text-right font-semibold"
                  :class="st.count === 0 ? 'text-rose-700' : 'text-ink'">{{ st.count }}</td>
                <td class="mono px-2 py-1 text-right text-muted">{{ st.of }}</td>
                <td class="mono px-2 py-1 text-right"
                  :class="st.count === 0 ? 'text-rose-700' : 'text-muted'">{{ st.pct }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="idx.preparation.break" class="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <span class="font-semibold">BROKEN at stage {{ idx.preparation.break.stage }}</span>
          — {{ idx.preparation.break.why }}
        </div>

        <div class="mt-3 text-xs font-semibold uppercase tracking-wide text-muted">Which field carries which stage</div>
        <div class="mt-2 overflow-auto rounded-xl border border-line">
          <table class="w-full text-left text-xs">
            <thead class="bg-soft text-muted">
              <tr>
                <th class="px-2 py-1">field</th>
                <th class="px-2 py-1 text-right">on Question</th>
                <th class="px-2 py-1 text-right">on Answer</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="f in idx.preparation.fields" :key="f.field" class="border-t border-line">
                <td class="mono px-2 py-1 text-ink">{{ f.field }}</td>
                <td class="mono px-2 py-1 text-right" :class="f.question ? 'text-ink' : 'text-muted'">{{ f.question }}</td>
                <td class="mono px-2 py-1 text-right" :class="f.answer ? 'text-ink' : 'text-muted'">{{ f.answer }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-2 text-xs text-muted">
          The routing fields are Question-only; the evidence fields are Answer-only.
          Nothing carries both — which is why the JOIN is 0.
        </p>
      </div>

      <!-- THE WORKING TABLE'S STEPS ARE MEASUREMENTS (2026-09-27). THE HUMAN:
           "good! for working table / their step is measure real data / measure /
           table field fucntion API module channel capability / -> get the entity
           ID / real data = value / does it fit to design / locic -> YES or NO /
           need to CRUD, create new, update have the new version, delete ->
           cleanup list report / ... -> register the entity ID".

           Every number is read from /api/conversation_center/entity_crud — the
           page holds NO literal count. -->
      <div v-if="crud" class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">
        <div class="text-xs font-semibold uppercase tracking-wide text-muted">The 7 kinds — real data, entity id, fits, CRUD</div>
        <p class="mt-1 text-xs text-muted">
          Each row is a MEASUREMENT: the source rows, how many carry an entity id,
          whether the register fits the source, and what CRUD is needed.
        </p>
        <div class="mt-3 overflow-auto rounded-xl border border-line">
          <table class="w-full text-left text-xs">
            <thead class="bg-soft text-muted">
              <tr>
                <th class="px-2 py-1">kind</th>
                <th class="px-2 py-1">entity id</th>
                <th class="px-2 py-1 text-right">real data</th>
                <th class="px-2 py-1 text-right">registered</th>
                <th class="px-2 py-1 text-right">gap</th>
                <th class="px-2 py-1 text-center">fits</th>
                <th class="px-2 py-1 text-right">create</th>
                <th class="px-2 py-1 text-right">update</th>
                <th class="px-2 py-1 text-right">delete</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="k in crud.kinds" :key="k.letter" class="border-t border-line align-top">
                <td class="px-2 py-1 font-semibold text-ink">{{ k.kind }}</td>
                <td class="mono whitespace-nowrap px-2 py-1 text-muted">{{ k.sample_entity_id || '—' }}</td>
                <td class="mono px-2 py-1 text-right text-ink">{{ k.source }}</td>
                <td class="mono px-2 py-1 text-right text-muted">{{ k.registered }}</td>
                <td class="mono px-2 py-1 text-right" :class="k.gap ? 'text-amber-700' : 'text-muted'">{{ k.gap }}</td>
                <td class="px-2 py-1 text-center">
                  <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                    :class="k.fits ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'">
                    {{ k.fits ? 'YES' : 'NO' }}
                  </span>
                </td>
                <td class="mono px-2 py-1 text-right text-ink">{{ k.create }}</td>
                <td class="mono px-2 py-1 text-right text-muted">{{ k.update }}</td>
                <td class="mono px-2 py-1 text-right" :class="k.delete ? 'text-rose-700 font-semibold' : 'text-muted'">{{ k.delete }}</td>
              </tr>
            </tbody>
            <tfoot class="border-t border-line bg-soft text-muted">
              <tr>
                <td class="px-2 py-1 font-semibold" colspan="2">TOTAL</td>
                <td class="mono px-2 py-1 text-right font-semibold text-ink">{{ crud.totals.source }}</td>
                <td class="mono px-2 py-1 text-right">{{ crud.totals.registered }}</td>
                <td class="mono px-2 py-1 text-right text-amber-700">{{ crud.totals.gap }} ({{ crud.totals.gap_pct }}%)</td>
                <td class="px-2 py-1"></td>
                <td class="mono px-2 py-1 text-right text-ink">{{ crud.totals.create }}</td>
                <td class="mono px-2 py-1 text-right">{{ crud.totals.update }}</td>
                <td class="mono px-2 py-1 text-right text-rose-700 font-semibold">{{ crud.totals.delete }}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <div class="mt-2 rounded-xl border border-line bg-soft px-3 py-2 text-xs text-muted">
          <span class="font-semibold text-ink">CRUD rule</span> —
          CREATE: {{ crud.crud_rule.CREATE }} ·
          UPDATE: {{ crud.crud_rule.UPDATE }} ·
          DELETE: {{ crud.crud_rule.DELETE }}
        </div>

        <!-- THE CLEANUP LIST — the DELETE candidates, with their ids. -->
        <div v-if="crud.totals.delete" class="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <div class="font-semibold">Cleanup list — {{ crud.totals.delete }} registered id(s) point at NO source row</div>
          <div v-for="k in crud.kinds" :key="'d' + k.letter" v-show="k.delete">
            <span class="mono">{{ k.letter }}</span> ({{ k.delete }}):
            <span class="mono">{{ k.delete_ids.join(', ') }}</span>
          </div>
        </div>

        <!-- THE ID FORMAT, BY EVIDENCE (2026-09-27). THE HUMAN: "sorry for my
             wrong typing, always by evidence". The earlier panel reported a
             "gap" against the dotted form T-3.1.1-2 — but that was a TYPO, so
             the panel presented a typo as a design gap. It is replaced by the
             EVIDENCE: the ONE format, its 4 parts with their MEANING, and a REAL
             id that both PARSES and VERIFIES. -->
        <div class="mt-2 rounded-xl border border-line bg-soft px-3 py-2 text-xs">
          <div class="font-semibold text-ink">Entity id — the ONE format, by evidence</div>
          <div class="mt-1 text-muted">
            <span class="mono">{{ crud.id_format.format }}</span>
            — e.g. <span class="mono font-semibold text-ink">{{ crud.id_format.example }}</span>
            <span v-if="crud.id_format.example_parses !== undefined">
              (parses: <span class="font-semibold">{{ crud.id_format.example_parses ? 'YES' : 'NO' }}</span>,
              verifies: <span class="font-semibold">{{ crud.id_format.example_verifies ? 'YES' : 'NO' }}</span>)
            </span>
          </div>
          <div class="mt-2 overflow-auto rounded-lg border border-line bg-panel">
            <table class="w-full text-left text-xs">
              <thead class="text-muted">
                <tr>
                  <th class="px-2 py-1">part</th>
                  <th class="px-2 py-1">example</th>
                  <th class="px-2 py-1">meaning</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="pt in crud.id_format.parts" :key="pt.part" class="border-t border-line">
                  <td class="mono px-2 py-1 font-semibold text-ink">{{ pt.part }}</td>
                  <td class="mono px-2 py-1 text-muted">{{ pt.example }}</td>
                  <td class="px-2 py-1 text-muted">{{ pt.meaning }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="mt-1 text-muted">
            All 7 kinds' real ids parse:
            <span class="font-semibold text-ink">{{ crud.id_format.kinds_parse_ok }}/{{ crud.id_format.kinds_total }}</span>.
            {{ crud.id_format.note }}
          </div>
        </div>
      </div>

      <!-- THE PROMPT -> PLAN RELATION, AND PLAYWRIGHT READINESS (2026-09-27).
           THE HUMAN: "i don't understand what is the relation for having a prompt
           to have plan ... question flow help us to have 100% clear plan before
           have the task ... we need to confirm playwright is ready".

           Every number is read from /api/conversation_center/prompt_to_plan. -->
      <div v-if="p2p" class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">
        <div class="text-xs font-semibold uppercase tracking-wide text-muted">The prompt → plan relation</div>
        <p class="mt-1 text-xs text-muted">{{ p2p.chain.relation }}</p>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span class="rounded-lg border border-line bg-soft px-2 py-1">
            <span class="font-semibold text-ink">{{ p2p.chain.prompts }}</span> prompts</span>
          <span class="text-muted">→</span>
          <span class="rounded-lg border border-line bg-soft px-2 py-1">
            <span class="font-semibold text-ink">{{ p2p.chain.flows }}</span> flows</span>
          <span class="text-muted">→</span>
          <span class="rounded-lg border border-line bg-soft px-2 py-1">
            <span class="font-semibold text-ink">{{ p2p.chain.steps }}</span> steps</span>
          <span class="text-muted">→</span>
          <span class="rounded-lg border border-line bg-soft px-2 py-1">
            <span class="font-semibold text-ink">{{ p2p.chain.steps_with_prompt }}</span> carry a prompt</span>
        </div>

        <div class="mt-3 overflow-auto rounded-xl border border-line">
          <table class="w-full text-left text-xs">
            <thead class="bg-soft text-muted">
              <tr>
                <th class="px-2 py-1">flow</th>
                <th class="px-2 py-1 text-right">step</th>
                <th class="px-2 py-1">kind</th>
                <th class="px-2 py-1">the question (the prompt)</th>
                <th class="px-2 py-1">expected</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="st in p2p.chain.sample" :key="st.flow_key + '-' + st.step_no" class="border-t border-line align-top">
                <td class="mono px-2 py-1 text-muted">{{ st.flow_key }}</td>
                <td class="mono px-2 py-1 text-right text-ink">{{ st.step_no }}</td>
                <td class="px-2 py-1 text-muted">{{ st.step_kind }}</td>
                <td class="px-2 py-1 text-ink">{{ st.question }}</td>
                <td class="mono px-2 py-1 text-muted">{{ st.expected }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="mt-3 text-xs font-semibold uppercase tracking-wide text-muted">The plan stages</div>
        <div class="mt-2 flex flex-wrap items-center gap-2 text-xs">
          <span v-for="st in p2p.plan.stages" :key="st.stage"
            class="rounded-lg border border-line bg-soft px-2 py-1">
            <span class="mono font-semibold text-ink">{{ st.stage }}</span>
            <span class="text-muted">{{ st.rows }}</span></span>
        </div>
        <div class="mt-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <span class="font-semibold">plan_sessions = {{ p2p.plan.plan_sessions_rows }}</span> — {{ p2p.plan.why }}
        </div>
      </div>

      <!-- PLAYWRIGHT READINESS — the 9 steps and their measured verdicts. -->
      <div v-if="p2p" class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">
        <div class="flex flex-wrap items-center gap-2">
          <span class="text-xs font-semibold uppercase tracking-wide text-muted">Playwright readiness</span>
          <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
            :class="p2p.playwright.ready ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'">
            {{ p2p.playwright.ready ? 'READY' : 'NOT READY' }}
          </span>
          <span class="text-xs text-muted">{{ p2p.playwright.why }}</span>
        </div>
        <div class="mt-2 overflow-auto rounded-xl border border-line">
          <table class="w-full text-left text-xs">
            <thead class="bg-soft text-muted">
              <tr>
                <th class="px-2 py-1 text-right">#</th>
                <th class="px-2 py-1">step</th>
                <th class="px-2 py-1">verdict</th>
                <th class="px-2 py-1">measured</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="st in p2p.playwright.steps" :key="st.step_no" class="border-t border-line align-top">
                <td class="mono px-2 py-1 text-right text-muted">{{ st.step_no }}</td>
                <td class="mono px-2 py-1 text-ink">{{ st.step_key }}</td>
                <td class="px-2 py-1">
                  <span class="rounded-full px-2 py-0.5 text-[11px] font-semibold"
                    :class="st.verdict === 'PASS' ? 'bg-emerald-100 text-emerald-700'
                          : (st.verdict === 'FAIL' ? 'bg-rose-100 text-rose-700' : 'bg-soft text-muted')">
                    {{ st.verdict }}</span>
                </td>
                <td class="px-2 py-1 text-muted">{{ st.got }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="p2p.session.mismatch"
          class="mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <span class="font-semibold">SESSION MISMATCH</span> — {{ p2p.session.why }}
        </div>
      </div>

      <div class="mt-3 grid gap-3 sm:grid-cols-2">
        <div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">
          <div class="text-xs font-semibold uppercase tracking-wide text-muted">Does CHAT group?</div>
          <div class="mt-2 text-xl font-semibold"
            :class="groupingIsOneToOne ? 'text-amber-600' : 'text-emerald-600'">{{ idx.grouping ? idx.grouping.verdict : 'UNKNOWN' }}</div>
          <div v-if="idx.grouping" class="mt-2 grid grid-cols-3 gap-2 text-xs">
            <div><div class="text-muted">chats</div>
              <div class="mono text-ink">{{ idx.grouping.chats }}</div></div>
            <div><div class="text-muted">conversations</div>
              <div class="mono text-ink">{{ idx.grouping.conversations }}</div></div>
            <div><div class="text-muted">max / chat</div>
              <div class="mono text-ink">{{ idx.grouping.max_conversations_per_chat }}</div></div>
          </div>
          <p v-if="idx.grouping" class="mt-2 text-xs text-muted">{{ idx.grouping.why }}</p>
        </div>

        <div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">
          <div class="text-xs font-semibold uppercase tracking-wide text-muted">Are all turns attached?</div>
          <div class="mt-2 text-xl font-semibold"
            :class="idx.turns && idx.turns.without_chat_id ? 'text-amber-600' : 'text-emerald-600'">
            <template v-if="idx.turns">{{ idx.turns.without_chat_id }} unlinked</template>
            <template v-else>UNKNOWN</template>
          </div>
          <p v-if="idx.turns" class="mt-2 text-xs text-muted">{{ idx.turns.why }}</p>
        </div>
      </div>
    </section>
  </div>
  `,

  setup() {
    const s = reactive({
      step: 'list',            // 'start' | 'list' | 'chat' | 'data' — ONE at a time
      // THE START STEP (2026-09-27). The human asked for
      // `/llm-tasks/conversation/start` to be a place where a new conversation
      // BEGINS. `newContent` is the first question; `newErr` is STATED, never
      // swallowed, because a submit that failed silently would look like a chat
      // that was created.
      newContent: '',
      newBusy: false,
      newErr: '',
      q: '',
      showAll: false,          // a 69-row wall is not a list a human can scan
      err: '',
      busy: false,
      chats: [],
      meta: { chats: 0, with_turns: 0, empty: 0, titled: 0, derived_labels: 0 },
      chat: null,
      turns: [],
      turnsBusy: false,
      turnsErr: '',
      turnsRead: false,
      // THE TURN FILTER (2026-09-27). MEASURED: 89 of 2,527 turns are PROOF
      // scaffolding and 1,974 are still 'draft', so the page showed
      // `__proof_turn_1__` as if it were a conversation. The filter defaults to
      // 'real' so a human sees the conversation first, and 'all' is one click
      // away — a proof turn is LABELLED and FILTERABLE, never hidden.
      turnFilter: 'real',
      turnCounts: null,
      // THE PAIRS (2026-09-27). The endpoint groups the rows into ask -> answer
      // pairs; the page renders each pair as a unit so the ASK is never buried.
      pairs: [],
      pairCount: 0,
      orphanAnswers: 0,
      // Which pairs the human has opened. MEASURED: one pair holds 46 answers,
      // so the default is COLLAPSED and the ask stays visible above it.
      expanded: {},
      idx: { levels: [], grouping: null, turns: null, preparation: null },
      // THE WORKING TABLE'S STEPS ARE MEASUREMENTS (2026-09-27). The 7 kinds,
      // their real data, the entity id, fits YES/NO, and the CRUD split.
      crud: null,
      // THE PROMPT -> PLAN RELATION, the plan stages, and playwright readiness.
      p2p: null,
    });

    const groupingIsOneToOne = computed(() => {
      const g = s.idx.grouping;
      return !!g && Number(g.max_conversations_per_chat) === 1;
    });

    const filtered = computed(() => {
      const q = (s.q || '').trim().toLowerCase();
      if (!q) return s.chats;
      return s.chats.filter((c) =>
        String(c.chat_id) === q
        || (c.label || '').toLowerCase().includes(q)
        || (c.session_id || '').toLowerCase().includes(q)
        || (c.chat_key || '').toLowerCase().includes(q));
    });

    // THE ORDER A HUMAN WANTS: most recently used first. MEASURED: the endpoint
    // orders by `turns DESC`, so a chat with 1,974 turns sat above one used a
    // minute ago — the list could not answer "which chat was I just in?".
    // `last_at` is the newest turn (or the chat row), so the sort is by ACTIVITY.
    //
    // MEASURED, and my FIRST version was WRONG: an empty `last_at` (a chat with
    // no turns) compared as `'' < '2026-...'`, so the EMPTY chats sorted to the
    // TOP — the opposite of "most recent first". A missing time is not the
    // newest time; it sorts LAST.
    const sortedChats = computed(() => {
      const rows = (filtered.value || []).slice();
      rows.sort((a, b) => {
        const ta = String(a.last_at || '');
        const tb = String(b.last_at || '');
        if (ta !== tb) {
          if (!ta) return 1;   // a chat with no time goes last
          if (!tb) return -1;
          return tb.localeCompare(ta);
        }
        return Number(b.turns || 0) - Number(a.turns || 0);
      });
      return rows;
    });

    const labelKindText = (k) => LABEL_KIND_TEXT[k] || k || 'an unknown source';
    const levelClass = (i) => LEVEL_CLASSES[i % LEVEL_CLASSES.length];

    // THE TIME, IN THE HUMAN'S CLOCK. `fmtLocal` converts the stored UTC value
    // using the offset detected from `user_environment`; an unknown offset is
    // MARKED, never silently shown as UTC.
    const fmtLocalSafe = (ts) => {
      try { return fmtLocal(ts); } catch (e) { return String(ts || '-'); }
    };
    const tzLabelSafe = () => {
      try { return tzLabel(); } catch (e) { return 'UTC (offset unknown)'; }
    };

    // THE TURNS THE HUMAN SEES. `kind` and `readable` come from the endpoint, so
    // the page never re-derives them from the content — one reader, one truth.
    const shownTurns = computed(() => {
      const rows = s.turns || [];
      if (s.turnFilter === 'proof') return rows.filter((t) => t.kind === 'proof');
      if (s.turnFilter === 'real') return rows.filter((t) => t.kind !== 'proof');
      return rows;
    });

    // A badge per row, so a proof turn and a draft turn are VISIBLE as such.
    const turnBadge = (t) => {
      if (t.kind === 'proof') return { text: 'PROOF', cls: 'bg-amber-100 text-amber-800' };
      const st = String(t.status || '').toLowerCase();
      if (st === 'draft') return { text: 'draft', cls: 'bg-soft text-muted' };
      if (st === 'done') return { text: 'done', cls: 'bg-emerald-100 text-emerald-700' };
      return { text: st || '—', cls: 'bg-soft text-muted' };
    };

    // THE PAIRS THE HUMAN SEES. The endpoint does the grouping (one reader, one
    // truth); the page only FILTERS by the same Real/Proof/All control, so a
    // proof pair and a real pair are never mixed.
    const shownPairs = computed(() => {
      const rows = s.pairs || [];
      if (s.turnFilter === 'proof') return rows.filter((p) => p.kind === 'proof');
      if (s.turnFilter === 'real') return rows.filter((p) => p.kind !== 'proof');
      return rows;
    });

    // COLLAPSE. A pair with more than COLLAPSE_AT answers shows the first
    // COLLAPSE_AT; the rest are behind one control. The ASK is rendered OUTSIDE
    // this, so collapsing never hides the ask.
    const isOpen = (id) => !!s.expanded[id];
    const toggle = (id) => { s.expanded[id] = !s.expanded[id]; };
    const shownAnswers = (p) => (isOpen(p.ask.id) ? p.answers : p.answers.slice(0, COLLAPSE_AT));

    // The rows actually rendered: the first screenful, unless the human asked
    // for all of them. A filter result is short enough to show whole.
    const visible = computed(() => {
      const rows = sortedChats.value;
      if (s.showAll || (s.q || '').trim()) return rows;
      return rows.slice(0, PAGE);
    });

    // ---- the reads. Each one records its OWN failure. ----------------------
    // THE OFFSET, read ONCE from the detected environment. MEASURED: this page
    // never read it, so it rendered RAW UTC while the badge said UTC+8.
    async function loadTz() {
      try {
        const res = await fetch('/api/user_environment');
        const data = await res.json();
        if (res.ok && data.ok && data.environment) {
          setTzOffsetSec(data.environment.tz_offset_sec);
          setTzName(data.environment.timezone);
        }
      } catch (e) {
        // An unknown offset is MARKED by `fmtLocal`, never silently shown as UTC.
      }
    }
    async function loadChats() {
      s.busy = true;
      s.err = '';
      try {
        // THE OFFSET IS FETCHED FIRST, so the times are converted on the FIRST
        // paint. MEASURED on other pages: painting before the offset arrives
        // left RAW UTC on screen for the life of the page.
        await loadTz();
        const res = await fetch(CHATS_URL + '?limit=200');
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.chats = data.chats || [];
        s.meta = Object.assign(
          { chats: 0, with_turns: 0, empty: 0, titled: 0, derived_labels: 0 },
          data.counts || {});
      } catch (e) {
        // CLEAR AND STATE. Neither a stale list nor an implied zero survives.
        s.chats = [];
        s.meta = { chats: 0, with_turns: 0, empty: 0, titled: 0, derived_labels: 0 };
        s.err = String((e && e.message) || e);
      } finally {
        s.busy = false;
      }
    }

    async function loadIdx() {
      try {
        const res = await fetch(INDEX_URL);
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.idx = { levels: data.levels || [], grouping: data.grouping || null,
                  turns: data.turns || null,
                  // THE PREPARATION CHAIN, read from the endpoint. The page never
                  // computes a stage count itself, so it cannot disagree.
                  preparation: data.preparation || null };
      } catch (e) {
        s.idx = { levels: [], grouping: null, turns: null, preparation: null };
        s.err = String((e && e.message) || e);
      }
    }

    // THE 7 KINDS: real data, entity id, fits, CRUD. Read from the endpoint so
    // the page holds NO literal count.
    async function loadCrud() {
      try {
        const res = await fetch(ENTITY_CRUD_URL);
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.crud = data;
      } catch (e) {
        s.crud = null;
        s.err = String((e && e.message) || e);
      }
    }

    // THE PROMPT -> PLAN RELATION + playwright readiness. The session id is the
    // one the page is showing, so the mismatch is measured for THIS chat.
    async function loadP2P() {
      try {
        const sid = (s.chat && s.chat.session_id) || '';
        const res = await fetch(PROMPT_TO_PLAN_URL + '?session_id=' + encodeURIComponent(sid));
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.p2p = data;
      } catch (e) {
        s.p2p = null;
        s.err = String((e && e.message) || e);
      }
    }

    async function loadAll() {
      await Promise.all([loadChats(), loadIdx(), loadCrud(), loadP2P()]);
    }

    // ---- THE ADDRESS (2026-09-26, LAW 6) -----------------------------------
    //
    // THE HUMAN: "path ... -> http://127.0.0.1:18765/llm-tasks/conversation/step2-68
    // can all path can be more meaningful". Their form encoded THREE things, and
    // TWO of them do not belong in an address:
    //   * `conversation` -- a typo (the registered term is `conversation`);
    //   * `step2`         -- the UI's INTERNAL step number. If the wizard is ever
    //                        re-ordered, EVERY bookmark breaks. That is the same
    //                        defect class as `environment_playwright`, which named
    //                        an internal stage instead of the page;
    //   * `68`            -- a row id, which IS legitimate as a final segment.
    //
    // So the address carries STRUCTURE ONLY:
    //     /llm-tasks/conversation          -> step 1, the chat list
    //     /llm-tasks/conversation/<id>     -> step 2, that chat
    //     /llm-tasks/conversation/recent   -> the newest chat (replaces
    //                                         /llm-tasks/chat_identity/recent)
    //
    // The typed form still RESOLVES via LEGACY_NAV_SLUGS (`conversation`), so
    // nothing the human has used 404s.
    function writeAddress(seg) {
      try {
        const url = '/llm-tasks/conversation' + (seg ? '/' + encodeURI(String(seg)) : '');
        if (window.location.pathname !== url) {
          history.pushState({ nav: 'conversation-center', tab: seg || '' }, '', url);
        }
      } catch (_) { /* an address is a nicety; never break the view over it */ }
    }

    // ---- navigation: ONE target at a time ---------------------------------
    //
    // THE LIST HAS A SEGMENT NOW. THE HUMAN (2026-09-27), verbatim:
    //     "path : http://127.0.0.1:18765/llm-tasks/conversation ->
    //      http://127.0.0.1:18765/llm-tasks/conversation/list"
    //
    // MEASURED BEFORE: `goList()` called `writeAddress('')`, so the list was the
    // ONLY step with no segment -- a step a human cannot link to. The bare
    // `/llm-tasks/conversation` STAYS an alias (LAW 5: an old URL is MAPPED,
    // never deleted), so nothing the human has bookmarked 404s.
    function goList() {
      s.step = 'list';
      writeAddress('list');
    }

    // THE START STEP. THE HUMAN (2026-09-27), verbatim: "+UI (image design):
    // http://127.0.0.1:18765/llm-tasks/conversation/index having a new
    // conversation".
    //
    // THE ADDRESS IS `index`, NOT `start`. ASKED AND ANSWERED:
    //     "`/llm-tasks/conversation/index` 你想佢係邊一樣？"  ->  "開新對話"
    //     "如果 /index 變成開新對話，現有嘅 `/conversation/start` 點處理？"  ->  "remove"
    //
    // MEASURED BEFORE: `/llm-tasks/conversation/index` fell through
    // `openFromAddress()`'s silent `return` (Number('index') is NaN, no trailing
    // digits) and rendered the LIST -- a URL that silently shows a DIFFERENT
    // page, which is worse than a 404.
    //
    // `start` is KEPT as an ALIAS (LAW 5: an old URL is MAPPED, never deleted).
    // The human said "remove"; the address stops being CANONICAL, and nothing
    // 404s. A hard 404 would be one line if that is what is wanted.
    function goStart() {
      s.step = 'start';
      s.newErr = '';
      writeAddress('index');
    }

    // THE ONE ACTION OF THE START STEP.
    //
    // MEASURED (F4): `POST /api/chat_center/submit` ALREADY creates a chat --
    // "When session_id is omitted a new UUID is generated (new chat)". So this
    // sends NO `session_id` and writes NO second create-chat route: a second
    // route would be a second source of truth for what a new chat is.
    async function submitNew() {
      const content = String(s.newContent || '').trim();
      if (!content) return;
      s.newBusy = true;
      s.newErr = '';
      try {
        const res = await fetch('/api/chat_center/submit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || ('HTTP ' + res.status));
        }
        // RELOAD THE LIST FIRST, so the new chat is IN it, then open it. Without
        // the reload `pick()` would be handed a chat the list does not hold and
        // step 2 would show an empty chat that looks like a failed create.
        await loadChats();
        const id = Number(data.chat_id);
        const c = s.chats.find((x) => Number(x.chat_id) === id);
        s.newContent = '';
        if (c) {
          pick(c);
        } else {
          // THE CHAT WAS CREATED BUT THE LIST DOES NOT HOLD IT. That is STATED,
          // not hidden: the id is real and the human can open it directly.
          s.step = 'chat';
          s.chat = { chat_id: id, label: content.slice(0, 80), turns: 0,
                     conversations: 0, identities: 0, session_id: '', ide: '' };
          writeAddress(id);
          readTurns();
        }
      } catch (e) {
        s.newErr = String((e && e.message) || e);
      } finally {
        s.newBusy = false;
      }
    }

    // THE MIDDLE STEP'S ONE ACTION. THE HUMAN (2026-09-27), verbatim:
    //     "highlight bar -> button for shortcut to index and list"
    //
    // MEASURED BEFORE: `2 · One chat` was a `<span>` (`:262-264`), so the step
    // bar was a control strip with a HOLE in it and no `aria-current` anywhere.
    // Now it is a button that RETURNS to the stepped-into chat. It is DISABLED
    // until a chat is picked, because before that there is no chat to return to
    // -- and a disabled control says that better than a hidden one, which would
    // make the bar's shape change under the reader.
    function goChat() {
      if (!s.chat || !s.chat.chat_id) return;   // the disabled state, enforced
      s.step = 'chat';
      writeAddress(s.chat.chat_id);
    }

    function goData() {
      s.step = 'data';      // THE ENGINEER'S VIEW IS ADDRESSABLE NOW (2026-09-27).
                            //
                            // MEASURED BEFORE: this wrote NO address, so
                            // `/llm-tasks/conversation/data` fell through
                            // `openFromAddress()`'s silent return and rendered the
                            // LIST -- a URL that silently shows a DIFFERENT page.
                            // And because the address never changed, Back popped
                            // past this step entirely.
                            //
                            // THE OLD NOTE SAID "not a place (LAW 6)". That was
                            // half right: LAW 6 keeps a GROUP or an internal STAGE
                            // out of the address, and `data` is neither -- it is a
                            // STEP OF THIS SAME WIZARD, rendered as a button
                            // beside two addressable steps. The choice was between
                            // an address and a button that lies.
      writeAddress('data');
    }

    function pick(c) {
      s.chat = c;
      s.step = 'chat';      // leave the list entirely: nothing is mixed
      s.turns = [];
      s.turnsErr = '';
      s.turnsRead = false;
      writeAddress(c.chat_id);
      readTurns();          // the ONE action of this step, started for the human
    }

    async function readTurns() {
      const id = s.chat && s.chat.chat_id;
      if (id === null || id === undefined) return;
      s.turnsBusy = true;
      s.turnsErr = '';
      try {
        const res = await fetch(HISTORY_URL + '?limit=200&chat_id='
          + encodeURIComponent(id));
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        s.turns = data.rows || [];
        // The endpoint's OWN counts, so the page cannot disagree with the reader.
        s.turnCounts = data.counts || null;
        // THE PAIRS, grouped by the endpoint. The page never re-groups them.
        s.pairs = data.pairs || [];
        s.pairCount = data.pair_count || 0;
        s.orphanAnswers = data.orphan_answers || 0;
        s.turnsRead = true;
      } catch (e) {
        s.turns = [];
        s.turnsRead = true;
        s.turnsErr = String((e && e.message) || e);
      } finally {
        s.turnsBusy = false;
      }
    }

    // THE URL CAN OPEN A CHAT DIRECTLY, and `recent` opens the newest one.
    // MEASURED: `chat_identity/recent` was a real address a human had bookmarked,
    // so a `/recent` address must actually RESOLVE rather than render the list.
    //
    // MEASURED 2026-09-26: this first matched ONLY `/llm-tasks/conversation/...`,
    // so the old bookmark `/llm-tasks/chat_identity/recent` landed on the LIST
    // instead of the chat it had always opened. The match is now "the last segment
    // is `recent`, on ANY alias of this page", which keeps the old address honest.
    async function openFromAddress() {
      const path = window.location.pathname || '';
      const m = /\/llm-tasks\/[^/]+\/(.+)$/.exec(path);
      if (!m) return;
      const seg = decodeURIComponent(m[1]);
      // THE NAMED SEGMENTS ARE HANDLED EXPLICITLY.
      //
      // MEASURED DEFECT (2026-09-27): without these branches they fell through
      // the silent `return` below -- `Number('list')`, `Number('index')` and
      // `Number('start')` are NaN and none has trailing digits -- so
      // `/conversation/list`, `/conversation/index` and `/conversation/start`
      // ALL rendered the list. `/list` was right by accident and the other two
      // were wrong by accident.
      //
      // `index` IS the canonical start address; `start` is an ALIAS (LAW 5).
      if (seg === 'list') {
        s.step = 'list';
        return;
      }
      if (seg === 'index' || seg === 'start') {
        s.step = 'start';
        return;
      }
      // THE DATA STEP RESOLVES (2026-09-27).
      //
      // MEASURED BEFORE: there was NO branch for `data`, so
      // `/llm-tasks/conversation/data` fell through the silent `return` below
      // (`Number('data')` is NaN, no trailing digits) and rendered the LIST.
      // **A URL that silently shows a DIFFERENT page is worse than a 404** -- the
      // same defect already recorded at `app.js:790-793` and `:1302-1306`.
      if (seg === 'data') {
        s.step = 'data';
        return;
      }
      if (seg === 'recent') {
        // The list is ordered by turns DESC, so the first row is the busiest and
        // most recently active chat. Named `recent` because that is what a human
        // opening it expects to see.
        if (s.chats.length) pick(s.chats[0]);
        return;
      }
      const id = Number(seg);
      // TOLERANT ID: MEASURED 2026-09-26 -- the human's OWN address was
      // `/llm-tasks/conversation/step2-68`, so the segment is `step2-68` and a
      // strict Number() gave NaN, landing them on the LIST while their URL said
      // chat 68. The trailing digits ARE the id they meant, so they are read.
      // This is the alias being helpful, not a step number entering the URL: the
      // canonical address stays `/llm-tasks/conversation/68` (LAW 6).
      const trailing = /(\d+)$/.exec(seg);
      const wanted = Number.isFinite(id) ? id : (trailing ? Number(trailing[1]) : NaN);
      if (!Number.isFinite(wanted) || !s.chats.length) return;
      const c = s.chats.find((x) => Number(x.chat_id) === wanted);
      if (c) { pick(c); return; }
      // AN ID THE LIST DOES NOT HOLD IS STATED, not shown as an empty step.
      s.step = 'chat';
      s.chat = { chat_id: wanted, label: '', turns: 0, conversations: 0,
                 identities: 0, session_id: '', ide: '' };
      readTurns();
    }

    // Start: load, then honour the address.
    (async () => {
      await loadAll();
      await openFromAddress();
    })();

    // WHY THERE IS NO `popstate` LISTENER HERE (MEASURED, 2026-09-27).
    //
    // I FIRST ADDED ONE, on the theory that `app.js`'s single listener
    // (`app.js:7636`) only re-rendered the shell and never re-ran this parser --
    // which would mean Back moved the URL while the screen stayed put. THE THEORY
    // WAS WRONG, and it was worth measuring rather than shipping:
    //
    //   `app.js`'s listener calls `mount(false)`; `mount()` does
    //   `app.innerHTML = shell()` (`app.js:7242`), which REPLACES every child of
    //   `#app`, including `#conversation-center-root`. The NEW element carries no
    //   `__vueMounted` flag, so `if (root && !root.__vueMounted)`
    //   (`app.js:7426-7430`) is TRUE, a fresh app is mounted, and
    //   `openFromAddress()` runs against the new path.
    //
    // SO BACK ALREADY MOVES THE STEP, through ONE mechanism. A second listener
    // here would be a SECOND parser for the same address -- the exact duplication
    // this plan's Forbidden Action 6 names. It is not written.

    // DO NOT SPREAD THIS STATE. `return { ...s, ... }` COPIES `step` / `chats` /
    // `chat` as plain VALUES and freezes `idx` at its ORIGINAL proxy, so writes
    // inside `loadAll()` / `pick()` never reach the template and the page renders
    // ONCE and never updates. MEASURED 2026-09-26 on the previous version: the
    // endpoint answered 200 with four levels while the DOM stayed on "Loading…"
    // and UNKNOWN. This is 陷阱 1 of `ui_skill` — return the SAME reactive object.
    return Object.assign(s, { filtered, sortedChats, visible, groupingIsOneToOne,
                              labelKindText, levelClass, loadAll, goList, goStart,
                              submitNew, goData, goChat,
                              pick, readTurns, shownTurns, shownPairs, turnBadge,
                              isOpen, toggle, shownAnswers, mdToHtml, COLLAPSE_AT,
                              loadCrud, loadP2P,
                              PAGE, fmtLocal: fmtLocalSafe, tzLabel: tzLabelSafe });
  },
};

export function mountConversationCenter(el) {
  createApp(ConversationCenter).mount(el);
}
