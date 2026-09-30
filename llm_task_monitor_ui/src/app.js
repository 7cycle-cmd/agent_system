import {
  createBlankRecord,
  deleteRecord,
  formatIdentityTrailer,
  listRecords,
  upsertRecord,
} from './storage.js';
import { mountChatCenter } from './chat-center.js';
import { mountChatCenterList } from './chat-center-list.js';
import { mountChatCenterSetting } from './chat-center-setting.js';
import { mountChatCenterWorkflow } from './chat-center-workflow.js';
import { mountConversationCenter } from './conversation-center.js';
import { mountCapabilityCenter } from './capability-center.js';
import { mountTicketCenter } from './ticket-center.js';
import { mountScreenWatch } from './screen-watch.js';
import { mountWorker, TABS as WORKER_TABS } from './worker.js';
import { mountIdentity, TABS as IDENTITY_TABS } from './identity.js';
import { mountPlaywright, TABS as PLAYWRIGHT_TABS } from './playwright.js';
import { mountModeSessions, TABS as MODE_SESSIONS_TABS } from './mode-sessions.js';
import { fmtLocal, setTzOffsetSec, tzOffsetSec, setTzName, tzLabel } from './timefmt.js';
import { mountTerminology, TABS as TERMINOLOGY_TABS } from './terminology.js';
import { mountGeneratorCenter } from './generator-center.js';
import { mountQuestionCenter, TABS as QUESTION_CENTER_TABS } from './question-center.js';
import { mountConsultantCenter, TABS as CONSULTANT_CENTER_TABS } from './consultant-center.js';
import { mountRegisters } from './registers.js';
import {
  destroyCharts,
  hudBarChart,
  hudCardHtml,
  fmtFull,
} from './telemetry.js';
import {
  evidenceDetailHtml,
  evidenceListHtml,
  llm100Html,
} from './evidence.js';
import {
  openclawHtml,
  openclawPill,
  openMcpToolModal,
} from './openclaw.js';

const NAV = [
  // EVERY `path` HERE IS A REGISTERED TERM_KEY. THAT IS LAW 1 of
  // LLM_TASKS.URL.AND.TERM.CONSISTENCY, and it is checked mechanically:
  //   * lowercase snake_case -- no spaces, no uppercase, no hyphens (LAW 2);
  //   * `path` EQUALS the `terminology_registry.term_key` (LAW 2/3);
  //   * NO address exists before its term is registered (LAW 4).
  //
  // MEASURED BEFORE this change: 8 of 23 paths were unlawful, including
  // `Skill Prompt SSOT`, which became `/llm-tasks/Skill%20Prompt%20SSOT` -- a
  // URL containing an encoded SPACE. That is the clearest possible proof the
  // path was never designed as an address, and the human could not SAY it to
  // anyone to debug. `environment_playwright` was both a TYPO and the WRONG
  // SUBJECT (the page is Playwright).
  //
  // THE OLD SPELLINGS ALL STAY ROUTABLE as aliases (LAW 5, see LEGACY_NAV_SLUGS).
  { id: 'task-center', label: 'Task Center', path: 'task_center' },
  { id: 'watchdog', label: 'Watchdog', path: 'watchdog' },
  { id: 'skill-ssot', label: 'Skill Prompt SSOT', path: 'skill_prompt_ssot' },
  { id: 'skill-learning', label: 'Skill Learning Center', path: 'skill_learning_center' },
  { id: 'llm-templates', label: 'Prompt Setting', path: 'prompt_setting' },
  { id: 'test-lib', label: 'Test Case Library', path: 'test_case_library' },
  { id: 'assets', label: 'Asset Registry', path: 'asset_registry' },
  { id: 'devtask-view', label: 'Dev Task View', path: 'dev_task_view', external: true },
  { id: 'tool-registry', label: 'Tool Registry 劇本庫', path: 'tool_registry' },
  // THE TWO ENTRIES BELOW BECAME ONE (2026-09-26).
  //
  // `chat-identity` and `chat-center` were TWO nav rows for ONE subject.
  // MEASURED: `chat_identity` states its SSOT as "chat_id + chat_identity_log";
  // `chat_center` states its persistence as "chat_center_message". Those key on
  // the SAME `chat_id`, and both are served by ONE capability
  // (`task_center.chat_identity`, capability_id 12811) whose module is
  // `chat_level` (module_id 25986).
  //
  // THE HUMAN: "is talking for same capabilty, with 2 ui / as design is totally
  // change to conversation module and group chat into it! / re-design UI to 1
  // index catalog".
  //
  // THE ADDRESS IS `conversation`, NOT `conversation_center` (2026-09-26).
  // The human asked for a meaningful path and named this one; the register
  // ALREADY carries the term `conversation`. MEASURED: keeping
  // `conversation_center` meant `/llm-tasks/conversation` did NOT resolve and the
  // SPA fell back to `/llm-tasks/task_center/analyze` -- a URL that silently
  // shows a DIFFERENT page is worse than a 404.
  { id: 'conversation-center', label: 'Conversation Center', path: 'conversation' },
  { id: 'worker', label: 'Worker', path: 'worker' },
  { id: 'identity', label: 'Identity', path: 'identity' },
  // The Playwright page. THE HUMAN (2026-09-25): "UI for playwright under
  // http://127.0.0.1:18765/llm-tasks/workflow/environment_playwright/".
  //
  // CORRECTED 2026-09-26: the segment is now `playwright` -- the registered term
  // and the page's ACTUAL subject. The old `environment_playwright` was BOTH a
  // TYPO (`environment`) and named the wrong thing (an environment, not the
  // Playwright page). Both old spellings stay routable as aliases.
  { id: 'playwright', label: 'Playwright', path: 'playwright' },
  // Which mode each chat is in, plus the per-conversation environment
  // checklist. Added because the APIs answered while NO UI code referenced
  // them (`grep 'api/mode/sessions'` over src/*.js returned zero).
  { id: 'mode-sessions', label: 'Mode & Env', path: 'mode_sessions' },
  // The whole site's names, registered in bounded phases, with the 7B's
  // measured performance. Added because the sweep APIs answered while NO UI
  // code referenced them.
  { id: 'terminology', label: 'Terminology', path: 'terminology' },
  { id: 'capability-center', label: 'Capability Center', path: 'capability_center' },
  { id: 'ticket-center', label: 'Ticket Center', path: 'ticket_center' },
  { id: 'screen-watch', label: 'Screen Watch', path: 'screen_watch' },
  { id: 'registers', label: 'Registers', path: 'registers' },
  { id: 'user-environment', label: 'User Environment', path: 'user_environment' },
  // Icon-only: no permanent text label (user spec). Tooltip shows "Telemetry".
  { id: 'telemetry', label: 'Telemetry', path: 'telemetry', iconOnly: true },
  // Icon-only too: the Evidence Center. Tooltip shows "Evidence Center".
  { id: 'evidence', label: 'Evidence Center', path: 'evidence', iconOnly: true },
  // OpenClaw settings. The segment is the registered term `openclaw`, so the
  // address says WHAT the page is instead of naming a settings GROUP.
  { id: 'openclaw', label: 'OpenClaw', path: 'openclaw' },
  // Generator Center: ONE template for the FIVE generators. The segment is the
  // registered term `generator` (term_id=1512), so LAW 1 holds -- the address
  // says WHAT the page is, and the term existed BEFORE this row was added.
  { id: 'generator', label: 'Generator Center', path: 'generator' },
  // Question Center: the questions a registry's own shape demands, and the two
  // directions a flow can move. The segment is the registered term `question`,
  // SINGULAR -- the human's own correction: "+ UI for
  // http://127.0.0.1:18765/llm-tasks/question".
  { id: 'question', label: 'Question Center', path: 'question' },
  // Consultant Center: the professional consultant team per industry, the
  // build-step standard, and the GitHub find. The segment is the registered
  // term `consultant`, so LAW 1 holds -- the address says WHAT the page is.
  // THE HUMAN: "＋ ui at http://127.0.0.1:18765/llm-tasks/consultant / your ui
  // team can help you, it should not be a single ui".
  { id: 'consultant', label: 'Consultant Center', path: 'consultant' },
];

// Inline SVG for the icon-only nav entries.
const NAV_ICON_TELEMETRY =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" aria-hidden="true">' +
  '<path d="M4 20h16"/><rect x="6" y="11" width="3" height="6" rx="1"/>' +
  '<rect x="11" y="7" width="3" height="10" rx="1"/>' +
  '<rect x="16" y="13" width="3" height="4" rx="1"/></svg>';

const NAV_ICON_EVIDENCE =
  '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" ' +
  'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
  '<rect x="3" y="4" width="18" height="16" rx="2"/>' +
  '<circle cx="8.5" cy="9.5" r="1.6"/>' +
  '<path d="M3.5 17l5-5 3.5 3.5L15.5 12l5 5"/></svg>';

const NAV_ICONS = {
  telemetry: NAV_ICON_TELEMETRY,
  evidence: NAV_ICON_EVIDENCE,
};

const TELEMETRY_TABS = [{ id: 'overview', label: 'Overview' }];

const TABS = [
  { id: 'analyze', label: 'Prompt Analyze' },
  { id: 'chat', label: 'Chat Box' },
  { id: 'result', label: 'Result Output' },
  { id: 'report', label: 'Task Report' },
  { id: 'queue', label: 'Task Queue' },
  { id: 'graph', label: 'System Graph Map' },
];

const SKILL_SSOT_TABS = [
  { id: 'editor', label: 'Editor' },
  { id: 'dimensions', label: 'Dimensions' },
  { id: 'factors', label: 'Factors (law table)' },
  { id: 'library', label: 'Skill Library' },
  { id: 'contracts', label: 'Contracts' },
  { id: 'lifecycle', label: 'Lifecycle' },
  { id: 'prompt-trace', label: 'Prompt Trace' },
  { id: 'proof', label: 'Proof' },
];

const SKILL_LEARNING_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'mismatches', label: 'Mismatches' },
  { id: 'lessons', label: 'Lessons' },
  { id: 'ingest', label: 'Ingest' },
  { id: 'candidates', label: 'Candidates' },
];

const WATCHDOG_TABS = [{ id: 'overview', label: 'Overview' }];

const LLM_TEMPLATE_TABS = [{ id: 'setting', label: 'Setting' }];

const TOOL_REGISTRY_TABS = [
  { id: 'list', label: '劇本 List' },
  { id: 'hotkey', label: 'Hotkey 示範' },
  { id: 'library', label: 'Library 文檔' },
  { id: 'steps', label: 'Step + Test' },
  { id: 'checklist', label: 'Permission Checklist' },
];

const USER_ENVIRONMENT_TABS = [
  { id: 'detect', label: 'Detect' },
  { id: 'presence', label: 'Presence' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'list', label: 'List' },
  { id: 'assets', label: 'Assets' },
];

// Capability Center: one flow (video-7-stage), many backends (PPT / video).
const CAPABILITY_CENTER_TABS = [
  { id: 'list', label: 'Capability List' },
  { id: 'flow', label: 'Flow Detail' },
  { id: 'interview', label: 'Interview' },
  { id: 'slide', label: 'Slide Room' },
];

// Ticket Center: a ticket traces how an ENTITY progresses. Five pages, one per
// table (ticket / ticket_center / the entity JOIN / ticket_event) plus `detail`,
// which shows ONE ticket in full and carries the "Get ticket" button.
//
// CRITICAL: this list and `ticket-center.js`'s own TABS must AGREE. This one is
// what `parseRoutePath()` validates a URL segment against, so a tab that exists
// only in the component is unreachable by URL, and a tab that exists only here
// renders nothing.
const TICKET_CENTER_TABS = [
  { id: 'tickets', label: 'Tickets' },
  { id: 'detail', label: 'Detail' },
  { id: 'services', label: 'Ticket Center' },
  { id: 'module', label: 'Module' },
  { id: 'progress', label: 'Progress' },
];

// Screen Watch: capture -> 7B analyse -> decide -> plan a dispatch.
// CRITICAL: this list and `screen-watch.js`'s own TABS must AGREE. This one is
// what `parseRoutePath()` validates a URL segment against, so a tab that exists
// only in the component is unreachable by URL, and a tab that exists only here
// renders nothing.
const SCREEN_WATCH_TABS = [
  { id: 'runs', label: 'Runs' },
  { id: 'detail', label: 'Detail' },
  { id: 'targets', label: 'Targets' },
  { id: 'control', label: 'Control' },
];

// Chat Center was previously a single view with a static label (tabsForNav -> []).
// `flow` keeps the 3-step Q/A flow; `list` adds the Discovery List, which is the
// ONLY place a script-written chat_center_message row can be seen (the flow is
// scoped to one chat). `workflow` shows the CASES — a case wraps a ticket and
// the chat it was discussed in, which neither of the other two tabs answers.
const CHAT_CENTER_TABS = [
  { id: 'flow', label: '3-step flow' },
  { id: 'list', label: 'Discovery List' },
  { id: 'workflow', label: 'Workflow' },
  { id: 'setting', label: 'Setting' },
];

// ONE tab. The page IS the index; a tab strip with a single entry would be
// decoration, but the router validates a URL segment against this list, so a
// one-entry list keeps the page's address addressable.
//
// THE ID IS `index` (2026-09-27). THE HUMAN, verbatim:
//     "where is the button to onlick ? /index and list?"
//     "navigator ? conversation center to
//      http://127.0.0.1:18765/llm-tasks/conversation/index"
// ASKED, and answered: "A (just asking) or B (change the nav to /index)?" -> "B"
//
// MEASURED BEFORE: the id was `list`, so `navPath()` emitted
// `/llm-tasks/conversation/list` and clicking the LEFT NAV wrote that address.
// MEASURED, `app.js:6458-6463`: the strip for this page is a HARD-CODED span, so
// this id is used ONLY as an address -- nothing displays it. Changing it is
// therefore an address change with no visual side effect.
//
// `/conversation/list` STILL RESOLVES: `openFromAddress()` handles `list`
// explicitly, so the list page keeps working (LAW 5: an old URL is MAPPED, never
// deleted). MEASURED, not assumed -- see the plan's finding F3.
//
// THE TRADE-OFF, RECORDED: the left nav was the ONLY entry point that reached the
// LIST from outside the page. The list is now reachable only from inside the page
// (the step bar's `1 · Chats`). That is the human's CHOICE, not a defect.
const CONVERSATION_CENTER_TABS = [{ id: 'index', label: 'Index' }];

const state = {
  nav: 'task-center',
  tab: 'analyze',
  // THE GENERATOR TAB IS A SEGMENT OF A GENERATOR'S OWN KEY, not a fixed list.
  // MEASURED 2026-09-28: `tabsForNav('generator')` returned `[]`, so
  // `/llm-tasks/generator/logic` matched NO tab and the page rendered its
  // DEFAULT -- a URL that silently shows a different page. The segment is the
  // key's LAST WORD (`logic_generator` -> `logic`), DERIVED from the registry
  // (`GET /api/generator/registry`, already publishing all 5 generators), so a
  // SIXTH generator becomes addressable with no edit here.
  generatorTab: '',
  generatorRegistry: null,
  leftOpen: true,
  rightOpen: true,
  query: '',
  catalogMode: 'server',
  selectedId: null,
  selectedServerId: null,
  draft: createBlankRecord(),
  templates: {
    rows: [],
    showAll: false,
    msg: '',
    msgOk: false,
    editId: null,
    form: {
      prompt_setting_key: '',
      name: '',
      description: '',
      catalog_id: 0,
      instruction: '',
    },
    samplePrompt: 'Verify membership REST channel.',
    previewOut: '',
  },
  status: {
    helperOk: false,
    ollamaOk: false,
    modelPresent: false,
    visionModel: '',
    visionPresent: false,
    model: '',
    computerId: '',
    computerName: '',
    baseUrl: '',
    text: 'Checking...',
    openclawOk: false,
    openclawConfigured: false,
    openclawError: '',
    openclawTools: 0,
    workerHbOk: false,
    workerHbName: '',
    workerHbAgeSec: null,
    workerHbError: '',
  },
  report: {
    tasks: [],
    totals: { count: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
    by_model: [],
    models: [],
    sources: [],
    taskCenter: [],
  },
  graph: {
    catalogs: [],
    msg: '',
    msgOk: true,
  },
  toolRegistry: {
    tab: 'list',
    tools: [],
    deliveryLog: [],
    designRules: [],
    f6Loop: [],
    hotkeys: [],
    sharedInfra: [],
    checklist: null,
    msg: '',
    msgOk: true,
  },
  userEnvironment: {
    tab: 'detect',
    env: null,
    rows: [],
    total: 0,
    msg: '',
    msgOk: true,
    assets: [],
    assetsTotal: 0,
    apps: [],
    syncMsg: '',
    syncOk: true,
    // THE TRIGGER POINT. The user: "trigger point by
    // http://127.0.0.1:18765/llm-tasks/ open at browser" / "so you can have
    // status now!". Opening the page records a visit; this tab READS it.
    presence: [],
    presenceTotal: 0,
    presenceThis: null,
    presenceThresholds: null,
  },
  capability: {
    tab: 'list',
    list: [],
    selected: null,
    msg: '',
    msgOk: true,
  },
  chatCenter: {
    tab: 'flow', // flow | list | workflow | setting
  },
  // Ticket Center's tab lives in state (not only in the component's
  // localStorage) so `parseRoutePath()` can validate the URL segment and the
  // component can be told which tab the URL asked for.
  ticketCenter: {
    tab: 'tickets',
  },
  // Screen Watch's tab lives in state (not only in the component's
  // localStorage) so `parseRoutePath()` can validate the URL segment and the
  // component can be told which tab the URL asked for.
  screenWatch: {
    tab: 'runs',
  },
  // The WORKER system and the IDENTITY system are TWO pages (user spec:
  // /llm-tasks/worker and /llm-tasks/identity). Their tabs live in state so
  // `parseRoutePath()` can validate the URL segment and the component can be
  // told which tab the URL asked for — ONE parser, not two.
  worker: {
    tab: 'list',
  },
  identity: {
    tab: 'list',
  },
  // Playwright: WHICH tab the URL asked for. THE HUMAN (2026-09-26):
  // "http://127.0.0.1:18765/llm-tasks/playwright/list +UI".
  //
  // MEASURED: the component kept its tab in its OWN `s` object, so a URL segment
  // could not reach it -- `/llm-tasks/playwright/list` resolved to the nav but
  // the page still opened on `environment`. The tab lives in `state` so
  // `parseRoutePath()` can validate the segment and the component can be told
  // which tab the URL asked for -- ONE parser, not two.
  playwright: {
    tab: 'environment',
  },
  // Mode & Env: which mode each chat is in, plus the per-conversation
  // environment checklist. `loaded` flags are absent on purpose — the component
  // declares its own state slice (idempotent initialiser) and re-fetches on
  // mount, so a remount shows fresh data instead of a stale cache.
  modeSessions: {
    tab: 'sessions',
  },
  // Terminology sweep: the component declares its own state slice (idempotent
  // initialiser) and re-fetches on mount, so a remount shows fresh data.
  terminology: {
    tab: 'sweep',
  },
  taskQueue: {
    rows: [],
    overview: null,
    filter: '',
    status: '',
    busy: false,
    msg: '',
    msgOk: true,
    loaded: false,
    preflight: null,
    preflightBusy: false,
    preflightMsg: '',
    scope: 'llm',
  },
  skillLearn: {
    tab: 'overview', // overview | mismatches | lessons | ingest | candidates
    skillKey: 'captcha_cell_detect',
    overview: null,
    mismatches: [],
    lessons: [],
    candidates: [],
    msg: '',
    msgOk: true,
    lessonText: '',
    importText: '',
    importUrl: false,
    ingestOut: '',
    busy: false,
  },
  skill: {
    tab: 'editor', // editor | dimensions | library | lifecycle | prompt-trace | proof
    skillKey: 'mouse_spot_verify',
    skillKeys: ['mouse_spot_verify'],
    version: '',
    active: null,
    versions: [],
    latestTest: null,
    casesCount: null,
    cases: [],
    ideTargets: ['Visual Studio Code', 'Cursor', 'Work Buddy', 'Codex'],
    msg: '',
    log: '(Seed / Reload / Test output)',
    timelineTaskId: '',
    lifecycleEvents: [],
    promptTraces: [],
    timelineMsg: '',
    contracts: { rows: [], selected: null, msg: '', msgOk: true },
    dimensions: {
      registry: {},
      combos: [],
      dimensionCount: 0,
      comboCount: 0,
      msg: '',
      msgOk: true,
    },
    library: { catalogs: [], count: 0, skillTotal: 0, msg: '', msgOk: true },
  },
  watchdog: {
    running: false,
    pid: null,
    helperPid: null,
    lastEvent: null,
    events: [],
    logTail: [],
    selectedId: null,
    msg: '',
    openclaw: null,
    workerHb: null,
  },
  telemetry: {
    tab: 'overview', // overview | <metric> (drill-down)
    metrics: {},     // metric key -> envelope
    order: [],       // stable render order from the server
    failed: [],
    generatedAt: '',
    loaded: false,
    msg: '',
    msgOk: true,
  },
  evidence: {
    rows: [],
    root: '',
    filter: '',
    detail: null,
    detailId: '',
    llm100: null,
    msg: '',
    loaded: false,
  },
  openclaw: {
    loaded: false,
    ok: null,
    error: '',
    settings: {},
    probe: {},
    capabilities: [],
    summary: {},
  },
};

/** UTC timestamp for draft ids: '20260919T041500'. */
function isoStamp() {
  return new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\..*$/, '')
    .replace('T', 'T')
    .slice(0, 15);
}

function $ (sel, root = document) {
  return root.querySelector(sel);
}

function toast(msg) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('opacity-0');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add('opacity-0'), 1800);
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&' + 'amp;')
    .replace(/</g, '&' + 'lt;')
    .replace(/>/g, '&' + 'gt;')
    .replace(/"/g, '&' + 'quot;');
}

function slugify(label) {
  return (
    String(label || '')
      .trim()
      .replace(/[^a-zA-Z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '') || 'home'
  );
}

// Legacy URL slugs -> current nav id. A route is a public address: an old
// bookmark must keep working, so the old slug is MAPPED rather than dropped.
// `llm_templates` (underscore) was the SPA's own emitted slug; `llm-templates`
// (hyphen) was the server route. Both are accepted.
const LEGACY_NAV_SLUGS = {
  llm_templates: 'llm-templates',
  'llm-templates': 'llm-templates',
  manager: 'llm-templates',
  // The user asked for `/llm-tasks/evidence/step1_worker` (2026-09-24). MEASURED:
  // `step1_worker` appeared in NO src/*.js, and `/evidence/<x>` accepts only
  // `EVID-*` ids and `llm-100`, so that URL fell back to the evidence list.
  //
  // It is an ALIAS, not a second page: a second page would be a second answer to
  // "where is the worker list". The ONE worker page is `/llm-tasks/worker`.
  step1_worker: 'worker',
  'step1-worker': 'worker',
  // The user then corrected the UNIT (2026-09-24):
  //   "path -> http://127.0.0.1:18765/llm-tasks/evidence/step1_environment"
  //   "worker ID -> environment_id from working_environment"
  // MEASURED: the page was a WORKER list (53 rows, one per session); the user's
  // unit is the ENVIRONMENT, and the 53 rows collapse to the environments
  // `working_environment` declares. So the page is now keyed by
  // `environment_id`, and this is its path. The misspelling `environment` is
  // kept because it is the URL the user typed; the correct spelling is accepted
  // too, so neither form 404s.
  step1_environment: 'worker',
  step1_environment: 'worker',
  'step1-environment': 'worker',
  'step1-environment': 'worker',
  // THE THREE-STEP PICK (2026-09-24). The user:
  //   "+ UI -> http://127.0.0.1:18765/llm-tasks/evidence/step2_worker"
  //   "+ UI -> http://127.0.0.1:18765/llm-tasks/evidence/step3_tools"
  // Each is an ALIAS to the ONE page, and the TAB is chosen by the slug, so the
  // URL the user typed lands on the right step rather than the list.
  step2_worker: 'worker',
  'step2-worker': 'worker',
  step3_tools: 'worker',
  'step3-tools': 'worker',
  // THE TWO CHAT PAGES BECAME ONE (2026-09-26).
  //
  // `chat_identity` and `chat_center` were TWO nav rows for ONE subject —
  // MEASURED: both key on the same `chat_id`, both are served by ONE capability
  // (`task_center.chat_identity`, capability_id 12811) in module `chat_level`
  // (module_id 25986). The human: "is talking for same capabilty, with 2 ui /
  // re-design UI to 1 index catalog".
  //
  // BOTH OLD PATHS STAY ROUTABLE. A route is a public address: a bookmark must
  // keep working, so each old slug is MAPPED to the one page rather than
  // dropped. This is the same rule `llm_templates` / `step1_worker` follow
  // above, and it is why neither URL 404s.
  chat_identity: 'conversation-center',
  'chat-identity': 'conversation-center',
  chat_center: 'conversation-center',
  'chat-center': 'conversation-center',
  conversation_center: 'conversation-center',
  'conversation-center': 'conversation-center',
  // THE MEANINGFUL-URL RENAME (2026-09-26). 8 nav paths became their registered
  // term_key (LAW 1). MEASURED: the old `Skill Prompt SSOT` became
  // `/llm-tasks/Skill%20Prompt%20SSOT` — a URL with an encoded SPACE that the
  // human could not SAY to anyone. These entries are why NO old bookmark 404s
  // (LAW 5: a route is a public address, and a bookmark is not the user's mistake).
  //
  // The UPCERCASE spellings do NOT strictly need an entry (slugify() is
  // case-preserving, so `Skill_Learning_Center` still matches the label), but they
  // are listed EXPLICITLY: a rename should be readable in one place, not depend on
  // a subtlety of a helper 500 lines away.
  'Skill Prompt SSOT': 'skill-ssot',
  skill_prompt_ssot: 'skill-ssot',
  Skill_Learning_Center: 'skill-learning',
  'Skill Learning Center': 'skill-learning',
  Test_Case_Library: 'test-lib',
  'Test Case Library': 'test-lib',
  Asset_Registry: 'assets',
  'Asset Registry': 'assets',
  devtask_view: 'devtask-view',
  'dev-task-view': 'devtask-view',
  'devtask-view': 'devtask-view',
  // Playwright. The wrong spelling `enviornment` was DELETED 2026-09-28 (plan
  // UNIFIED.NAME.NO.TYPO, R-2): a wrong name must not resolve, so it is NOT a
  // nav alias any more. `environment` and `environment_playwright` are the
  // correct spellings and both stay routable.
  environment_playwright: 'playwright',
  environment: 'playwright',
  // OpenClaw used to be a two-segment `/llm-tasks/setting/openclaw`.
  Setting_OpenClaw: 'openclaw',
  setting_openclaw: 'openclaw',
  // THE HUMAN'S OWN SPELLING (2026-09-26): they wrote
  // `/llm-tasks/conversation/step2-68` (a typo of `conversation` plus an
  // INTERNAL step number). It is NOT adopted as the canonical address -- a step
  // number in a public URL breaks every bookmark when the wizard is re-ordered
  // (law 6) -- but it RESOLVES, so the URL the human has actually typed works.
  // The canonical conversation address is a pending decision, see plan §4 / §10.
  conversation: 'conversation-center',
  conversations: 'conversation-center',
};

function tabsForNav(navId) {
  if (navId === 'task-center') return TABS;
  if (navId === 'skill-ssot') return SKILL_SSOT_TABS;
  if (navId === 'skill-learning') return SKILL_LEARNING_TABS;
  if (navId === 'watchdog') return WATCHDOG_TABS;
  if (navId === 'llm-templates') return LLM_TEMPLATE_TABS;
  if (navId === 'tool-registry') return TOOL_REGISTRY_TABS;
  if (navId === 'conversation-center') return CONVERSATION_CENTER_TABS;
  if (navId === 'user-environment') return USER_ENVIRONMENT_TABS;
  if (navId === 'capability-center') return CAPABILITY_CENTER_TABS;
  if (navId === 'ticket-center') return TICKET_CENTER_TABS;
  if (navId === 'screen-watch') return SCREEN_WATCH_TABS;
  if (navId === 'worker') return WORKER_TABS;
  if (navId === 'identity') return IDENTITY_TABS;
  if (navId === 'playwright') return PLAYWRIGHT_TABS;
  if (navId === 'mode-sessions') return MODE_SESSIONS_TABS;
  if (navId === 'terminology') return TERMINOLOGY_TABS;
  if (navId === 'question') return QUESTION_CENTER_TABS;
  if (navId === 'consultant') return CONSULTANT_CENTER_TABS;
  if (navId === 'registers') return [{ id: 'chain', label: 'Chain' }];
  if (navId === 'telemetry') {
    // Drill-down routes are per-metric, so the tab list is dynamic. Built from
    // state when loaded; falls back to the static key list for deep links that
    // arrive before /api/telemetry/summary has resolved.
    const keys = state.telemetry?.order?.length
      ? state.telemetry.order
      : Object.keys(TELE_METRIC_LABEL);
    return [
      ...TELEMETRY_TABS,
      ...keys
        .filter((k) => k !== 'overview')
        .map((k) => ({ id: k, label: TELE_METRIC_LABEL[k] || k })),
    ];
  }
  if (navId === 'evidence') {
    // The evidence detail route IS the tab, so a deep link to one record
    // resolves without any extra machinery.
    //
    // CRITICAL: the tab list must accept ANY evidence id, not just the one
    // currently in state. parseRoutePath() validates the URL segment against
    // this list, so returning only ['list'] on a cold load made every deep link
    // fall back to the list — the URL said /evidence/<id> while the page showed
    // the list. Accept the segment as-is when it looks like an evidence id.
    const tabs = [{ id: 'list', label: 'List' }];
    if (state.evidence?.detailId) {
      tabs.push({ id: state.evidence.detailId, label: 'Record' });
    }
    tabs.push({ id: 'LLM 100', label: 'LLM 100' });
    return tabs;
  }
  if (navId === 'openclaw') return [{ id: 'openclaw', label: 'OpenClaw' }];
  if (navId === 'chat-center') return CHAT_CENTER_TABS;
  if (navId === 'generator') return generatorTabs();
  return [];
}

// THE GENERATOR TABS ARE DERIVED FROM THE REGISTRY, NEVER LISTED.
//
// `Run` is the wizard itself. Every other tab is ONE generator, addressed by its
// DISTINGUISHING WORD -- the key with the shared `_generator` suffix removed
// (`logic_generator` -> `logic`, `prompt_generator` -> `prompt`) -- because the
// key is the registered term and a URL segment is a term (`/memories/repo/
// llmtasks_url_naming_law.md`).
//
// MEASURED 2026-09-28, and it corrected my FIRST attempt: taking the key's LAST
// word gave `generator` for ALL FIVE keys (`logic_generator`, `prompt_generator`,
// `factor_generator`, `skill_factor_generator`, `terminology_generator`), so the
// segment had NO discriminating power -- five tabs with one id. The suffix is
// the word they SHARE, so the segment is what remains after removing it, and
// the suffix is READ from the keys (`prefixOf`) rather than assumed: if a sixth
// generator does not carry it, its key is the segment unchanged.
function generatorSeg(key) {
  const k = String(key || '');
  const gens = (state.generatorRegistry && state.generatorRegistry.generators) || [];
  const suffix = commonSuffix(gens.map((g) => String(g.key || '')));
  if (suffix && k.endsWith(suffix) && k.length > suffix.length) {
    return k.slice(0, k.length - suffix.length).replace(/_+$/, '');
  }
  return k;
}

function commonSuffix(keys) {
  if (keys.length < 2) return '';
  const parts = keys.map((k) => k.split('_'));
  const last = parts[0][parts[0].length - 1];
  if (!parts.every((p) => p[p.length - 1] === last)) return '';
  return '_' + last;
}

function generatorTabs() {
  const gens = (state.generatorRegistry && state.generatorRegistry.generators) || [];
  const tabs = [{ id: 'run', label: 'Run' }];
  for (const g of gens) {
    const seg = generatorSeg(g.key);
    if (!seg) continue;
    tabs.push({ id: seg, label: g.label || g.key, generator_key: g.key });
  }
  return tabs;
}

function generatorKeyForTab(tabId) {
  const t = generatorTabs().find((x) => x.id === tabId);
  if (t && t.generator_key) return t.generator_key;
  // COLD LOAD: the registry has not resolved yet, so the tab list is only `Run`
  // and the segment cannot be matched here. The segment IS still a generator's
  // last word, so it is accepted as-is (the same shape-acceptance the evidence
  // deep link uses) and the wizard resolves it once the registry arrives.
  return '';
}

function currentTabId(navId) {
  if (navId === 'task-center') return state.tab;
  if (navId === 'skill-ssot') return state.skill?.tab || 'editor';
  if (navId === 'skill-learning') return state.skillLearn?.tab || 'overview';
  if (navId === 'watchdog') return 'overview';
  if (navId === 'llm-templates') return 'setting';
  if (navId === 'tool-registry') return state.toolRegistry?.tab || 'list';
  if (navId === 'conversation-center') return 'index';
  if (navId === 'user-environment') return state.userEnvironment?.tab || 'detect';
  if (navId === 'capability-center') return state.capability?.tab || 'list';
  if (navId === 'ticket-center') return state.ticketCenter?.tab || 'tickets';
  if (navId === 'screen-watch') return state.screenWatch?.tab || 'runs';
  if (navId === 'worker') return state.worker?.tab || 'list';
  if (navId === 'identity') return state.identity?.tab || 'list';
  if (navId === 'mode-sessions') return state.modeSessions?.tab || 'sessions';
  if (navId === 'terminology') return state.terminology?.tab || 'sweep';
  if (navId === 'registers') return 'chain';
  if (navId === 'telemetry') return state.telemetry?.tab || 'overview';
  if (navId === 'evidence') return state.evidence?.detailId || 'list';
  if (navId === 'openclaw') return 'openclaw';
  if (navId === 'chat-center') return state.chatCenter?.tab || 'flow';
  if (navId === 'generator') return state.generatorTab || 'run';
  return null;
}

function setTabId(navId, tabId) {
  if (navId === 'task-center') state.tab = tabId;
  else if (navId === 'skill-ssot') state.skill.tab = tabId;
  else if (navId === 'skill-learning') state.skillLearn.tab = tabId;
  else if (navId === 'tool-registry') state.toolRegistry.tab = tabId;
  else if (navId === 'conversation-center') { /* one tab: nothing to set */ }
  else if (navId === 'user-environment') state.userEnvironment.tab = tabId;
  else if (navId === 'capability-center') state.capability.tab = tabId;
  else if (navId === 'ticket-center') state.ticketCenter.tab = tabId || 'tickets';
  else if (navId === 'screen-watch') state.screenWatch.tab = tabId || 'runs';
  else if (navId === 'worker') state.worker.tab = tabId || 'list';
  else if (navId === 'identity') state.identity.tab = tabId || 'list';
  else if (navId === 'playwright') state.playwright.tab = tabId || 'environment';
  else if (navId === 'mode-sessions') state.modeSessions.tab = tabId || 'sessions';
  else if (navId === 'terminology') state.terminology.tab = tabId || 'sweep';
  else if (navId === 'telemetry') state.telemetry.tab = tabId;
  else if (navId === 'evidence') state.evidence.detailId = (tabId === 'list' ? '' : tabId);
  else if (navId === 'chat-center') state.chatCenter.tab = tabId || 'flow';
  else if (navId === 'generator') state.generatorTab = tabId || 'run';
}

function navPath(navId, tabId) {
  // THE TWO SPECIAL CASES ARE GONE (2026-09-26, LAW 1/2/6).
  //
  // `openclaw` used to emit `/llm-tasks/setting/openclaw` and `playwright` used to
  // emit `/llm-tasks/playwright/environment`. Both were TWO-SEGMENT addresses, and
  // the second segment described a GROUP or an INTERNAL STAGE rather than the page:
  // a group is not stable (a setting can move), and `environment` was a TYPO that
  // also named the wrong subject.
  //
  // Now each page has ONE segment equal to its registered term, so the generic
  // builder below is correct for every entry and no special case is needed.
  //
  // BOTH OLD FORMS STILL RESOLVE (LAW 5): `setting/openclaw`,
  // `playwright/environment`, `workflow/environment_playwright` and
  // `workflow/playwright` are handled in `parseRoutePath()` as aliases. No
  // bookmark 404s.
  const item = NAV.find((n) => n.id === navId);
  const slug = (item && item.path) || (item && slugify(item.label)) || slugify(navId);
  let p = '/llm-tasks/' + encodeURI(slug);
  const t = tabsForNav(navId).find((x) => x.id === tabId);
  // THE TAB SEGMENT IS THE REGISTERED TERM, NOT THE KEY.
  //
  // THE HUMAN (2026-09-27), verbatim: "path need to update too" / "example
  // Environment onclick = http://127.0.0.1:18765/llm-tasks/playwright/environment
  // (all in lower case)".
  //
  // MEASURED BEFORE: this emitted `t.id`, so 4 of the 6 Playwright URLs
  // disagreed with the register -- `/llm-tasks/playwright/steps` (id) instead of
  // `.../step` (term). The URL law
  // (`/memories/repo/llmtasks_url_naming_law.md:4`) says the segment IS the
  // registered `term_key`.
  //
  // `t.slug || t.id` IS SCOPED BY CONSTRUCTION, NOT BY A LIST: only the
  // Playwright tabs carry a `slug`, so every other page's URL is byte-identical.
  if (t) p += '/' + encodeURI(t.slug || t.id);
  return p;
}

function parseRoutePath() {
  const path = window.location.pathname || '';
  const prefix = '/llm-tasks';
  if (!path.startsWith(prefix)) return null;
  let rest = path.slice(prefix.length).replace(/^\/+/, '');
  if (!rest) return null;
  try {
    rest = decodeURIComponent(rest);
  } catch (_) {}
  const segs = rest.split('/').filter(Boolean);
  // Two-segment routes FIRST, because "setting" alone is not a NAV entry.
  if (segs[0] === 'setting' && segs[1]) {
    const two = NAV.find(
      (n) => n.id === segs[1] || n.path === segs[1] || slugify(n.label) === segs[1]
    );
    if (two) return { nav: two.id, tab: two.id };
  }
  // `/llm-tasks/playwright/environment` is the PRIMARY URL (user spec,
  // 2026-09-25). MEASURED: the first path was
  // `/llm-tasks/workflow/environment_playwright/`, which MIXED two concepts --
  // `workflow` and `environment_playwright` -- in one path. The human's
  // correction: "mixed / be /llm-tasks/playwright/environment" and
  // "/llm-tasks/workflow/playwright/".
  //
  // ALL THREE FORMS RESOLVE. The old path is an ALIAS, not a 404: a URL that
  // stops working is a defect, and a bookmark is not the user's mistake.
  if (segs[0] === 'playwright' && segs[1]) {
    const PW_ENV_SLUGS = ['environment'];
    if (PW_ENV_SLUGS.includes(segs[1])) {
      // MEASURED DEFECT (2026-09-27): this returned `tab: 'playwright'`, which is
      // NOT one of the 6 tab ids (`environment|workflow|steps|coords|
      // coordsession|list`). `setTabId()` wrote `state.playwright.tab =
      // 'playwright'`, `render()` fell through every `else if` to the final
      // `else`, and the environment table appeared. **It worked by accident, not
      // by match.** A route that names a tab that does not exist is a route that
      // works by accident.
      return { nav: 'playwright', tab: 'environment' };
    }
    // `/llm-tasks/playwright/value` IS THE `page_key`, NOT A TERM.
    //
    // MEASURED (2026-09-27): `ui_element_registry` has exactly two page keys --
    // `playwright.value` (6 rows) and `user_environment.sessions` (18 rows).
    // `value` is not a tab id, not a label, and not a `term_key`. The human was
    // LOOKING AT this URL (their own words: "path
    // http://127.0.0.1:18765/llm-tasks/playwright/value"), and it matched no tab,
    // so it rendered the default -- a URL that silently shows a DIFFERENT page.
    //
    // It is MAPPED to the page root (LAW 5: an old URL is mapped, never deleted).
    // Renaming the `page_key` itself is a register-naming question and is OUT OF
    // SCOPE for this plan.
    if (segs[1] === 'value') {
      return { nav: 'playwright', tab: 'environment' };
    }
    // `/llm-tasks/playwright/list` -- WHICH RUNS ARE RUNNING, AND WHY.
    // THE HUMAN (2026-09-26): "http://127.0.0.1:18765/llm-tasks/playwright/list
    // +UI / show me which playwright and running and why, with stop button".
    //
    // MEASURED: without this branch the URL fell through to the generic builder,
    // which found no tab named `list` and landed on the FIRST tab -- a URL that
    // silently shows a DIFFERENT page is worse than a 404.
    if (segs[1] === 'list') {
      return { nav: 'playwright', tab: 'list' };
    }
  }
  if (segs[0] === 'workflow' && segs[1]) {
    const PW_SLUGS = ['playwright', 'environment_playwright'];
    if (PW_SLUGS.includes(segs[1])) {
      return { nav: 'playwright', tab: 'playwright' };
    }
  }
  // `/evidence/step1_worker` is the URL the user asked for (2026-09-24).
  // MEASURED: `step1_worker` appeared in NO src/*.js, and the `/evidence/<x>`
  // branch accepts only `EVID-*` ids and `llm-100`, so that URL fell back to the
  // evidence LIST. It is an ALIAS to the ONE worker page, not a second page.
  //
  // THE STEP SLUG ALSO CHOOSES THE TAB (2026-09-24). The user asked for
  // `/evidence/step2_worker` and `/evidence/step3_tools`; landing on the step-1
  // list would make the URL a lie, so the slug maps to its step.
  const STEP_TAB_BY_SLUG = {
    step1_worker: 'list', 'step1-worker': 'list',
    step1_environment: 'list', step1_environment: 'list',
    'step1-environment': 'list', 'step1-environment': 'list',
    step2_worker: 'step2', 'step2-worker': 'step2',
    step3_tools: 'step3', 'step3-tools': 'step3',
  };
  if (segs[0] === 'evidence' && segs[1] && LEGACY_NAV_SLUGS[segs[1]]) {
    const target = LEGACY_NAV_SLUGS[segs[1]];
    const nav = NAV.find((n) => n.id === target || n.path === target);
    if (nav) {
      const stepTab = STEP_TAB_BY_SLUG[segs[1]];
      return { nav: nav.id, tab: stepTab || nav.id };
    }
  }
  // Legacy slugs resolve to the current nav id, so an old bookmark still works.
  const legacyNav = LEGACY_NAV_SLUGS[segs[0]];
  const targetSlug = legacyNav || segs[0];
  const found = NAV.find(
    (n) =>
      n.path === targetSlug ||
      n.id === targetSlug ||
      slugify(n.label) === targetSlug ||
      slugify(n.path || '') === targetSlug
  );
  if (!found) return null;
  let tab = null;
  if (segs[1]) {
    // A legacy tab id (e.g. 'manager') maps to the current one.
    const tabSlug = LEGACY_NAV_SLUGS[segs[1]] ? segs[1] : segs[1];
    // THE TAB SEGMENT MAY BE THE SLUG, THE ID, OR THE LABEL.
    //
    // MEASURED DEFECT (2026-09-27): this matched only `x.id === tabSlug ||
    // slugify(x.label) === tabSlug`, and `slugify()` is CASE-PRESERVING
    // (`app.js:515-522`), so `slugify('Step') === 'Step' !== 'step'`. For
    // `/llm-tasks/playwright/step` BOTH sides missed, `tab` stayed `null`, and
    // the page rendered the DEFAULT tab -- **the URL said `step` while the page
    // showed Environment.** A URL that silently shows a different page is worse
    // than a 404 (the same defect already recorded at `app.js:790-793`).
    //
    // ALL THREE FORMS RESOLVE (LAW 5): the slug is the canonical address, the id
    // is the pre-2026-09-27 address, and the label is what a human might type.
    // A bookmark is not the user's mistake.
    const t = tabsForNav(found.id).find(
      (x) => x.slug === tabSlug || x.id === tabSlug ||
             slugify(x.label) === tabSlug ||
             slugify(x.label).toLowerCase() === tabSlug
    );
    if (t) tab = t.id;
    // Evidence detail ids are DATA, not a fixed tab list. On a cold load
    // state.evidence.detailId is empty, so tabsForNav() cannot contain the id
    // and the deep link silently fell back to the list — the URL said
    // /evidence/<id> while the page rendered the list. Accept the segment when
    // it has the evidence-id shape.
    else if (found.id === 'evidence' && /^EVID-/i.test(segs[1])) {
      tab = segs[1];
    }
    // LLM 100 proof is a fixed pseudo-record, not an EVID- folder.
    else if (found.id === 'evidence' && /^llm[\s_-]*100$/i.test(segs[1])) {
      tab = 'LLM 100';
    }
    // A GENERATOR SEGMENT IS A GENERATOR'S LAST WORD, which is DATA, not a fixed
    // tab list. On a cold load the registry has not resolved, so
    // `tabsForNav('generator')` is only `Run` and the segment matches nothing --
    // the URL would silently render the wizard's default generator. The segment
    // is accepted when it has the shape of a key's last word, exactly as an
    // EVID- id is accepted above.
    else if (found.id === 'generator' && /^[a-z][a-z0-9]*$/.test(segs[1])) {
      tab = segs[1];
    }
  }
  return { nav: found.id, tab };
}

function fmtDuration(startedAt, endedAt, durationMs) {
  if (durationMs != null && !Number.isNaN(Number(durationMs))) {
    const ms = Number(durationMs);
    if (ms < 1000) return ms + ' ms';
    return (ms / 1000).toFixed(1) + ' s';
  }
  if (!startedAt || !endedAt) return '-';
  try {
    const ms = new Date(endedAt).getTime() - new Date(startedAt).getTime();
    if (Number.isNaN(ms)) return '-';
    if (ms < 1000) return ms + ' ms';
    return (ms / 1000).toFixed(1) + ' s';
  } catch {
    return '-';
  }
}

function filteredLocal() {
  const q = state.query.trim().toLowerCase();
  const all = listRecords();
  if (!q) return all;
  return all.filter((r) =>
    [r.id, r.name, r.task_id, r.writer, r.session_id, r.status, r.prompt_content]
      .join(' ')
      .toLowerCase()
      .includes(q)
  );
}

function filteredServer() {
  const q = state.query.trim().toLowerCase();
  const all = state.report.tasks || [];
  if (!q) return all;
  return all.filter((t) =>
    [t.id, t.task, t.model, t.model_label, t.status, t.result, t.writer, t.task_id, t.session_id, t.reason, t.target_name]
      .join(' ')
      .toLowerCase()
      .includes(q)
  );
}

function readFormIntoDraft() {
  state.draft = {
    ...state.draft,
    task_id: $('#f-task-id')?.value || '',
    writer: $('#f-writer')?.value || '',
    session_id: $('#f-session')?.value || '',
    context: $('#f-context')?.value || '',
    prompt_content: $('#f-prompt')?.value || '',
    result_content: $('#f-result')?.value || '',
    status: $('#f-status')?.value || state.draft.status || 'draft',
    model: $('#f-model')?.value || state.draft.model || '',
  };
}

function fillForm(rec, animate = true) {
  state.draft = createBlankRecord(rec);
  state.selectedId = rec.id;
  state.selectedServerId = null;
  const box = $('#workspace-body');
  if (box && animate) {
    box.classList.remove('fade-swap');
    void box.offsetWidth;
    box.classList.add('fade-swap');
  }
  if ($('#f-task-id')) $('#f-task-id').value = rec.task_id || '';
  if ($('#f-writer')) $('#f-writer').value = rec.writer || '';
  if ($('#f-session')) $('#f-session').value = rec.session_id || '';
  if ($('#f-context')) $('#f-context').value = rec.context || '';
  if ($('#f-prompt')) $('#f-prompt').value = rec.prompt_content || '';
  if ($('#f-result')) $('#f-result').value = rec.result_content || '';
  if ($('#f-status')) $('#f-status').value = rec.status || 'draft';
  renderRightList();
  renderStatusPills();
}

function loadServerTaskIntoWorkspace(task) {
  const rec = createBlankRecord({
    id: state.draft.id || undefined,
    task_id: String(task.task_id || task.id || ''),
    writer: task.writer || '',
    session_id: task.session_id || '',
    context: [task.task, task.target_name, task.model_label || task.model].filter(Boolean).join(' | '),
    prompt_content: state.draft.prompt_content || '',
    result_content: task.reason || task.error || task.result || '',
    status: String(task.status || task.result || 'done').toLowerCase(),
    name: task.task || task.id || 'Server task',
  });
  state.selectedServerId = task.id;
  state.selectedId = null;
  state.catalogMode = 'server';
  state.tab = 'report';
  state.draft = rec;
  mount(false);
  if ($('#f-result')) $('#f-result').value = rec.result_content || '';
  toast('Loaded ' + task.id);
}

function pill(ok, onLabel, offLabel) {
  if (ok) {
    return '<span class="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-100">' + onLabel + '</span>';
  }
  return '<span class="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700 ring-1 ring-rose-100">' + offLabel + '</span>';
}

function renderStatusPills() {
  const host = $('#status-pills');
  if (!host) return;
  const s = state.status;
  const wd = state.watchdog || {};
  const last = wd.lastEvent || {};
  const alertish = String(last.level || '') === 'alert' || String(last.kind || '').includes('down') || String(last.kind || '').includes('failed');
  host.innerHTML = [
    pill(s.helperOk, 'mouse_spot_helper ON', 'mouse_spot_helper OFF'),
    pill(!!wd.running, 'Watchdog ON', 'Watchdog OFF'),
    pill(s.ollamaOk, 'LLM ON', 'LLM OFF'),
    // Prefer the LIVE probe over /api/system-status: status only tells us the
    // endpoint answered, not that tools work. The probe distinguishes
    // "server down" from "server up but a capability failing".
    openclawPill(state, esc) ||
      pill(
        !!s.openclawOk,
        'OpenClaw MCP ON',
        s.openclawConfigured ? 'OpenClaw MCP OFF' : 'OpenClaw MCP n/a'
      ),
    pill(!!s.workerHbOk, 'Worker HB ON', 'Worker HB OFF'),
    s.modelPresent
      ? '<span class="inline-flex items-center rounded-full bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent" title="TEXT model — used by terminology_sweep and worker_engine">' + esc(s.model || 'model') + '</span>'
      : '<span class="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">Model missing</span>',
    // The VISION model is a SEPARATE fact from the text model, so it is a
    // SEPARATE badge. Showing only one of them named a model that half the
    // repo does not use.
    s.visionPresent
      ? '<span class="inline-flex items-center rounded-full bg-soft px-2.5 py-1 text-xs font-medium text-muted" title="VISION model — used by evidence-classify / browser_task_runner; NOT used for text">' + esc(s.visionModel || 'vision') + '</span>'
      : '',
    alertish && last.message
      ? '<span class="inline-flex max-w-[280px] items-center truncate rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700 ring-1 ring-rose-100" title="' +
        esc(last.message) +
        '">! ' +
        esc(last.kind || 'alert') +
        '</span>'
      : '',
    s.computerId
      ? '<span class="hidden sm:inline-flex items-center rounded-full bg-soft px-2.5 py-1 text-xs font-medium text-muted">' + esc(s.computerId) + '</span>'
      : '',
  ].join('');
  const line = $('#status-line');
  if (line) line.textContent = s.text;
  // The pills are injected here, AFTER bind() ran, so their nav buttons need
  // their handlers attached now (the OpenClaw pill navigates on click).
  bindNavButtons();
}

// Country code -> flag emoji (regional indicator symbols). e.g. HK -> 🇭🇰
// NOTE: Windows does NOT ship flag emoji glyphs (renders as "HK" letters),
// so the badge uses a real flag IMAGE from flagcdn.com instead.
function flagEmoji(cc) {
  const code = String(cc || '').trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(code)) return '';
  return String.fromCodePoint(
    ...[...code].map((c) => 0x1f1e6 + (c.charCodeAt(0) - 65))
  );
}

// Real flag image (Windows-safe). Falls back to a globe glyph if it fails.
function flagImg(cc, alt) {
  const code = String(cc || '').trim().toLowerCase();
  if (!/^[a-z]{2}$/.test(code)) return '<span class="text-base leading-none">🌐</span>';
  return (
    '<img src="https://flagcdn.com/24x18/' + code + '.png" ' +
    'srcset="https://flagcdn.com/48x36/' + code + '.png 2x" ' +
    'width="24" height="18" alt="' + esc(alt || code.toUpperCase()) + '" ' +
    'class="inline-block rounded-[2px] shadow-sm" loading="lazy" ' +
    'onerror="this.outerHTML=\'<span class=&quot;text-base leading-none&quot;>🌐</span>\'" />'
  );
}

// Top-right environment badge: country flag IMAGE + timezone + refresh.
function renderEnvBadge() {
  const host = $('#env-badge');
  if (!host) return;
  const e = (state.userEnvironment && state.userEnvironment.env) || null;
  if (!e) {
    host.innerHTML =
      '<button id="btn-env-refresh" type="button" title="Detect my environment" ' +
      'class="rounded-lg border border-line bg-panel px-2.5 py-1.5 text-sm text-muted hover:bg-soft">🌐</button>';
    return;
  }
  const flag = flagImg(e.country_code, e.country);
  const off = e.tz_offset_sec;
  const utc = off == null ? '' : 'UTC' + (off >= 0 ? '+' : '') + (off / 3600);
  const tz = e.timezone || '';
  const title =
    (e.computer_name || '') + ' · ' + (e.ip_address || '') + ' · ' +
    (e.city || '') + ', ' + (e.country || '') + ' · ' + tz + ' (' + utc + ')';
  host.innerHTML =
    '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-line bg-panel px-2.5 py-1 text-xs font-medium text-ink shadow-panel" title="' +
    esc(title) + '">' +
    flag +
    '<span class="mono">' + esc(tz || '-') + '</span>' +
    (utc ? '<span class="text-muted">' + esc(utc) + '</span>' : '') +
    '</span>' +
    '<button id="btn-env-refresh" type="button" title="Refresh environment" ' +
    'class="shrink-0 rounded-lg border border-line bg-panel px-2 py-1.5 text-sm text-muted hover:bg-soft">⟳</button>';
}

// Auto-detect on enter: fetch env once (cached in state), then render badge.
//
// THE SECOND HALF OF THE "2 TIMEZONES" DEFECT (2026-09-25). THE HUMAN:
//     "will have 2 timezone in my eye, it will make user confuse"
//
// MEASURED: this function set the offset and re-rendered ONLY the badge. The
// BODY was never re-rendered, and `refreshAll()` does not re-render either
// (measured: it only assigns into `state`). So the timestamps painted BEFORE
// the offset arrived stayed RAW UTC for the life of the page — the badge said
// `Asia/Hong_Kong UTC+8` while the table beside it showed UTC. TWO CLOCKS.
//
// The fix: when the offset CHANGES from unknown to known, the rendered
// timestamps are stale, so the body is re-rendered ONCE. The guard is the
// change itself, so this cannot loop: the second call finds the same offset and
// returns without re-rendering.
async function ensureUserEnvironment() {
  const ue = state.userEnvironment;
  const before = tzOffsetSec();
  if (ue.env) {
    setTzOffsetSec(ue.env.tz_offset_sec);
    setTzName(ue.env.timezone);
    renderEnvBadge();
    rerenderIfOffsetChanged(before);
    return;
  }
  try {
    const res = await fetch('/api/user_environment');
    const data = await res.json();
    if (res.ok && data.ok) {
      ue.env = data.environment || null;
      setTzOffsetSec(ue.env && ue.env.tz_offset_sec);
      setTzName(ue.env && ue.env.timezone);
      renderEnvBadge();
      rerenderIfOffsetChanged(before);
    }
  } catch (_) {
    renderEnvBadge();
  }
}

// Re-render the body ONLY when the offset actually changed. A re-render is what
// turns the already-painted UTC strings into local time; without it the page
// keeps two clocks. Guarded on the change so it runs at most once per page.
function rerenderIfOffsetChanged(before) {
  const after = tzOffsetSec();
  if (before === after) return;
  if (after == null) return;   // still unknown: nothing better to paint
  try {
    mount(false);
  } catch (_) {
    /* a re-render must never break the page */
  }
}

function badge(status) {
  const s = String(status || 'draft').toLowerCase();
  const map = {
    draft: 'bg-soft text-muted',
    ready: 'bg-accent-soft text-accent',
    done: 'bg-emerald-50 text-emerald-700',
    success: 'bg-emerald-50 text-emerald-700',
    error: 'bg-rose-50 text-rose-700',
    fail: 'bg-rose-50 text-rose-700',
    running: 'bg-amber-50 text-amber-700',
  };
  const cls = map[s] || map.draft;
  return '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ' + cls + '">' + esc(status || 'draft') + '</span>';
}

// THE TURN STATUS (2026-09-25). THE HUMAN: "and where is status!!!!" /
// "status : draft / penping / progressing / completed".
//
// MEASURED, and this is the defect: `draft` is the MOST COMMON value in
// `chat_center_message` (1899 rows) and it was the ONLY one with no colour —
// `chat-center-list.js` mapped done/ask/progressive/qc and nothing else. A
// status a reader cannot see is a status nobody filters on.
//
// An UNKNOWN status renders the raw value in the neutral colour, never blank:
// a blank cell reads as "no problem", which is the failure this whole feature
// exists to prevent.
function statusBadge(status) {
  const s = String(status || '').trim();
  if (!s) return '<span class="text-xs text-muted">-</span>';
  const map = {
    draft: 'bg-soft text-muted',
    pending: 'bg-amber-100 text-amber-800',
    progressing: 'bg-sky-100 text-sky-700',
    completed: 'bg-emerald-100 text-emerald-700',
    done: 'bg-emerald-100 text-emerald-700',
    ask: 'bg-amber-100 text-amber-800',
    progressive: 'bg-sky-100 text-sky-700',
    qc: 'bg-violet-100 text-violet-700',
  };
  const cls = map[s.toLowerCase()] || 'bg-slate-100 text-slate-600';
  return '<span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' + cls + '">' + esc(s) + '</span>';
}

function renderRightList() {
  const host = $('#record-list');
  if (!host) return;

  const modeBar =
    '<div class="flex gap-1 border-b border-line p-2">' +
    '<button type="button" data-catalog="server" class="flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition ' +
    (state.catalogMode === 'server' ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
    '">Server tasks</button>' +
    '<button type="button" data-catalog="local" class="flex-1 rounded-lg px-2 py-1.5 text-xs font-medium transition ' +
    (state.catalogMode === 'local' ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
    '">Local drafts</button></div>';

  if (state.catalogMode === 'local') {
    const rows = filteredLocal();
    host.innerHTML =
      modeBar +
      (rows.length
        ? rows
            .map((r) => {
              const active = r.id === state.selectedId;
              return (
                '<button type="button" data-local-id="' +
                esc(r.id) +
                '" class="w-full text-left px-3 py-2.5 border-b border-line/80 transition hover:bg-soft/80 ' +
                (active ? 'bg-accent-soft border-l-2 border-l-accent' : 'border-l-2 border-l-transparent') +
                '"><div class="flex items-center justify-between gap-2"><span class="text-[11px] mono text-muted truncate">' +
                esc(r.id) +
                '</span>' +
                badge(r.status) +
                '</div><div class="mt-0.5 text-sm font-medium text-ink truncate">' +
                esc(r.name || r.task_id || 'Untitled') +
                '</div></button>'
              );
            })
            .join('')
        : '<div class="p-4 text-sm text-muted">No local drafts. Click <b>New</b>.</div>');
    return;
  }

  const rows = filteredServer();
  host.innerHTML =
    modeBar +
    (rows.length
      ? rows
          .map((t) => {
            const active = t.id === state.selectedServerId;
            const title = t.task || t.model_label || t.id;
            const sub = [t.model_label || t.model, t.writer, t.task_id].filter(Boolean).join(' | ');
            return (
              '<button type="button" data-server-id="' +
              esc(t.id) +
              '" class="w-full text-left px-3 py-2.5 border-b border-line/80 transition hover:bg-soft/80 ' +
              (active ? 'bg-accent-soft border-l-2 border-l-accent' : 'border-l-2 border-l-transparent') +
              '"><div class="flex items-center justify-between gap-2"><span class="text-[11px] mono text-muted truncate">' +
              esc(t.id) +
              '</span>' +
              badge(t.result || t.status) +
              '</div><div class="mt-0.5 text-sm font-medium text-ink truncate">' +
              esc(title) +
              '</div><div class="mt-0.5 text-[11px] text-muted truncate">' +
              esc(sub) +
              '</div><div class="mt-1 flex gap-2 text-[11px] mono text-muted"><span title="Prompt tokens">in ' +
              esc(t.prompt_tokens ?? 0) +
              '</span><span title="Output tokens">out ' +
              esc(t.completion_tokens ?? 0) +
              '</span><span title="Total tokens">Sum ' +
              esc(t.total_tokens ?? 0) +
              '</span></div></button>'
            );
          })
          .join('')
      : '<div class="p-4 text-sm text-muted">No server tasks yet. Run Analyze / Improve first.</div>');
}

function reportHtml() {
  const t = state.report.totals || {};
  const tasks = state.report.tasks || [];
  const byModel = state.report.by_model || [];
  const sources = state.report.sources || [];
  const taskCenter = state.report.taskCenter || [];
  const tcLabels = new Set(taskCenter.map((r) => String(r.task_label || '')));

  return (
    '<div class="mx-auto flex max-w-5xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-2"><div><h2 class="text-lg font-semibold">Task Report</h2><p class="text-sm text-muted">Live history from helper | Refresh / auto 5s</p></div>' +
    '<button id="btn-clear-server" type="button" class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-100">Clear server history</button></div>' +
    '<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel"><div class="text-xs font-medium text-muted">Tasks</div><div class="mt-1 text-2xl font-semibold text-ink">' +
    esc(t.count || 0) +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel" title="Tokens sent to the model"><div class="text-xs font-medium text-muted">Prompt tok</div><div class="mt-1 text-2xl font-semibold text-ink">' +
    esc(t.prompt_tokens || 0) +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel" title="Tokens generated by the model"><div class="text-xs font-medium text-muted">Out tok</div><div class="mt-1 text-2xl font-semibold text-ink">' +
    esc(t.completion_tokens || 0) +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel" title="Prompt + Output"><div class="text-xs font-medium text-muted">Total tok</div><div class="mt-1 text-2xl font-semibold text-accent">' +
    esc(t.total_tokens || 0) +
    '</div></div></div>' +
    '<div class="grid gap-3 sm:grid-cols-2">' +
    (byModel
      .map(
        (m) =>
          '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel"><div class="font-medium text-ink">' +
          esc(m.label || m.model) +
          '</div><div class="mt-1 text-xs text-muted mono">' +
          esc(m.model) +
          '</div><div class="mt-2 text-sm text-muted">tasks ' +
          esc(m.count || 0) +
          ' | Sum ' +
          esc(m.total_tokens || 0) +
          '</div><div class="text-xs text-muted">in ' +
          esc(m.prompt_tokens || 0) +
          ' / out ' +
          esc(m.completion_tokens || 0) +
          '</div></div>'
      )
      .join('') ||
      '<div class="rounded-2xl border border-line bg-panel p-4 text-sm text-muted shadow-panel">No model stats yet.</div>') +
    '</div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"><div class="overflow-auto"><table class="min-w-full text-left text-sm"><thead class="bg-soft/80 text-xs uppercase tracking-wide text-muted"><tr>' +
    '<th class="px-3 py-2 font-semibold">Task ID</th><th class="px-3 py-2 font-semibold">Duration</th><th class="px-3 py-2 font-semibold">Source</th><th class="px-3 py-2 font-semibold">Model</th><th class="px-3 py-2 font-semibold">Task</th><th class="px-3 py-2 font-semibold">Status</th><th class="px-3 py-2 font-semibold">Result</th>' +
    '<th class="px-3 py-2 font-semibold" title="Prompt tokens">Prompt</th><th class="px-3 py-2 font-semibold" title="Output tokens">Out</th><th class="px-3 py-2 font-semibold" title="Total tokens">Total</th><th class="px-3 py-2 font-semibold">Detail</th>' +
    '</tr></thead><tbody>' +
    (tasks.length
      ? tasks
          .map((row) => {
            const active = row.id === state.selectedServerId;
            return (
              '<tr data-report-id="' +
              esc(row.id) +
              '" class="border-t border-line cursor-pointer transition hover:bg-soft/70 ' +
              (active ? 'bg-accent-soft/60' : '') +
              '"><td class="px-3 py-2"><code class="rounded bg-soft px-1.5 py-0.5 text-xs font-semibold text-ink">' +
              esc(row.task_id || '-') +
              ' | ' +
              esc(row.template_id || row.task_id || '-') +
              (row.item_type ? ' <span class="text-accent" title="Type: ' + esc(TASK_ID_TYPE_LABEL[row.item_type] || row.item_type) + '">[' + esc(row.item_type) + ']</span>' : '') +
              (row.task_version ? ' <span class="text-muted" title="Version">v' + esc(row.task_version) + '</span>' : '') +
              '</code></td><td class="px-3 py-2 mono text-xs">' +
              esc(fmtDuration(row.started_at, row.ended_at, row.duration_ms)) +
              '</td><td class="px-3 py-2 text-xs text-muted">' +
              esc(row.source || row.writer || '-') +
              '</td><td class="px-3 py-2">' +
              esc(row.model_label || row.model || '-') +
              '</td><td class="px-3 py-2">' +
              esc(row.task || '-') +
              '</td><td class="px-3 py-2">' +
              badge(row.status) +
              '</td><td class="px-3 py-2">' +
              badge(row.result || '-') +
              '</td><td class="px-3 py-2 mono text-xs" title="Prompt tokens: sent to model">' +
              esc(row.prompt_tokens ?? 0) +
              '</td><td class="px-3 py-2 mono text-xs" title="Output tokens: model reply">' +
              esc(row.completion_tokens ?? 0) +
              '</td><td class="px-3 py-2 mono text-xs font-semibold" title="Total = Prompt + Out">' +
              esc(row.total_tokens ?? 0) +
              '</td><td class="px-3 py-2 max-w-[220px] truncate text-xs text-muted" title="' +
              esc(row.reason || row.error || '') +
              '">' +
              esc(row.reason || row.error || '-') +
              '</td></tr>'
            );
          })
          .join('')
      : '<tr><td colspan="11" class="px-3 py-8 text-center text-muted">No LLM tasks yet.</td></tr>') +
    '</tbody></table></div></div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"><div class="mb-3 flex items-center justify-between border-b border-line px-3 py-2"><h3 class="text-base font-semibold text-ink">All Sources</h3><span class="text-xs text-muted mono">' +
    esc(sources.length) +
    ' rows | task_source_log + llm_tasks.json</span></div><div class="overflow-auto"><table class="min-w-full text-left text-sm"><thead class="bg-soft/80 text-xs uppercase tracking-wide text-muted"><tr>' +
    '<th class="px-3 py-2 font-semibold">Task ID</th><th class="px-3 py-2 font-semibold">Who</th><th class="px-3 py-2 font-semibold">Where</th><th class="px-3 py-2 font-semibold">Chat ID</th><th class="px-3 py-2 font-semibold">Session ID</th>' +
    '</tr></thead><tbody>' +
    (sources.length
      ? sources
          .map((row) =>
            '<tr class="border-t border-line"><td class="px-3 py-2"><code class="rounded bg-soft px-1.5 py-0.5 text-xs font-semibold text-ink">' +
            esc(row.task_id || '-') +
            '</code></td><td class="px-3 py-2 text-xs text-muted">' +
            esc(row.who || '-') +
            '</td><td class="px-3 py-2 text-xs text-muted mono">' +
            esc(row.where || '-') +
            '</td><td class="px-3 py-2 text-xs text-muted mono">' +
            esc(row.chat_id || '-') +
            '</td><td class="px-3 py-2 text-xs text-muted mono">' +
            esc(row.session_id || '-') +
            '</td></tr>'
          )
          .join('')
      : '<tr><td colspan="5" class="px-3 py-8 text-center text-muted">No task source rows yet.</td></tr>') +
    '</tbody></table></div></div>' +
    '<div class="overflow-hidden rounded-2xl border border-line bg-panel shadow-panel"><div class="mb-3 flex items-center justify-between border-b border-line px-3 py-2"><h3 class="text-base font-semibold text-ink">Task Center</h3><span class="text-xs text-muted mono">' +
    esc(taskCenter.length) +
    ' rows | dev_task (SSOT: every work item must be registered here)</span></div><div class="overflow-auto"><table class="min-w-full text-left text-sm"><thead class="bg-soft/80 text-xs uppercase tracking-wide text-muted"><tr>' +
    '<th class="px-3 py-2 font-semibold">Task ID</th><th class="px-3 py-2 font-semibold">Title</th><th class="px-3 py-2 font-semibold">Status</th><th class="px-3 py-2 font-semibold">Writer</th><th class="px-3 py-2 font-semibold">Updated</th>' +
    '</tr></thead><tbody>' +
    (taskCenter.length
      ? taskCenter
          .map(
            (row) =>
              '<tr class="border-t border-line"><td class="px-3 py-2"><code class="rounded bg-soft px-1.5 py-0.5 text-xs font-semibold text-ink">' +
              esc(row.task_label || '-') +
              '</code></td><td class="px-3 py-2">' +
              esc(row.title || '-') +
              '</td><td class="px-3 py-2">' +
              badge(row.status || '-') +
              '</td><td class="px-3 py-2 text-xs text-muted">' +
              esc(row.writer || '-') +
              '</td><td class="px-3 py-2 text-xs text-muted mono">' +
              esc(fmtLocal(row.updated_at)) +
              '</td></tr>'
          )
          .join('')
      : '<tr><td colspan="5" class="px-3 py-8 text-center text-muted">No Task Center rows yet.</td></tr>') +
    '</tbody></table></div></div></div>'
  );
}

// THE CATALOG TREE — the `catalog` table nested by `parent_id`.
//
// THE HUMAN (2026-09-27): "catalog table has upgrade with parent_id, subcatalog is
// not need any more".
//
// MEASURED before this: the renderer read `cat.catalog` (an INTEGER) and
// `tpl.subcatalog`, and the API behind it grouped `skill_template` by its integer
// `catalog` column. MEASURED: that column is 10 on every row and
// `skill_prompt_ext.ROOT_TASK_ID` is 10 — it is a ROOT TASK ID, not a `catalog`
// row, so the page showed ONE card labelled "10" and the five real shelves
// (UI, QA, chat_center, verification, ui_panel) never appeared.
//
// THE NESTING IS RECURSIVE, because a `parent_id` tree has no fixed depth: the old
// flat "catalog > templates" shape cannot express a shelf whose child is itself a
// group. A node renders its own templates, then its children.
function graphNodeHtml(node, depth) {
  const pad = depth ? ' ml-' + Math.min(depth * 4, 12) : '';
  const kind = node.node_kind === 'group'
    ? '<span class="rounded bg-soft px-1.5 py-0.5 text-xs text-muted">group</span>'
    : '<span class="rounded bg-soft px-1.5 py-0.5 text-xs text-muted">leaf</span>';
  const tpls = node.templates || [];
  let h =
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel' + pad + '">' +
    '<div class="mb-2 flex items-center justify-between gap-2">' +
    '<h3 class="text-base font-semibold text-ink">' + esc(node.name) +
    ' <span class="mono text-xs text-muted">#' + esc(node.catalog_id) + '</span></h3>' +
    '<span class="flex items-center gap-2">' + kind +
    '<span class="text-xs text-muted mono">' + esc(tpls.length) + ' templates</span></span>' +
    '</div>' +
    (node.description
      ? '<p class="mb-2 text-xs text-muted">' + esc(node.description) + '</p>'
      : '');
  if (tpls.length) {
    h +=
      '<div class="overflow-auto"><table class="min-w-full text-left text-sm">' +
      '<thead class="bg-soft/80 text-xs uppercase tracking-wide text-muted"><tr>' +
      '<th class="px-3 py-2 font-semibold">Template</th>' +
      '<th class="px-3 py-2 font-semibold">Task root.seq</th>' +
      '<th class="px-3 py-2 font-semibold">Type</th>' +
      '<th class="px-3 py-2 font-semibold">Version</th>' +
      '<th class="px-3 py-2 font-semibold">Description</th>' +
      '</tr></thead><tbody>';
    for (const tpl of tpls) {
      h +=
        '<tr class="border-t border-line">' +
        '<td class="px-3 py-2"><code class="rounded bg-soft px-1.5 py-0.5 text-xs font-semibold text-ink">' +
        esc(tpl.template_id) + '</code></td>' +
        // WHAT THE LEGACY COLUMNS MEAN: `{root}.{seq}`, the task numbering. Shown
        // as such rather than as a "subcatalog", which is the name of a VIEW.
        '<td class="px-3 py-2 mono text-xs">' + esc(tpl.task_root) + '.' + esc(tpl.task_seq) + '</td>' +
        '<td class="px-3 py-2"><span class="rounded bg-soft px-1.5 py-0.5 text-xs font-semibold text-ink">' +
        esc(tpl.item_type || '-') + '</span></td>' +
        '<td class="px-3 py-2 mono text-xs">v' + esc(tpl.version) + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(tpl.description || '-') + '</td>' +
        '</tr>';
    }
    h += '</tbody></table></div>';
  }
  h += '</div>';
  for (const child of (node.children || [])) {
    h += graphNodeHtml(child, depth + 1);
  }
  return h;
}

function graphHtml() {
  const g = state.graph || {};
  const catalogs = g.catalogs || [];
  const cats = catalogs.length
    ? catalogs.map((n) => graphNodeHtml(n, 0)).join('')
    : '<div class="p-4 text-sm text-muted">No catalog rows yet. Seed via backend.</div>';
  // THE COUNTS ARE SHOWN, INCLUDING THE LINK GAP. MEASURED 2026-09-27: 21 skill
  // templates exist and NONE is linked to a shelf (`skill_template.catalog` is the
  // root task id 10). Reporting that gap is the point — the alternative is a page
  // that looks complete while nothing is attached.
  const gap = (g.unlinked_templates || 0)
    ? '<div class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">' +
      esc(g.unlinked_templates) + ' of ' + esc(g.template_count || 0) +
      ' skill template(s) are NOT linked to a catalog node. skill_template.catalog ' +
      'holds the ROOT TASK ID, not a catalog id, so no shelf can be derived from it.</div>'
    : '';
  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-center justify-between gap-2"><div><h2 class="text-lg font-semibold">Catalog Tree</h2>' +
    '<p class="text-sm text-muted">catalog nested by parent_id (' + esc(g.node_count || 0) +
    ' nodes, ' + esc(g.root_count || 0) + ' roots) \u00b7 source for system topology</p></div>' +
    '<button id="btn-graph-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button></div>' +
    (g.msg ? '<div class="rounded-xl border border-line bg-panel px-3 py-2 text-sm ' + (g.msgOk ? 'text-ink' : 'text-rose-700') + '">' + esc(g.msg) + '</div>' : '') +
    gap +
    '<div class="flex flex-col gap-4">' +
    cats +
    '</div></div>'
  );
}

function skillContractsHtml() {
  const c = state.skill.contracts || {};
  const rows = c.rows || [];
  const sel = c.selected || null;
  const list = rows.length
    ? rows
        .map((r) => {
          const active = sel && sel.contract_id === r.contract_id;
          const q = r.streak_qualified ? 'bg-emerald-50 text-emerald-700' : 'bg-soft';
          return (
            '<tr class="cursor-pointer ' +
            (active ? 'bg-accent-soft' : 'hover:bg-soft') +
            '" data-contract="' +
            esc(r.contract_id) +
            '">' +
            '<td class="px-3 py-2 mono text-xs">' +
            esc(r.contract_id) +
            '</td>' +
            '<td class="px-3 py-2 text-sm">' +
            esc(r.skill_key) +
            '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' +
            esc(r.taxonomy_path) +
            '</td>' +
            '<td class="px-3 py-2 text-xs">' +
            (r.write_owner ? '<span class="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700">write owner</span>' : '—') +
            '</td>' +
            '<td class="px-3 py-2 text-xs">' +
            esc(r.status) +
            '</td>' +
            '<td class="px-3 py-2 text-xs"><span class="rounded-full ' +
            q +
            ' px-2 py-0.5 mono">' +
            esc(r.current_streak == null ? '-' : r.current_streak) +
            '/' +
            esc(r.target_streak == null ? '-' : r.target_streak) +
            '</span></td>' +
            '<td class="px-3 py-2 text-xs">' +
            esc(r.best_streak == null ? '-' : r.best_streak) +
            '</td>' +
            '<td class="px-3 py-2 text-xs">' +
            esc(r.reset_count == null ? '-' : r.reset_count) +
            '</td>' +
            '</tr>'
          );
        })
        .join('')
    : '<tr><td colspan="8" class="px-3 py-6 text-center text-muted">No skill contracts yet.</td></tr>';

  let detail = '<div class="rounded-2xl border border-dashed border-line bg-soft/40 p-4 text-sm text-muted">Select a contract row to inspect Environment / Purpose / Flow / Not To Do, the Field Register, TDD cases and the review log.</div>';
  if (sel) {
    const env = sel.environment || {};
    const flow = sel.flow || [];
    const ntd = sel.not_to_do || [];
    const fields = sel.fields || [];
    const cases = sel.tdd_cases || [];
    const logs = sel.review_log || [];
    const kv = Object.keys(env).length
      ? Object.keys(env)
          .map(
            (k) =>
              '<div class="flex gap-2 text-xs"><span class="w-28 shrink-0 text-muted">' +
              esc(k) +
              '</span><span class="mono">' +
              esc(typeof env[k] === 'object' ? JSON.stringify(env[k]) : env[k]) +
              '</span></div>'
          )
          .join('')
      : '<div class="text-xs text-muted">—</div>';
    const flowHtml = flow.length
      ? '<ol class="ml-4 list-decimal text-xs leading-relaxed">' +
        flow.map((f) => '<li>' + esc(typeof f === 'object' ? JSON.stringify(f) : f) + '</li>').join('') +
        '</ol>'
      : '<div class="text-xs text-muted">—</div>';
    const ntdHtml = ntd.length
      ? '<ul class="ml-4 list-disc text-xs leading-relaxed text-rose-700">' +
        ntd.map((f) => '<li>' + esc(typeof f === 'object' ? JSON.stringify(f) : f) + '</li>').join('') +
        '</ul>'
      : '<div class="text-xs text-muted">—</div>';
    const fieldRows = fields.length
      ? fields
          .map(
            (f) =>
              '<tr><td class="px-2 py-1 mono text-xs">' +
              esc(f.field_id || '-') +
              '</td><td class="px-2 py-1 mono text-xs">' +
              esc(f.field_name) +
              '</td><td class="px-2 py-1 text-xs">' +
              esc(f.data_type) +
              '</td><td class="px-2 py-1 text-xs">' +
              (f.mandatory ? 'Y' : 'N') +
              '</td><td class="px-2 py-1 text-xs">' +
              esc(f.enum ? JSON.stringify(f.enum) : '—') +
              '</td><td class="px-2 py-1 text-xs">' +
              esc(f.hard_rule) +
              '</td><td class="px-2 py-1 text-xs">' +
              (f.immutable ? 'Y' : 'N') +
              '</td></tr>'
          )
          .join('')
      : '<tr><td colspan="7" class="px-2 py-4 text-center text-muted">No Field Register rows.</td></tr>';
    const caseRows = cases.length
      ? cases
          .map(
            (t) =>
              '<tr><td class="px-2 py-1 text-xs"><span class="rounded-full ' +
              (t.kind === 'pass' ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700') +
              ' px-2 py-0.5">' +
              esc(t.kind) +
              '</span></td><td class="px-2 py-1 mono text-xs">' +
              esc(t.case_key) +
              '</td><td class="px-2 py-1 text-xs">' +
              esc(t.assertion) +
              '</td></tr>'
          )
          .join('')
      : '<tr><td colspan="3" class="px-2 py-4 text-center text-muted">No TDD cases.</td></tr>';
    const logRows = logs.length
      ? logs
          .map(
            (l) =>
              '<div class="rounded-xl border border-line bg-panel p-2 text-xs"><div class="text-muted">' +
              esc(fmtLocal(l.created_at)) +
              (l.linked_trace_id ? ' · trace ' + esc(l.linked_trace_id) : '') +
              '</div><div class="mt-1"><b>gap</b> ' +
              esc(l.gap || '—') +
              '</div><div><b>revision</b> ' +
              esc(l.revision || '—') +
              '</div><div><b>streak</b> ' +
              esc(l.streak_result || '—') +
              '</div></div>'
          )
          .join('')
      : '<div class="text-xs text-muted">No review log entries.</div>';

    detail =
      '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="flex flex-wrap items-center justify-between gap-2">' +
      '<div><h3 class="text-base font-semibold mono">' +
      esc(sel.contract_id) +
      ' · ' +
      esc(sel.skill_key) +
      '</h3><p class="text-xs text-muted">' +
      esc(sel.taxonomy_path) +
      ' · v' +
      esc(sel.version) +
      ' · ' +
      esc(sel.status) +
      ' · source ' +
      esc(sel.source) +
      '</p></div>' +
      '<div class="flex gap-2 text-xs">' +
      '<span class="rounded-full bg-soft px-2.5 py-1 mono">streak ' +
      esc(sel.current_streak == null ? '-' : sel.current_streak) +
      '/' +
      esc(sel.target_streak == null ? '-' : sel.target_streak) +
      '</span>' +
      '<span class="rounded-full bg-soft px-2.5 py-1 mono">best ' +
      esc(sel.best_streak == null ? '-' : sel.best_streak) +
      '</span>' +
      '<span class="rounded-full bg-soft px-2.5 py-1 mono">resets ' +
      esc(sel.reset_count == null ? '-' : sel.reset_count) +
      '</span>' +
      (sel.write_owner ? '<span class="rounded-full bg-amber-50 px-2.5 py-1 text-amber-700">sole write entry</span>' : '') +
      '<button id="sk-contracts-run-tdd" type="button" data-contract="' +
      esc(sel.contract_id) +
      '" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Run TDD</button>' +
      '</div></div>' +
      '<div class="mt-3 grid gap-3 md:grid-cols-2">' +
      '<div class="rounded-xl border border-line bg-soft/40 p-3"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Environment</div>' +
      kv +
      '</div>' +
      '<div class="rounded-xl border border-line bg-soft/40 p-3"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Purpose</div>' +
      '<div class="text-xs leading-relaxed">' +
      esc(sel.purpose) +
      '</div>' +
      (sel.purpose_not_responsible
        ? '<div class="mt-2 text-xs leading-relaxed text-rose-700">' + esc(sel.purpose_not_responsible) + '</div>'
        : '') +
      '</div>' +
      '<div class="rounded-xl border border-line bg-soft/40 p-3"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Flow</div>' +
      flowHtml +
      '</div>' +
      '<div class="rounded-xl border border-line bg-soft/40 p-3"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Not To Do</div>' +
      ntdHtml +
      '</div>' +
      '</div>' +
      '<div class="mt-4"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Field Register</div>' +
      '<div class="overflow-auto rounded-xl border border-line"><table class="w-full text-left"><thead class="bg-soft text-xs text-muted"><tr><th class="px-2 py-1">ID</th><th class="px-2 py-1">Field</th><th class="px-2 py-1">Type</th><th class="px-2 py-1">Req</th><th class="px-2 py-1">Enum</th><th class="px-2 py-1">Hard rule</th><th class="px-2 py-1">Imm</th></tr></thead><tbody>' +
      fieldRows +
      '</tbody></table></div></div>' +
      '<div class="mt-4"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">TDD Proof Cases</div>' +
      '<div class="overflow-auto rounded-xl border border-line"><table class="w-full text-left"><thead class="bg-soft text-xs text-muted"><tr><th class="px-2 py-1">Kind</th><th class="px-2 py-1">Case</th><th class="px-2 py-1">Assertion</th></tr></thead><tbody>' +
      caseRows +
      '</tbody></table></div></div>' +
      '<div class="mt-4"><div class="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Post-Case Review Log</div>' +
      '<div class="flex flex-col gap-2">' +
      logRows +
      '</div></div>' +
      '</div>';
  }

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-center justify-between gap-2"><div><h2 class="text-lg font-semibold">Skill Contracts</h2>' +
    '<p class="text-sm text-muted">Environment / Purpose / Flow / Not To Do + Field Register + TDD + streak (DB-driven SSOT)</p></div>' +
    '<button id="sk-contracts-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button></div>' +
    (c.msg ? '<div class="rounded-xl border border-line bg-panel px-3 py-2 text-sm ' + (c.msgOk ? 'text-ink' : 'text-rose-700') + '">' + esc(c.msg) + '</div>' : '') +
    '<div class="overflow-auto rounded-2xl border border-line bg-panel shadow-panel"><table class="w-full text-left"><thead class="bg-soft text-xs text-muted"><tr><th class="px-3 py-2">Contract</th><th class="px-3 py-2">Skill</th><th class="px-3 py-2">Taxonomy</th><th class="px-3 py-2">Write</th><th class="px-3 py-2">Status</th><th class="px-3 py-2">Streak</th><th class="px-3 py-2">Best</th><th class="px-3 py-2">Resets</th></tr></thead><tbody>' +
    list +
    '</tbody></table></div>' +
    detail +
    '</div>'
  );
}

async function loadSkillContracts(contractId) {
  const c = state.skill.contracts;
  try {
    const res = await fetch('/api/skill-contracts');
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'load failed');
    c.rows = data.contracts || [];
    const want = contractId || (c.selected && c.selected.contract_id) || (c.rows[0] && c.rows[0].contract_id);
    if (want) {
      const dres = await fetch('/api/skill-contracts/' + encodeURIComponent(want));
      const ddata = await dres.json();
      if (!dres.ok || ddata.ok === false) throw new Error(ddata.error || 'detail failed');
      c.selected = {
        ...(ddata.contract || {}),
        ...(ddata.streak || {}),
        fields: ddata.fields || [],
        tdd_cases: ddata.tdd_cases || [],
        review_log: ddata.review_log || [],
      };
    } else {
      c.selected = null;
    }
    c.msg = '';
    c.msgOk = true;
  } catch (e) {
    c.msg = 'Load failed: ' + (e && e.message ? e.message : e);
    c.msgOk = false;
  }
  const body = $('#workspace-body');
  if (body && state.nav === 'skill-ssot' && state.skill.tab === 'contracts') {
    body.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
}

async function runSkillContractTdd(contractId) {
  const c = state.skill.contracts;
  if (!contractId) return;
  c.msg = 'Running TDD for ' + contractId + '…';
  c.msgOk = true;
  const body = $('#workspace-body');
  if (body && state.nav === 'skill-ssot' && state.skill.tab === 'contracts') {
    body.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
  let msg = '';
  let msgOk = true;
  try {
    const res = await fetch(
      '/api/skill-contracts/' + encodeURIComponent(contractId) + '/run-tdd',
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
    );
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || ('run failed (' + res.status + ')'));
    }
    const st = data.streak || {};
    // Show n_passed/n_cases even on success, so a partial pass is visible
    // rather than hidden behind a green button.
    msg =
      'TDD ' + data.n_passed + '/' + data.n_cases + ' passed · streak ' +
      (st.current_streak == null ? '-' : st.current_streak) + '/' +
      (st.target_streak == null ? '-' : st.target_streak) +
      (st.qualified ? ' · QUALIFIED' : '');
    msgOk = data.n_passed === data.n_cases;
  } catch (e) {
    msg = 'Run TDD failed: ' + (e && e.message ? e.message : e);
    msgOk = false;
  }
  // loadSkillContracts() resets c.msg to '' on success, which would wipe the
  // result the user just asked for. Set the message AFTER the refresh and
  // re-render, so the outcome is actually visible.
  await loadSkillContracts(contractId);
  c.msg = msg;
  c.msgOk = msgOk;
  const body2 = $('#workspace-body');
  if (body2 && state.nav === 'skill-ssot' && state.skill.tab === 'contracts') {
    body2.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
}

function skillLifecycleHtml() {
  const s = state.skill || {};
  const events = s.lifecycleEvents || [];
  const rows = events.length
    ? events
        .map(
          (ev) =>
            '<tr class="border-t border-line">' +
            '<td class="px-2 py-1.5 mono text-xs">' +
            esc(ev.event_sequence ?? '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(ev.event_timestamp || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs font-medium">' +
            esc(ev.event_type || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs">' +
            esc(ev.task_state || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(ev.skill_id || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(ev.module || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(ev.capability || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs">' +
            esc(ev.event_summary || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono break-all">' +
            esc(ev.linked_trace_id || '-') +
            '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="9" class="px-3 py-6 text-center text-sm text-muted">No lifecycle rows. Enter task_id and Load.</td></tr>';
  return (
    '<div class="mx-auto flex max-w-5xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h2 class="text-lg font-semibold">Lifecycle Trace</h2>' +
    '<p class="mt-1 text-sm text-muted">task_lifecycle_log | append-only | module+capability on each row | event_type is NOT module/capability</p>' +
    '<div class="mt-3 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">task_id' +
    '<input id="lc-task-id" value="' +
    esc(s.timelineTaskId || '') +
    '" class="ml-1 w-56 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm mono" placeholder="t_bae8519f5466" /></label>' +
    '<button id="lc-load" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Load timeline</button>' +
    '<button id="lc-seed" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Seed lifecycle skills</button>' +
    '</div>' +
    '<div id="lc-msg" class="mt-2 text-xs text-muted">' +
    esc(s.timelineMsg || '') +
    '</div>' +
    '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left text-sm"><thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-2 py-2">seq</th><th class="px-2 py-2">time</th><th class="px-2 py-2">event_type</th><th class="px-2 py-2">state</th>' +
    '<th class="px-2 py-2">skill</th><th class="px-2 py-2">module</th><th class="px-2 py-2">capability</th><th class="px-2 py-2">summary</th><th class="px-2 py-2">trace</th>' +
    '</tr></thead><tbody>' +
    rows +
    '</tbody></table></div></div></div>'
  );
}

function skillPromptTraceHtml() {
  const s = state.skill || {};
  const traces = s.promptTraces || [];
  const rows = traces.length
    ? traces
        .map(
          (tr) =>
            '<tr class="border-t border-line">' +
            '<td class="px-2 py-1.5 mono text-xs break-all">' +
            esc(tr.trace_id || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(fmtLocal(tr.created_at)) +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(tr.skill_id || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(tr.module || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(tr.capability || '') +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs max-w-[280px] truncate" title="' +
            esc(tr.prompt_snapshot || '') +
            '">' +
            esc((tr.prompt_snapshot || '').slice(0, 120)) +
            '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="6" class="px-3 py-6 text-center text-sm text-muted">No prompt traces. Load task_id or POST /api/tasks/prompt-trace.</td></tr>';
  return (
    '<div class="mx-auto flex max-w-5xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h2 class="text-lg font-semibold">Prompt Trace</h2>' +
    '<p class="mt-1 text-sm text-muted">task_prompt_trace snapshots | assembler full skill is Phase D | minimal write API ready</p>' +
    '<div class="mt-3 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">task_id' +
    '<input id="pt-task-id" value="' +
    esc(s.timelineTaskId || '') +
    '" class="ml-1 w-56 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm mono" /></label>' +
    '<button id="pt-load" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Load traces</button>' +
    '</div>' +
    '<div id="pt-msg" class="mt-2 text-xs text-muted">' +
    esc(s.timelineMsg || '') +
    '</div>' +
    '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
    '<table class="min-w-full text-left text-sm"><thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-2 py-2">trace_id</th><th class="px-2 py-2">created</th><th class="px-2 py-2">skill</th><th class="px-2 py-2">module</th><th class="px-2 py-2">capability</th><th class="px-2 py-2">snapshot</th>' +
    '</tr></thead><tbody>' +
    rows +
    '</tbody></table></div></div></div>'
  );
}

function skillFactorTableHtml() {
  const s = state.skill || {};
  const t = s.factorTable || null;
  const skillKey = s.skillKey || 'mouse_spot_verify';
  // A rule with no proof is reported UNMEASURED, never as a pass. The three
  // states are coloured differently so "no proof recorded" cannot be mistaken
  // for "verified".
  const pill = (st) =>
    st === 'PASS'
      ? '<span class="rounded-full bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">PASS</span>'
      : st === 'FAIL'
        ? '<span class="rounded-full bg-rose-50 px-2 py-0.5 text-xs text-rose-700">FAIL</span>'
        : '<span class="rounded-full bg-soft px-2 py-0.5 text-xs text-muted" title="no proof row recorded — this is NOT a pass">UNMEASURED</span>';
  const rows = ((t && t.rows) || [])
    .map(
      (r) =>
        '<tr class="border-t border-line align-top">' +
        '<td class="px-2 py-2 font-medium">' + esc(r.name || '') +
        '<div class="mono mt-0.5 text-[11px] text-muted">' + esc(r.factor_key) + '</div></td>' +
        '<td class="px-2 py-2 max-w-sm text-xs">' + esc(r.rule_definition || '') + '</td>' +
        '<td class="px-2 py-2 max-w-xs text-xs text-muted">' + esc(r.action || '') + '</td>' +
        '<td class="px-2 py-2 mono whitespace-nowrap text-xs">' + esc(r.metric_kind || '') + ' &le; ' + esc(r.metric_target || '') + '</td>' +
        '<td class="px-2 py-2 mono text-xs">' + esc(r.metric_value === '' ? '-' : r.metric_value) + '</td>' +
        '<td class="px-2 py-2 mono break-all text-[11px] text-muted">' + esc(r.proof_artifact_id || '') + '</td>' +
        '<td class="px-2 py-2">' + pill(r.state) + '</td></tr>'
    )
    .join('');
  const summary = t
    ? '<span class="rounded-full bg-soft px-2.5 py-1">' + esc(t.applicable) + ' applicable</span>' +
      '<span class="rounded-full bg-soft px-2.5 py-1">' + esc(t.measured) + ' measured</span>' +
      '<span class="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">' + esc(t.passed) + ' PASS</span>' +
      '<span class="rounded-full bg-rose-50 px-2.5 py-1 text-rose-700">' + esc(t.failed) + ' FAIL</span>' +
      '<span class="rounded-full bg-amber-50 px-2.5 py-1 text-amber-700">' + esc(t.unmeasured) + ' UNMEASURED</span>'
    : '';
  return (
    '<div class="mx-auto max-w-6xl">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="flex flex-wrap items-start justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold">Factors — the law table</h2>' +
    '<p class="mt-1 text-sm text-muted">Every rule that applies to this skill, with its MEASURED unit and proof. A factor with no proof is UNMEASURED, never a pass.</p></div>' +
    '<div class="flex flex-wrap items-end gap-2 text-xs">' + summary +
    '<label class="text-xs font-medium text-muted">Skill<select id="skf-pick" class="ml-1 min-w-[160px] rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    (function () {
      const keys = (state.skill.skillKeys && state.skill.skillKeys.length) ? state.skill.skillKeys : [skillKey];
      return keys.map((k) => '<option value="' + esc(k) + '"' + (k === skillKey ? ' selected' : '') + '>' + esc(k) + '</option>').join('');
    })() +
    '</select></label>' +
    '<button type="button" id="skf-refresh" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div></div>' +
    '<div class="mt-3 overflow-auto rounded-xl border border-line">' +
    '<table class="w-full text-left text-sm">' +
    '<thead class="bg-soft/80 text-[11px] uppercase text-muted"><tr>' +
    '<th class="px-2 py-2">Factor</th><th class="px-2 py-2">Rule definition</th><th class="px-2 py-2">Action</th>' +
    '<th class="px-2 py-2">Metric (unit &le; target)</th><th class="px-2 py-2">Value</th><th class="px-2 py-2">Proof</th><th class="px-2 py-2">State</th>' +
    '</tr></thead><tbody>' +
    (rows || '<tr><td colspan="7" class="px-3 py-6 text-center text-sm text-muted">No factors loaded.</td></tr>') +
    '</tbody></table></div>' +
    '<pre class="mono mt-3 max-h-40 overflow-auto rounded-xl border border-line bg-soft/80 p-3 text-xs whitespace-pre-wrap">' +
    esc(s.factorMsg || '') + '</pre></div></div>'
  );
}

async function loadSkillFactors() {
  const skillKey = $('#skf-pick')?.value || state.skill.skillKey || 'mouse_spot_verify';
  state.skill.skillKey = skillKey;
  try {
    // The picker must list EVERY skill, not just the one being viewed. MEASURED
    // 2026-09-28: the first version of this tab shipped a picker holding ONE option
    // (`mouse_spot_verify`), because it reused `state.skill.skillKeys`, which is only
    // populated by the Editor tab's load. A picker with one option makes every other
    // skill unreachable, which is the defect this tab exists to remove — so this tab
    // loads the list ITSELF rather than depending on another tab having run first.
    if (!state.skill.skillKeys || state.skill.skillKeys.length < 2) {
      try {
        const lr = await fetch('/api/skills');
        const ld = await lr.json();
        if (lr.ok && ld.ok) {
          const keys = ld.skill_keys || [];
          const fromRows = (ld.skills || []).map((r) => r.skill_key).filter(Boolean);
          state.skill.skillKeys = Array.from(
            new Set([...(keys || []), ...fromRows, skillKey])
          );
        }
      } catch (_) {
        /* keep whatever we have */
      }
    }
    const res = await fetch('/api/skills/' + encodeURIComponent(skillKey) + '/table');
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'load failed');
    state.skill.factorTable = data;
    state.skill.factorMsg = 'Loaded ' + data.applicable + ' factors for ' + skillKey;
  } catch (e) {
    state.skill.factorMsg = 'Load failed: ' + (e && e.message ? e.message : e);
  }
  const body = $('#workspace-body');
  if (body && state.nav === 'skill-ssot' && state.skill.tab === 'factors') {
    body.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
}

function skillProofHtml() {
  const s = state.skill || {};
  return (
    '<div class="mx-auto flex max-w-4xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h2 class="text-lg font-semibold">Gold / Proof</h2>' +
    '<p class="mt-1 text-sm text-muted">Use Editor tab Run proof test / Test gold for vision skills. Lifecycle uses Python append API.</p>' +
    '<div class="mt-3 flex flex-wrap gap-2">' +
    '<button id="sk-proof-to-editor" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Open Editor</button>' +
    '<button id="sk-proof-seed-lc" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Seed lifecycle skills</button>' +
    '</div>' +
    '<pre class="mono mt-3 max-h-64 overflow-auto rounded-xl border border-line bg-soft/80 p-3 text-xs whitespace-pre-wrap">' +
    esc(s.log || '(no proof log yet)') +
    '</pre></div></div>'
  );
}

function skillDimensionsHtml() {
  // Multi-dimensional prompt SSOT. One combination = one prompt_key.
  const d = (state.skill && state.skill.dimensions) || {};
  const reg = d.registry || {};
  const combos = d.combos || [];
  const msgBar =
    '<div id="sk-dim-msg" class="mb-3 rounded-xl border border-line bg-soft/60 px-3 py-2 text-xs ' +
    (d.msgOk === false ? 'text-red-600' : 'text-muted') + '">' +
    esc(d.msg || '') +
    '</div>';

  const header =
    '<div class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="flex flex-wrap items-start justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold">Prompt Dimensions</h2>' +
    '<p class="mt-1 text-xs text-muted">A prompt is <b>composed</b> from named axes, not copied. ' +
    'Each combination gets its own <span class="mono">prompt_key</span>, so variants coexist and can be measured. ' +
    'Nothing is duplicated: each rule lives once per axis value.</p></div>' +
    '<div class="flex flex-wrap gap-2">' +
    '<button id="sk-dim-seed" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Seed axes</button>' +
    '<button id="sk-dim-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '<button id="sk-dim-plan" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="Show split + cost without calling the model">Sweep plan</button>' +
    '</div></div>' +
    '<div class="mt-2 flex flex-wrap gap-3 text-xs text-muted">' +
    '<span>axes: <b class="text-ink">' +
    esc(String(d.dimensionCount || Object.keys(reg).length || 0)) +
    '</b></span><span>combinations: <b class="text-ink">' +
    esc(String(d.comboCount || combos.length || 0)) +
    '</b></span><span>measured: <b class="text-ink">' +
    esc(String(combos.length)) +
    '</b></span></div></div>';

  if (!Object.keys(reg).length) {
    return (
      header +
      msgBar +
      '<div class="rounded-2xl border border-line bg-panel p-8 text-center text-sm text-muted">' +
      'No axes registered. Press <b>Seed axes</b> to load the mouse_spot_verify registry.' +
      '</div>'
    );
  }

  const axisBlocks = Object.keys(reg)
    .map((dimKey) => {
      const dim = reg[dimKey];
      const vals = Object.keys(dim.values).sort();
      const rows = vals
        .map((vk) => {
          const v = dim.values[vk];
          const txt = String(v.value_text || '');
          return (
            '<tr class="border-t border-line align-top">' +
            '<td class="px-3 py-2"><span class="mono text-xs font-medium text-accent">' +
            esc(vk) +
            '</span></td>' +
            '<td class="px-3 py-2 text-xs text-muted">' +
            esc(v.description || '') +
            '</td>' +
            '<td class="px-3 py-2 text-xs"><span class="mono">' +
            esc(txt ? txt.slice(0, 150) + (txt.length > 150 ? '…' : '') : '(empty)') +
            '</span></td>' +
            '<td class="px-3 py-2 text-center text-xs mono">' +
            esc(String(txt.length)) +
            '</td></tr>'
          );
        })
        .join('');
      return (
        '<section class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex flex-wrap items-center gap-2">' +
        '<h3 class="text-sm font-semibold text-ink mono">' +
        esc(dimKey) +
        '</h3>' +
        '<span class="rounded bg-soft px-2 py-0.5 text-[11px] text-muted">' +
        esc(String(vals.length)) +
        ' values</span></div>' +
        '<div class="overflow-x-auto rounded-xl border border-line">' +
        '<table class="w-full text-left"><thead class="bg-soft/60 text-xs text-muted"><tr>' +
        '<th class="px-3 py-2">value</th><th class="px-3 py-2">what it changes</th>' +
        '<th class="px-3 py-2">text</th><th class="px-3 py-2 text-center">chars</th>' +
        '</tr></thead><tbody>' +
        rows +
        '</tbody></table></div></section>'
      );
    })
    .join('');

  // Measurement ledger: ranked by holdout balanced accuracy, then train.
  let ledger = '';
  if (combos.length) {
    const rows = combos
      .map((c) => {
        const st = String(c.status || '');
        const badge = st
          ? '<span class="rounded px-1.5 py-0.5 text-[11px] ' +
            (st === 'certified'
              ? 'bg-emerald-100 text-emerald-700'
              : st === 'overfit'
                ? 'bg-rose-100 text-rose-700'
                : st === 'void'
                  ? 'bg-amber-100 text-amber-700'
                  : 'bg-soft text-muted') +
            '">' +
            esc(st) +
            '</span>'
          : '';
        const ax = c.axes || {};
        const axStr = Object.keys(ax)
          .sort()
          .map((k) => k + '=' + ax[k])
          .join(' | ');
        // Always show a RANGE when repeated seeds were measured. A single number
        // hid the certified winner's train score moving 71-92% across seeds while
        // its holdout stayed at 100%.
        const fmtRange = (lo, hi, single) => {
          if (hi != null && lo != null && Math.abs(hi - lo) >= 0.005) {
            return Number(lo).toFixed(1) + '-' + Number(hi).toFixed(1) + '%';
          }
          if (hi != null) return Number(hi).toFixed(1) + '%';
          return single == null ? '-' : Number(single).toFixed(1) + '%';
        };
        const seedNote =
          c.seed_runs && c.seed_runs > 1
            ? ' <span class="text-[10px] text-muted">(' +
              c.hold_perfect +
              '/' +
              c.seed_runs +
              ' seeds)</span>'
            : '';
        return (
          '<tr class="border-t border-line align-top">' +
          '<td class="px-3 py-2"><span class="mono text-[11px]">' +
          esc(c.prompt_key || '') +
          '</span></td>' +
          '<td class="px-3 py-2 text-[11px] text-muted">' +
          esc(axStr) +
          '</td>' +
          '<td class="px-3 py-2 text-center text-xs mono">' +
          esc(fmtRange(c.train_ba_min, c.train_ba_max, c.train_ba_pct)) +
          '</td>' +
          '<td class="px-3 py-2 text-center text-xs mono">' +
          esc(fmtRange(c.hold_ba_min, c.hold_ba_max, c.heldout_ba_pct)) +
          seedNote +
          '</td>' +
          '<td class="px-3 py-2 text-center">' +
          badge +
          '</td></tr>'
        );
      })
      .join('');
    ledger =
      '<section class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 flex flex-wrap items-center gap-2">' +
      '<h3 class="text-sm font-semibold text-ink">Combination ledger</h3>' +
      '<p class="text-[11px] text-muted">Ranked by holdout balanced accuracy, shown as a RANGE across seeds. ' +
      '<b>Balanced</b> = mean per-class recall, so a prompt that always answers the ' +
      'majority class cannot score high. Certified = holdout 100% on every seed.</p></div>' +
      '<div class="overflow-x-auto rounded-xl border border-line">' +
      '<table class="w-full text-left"><thead class="bg-soft/60 text-xs text-muted"><tr>' +
      '<th class="px-3 py-2">prompt_key</th><th class="px-3 py-2">combination</th>' +
      '<th class="px-3 py-2 text-center">train bal</th>' +
      '<th class="px-3 py-2 text-center">holdout bal</th>' +
      '<th class="px-3 py-2 text-center">verdict</th>' +
      '</tr></thead><tbody>' +
      rows +
      '</tbody></table></div></section>';
  }

  return header + msgBar + axisBlocks + ledger;
}

async function loadSkillDimensions() {
  const d = state.skill.dimensions;
  const skill = state.skill.skillKey || 'mouse_spot_verify';
  const body = $('#workspace-body');
  try {
    const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/dimensions');
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'load failed');
    d.registry = data.dimensions || {};
    d.dimensionCount = data.dimension_count || 0;
    d.comboCount = data.combo_count || 0;
    d.msg = 'Loaded ' + d.dimensionCount + ' axes, ' + d.comboCount + ' combinations.';
    d.msgOk = true;
    try {
      const cr = await fetch('/api/skills/' + encodeURIComponent(skill) + '/combos');
      const cd = await cr.json();
      if (cr.ok && cd.ok) d.combos = cd.combos || [];
    } catch (_) {}
  } catch (e) {
    d.msg = 'Load failed: ' + (e && e.message ? e.message : e);
    d.msgOk = false;
  }
  if (body && state.nav === 'skill-ssot' && state.skill.tab === 'dimensions') {
    body.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
}

function bindSkillDimensions() {
  const d = state.skill.dimensions;
  d.combos = d.combos || [];
  const seed = $('#sk-dim-seed');
  if (seed) {
    seed.addEventListener('click', async () => {
      const skill = state.skill.skillKey || 'mouse_spot_verify';
      try {
        const res = await fetch(
          '/api/skills/' + encodeURIComponent(skill) + '/dimensions?seed=1'
        );
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'seed failed');
        await loadSkillDimensions();
      } catch (e) {
        d.msg = String(e.message || e);
        d.msgOk = false;
        const body = $('#workspace-body');
        if (body && state.nav === 'skill-ssot') {
          body.innerHTML = skillHtml();
          bindSkillSsotExtra();
        }
      }
    });
  }
  const refresh = $('#sk-dim-refresh');
  if (refresh) refresh.addEventListener('click', () => loadSkillDimensions());

  // Sweep PLAN only: shows the train/holdout split and the split's class mix
  // before any model call is spent. A holdout with one class cannot detect a
  // prompt that fails the other class, so this is checked first.
  const plan = $('#sk-dim-plan');
  if (plan) {
    plan.addEventListener('click', async () => {
      const skill = state.skill.skillKey || 'mouse_spot_verify';
      try {
        const res = await fetch(
          '/api/skills/' + encodeURIComponent(skill) + '/sweep/plan'
        );
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'plan failed');
        d.msg =
          'Split: train ' +
          data.train.n +
          ' ' +
          JSON.stringify(data.train.classes) +
          ' | holdout ' +
          data.holdout.n +
          ' ' +
          JSON.stringify(data.holdout.classes) +
          ' | combos ' +
          data.combo_count +
          (data.holdout_can_discriminate
            ? ' — holdout can discriminate.'
            : ' — WARNING: holdout has one class, it cannot detect a prompt that fails the other.');
        d.msgOk = !!data.holdout_can_discriminate;
      } catch (e) {
        d.msg = String(e.message || e);
        d.msgOk = false;
      }
      const body = $('#workspace-body');
      if (body && state.nav === 'skill-ssot') {
        body.innerHTML = skillHtml();
        bindSkillSsotExtra();
      }
    });
  }
}

function skillLibraryHtml() {
  const lib = (state.skill && state.skill.library) || {};
  const cats = lib.catalogs || [];
  const msgBar =
    '<div id="sk-lib-msg" class="mb-3 rounded-xl border border-line bg-soft/60 px-3 py-2 text-xs ' +
    (lib.msgOk === false ? 'text-red-600' : 'text-muted') +
    '">' +
    esc(lib.msg || '') +
    '</div>';
  const header =
    '<div class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="flex flex-wrap items-center justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold">Skill Library</h2>' +
    '<p class="mt-1 text-xs text-muted">Catalog tree scanned from <span class="mono">skills/</span>. Catalog is derived from the FOLDER, not from frontmatter, so a sample catalog_id cannot mis-file a skill.</p></div>' +
    '<div class="flex flex-wrap gap-2">' +
    '<button id="sk-lib-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '<button id="sk-lib-sync" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Rescan skills/</button>' +
    '</div></div>' +
    '<div class="mt-2 flex flex-wrap gap-3 text-xs text-muted">' +
    '<span>catalogs: <b class="text-ink">' +
    esc(String(lib.count || cats.length || 0)) +
    '</b></span>' +
    '<span>skills: <b class="text-ink">' +
    esc(String(lib.skillTotal || 0)) +
    '</b></span>' +
    '</div></div>';

  if (!cats.length) {
    return (
      header +
      msgBar +
      '<div class="rounded-2xl border border-line bg-panel p-8 text-center text-sm text-muted">' +
      '\u672a\u8b80\u5230 catalog\u3002\u6309 Rescan skills/ \u6216\u5148 POST /api/skill-library/sync\u3002' +
      '</div>'
    );
  }

  const catBlocks = cats
    .map((c) => {
      const subs = c.subcatalogs || [];
      const subBlocks = subs
        .map((sub) => {
          const skills = sub.skills || [];
          const rows = skills.length
            ? skills
                .map((s) => {
                  const st = String(s.latest_status || '').toLowerCase();
                  const badge = st
                    ? '<span class="rounded px-1.5 py-0.5 text-[11px] ' +
                      (st === 'active'
                        ? 'bg-green-100 text-green-700'
                        : st === 'draft'
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-soft text-muted') +
                      '">' +
                      esc(st) +
                      '</span>'
                    : '<span class="rounded bg-soft px-1.5 py-0.5 text-[11px] text-muted">no version</span>';
                  return (
                    '<tr class="border-t border-line align-top">' +
                    '<td class="px-3 py-2"><button type="button" data-lib-skill="' +
                    esc(s.skill_id) +
                    '" class="mono text-left text-xs font-medium text-accent hover:underline">' +
                    esc(s.skill_id) +
                    '</button></td>' +
                    '<td class="px-3 py-2 text-xs text-muted">' +
                    esc(s.description || '') +
                    '</td>' +
                    '<td class="px-3 py-2 text-center text-xs mono">' +
                    esc(String(s.version_count || 0)) +
                    '</td>' +
                    '<td class="px-3 py-2 text-center">' +
                    badge +
                    '</td></tr>'
                  );
                })
                .join('')
            : '<tr><td colspan="4" class="px-3 py-4 text-center text-xs text-muted">\u6b64 subcatalog \u7121 skill\u3002</td></tr>';
          return (
            '<div class="mt-3">' +
            '<div class="mb-1 flex items-center gap-2">' +
            '<span class="rounded bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent">' +
            esc(sub.name) +
            '</span>' +
            '<span class="text-[11px] text-muted">' +
            esc(String(skills.length)) +
            ' skill(s)</span></div>' +
            '<div class="overflow-x-auto rounded-xl border border-line">' +
            '<table class="w-full text-left"><thead class="bg-soft/60 text-xs text-muted"><tr>' +
            '<th class="px-3 py-2">skill_id</th><th class="px-3 py-2">description</th>' +
            '<th class="px-3 py-2 text-center">versions</th><th class="px-3 py-2 text-center">latest</th>' +
            '</tr></thead><tbody>' +
            rows +
            '</tbody></table></div></div>'
          );
        })
        .join('');
      return (
        '<section class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="flex flex-wrap items-center gap-2">' +
        '<h3 class="text-sm font-semibold text-ink">' +
        esc(c.name) +
        '</h3>' +
        '<span class="rounded bg-soft px-2 py-0.5 text-[11px] text-muted">catalog ' +
        esc(String(c.order ?? '')) +
        '</span>' +
        '<span class="text-[11px] text-muted">' +
        esc(String(c.skill_count || 0)) +
        ' skill(s)</span></div>' +
        subBlocks +
        '</section>'
      );
    })
    .join('');

  return header + msgBar + catBlocks;
}

async function loadSkillLibrary() {
  const lib = state.skill.library;
  try {
    const res = await fetch('/api/skill-catalogs');
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'load failed');
    lib.catalogs = data.catalogs || [];
    lib.count = data.count || 0;
    lib.skillTotal = data.skill_total || 0;
    lib.msg = '';
    lib.msgOk = true;
  } catch (e) {
    lib.msg = 'Load failed: ' + (e && e.message ? e.message : e);
    lib.msgOk = false;
  }
  const body = $('#workspace-body');
  if (body && state.nav === 'skill-ssot' && state.skill.tab === 'library') {
    body.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
}

async function syncSkillLibrary() {
  const lib = state.skill.library;
  lib.msg = 'Scanning skills/ ...';
  lib.msgOk = true;
  const body = $('#workspace-body');
  if (body && state.nav === 'skill-ssot' && state.skill.tab === 'library') {
    body.innerHTML = skillHtml();
    bindSkillSsotExtra();
  }
  try {
    const res = await fetch('/api/skill-library/sync', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'sync failed');
    lib.msg =
      'Sync ok \u2014 added=' +
      (data.added ?? '?') +
      ' updated=' +
      (data.updated ?? '?') +
      ' total=' +
      (data.total ?? '?');
    lib.msgOk = true;
  } catch (e) {
    lib.msg = 'Sync failed: ' + (e && e.message ? e.message : e);
    lib.msgOk = false;
  }
  await loadSkillLibrary();
}

function skillHtml() {
  const tab = state.skill?.tab || 'editor';
  if (tab === 'dimensions') return skillDimensionsHtml();
  if (tab === 'factors') return skillFactorTableHtml();
  if (tab === 'library') return skillLibraryHtml();
  if (tab === 'contracts') return skillContractsHtml();
  if (tab === 'lifecycle') return skillLifecycleHtml();
  if (tab === 'prompt-trace') return skillPromptTraceHtml();
  if (tab === 'proof') return skillProofHtml();
  const s = state.skill;
  const a = s.active || {};
  const verOpts =
    '<option value="">active</option>' +
    (s.versions || [])
      .map((v) => {
        const lab = v.version_label || '';
        const sel = lab && lab === s.version ? ' selected' : '';
        return (
          '<option value="' +
          esc(lab) +
          '"' +
          sel +
          '>' +
          esc(lab) +
          (v.status ? ' | ' + esc(v.status) : '') +
          '</option>'
        );
      })
      .join('');
  const gate = s.latestTest
    ? s.latestTest.pass_gate
      ? 'PASS'
      : 'FAIL'
    : '-';
  const acc =
    s.latestTest && s.latestTest.accuracy_pct != null
      ? String(s.latestTest.accuracy_pct) + '%'
      : '-';
  return (
    '<div class="mx-auto flex max-w-4xl flex-col gap-4">' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="flex flex-wrap items-start justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold">Skill Prompt SSOT</h2>' +
    '<p class="mt-1 text-sm text-muted">Versioned verify prompt | gold cases | Task Center 10.x | improve->draft only</p></div>' +
    '<div class="flex flex-wrap gap-2 text-xs">' +
    '<span class="rounded-full bg-soft px-2.5 py-1 mono">' +
    esc(a.skill_key || s.skillKey) +
    '</span>' +
    '<span class="rounded-full bg-accent-soft px-2.5 py-1 text-accent mono">' +
    esc(a.version_label || '-') +
    '</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1">' +
    esc(a.status || '-') +
    '</span>' +
    '<span class="rounded-full bg-soft px-2.5 py-1">parser ' +
    esc(a.parser || 'result_yes_no') +
    '</span>' +
    (s.casesCount != null
      ? '<span class="rounded-full bg-soft px-2.5 py-1">gold ' + esc(s.casesCount) + '</span>'
      : '') +
    '<span class="rounded-full ' +
    (gate === 'PASS' ? 'bg-emerald-50 text-emerald-700' : gate === 'FAIL' ? 'bg-rose-50 text-rose-700' : 'bg-soft') +
    ' px-2.5 py-1">gate ' +
    esc(gate) +
    ' | acc ' +
    esc(acc) +
    '</span></div></div>' +
    '<div class="mt-4 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">Skill<select id="sk-pick" class="ml-1 min-w-[160px] rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    (function(){const keys=(state.skill.skillKeys&&state.skill.skillKeys.length)?state.skill.skillKeys:['mouse_spot_verify'];const cur=state.skill.skillKey||'mouse_spot_verify';return keys.map(k=>'<option value="'+esc(k)+'"'+(k===cur?' selected':'')+'>'+esc(k)+'</option>').join('');})() +
    '</select></label>' +
    '<button id="sk-new-skill" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="Create new skill template">+ New template</button>' +
    '<button id="sk-clone" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="New draft from template">From template</button>' +
    '<label class="text-xs font-medium text-muted">Version<select id="sk-ver" class="ml-1 min-w-[140px] rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    verOpts +
    '</select></label>' +
    '<button id="sk-reload" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Reload</button>' +
    '<button id="sk-seed" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Seed v1</button>' +
    '<button id="sk-seed-all" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="prompt + gold + Task Center">Seed all</button>' +
    '<button id="sk-task-lines" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Task IDs</button>' +
    '</div>' +
    '<label class="mt-4 block text-xs font-medium text-muted">Active / draft prompt template' +
    '<textarea id="sk-prompt" rows="10" class="mono mt-1 w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed outline-none focus:ring-2 focus:ring-accent" placeholder="{{target_name}} {{target_action}}">' +
    esc(a.prompt_text || '') +
    '</textarea></label>' +
    '<div class="mt-3 flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">target<input id="sk-target" value="Visual Studio Code" class="ml-1 w-40 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm" /></label>' +
    '<label class="text-xs font-medium text-muted">action<input id="sk-action" value="" placeholder="optional" class="ml-1 w-28 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm" /></label>' +
    '<label class="text-xs font-medium text-muted">expected<select id="sk-expected" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm"><option value="NO" selected>NO</option><option value="YES">YES</option></select></label>' +
    '<label class="text-xs font-medium text-muted">runs<select id="sk-runs" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm"><option value="1">1</option><option value="5">5</option><option value="10" selected>10</option><option value="20">20</option><option value="100">100</option></select></label>' +
    '</div>' +
    '<div class="mt-3 flex flex-wrap gap-2">' +
    '<button id="sk-save-draft" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Save draft</button>' +
    '<button id="sk-improve-draft" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="LLM improve -> draft only">Improve->draft</button>' +
    '<button id="sk-test" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Run proof test</button>' +
    '<button id="sk-test-gold" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Test gold</button>' +
    '<button id="sk-promote" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Promote active</button>' +
    '<button id="sk-to-chat" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">To chat box</button>' +
    '<button id="sk-from-chat" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">From chat</button>' +
    '</div>' +
    '<div class="mt-3 rounded-xl border border-dashed border-line bg-soft/40 p-3">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Catalog case (IDE targets)</div>' +
    '<div class="flex flex-wrap items-end gap-2">' +
    '<label class="text-xs font-medium text-muted">target<select id="sk-case-target" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm">' +
    (state.skill.ideTargets||['Visual Studio Code','Cursor','Work Buddy','Codex']).map(function(n){return '<option value="'+esc(n)+'">'+esc(n)+'</option>';}).join('') +
    '</select></label>' +
    '<label class="text-xs font-medium text-muted">expected<select id="sk-case-expected" class="ml-1 rounded-xl border border-line bg-canvas px-2 py-1.5 text-sm"><option value="NO" selected>NO</option><option value="YES">YES</option></select></label>' +
    '<button id="sk-add-case" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">+ Catalog case</button>' +
    '</div></div>' +
    '<div id="sk-msg" class="mt-2 text-sm text-muted">' +
    esc(s.msg || '') +
    '</div>' +
    '<pre id="sk-log" class="mono mt-3 max-h-64 overflow-auto rounded-xl border border-line bg-soft/80 p-3 text-xs leading-relaxed whitespace-pre-wrap">' +
    esc(s.log || '') +
    '</pre></div></div>'
  );
}

function setSkillMsg(msg, isErr) {
  state.skill.msg = msg || '';
  const el = $('#sk-msg');
  if (el) {
    el.textContent = msg || '';
    el.className = 'mt-2 text-sm ' + (isErr ? 'text-rose-700' : 'text-muted');
  }
  if (msg) toast(msg);
}

function setSkillLog(obj) {
  const text = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  state.skill.log = text;
  const el = $('#sk-log');
  if (el) el.textContent = text;
}

async function loadSkillPanel() {
  const skill = $('#sk-pick')?.value || state.skill.skillKey || 'mouse_spot_verify';
  const version = $('#sk-ver')?.value || state.skill.version || '';
  state.skill.skillKey = skill;
  state.skill.version = version;
  try {
    const q = version ? '?version=' + encodeURIComponent(version) : '';
    const res = await fetch('/api/skills/' + encodeURIComponent(skill) + q);
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'load failed');
    state.skill.active = data.active || null;
    state.skill.versions = data.versions || [];
    state.skill.latestTest = data.latest_test || null;
    try {
      const lr = await fetch('/api/skills');
      const ld = await lr.json();
      if (lr.ok && ld.ok) {
        const keys = ld.skill_keys || [];
        const fromRows = (ld.skills || []).map((r) => r.skill_key).filter(Boolean);
        state.skill.skillKeys = Array.from(new Set([...(keys || []), ...fromRows, skill]));
        if (ld.ide_targets && ld.ide_targets.length) state.skill.ideTargets = ld.ide_targets;
        const pick = $('#sk-pick');
        if (pick) {
          const cur = skill;
          pick.innerHTML = state.skill.skillKeys
            .map(
              (k) =>
                '<option value="' +
                esc(k) +
                '"' +
                (k === cur ? ' selected' : '') +
                '>' +
                esc(k) +
                '</option>'
            )
            .join('');
        }
      }
    } catch (_) {}
    if ($('#sk-prompt') && data.active && data.active.prompt_text != null) {
      $('#sk-prompt').value = data.active.prompt_text;
    }
    try {
      const cr = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/cases'
      );
      const cd = await cr.json();
      if (cr.ok && cd.ok) state.skill.casesCount = cd.count;
    } catch {
      /* ignore */
    }
    setSkillMsg(
      'Loaded ' + ((data.active && data.active.version_label) || skill)
    );
    if (data.latest_test) setSkillLog(data.latest_test);
    // refresh version select without full remount when possible
    const ver = $('#sk-ver');
    if (ver) {
      const cur = version;
      ver.innerHTML =
        '<option value="">active</option>' +
        (state.skill.versions || [])
          .map((v) => {
            const lab = v.version_label || '';
            return (
              '<option value="' +
              esc(lab) +
              '"' +
              (lab === cur ? ' selected' : '') +
              '>' +
              esc(lab) +
              (v.status ? ' | ' + esc(v.status) : '') +
              '</option>'
            );
          })
          .join('');
    }
  } catch (e) {
    setSkillMsg(String(e.message || e), true);
  }
}

function bindSkillPanel() {
  $('#sk-reload')?.addEventListener('click', () => loadSkillPanel());
  $('#sk-pick')?.addEventListener('change', () => {
    state.skill.version = '';
    if ($('#sk-ver')) $('#sk-ver').value = '';
    loadSkillPanel();
  });
  $('#sk-ver')?.addEventListener('change', () => {
    state.skill.version = $('#sk-ver')?.value || '';
    loadSkillPanel();
  });

  $('#sk-seed')?.addEventListener('click', async () => {
    setSkillMsg('Seeding...');
    try {
      const res = await fetch('/api/skills/seed', { method: 'POST' });
      const data = await res.json();
      if (!res.ok && !data.ok && !data.seeded) throw new Error(data.error || 'seed failed');
      setSkillLog(data);
      setSkillMsg('Seeded v1_strict');
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-seed-all')?.addEventListener('click', async () => {
    setSkillMsg('Seeding all (prompt + gold + Task Center)...');
    try {
      const res = await fetch('/api/skills/seed-all', { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'seed-all failed');
      setSkillLog(data);
      const nGold = (data.gold && data.gold.seeded) || 0;
      setSkillMsg('Seed all ok | gold ' + nGold);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-task-lines')?.addEventListener('click', async () => {
    setSkillMsg('Loading Task IDs...');
    try {
      const res = await fetch('/api/skills/task-lines');
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'task-lines failed');
      setSkillLog('Root ' + (data.root || 10) + '\n' + (data.lines || []).join('\n'));
      setSkillMsg('Task IDs | ' + (data.lines || []).length + ' items');
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-save-draft')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const prompt_text = $('#sk-prompt')?.value || '';
    if (!prompt_text.trim()) return setSkillMsg('Prompt empty', true);
    let version_label = $('#sk-ver')?.value || '';
    const activeLab = state.skill.active && state.skill.active.version_label;
    if (!version_label || version_label === activeLab) {
      version_label = 'draft_' + isoStamp();
    }
    setSkillMsg('Saving ' + version_label + '...');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt_text,
          version_label,
          parser: 'result_yes_no',
          source: 'llm_tasks_spa',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'save failed');
      setSkillLog(data.skill || data);
      state.skill.version = version_label;
      setSkillMsg('Saved draft ' + version_label);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-improve-draft')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const prompt_text = ($('#sk-prompt')?.value || $('#f-prompt')?.value || '').trim();
    const btn = $('#sk-improve-draft');
    if (btn) btn.disabled = true;
    setSkillMsg('Improving -> draft only (never auto-promote)...');
    try {
      const body = { source: 'llm_tasks_spa' };
      if (prompt_text) body.prompt_text = prompt_text;
      const res = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/improve-draft',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      );
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'improve failed');
      setSkillLog(data);
      const ver = data.version_label || (data.row && data.row.version_label) || '';
      if (data.row && data.row.prompt_text && $('#sk-prompt')) {
        $('#sk-prompt').value = data.row.prompt_text;
      }
      if (ver) state.skill.version = ver;
      setSkillMsg('Draft saved ' + ver + ' | promote still gated');
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $('#sk-test')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label) ||
      null;
    const runs = parseInt($('#sk-runs')?.value || '10', 10);
    const expected = $('#sk-expected')?.value || 'NO';
    const target_name = $('#sk-target')?.value || 'Visual Studio Code';
    const target_action = $('#sk-action')?.value || '';
    const btn = $('#sk-test');
    if (btn) btn.disabled = true;
    setSkillMsg('Running ' + runs + '-time proof test...');
    setSkillLog('Testing...');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version_label: version,
          runs,
          expected,
          target_name,
          target_action,
        }),
      });
      const data = await res.json();
      if (!res.ok && data.yes == null) throw new Error(data.error || 'test failed');
      setSkillLog(data);
      setSkillMsg(
        'Done | Yes ' +
          data.yes +
          ' | No ' +
          data.no +
          ' | acc ' +
          data.accuracy_pct +
          '% | gate ' +
          (data.pass_gate ? 'PASS' : 'FAIL'),
        !data.pass_gate
      );
      await refreshAll();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $('#sk-test-gold')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label) ||
      null;
    const runs = parseInt($('#sk-runs')?.value || '1', 10);
    const btn = $('#sk-test-gold');
    if (btn) btn.disabled = true;
    setSkillMsg('Running gold suite...');
    try {
      const res = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/test-gold',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version_label: version, runs_per_case: runs }),
        }
      );
      const data = await res.json();
      if (!res.ok && data.ok == null) throw new Error(data.error || 'test-gold failed');
      setSkillLog(data);
      setSkillMsg(
        'Gold | pass ' +
          (data.cases_passed || 0) +
          '/' +
          (data.cases_total || 0),
        !data.ok
      );
      await refreshAll();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    } finally {
      if (btn) btn.disabled = false;
    }
  });

  $('#sk-promote')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label);
    if (!version) return setSkillMsg('Pick a version to promote', true);
    if (!confirm('Promote ' + version + ' to active?\nRequires pass_gate unless you force after.')) return;
    setSkillMsg('Promoting ' + version + '...');
    try {
      let res = await fetch(
        '/api/skills/' + encodeURIComponent(skill) + '/activate',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ version_label: version, require_pass_gate: true }),
        }
      );
      let data = await res.json();
      if (!res.ok || !data.ok) {
        if (!confirm((data.error || 'blocked') + '\n\nForce promote anyway?')) {
          setSkillMsg(data.error || 'blocked', true);
          return;
        }
        res = await fetch(
          '/api/skills/' + encodeURIComponent(skill) + '/activate',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              version_label: version,
              force: true,
              require_pass_gate: false,
            }),
          }
        );
        data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'force failed');
      }
      setSkillLog(data.active || data);
      state.skill.version = '';
      setSkillMsg('Active -> ' + version);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-new-skill')?.addEventListener('click', async () => {
    const name = prompt('New skill_key (letters/digits/underscore):', 'ide_spot_verify');
    if (!name) return;
    const from = $('#sk-pick')?.value || 'mouse_spot_verify';
    setSkillMsg('Creating ' + name + '...');
    try {
      const res = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_key: name.trim(),
          from_skill: from,
          version_label: 'v1_draft',
          source: 'llm_tasks_spa',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'create failed');
      setSkillLog(data);
      state.skill.skillKey = name.trim();
      if (!state.skill.skillKeys.includes(name.trim())) state.skill.skillKeys.push(name.trim());
      setSkillMsg('Created template ' + name.trim());
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-clone')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const from_version =
      $('#sk-ver')?.value ||
      (state.skill.active && state.skill.active.version_label) ||
      '';
    const version_label =
      'draft_' + isoStamp();
    setSkillMsg('Cloning ' + (from_version || 'active') + ' -> ' + version_label + '...');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version_label,
          from_version: from_version || undefined,
          prompt_text: $('#sk-prompt')?.value || undefined,
          source: 'llm_tasks_spa_clone',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'clone failed');
      setSkillLog(data.skill || data);
      state.skill.version = version_label;
      setSkillMsg('Draft from template | ' + version_label);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-add-case')?.addEventListener('click', async () => {
    const skill = $('#sk-pick')?.value || 'mouse_spot_verify';
    const target_name = $('#sk-case-target')?.value || 'Visual Studio Code';
    const expected = $('#sk-case-expected')?.value || 'NO';
    setSkillMsg('Adding catalog case ' + target_name + '...');
    try {
      const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_name,
          expected,
          target_action: 'open',
          source: 'llm_tasks_spa',
        }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'case failed');
      setSkillLog(data.case || data);
      setSkillMsg('Catalog case | ' + target_name + ' | ' + expected);
      await loadSkillPanel();
    } catch (e) {
      setSkillMsg(String(e.message || e), true);
    }
  });

  $('#sk-to-chat')?.addEventListener('click', () => {
    const text = $('#sk-prompt')?.value || '';
    state.draft.prompt_content = text;
    state.nav = 'task-center';
    state.tab = 'chat';
    mount(false);
    toast('Copied skill prompt -> chat');
  });

  $('#sk-from-chat')?.addEventListener('click', () => {
    const text = state.draft.prompt_content || $('#f-prompt')?.value || '';
    if ($('#sk-prompt')) $('#sk-prompt').value = text;
    setSkillMsg('Loaded chat -> skill editor');
  });
}

async function loadSkillTimeline(taskId) {
  const tid = String(taskId || state.skill.timelineTaskId || '').trim();
  state.skill.timelineTaskId = tid;
  if (!tid) {
    state.skill.timelineMsg = 'task_id required';
    return;
  }
  state.skill.timelineMsg = 'Loading ' + tid + '...';
  try {
    const res = await fetch('/api/tasks/' + encodeURIComponent(tid) + '/timeline');
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || 'timeline failed');
    state.skill.lifecycleEvents = data.timeline || [];
    state.skill.promptTraces = data.traces || [];
    state.skill.timelineMsg =
      'lifecycle ' +
      (data.lifecycle_count || 0) +
      ' | traces ' +
      (data.trace_count || 0);
  } catch (e) {
    state.skill.timelineMsg = String(e.message || e);
    state.skill.lifecycleEvents = [];
    state.skill.promptTraces = [];
  }
}

function bindSkillSsotExtra() {
  document.querySelectorAll('[data-skill-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.skill.tab = btn.getAttribute('data-skill-tab') || 'editor';
      try {
        history.pushState(
          { nav: 'skill-ssot', tab: state.skill.tab },
          '',
          navPath('skill-ssot', state.skill.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });

  // Prompt Dimensions tab: axes + combination ledger + sweep plan.
  if (state.skill?.tab === 'dimensions') {
    bindSkillDimensions();
  }

  // Factors tab: the skill rendered as its LAW TABLE (factors + measured state).
  if (state.skill?.tab === 'factors') {
    const pick = $('#skf-pick');
    if (pick) {
      pick.addEventListener('change', () => {
        state.skill.skillKey = pick.value;
        loadSkillFactors();
      });
    }
    const refresh = $('#skf-refresh');
    if (refresh) refresh.addEventListener('click', () => loadSkillFactors());
    loadSkillFactors();
  }

  // Skill Contracts tab (DB-driven contract SSOT)
  $('#sk-contracts-refresh')?.addEventListener('click', () => loadSkillContracts());  document.querySelectorAll('[data-contract]').forEach((tr) => {
    tr.addEventListener('click', () => {
      loadSkillContracts(tr.getAttribute('data-contract'));
    });
  });
  $('#sk-contracts-run-tdd')?.addEventListener('click', (ev) => {
    ev.stopPropagation();
    runSkillContractTdd(ev.currentTarget.getAttribute('data-contract'));
  });

  // Skill Library tab (catalog tree scanned from skills/)
  $('#sk-lib-refresh')?.addEventListener('click', () => loadSkillLibrary());
  $('#sk-lib-sync')?.addEventListener('click', () => syncSkillLibrary());
  document.querySelectorAll('[data-lib-skill]').forEach((btn) => {
    btn.addEventListener('click', () => {
      // Jump to the Editor tab with this skill loaded.
      state.skill.tab = 'editor';
      state.skill.skillKey = btn.getAttribute('data-lib-skill');
      try {
        history.pushState(
          { nav: 'skill-ssot', tab: 'editor' },
          '',
          navPath('skill-ssot', 'editor')
        );
      } catch (_) {}
      mount(false);
    });
  });

  const syncTid = (sel) => {
    const v = $(sel)?.value;
    if (v != null) state.skill.timelineTaskId = v;
  };

  $('#lc-load')?.addEventListener('click', async () => {
    syncTid('#lc-task-id');
    await loadSkillTimeline(state.skill.timelineTaskId);
    const body = $('#workspace-body');
    if (body && state.nav === 'skill-ssot') {
      body.innerHTML = skillHtml();
      bindSkillPanel();
      bindSkillSsotExtra();
    }
  });
  $('#pt-load')?.addEventListener('click', async () => {
    syncTid('#pt-task-id');
    await loadSkillTimeline(state.skill.timelineTaskId);
    const body = $('#workspace-body');
    if (body && state.nav === 'skill-ssot') {
      body.innerHTML = skillHtml();
      bindSkillPanel();
      bindSkillSsotExtra();
    }
  });

  const seedLc = async () => {
    try {
      const res = await fetch('/api/skills/seed-lifecycle', { method: 'POST' });
      const data = await res.json();
      setSkillLog(data);
      state.skill.timelineMsg = data.ok ? 'Lifecycle skills seeded (T-SKILL02)' : data.error || 'seed failed';
      toast(state.skill.timelineMsg);
      await loadSkillPanel();
    } catch (e) {
      state.skill.timelineMsg = String(e.message || e);
      toast(state.skill.timelineMsg);
    }
  };
  $('#lc-seed')?.addEventListener('click', seedLc);
  $('#sk-proof-seed-lc')?.addEventListener('click', seedLc);
  $('#sk-proof-to-editor')?.addEventListener('click', () => {
    state.skill.tab = 'editor';
    mount(false);
  });
}


const TASK_ID_TYPE_LABEL = {
  F: 'Function',
  A: 'API',
  T: 'Table',
  D: 'Field',
  J: 'Job',
  E: 'Event',
  S: 'Skill',
};

function setSkillLearnMsg(msg, isErr) {
  state.skillLearn.msg = msg || '';
  state.skillLearn.msgOk = !isErr;
}

async function refreshSkillLearning(selectedKeep = true) {
  const s = state.skillLearn;
  try {
    const res = await fetch('/api/learning/overview');
    s.overview = (await res.json()) || null;
    s.msg = '';
    s.msgOk = true;
  } catch (e) {
    s.msg = 'Learning API unavailable: ' + (e.message || e);
    s.msgOk = false;
  }
  if (s.tab === 'mismatches') {
    try {
      const res = await fetch('/api/learning/mismatches?limit=200');
      const data = await res.json();
      s.mismatches = data.items || [];
    } catch {
      /* keep previous */
    }
  }
  if (s.tab === 'lessons') {
    try {
      const res = await fetch('/api/learning/lessons?limit=200');
      const data = await res.json();
      s.lessons = data.items || [];
    } catch {
      /* keep previous */
    }
  }
  if (s.tab === 'candidates') {
    try {
      const res = await fetch('/api/learning/candidates');
      const data = await res.json();
      s.candidates = data.items || [];
    } catch {
      /* keep previous */
    }
  }
}

function slMsgHtml() {
  const s = state.skillLearn;
  if (!s.msg) return '';
  return (
    '<p class="mb-2 text-sm ' +
    (s.msgOk ? 'text-emerald-600' : 'text-rose-600') +
    '">' +
    esc(s.msg) +
    '</p>'
  );
}

function slSkillKeySelect() {
  const s = state.skillLearn;
  return (
    '<label class="block text-xs font-medium text-muted">skill_key' +
    '<input id="sl-skill-key" type="text" value="' +
    esc(s.skillKey) +
    '" class="mono mt-1 w-72 rounded-xl border border-line bg-canvas px-3 py-2 text-sm" /></label>'
  );
}

function slOverviewHtml() {
  const o = state.skillLearn.overview || {};
  const card = (label, value) =>
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<p class="text-xs text-muted">' +
    label +
    '</p><p class="mt-1 text-2xl font-semibold">' +
    esc(value ?? '-') +
    '</p></div>';
  return (
    '<div class="mx-auto max-w-4xl">' +
    slMsgHtml() +
    '<div class="grid grid-cols-2 gap-3 sm:grid-cols-3">' +
    card('Mismatches to review', o.mismatches_to_review) +
    card('Mismatches total', o.mismatches_total) +
    card('Lessons (self_fail)', o.lessons_self_fail) +
    card('Lessons (github)', o.lessons_github) +
    card('Lessons merged', o.lessons_merged) +
    card('Candidate versions', o.candidates) +
    '</div>' +
    '<p class="mt-3 text-sm text-muted">Skills with failures: ' +
    esc((o.skills_with_failures || []).join(', ') || '(none)') +
    '</p></div>'
  );
}

function slMismatchesHtml() {
  const rows = (state.skillLearn.mismatches || []).map(
    (m) =>
      '<tr class="border-t border-line">' +
      '<td class="px-2 py-2">' +
      esc(m.id) +
      '</td><td class="px-2 py-2">' +
      esc(m.skill_key) +
      '</td><td class="px-2 py-2">' +
      esc(m.version_label || '-') +
      '</td><td class="px-2 py-2">' +
      esc(m.target_name || '-') +
      '</td><td class="px-2 py-2">' +
      esc(m.ask_output || '-') +
      '</td><td class="px-2 py-2">' +
      esc(m.expected || '-') +
      '</td><td class="px-2 py-2">' +
      esc(m.status) +
      '</td><td class="px-2 py-2">' +
      esc(fmtLocal(m.created_at)) +
      '</td><td class="px-2 py-2">' +
      (m.status === 'to_review'
        ? '<button type="button" data-mm-act="promote_to_gold" data-mm-id="' +
          m.id +
          '" class="mr-1 rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">Promote</button>' +
          '<button type="button" data-mm-act="reject" data-mm-id="' +
          m.id +
          '" class="rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">Reject</button>'
        : '') +
      '</td></tr>'
  ).join('');
  return (
    '<div class="mx-auto max-w-6xl">' +
    slMsgHtml() +
    '<div class="mb-3 flex items-center gap-2">' +
    '<button type="button" id="btn-sl-mm-refresh" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '<span class="text-xs text-muted">Promote = 加入 gold case（expected 必須係 YES/NO）</span></div>' +
    '<div class="overflow-auto rounded-2xl border border-line bg-panel shadow-panel">' +
    '<table class="w-full text-left text-sm">' +
    '<thead><tr class="text-xs text-muted"><th class="px-2 py-2">id</th><th class="px-2 py-2">skill</th><th class="px-2 py-2">version</th><th class="px-2 py-2">target</th><th class="px-2 py-2">ask</th><th class="px-2 py-2">expected</th><th class="px-2 py-2">status</th><th class="px-2 py-2">created</th><th class="px-2 py-2">actions</th></tr></thead>' +
    '<tbody>' +
    (rows || '<tr><td colspan="9" class="px-2 py-4 text-center text-muted">No mismatches</td></tr>') +
    '</tbody></table></div></div>'
  );
}

function slLessonsHtml() {
  const s = state.skillLearn;
  const rows = (s.lessons || []).map(
    (l) =>
      '<tr class="border-t border-line align-top">' +
      '<td class="px-2 py-2">' +
      esc(l.id) +
      '</td><td class="px-2 py-2">' +
      esc(l.skill_key) +
      '</td><td class="px-2 py-2">' +
      esc(l.source_type) +
      '</td><td class="px-2 py-2">' +
      esc(l.status) +
      '</td><td class="px-2 py-2 max-w-md">' +
      esc(l.lesson_text) +
      '</td><td class="px-2 py-2 max-w-xs">' +
      esc(l.root_cause || '') +
      '</td><td class="px-2 py-2 max-w-xs">' +
      esc(l.suggested_fix || '') +
      '</td><td class="px-2 py-2">' +
      esc(l.source_ref || '') +
      '</td><td class="px-2 py-2">' +
      '<button type="button" data-lesson-rewrite="' +
      esc(l.lesson_key) +
      '" data-lesson-skill="' +
      esc(l.skill_key) +
      '" class="whitespace-nowrap rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">Rewrite prompt</button>' +
      '</td></tr>'
  ).join('');
  return (
    '<div class="mx-auto max-w-6xl">' +
    slMsgHtml() +
    '<div class="mb-3 flex flex-wrap items-end gap-3">' +
    slSkillKeySelect() +
    '<label class="block text-xs font-medium text-muted">lesson_text' +
    '<textarea id="sl-lesson-text" rows="2" class="mt-1 w-96 rounded-xl border border-line bg-canvas px-3 py-2 text-sm">' +
    esc(s.lessonText) +
    '</textarea></label>' +
    '<button type="button" id="btn-sl-lesson-add" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Add lesson</button>' +
    '<button type="button" id="btn-sl-lesson-refresh" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button></div>' +
    '<div class="overflow-auto rounded-2xl border border-line bg-panel shadow-panel">' +
    '<table class="w-full text-left text-sm">' +
    '<thead><tr class="text-xs text-muted"><th class="px-2 py-2">id</th><th class="px-2 py-2">skill</th><th class="px-2 py-2">source</th><th class="px-2 py-2">status</th><th class="px-2 py-2">lesson</th><th class="px-2 py-2">root_cause</th><th class="px-2 py-2">suggested_fix</th><th class="px-2 py-2">source_ref</th><th class="px-2 py-2">actions</th></tr></thead>' +
    '<tbody>' +
    (rows || '<tr><td colspan="9" class="px-2 py-4 text-center text-muted">No lessons</td></tr>') +
    '</tbody></table></div></div>'
  );
}

function slIngestHtml() {
  const s = state.skillLearn;
  return (
    '<div class="mx-auto max-w-4xl">' +
    slMsgHtml() +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="text-sm font-semibold">Case study ingest (self_fail)</h3>' +
    '<p class="mt-1 text-xs text-muted">讀最近 is_wrong=1 推論 + to_review mismatch → 本地 7B case study → 寫 lesson + 候選 draft 版本</p>' +
    '<div class="mt-3 flex flex-wrap items-end gap-3">' +
    slSkillKeySelect() +
    '<label class="block text-xs font-medium text-muted">limit' +
    '<input id="sl-ingest-limit" type="number" value="50" class="mono mt-1 w-24 rounded-xl border border-line bg-canvas px-3 py-2 text-sm" /></label>' +
    '<button type="button" id="btn-sl-ingest" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Run case study</button></div>' +
    '<label class="mt-4 block text-xs font-medium text-muted">result</label>' +
    '<pre id="sl-ingest-out" class="mt-1 max-h-72 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs mono whitespace-pre-wrap">' +
    esc(s.ingestOut || '(no run yet)') +
    '</pre></section>' +
    '<section class="mt-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="text-sm font-semibold">GitHub lesson import</h3>' +
    '<p class="mt-1 text-xs text-muted">貼文檔片段或 URL → 本地 7B 提取結構化 lesson（source_type=github_proofed_lesson）</p>' +
    '<div class="mt-3 flex flex-wrap items-center gap-3">' +
    slSkillKeySelect() +
    '<label class="flex items-center gap-1 text-xs text-muted"><input id="sl-import-url" type="checkbox" ' +
    (s.importUrl ? 'checked' : '') +
    ' /> is_url</label></div>' +
    '<textarea id="sl-import-text" rows="5" class="mono mt-2 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm" placeholder="paste GitHub doc / lesson text, or a URL">' +
    esc(s.importText) +
    '</textarea>' +
    '<button type="button" id="btn-sl-import" class="mt-2 rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Import lessons</button></section></div>'
  );
}

function slCandidatesHtml() {
  const rows = (state.skillLearn.candidates || []).map(
    (c) => {
      // Merge is only offered when the candidate has EARNED it: a recorded test
      // run with pass_gate=1. An enabled Merge button on an untested draft is
      // how a false success walks in. The backend guard is the real protection;
      // this just stops the UI from inviting it.
      const run = c.latest_run || null;
      const qualified = !!(run && Number(run.pass_gate) === 1);
      const runTxt = run
        ? 'run=' +
          esc(run.run_id || '-') +
          ' | pass_gate=' +
          esc(run.pass_gate ?? '-') +
          ' | acc=' +
          esc(run.accuracy_pct ?? '-') +
          '% | n=' +
          esc(run.n_runs ?? '-')
        : 'no test run yet';
      return (
        '<tr class="border-t border-line align-top">' +
        '<td class="px-2 py-2">' +
        esc(c.skill_key) +
        '</td><td class="px-2 py-2">' +
        esc(c.version_label) +
        '</td><td class="px-2 py-2">' +
        esc(c.status) +
        '</td><td class="px-2 py-2">' +
        esc(c.source || '') +
        '</td><td class="px-2 py-2 text-xs">' +
        (qualified
          ? '<span class="rounded bg-green-100 px-1.5 py-0.5 text-green-700">qualified</span> '
          : '<span class="rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">not qualified</span> ') +
        runTxt +
        '</td><td class="px-2 py-2 whitespace-nowrap">' +
        '<button type="button" data-cand-act="test" data-cand-skill="' +
        esc(c.skill_key) +
        '" data-cand-ver="' +
        esc(c.version_label) +
        '" class="mr-1 rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">Test (streak 20)</button>' +
        (qualified
          ? '<button type="button" data-cand-act="merge" data-cand-skill="' +
            esc(c.skill_key) +
            '" data-cand-ver="' +
            esc(c.version_label) +
            '" class="rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">Merge</button>'
          : '<button type="button" disabled title="pass_gate=1 required — run Test first" class="cursor-not-allowed rounded-lg border border-line px-2 py-0.5 text-xs text-muted opacity-50">Merge</button>') +
        '</td></tr>'
      );
    }
  ).join('');
  return (
    '<div class="mx-auto max-w-6xl">' +
    slMsgHtml() +
    '<div class="mb-3 flex items-center gap-2">' +
    '<button type="button" id="btn-sl-cand-refresh" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '<span class="text-xs text-muted">Merge guard: 該版本最新 test_run 必須 pass_gate=1</span></div>' +
    '<div class="overflow-auto rounded-2xl border border-line bg-panel shadow-panel">' +
    '<table class="w-full text-left text-sm">' +
    '<thead><tr class="text-xs text-muted"><th class="px-2 py-2">skill</th><th class="px-2 py-2">version</th><th class="px-2 py-2">status</th><th class="px-2 py-2">source</th><th class="px-2 py-2">latest run</th><th class="px-2 py-2">actions</th></tr></thead>' +
    '<tbody>' +
    (rows || '<tr><td colspan="6" class="px-2 py-4 text-center text-muted">No candidates</td></tr>') +
    '</tbody></table></div></div>'
  );
}

function skillLearningHtml() {
  const tab = state.skillLearn?.tab || 'overview';
  if (tab === 'mismatches') return slMismatchesHtml();
  if (tab === 'lessons') return slLessonsHtml();
  if (tab === 'ingest') return slIngestHtml();
  if (tab === 'candidates') return slCandidatesHtml();
  return slOverviewHtml();
}

async function slHandleBodyClick(e, remount) {
  const mm = e.target.closest('[data-mm-act]');
  if (mm) {
    const id = mm.getAttribute('data-mm-id');
    const status = mm.getAttribute('data-mm-act');
    try {
      const res = await fetch('/api/learning/mismatches/' + id + '/status', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'status update failed');
      setSkillLearnMsg('Mismatch ' + id + ' -> ' + status);
      await remount();
    } catch (err) {
      setSkillLearnMsg(String(err.message || err), true);
    }
    return;
  }
  const cand = e.target.closest('[data-cand-act]');
  if (cand) {
    const act = cand.getAttribute('data-cand-act');
    const skillKey = cand.getAttribute('data-cand-skill');
    const ver = cand.getAttribute('data-cand-ver');
    const url =
      act === 'test'
        ? '/api/learning/candidates/test'
        : '/api/learning/candidates/merge';
    try {
      setSkillLearnMsg(
        act === 'test'
          ? 'Running streak test for ' + ver + ' ... (LLM, may take a while)'
          : 'Merging ' + ver + ' ...'
      );
      await remount();
      // Always force the full 20-run streak from the UI: a shoft streak is not
      // evidence. The backend also defaults to 20, but pass it explicitly so
      // the request records what was actually asked for.
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_key: skillKey, version_label: ver, streak: 20 }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || act + ' failed');
      // `data.ok` is transport success. The actual outcome is result_ok: a
      // refused merge is a successful CALL. Reporting "merge -> ok" for a
      // refusal is exactly the false-success this workset exists to remove.
      if (act === 'merge' && data.result_ok === false) {
        const gate = data.contract_gate || {};
        const why = data.refused_by === 'contract_tdd'
          ? 'contract TDD gate refused (' + (gate.reason || '') + ')'
          : 'test-run gate refused (pass_gate=1 required)';
        setSkillLearnMsg('MERGE REFUSED: ' + why, true);
      } else if (act === 'merge') {
        const gate = data.contract_gate || {};
        const covered = gate.applies
          ? 'contract ' + gate.contract_id + ' TDD ' + (gate.reason || '')
          : 'WARNING: this skill has no contract, so it is NOT covered by a TDD gate';
        setSkillLearnMsg('Merged ' + ver + '. ' + covered);
      } else {
        setSkillLearnMsg(
          act + ' ' + ver + ' -> ' + JSON.stringify(data, null, 0).slice(0, 240)
        );
      }
      await remount();
    } catch (err) {
      setSkillLearnMsg(String(err.message || err), true);
    }
    return;
  }
  // Lesson -> rewrite into a NEW DRAFT candidate. Never activates.
  const lrw = e.target.closest('[data-lesson-rewrite]');
  if (lrw) {
    const lessonKey = lrw.getAttribute('data-lesson-rewrite');
    const skillKey = lrw.getAttribute('data-lesson-skill') || '';
    try {
      setSkillLearnMsg(
        'Rewriting prompt from ' + lessonKey + ' ... (LLM, may take ~30s)'
      );
      await remount();
      const res = await fetch(
        '/api/learning/lessons/' + encodeURIComponent(lessonKey) + '/rewrite',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill_key: skillKey }),
        }
      );
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'rewrite failed');
      // Prove the law held: the response must say draft-only / not activated.
      if (data.activated || data.draft_only === false) {
        setSkillLearnMsg(
          'UNSAFE: rewrite reported activated=' +
            data.activated +
            ' draft_only=' +
            data.draft_only +
            ' — active version must be inspected',
          true
        );
      } else {
        setSkillLearnMsg(
          'Draft candidate created: ' +
            data.candidate_version +
            ' (base ' +
            data.base_version +
            ', not activated). Go to Candidates tab to test 20 streak.'
        );
      }
      await remount();
    } catch (err) {
      setSkillLearnMsg(String(err.message || err), true);
    }
  }
}

function bindSkillLearningPanel() {
  const s = state.skillLearn;
  document.querySelectorAll('[data-sl-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      s.tab = btn.getAttribute('data-sl-tab') || 'overview';
      try {
        history.pushState(
          { nav: 'skill-learning', tab: s.tab },
          '',
          navPath('skill-learning', s.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });
  const remount = async () => {
    await refreshSkillLearning(true);
    const body = $('#workspace-body');
    if (body && state.nav === 'skill-learning') {
      body.innerHTML = skillLearningHtml();
      bindSkillLearningPanel();
    }
  };
  $('#btn-sl-mm-refresh')?.addEventListener('click', remount);
  $('#btn-sl-lesson-refresh')?.addEventListener('click', remount);
  $('#btn-sl-cand-refresh')?.addEventListener('click', remount);
  $('#btn-sl-lesson-add')?.addEventListener('click', async () => {
    const skillKey = $('#sl-skill-key')?.value.trim();
    const lessonText = $('#sl-lesson-text')?.value.trim();
    if (!skillKey || !lessonText) {
      setSkillLearnMsg('skill_key + lesson_text required', true);
      return;
    }
    s.lessonText = lessonText;
    try {
      const res = await fetch('/api/learning/lessons', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill_key: skillKey, lesson_text: lessonText }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'add lesson failed');
      setSkillLearnMsg('Lesson added: ' + (data.lesson_key || ''));
      s.lessonText = '';
      await remount();
    } catch (e) {
      setSkillLearnMsg(String(e.message || e), true);
    }
  });
  if (!window.__slBoundOnce) {
    window.__slBoundOnce = true;
    $('#workspace-body')?.addEventListener('click', async (e) => {
      await slHandleBodyClick(e, remount);
    });
  }
  $('#btn-sl-ingest')?.addEventListener('click', async () => {
    const skillKey = $('#sl-skill-key')?.value.trim();
    if (!skillKey) {
      setSkillLearnMsg('skill_key required', true);
      return;
    }
    s.busy = true;
    setSkillLearnMsg('Running case study... (LLM, may take ~30s)');
    try {
      const res = await fetch('/api/learning/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          skill_key: skillKey,
          limit: parseInt($('#sl-ingest-limit')?.value || '50', 10),
        }),
      });
      const data = await res.json();
      s.ingestOut = JSON.stringify(data, null, 2);
      if (!data.ok) throw new Error(data.error || 'ingest failed');
      setSkillLearnMsg(
        'Ingest done | lesson=' +
          (data.lesson_key || '(none)') +
          ' | candidate=' +
          (data.candidate_version || '(none)')
      );
    } catch (e) {
      setSkillLearnMsg(String(e.message || e), true);
    } finally {
      s.busy = false;
      const body = $('#workspace-body');
      if (body && state.nav === 'skill-learning') {
        body.innerHTML = skillLearningHtml();
        bindSkillLearningPanel();
      }
    }
  });
  $('#btn-sl-import')?.addEventListener('click', async () => {
    const skillKey = $('#sl-skill-key')?.value.trim();
    const text = $('#sl-import-text')?.value.trim();
    if (!skillKey || !text) {
      setSkillLearnMsg('skill_key + text/url required', true);
      return;
    }
    s.importText = text;
    s.importUrl = !!$('#sl-import-url')?.checked;
    setSkillLearnMsg('Importing... (LLM, may take ~30s)');
    try {
      const res = await fetch('/api/learning/github-import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text_or_url: text,
          skill_key: skillKey,
          is_url: s.importUrl,
        }),
      });
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'import failed');
      setSkillLearnMsg('Imported ' + (data.imported ?? 0) + ' lessons');
      s.importText = '';
      await remount();
    } catch (e) {
      setSkillLearnMsg(String(e.message || e), true);
    }
  });
  if (s.tab !== 'overview') {
    refreshSkillLearning(true).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'skill-learning') {
        body.innerHTML = skillLearningHtml();
        bindSkillLearningPanel();
      }
    });
  }
}

function levelBadge(level, kind) {
  const lv = String(level || kind || 'info').toLowerCase();
  if (lv === 'alert' || lv.includes('down') || lv.includes('fail')) {
    return '<span class="inline-flex items-center rounded-full bg-rose-50 px-2 py-0.5 text-[11px] font-semibold text-rose-700">ALERT</span>';
  }
  if (lv === 'warn' || lv.includes('restart')) {
    return '<span class="inline-flex items-center rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">WARN</span>';
  }
  if (lv === 'ok' || lv.includes('ready') || lv.includes('recover') || lv.includes('up')) {
    return '<span class="inline-flex items-center rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">OK</span>';
  }
  return '<span class="inline-flex items-center rounded-full bg-soft px-2 py-0.5 text-[11px] font-medium text-muted">INFO</span>';
}

// ---------------------------------------------------------------------------
// User Environment — DB-driven who/where (user + IP + computer + locale)
// SSOT = user_environment table via /api/user_environment
// ---------------------------------------------------------------------------

// The PINNED SESSION AREA. SSOT = /api/user_environment/sessions, which joins
// coordinate_session -> identity_registry -> environment_template. The count and
// the rows come from the SAME response, so a count can never be shown without
// its rows.
//
// `&live=1` MEASURES NOW. THE HUMAN (2026-09-27): *"what is meaning for `4 rows
// at the pinned area` + `[4, 1, 4]` + `DIFFERENT population`, but i have 5..."*
// MEASURED: the recorded run was 19 hours old (`2026-09-26 18:17:22` vs
// `2026-09-27 13:31`) and the page had no live path, so it showed a stale 4
// while the screen said 5. A recorded measurement is EVIDENCE of what was true
// THEN; it is not a reading of NOW. The live reading WINS, and the recorded one
// is kept beside it so the two can be COMPARED rather than one silently
// replacing the other.
async function refreshPinnedSessions(environmentId = 6) {
  const ue = state.userEnvironment;
  try {
    const res = await fetch(
      '/api/user_environment/sessions?environment_id=' +
        encodeURIComponent(environmentId) + '&live=1'
    );
    const data = await res.json();
    ue.pinnedSessions = data || {};
    // A NON-OK ANSWER IS STILL AN ANSWER. `ok:false` carries a `why`, and the
    // renderer shows it — a blank table would read as "no sessions".
    ue.pinnedMsg = data && data.ok ? '' : (data && (data.why || data.error)) || '';
    ue.pinnedLoaded = true;
  } catch (e) {
    ue.pinnedSessions = { ok: false, why: 'Pinned sessions API unavailable: ' + (e.message || e) };
    ue.pinnedMsg = ue.pinnedSessions.why;
    ue.pinnedLoaded = true;
  }
}

async function refreshUserEnvironment() {
  const ue = state.userEnvironment;
  try {
    const res = await fetch('/api/user_environment/list?limit=50');
    const data = await res.json();
    if (!res.ok || !data.ok) {
      ue.msg = (data && (data.error || data.message)) || 'HTTP ' + res.status;
      ue.msgOk = false;
      return;
    }
    ue.rows = data.rows || [];
    ue.total = data.total || 0;
    // The list carries the same row; keep the shared formatter in step with it.
    if (ue.rows.length && ue.rows[0].tz_offset_sec != null) {
      setTzOffsetSec(ue.rows[0].tz_offset_sec);
    }
    ue.msg = '';
    ue.msgOk = true;
  } catch (e) {
    ue.msg = 'User Environment API unavailable: ' + (e.message || e);
    ue.msgOk = false;
  }
}

async function userEnvironmentDetect(save) {
  const ue = state.userEnvironment;
  try {
    const res = await fetch('/api/user_environment', {
      method: save ? 'POST' : 'GET',
      headers: save ? { 'Content-Type': 'application/json' } : undefined,
      body: save ? JSON.stringify({}) : undefined,
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      ue.msg = (data && (data.error || data.message)) || 'HTTP ' + res.status;
      ue.msgOk = false;
      return;
    }
    ue.env = data.environment || null;
    setTzOffsetSec(ue.env && ue.env.tz_offset_sec);
    ue.msg = save ? ('Saved (' + (data.action || 'ok') + ')') : '';
    ue.msgOk = true;
    await refreshUserEnvironment();
  } catch (e) {
    ue.msg = 'User Environment API unavailable: ' + (e.message || e);
    ue.msgOk = false;
  }
}

function ueField(label, value) {
  return (
    '<div class="flex gap-2 py-1 text-sm"><span class="w-36 shrink-0 text-muted">' + label +
    '</span><span class="mono break-all">' + esc(value == null || value === '' ? '-' : String(value)) + '</span></div>'
  );
}

// Same row, but the value is TRUSTED HTML (already escaped by the caller).
// Needed for the flag <img> and for a formatted UTC offset, which the plain
// ueField() would escape into visible markup.
function ueFieldHtml(label, html) {
  return (
    '<div class="flex gap-2 py-1 text-sm"><span class="w-36 shrink-0 text-muted">' + label +
    '</span><span class="mono break-all">' + html + '</span></div>'
  );
}

// THE RUNNING PROOF, per environment_id, from the Windows Task Manager.
// THE HUMAN (2026-09-25): "how to proof it is running, is by windows task to
// proof does environment id is running at detect computer ... never = not
// channel id!!!". So each row is keyed by environment_id and shows the
// process it was measured from. An environment with no app row is UNKNOWN.
function envRunningHtml(envs) {
  const list = Array.isArray(envs) ? envs : [];
  if (!list.length) {
    return '<div class="mt-3 rounded-xl border border-line bg-soft/50 p-3 text-xs text-muted">' +
      'No environment declared in <span class="mono">working_environment</span>.</div>';
  }
  const rows = list.map((x) => {
    const v = String(x.status || 'UNKNOWN');
    const cls = v === 'RUNNING' ? 'bg-emerald-100 text-emerald-800'
      : v === 'STOPPED' ? 'bg-rose-100 text-rose-800'
      : 'bg-soft text-muted';
    return '<tr class="border-b border-line">' +
      '<td class="px-3 py-2 mono text-xs">' + esc(x.environment_id) + '</td>' +
      '<td class="px-3 py-2 text-xs">' + esc(x.display || '') + '</td>' +
      '<td class="px-3 py-2"><span class="rounded-full px-2 py-0.5 text-xs ' + cls +
      '" title="' + esc(x.status_why || '') + '">' + esc(v) + '</span></td>' +
      '<td class="px-3 py-2 text-xs text-muted">' + esc(x.status_app || '-') + '</td>' +
      '<td class="px-3 py-2 text-xs text-muted">' +
      esc(x.status_count == null ? '-' : x.status_count) + '</td>' +
      '</tr>';
  }).join('');
  return '<div class="mt-3 rounded-xl border border-line p-3">' +
    '<div class="text-xs font-semibold">Running proof (Windows Task Manager)</div>' +
    '<p class="mt-0.5 text-[11px] text-muted">Keyed by ' +
    '<span class="mono">environment_id</span> — not a channel.</p>' +
    '<div class="mt-2 overflow-x-auto"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-[11px] text-muted"><tr>' +
    '<th class="px-3 py-2">environment_id</th>' +
    '<th class="px-3 py-2">environment</th>' +
    '<th class="px-3 py-2">status</th>' +
    '<th class="px-3 py-2">app</th>' +
    '<th class="px-3 py-2">instances</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div></div>';
}

// Seconds east of UTC -> "UTC+8" / "UTC-3.5" / "UTC".
// ---------------------------------------------------------------------------
// LOCAL TIME — every timestamp renders in the DETECTED timezone.
//
// WHY (the human, 2026-09-25):
//     "display timezone will auto by my timezone as you have detect my computer
//      to have my tz_offset_sec already"
//
// MEASURED, and this is the defect: the DB stores UTC and the UI rendered it
// RAW. The same instant, three ways:
//
//     chat_identity_log.created_at   2026-09-25 06:00:01   (UTC, stored)
//     SQLite CURRENT_TIMESTAMP       2026-09-25 06:25:03   (UTC)
//     SQLite datetime('now','localtime')  2026-09-25 14:25:03  (the human's clock)
//
// The difference is 8 hours — exactly `tz_offset_sec / 3600`. MEASURED: there
// were TEN timestamp render sites and ZERO conversions, and `tz_offset_sec` was
// used ONLY to build a "UTC+8" LABEL. So the UI knew the offset and never
// applied it.
//
// THE OFFSET IS READ, NEVER HARD-CODED. MEASURED: `user_environment.tz_offset_sec`
// is already 28800 (Asia/Hong_Kong), so a literal here would be a second source
// of truth that disagrees the moment the human travels.
//
// AN UNKNOWN OFFSET DOES NOT SILENTLY SHOW UTC. It returns the raw value MARKED,
// so a reader can tell "this is UTC because the offset is unknown" from "this is
// my local time". A silent UTC display is the defect: the human cannot tell
// which clock they are reading.
// ---------------------------------------------------------------------------
function utcOffsetLabel(off) {
  if (off == null || off === '') return '-';
  const n = Number(off);
  if (!Number.isFinite(n)) return '-';
  const hours = n / 3600;
  const sign = hours >= 0 ? '+' : '';
  const shown = Number.isInteger(hours) ? String(hours) : hours.toFixed(1);
  return hours === 0 ? 'UTC' : 'UTC' + sign + shown;
}

// ---------------------------------------------------------------------------
// User Assets — which app a user has, and its state.
// SSOT = user_asset (id, user_id, app_id) + app catalog. Both id-driven:
// user_id is users.user_id (INTEGER), app_id is app.app_id. No alias/slug.
// ---------------------------------------------------------------------------

const ASSET_STATUS_TONE = {
  online: 'bg-emerald-50 text-emerald-700',
  installed: 'bg-sky-50 text-sky-700',
  offline: 'bg-amber-50 text-amber-700',
  missing: 'bg-rose-50 text-rose-700',
  unknown: 'bg-soft text-muted',
};

function assetStatusChip(status) {
  const s = String(status || 'unknown');
  const tone = ASSET_STATUS_TONE[s] || ASSET_STATUS_TONE.unknown;
  return '<span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' + tone + '">' + esc(s) + '</span>';
}

async function refreshUserAssets() {
  const ue = state.userEnvironment;
  try {
    const res = await fetch('/api/user_assets?limit=200');
    const data = await res.json();
    if (!res.ok || !data.ok) {
      ue.syncMsg = (data && (data.error || data.message)) || 'HTTP ' + res.status;
      ue.syncOk = false;
      return;
    }
    ue.assets = data.rows || [];
    ue.assetsTotal = data.total || 0;
  } catch (e) {
    ue.syncMsg = 'User Assets API unavailable: ' + (e.message || e);
    ue.syncOk = false;
  }
}

async function syncUserAssets() {
  const ue = state.userEnvironment;
  try {
    const res = await fetch('/api/user_assets/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      ue.syncMsg = (data && (data.error || data.message)) || 'HTTP ' + res.status;
      ue.syncOk = false;
      return;
    }
    ue.syncMsg = 'Synced ' + data.synced + ' apps (' + data.online + ' online) for user_id ' + data.user_id;
    ue.syncOk = true;
    await refreshUserAssets();
  } catch (e) {
    ue.syncMsg = 'User Assets API unavailable: ' + (e.message || e);
    ue.syncOk = false;
  }
}

// ---------------------------------------------------------------------------
// The PINNED SESSION AREA — one row per session, with its rect.
//
// THE HUMAN (2026-09-27): "workspace exclusive — i have work for that!! but why
// i can't find at my http://127.0.0.1:18765/llm-tasks/user_environment/ ????"
// "example VScode > chat > session" / "1) how many session at the pinned area,
// sample = 5 / 2) register to table for each session? 1 name = 1, 2 name = 2 /
// 3) did have x,y for all session at pinned?"
//
// THE ANSWER: the work existed and this page had NO READER for it. This is the
// reader. SSOT = /api/user_environment/sessions (which joins
// coordinate_session -> identity_registry -> environment_template).
//
// A COUNT WITH NO ROWS IS THE DEFECT THIS CLOSES, so the count and the rows are
// rendered from the SAME response and the count is never shown alone.
// ---------------------------------------------------------------------------
function userEnvironmentSessionsHtml() {
  const ue = state.userEnvironment || {};
  const ps = ue.pinnedSessions || {};
  const loaded = !!ue.pinnedLoaded;
  const msg = ue.pinnedMsg
    ? '<div class="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">' +
      esc(ue.pinnedMsg) + '</div>'
    : '';
  if (!loaded) {
    return (
      '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">Pinned session area</h3>' +
      '<p class="mb-3 text-xs text-muted">Loading…</p>' +
      '<button id="btn-ue-sessions-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-2 text-sm hover:bg-soft">Load</button>' +
      '</section>'
    );
  }
  if (!ps.ok) {
    // AN ABSENT BAND IS NOT AN EMPTY LIST. The API returns ok:false with a why,
    // and the page shows the WHY — a blank table would read as "no sessions".
    return (
      msg +
      '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">Pinned session area</h3>' +
      '<div class="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">' +
      esc(ps.why || 'the pinned band is not measured for this environment') +
      '</div>' +
      '<button id="btn-ue-sessions-refresh" type="button" class="mt-3 rounded-xl border border-line bg-panel px-3 py-2 text-sm hover:bg-soft">Refresh</button>' +
      '</section>'
    );
  }
  const band = ps.band || {};
  // THE ROW IS A BUTTON, NOT A TR. THE HUMAN (2026-09-27): "can onclick to have
  // why = detail report". MEASURED before this: the pinned table had 3 hover-only
  // `title=` attributes and 0 clickable `data-*`, so the "why" was invisible on
  // touch and could not be copied. The pattern already exists in this file
  // (`openTaskDetailPopup`, app.js:6648): backdrop click + ✕ + Escape.
  const rows = (ps.sessions || []).length
    ? ps.sessions
        .map(
          (s) =>
            '<tr class="border-b border-line align-top cursor-pointer transition hover:bg-soft/60" ' +
            'data-ue-row="' + esc(s.pinned_row_no) + '">' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(s.pinned_row_no) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' +
            (s.session_id
              ? esc(s.session_id)
              : '<span class="text-muted">(no session linked)</span>') + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(s.x1) + ', ' + esc(s.y1) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(s.x2) + ', ' + esc(s.y2) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-muted">' + esc(s.cx) + ', ' + esc(s.cy) + '</td>' +
            // THE NUMBER NAMES ITS POPULATION. `coords` is a raw DB column name;
            // MEASURED: 8 means "measured 8 times", NOT "8 sessions".
            '<td class="px-3 py-2 text-xs text-ink">' + esc(s.coord_count) +
            '<div class="text-[11px] text-muted">measurements</div></td>' +
            // THE UNKNOWN NAMES ITS TABLE AND ITS VALUE. MEASURED: the badge
            // said only `no identity row`, so the human asked "without session ID
            // and which value". The session id is in the adjacent column; this
            // badge is about a DIFFERENT table.
            '<td class="px-3 py-2 text-xs">' +
            (s.session_id
              ? (s.identity_known
                ? '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">registered</span>'
                : '<span class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">' +
                  'identity_registry has no row for ' + esc(String(s.session_id).slice(0, 8)) + '…</span>')
              : '<span class="text-muted">-</span>') +
            '</td>' +
            // THE STATUS (2026-09-27). `is_live` is a MEASUREMENT of the
            // session's OWN chatSessions file mtime against LIVE_WINDOW_S —
            // never a guess from `last_seen`, which is a LINK time 16-27h old.
            '<td class="px-3 py-2 text-xs">' +
            (s.session_id
              ? (s.is_live
                ? '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">LIVE</span>'
                : '<span class="rounded-full bg-soft px-2 py-0.5 text-[11px] font-medium text-muted">idle</span>')
              : '<span class="text-muted">-</span>') +
            (s.age_sec != null
              ? '<div class="mt-0.5 text-[11px] text-muted">' + esc(s.age_sec) + 's ago</div>'
              : '') +
            '</td>' +
            // THE ENVIRONMENT (2026-09-27). DERIVED from the coordinate this
            // session was linked to; an underivable one is REPORTED, never blank.
            '<td class="px-3 py-2 text-xs">' +
            (s.environment
              ? '<span class="rounded-full bg-sky-100 px-2 py-0.5 text-[11px] font-medium text-sky-700">' + esc(s.environment) + '</span>'
              : '<span class="text-amber-700">environment underivable from this session\u2019s coordinate</span>') +
            '</td>' +
            '<td class="px-3 py-2 text-xs text-muted whitespace-nowrap">' + esc(fmtLocal(s.last_seen)) +
            '<div class="text-[11px]">link time, not run time</div></td>' +
            '</tr>'
        )
        .join('')
    // THE EMPTY STATE NAMES A NEXT ACTION. A blank table reads as "nothing
    // exists" rather than "nothing measured yet".
    : '<tr><td colspan="10" class="px-3 py-6 text-center text-sm text-muted">' +
      'The run measured rows but none is linked to a session. ' +
      '<span class="text-accent">Run the <span class="mono">open_pinned_session</span> step to link them.</span>' +
      '</td></tr>';
  return (
    msg +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 flex flex-wrap items-center justify-between gap-2">' +
    '<div><h3 class="text-sm font-semibold text-ink">Pinned session area</h3>' +
    '<p class="mt-0.5 text-xs text-muted">THE COUNT IS A MEASUREMENT, not a count of a table · source = <span class="mono">' +
    esc(ps.source || '-') + '</span></p></div>' +
    '<button id="btn-ue-sessions-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div>' +
    '<div class="mb-3 flex flex-wrap items-center gap-3">' +
    '<span class="rounded-full bg-accent-soft px-3 py-1 text-sm font-semibold text-accent">' +
    esc(ps.count) + ' row' + (ps.count === 1 ? '' : 's') + ' at the pinned area</span>' +
    (ps.recorded_count != null
      ? '<span class="rounded-full border border-line bg-panel px-3 py-1 text-xs text-muted">' +
        'recorded run said ' + esc(ps.recorded_count) + ' — kept for comparison</span>'
      : '') +
    '<span class="text-xs text-muted">measured ' + esc(fmtLocal(ps.measured_at)) +
    ' · evidence <span class="mono">' + esc(ps.evidence_id || '-') + '</span>' +
    ' · run ' + esc(ps.run_status || '-') + '</span>' +
    (ps.live_count != null
      ? '<span class="rounded-full border border-line bg-panel px-3 py-1 text-xs text-muted">' +
        esc(ps.live_count) + ' live now (window ' + esc(ps.live_window_s) + 's)</span>'
      : '') +
    '</div>' +
    // THE THREE STRINGS, EXPLAINED IN THE UI ITSELF. The human asked what they
    // mean; an explanation that lives only in chat is lost the moment the page
    // is reloaded.
    '<div class="mb-3 rounded-xl border border-line bg-soft/50 p-3 text-xs text-muted">' +
    '<div class="mb-1 font-semibold text-ink">What these three numbers mean</div>' +
    '<div><span class="mono text-ink">' + esc(ps.count) + ' rows at the pinned area</span> — the number of PINNED rows on screen NOW. ' +
    'It is read from the Sessions panel\u2019s own <span class="mono">Pinned N</span> heading in a fresh screenshot, ' +
    'so it is a MEASUREMENT of the list, not a count of a table.</div>' +
    '<div class="mt-1"><span class="mono text-ink">seen across cycles: [' + esc((ps.seen_across_cycles || []).join(', ')) + ']</span> — ' +
    'the counts the LAST RECORDED RUN saw, one per retry cycle, over ' + esc(ps.cycles) + ' cycle(s) ' +
    '(max seen ' + esc(ps.max_seen) + '). This is EVIDENCE of what was true THEN, not a reading of NOW — ' +
    'the count changes because it is a live screen measurement.</div>' +
    '<div class="mt-1"><span class="mono text-ink">linked sessions: ' + esc(ps.linked_sessions) + '</span> — ' +
    'rows in <span class="mono">coordinate_session</span>. That is a DIFFERENT population from the pinned rows: ' +
    'a session can be recorded without being pinned, and a pinned row can have no session linked yet. ' +
    'It is shown for context only and is NEVER the row count.</div>' +
    '<div class="mt-1">band (' + esc(band.x1) + ',' + esc(band.y1) + ')-(' + esc(band.x2) + ',' + esc(band.y2) + ')' +
    ' · row height ' + esc(ps.row_height) + 'px · ' + esc(ps.band_source || '') + '</div>' +
    // THE LIVE ATTEMPT IS SHOWN EVEN WHEN IT FAILED. A silent fall-back to the
    // recorded number is how the page showed a stale 4 while the screen said 5.
    (ps.live
      ? '<div class="mt-1">live reading: ' +
        (ps.live.ok
          ? '<span class="text-emerald-700">ok</span> — the Sessions panel\\u2019s own heading read as ' +
            '<span class="mono text-ink">' + esc(ps.live.vl_answer) + '</span>' +
            (ps.live.activated ? ' (VS Code was brought to the foreground' +
              (ps.live.restored ? ' and your window was restored)' : ')') : '')
          : '<span class="text-rose-700">FAILED</span> — ' + esc(ps.live.why || 'no reason given') +
            ' <span class="text-muted">(the recorded number above is shown instead)</span>') +
        '</div>'
      : '') +
    '</div>' +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    // THE HEADERS ARE REGISTERED USER WORDS, NOT DB COLUMN NAMES.
    // MEASURED 2026-09-27: 8 of these 10 were raw column names (`coords`,
    // `status`, `session_id`, ...) and only 2 were registered terms. A user
    // cannot know `coords` means "how many times this session was measured".
    // Each header carries a `title=` naming the DB column it reads, so the
    // mapping is checkable rather than lost.
    '<th class="px-3 py-2" title="the pinned row\u2019s position, 1-based">Row</th>' +
    '<th class="px-3 py-2" title="reads session_id">Session</th>' +
    '<th class="px-3 py-2" title="reads x1, y1 \u2014 the rect\u2019s top-left corner in screen pixels">Top-left</th>' +
    '<th class="px-3 py-2" title="reads x2, y2 \u2014 the rect\u2019s bottom-right corner in screen pixels">Bottom-right</th>' +
    '<th class="px-3 py-2" title="reads cx, cy \u2014 the rect\u2019s centre point">Centre</th>' +
    '<th class="px-3 py-2" title="reads coord_count \u2014 coordinate_session rows linked to this session, NOT a count of sessions">Measurements</th>' +
    '<th class="px-3 py-2" title="reads identity_known \u2014 whether identity_registry has a row for this session_id">Identity known?</th>' +
    '<th class="px-3 py-2" title="reads is_live \u2014 whether the session\u2019s own file was touched within the live window">Live?</th>' +
    '<th class="px-3 py-2" title="reads environment \u2014 the environment this session\u2019s coordinate resolves to">Where</th>' +
    '<th class="px-3 py-2" title="reads last_seen \u2014 when the session was last LINKED to a coordinate, not when it last ran">Last linked</th>' +
    '</tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>' +
    '<p class="mt-2 text-xs text-muted">Click any row for the full detail report. The COUNT is a live measurement of the panel\u2019s own <span class="mono">Pinned N</span> heading. The per-row rect is DERIVED from the band plus the fixed row height (the human: “each session height is fixed and width will update according to the session area”).</p>' +
    '</section>'
  );
}

function userAssetsHtml() {
  const ue = state.userEnvironment || {};
  const rows = (ue.assets || []).length
    ? ue.assets
        .map(
          (a) =>
            '<tr class="border-b border-line align-top">' +
            '<td class="px-3 py-2 mono text-xs text-muted">' + esc(a.id) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(a.user_id) +
            '<span class="ml-1 text-muted">' + esc(a.user_name || '-') + '</span></td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(a.app_id) +
            '<span class="ml-1 text-muted">' + esc(a.app_key || '-') + '</span></td>' +
            '<td class="px-3 py-2 text-xs text-ink">' + esc(a.app_name || '-') +
            '<span class="ml-1 text-muted">' + esc(a.kind || '') + '</span></td>' +
            '<td class="px-3 py-2">' + assetStatusChip(a.status) + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' + esc(a.detail || '-') + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted whitespace-nowrap">' + esc(fmtLocal(a.last_seen_at)) + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' + esc(a.source || '-') + '</td>' +
            '</tr>'
        )
        .join('')
    : '<tr><td colspan="8" class="px-3 py-6 text-center text-sm text-muted">No assets yet — click Sync to detect installed apps.</td></tr>';
  const msgBar = ue.syncMsg
    ? '<div class="mb-3 rounded-xl border ' +
      (ue.syncOk ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700') +
      ' px-3 py-2 text-sm">' + esc(ue.syncMsg) + '</div>'
    : '';
  return (
    msgBar +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 flex flex-wrap items-center justify-between gap-2">' +
    '<div><h3 class="text-sm font-semibold text-ink">user_asset rows (' + esc(String(ue.assetsTotal ?? 0)) + ')</h3>' +
    '<p class="mt-0.5 text-xs text-muted">Which app a user has, and its state. ' +
    'id-driven: <span class="mono">user_id</span> → <span class="mono">users</span>, ' +
    '<span class="mono">app_id</span> → <span class="mono">app</span>. No alias or slug.</p></div>' +
    '<div class="flex items-center gap-2">' +
    '<button id="btn-ua-sync" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90">Sync</button>' +
    '<button id="btn-ua-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div></div>' +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">id</th>' +
    '<th class="px-3 py-2">user_id</th>' +
    '<th class="px-3 py-2">app_id</th>' +
    '<th class="px-3 py-2">app</th>' +
    '<th class="px-3 py-2">status</th>' +
    '<th class="px-3 py-2">detail</th>' +
    '<th class="px-3 py-2">last_seen_at</th>' +
    '<th class="px-3 py-2">source</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<p class="mt-3 text-xs text-muted">Sync probes the live environment: ' +
    'OpenClaw by its MCP port, Ollama by its HTTP port, other apps by process name.</p>' +
    '</section>'
  );
}

// ---------------------------------------------------------------------------
// COMPUTER PRESENCE — the TRIGGER POINT
// The user: "trigger point by http://127.0.0.1:18765/llm-tasks/ open at browser"
//           "so you can have status now!"
// Opening the page records a visit (server-side, in `_llm_monitor_spa_index`).
// This tab READS that record. SSOT = computer_presence via /api/computer_presence.
// ---------------------------------------------------------------------------

async function refreshComputerPresence() {
  const ue = state.userEnvironment;
  try {
    const res = await fetch('/api/computer_presence');
    const data = await res.json();
    if (!res.ok || !data.ok) {
      ue.msg = (data && (data.error || data.message)) || 'HTTP ' + res.status;
      ue.msgOk = false;
      return;
    }
    ue.presence = data.rows || [];
    ue.presenceTotal = data.count || 0;
    ue.presenceThis = data.this_computer || null;
    ue.presenceThresholds = data.thresholds || null;
    ue.msg = '';
    ue.msgOk = true;
  } catch (e) {
    ue.msg = 'Presence API unavailable: ' + (e.message || e);
    ue.msgOk = false;
  }
}

// A status pill. UNKNOWN is a REAL outcome, not a failure: a computer with no
// row has never opened the page, so its presence is NOT measured.
function presencePill(status) {
  const s = String(status || 'UNKNOWN');
  const cls = s === 'ONLINE' ? 'bg-emerald-100 text-emerald-800'
    : s === 'STALE' ? 'bg-amber-100 text-amber-800'
    : s === 'OFFLINE' ? 'bg-rose-100 text-rose-800'
    : 'bg-soft text-muted';
  return '<span class="rounded-full px-2 py-0.5 text-xs ' + cls + '">' + esc(s) + '</span>';
}

function userPresenceHtml() {
  const ue = state.userEnvironment || {};
  const th = ue.presenceThresholds || {};
  const mine = ue.presenceThis;
  const mineCard = mine
    ? '<div class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="mb-2 flex items-center gap-2">' +
      '<span class="text-sm font-semibold text-ink">This computer</span>' +
      presencePill(mine.status) + '</div>' +
      '<div class="grid gap-1 sm:grid-cols-2">' +
      ueField('computer_id', mine.computer_id) +
      ueField('last_seen_at', fmtLocal(mine.last_seen_at)) +
      ueField('age', mine.age_sec == null ? '-' : Math.round(mine.age_sec) + 's') +
      ueField('hits', mine.hits) +
      '</div>' +
      '<div class="mt-2 text-xs text-muted">' + esc(mine.why || '') + '</div>' +
      '</div>'
    : '';
  const rows = (ue.presence || []).length
    ? ue.presence.map((x) =>
        '<tr class="border-b border-line align-top">' +
        '<td class="px-3 py-2 mono text-xs text-ink">' + esc(x.computer_id || '-') + '</td>' +
        '<td class="px-3 py-2 text-xs text-ink">' + esc(x.computer_name || '-') + '</td>' +
        '<td class="px-3 py-2 mono text-xs text-ink">' + esc(x.ip_address || '-') + '</td>' +
        '<td class="px-3 py-2 text-xs">' + presencePill(x.status) + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted whitespace-nowrap">' + esc(fmtLocal(x.last_seen_at)) + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(x.age_sec == null ? '-' : Math.round(x.age_sec) + 's') + '</td>' +
        '<td class="px-3 py-2 mono text-xs text-ink">' + esc(x.hits ?? '-') + '</td>' +
        '<td class="px-3 py-2 text-xs text-muted">' + esc(x.last_path || '-') + '</td>' +
        '</tr>').join('')
    : '<tr><td colspan="8" class="px-3 py-6 text-center text-sm text-muted">' +
      'No computer has opened the page yet. Open ' +
      '<span class="mono">http://127.0.0.1:18765/llm-tasks/</span> to record a visit.</td></tr>';
  return (
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="mb-2 text-sm font-semibold text-ink">Computer presence (' +
    esc(String(ue.presenceTotal ?? 0)) + ')</h3>' +
    '<p class="mb-3 text-xs text-muted">The TRIGGER POINT: opening ' +
    '<span class="mono">/llm-tasks/</span> in a browser records a visit. ' +
    'Status is DERIVED from the age of <span class="mono">last_seen_at</span> ' +
    '(ONLINE &le; ' + esc(String(th.online_sec ?? '-')) + 's, STALE &le; ' +
    esc(String(th.stale_sec ?? '-')) + 's). A computer with no row is ' +
    '<span class="mono">UNKNOWN</span>, never OFFLINE.</p>' +
    mineCard +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">computer_id</th>' +
    '<th class="px-3 py-2">computer_name</th>' +
    '<th class="px-3 py-2">ip_address</th>' +
    '<th class="px-3 py-2">status</th>' +
    '<th class="px-3 py-2">last_seen_at</th>' +
    '<th class="px-3 py-2">age</th>' +
    '<th class="px-3 py-2">hits</th>' +
    '<th class="px-3 py-2">last_path</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '</section>'
  );
}

function userEnvironmentHtml() {
  const ue = state.userEnvironment || {};  const tab = ue.tab || 'detect';
  const msgBar = ue.msg
    ? '<div class="mb-3 rounded-xl border ' +
      (ue.msgOk ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-rose-200 bg-rose-50 text-rose-700') +
      ' px-3 py-2 text-sm">' + esc(ue.msg) + '</div>'
    : '';
  const header =
    '<div class="mb-4 flex flex-wrap items-center justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold text-ink">User Environment</h2>' +
    '<p class="mt-0.5 text-sm text-muted">DB-driven who/where · SSOT = <span class="mono">user_environment</span> · timezone by IP (ip-api.com)</p></div>' +
    '<button id="btn-ue-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div>';

  if (tab === 'detect') {
    const e = ue.env;
    const card = e
      ? '<div class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
        '<div class="mb-2 flex items-center gap-2"><span class="text-sm font-semibold text-ink">Detected environment</span>' +
        '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">live</span></div>' +
        '<div class="grid gap-1 sm:grid-cols-2">' +
        ueField('user_id', e.user_id) +
        ueField('ip_address', e.ip_address) +
        ueField('computer_id', e.computer_id) +
        ueField('computer_name', e.computer_name) +
        ueField('timezone', e.timezone) +
        ueFieldHtml('tz_offset_sec',
          esc(utcOffsetLabel(e.tz_offset_sec)) +
          '<span class="ml-1 text-muted">(' + esc(String(e.tz_offset_sec ?? '-')) + 's)</span>') +
        ueFieldHtml('country',
          flagImg(e.country_code, e.country) +
          '<span class="ml-1">' + esc(e.country || '-') + '</span>' +
          (e.country_code ? '<span class="ml-1 text-muted">' + esc(e.country_code) + '</span>' : '')) +
        ueField('region / city', (e.region || '-') + ' / ' + (e.city || '-')) +
        ueField('isp', e.isp) +
        ueField('language', e.language) +
        ueField('locale', e.locale) +
        ueField('os', (e.os_name || '-') + ' ' + (e.os_version || '')) +
        ueField('python', e.python_version) +
        '</div>' +
        // WHICH ENVIRONMENT_ID IS RUNNING ON THIS COMPUTER.
        // THE HUMAN (2026-09-25): "how to proof it is running, is by windows
        // task to proof does environment id is running at detect computer ...
        // never = not channel id!!!". Keyed by environment_id, from the
        // Windows Task Manager.
        envRunningHtml(e.environments) +
        '<div class="mt-3 rounded-xl border border-line bg-soft/50 p-3 text-sm"><span class="text-muted">detail：</span>' + esc(e.detail || '-') + '</div>' +
        '</div>'
      : '<div class="mt-3 rounded-2xl border border-line bg-panel p-6 text-center text-sm text-muted">Click Detect to read your environment.</div>';
    return (
      header + msgBar +
      '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">Detect my environment</h3>' +
      '<p class="mb-3 text-xs text-muted">Detect = read-only (no DB write). Save = upsert one <span class="mono">user_environment</span> row (UNIQUE computer_id + ip_address).</p>' +
      '<div class="flex flex-wrap items-center gap-2">' +
      '<button id="btn-ue-detect" type="button" class="rounded-xl bg-accent px-3 py-2 text-sm font-medium text-white hover:opacity-90">Detect</button>' +
      '<button id="btn-ue-save" type="button" class="rounded-xl border border-line bg-panel px-3 py-2 text-sm hover:bg-soft">Save to DB</button>' +
      '</div>' +
      card +
      '</section>'
    );
  }

  if (tab === 'presence') {
    return header + msgBar + userPresenceHtml();
  }

  if (tab === 'sessions') {
    return header + msgBar + userEnvironmentSessionsHtml();
  }

  if (tab === 'assets') {
    return header + userAssetsHtml();
  }

  const rows = (ue.rows || []).length
    ? ue.rows
        .map((x) => {
          const off = x.tz_offset_sec;
          const utc = off == null ? '-' : 'UTC' + (off >= 0 ? '+' : '') + (off / 3600);
          return (
            '<tr class="border-b border-line align-top">' +
            '<td class="px-3 py-2 text-xs text-muted whitespace-nowrap">' + esc(fmtLocal(x.updated_at)) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(x.user_id || '-') + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(x.ip_address || '-') + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(x.computer_id || '-') + '</td>' +
            '<td class="px-3 py-2 text-xs text-ink">' + esc(x.computer_name || '-') + '</td>' +
            '<td class="px-3 py-2 text-xs text-ink">' + esc(x.timezone || '-') + '<span class="ml-1 text-muted">' + esc(utc) + '</span></td>' +
            '<td class="px-3 py-2 text-xs text-ink">' + esc((x.city || '-') + ', ' + (x.country || '-')) + '</td>' +
            '<td class="px-3 py-2 text-xs text-ink">' + esc(x.language || '-') + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' + esc(x.isp || '-') + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' + esc(x.source || '-') + '</td>' +
            '</tr>'
          );
        })
        .join('')
    : '<tr><td colspan="10" class="px-3 py-6 text-center text-sm text-muted">No rows yet — use Detect → Save to DB.</td></tr>';
  return (
    header + msgBar +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="mb-2 text-sm font-semibold text-ink">user_environment rows (' + esc(String(ue.total ?? 0)) + ')</h3>' +
    '<p class="mb-3 text-xs text-muted">Who has activity: user + IP + computer + timezone + language.</p>' +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">updated_at</th>' +
    '<th class="px-3 py-2">user_id</th>' +
    '<th class="px-3 py-2">ip_address</th>' +
    '<th class="px-3 py-2">computer_id</th>' +
    '<th class="px-3 py-2">computer_name</th>' +
    '<th class="px-3 py-2">timezone</th>' +
    '<th class="px-3 py-2">city, country</th>' +
    '<th class="px-3 py-2">language</th>' +
    '<th class="px-3 py-2">isp</th>' +
    '<th class="px-3 py-2">source</th>' +
    '</tr></thead>' +
    '<tbody>' + rows + '</tbody></table></div>' +
    '</section>'
  );
}

function bindUserEnvironmentPanel() {
  $('#btn-ue-refresh')?.addEventListener('click', async () => {
    await refreshUserEnvironment();
    await refreshComputerPresence();
    await refreshPinnedSessions();
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
    toast('User Environment refreshed');
  });
  $('#btn-ue-sessions-refresh')?.addEventListener('click', async () => {
    await refreshPinnedSessions();
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
    toast('Pinned sessions refreshed');
  });
  // CLICK-TO-WHY. THE HUMAN (2026-09-27): "can onclick to have why = detail
  // report". MEASURED before this: the pinned table had 3 hover-only `title=`
  // and 0 clickable `data-*`, so the "why" was invisible on touch.
  //
  // THE PATTERN IS THIS FILE'S OWN (`openTaskDetailPopup`, app.js:6648):
  // backdrop click + ✕ + Escape. It is REUSED, not re-invented, so the two
  // popups cannot drift apart.
  document.querySelectorAll('[data-ue-row]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const n = Number(tr.getAttribute('data-ue-row'));
      const s = (state.userEnvironment?.pinnedSessions?.sessions || [])
        .find((x) => Number(x.pinned_row_no) === n);
      if (s) openPinnedRowDetail(s);
    });
  });
  $('#btn-ue-detect')?.addEventListener('click', async () => {
    await userEnvironmentDetect(false);
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
  });
  $('#btn-ue-save')?.addEventListener('click', async () => {
    await userEnvironmentDetect(true);
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
  });
  $('#btn-ua-refresh')?.addEventListener('click', async () => {
    await refreshUserAssets();
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
  });
  $('#btn-ua-sync')?.addEventListener('click', async () => {
    await syncUserAssets();
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
    toast('User assets synced');
  });
  $('#btn-up-refresh')?.addEventListener('click', async () => {
    await refreshComputerPresence();
    const body = $('#workspace-body');
    if (body && state.nav === 'user-environment') {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
    toast('Presence refreshed');
  });
  document.querySelectorAll('[data-ue-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.userEnvironment.tab = btn.getAttribute('data-ue-tab') || 'detect';
      try {
        history.pushState(
          { nav: 'user-environment', tab: state.userEnvironment.tab },
          '',
          navPath('user-environment', state.userEnvironment.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });
  // THE CONVERSATION STRIP'S TWO SHORTCUTS (2026-09-27).
  //
  // THE HUMAN, verbatim: "Conversation Center · one index · chat → chat_main →
  // chat_center_message → identity_registry / is the position for tha button"
  //
  // THIS IS THE SAME THREE STEPS `[data-ue-tab]` USES, not a new mechanism:
  // `pushState` + `mount(false)`. `mount(false)` replaces EVERY child of `#app`
  // (`app.js:7242`), so `#conversation-center-root` is a NEW element with no
  // `__vueMounted` flag, the Vue app remounts, and `openFromAddress()` -- the ONE
  // parser -- reads the new path. **One parser, two entry points.**
  //
  // `navPath('conversation-center', step)` yields `/llm-tasks/conversation/<step>`
  // ONLY IF `tabsForNav` knows the step. MEASURED: it returns the single
  // `{ id: 'index' }`, so `index` is correct and every other step falls back to
  // the bare `/llm-tasks/conversation`. Both are handled explicitly below so the
  // address is EXACT and never silently the page root.
  document.querySelectorAll('[data-cc-step]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const step = btn.getAttribute('data-cc-step') || 'index';
      const url = '/llm-tasks/conversation/' + encodeURI(step);
      try {
        history.pushState({ nav: 'conversation-center', tab: step }, '', url);
      } catch (_) {}
      mount(false);
    });
  });
}

// ---------------------------------------------------------------------------
// Tool Registry (劇本庫) — data SSOT = hotkey_tools.md via /api/tool-registry
// ---------------------------------------------------------------------------

async function refreshToolRegistry() {
  const tr = state.toolRegistry;
  try {
    const res = await fetch('/api/tool-registry');
    const data = await res.json();
    if (!res.ok || !data.ok) {
      tr.msg = (data && (data.error || data.message)) || 'HTTP ' + res.status;
      tr.msgOk = false;
      return;
    }
    tr.tools = data.tools || [];
    tr.deliveryLog = data.delivery_log || [];
    tr.designRules = data.design_rules || [];
    tr.f6Loop = data.f6_loop || [];
    tr.hotkeys = data.hotkeys || [];
    tr.sharedInfra = data.shared_infra || [];
    tr.msg = '';
    tr.msgOk = true;
  } catch (e) {
    tr.msg = 'Tool Registry API unavailable: ' + (e.message || e);
    tr.msgOk = false;
  }
}

// Permission Checklist — DB-driven target AREAS + checklist_confirm.
// SSOT = coords.db target_area via /api/coord-targets/checklist.
async function refreshPermissionChecklist() {
  const tr = state.toolRegistry;
  try {
    const res = await fetch('/api/coord-targets/checklist?popup=perm_picker');
    const data = await res.json();
    if (!res.ok || !data.ok) {
      tr.checklist = { error: (data && data.error) || 'HTTP ' + res.status };
      return;
    }
    tr.checklist = data;
  } catch (e) {
    tr.checklist = { error: 'Checklist API unavailable: ' + (e.message || e) };
  }
}

function checklistBadge(v) {
  const yes = String(v || '').toLowerCase() === 'yes';
  return yes
    ? '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">yes</span>'
    : '<span class="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-medium text-rose-700">no</span>';
}

function permissionChecklistHtml() {
  const tr = state.toolRegistry || {};
  const cl = tr.checklist || {};
  const header =
    '<div class="mb-4 flex flex-wrap items-center justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold text-ink">Permission Checklist</h2>' +
    '<p class="mt-0.5 text-sm text-muted">Popup target <b>AREAS</b> (X1,Y1)-(X2,Y2) + <span class="mono">checklist_confirm</span> · SSOT = <span class="mono">coords.db target_area</span> · <span class="mono">f_perm_click.py --set-native</span> aborts unless all <b>yes</b></p></div>' +
    '<button id="btn-cl-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div>';

  if (cl.error) {
    return header + '<div class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">' + esc(cl.error) + '</div>';
  }

  const items = cl.items || [];
  const allYes = String(cl.all_present || '').toLowerCase() === 'yes';
  const banner =
    '<div class="mb-3 rounded-xl border px-3 py-2 text-sm ' +
    (allYes
      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
      : 'border-amber-200 bg-amber-50 text-amber-700') +
    '">all_present = <b>' + esc(cl.all_present || 'no') + '</b> · confirmed ' +
    esc(String(cl.confirmed || 0)) + '/' + esc(String(cl.count || 0)) +
    (allYes ? ' — click allowed' : ' — click will ABORT') +
    (cl.missing && cl.missing.length ? ' · missing: <span class="mono">' + esc(cl.missing.join(', ')) + '</span>' : '') +
    '</div>';

  const rows = items.length
    ? items
        .map((a) => {
          return (
            '<tr class="border-b border-line align-top">' +
            '<td class="px-3 py-2 mono text-xs text-ink">' + esc(a.target_id) + '</td>' +
            '<td class="px-3 py-2 text-sm text-ink">' + esc(a.label || '') + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-muted">' + esc(String(a.x1)) + ', ' + esc(String(a.y1)) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-muted">' + esc(String(a.x2)) + ', ' + esc(String(a.y2)) + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-accent">' + esc(String(a.cx)) + ', ' + esc(String(a.cy)) + '</td>' +
            '<td class="px-3 py-2">' + checklistBadge(a.checklist_confirm) + '</td>' +
            '<td class="px-3 py-2 text-xs text-muted">' + esc(a.confirm_reason || '') + '</td>' +
            '<td class="px-3 py-2 mono text-xs text-muted">' + esc(fmtLocal(a.confirmed_at)) + '</td>' +
            '</tr>'
          );
        })
        .join('')
    : '<tr><td colspan="8" class="px-3 py-6 text-center text-sm text-muted">No target_area rows yet. Run <span class="mono">f_perm_click.py --checklist</span> with the picker open.</td></tr>';

  return (
    header + banner +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr>' +
    '<th class="px-3 py-2">target_id</th><th class="px-3 py-2">label</th>' +
    '<th class="px-3 py-2">X1, Y1</th><th class="px-3 py-2">X2, Y2</th>' +
    '<th class="px-3 py-2">center (cx, cy)</th><th class="px-3 py-2">checklist_confirm</th>' +
    '<th class="px-3 py-2">reason</th><th class="px-3 py-2">confirmed_at</th>' +
    '</tr></thead><tbody>' + rows + '</tbody></table></div>' +
    '<p class="mt-3 text-xs text-muted">Center is <b>derived</b> from the area ((X1+X2)/2, (Y1+Y2)/2) — it can never disagree with X1/X2 + Y1/Y2. <span class="mono">id</span> is INTEGER PRIMARY KEY AUTOINCREMENT (one row per popup+target).</p>'
  );
}

function trStatusBadge(status) {
  const s = status || '';
  if (s.includes('✅')) return '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">已交付</span>';
  if (s.includes('🚧')) return '<span class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">進行中</span>';
  return '<span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">' + esc(s || '-') + '</span>';
}

function toolRegistryHtml() {
  const tr = state.toolRegistry || {};
  const tab = tr.tab || 'list';
  const msgBar = tr.msg
    ? '<div class="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">' + esc(tr.msg) + '</div>'
    : '';
  const header =
    '<div class="mb-4 flex flex-wrap items-center justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold text-ink">Tool Registry 劇本庫</h2>' +
    '<p class="mt-0.5 text-sm text-muted">一鍵工具總表 · SSOT = <span class="mono">hotkey_tools.md</span> · AHK 入口 <span class="mono">deepseek_strip.ahk</span></p></div>' +
    '<button id="btn-tr-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div>';

  if (tab === 'list') {
    const tools = tr.tools || [];
    const cards = tools.length
      ? tools
          .map((t) => {
            return (
              '<button type="button" data-tr-tool="' + esc(t.num) + '" class="w-full text-left rounded-2xl border border-line bg-panel p-4 shadow-panel transition hover:ring-2 hover:ring-accent cursor-pointer">' +
              '<div class="flex flex-wrap items-center justify-between gap-2">' +
              '<div class="flex items-center gap-2">' +
              '<span class="rounded-lg bg-accent px-2.5 py-1 text-sm font-bold text-white">' + esc(t.hotkey) + '</span>' +
              '<span class="text-sm font-semibold text-ink">' + esc(t.name) + '</span>' +
              '<span class="mono text-xs text-muted">#' + esc(t.num) + '</span>' +
              '</div>' +
              trStatusBadge(t.status) +
              '</div>' +
              '<div class="mt-2 grid gap-1 text-xs sm:grid-cols-2">' +
              '<div><span class="text-muted">Tool ID：</span><span class="mono">' + esc(t.tool_id) + '</span></div>' +
              '<div><span class="text-muted">腳本：</span><span class="mono">' + esc(t.script) + '</span></div>' +
              '<div><span class="text-muted">用 mouse？</span> ' + esc(t.mouse) + '</div>' +
              '<div><span class="text-muted">前提：</span>' + esc(t.prereq) + '</div>' +
              '</div>' +
              '<div class="mt-2 text-xs"><span class="text-muted">功能：</span>' + esc(t.function) + '</div>' +
              '<div class="mt-1 text-xs"><span class="text-muted">驗證方式：</span><span class="mono">' + esc(t.verification) + '</span></div>' +
              '<div class="mt-3 text-right text-[11px] font-medium text-accent">click 睇詳情 →</div>' +
              '</button>'
            );
          })
          .join('')
      : '<div class="rounded-2xl border border-line bg-panel p-8 text-center text-sm text-muted">未讀到工具（hotkey_tools.md 工具總表為空或 API 未回傳）。</div>';
    return header + msgBar + '<div class="grid gap-3 lg:grid-cols-2">' + cards + '</div>';
  }

  if (tab === 'hotkey') {
    const tools = tr.tools || [];
    const rows = tools.length
      ? tools
          .map((t) => {
            return (
              '<tr class="border-b border-line align-top">' +
              '<td class="px-3 py-2.5"><span class="rounded-lg bg-accent px-2 py-0.5 text-sm font-bold text-white">' + esc(t.hotkey) + '</span></td>' +
              '<td class="px-3 py-2.5 text-sm font-medium text-ink">' + esc(t.name) + '</td>' +
              '<td class="px-3 py-2.5 text-xs text-muted">' + esc(t.function) + '</td>' +
              '<td class="px-3 py-2.5 text-xs text-muted">' + esc(t.prereq) + '</td>' +
              '</tr>'
            );
          })
          .join('')
      : '<tr><td colspan="4" class="px-3 py-6 text-center text-sm text-muted">未讀到工具。</td></tr>';
    const hotkeys = tr.hotkeys || [];
    const hkBadge = (status) => {
      const s = status || '';
      if (s.includes('in-use')) return '<span class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">🟢 in-use</span>';
      if (s.includes('reserved')) return '<span class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">🟡 reserved</span>';
      if (s.includes('available')) return '<span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">⚪ available</span>';
      return '<span class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600">' + esc(s || '-') + '</span>';
    };
    const hkRows = hotkeys.length
      ? hotkeys
          .map((h) => {
            return (
              '<tr class="border-b border-line align-top">' +
              '<td class="whitespace-nowrap px-3 py-2"><span class="mono rounded-lg bg-accent px-2 py-0.5 text-sm font-bold text-white">' + esc(h.hotkey) + '</span></td>' +
              '<td class="px-3 py-2 mono text-xs text-ink">' + esc(h.owner) + '</td>' +
              '<td class="px-3 py-2 text-xs text-muted">' + esc(h.purpose) + '</td>' +
              '<td class="whitespace-nowrap px-3 py-2">' + hkBadge(h.status) + '</td>' +
              '</tr>'
            );
          })
          .join('')
      : '<tr><td colspan="4" class="px-3 py-6 text-center text-sm text-muted">未讀到 Hotkey 註冊表（hotkey_tools.md 缺「Hotkey 註冊表」section）。</td></tr>';
    return (
      header + msgBar +
      '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">Hotkey 註冊表（所有 hotkey 必登記）</h3>' +
      '<p class="mb-2 text-xs text-muted">防衝突 SSOT：任何 hotkey 落手之前，必先喺呢度登記 owner + 用途 + 狀態。未登記 = 唔准用（設計規則 #8）。例如 2026-09-18 <span class="mono">Ctrl+Alt+M</span> 撞 mouse spot helper 嘅事故。</p>' +
      '<div class="mb-3 flex flex-wrap gap-1.5 text-[11px]">' +
      '<span class="rounded-full bg-emerald-50 border border-emerald-200 px-2 py-0.5 text-emerald-700">🟢 in-use = <b>global AHK hotkey</b>（<span class="mono">deepseek_strip.ahk</span>，全系統有效，<b>唔係 VS Code keybinding</b>）</span>' +
      '<span class="rounded-full bg-rose-50 border border-rose-200 px-2 py-0.5 text-rose-700">⛔ VS Code owned = VS Code 自己嘅 shortcut，AHK 搶唔到</span>' +
      '<span class="rounded-full bg-amber-50 border border-amber-200 px-2 py-0.5 text-amber-700">🟡 reserved = 保留（舊路徑）</span>' +
      '<span class="rounded-full bg-slate-50 border border-slate-200 px-2 py-0.5 text-slate-600">⚪ available = 未分配</span>' +
      '</div>' +
      '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
      '<thead class="bg-soft/60 text-xs text-muted"><tr><th class="px-3 py-2">Hotkey</th><th class="px-3 py-2">Owner（script）</th><th class="px-3 py-2">用途</th><th class="px-3 py-2">狀態</th></tr></thead>' +
      '<tbody>' + hkRows + '</tbody></table></div>' +
      '</section>' +
      '<section class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">Hotkey 示範 + Tutorial</h3>' +
      '<p class="mb-3 text-xs text-muted">每個 tool = 一個 hotkey + 一個 Python 腳本 + 明確前提 + 客觀驗證。AHK 層負責 input 編排（Send 權限），Python 層負責偵測 + 報告。按下 hotkey 即一鍵執行。</p>' +
      '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
      '<thead class="bg-soft/60 text-xs text-muted"><tr><th class="px-3 py-2">Hotkey</th><th class="px-3 py-2">名稱</th><th class="px-3 py-2">功能</th><th class="px-3 py-2">前提</th></tr></thead>' +
      '<tbody>' + rows + '</tbody></table></div>' +
      '</section>'
    );
  }

  if (tab === 'checklist') {
    return permissionChecklistHtml();
  }

  if (tab === 'library') {
    const tools = tr.tools || [];
    const log = tr.deliveryLog || [];
    const infra = tr.sharedInfra || [];
    const rules = tr.designRules || [];
    const docCards = tools.length
      ? tools
          .map((t) => {
            const related = log.filter((d) => (d.tool || '').includes(t.hotkey) || (d.tool || '').includes(t.tool_id));
            const relHtml = related.length
              ? '<div class="mt-3"><div class="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">交付記錄 / 證據</div>' +
                '<ul class="space-y-1">' +
                related
                  .map(
                    (d) =>
                      '<li class="rounded-lg border border-line bg-soft/50 px-2.5 py-1.5 text-xs">' +
                      '<span class="mono text-muted">' + esc(d.date) + '</span> · ' +
                      '<span class="text-ink">' + esc(d.event) + '</span>' +
                      '<div class="mt-0.5 text-muted">證據：' + esc(d.evidence) + '</div>' +
                      '</li>'
                  )
                  .join('') +
                '</ul></div>'
              : '';
            return (
              '<article class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
              '<div class="flex flex-wrap items-center justify-between gap-2">' +
              '<div class="flex flex-wrap items-center gap-2">' +
              '<span class="rounded-lg bg-accent px-2.5 py-1 text-sm font-bold text-white">' + esc(t.hotkey) + '</span>' +
              '<span class="text-sm font-semibold text-ink">' + esc(t.name) + '</span>' +
              '<span class="mono text-xs text-muted">#' + esc(t.num) + '</span>' +
              '</div>' +
              trStatusBadge(t.status) +
              '</div>' +
              '<div class="mt-2 grid gap-1 text-xs sm:grid-cols-2">' +
              '<div><span class="text-muted">Tool ID：</span><span class="mono">' + esc(t.tool_id) + '</span></div>' +
              '<div><span class="text-muted">腳本：</span><span class="mono">' + esc(t.script) + '</span></div>' +
              '<div><span class="text-muted">用 mouse？</span> ' + esc(t.mouse) + '</div>' +
              '<div><span class="text-muted">前提：</span>' + esc(t.prereq) + '</div>' +
              '</div>' +
              '<div class="mt-3"><div class="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">功能（完整）</div>' +
              '<p class="whitespace-pre-wrap text-xs leading-relaxed text-ink">' + esc(t.function) + '</p></div>' +
              '<div class="mt-3"><div class="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">驗證方式</div>' +
              '<p class="mono text-xs leading-relaxed text-ink">' + esc(t.verification) + '</p></div>' +
              relHtml +
              '</article>'
            );
          })
          .join('')
      : '<div class="rounded-2xl border border-line bg-panel p-8 text-center text-sm text-muted">未讀到工具文檔。</div>';
    const infraRows = infra.length
      ? infra
          .map(
            (i) =>
              '<tr class="border-b border-line align-top">' +
              '<td class="px-3 py-2 mono text-xs text-ink">' + esc(i.component) + '</td>' +
              '<td class="px-3 py-2 text-xs text-muted">' + esc(i.purpose) + '</td>' +
              '<td class="px-3 py-2 mono text-xs text-muted">' + esc(i.source) + '</td>' +
              '</tr>'
          )
          .join('')
      : '<tr><td colspan="3" class="px-3 py-6 text-center text-sm text-muted">未讀到共用基礎設施。</td></tr>';
    const rulesHtml = rules.length
      ? rules
          .map((r) => '<li class="flex gap-2 text-sm text-ink"><span class="mono shrink-0 text-accent">' + esc(r.n) + '.</span><span>' + esc(r.text) + '</span></li>')
          .join('')
      : '<li class="text-sm text-muted">未讀到設計規則。</li>';
    return (
      header + msgBar +
      '<section class="mb-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">Library 文檔（公開用）</h3>' +
      '<p class="text-xs text-muted">AHK 入口 <span class="mono">deepseek_strip.ahk</span> = <b>我哋自己嘅 global hotkey 層</b>（全系統有效，唔係 VS Code keybinding）。每個 tool = 一個 hotkey + Python 腳本 + 明確前提 + 客觀驗證。SSOT = <span class="mono">hotkey_tools.md</span>。</p>' +
      '</section>' +
      '<div class="grid gap-3 lg:grid-cols-2">' + docCards + '</div>' +
      '<section class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">共用基礎設施</h3>' +
      '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
      '<thead class="bg-soft/60 text-xs text-muted"><tr><th class="px-3 py-2">組件</th><th class="px-3 py-2">用途</th><th class="px-3 py-2">來源</th></tr></thead>' +
      '<tbody>' + infraRows + '</tbody></table></div>' +
      '</section>' +
      '<section class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<h3 class="mb-2 text-sm font-semibold text-ink">設計規則（每個新 tool 必跟）</h3>' +
      '<ol class="list-none space-y-1.5">' + rulesHtml + '</ol>' +
      '</section>'
    );
  }

  // tab === 'steps'
  const rules = tr.designRules || [];
  const rulesHtml = rules.length
    ? rules
        .map((r) => '<li class="flex gap-2 text-sm text-ink"><span class="mono shrink-0 text-accent">' + esc(r.n) + '.</span><span>' + esc(r.text) + '</span></li>')
        .join('')
    : '<li class="text-sm text-muted">未讀到設計規則。</li>';
  const f6 = tr.f6Loop || [];
  const f6Html = f6.length
    ? '<pre class="mono max-h-96 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-soft/60 p-3 text-xs leading-relaxed">' + esc(f6.join('\n')) + '</pre>'
    : '<p class="text-sm text-muted">未讀到 F6 開發監控循環。</p>';
  const log = tr.deliveryLog || [];
  const logRows = log.length
    ? log
        .map((d) => (
          '<tr class="border-b border-line align-top">' +
          '<td class="whitespace-nowrap px-3 py-2 text-xs mono text-muted">' + esc(d.date) + '</td>' +
          '<td class="px-3 py-2"><span class="rounded bg-accent-soft px-1.5 py-0.5 text-xs font-medium text-accent">' + esc(d.tool) + '</span></td>' +
          '<td class="px-3 py-2 text-xs text-ink">' + esc(d.event) + '</td>' +
          '<td class="px-3 py-2 text-xs text-muted">' + esc(d.evidence) + '</td>' +
          '</tr>'
        ))
        .join('')
    : '<tr><td colspan="4" class="px-3 py-6 text-center text-sm text-muted">未讀到交付記錄。</td></tr>';
  return (
    header + msgBar +
    '<div class="grid gap-3 lg:grid-cols-2">' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="mb-2 text-sm font-semibold text-ink">設計規則（每個新 tool 必跟）</h3>' +
    '<ol class="list-none space-y-1.5">' + rulesHtml + '</ol>' +
    '</section>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="mb-2 text-sm font-semibold text-ink">F6 開發監控循環（每個新 py 必跟）</h3>' +
    f6Html +
    '</section>' +
    '</div>' +
    '<section class="mt-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="mb-2 text-sm font-semibold text-ink">交付記錄（Test 證據）</h3>' +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr><th class="px-3 py-2">日期</th><th class="px-3 py-2">Tool</th><th class="px-3 py-2">事件</th><th class="px-3 py-2">證據</th></tr></thead>' +
    '<tbody>' + logRows + '</tbody></table></div>' +
    '</section>'
  );
}

/* ==================================================================
   Telemetry HUD screen
   SSOT = /api/telemetry/summary (all metrics) + /api/telemetry/<metric>
   Charts are quiet; detail lives in a modal + a drill-down tab.
   ================================================================== */

const TELE_METRIC_LABEL = {
  heartbeat_hourly: 'Heartbeats per hour',
  fault_daily: 'Fault events per day',
  task_by_model: 'Task pass / fail per model',
  watchdog_by_kind: 'Watchdog events by kind',
  llm_tokens_by_model: 'LLM tokens per model',
};

/** Column definitions per metric, for the detail table. */
const TELE_COLUMNS = {
  heartbeat_hourly: [
    ['heartbeat_at', 'time'],
    ['bucket', 'hour'],
    ['worker_id', 'worker'],
    ['pid', 'pid'],
    ['business_alive', 'alive'],
  ],
  fault_daily: [
    ['event_id', 'event'],
    ['fault_type', 'fault type'],
    ['status', 'status'],
    ['detect_at', 'detected at'],
    ['bucket', 'day'],
  ],
  task_by_model: [
    ['task_id', 'task'],
    ['skill_id', 'skill'],
    ['assigned_model', 'model'],
    ['status', 'status'],
    ['retry_count', 'retry'],
    ['handoff_count', 'handoff'],
    ['error_msg', 'error'],
  ],
  watchdog_by_kind: [
    ['local_time', 'time'],
    ['kind', 'kind'],
    ['level', 'level'],
    ['message', 'message'],
    ['has_screenshot', 'shot'],
  ],
  llm_tokens_by_model: [
    ['started_at', 'started'],
    ['model', 'model'],
    ['task', 'task'],
    ['prompt_tokens', 'prompt'],
    ['completion_tokens', 'completion'],
    ['total_tokens', 'total'],
    ['status', 'status'],
  ],
};

function telemetryLoadingHtml() {
  return (
    '<div class="hud"><div class="hud-head"><div><h2>Telemetry</h2>' +
    '<p>waiting for /api/telemetry/summary …</p></div></div>' +
    '<div class="hud-grid">' +
    Array(4).fill('<div class="hud-empty">loading…</div>').join('') +
    '</div></div>'
  );
}

function telemetryHtml() {
  const t = state.telemetry || {};
  if (!t.loaded) return telemetryLoadingHtml();

  const order = t.order && t.order.length ? t.order : Object.keys(t.metrics || {});
  const cards = order
    .map((k) => hudCardHtml(t.metrics[k] || {}, { open: true }))
    .join('');

  const failed = (t.failed || []).length;
  return (
    '<div class="hud">' +
    '<div class="hud-head">' +
    '<div><h2>Telemetry</h2>' +
    '<p>read-only aggregates · agent.db + watchdog events + llm_tasks ' +
    (t.generatedAt ? '· generated ' + esc(t.generatedAt) : '') +
    '</p></div>' +
    '<div class="hud-head-right">' +
    '<span class="hud-pill">' + order.length + ' metrics</span>' +
    (failed
      ? '<span class="hud-pill" style="color:#fb7185">' + failed + ' degraded</span>'
      : '<span class="hud-pill" style="color:#34d399">all healthy</span>') +
    '<button type="button" class="hud-btn" id="btn-tele-refresh">Refresh</button>' +
    '</div></div>' +
    (t.msg ? '<p class="hud-note">' + esc(t.msg) + '</p>' : '') +
    '<div class="hud-grid">' + cards + '</div>' +
    '<p class="hud-note" style="margin-top:14px">' +
    'Safe Actions: <b>Refresh</b> re-reads the aggregates · ' +
    '<b>detail →</b> opens the breakdown modal · ' +
    'every write originates from other tools — this page never writes.' +
    '</p></div>'
  );
}

/** Drill-down tab: full table for one metric. */
function telemetryDetailHtml() {
  const t = state.telemetry || {};
  const key = t.tab;
  const m = (t.metrics || {})[key];
  if (!m) {
    return '<div class="hud"><div class="hud-empty">metric not loaded — go back to Overview</div></div>';
  }
  const cols = TELE_COLUMNS[key] || [];
  const rows = m.detail_rows || [];

  const thead = cols.map(([, label]) => '<th>' + esc(label) + '</th>').join('');
  const tbody = rows.length
    ? rows
        .map((r) => {
          const tds = cols
            .map(([field]) => {
              let v = r[field];
              if (typeof v === 'boolean') v = v ? 'yes' : 'no';
              if (v === null || v === undefined || v === '') v = '—';
              if (field === 'level') {
                const color =
                  { alert: '#f43f5e', warn: '#f59e0b', info: '#38bdf8', ok: '#10b981' }[
                    String(v)
                  ] || '#8b949e';
                return (
                  '<td><span class="hud-lvl" style="border:1px solid ' +
                  color + ';color:' + color + '">' + esc(v) + '</span></td>'
                );
              }
              return '<td>' + esc(v) + '</td>';
            })
            .join('');
          return '<tr>' + tds + '</tr>';
        })
        .join('')
    : '<tr><td colspan="' + cols.length + '" style="text-align:center;color:#8b949e;padding:22px">' +
      'no rows in range</td></tr>';

  return (
    '<div class="hud">' +
    '<div class="hud-head">' +
    '<div><h2>' + esc(m.title || key) + '</h2>' +
    '<p>' + rows.length + ' rows · bucket=' + esc(m.bucket || '') +
    ' · unit=' + esc(m.unit || '') +
    (m.detail_truncated ? ' · truncated' : '') + '</p></div>' +
    '<div class="hud-head-right">' +
    '<button type="button" class="hud-btn" data-tele-back="1">← Overview</button>' +
    '</div></div>' +
    (m.error ? '<p class="hud-note" style="color:#fb7185">' + esc(m.error) + '</p>' : '') +
    '<div style="max-height:64vh;overflow:auto;border:1px solid #30363d;border-radius:12px">' +
    '<table class="hud-table"><thead><tr>' + thead + '</tr></thead>' +
    '<tbody>' + tbody + '</tbody></table></div></div>'
  );
}

async function refreshTelemetry() {
  try {
    const res = await fetch('/api/telemetry/summary');
    const data = await res.json();
    const metrics = data.metrics || {};
    state.telemetry.metrics = metrics;
    state.telemetry.order = Object.keys(metrics);
    state.telemetry.failed = data.failed || [];
    state.telemetry.generatedAt = data.generated_at || '';
    state.telemetry.loaded = true;
    state.telemetry.msg = '';
    state.telemetry.msgOk = true;
  } catch (e) {
    state.telemetry.loaded = true;
    state.telemetry.msg = 'Telemetry API unavailable: ' + (e.message || e);
    state.telemetry.msgOk = false;
    state.telemetry.metrics = state.telemetry.metrics || {};
    if (!state.telemetry.order.length) state.telemetry.order = Object.keys(TELE_METRIC_LABEL);
  }
}

/** Open the breakdown modal for one metric. Kind = 'chart' (live) or 'table'. */
function openTelemetryModal(metricKey) {
  const m = (state.telemetry.metrics || {})[metricKey];
  if (!m) return;
  const old = document.getElementById('tele-metric-modal');
  if (old) old.remove();

  const totals = m.totals || {};
  const totalsHtml = Object.entries(totals)
    .filter(([, v]) => typeof v !== 'object')
    .map(
      ([k, v]) =>
        '<div><div class="hud-stat-k">' + esc(k.replace(/_/g, ' ')) + '</div>' +
        '<div class="hud-stat-v">' + esc(Number.isInteger(v) ? fmtFull(v) : v) + '</div></div>'
    )
    .join('');

  const nested = Object.entries(totals)
    .filter(([, v]) => v && typeof v === 'object')
    .map(
      ([k, v]) =>
        '<p class="hud-note"><b>' + esc(k.replace(/_/g, ' ')) + '</b>: ' +
        esc(Object.entries(v).map(([a, b]) => a + '=' + b).join(' · ')) + '</p>'
    )
    .join('');

  const seriesTable =
    '<table class="hud-table"><thead><tr><th>series</th>' +
    (m.categories || [])
      .map((c) => '<th>' + esc(c) + '</th>')
      .join('') +
    '</tr></thead><tbody>' +
    (m.series || [])
      .map(
        (s) =>
          '<tr><td>' + esc(s.name) + '</td>' +
          (s.data || []).map((v) => '<td>' + esc(fmtFull(v)) + '</td>').join('') +
          '</tr>'
      )
      .join('') +
    '</tbody></table>';

  const el = document.createElement('div');
  el.id = 'tele-metric-modal';
  el.className = 'hud-modal-backdrop';
  el.innerHTML =
    '<div class="hud-modal" role="dialog" aria-modal="true" aria-label="' + esc(m.title) + '">' +
    '<div class="hud-modal-h">' +
    '<div><h3>' + esc(m.title || metricKey) + '</h3>' +
    '<p>' + esc(m.bucket || '') + ' · ' + esc(m.unit || '') +
    ' · ' + (m.detail_rows || []).length + ' detail rows' +
    (m.error ? ' · ' + esc(m.error) : '') + '</p></div>' +
    '<div style="display:flex;gap:6px;align-items:center">' +
    '<button type="button" class="hud-btn" id="tele-modal-full">View full page →</button>' +
    '<button type="button" class="hud-x" id="tele-modal-close" aria-label="Close">✕</button>' +
    '</div></div>' +
    '<div class="hud-modal-body">' +
    (totalsHtml ? '<div class="hud-stats" style="margin-bottom:12px">' + totalsHtml + '</div>' : '') +
    nested +
    '<div class="hud-chart" id="tele-modal-chart"></div>' +
    '<h4 class="hud-note" style="margin:14px 0 6px;font-weight:650;color:#e6edf3">Values</h4>' +
    '<div style="overflow:auto;max-height:32vh;border:1px solid #30363d;border-radius:10px">' +
    seriesTable + '</div></div></div>';
  document.body.appendChild(el);

  hudBarChart(document.getElementById('tele-modal-chart'), m, { height: 240 });

  const close = () => {
    destroyCharts(el);
    el.remove();
  };
  el.addEventListener('click', (e) => {
    if (e.target === el) close();
  });
  el.querySelector('#tele-modal-close').addEventListener('click', close);
  el.querySelector('#tele-modal-full').addEventListener('click', () => {
    close();
    state.telemetry.tab = metricKey;
    try {
      history.pushState(
        { nav: 'telemetry', tab: metricKey },
        '',
        navPath('telemetry', metricKey)
      );
    } catch (_) {}
    mount(false);
  });
  const onKey = (e) => {
    if (e.key === 'Escape') {
      close();
      document.removeEventListener('keydown', onKey);
    }
  };
  document.addEventListener('keydown', onKey);
}

/** Mount charts into a freshly-rendered telemetry page. */
function bindTelemetryPanel() {
  const root = $('#workspace-body');
  if (!root) return;
  const t = state.telemetry || {};

  if (t.tab && t.tab !== 'overview') {
    destroyCharts(root);
    return; // drill-down is a table, no charts
  }

  destroyCharts(root);
  (t.order || []).forEach((key) => {
    const m = t.metrics[key];
    const host = root.querySelector('[data-hud-chart="' + key + '"]');
    if (!m || !host) return;
    hudBarChart(host, m, {
      height: 220,
      onBarClick: () => openTelemetryModal(key),
    });
  });

  root.querySelectorAll('[data-hud-open]').forEach((btn) => {
    btn.addEventListener('click', () => openTelemetryModal(btn.getAttribute('data-hud-open')));
  });
  root.querySelector('#btn-tele-refresh')?.addEventListener('click', async () => {
    await refreshTelemetry();
    toast('Telemetry refreshed');
    mount(false);
  });
}

/* ==================================================================
   Evidence Center screen
   SSOT = /api/evidence/list + /api/evidence/<id>
   Detail is URL-addressable so a verdict can be reported as a link.
   ================================================================== */

async function refreshEvidence() {
  try {
    const q = state.evidence.filter
      ? '?target=' + encodeURIComponent(state.evidence.filter)
      : '';
    const res = await fetch('/api/evidence/list' + q);
    const data = await res.json();
    state.evidence.rows = data.evidence || [];
    state.evidence.root = data.root || '';
    state.evidence.msg = data.ok ? '' : (data.error || 'evidence API error');
    state.evidence.loaded = true;
  } catch (e) {
    state.evidence.msg = 'Evidence API unavailable: ' + (e.message || e);
    state.evidence.loaded = true;
  }
}

async function loadEvidenceDetail(id) {
  state.evidence.detailId = id;
  state.evidence.detail = null;
  try {
    const res = await fetch('/api/evidence/' + encodeURIComponent(id));
    state.evidence.detail = await res.json();
  } catch (e) {
    state.evidence.detail = { ok: false, error: String(e) };
  }
}

async function loadLlm100() {
  state.evidence.llm100 = null;
  try {
    const res = await fetch('/api/evidence/llm-100');
    state.evidence.llm100 = await res.json();
  } catch (e) {
    state.evidence.llm100 = { ok: false, error: String(e) };
  }
}

function openEvidence(id) {
  loadEvidenceDetail(id).then(() => {
    const body = $('#workspace-body');
    if (body && state.nav === 'evidence') {
      body.innerHTML = workspaceHtml();
      bindEvidencePanel();
    }
    try {
      history.pushState({ nav: 'evidence', tab: id }, '', navPath('evidence', id));
    } catch (_) {}
  });
}

function bindEvidencePanel() {
  const root = $('#workspace-body');
  if (!root) return;

  root.querySelectorAll('[data-ev-open]').forEach((row) => {
    row.addEventListener('click', () => openEvidence(row.getAttribute('data-ev-open')));
  });
  root.querySelector('#btn-ev-refresh')?.addEventListener('click', async () => {
    const el = root.querySelector('#ev-filter');
    if (el) state.evidence.filter = el.value.trim();
    await refreshEvidence();
    toast('Evidence refreshed');
    mount(false);
  });
  root.querySelector('#btn-llm100-refresh')?.addEventListener('click', async () => {
    await loadLlm100();
    toast('LLM 100 refreshed');
    mount(false);
  });
  root.querySelector('#ev-filter')?.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    state.evidence.filter = e.target.value.trim();
    await refreshEvidence();
    mount(false);
  });
  root.querySelector('#btn-ev-back')?.addEventListener('click', () => {
    state.evidence.detailId = '';
    state.evidence.detail = null;
    try {
      history.pushState({ nav: 'evidence', tab: 'list' }, '', navPath('evidence', 'list'));
    } catch (_) {}
    mount(false);
  });
  root.querySelector('#btn-ev-copy')?.addEventListener('click', async () => {
    const url = window.location.origin + navPath('evidence', state.evidence.detailId);
    try {
      await navigator.clipboard.writeText(url);
      toast('URL copied');
    } catch (_) {
      toast(url);
    }
  });
}

/* ==================================================================
   OpenClaw settings screen
   SSOT = /api/openclaw/report (openclaw_settings.py)
   Shows config AND a live probe, because the failure is a contradiction:
   a tool can fail while its settings key reads true.
   ================================================================== */

async function refreshOpenclaw(opts) {
  // THE TRIGGER POINT (2026-09-25).
  //
  // THE HUMAN: "these fucking deign is wrong, it make the site become slow and
  // slow" / "by trigger point before have the call!! not need tochecking status
  // : alive".
  //
  // MEASURED, and this is the defect: the TIMER called this, and it fetched
  // `/api/openclaw/report`, whose route called `check_openclaw_status()` — a
  // LIVE MCP round-trip. OpenClaw Companion is not running, so port 8765
  // refuses, and a REFUSED LOOPBACK CONNECT costs ~2040 ms on this machine.
  // The UI polls every 5 s and the cache TTL was 8 s, so the cache was ALWAYS
  // expired: MEASURED, 4 of 8 polls stalled 2-3.6 s (50%).
  //
  // SO: the TIMER now reads the background refresher's CACHE (instant), and
  // only an explicit USER action hits the TRIGGER POINT
  // (`POST /api/openclaw/refresh`), which does the real work.
  //
  // `probe` IS STILL OPT-IN (2026-09-23): a status read must not open an MCP
  // session, because OpenClaw raises its "capturing your screen" consent
  // notification when a session starts.
  const wantProbe = !!(opts && opts.probe);
  try {
    if (wantProbe) {
      // THE TRIGGER POINT: a user asked for a fresh answer.
      const res = await fetch('/api/openclaw/refresh', { method: 'POST' });
      const data = await res.json();
      const oc = data.openclaw || {};
      Object.assign(state.openclaw, oc, { loaded: true });
      state.openclaw.error = oc.ok ? '' : (oc.error || 'probe error');
      state.openclaw.cacheAgeSec = data.age_sec;
      state.openclaw.cacheStale = data.stale;
    } else {
      // THE TIMER: read the cache. This must never block.
      const res = await fetch('/api/openclaw/status');
      const data = await res.json();
      const oc = data.openclaw || {};
      Object.assign(state.openclaw, oc, { loaded: true });
      state.openclaw.error = oc.ok ? '' : (oc.error || 'probe error');
      state.openclaw.cacheAgeSec = oc.cache_age_sec;
      state.openclaw.cacheStale = oc.cache_stale;
    }
  } catch (e) {
    state.openclaw.loaded = true;
    state.openclaw.ok = false;
    state.openclaw.error = 'OpenClaw API unavailable: ' + (e.message || e);
  }
}

function bindOpenclawPanel() {
  $('#btn-oc-refresh')?.addEventListener('click', async () => {
    // The ONE place that asks for the live probe: a user pressed Refresh.
    await refreshOpenclaw({ probe: true });
    toast('OpenClaw refreshed');
    mount(false);
  });
  // Every tool chip opens a full-detail modal (description + input schema +
  // side-effect flag). Names alone hid that screen.snapshot captures the screen.
  document.querySelectorAll('[data-oc-tool]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openMcpToolModal(btn.getAttribute('data-oc-tool'), state, esc);
    });
  });
  $('#btn-oc-connect')?.addEventListener('click', async () => {
    const btn = $('#btn-oc-connect');
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Connecting…';
    }
    try {
      const res = await fetch('/api/openclaw/connect', { method: 'POST' });
      const data = await res.json();
      const c = data.connect || {};
      if (data.ok) {
        toast('OpenClaw connected (' + (c.action || 'ok') + ')');
      } else {
        toast('Connect failed: ' + (c.error || data.error || 'unknown'));
      }
    } catch (e) {
      toast('Connect failed: ' + (e.message || e));
    }
    // A user pressed Connect, so the live probe is wanted here.
    await refreshOpenclaw({ probe: true });
    mount(false);
  });
}

function openToolDetailModal(tool) {
  const old = document.getElementById('tool-detail-modal');
  if (old) old.remove();
  const tr = state.toolRegistry || {};
  const related = (tr.deliveryLog || []).filter((d) => (d.tool || '').includes(tool.hotkey));
  const relRows = related.length
    ? related
        .map((d) =>
          '<tr class="border-b border-line align-top"><td class="whitespace-nowrap px-3 py-2 text-xs mono text-muted">' + esc(d.date) +
          '</td><td class="px-3 py-2 text-xs text-ink">' + esc(d.event) + '</td><td class="px-3 py-2 text-xs text-muted">' + esc(d.evidence) + '</td></tr>'
        )
        .join('')
    : '<tr><td colspan="3" class="px-3 py-4 text-center text-xs text-muted">無相關交付記錄。</td></tr>';
  const el = document.createElement('div');
  el.id = 'tool-detail-modal';
  el.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4';
  el.innerHTML =
    '<div class="w-full max-w-2xl rounded-2xl border border-line bg-panel p-5 shadow-panel">' +
    '<div class="mb-3 flex items-center justify-between">' +
    '<div class="flex items-center gap-2">' +
    '<span class="rounded-lg bg-accent px-2.5 py-1 text-sm font-bold text-white">' + esc(tool.hotkey) + '</span>' +
    '<h3 class="text-base font-semibold text-ink">' + esc(tool.name) + '</h3>' +
    '<span class="mono text-xs text-muted">#' + esc(tool.num) + '</span>' +
    trStatusBadge(tool.status) +
    '</div>' +
    '<button id="tool-detail-close" class="rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">✕</button>' +
    '</div>' +
    '<div class="grid gap-2 text-sm sm:grid-cols-2">' +
    '<div><span class="text-muted">Tool ID：</span><span class="mono">' + esc(tool.tool_id) + '</span></div>' +
    '<div><span class="text-muted">腳本：</span><span class="mono">' + esc(tool.script) + '</span></div>' +
    '<div><span class="text-muted">用 mouse？</span> ' + esc(tool.mouse) + '</div>' +
    '<div><span class="text-muted">前提：</span>' + esc(tool.prereq) + '</div>' +
    '</div>' +
    '<div class="mt-3 text-sm"><span class="text-muted">功能：</span>' + esc(tool.function) + '</div>' +
    '<div class="mt-2 text-sm"><span class="text-muted">驗證方式：</span><span class="mono">' + esc(tool.verification) + '</span></div>' +
    '<h4 class="mt-4 mb-1 text-sm font-semibold text-ink">相關交付記錄（' + related.length + '）</h4>' +
    '<div class="overflow-x-auto rounded-xl border border-line"><table class="w-full text-left">' +
    '<thead class="bg-soft/60 text-xs text-muted"><tr><th class="px-3 py-2">日期</th><th class="px-3 py-2">事件</th><th class="px-3 py-2">證據</th></tr></thead>' +
    '<tbody>' + relRows + '</tbody></table></div>' +
    '</div>';
  document.body.appendChild(el);
  const close = () => el.remove();
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  el.querySelector('#tool-detail-close').addEventListener('click', close);
  const onKey = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}

function bindToolRegistryPanel() {
  $('#btn-tr-refresh')?.addEventListener('click', async () => {
    await refreshToolRegistry();
    const body = $('#workspace-body');
    if (body && state.nav === 'tool-registry') {
      body.innerHTML = toolRegistryHtml();
      bindToolRegistryPanel();
    }
    toast('Tool Registry refreshed');
  });
  $('#btn-cl-refresh')?.addEventListener('click', async () => {
    await refreshPermissionChecklist();
    const body = $('#workspace-body');
    if (body && state.nav === 'tool-registry') {
      body.innerHTML = toolRegistryHtml();
      bindToolRegistryPanel();
    }
    toast('Permission checklist refreshed');
  });
  document.querySelectorAll('[data-tr-tool]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const num = btn.getAttribute('data-tr-tool');
      const tool = (state.toolRegistry.tools || []).find((t) => String(t.num) === String(num));
      if (tool) openToolDetailModal(tool);
    });
  });
  document.querySelectorAll('[data-tr-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.toolRegistry.tab = btn.getAttribute('data-tr-tab') || 'list';
      try {
        history.pushState(
          { nav: 'tool-registry', tab: state.toolRegistry.tab },
          '',
          navPath('tool-registry', state.toolRegistry.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });
}

// THE DETAIL MODAL (2026-09-25). THE HUMAN: "at the end + button [Detail]
// onclick can see the detail".
//
// WHY A MODAL AND NOT A WIDER TABLE: MEASURED, the table already had 9 columns
// and the human was asking to REMOVE one (IDE, which the Environment string
// already carries). A modal shows EVERY field the payload carries — including
// the ones the table deliberately hides (`sha256`, `chat_hash`, `chat_id`,
// `action`, `llm_free_text`) — without widening the table. It is the pattern
// this repo already uses (`openMcpToolModal`).
//
// A FIELD WITH NO VALUE RENDERS `-`, never blank: a blank cell reads as "no
// problem", which is the failure this whole page exists to prevent.
// THE PAIR KEY, SHOWN ONCE (2026-09-25). THE HUMAN, on the Detail modal:
//     "chat_hash / chat_hash_recomputed ... why need to have 2? purpose is?"
//
// MEASURED, and the human is right: they are ONE fact, not two. Both are
// sha256("<chat_id>|<session_id>"). `chat_hash` is what the WRITER stored;
// `chat_hash_recomputed` is the SAME formula re-derived by
// `db_schema._backfill_chat_hash_recomputed` (db_schema.py:334).
//
// WHY THE SECOND COLUMN EXISTS AT ALL: `chat_id` CHANGED MEANING. It used to be
// sha256(session_id) (a TEXT hash) and is now `chat_main.id` (an INTEGER). So a
// LEGACY row's `chat_hash` was built from the OLD chat_id and is wrong under the
// new meaning. The append-only rule forbids fixing it in place
// (db_schema.py:343 "never touches chat_hash, so audit history stays intact"),
// so the correct value was added BESIDE it.
//
// MEASURED: 311 of 344 rows AGREE, so the modal was printing the same 64-char
// string twice with no explanation. THAT is the user-hostile part. So:
//   * agree  -> ONE row, labelled as the pair key
//   * differ -> BOTH rows, the legacy one marked, with the reason
function watchdogHtml() {
  const w = state.watchdog || {};
  const events = w.events || [];
  const selected =
    events.find((e) => e.id === w.selectedId) ||
    events[0] ||
    null;
  if (selected && !w.selectedId) state.watchdog.selectedId = selected.id;
  const shot = (selected && selected.screenshot) || {};
  const detail = (selected && selected.detail) || {};
  const logTail = (w.logTail || []).slice().reverse().join('\n') || '(no log yet)';

  const eventRows = events.length
    ? events
        .map((ev) => {
          const active = selected && ev.id === selected.id;
          const hasShot = !!(ev.screenshot && ev.screenshot.ok && ev.screenshot.url);
          return (
            '<button type="button" data-wd-id="' +
            esc(ev.id) +
            '" class="w-full border-b border-line px-3 py-2.5 text-left transition hover:bg-soft/80 ' +
            (active ? 'bg-accent-soft/70' : '') +
            '"><div class="flex items-center justify-between gap-2">' +
            levelBadge(ev.level, ev.kind) +
            '<span class="text-[11px] mono text-muted">' +
            esc(ev.local_time || ev.ts || '') +
            '</span></div><div class="mt-1 text-sm font-medium text-ink">' +
            esc(ev.kind || 'event') +
            (hasShot ? ' | [cam]' : '') +
            '</div><div class="mt-0.5 line-clamp-2 text-xs text-muted">' +
            esc(ev.message || '') +
            '</div></button>'
          );
        })
        .join('')
    : '<div class="p-4 text-sm text-muted">No watchdog events yet. Alerts appear when mouse_spot_helper goes down / restarts.</div>';

  const helperPid =
    w.helperPid ||
    (detail && detail.helper_pid) ||
    (selected && selected.detail && selected.detail.helper_pid) ||
    null;
  const st = state.status || {};
  const llmOn = !!st.ollamaOk;
  const modelLabel = st.modelPresent ? st.model || 'model ok' : 'model missing';
  const oc = w.openclaw || {};
  const wh = w.workerHb || {};
  const ocOn = !!st.openclawOk;
  const ocConfigured = !!st.openclawConfigured;
  const hbOn = !!st.workerHbOk;
  const ocDetail = ocOn
    ? (st.openclawTools ? st.openclawTools + ' tools' : 'MCP up') +
      (oc.latency_ms != null ? ' · ' + oc.latency_ms + 'ms' : '')
    : st.openclawError || oc.error || (ocConfigured ? 'MCP down' : 'env not set');
  const hbDetail = hbOn
    ? (st.workerHbName || wh.worker_name || 'worker') +
      (st.workerHbAgeSec != null ? ' · age ' + st.workerHbAgeSec + 's' : '')
    : st.workerHbError || wh.error || 'no fresh heartbeat';

  // THE CATALOGUE: group the statuses by WHAT THEY ARE, so "so many things" is
  // classifiable instead of a flat row of pills. MEASURED: the card below was
  // titled "three stacks" while listing SIX blocks, so the reader could not tell
  // a MONITOR from a LIFECYCLE from a ROUTE HEALTH.
  const STATUS_CATALOGUE = [
    {
      group: 'Liveness of a supervised unit',
      source: 'watchdog_health',
      note: 'seconds since the unit last wrote its own evidence',
      values: [['ALIVE', 'v', 'inside its window'], ['DEAD', 'x', 'past the window'],
               ['NEVER', '?', 'no evidence was ever written (NOT the same as DEAD)']],
    },
    {
      group: 'Keep-alive decision',
      source: 'keepalive_runner.decide',
      note: 'what the tick DID about a unit that is not ALIVE',
      values: [['none', 'v', 'nothing to do'], ['start', 'a', 'no process: started one'],
               ['report_wedged', 'w', 'process running, writing nothing (NEVER killed)']],
    },
    {
      group: 'Fault lifecycle',
      source: 'fault_event.status',
      note: 'one OPEN row per ongoing occurrence',
      values: [['open', 'a', 'still happening'], ['resolved', 'v', 'closed by a resolve or an ack'],
               ['OPEN_ACTIONABLE', 'x', 'a report still asks for a human'],
               ['OPEN_UNREPORTED', 'x', 'open with NO report: nobody was told']],
    },
    {
      group: 'Report workflow',
      source: 'chat_center_message.status',
      note: 'where the WORK stands, a different axis from `event`',
      values: [['ask', 'a', 'needs a human (amber on purpose)'], ['done', 'v', 'finished or retired'],
               ['progressive', 'i', 'in flight'], ['QC', 'i', 'awaiting an independent verdict'],
               ['superseded', 'n', 'a newer reminder replaced it']],
    },
    {
      group: 'Route health',
      source: 'route_registry.health',
      note: 'a declared route between two refs',
      values: [['OK', 'v', 'healthy'], ['NOWHERE', 'x', 'no target exists'],
               ['NEVER', 'x', 'never observed'], ['ONE_WAY', 'a', 'one direction only'],
               ['STALE', 'a', 'evidence too old'], ['UNKNOWN', '?', 'not measured yet']],
    },
  ];
  const CAT_GLYPH = { v: '\u2713', x: '\u2715', a: '!', i: 'i', w: 'W', n: '\u21b3', '?': '?' };
  const CAT_TONE = {
    v: 'text-emerald-600', x: 'text-rose-600', a: 'text-amber-600',
    i: 'text-sky-600', w: 'text-orange-600', n: 'text-slate-500', '?': 'text-slate-400',
  };
  const legendHtml = (vals) =>
    '<div class="mt-1.5 flex flex-wrap gap-1.5">' +
    vals.map(([name, tone, meaning]) =>
      '<span class="rounded-full bg-soft px-2 py-0.5 text-[11px]" title="' +
      esc(meaning) + '"><span class="mr-1 ' + (CAT_TONE[tone] || 'text-muted') + '">' +
      (CAT_GLYPH[tone] || '?') + '</span><span class="mono text-ink">' + esc(name) +
      '</span><span class="ml-1 text-muted">' + esc(meaning) + '</span></span>').join('') +
    '</div>';
  const catalogueCard =
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">' +
    'Status catalogue \u2014 what each value MEANS, grouped by what it describes</div>' +
    '<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">' +
    STATUS_CATALOGUE.map((g) =>
      '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
      '<div class="text-xs font-semibold text-ink">' + esc(g.group) + '</div>' +
      '<div class="mt-0.5 text-[11px] text-muted"><span class="mono">' + esc(g.source) +
      '</span> \u00b7 ' + esc(g.note) + '</div>' +
      legendHtml(g.values) + '</div>').join('') +
    '</div></div>';

  const scopeCard =
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Monitors \u2014 the keep-alive owner, its target, and the observe-only stacks</div>' +
    '<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">1. mouse_spot_helper</div>' +
    '<div class="mt-1 text-xs text-muted">Keep-alive <strong>target</strong>. HTTP <span class="mono">:18765</span>. Restarted by helper_watchdog if down.</div>' +
    '<div class="mt-2">' +
    pill(!!st.helperOk, 'mouse_spot_helper ON', 'mouse_spot_helper OFF') +
    '</div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">2. helper_watchdog</div>' +
    '<div class="mt-1 text-xs text-muted">Keep-alive <strong>owner</strong>. Probes helper every ~15s. Does <strong>not</strong> restart OpenClaw MCP or workers.</div>' +
    '<div class="mt-2">' +
    pill(!!w.running, 'Watchdog ON', 'Watchdog OFF') +
    '</div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">3. LLM / Ollama</div>' +
    '<div class="mt-1 text-xs text-muted">Observed when helper is up. <strong>Not restarted</strong> by helper_watchdog if OFF.</div>' +
    '<div class="mt-2 flex flex-wrap gap-1.5">' +
    pill(llmOn, 'LLM ON', 'LLM OFF') +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted">' +
    esc(modelLabel) +
    '</span></div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">4. OpenClaw MCP</div>' +
    '<div class="mt-1 text-xs text-muted">Local MCP tools (snapshot / notify / ping). <strong>Observe only</strong> — not restarted by helper_watchdog.</div>' +
    '<div class="mt-2 flex flex-wrap gap-1.5">' +
    pill(ocOn, 'OpenClaw MCP ON', ocConfigured ? 'OpenClaw MCP OFF' : 'OpenClaw MCP n/a') +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted" title="' +
    esc(ocDetail) +
    '">' +
    esc(ocDetail) +
    '</span></div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">5. Worker heartbeat</div>' +
    '<div class="mt-1 text-xs text-muted">Worker pulse in <span class="mono">agent.db</span>. Faults via <span class="mono">watchdog.py</span> — not this keep-alive.</div>' +
    '<div class="mt-2 flex flex-wrap gap-1.5">' +
    pill(hbOn, 'Worker HB ON', 'Worker HB OFF') +
    '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted" title="' +
    esc(hbDetail) +
    '">' +
    esc(hbDetail) +
    '</span></div></div>' +
    '<div class="rounded-xl border border-line bg-soft/50 p-3">' +
    '<div class="text-xs font-semibold text-ink">Process IDs</div>' +
    '<div class="mt-1 space-y-1 text-xs mono text-muted">' +
    '<div><span class="font-semibold text-ink">watchdog pid</span> ' +
    esc(w.pid || '-') +
    ' <span class="text-[11px]">(helper_watchdog.py)</span></div>' +
    '<div><span class="font-semibold text-ink">helper pid</span> ' +
    esc(helperPid || '-') +
    ' <span class="text-[11px]">(mouse_spot_helper.py)</span></div>' +
    '</div></div></div></div>';

  const detailBlock = selected
    ? '<div class="space-y-3">' +
      '<div class="flex flex-wrap items-center gap-2">' +
      levelBadge(selected.level, selected.kind) +
      '<span class="text-sm font-semibold text-ink">' +
      esc(selected.kind || '') +
      '</span><span class="text-xs mono text-muted">' +
      esc(selected.local_time || selected.ts || '') +
      '</span></div>' +
      '<p class="text-sm text-ink">' +
      esc(selected.message || '') +
      '</p>' +
      '<pre class="max-h-40 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs mono text-muted">' +
      esc(JSON.stringify(detail, null, 2)) +
      '</pre>' +
      (shot.ok && shot.url
        ? '<div><div class="mb-1 text-xs font-medium text-muted">Desktop screenshot at alert</div>' +
          '<a href="' +
          esc(shot.url) +
          '" target="_blank" rel="noopener" class="block overflow-hidden rounded-xl border border-line bg-soft">' +
          '<img src="' +
          esc(shot.url) +
          '" alt="watchdog screenshot" class="max-h-[420px] w-full object-contain bg-black/5" />' +
          '</a><div class="mt-1 text-[11px] text-muted mono">' +
          esc(shot.path || shot.url) +
          (shot.bytes ? ' | ' + esc(shot.bytes) + ' bytes' : '') +
          '</div></div>'
        : shot && shot.error
          ? '<div class="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">Screenshot failed: ' +
            esc(shot.error) +
            '</div>'
          : '<div class="rounded-xl border border-line bg-soft/60 p-3 text-sm text-muted">No screenshot for this event.</div>') +
      '</div>'
    : '<div class="text-sm text-muted">Select an event to see why the watchdog alerted and the desktop screenshot.</div>';

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-end justify-between gap-3">' +
    '<div><h2 class="text-lg font-semibold">Watchdog</h2>' +
    '<p class="text-sm text-muted">helper_watchdog keep-alive for <span class="mono">mouse_spot_helper :18765</span> · LLM / OpenClaw MCP / Worker HB are <strong>observe-only</strong> here · screenshot evidence on helper down</p></div>' +
    '<div class="flex flex-wrap items-center gap-2">' +
    pill(!!w.running, 'Watchdog ON', 'Watchdog OFF') +
    pill(!!st.helperOk, 'mouse_spot_helper ON', 'mouse_spot_helper OFF') +
    pill(llmOn, 'LLM ON', 'LLM OFF') +
    pill(ocOn, 'OpenClaw MCP ON', ocConfigured ? 'OpenClaw MCP OFF' : 'OpenClaw MCP n/a') +
    pill(hbOn, 'Worker HB ON', 'Worker HB OFF') +
    (w.pid
      ? '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted" title="helper_watchdog.py process id">watchdog pid ' +
        esc(w.pid) +
        '</span>'
      : '') +
    (helperPid
      ? '<span class="rounded-full bg-soft px-2.5 py-1 text-xs mono text-muted" title="mouse_spot_helper.py process id">helper pid ' +
        esc(helperPid) +
        '</span>'
      : '') +
    '<button id="btn-wd-refresh" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button>' +
    '</div></div>' +
    (w.msg ? '<div class="text-sm text-muted">' + esc(w.msg) + '</div>' : '') +
    catalogueCard +
    scopeCard +
    '<div class="grid gap-4 lg:grid-cols-5">' +
    '<div class="lg:col-span-2 overflow-hidden rounded-2xl border border-line bg-panel shadow-panel">' +
    '<div class="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted">Events</div>' +
    '<div id="wd-event-list" class="max-h-[560px] overflow-auto">' +
    eventRows +
    '</div></div>' +
    '<div class="lg:col-span-3 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-3 text-xs font-semibold uppercase tracking-wide text-muted">Selected alert</div>' +
    detailBlock +
    '</div></div>' +
    '<div class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 flex items-center justify-between"><div class="text-xs font-semibold uppercase tracking-wide text-muted">helper_watchdog.log (tail)</div>' +
    '<span class="text-[11px] text-muted">auto-refresh with page</span></div>' +
    '<pre class="max-h-56 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs mono leading-relaxed text-ink">' +
    esc(logTail) +
    '</pre></div></div>'
  );
}

function templatesHtml() {
  const t = state.templates || {};
  const rows = t.rows || [];
  const f = t.form || {};
  const msgClass = t.msgOk ? 'text-emerald-700' : 'text-rose-700';
  const rowsHtml = rows.length
    ? rows
        .map((r) => {
          const active = Number(r.is_active) === 1;
          const isDefault = r.prompt_setting_key === 'verdict_3line';
          return (
            '<tr class="border-b border-line/80">' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(r.id) +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs mono">' +
            esc(r.prompt_setting_key) +
            '</td>' +
            '<td class="px-2 py-1.5 text-sm">' +
            esc(r.name) +
            '</td>' +
            '<td class="px-2 py-1.5 text-xs">' +
            esc(r.catalog_id ?? 0) +
            '</td>' +
            '<td class="px-2 py-1.5 text-[11px] text-muted">' +
            esc(fmtLocal(r.updated_at || r.created_at)) +
            '</td>' +
            '<td class="px-2 py-1.5"><span class="rounded-full px-2 py-0.5 text-[10px] ' +
            (active ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-800') +
            '">' +
            (active ? 'on' : 'off') +
            '</span></td>' +
            '<td class="px-2 py-1.5 whitespace-nowrap">' +
            '<button type="button" data-tpl-act="edit" data-tpl-id="' +
            esc(r.id) +
            '" class="mr-1 rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">Edit</button>' +
            '<button type="button" data-tpl-act="preview" data-tpl-id="' +
            esc(r.id) +
            '" class="mr-1 rounded-lg border border-line px-2 py-1 text-xs hover:bg-soft">Preview</button>' +
            '<button type="button" data-tpl-act="del" data-tpl-id="' +
            esc(r.id) +
            '" class="rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700 hover:bg-rose-100" ' +
            (isDefault ? 'disabled' : '') +
            '>Delete</button></td></tr>'
          );
        })
        .join('')
    : '<tr><td colspan="7" class="px-3 py-6 text-center text-sm text-muted">No prompt settings loaded</td></tr>';

  return (
    '<div class="mx-auto flex max-w-6xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-start justify-between gap-2">' +
    '<div><h2 class="text-lg font-semibold">Prompt Setting Manager</h2>' +
    '<p class="mt-1 text-sm text-muted">Prompt Setting：設定 LLM 返回結果嘅格式規則（JSON / YESNO / 固定文字），唔包含任務內容。Manage <code class="text-xs">format_templates</code> under Task Monitor. Scanner/worker read DB live; missing keys fall back to <code class="text-xs">verdict_3line</code>.</p></div>' +
    '<button type="button" id="btn-tpl-refresh" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Refresh</button></div>' +
    (t.msg
      ? '<div class="rounded-xl border border-line bg-panel px-3 py-2 text-sm ' +
        msgClass +
        '">' +
        esc(t.msg) +
        '</div>'
      : '') +
    '<div class="grid gap-4 lg:grid-cols-2">' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel overflow-auto">' +
    '<div class="mb-3 flex flex-wrap items-center justify-between gap-2">' +
    '<h3 class="text-sm font-semibold">Prompt Settings</h3>' +
    '<label class="flex items-center gap-2 py-1 text-xs text-muted"><input id="tpl-show-all" type="checkbox" ' +
    (t.showAll ? 'checked' : '') +
    '/> show inactive</label></div>' +
    '<table class="w-full min-w-[520px] border-collapse text-left"><thead><tr class="text-[11px] uppercase tracking-wide text-muted">' +
    '<th class="px-2 py-1">ID</th><th class="px-2 py-1">Setting Key</th><th class="px-2 py-1">Setting Name</th><th class="px-2 py-1">Cat</th><th class="px-2 py-1">Updated</th><th class="px-2 py-1">Act</th><th class="px-2 py-1"></th>' +
    '</tr></thead><tbody id="tpl-tbody">' +
    rowsHtml +
    '</tbody></table></section>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<h3 class="mb-2 text-sm font-semibold" id="tpl-form-title">' +
    (t.editId != null ? 'Edit #' + esc(t.editId) : 'New prompt setting') +
    '</h3>' +
    '<input type="hidden" id="tpl-edit-id" value="' +
    esc(t.editId != null ? t.editId : '') +
    '" />' +
    '<label class="mt-1 block text-xs font-medium text-muted">Setting Key' +
    '<input id="tpl-key" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm mono" value="' +
    esc(f.prompt_setting_key || '') +
    '" ' +
    (f.prompt_setting_key === 'verdict_3line' ? 'disabled' : '') +
    ' /></label>' +
    '<label class="mt-2 block text-xs font-medium text-muted">Setting Name' +
    '<input id="tpl-name" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm" value="' +
    esc(f.name || '') +
    '" /></label>' +
    '<label class="mt-2 block text-xs font-medium text-muted">description / sub-agent notes' +
    '<input id="tpl-desc" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm" value="' +
    esc(f.description || '') +
    '" /></label>' +
    '<label class="mt-2 block text-xs font-medium text-muted">catalog_id' +
    '<input id="tpl-catalog" type="number" min="0" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm" value="' +
    esc(f.catalog_id ?? 0) +
    '" /></label>' +
    '<label class="mt-2 block text-xs font-medium text-muted">instruction' +
    '<textarea id="tpl-instruction" rows="8" class="mono mt-1 w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed">' +
    esc(f.instruction || '') +
    '</textarea></label>' +
    '<div class="mt-3 flex flex-wrap gap-2">' +
    '<button type="button" id="btn-tpl-save" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Save</button>' +
    '<button type="button" id="btn-tpl-reset" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Reset form</button></div>' +
    '<h3 class="mb-2 mt-5 text-sm font-semibold">Preview</h3>' +
    '<label class="block text-xs font-medium text-muted">sample task prompt' +
    '<textarea id="tpl-sample" rows="3" class="mono mt-1 w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm">' +
    esc(t.samplePrompt || '') +
    '</textarea></label>' +
    '<button type="button" id="btn-tpl-preview" class="mt-2 rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Preview final prompt</button>' +
    '<label class="mt-2 block text-xs font-medium text-muted">final_prompt</label>' +
    '<pre id="tpl-preview-out" class="mt-1 max-h-56 overflow-auto rounded-xl border border-line bg-soft/70 p-3 text-xs mono leading-relaxed whitespace-pre-wrap">' +
    esc(t.previewOut || '') +
    '</pre></section></div></div>'
  );
}

function workspaceHtml() {
  if (state.nav === 'watchdog') return watchdogHtml();
  if (state.nav === 'openclaw') return openclawHtml(state, esc);
  if (state.nav === 'evidence') {
    if (state.evidence?.detailId === 'LLM 100') return llm100Html(state, esc);
    return state.evidence?.detailId
      ? evidenceDetailHtml(state, esc)
      : evidenceListHtml(state, esc);
  }
  if (state.nav === 'telemetry') {
    return state.telemetry?.tab && state.telemetry.tab !== 'overview'
      ? telemetryDetailHtml()
      : telemetryHtml();
  }
  if (state.nav === 'skill-ssot') return skillHtml();
  if (state.nav === 'skill-learning') return skillLearningHtml();
  if (state.nav === 'llm-templates') return templatesHtml();
  if (state.nav === 'tool-registry') return toolRegistryHtml();
  // The two chat pages became ONE (2026-09-26). `chat_identity` and
  // `chat_center` are ALIASED to `conversation-center`, so this is the only
  // branch either old URL reaches.
  if (state.nav === 'conversation-center') {
    return '<div id="conversation-center-root"></div>';
  }
  if (state.nav === 'user-environment') return userEnvironmentHtml();
  // Chat Center is a Vue 3 app — mounted asynchronously after the shell renders.
  // Three tabs, three mount roots: the 3-step flow, the Discovery List, and the
  // Setting page (STEP 1 Question / STEP 2 Answer).
  if (state.nav === 'chat-center') {
    const ccTab = state.chatCenter?.tab || 'flow';
    if (ccTab === 'list') return '<div id="chat-center-list-root"></div>';
    if (ccTab === 'workflow') return '<div id="chat-center-workflow-root"></div>';
    if (ccTab === 'setting') return '<div id="chat-center-setting-root"></div>';
    return '<div id="chat-center-root"></div>';
  }
  // Capability Center is also a Vue 3 app (same mount pattern).
  if (state.nav === 'capability-center') return '<div id="capability-center-root"></div>';
  if (state.nav === 'ticket-center') return '<div id="ticket-center-root"></div>';
  if (state.nav === 'screen-watch') return '<div id="screen-watch-root"></div>';
  if (state.nav === 'worker') return '<div id="worker-root"></div>';
  if (state.nav === 'identity') return '<div id="identity-root"></div>';
  if (state.nav === 'playwright') return '<div id="playwright-root"></div>';
  if (state.nav === 'mode-sessions') return '<div id="mode-sessions-root"></div>';
  if (state.nav === 'terminology') return '<div id="terminology-root"></div>';
  if (state.nav === 'generator') return '<div id="generator-root"></div>';
  if (state.nav === 'question') return '<div id="question-root"></div>';
  if (state.nav === 'consultant') return '<div id="consultant-root"></div>';
  if (state.nav === 'registers') return '<div id="registers-root"></div>';
  if (state.nav !== 'task-center') {
    const label = NAV.find((n) => n.id === state.nav)?.label || state.nav;
    return (
      '<div class="rounded-2xl border border-line bg-panel p-8 shadow-panel"><h2 class="text-lg font-semibold">' +
      esc(label) +
      '</h2><p class="mt-2 text-sm text-muted">Placeholder panel - connect helper APIs in a follow-up.</p></div>'
    );
  }

  if (state.tab === 'report') return reportHtml();
  if (state.tab === 'queue') return taskQueueHtml();
  if (state.tab === 'graph') return graphHtml();

  const tabHint =
    state.tab === 'chat'
      ? 'Chat Box focuses the prompt area for iterative edits.'
      : state.tab === 'result'
        ? 'Result Output shows the latest analysis / improve output.'
        : 'Prompt Analyze holds task metadata and actions.';

  return (
    '<div class="mx-auto flex max-w-4xl flex-col gap-4">' +
    '<div class="flex flex-wrap items-center justify-between gap-2"><p class="text-sm text-muted">' +
    tabHint +
    '</p><div class="flex flex-wrap gap-2">' +
    '<button id="btn-new" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">New</button>' +
    '<button id="btn-save" type="button" class="rounded-xl bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">Save</button>' +
    '<button id="btn-delete" type="button" class="rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm text-rose-700 hover:bg-rose-100">Delete</button>' +
    '</div></div>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel ' +
    (state.tab === 'result' ? 'hidden' : '') +
    '"><div class="grid gap-3 sm:grid-cols-2">' +
    '<label class="block text-xs font-medium text-muted">task_id<input id="f-task-id" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" /></label>' +
    '<label class="block text-xs font-medium text-muted">writer<input id="f-writer" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" /></label>' +
    '<label class="block text-xs font-medium text-muted">session_id <span class="font-normal text-muted">(IDE session | empty until IDE works)</span><input id="f-session" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" placeholder="No IDE session yet" /></label>' +
    '<label class="block text-xs font-medium text-muted">status<select id="f-status" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent"><option value="draft">draft</option><option value="ready">ready</option><option value="running">running</option><option value="done">done</option><option value="error">error</option></select></label>' +
    '</div><p class="mt-2 text-[11px] text-muted">session_id = VS Code / Cursor / Work Buddy / Codex session. New tasks leave it blank; after IDE work, paste reply and Apply IDs.</p>' +
    '<label class="mt-3 block text-xs font-medium text-muted">context<input id="f-context" class="mt-1 w-full rounded-xl border border-line bg-canvas px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent" /></label></section>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel ' +
    (state.tab === 'result' ? 'hidden' : '') +
    '"><div class="mb-2 flex flex-wrap items-center justify-between gap-2"><h3 class="text-sm font-semibold">Prompt</h3><div class="flex flex-wrap items-center gap-2"><label class="text-[11px] text-muted">IDE target<select id="f-ide-target" class="ml-1 rounded-lg border border-line bg-canvas px-2 py-1 text-xs"><option>Visual Studio Code</option><option>Cursor</option><option>Work Buddy</option><option>Codex</option></select></label>' +
    '<label class="text-[11px] text-muted">model<select id="f-model" class="ml-1 rounded-lg border border-line bg-canvas px-2 py-1 text-xs">' +
    (state.report.models.length
      ? state.report.models.map((m) => '<option value="' + esc(m.id) + '"' +
          (m.id === (state.draft.model || '') ? ' selected' : '') + '>' +
          esc(m.label || m.id) + ' (' + esc(m.kind || 'text') + ')</option>').join('')
      : '<option value="">(no model list — /api/llm-tasks offline)</option>') +
    '</select></label><span class="text-[11px] text-muted mono">monospace</span></div></div>' +
    '<textarea id="f-prompt" rows="12" placeholder="Paste prompt here..." class="mono w-full resize-y rounded-xl border border-line bg-canvas px-3 py-2 text-sm leading-relaxed outline-none focus:ring-2 focus:ring-accent"></textarea>' +
    '<div class="mt-3 flex flex-wrap gap-2"><button id="btn-from-template" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">From IDE template</button><button id="btn-improve" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Improve</button><button id="btn-analyze" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Analyze</button><button id="btn-apply-ids" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft" title="Parse session_id/task_id/writer from reply">Apply IDs from reply</button><button id="btn-apply" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Apply to chat</button><button id="btn-copy" type="button" class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">Copy result</button></div></section>' +
    '<section class="rounded-2xl border border-line bg-panel p-4 shadow-panel ' +
    (state.tab === 'chat' ? 'hidden' : '') +
    '"><div class="mb-2 flex items-center justify-between"><h3 class="text-sm font-semibold">Result</h3><span class="text-[11px] text-muted">readonly</span></div>' +
    '<textarea id="f-result" rows="10" readonly class="mono w-full resize-y rounded-xl border border-line bg-soft/80 px-3 py-2 text-sm leading-relaxed text-ink outline-none"></textarea></section></div>'
  );
}

// ---------------------------------------------------------------------------
// Task Queue — the 7B skill_task_queue, made visible and provable.
// WHY: the queue had a worker (worker_once) and a table, but the ONLY surface
// was a telemetry chart, so "which model ran this, and did it finish" could not
// be answered from the UI. This panel shows every row verbatim, including
// assigned_model, retry/handoff counts, result and error_msg.
// ---------------------------------------------------------------------------

const QUEUE_STATUS_CLASSES = {
  success: 'bg-emerald-100 text-emerald-700',
  running: 'bg-sky-100 text-sky-700',
  pending: 'bg-slate-100 text-slate-700',
  retry: 'bg-amber-100 text-amber-700',
  handoff: 'bg-amber-100 text-amber-700',
  failed: 'bg-rose-100 text-rose-700',
};

function queueStatusClass(s) {
  return QUEUE_STATUS_CLASSES[String(s || '').toLowerCase()] || 'bg-slate-100 text-slate-600';
}

// ---------------------------------------------------------------------------
// Preflight panel — the environment gate, made visible.
//
// WHY: env_task_proof Rule 6 — a recorded check is not a gate. The gate lives in
// f_env_preflight.py and REFUSES an enqueue (HTTP 409). This panel shows the
// SAME report the gate uses, so a human can see WHY a task was refused instead
// of guessing. It is read-only: it never enqueues and never mutates.
// ---------------------------------------------------------------------------

function preflightHtml() {
  const q = state.taskQueue;
  const p = q.preflight;
  const scopes = ['llm', 'video', 'ui', 'all'];
  const scopeSel = '<label class="text-[11px] text-muted">scope<select id="pf-scope" ' +
    'class="ml-1 rounded-lg border border-line bg-canvas px-2 py-1 text-xs">' +
    scopes.map((s) => '<option value="' + s + '"' +
      (s === q.scope ? ' selected' : '') + '>' + s + '</option>').join('') +
    '</select></label>';

  if (!p) {
    return '<div class="mb-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
      '<div class="flex flex-wrap items-center justify-between gap-2">' +
      '<h3 class="text-sm font-semibold text-ink">Environment preflight</h3>' +
      '<div class="flex items-center gap-2">' + scopeSel +
      '<button id="btn-pf-refresh" type="button" class="rounded-xl border border-line ' +
      'bg-panel px-3 py-1.5 text-sm hover:bg-soft">' +
      (q.preflightBusy ? 'Checking…' : 'Run preflight') + '</button></div></div>' +
      '<p class="mt-2 text-xs text-muted">' +
      (q.preflightMsg ? esc(q.preflightMsg)
        : 'Not run yet. The gate runs this before every enqueue.') + '</p></div>';
  }

  const checks = p.checks || [];
  const required = new Set(p.required || []);
  const rows = checks.map((c) => {
    const isReq = required.has(c.name);
    const mark = c.ok ? 'PASS' : (isReq ? 'FAIL' : 'WARN');
    const cls = c.ok ? 'bg-emerald-100 text-emerald-700'
      : (isReq ? 'bg-rose-100 text-rose-700' : 'bg-amber-100 text-amber-700');
    return '<tr class="border-t border-line align-top">' +
      '<td class="px-3 py-1.5"><span class="rounded-full px-2 py-0.5 text-[11px] ' +
        'font-medium ' + cls + '">' + mark + '</span></td>' +
      '<td class="mono px-3 py-1.5 text-xs text-ink">' + esc(c.name) +
        (isReq ? '' : '<span class="ml-1 text-muted">(advisory)</span>') + '</td>' +
      '<td class="px-3 py-1.5 text-xs text-muted">' + esc(c.detail || '') + '</td></tr>';
  }).join('');

  const banner = p.ready
    ? '<span class="rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold ' +
      'text-emerald-700">READY — a task may be enqueued</span>'
    : '<span class="rounded-full bg-rose-100 px-3 py-1 text-xs font-semibold ' +
      'text-rose-700">NOT READY — enqueue is REFUSED (409)</span>';

  return '<div class="mb-4 rounded-2xl border border-line bg-panel p-4 shadow-panel">' +
    '<div class="mb-2 flex flex-wrap items-center justify-between gap-2">' +
    '<div class="flex flex-wrap items-center gap-2"><h3 class="text-sm font-semibold ' +
    'text-ink">Environment preflight</h3>' + banner + '</div>' +
    '<div class="flex items-center gap-2">' + scopeSel +
    '<button id="btn-pf-refresh" type="button" class="rounded-xl border border-line ' +
    'bg-panel px-3 py-1.5 text-sm hover:bg-soft">' +
    (q.preflightBusy ? 'Checking…' : 'Re-run') + '</button></div></div>' +
    '<p class="mb-2 text-[11px] text-muted">' +
    esc(String(p.passed)) + '/' + esc(String(p.total)) + ' passed · scope ' +
    '<span class="mono">' + esc(p.scope || 'all') + '</span> · ' +
    (p.blocking && p.blocking.length
      ? 'blocking: <span class="mono text-rose-600">' + esc(p.blocking.join(', ')) + '</span>'
      : 'nothing blocking') +
    (p.advisory && p.advisory.length
      ? ' · advisory: <span class="mono">' + esc(p.advisory.join(', ')) + '</span>' : '') +
    '</p>' +
    '<div class="max-h-64 overflow-auto rounded-xl border border-line">' +
    '<table class="w-full"><thead class="bg-soft/60 text-left text-xs text-muted">' +
    '<tr><th class="px-3 py-1.5">result</th><th class="px-3 py-1.5">check</th>' +
    '<th class="px-3 py-1.5">measured</th></tr></thead><tbody>' + rows +
    '</tbody></table></div>' +
    '<p class="mt-2 text-[11px] text-muted">Read-only. This panel never enqueues. ' +
    'The gate is <span class="mono">f_env_preflight.py</span>; a task is refused ' +
    'with HTTP 409 when a required check fails.</p></div>';
}

async function refreshPreflight() {
  const q = state.taskQueue;
  q.preflightBusy = true;
  q.preflightMsg = '';
  try {
    const res = await fetch('/api/task_center/preflight?scope=' +
      encodeURIComponent(q.scope || 'llm'));
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
    q.preflight = data;
  } catch (e) {
    q.preflight = null;
    q.preflightMsg = 'Preflight API unavailable: ' + (e.message || e);
  } finally {
    q.preflightBusy = false;
  }
}

function bindPreflightPanel() {
  const rerender = () => {
    const body = $('#workspace-body');
    if (body && state.nav === 'task-center' && state.tab === 'queue') {
      body.innerHTML = taskQueueHtml();
      bindTaskQueuePanel();
    }
  };
  $('#btn-pf-refresh')?.addEventListener('click', async () => {
    await refreshPreflight();
    rerender();
  });
  $('#pf-scope')?.addEventListener('change', async (e) => {
    state.taskQueue.scope = e.target.value || 'llm';
    await refreshPreflight();
    rerender();
  });
}

function taskQueueHtml() {
  const q = state.taskQueue;
  const ov = q.overview || {};
  const counts = ['pending', 'running', 'retry', 'handoff', 'success', 'failed']
    .map((k) => '<span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' +
      queueStatusClass(k) + '">' + k + ' ' + esc(String(ov[k] ?? 0)) + '</span>')
    .join(' ');

  const f = q.filter.trim().toLowerCase();
  const rows = f
    ? q.rows.filter((r) => ['task_id', 'skill_id', 'task_type', 'assigned_model',
        'status', 'error_msg', 'result']
        .map((k) => String(r[k] == null ? '' : r[k]).toLowerCase())
        .some((v) => v.includes(f)))
    : q.rows;

  const body = rows.length
    ? rows.map((r) => {
        const dur = (r.created_at && r.finished_at)
          ? 'done' : (r.status === 'running' ? 'running' : '-');
        return '<tr class="border-t border-line align-top">' +
          '<td class="px-3 py-2"><span class="rounded-full px-2 py-0.5 text-[11px] font-medium ' +
            queueStatusClass(r.status) + '">' + esc(r.status || '-') + '</span></td>' +
          '<td class="mono px-3 py-2 text-xs">' + esc(r.task_id || '-') + '</td>' +
          '<td class="px-3 py-2 text-xs">' + esc(r.skill_id || '-') +
            '<div class="text-muted">' + esc(r.task_type || '-') + '</div></td>' +
          '<td class="mono px-3 py-2 text-xs font-semibold text-ink">' +
            esc(r.assigned_model || '-') + '</td>' +
          '<td class="px-3 py-2 text-xs">' + esc(String(r.retry_count ?? 0)) + '/' +
            esc(String(r.max_retry ?? 0)) +
            '<div class="text-muted">handoff ' + esc(String(r.handoff_count ?? 0)) + '</div></td>' +
          '<td class="px-3 py-2 text-xs text-muted">' + esc(fmtLocal(r.created_at)) +
            '<div>' + esc(fmtLocal(r.finished_at) || dur) + '</div></td>' +
          '<td class="px-3 py-2 text-xs">' +
            (r.error_msg
              ? '<span class="text-rose-600">' + esc(String(r.error_msg).slice(0, 60)) + '</span>'
              : (r.result ? '<span class="text-emerald-600">result ok</span>'
                          : '<span class="text-muted">-</span>')) + '</td>' +
          '</tr>';
      }).join('')
    : '<tr><td colspan="7" class="px-3 py-6 text-center text-sm text-muted">' +
      (f ? 'No row matches “' + esc(q.filter) + '”.'
         : '無相關記錄。 The queue is EMPTY — nothing has been enqueued yet, so ' +
           'no model has run a queued task.') + '</td></tr>';

  return (
    '<div class="mx-auto max-w-6xl">' +
    preflightHtml() +
    '<div class="mb-4 flex flex-wrap items-end justify-between gap-2">' + +
    '<div><h2 class="text-lg font-semibold text-ink">Task Queue</h2>' +
    '<p class="mt-0.5 text-sm text-muted">SSOT = <span class="mono">skill_task_queue</span> · ' +
    'worker = <span class="mono">worker_once()</span> · ' +
    'total <span class="mono">' + esc(String(ov.total ?? 0)) + '</span></p></div>' +
    '<div class="flex items-center gap-2">' +
    '<input id="q-filter" value="' + esc(q.filter) + '" placeholder="filter id / skill / model…" ' +
    'class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm text-ink outline-none focus:border-accent" />' +
    '<button id="btn-queue-refresh" type="button" ' +
    'class="rounded-xl border border-line bg-panel px-3 py-1.5 text-sm hover:bg-soft">' +
    (q.busy ? 'Loading…' : 'Refresh') + '</button></div></div>' +
    (q.msg ? '<div class="mb-3 rounded-xl border px-3 py-2 text-sm ' +
      (q.msgOk ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
               : 'border-rose-200 bg-rose-50 text-rose-700') + '">' + esc(q.msg) + '</div>' : '') +
    '<div class="mb-3 flex flex-wrap items-center gap-2">' + counts + '</div>' +
    '<div class="overflow-auto rounded-2xl border border-line bg-panel shadow-panel">' +
    '<table class="w-full"><thead class="bg-soft/60 text-left text-xs text-muted">' +
    '<tr><th class="px-3 py-2">status</th><th class="px-3 py-2">task_id</th>' +
    '<th class="px-3 py-2">skill / type</th><th class="px-3 py-2">assigned_model</th>' +
    '<th class="px-3 py-2">retry / handoff</th><th class="px-3 py-2">created / finished</th>' +
    '<th class="px-3 py-2">result / error</th></tr></thead>' +
    '<tbody>' + body + '</tbody></table></div>' +
    '<p class="mt-3 text-[11px] text-muted">Read-only. This panel never claims or ' +
    'mutates a task. A row only moves when something calls ' +
    '<span class="mono">worker_once()</span> — nothing schedules it today.</p>' +
    '<p class="mt-1 text-[11px] text-amber-700">' +
    '<span class="font-semibold">success means the output matched the SCHEMA, not that it was TRUE.</span> ' +
    'Measured: asked for a port in a sentence with no port, the 7B answered ' +
    '<span class="mono">{"port": 0}</span> and the row was recorded as success. ' +
    'A malformed answer is refused (retry/handoff/failed), so the gate is real — ' +
    'but it is a FORMAT gate. Do not read success as correctness.</p>' +
    '</div>'
  );
}

async function refreshTaskQueue() {
  const q = state.taskQueue;
  q.busy = true;
  q.msg = '';
  try {
    const res = await fetch('/api/task_center/queue?limit=200');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
    q.rows = data.tasks || [];
    q.overview = data.overview || null;
    q.loaded = true;
    q.msgOk = true;
  } catch (e) {
    q.msgOk = false;
    q.msg = 'Queue API unavailable: ' + (e.message || e);
  } finally {
    q.busy = false;
  }
}

function bindTaskQueuePanel() {
  bindPreflightPanel();
  $('#btn-queue-refresh')?.addEventListener('click', async () => {
    await refreshTaskQueue();
    const body = $('#workspace-body');
    if (body && state.nav === 'task-center' && state.tab === 'queue') {
      body.innerHTML = taskQueueHtml();
      bindTaskQueuePanel();
    }
  });
  $('#q-filter')?.addEventListener('input', (e) => {
    state.taskQueue.filter = e.target.value || '';
    const body = $('#workspace-body');
    if (body && state.nav === 'task-center' && state.tab === 'queue') {
      body.innerHTML = taskQueueHtml();
      bindTaskQueuePanel();
      const inp = $('#q-filter');
      if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
    }
  });
}

function readTemplatesFormIntoState() {
  const t = state.templates;
  if (!t) return;
  t.editId = $('#tpl-edit-id')?.value ? Number($('#tpl-edit-id').value) : null;
  t.form = {
    prompt_setting_key: $('#tpl-key')?.value || '',
    name: $('#tpl-name')?.value || '',
    description: $('#tpl-desc')?.value || '',
    catalog_id: Number($('#tpl-catalog')?.value || 0),
    instruction: $('#tpl-instruction')?.value || '',
  };
  t.samplePrompt = $('#tpl-sample')?.value || '';
  t.showAll = !!$('#tpl-show-all')?.checked;
}

function resetTemplatesForm() {
  state.templates.editId = null;
  state.templates.form = {
    prompt_setting_key: '',
    name: '',
    description: '',
    catalog_id: 0,
    instruction: '',
  };
  state.templates.msg = '';
  state.templates.previewOut = '';
}

function fillTemplatesForm(row) {
  if (!row) {
    resetTemplatesForm();
    return;
  }
  state.templates.editId = row.id != null ? Number(row.id) : null;
  state.templates.form = {
    prompt_setting_key: row.prompt_setting_key || '',
    name: row.name || '',
    description: row.description || '',
    catalog_id: row.catalog_id != null ? Number(row.catalog_id) : 0,
    instruction: row.instruction || '',
  };
}

async function refreshTemplates() {
  const t = state.templates;
  try {
    const q = t.showAll ? '?all=1' : '';
    const res = await fetch('/api/templates' + q);
    const data = await res.json();
    if (!res.ok) {
      t.msg = data.detail || data.error || 'HTTP ' + res.status;
      t.msgOk = false;
      t.rows = [];
      return;
    }
    t.rows = Array.isArray(data) ? data : data.templates || [];
  } catch (e) {
    t.rows = [];
    t.msg = 'Templates API unavailable: ' + (e.message || e);
    t.msgOk = false;
  }
}

function remountTemplatesPanel() {
  const body = $('#workspace-body');
  if (body && state.nav === 'llm-templates') {
    body.innerHTML = templatesHtml();
    bindTemplatesPanel();
  }
}

function bindTemplatesPanel() {
  $('#btn-tpl-refresh')?.addEventListener('click', async () => {
    readTemplatesFormIntoState();
    await refreshTemplates();
    remountTemplatesPanel();
    toast('Templates refreshed');
  });
  $('#tpl-show-all')?.addEventListener('change', async () => {
    readTemplatesFormIntoState();
    await refreshTemplates();
    remountTemplatesPanel();
  });
  $('#btn-tpl-reset')?.addEventListener('click', () => {
    resetTemplatesForm();
    remountTemplatesPanel();
  });
  $('#btn-tpl-save')?.addEventListener('click', async () => {
    readTemplatesFormIntoState();
    const t = state.templates;
    const body = { ...t.form, catalog_id: Number(t.form.catalog_id || 0) };
    try {
      let res;
      if (t.editId != null) {
        res = await fetch('/api/templates/' + t.editId, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } else {
        res = await fetch('/api/templates', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        t.msg = data.detail || data.error || 'HTTP ' + res.status;
        t.msgOk = false;
        remountTemplatesPanel();
        return;
      }
      t.msg = t.editId != null ? 'Updated.' : 'Created id=' + data.id;
      t.msgOk = true;
      if (data && data.id != null) fillTemplatesForm(data);
      await refreshTemplates();
      remountTemplatesPanel();
      toast(t.msg);
    } catch (e) {
      t.msg = String(e.message || e);
      t.msgOk = false;
      remountTemplatesPanel();
    }
  });
  $('#btn-tpl-preview')?.addEventListener('click', async () => {
    readTemplatesFormIntoState();
    const t = state.templates;
    const payload = { sample_prompt: t.samplePrompt || '' };
    if (t.editId != null) payload.setting_id = Number(t.editId);
    else payload.instruction = t.form.instruction || '';
    try {
      const res = await fetch('/api/templates/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        t.msg = data.detail || data.error || 'HTTP ' + res.status;
        t.msgOk = false;
      } else {
        t.previewOut = data.final_prompt || '';
        t.msg = '';
      }
      remountTemplatesPanel();
    } catch (e) {
      t.msg = String(e.message || e);
      t.msgOk = false;
      remountTemplatesPanel();
    }
  });
  $('#tpl-tbody')?.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-tpl-act]');
    if (!btn) return;
    const id = btn.getAttribute('data-tpl-id');
    const act = btn.getAttribute('data-tpl-act');
    const t = state.templates;
    if (act === 'edit' || act === 'preview') {
      try {
        const res = await fetch('/api/templates/' + id);
        const data = await res.json();
        if (!res.ok) {
          t.msg = data.detail || data.error || 'load failed';
          t.msgOk = false;
          remountTemplatesPanel();
          return;
        }
        fillTemplatesForm(data);
        if (act === 'preview') {
          readTemplatesFormIntoState();
          const payload = {
            sample_prompt: t.samplePrompt || '',
            setting_id: Number(id),
          };
          const pr = await fetch('/api/templates/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const pdata = await pr.json().catch(() => ({}));
          if (pr.ok) t.previewOut = pdata.final_prompt || '';
          else {
            t.msg = pdata.detail || pdata.error || 'preview failed';
            t.msgOk = false;
          }
        }
        remountTemplatesPanel();
      } catch (e) {
        t.msg = String(e.message || e);
        t.msgOk = false;
        remountTemplatesPanel();
      }
      return;
    }
    if (act === 'del') {
      if (!confirm('Soft-delete template #' + id + '?')) return;
      try {
        const res = await fetch('/api/templates/' + id, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          t.msg = data.detail || data.error || 'HTTP ' + res.status;
          t.msgOk = false;
        } else {
          t.msg = 'Deleted (' + (data.mode || 'soft') + ').';
          t.msgOk = true;
          resetTemplatesForm();
          await refreshTemplates();
        }
        remountTemplatesPanel();
        toast(t.msg);
      } catch (e) {
        t.msg = String(e.message || e);
        t.msgOk = false;
        remountTemplatesPanel();
      }
    }
  });
}

function shell() {
  return (
    '<div class="min-h-screen flex flex-col"><header class="sticky top-0 z-20 border-b border-line bg-panel/95 backdrop-blur-sm"><div class="mx-auto flex max-w-[1600px] flex-col gap-2 px-3 py-2.5 sm:px-4">' +
    '<div class="flex items-center gap-3"><button id="btn-left" type="button" class="rounded-lg border border-line px-2.5 py-1.5 text-sm text-muted hover:bg-soft lg:hidden">Menu</button>' +
    '<div class="flex min-w-0 flex-1 items-center gap-3"><h1 class="truncate text-base font-semibold tracking-tight sm:text-lg">LLM Task Monitor</h1></div>' +
    '<div id="env-badge" class="flex shrink-0 items-center gap-1"></div>' +
    '<button id="btn-refresh" type="button" class="shrink-0 rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium text-ink shadow-panel hover:bg-soft">Refresh</button></div>' +
    '<div class="flex flex-wrap items-center gap-2"><div id="status-pills" class="flex flex-wrap items-center gap-2"></div><div id="status-line" class="text-xs text-muted"></div></div></div></header>' +
    '<div class="mx-auto flex w-full max-w-[1600px] flex-1 overflow-hidden">' +
    '<aside id="left-nav" class="' +
    (state.leftOpen ? '' : 'hidden') +
    ' w-56 shrink-0 border-r border-line bg-panel lg:block"><nav class="flex h-full flex-col gap-1 p-3"><div class="mb-2 px-2 text-[11px] font-semibold uppercase tracking-wide text-muted">Navigate</div>' +
    NAV.map(
      (n) =>
        '<button type="button" data-nav="' +
        n.id +
        '"' +
        (n.iconOnly ? ' title="' + esc(n.label) + '" aria-label="' + esc(n.label) + '"' : '') +
        ' class="nav-item rounded-xl px-3 py-2 text-left text-sm transition hover:bg-soft ' +
        (n.iconOnly ? 'flex w-full items-center justify-center ' : '') +
        (state.nav === n.id ? 'bg-accent-soft font-medium text-accent' : 'text-ink') +
        '">' +
        (n.iconOnly ? (NAV_ICONS[n.id] || n.label) : esc(n.label)) +
        '</button>'
    ).join('') +
    '<div class="mt-auto rounded-xl border border-line bg-soft/60 p-3 text-xs text-muted">Light mode | server catalog + local drafts</div></nav></aside>' +
    '<main class="flex min-w-0 flex-1 flex-col bg-canvas"><div class="border-b border-line bg-panel px-3 py-2 sm:px-4"><div class="flex flex-wrap gap-1">' +
    (state.nav === 'skill-ssot'
        ? SKILL_SSOT_TABS.map(
            (t) =>
              '<button type="button" data-skill-tab="' +
              t.id +
              '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
              ((state.skill?.tab || 'editor') === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
              '">' +
              t.label +
              '</button>'
          ).join('')
        : state.nav === 'skill-learning'
          ? SKILL_LEARNING_TABS.map(
              (t) =>
                '<button type="button" data-sl-tab="' +
                t.id +
                '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
                ((state.skillLearn?.tab || 'overview') === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
                '">' +
                t.label +
                '</button>'
            ).join('')
          : state.nav === 'watchdog'
            ? WATCHDOG_TABS.map(
                (t) =>
                  '<button type="button" class="tab-btn rounded-lg px-3 py-1.5 text-sm bg-accent text-white">' +
                  t.label +
                  '</button>'
              ).join('')
            : state.nav === 'llm-templates'
            ? LLM_TEMPLATE_TABS.map(
                (t) =>
                  '<button type="button" class="tab-btn rounded-lg px-3 py-1.5 text-sm bg-accent text-white">' +
                  t.label +
                  '</button>'
              ).join('')
            : state.nav === 'tool-registry'
            ? TOOL_REGISTRY_TABS.map(
                (t) =>
                  '<button type="button" data-tr-tab="' +
                  t.id +
                  '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
                  ((state.toolRegistry?.tab || 'list') === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
                  '">' +
                  t.label +
                  '</button>'
              ).join('')
            : state.nav === 'conversation-center'
            ? // THE STRIP BECAME BUTTONS (2026-09-27).
              //
              // THE HUMAN, verbatim: "Conversation Center · one index · chat →
              // chat_main → chat_center_message → identity_registry / is the
              // position for tha button".
              //
              // MEASURED BEFORE: this was ONE `<span>` with 0 `<button>` and 0
              // `href`. It NAMED `index` in its own text -- "Conversation Center
              // · one index" -- while offering NO way to reach it. **A label that
              // names a destination and is not a control.**
              //
              // THE TEXT IS KEPT, and that is deliberate: BOTH
              // `_proof_conversation_index_is_start.py:220` and
              // `_proof_conversation_nav_to_index.py:167` assert the literal
              // string `Conversation Center · one index ·` is present here.
              // Keeping the prefix keeps both assertions TRUE and UNMODIFIED.
              //
              // THE MECHANISM IS THE SHELL'S OWN, not a new one: `pushState` +
              // `mount(false)`, exactly as `data-ue-tab` (`:4424`) and
              // `data-tr-tab` (`:5376`) do. `mount(false)` replaces
              // `#conversation-center-root`, so the Vue app remounts and
              // `openFromAddress()` -- the ONE parser -- runs against the new
              // path. No second parser is introduced.
              //
              // NO `title=` ATTRIBUTE, AND THAT IS DELIBERATE. The `ui-standard`
              // skill this repo registers gives rule 2 (`why_clickable`) the unit
              // "count of hover-only title= attributes" with target **0**, and
              // `ui_element_registry.check_divergence` COUNTS `title="` in this
              // file. A hover-only "why" is exactly the anti-pattern, so the
              // reason lives in the REGISTER (`why_text`) where it is readable
              // without a mouse, and the labels say what they do.
              '<span class="px-2 py-1.5 text-sm text-muted">Conversation Center · one index · ' +
              'chat → chat_main → chat_center_message → identity_registry</span>' +
              '<button type="button" data-cc-step="index" ' +
              'class="tab-btn rounded-lg px-3 py-1.5 text-sm transition text-muted hover:bg-soft">' +
              '+ New chat</button>' +
              '<button type="button" data-cc-step="list" ' +
              'class="tab-btn rounded-lg px-3 py-1.5 text-sm transition text-muted hover:bg-soft">' +
              'Chats</button>'
            : state.nav === 'user-environment'
            ? USER_ENVIRONMENT_TABS.map(
                (t) =>
                  '<button type="button" data-ue-tab="' +
                  t.id +
                  '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
                  ((state.userEnvironment?.tab || 'detect') === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
                  '">' +
                  t.label +
                  '</button>'
              ).join('')
            : state.nav === 'chat-center'
            ? CHAT_CENTER_TABS.map(
                (t) =>
                  '<button type="button" data-cc-tab="' +
                  t.id +
                  '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
                  ((state.chatCenter?.tab || 'flow') === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
                  '">' +
                  t.label +
                  '</button>'
              ).join('')
            : state.nav === 'capability-center'
            ? '<span class="px-2 py-1.5 text-sm text-muted">Capability Center · one flow, many backends</span>'
            : state.nav === 'telemetry'
            ? TELEMETRY_TABS.map(
                (t) =>
                  '<button type="button" data-tele-tab="overview" ' +
                  'class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
                  (state.telemetry?.tab === t.id || !state.telemetry?.tab
                    ? 'bg-accent text-white'
                    : 'text-muted hover:bg-soft') +
                  '">' +
                  t.label +
                  '</button>'
              ).join('') +
              (state.telemetry && state.telemetry.tab && state.telemetry.tab !== 'overview'
                ? '<button type="button" data-tele-back="1" class="tab-btn rounded-lg px-3 py-1.5 text-sm text-muted hover:bg-soft">← ' +
                  esc((state.telemetry.metrics[state.telemetry.tab] || {}).title || state.telemetry.tab) +
                  '</button>'
                : '')
            : state.nav === 'evidence'
            ? '<button type="button" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
              (state.evidence?.detailId ? 'text-muted hover:bg-soft' : 'bg-accent text-white') +
              '">Evidence List</button>' +
              (state.evidence?.detailId
                ? '<button type="button" class="tab-btn rounded-lg px-3 py-1.5 text-sm bg-accent text-white mono">' +
                  esc(state.evidence.detailId) +
                  '</button>'
                : '')
            : state.nav === 'task-center'
              ? TABS.map(
                  (t) =>
                    '<button type="button" data-tab="' +
                    t.id +
                    '" class="tab-btn rounded-lg px-3 py-1.5 text-sm transition ' +
                    (state.tab === t.id ? 'bg-accent text-white' : 'text-muted hover:bg-soft') +
                    '">' +
                    t.label +
                    '</button>'
                ).join('')
              : '<span class="px-2 py-1.5 text-sm text-muted">Panel</span>') +
    '</div></div><div id="workspace-body" class="fade-swap flex-1 overflow-auto p-3 sm:p-4">' +
    workspaceHtml() +
    '</div></main></div>' +
    '<div id="toast" class="pointer-events-none fixed bottom-4 right-4 rounded-xl bg-ink px-3 py-2 text-sm text-white opacity-0 shadow-panel transition"></div></div>'
  );
}

async function refreshWatchdog(selectedKeep = true) {
  try {
    const res = await fetch('/api/watchdog/events?limit=80');
    const data = await res.json();
    const wd = data.watchdog || {};
    const prevSelected = selectedKeep ? state.watchdog.selectedId : null;
    state.watchdog.running = !!(wd.running || wd.ok);
    state.watchdog.pid = wd.pid || null;
    state.watchdog.lastEvent = wd.last_event || (data.events && data.events[0]) || null;
    state.watchdog.events = data.events || [];
    state.watchdog.logTail = data.log_tail || [];
    const last = state.watchdog.lastEvent || {};
    const lastDetail = last.detail || {};
    const fromEvents = (state.watchdog.events || [])
      .map((e) => (e && e.detail && e.detail.helper_pid) || null)
      .find((p) => p);
    state.watchdog.helperPid = lastDetail.helper_pid || fromEvents || state.watchdog.helperPid || null;
    if (prevSelected && state.watchdog.events.some((e) => e.id === prevSelected)) {
      state.watchdog.selectedId = prevSelected;
    } else if (!state.watchdog.selectedId && state.watchdog.events[0]) {
      state.watchdog.selectedId = state.watchdog.events[0].id;
    }
    state.watchdog.msg = '';
  } catch (e) {
    state.watchdog.running = false;
    state.watchdog.msg = 'Watchdog API unavailable: ' + (e.message || e);
  }
}

function bindWatchdogPanel() {
  $('#btn-wd-refresh')?.addEventListener('click', async () => {
    await refreshWatchdog(true);
    const body = $('#workspace-body');
    if (body && state.nav === 'watchdog') {
      body.innerHTML = watchdogHtml();
      bindWatchdogPanel();
    }
    renderStatusPills();
    toast('Watchdog refreshed');
  });
  $('#wd-event-list')?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-wd-id]');
    if (!btn) return;
    state.watchdog.selectedId = btn.getAttribute('data-wd-id');
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = watchdogHtml();
      bindWatchdogPanel();
    }
  });
}

async function refreshAll() {
  try {
    const res = await fetch('/api/system-status');
    const data = await res.json();
    const ol = data.ollama || {};
    const idn = data.identity || {};
    const helper = data.helper || {};
    const wd = data.watchdog || helper.watchdog || {};
    const oc = data.openclaw || {};
    const wh = data.worker_hb || {};
    state.status.helperOk = !!(data.ok || helper.ok);
    state.status.ollamaOk = !!ol.ok;
    state.status.modelPresent = !!ol.model_present;
    // TWO models, TWO facts. MEASURED DEFECT: the header showed ONE pill built
    // from the VISION model while every text job runs the TEXT model, so the
    // badge named a model no text job uses. They are now tracked separately.
    state.status.model = ol.model || ol.text_model || '';
    state.status.visionModel = ol.vision_model || '';
    state.status.visionPresent = !!ol.vision_model_present;
    state.status.computerId = idn.computer_id || '';
    state.status.computerName = idn.computer_name || '';
    state.status.baseUrl = ol.base_url || '';
    state.status.text = (idn.computer_name || '-') + ' | ' + (idn.computer_id || '-') + ' | ' + (ol.base_url || '');
    state.status.openclawOk = !!oc.ok;
    state.status.openclawConfigured = !!oc.configured;
    state.status.openclawError = oc.error || '';
    state.status.openclawTools = oc.tool_count || 0;
    state.status.workerHbOk = !!wh.ok;
    state.status.workerHbName = wh.worker_name || '';
    state.status.workerHbAgeSec = wh.age_sec != null ? wh.age_sec : null;
    state.status.workerHbError = wh.error || '';
    state.watchdog.running = !!(wd.running || wd.ok);
    state.watchdog.pid = wd.pid || state.watchdog.pid;
    state.watchdog.lastEvent = wd.last_event || state.watchdog.lastEvent;
    state.watchdog.openclaw = oc;
    state.watchdog.workerHb = wh;
    if (helper.pid) state.watchdog.helperPid = helper.pid;
  } catch {
    state.status.helperOk = false;
    state.status.ollamaOk = false;
    state.status.modelPresent = false;
    state.status.visionPresent = false;
    state.status.openclawOk = false;
    state.status.openclawConfigured = false;
    state.status.workerHbOk = false;
    state.status.text = 'mouse_spot_helper offline';
    state.watchdog.running = false;
  }

  try {
    const res = await fetch('/api/llm-tasks?limit=200');
    const data = await res.json();
    state.report.tasks = data.tasks || [];
    state.report.totals = data.totals || state.report.totals;
    state.report.by_model = data.by_model || [];
    state.report.models = data.models || [];
  } catch {
    /* keep previous */
  }

  try {
    const res = await fetch('/api/task-sources?limit=200');
    const data = await res.json();
    state.report.sources = data.sources || [];
  } catch {
    /* keep previous */
  }

  try {
    const res = await fetch('/api/task-center/tasks?limit=200');
    const data = await res.json();
    state.report.taskCenter = data.tasks || [];
  } catch {
    /* keep previous */
  }

  try {
    const res = await fetch('/api/task_center/graph');
    const data = await res.json();
    if (data.ok) {
      // THE COUNTS ARE STORED, NOT ONLY THE TREE. MEASURED: the header read
      // `g.node_count` while this handler copied only `catalogs`, so the page
      // said "0 nodes, 0 roots" above a tree that was rendering correctly — a
      // header that contradicts its own body. The counts ARE part of the reply,
      // so not storing them threw them away.
      state.graph = {
        catalogs: data.catalogs || [],
        node_count: data.node_count || 0,
        root_count: data.root_count || 0,
        template_count: data.template_count || 0,
        linked_templates: data.linked_templates || 0,
        unlinked_templates: data.unlinked_templates || 0,
        shape: data.shape || '',
        note: data.note || '',
        msg: '',
        msgOk: true,
      };
    }
  } catch {
    /* keep previous */
  }

  if (state.nav === 'watchdog') {
    await refreshWatchdog(true);
  }
  if (
    state.nav === 'skill-learning' &&
    !state.skillLearn.busy &&
    document.activeElement?.id !== 'sl-import-text' &&
    document.activeElement?.id !== 'sl-lesson-text'
  ) {
    await refreshSkillLearning(true);
  }

  renderStatusPills();
  renderEnvBadge();
  renderRightList();
  if (state.tab === 'report' && state.nav === 'task-center') {
    const body = $('#workspace-body');
    if (body) body.innerHTML = reportHtml();
    bindReportOnly();
  }
  if (state.tab === 'graph' && state.nav === 'task-center') {
    const body = $('#workspace-body');
    if (body) body.innerHTML = graphHtml();
    bindGraphPanel();
  }
  if (state.tab === 'queue' && state.nav === 'task-center') {
    // Fetch first, then render, so the panel never shows a stale empty table.
    //
    // WHY preflight is NOT re-run here on every poll: refreshAll() runs every
    // 5s, and the preflight spawns subprocesses (a torch CUDA op, nvidia-smi,
    // ffmpeg). Re-running it every 5s was a resource storm AND — before the
    // CREATE_NO_WINDOW fix — popped a console window every 5 seconds, forever.
    // The result is cached; the user re-runs it explicitly with "Re-run".
    const needPreflight = !state.taskQueue.preflight && !state.taskQueue.preflightBusy;
    const jobs = [refreshTaskQueue()];
    if (needPreflight) jobs.push(refreshPreflight());
    Promise.all(jobs).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'task-center' && state.tab === 'queue') {
        body.innerHTML = taskQueueHtml();
        bindTaskQueuePanel();
      }
    });
  }
  if (state.nav === 'watchdog') {
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = watchdogHtml();
      bindWatchdogPanel();
    }
  }
  if (state.nav === 'tool-registry') {
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = toolRegistryHtml();
      bindToolRegistryPanel();
    }
  }
  if (state.nav === 'user-environment') {
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = userEnvironmentHtml();
      bindUserEnvironmentPanel();
    }
  }
  if (state.nav === 'chat-center') {
    // Vue owns this subtree — do NOT touch it here. The 5s poll calls
    // refreshAll(); re-rendering would destroy the Vue app and lose UI state.
    // Mounting is handled once in mount().
  }
  if (
    state.nav === 'skill-learning' &&
    !state.skillLearn.busy &&
    document.activeElement?.id !== 'sl-import-text' &&
    document.activeElement?.id !== 'sl-lesson-text'
  ) {
    const body = $('#workspace-body');
    if (body) {
      body.innerHTML = skillLearningHtml();
      bindSkillLearningPanel();
    }
  }
}

// THE PINNED ROW DETAIL REPORT. THE HUMAN (2026-09-27): "can onclick to have
// why = detail report" and "identity , no identity row.... i don't understand is
// without session ID and which value".
//
// WHY THIS EXISTS. MEASURED before this: the pinned table carried 3 hover-only
// `title=` attributes and 0 clickable `data-*`, so the "why" was invisible on
// touch and could not be copied. The human had to ASK what `coords` and
// `identity` meant, which is the defect: a UI that needs a conversation to
// explain itself is not user friendly.
//
// THE PATTERN IS THIS FILE'S OWN (`openTaskDetailPopup` below): backdrop click +
// ✕ + Escape. It is REUSED so the two popups cannot drift apart.
function openPinnedRowDetail(s) {
  const old = document.getElementById('pinned-row-popup');
  if (old) old.remove();
  const line = (label, value, note) =>
    '<div class="flex gap-2 py-1 text-sm"><span class="w-32 shrink-0 text-muted">' + label +
    '</span><span class="mono break-all">' + esc(value == null || value === '' ? '-' : value) + '</span>' +
    (note ? '<span class="text-xs text-muted">' + esc(note) + '</span>' : '') + '</div>';
  const el = document.createElement('div');
  el.id = 'pinned-row-popup';
  el.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4';
  el.innerHTML =
    '<div class="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-line bg-panel p-5 shadow-panel">' +
    '<div class="mb-3 flex items-center justify-between">' +
    '<h3 class="text-base font-semibold text-ink">Pinned row ' + esc(s.pinned_row_no) + ' — detail report</h3>' +
    '<button id="pinned-row-close" class="rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">✕</button></div>' +

    '<div class="mb-3 rounded-xl border border-line bg-soft/50 p-3 text-xs text-muted">' +
    '<div class="mb-1 font-semibold text-ink">What each column means</div>' +
    '<div><span class="mono text-ink">Measurements</span> — the number of ' +
    '<span class="mono">coordinate_session</span> rows linked to this session. ' +
    'It counts MEASUREMENTS, not sessions: 8 means this session was measured 8 times.</div>' +
    '<div class="mt-1"><span class="mono text-ink">Identity known?</span> — whether ' +
    '<span class="mono">identity_registry</span> holds a row for this session_id. ' +
    'It is about a DIFFERENT table from the Session column, which always holds the id.</div>' +
    '<div class="mt-1"><span class="mono text-ink">Live?</span> — whether this session\u2019s own ' +
    '<span class="mono">chatSessions</span> file was touched within the live window. ' +
    'It is a MEASUREMENT of the file, never a guess from Last linked.</div>' +
    '<div class="mt-1"><span class="mono text-ink">Last linked</span> — when this session was last ' +
    'LINKED to a coordinate. It is a LINK time, not a run time, so it can be many hours older.</div>' +
    '</div>' +

    line('Row', s.pinned_row_no, 'position in the pinned area, 1-based') +
    line('Session', s.session_id || '(no session linked)') +
    line('Top-left', s.x1 + ', ' + s.y1, 'screen pixels') +
    line('Bottom-right', s.x2 + ', ' + s.y2, 'screen pixels') +
    line('Centre', s.cx + ', ' + s.cy, 'screen pixels') +
    line('Measurements', s.coord_count, 'coordinate_session rows linked') +
    line('Identity known?', s.identity_known ? 'yes' : 'no',
         s.identity_known
           ? 'identity_registry has a row for this session_id'
           : 'identity_registry has NO row for session_id=' + String(s.session_id || '').slice(0, 8) + '…') +
    line('Live?', s.status || (s.is_live ? 'LIVE' : 'idle'), s.status_why || '') +
    line('Age', s.age_sec == null ? '-' : s.age_sec + 's',
         'seconds since the session\u2019s own file was touched') +
    line('Where', s.environment || 'underivable', s.environment_why || '') +
    line('Last linked', fmtLocal(s.last_seen), 'a LINK time, not a run time') +
    line('Cite', s.cite_ref || '-', 'how this row\u2019s rect was derived') +
    '</div>';
  document.body.appendChild(el);
  const close = () => el.remove();
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  el.querySelector('#pinned-row-close').addEventListener('click', close);
  const onKey = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}

function openTaskDetailPopup(task) {
  const old = document.getElementById('task-detail-popup');
  if (old) old.remove();
  const line = (label, value) =>
    '<div class="flex gap-2 py-1 text-sm"><span class="w-28 shrink-0 text-muted">' + label +
    '</span><span class="mono break-all">' + esc(value || '-') + '</span></div>';
  const el = document.createElement('div');
  el.id = 'task-detail-popup';
  el.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4';
  el.innerHTML =
    '<div class="w-full max-w-md rounded-2xl border border-line bg-panel p-5 shadow-panel">' +
    '<div class="mb-3 flex items-center justify-between"><h3 class="text-base font-semibold text-ink">Task Detail</h3>' +
    '<button id="task-detail-close" class="rounded-lg border border-line px-2 py-0.5 text-xs hover:bg-soft">✕</button></div>' +
    line('Worker', (task.source || task.writer || '-') + ' | ' + (task.model_label || task.model || '-')) +
    line('Session ID', task.session_id) +
    line('Task ID', task.task_id) +
    line('Template ID', task.template_id || task.task_id || '-') +
    line('Catalog', task.catalog || '-') +
    line('Subcatalog', task.subcatalog || '-') +
    line('Type', task.item_type ? task.item_type + ' ' + (TASK_ID_TYPE_LABEL[task.item_type] || '') : '-') +
    line('Version', task.task_version ? 'v' + task.task_version : '-') +
    line('Chat ID', task.chat_id) +
    line('Task Name', task.task) +
    line('Type', task.type) +
    line('Action', task.action) +
    line('Name', task.name) +
    line('Format', task.format) +
    line('Status', task.status) +
    line('Result', task.result) +
    line('Duration', fmtDuration(task.started_at, task.ended_at, task.duration_ms)) +
    line('Tokens', (task.prompt_tokens ?? 0) + ' / ' + (task.completion_tokens ?? 0) + ' / ' + (task.total_tokens ?? 0)) +
    line('Detail', task.reason || task.error) +
    '</div>';
  document.body.appendChild(el);
  const close = () => el.remove();
  el.addEventListener('click', (e) => { if (e.target === el) close(); });
  el.querySelector('#task-detail-close').addEventListener('click', close);
  const onKey = (e) => { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
}

function bindReportOnly() {
  $('#btn-clear-server')?.addEventListener('click', async () => {
    if (!confirm('Clear all server LLM task history?')) return;
    await fetch('/api/llm-tasks/clear', { method: 'POST' });
    await refreshAll();
    toast('Server history cleared');
  });
  document.querySelectorAll('[data-report-id]').forEach((tr) => {
    tr.addEventListener('click', () => {
      const id = tr.getAttribute('data-report-id');
      const task = state.report.tasks.find((x) => x.id === id);
      if (task) openTaskDetailPopup(task);
    });
  });
}

function bindGraphPanel() {
  $('#btn-graph-refresh')?.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/task_center/graph');
      const data = await res.json();
      if (data.ok) {
        // THE SAME SHAPE THE REFRESH LOOP STORES — see the note there. A Refresh
        // that dropped the counts would put the "0 nodes" header BACK.
        state.graph = {
          catalogs: data.catalogs || [],
          node_count: data.node_count || 0,
          root_count: data.root_count || 0,
          template_count: data.template_count || 0,
          linked_templates: data.linked_templates || 0,
          unlinked_templates: data.unlinked_templates || 0,
          shape: data.shape || '',
          note: data.note || '',
          msg: 'Refreshed ' + (data.node_count || 0) + ' nodes',
          msgOk: true,
        };
      } else {
        state.graph.msg = data.error || 'Graph API error';
        state.graph.msgOk = false;
      }
    } catch (e) {
      state.graph.msg = 'Graph API unavailable: ' + (e.message || e);
      state.graph.msgOk = false;
    }
    const body = $('#workspace-body');
    if (body && state.nav === 'task-center' && state.tab === 'graph') {
      body.innerHTML = graphHtml();
      bindGraphPanel();
    }
    toast('Graph refreshed');
  });
}

// Bind click handlers for every nav button, including ones injected AFTER the
// first bind() pass (the status pills are rendered by renderStatusPills(), which
// runs after bind(), so the OpenClaw pill would otherwise be inert).
function bindNavButtons() {
  document.querySelectorAll('[data-nav]').forEach((btn) => {
    if (btn.__navBound) return;
    btn.__navBound = true;
    btn.addEventListener('click', () => {
      readFormIntoDraft();
      const nextNav = btn.getAttribute('data-nav');
      if (!nextNav || nextNav === state.nav) return;
      const navItem = NAV.find((n) => n.id === nextNav);
      // External navs (e.g. devtask-view) open the static page in a new tab.
      if (navItem && navItem.external) {
        window.open('/llm-tasks/' + navItem.path, '_blank');
        return;
      }
      state.nav = nextNav;
      try {
        history.pushState(
          { nav: nextNav, tab: currentTabId(nextNav) },
          '',
          navPath(nextNav, currentTabId(nextNav))
        );
      } catch (_) {}
      mount(false);
    });
  });
}

function bind() {
  $('#btn-left')?.addEventListener('click', () => {
    state.leftOpen = !state.leftOpen;
    $('#left-nav')?.classList.toggle('hidden', !state.leftOpen);
  });
  $('#btn-refresh')?.addEventListener('click', async () => {
    await refreshAll();
    toast('Refreshed');
  });
  // Env badge refresh (top-right flag): re-detect + re-render.
  $('#btn-env-refresh')?.addEventListener('click', async () => {
    state.userEnvironment.env = null;
    await ensureUserEnvironment();
    toast('Environment refreshed');
  });

  bindNavButtons();
  document.querySelectorAll('[data-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      readFormIntoDraft();
      state.tab = btn.getAttribute('data-tab');
      try {
        history.pushState(
          { nav: state.nav, tab: state.tab },
          '',
          navPath(state.nav, state.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });

  // Chat Center tabs (3-step flow | Discovery List).
  // MUST live in bind(), NOT inside bindUserEnvironmentPanel() — that function
  // runs only when the user-environment page is active, so a binding placed
  // there is never attached on Chat Center and the tab buttons do nothing.
  // (Measured: clicking the tab changed neither the URL nor the rendered view.)
  // `mount(false)` is required rather than mountBody(): #chat-center*-root is
  // NOT inside a [data-body] element, so mountBody() would refresh nothing.
  document.querySelectorAll('[data-cc-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.chatCenter.tab = btn.getAttribute('data-cc-tab') || 'flow';
      try {
        history.pushState(
          { nav: 'chat-center', tab: state.chatCenter.tab },
          '',
          navPath('chat-center', state.chatCenter.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });

  // Telemetry: Overview is always the same chart grid; metric tabs and the
  // "← title" back button both just re-point state.telemetry.tab.
  document.querySelectorAll('[data-tele-tab]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.telemetry.tab = btn.getAttribute('data-tele-tab') || 'overview';
      try {
        history.pushState(
          { nav: 'telemetry', tab: state.telemetry.tab },
          '',
          navPath('telemetry', state.telemetry.tab)
        );
      } catch (_) {}
      mount(false);
    });
  });
  document.querySelectorAll('[data-tele-back]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.telemetry.tab = 'overview';
      try {
        history.pushState(
          { nav: 'telemetry', tab: 'overview' },
          '',
          navPath('telemetry', 'overview')
        );
      } catch (_) {}
      mount(false);
    });
  });

  $('#btn-new')?.addEventListener('click', () => {
    const rec = createBlankRecord({ status: 'draft', name: 'New record' });
    state.tab = 'analyze';
    state.catalogMode = 'local';
    fillForm(rec, true);
    mount(false);
    toast('New draft');
  });

  $('#btn-save')?.addEventListener('click', () => {
    readFormIntoDraft();
    const saved = upsertRecord(state.draft);
    state.draft = saved;
    state.selectedId = saved.id;
    state.catalogMode = 'local';
    renderRightList();
    toast('Saved to LocalStorage');
  });

  if (state.nav === 'llm-templates') {
    bindTemplatesPanel();
    refreshTemplates().then(() => {
      remountTemplatesPanel();
    });
  }
  $('#btn-delete')?.addEventListener('click', () => {
    const id = state.selectedId || state.draft.id;
    if (!id) return;
    if (!confirm('Delete this local record?')) return;
    deleteRecord(id);
    const next = listRecords()[0] || createBlankRecord({ name: 'New record' });
    fillForm(next, true);
    mount(false);
    toast('Deleted');
  });

  $('#btn-copy')?.addEventListener('click', async () => {
    const text = $('#f-result')?.value || '';
    try {
      await navigator.clipboard.writeText(text);
      toast('Result copied');
    } catch {
      toast('Copy failed');
    }
  });

  $('#btn-apply')?.addEventListener('click', () => {
    const r = $('#f-result')?.value || '';
    if (!r) return toast('No result to apply');
    const p = $('#f-prompt');
    if (p) p.value = r;
    state.tab = 'chat';
    readFormIntoDraft();
    mount(false);
    toast('Applied to prompt');
  });

  $('#btn-from-template')?.addEventListener('click', async () => {
    readFormIntoDraft();
    const d = state.draft;
    const ide = $('#f-ide-target')?.value || 'Visual Studio Code';
    try {
      const res = await fetch('/api/prompt/from-template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_id: 'ide_work',
          goal: d.prompt_content || d.context || d.name || 'Complete this coding task',
          context: d.context || '',
          ide_target: ide,
          writer: d.writer,
          task_id: d.task_id,
          session_id: d.session_id || '',
        }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || 'template failed');
      const prompt = data.prompt || data.improved_prompt || '';
      if ($('#f-prompt')) $('#f-prompt').value = prompt;
      state.draft.prompt_content = prompt;
      if ($('#f-result')) {
        $('#f-result').value =
          (data.hint || 'Created from IDE template') +
          '\n\n' +
          (data.trailer || formatIdentityTrailer(data));
      }
      state.draft.result_content = $('#f-result')?.value || '';
      toast('Prompt from IDE template');
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  $('#btn-apply-ids')?.addEventListener('click', async () => {
    const text = ($('#f-result')?.value || $('#f-prompt')?.value || '').trim();
    if (!text) return toast('No reply/prompt to parse');
    try {
      const res = await fetch('/api/prompt/apply-ids', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || 'parse failed');
      const idn = data.identity || data;
      if (idn.session_id && $('#f-session')) $('#f-session').value = idn.session_id;
      if (idn.task_id && $('#f-task-id')) $('#f-task-id').value = idn.task_id;
      if (idn.writer && $('#f-writer')) $('#f-writer').value = idn.writer;
      readFormIntoDraft();
      toast(
        'Applied IDs | session=' +
          (idn.session_id || '(empty)') +
          ' | task=' +
          (idn.task_id || '(empty)') +
          ' | writer=' +
          (idn.writer || '(empty)')
      );
    } catch (err) {
      toast(String(err.message || err));
    }
  });

  $('#btn-improve')?.addEventListener('click', async () => {
    readFormIntoDraft();
    const d = state.draft;
    try {
      const res = await fetch('/api/prompt/improve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: d.prompt_content,
          writer: d.writer,
          task_id: d.task_id,
          session_id: d.session_id,
          context: d.context,
          model: d.model || undefined,
          require_session: false,
        }),
      });
      const data = await res.json();
      let out =
        data.improved_prompt ||
        data.improved ||
        data.result ||
        data.text ||
        data.error ||
        JSON.stringify(data, null, 2);
      if (typeof out !== 'string') out = JSON.stringify(out, null, 2);
      const trailer = data.trailer || formatIdentityTrailer(data.identity || data);
      if (trailer && !String(out).includes('session_id:')) {
        out = String(out).replace(/\s*$/, '') + '\n\n' + trailer;
      }
      if ($('#f-result')) $('#f-result').value = out;
      state.draft.result_content = $('#f-result')?.value || '';
      state.draft.status = data.ok === false ? 'error' : 'done';
      if ($('#f-status')) $('#f-status').value = state.draft.status;
      state.tab = 'result';
      mount(false);
      await refreshAll();
      toast(data.ok === false ? 'Improve error' : 'Improved');
    } catch (err) {
      if ($('#f-result')) $('#f-result').value = String(err);
      toast('Improve failed');
    }
  });

  $('#btn-analyze')?.addEventListener('click', async () => {
    readFormIntoDraft();
    const d = state.draft;
    try {
      const res = await fetch('/api/prompt/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: d.prompt_content,
          writer: d.writer,
          task_id: d.task_id,
          session_id: d.session_id,
          context: d.context,
          model: d.model || undefined,
          require_session: false,
        }),
      });
      const data = await res.json();
      let out = data.notes || data.analysis || data.result || data.error || data;
      if (typeof out !== 'string') out = JSON.stringify(out, null, 2);
      const trailer = data.trailer || formatIdentityTrailer(data.identity || data);
      if (trailer && !String(out).includes('session_id:')) {
        out = String(out).replace(/\s*$/, '') + '\n\n' + trailer;
      }
      if ($('#f-result')) $('#f-result').value = out;
      state.draft.result_content = $('#f-result')?.value || '';
      state.draft.status = data.ok === false ? 'error' : 'done';
      state.tab = 'result';
      mount(false);
      await refreshAll();
      toast(data.ok === false ? 'Analyze error' : 'Analyzed');
    } catch (err) {
      if ($('#f-result')) $('#f-result').value = String(err);
      toast('Analyze failed');
    }
  });

  bindReportOnly();
}

function mount(fromBoot = false) {
  const app = document.getElementById('app');
  const keepDraft = { ...state.draft };
  const route = parseRoutePath();
  if (route) {
    if (route.nav !== state.nav) state.nav = route.nav;
    if (route.tab) setTabId(state.nav, route.tab);
  }
  app.innerHTML = shell();
  bind();
  renderStatusPills();
  renderRightList();
  if (state.nav === 'task-center' && state.tab !== 'report') {
    fillForm(keepDraft, fromBoot);
  }
  if (state.nav === 'skill-ssot') {
    bindSkillSsotExtra();
    if ((state.skill?.tab || 'editor') === 'editor') {
      bindSkillPanel();
      loadSkillPanel();
    } else if (state.skill?.tab === 'contracts') {
      loadSkillContracts();
    } else if (state.skill?.tab === 'dimensions') {
      loadSkillDimensions();
    } else if (state.skill?.tab === 'library') {
      loadSkillLibrary();
    } else if (
      (state.skill?.tab === 'lifecycle' || state.skill?.tab === 'prompt-trace') &&
      state.skill.timelineTaskId
    ) {
      loadSkillTimeline(state.skill.timelineTaskId).then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'skill-ssot') {
          body.innerHTML = skillHtml();
          bindSkillSsotExtra();
        }
      });
    }
  }
  if (state.nav === 'skill-learning') {
    bindSkillLearningPanel();
  }
  if (state.nav === 'watchdog') {
    bindWatchdogPanel();
    refreshWatchdog(true).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'watchdog') {
        body.innerHTML = watchdogHtml();
        bindWatchdogPanel();
      }
      renderStatusPills();
    });
  }
  if (state.nav === 'telemetry') {
    bindTelemetryPanel();
    refreshTelemetry().then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'telemetry') {
        body.innerHTML = workspaceHtml();
        bindTelemetryPanel();
      }
    });
  }
  if (state.nav === 'openclaw') {
    bindOpenclawPanel();
    // The user NAVIGATED to the OpenClaw page, so the live probe is wanted.
    // The TIMER polls below do NOT probe (a status read must not open an MCP
    // session — see `refreshOpenclaw`).
    refreshOpenclaw({ probe: true }).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'openclaw') {
        body.innerHTML = workspaceHtml();
        bindOpenclawPanel();
      }
      renderStatusPills();
    });
  }
  if (state.nav === 'evidence') {
    bindEvidencePanel();
    if (state.evidence.detailId === 'LLM 100') {
      // LLM 100 proof is a fixed pseudo-record, not an EVID- folder.
      loadLlm100().then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'evidence') {
          body.innerHTML = workspaceHtml();
          bindEvidencePanel();
        }
      });
    } else if (state.evidence.detailId) {
      // Deep link straight to one record.
      loadEvidenceDetail(state.evidence.detailId).then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'evidence') {
          body.innerHTML = workspaceHtml();
          bindEvidencePanel();
        }
      });
    } else {
      refreshEvidence().then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'evidence') {
          body.innerHTML = workspaceHtml();
          bindEvidencePanel();
        }
      });
    }
  }
  if (state.nav === 'tool-registry') {
    bindToolRegistryPanel();
    Promise.all([refreshToolRegistry(), refreshPermissionChecklist()]).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'tool-registry') {
        body.innerHTML = toolRegistryHtml();
        bindToolRegistryPanel();
      }
    });
  }
  if (state.nav === 'user-environment') {
    bindUserEnvironmentPanel();
    // Auto-detect on enter (read-only) so the page is populated immediately.
    userEnvironmentDetect(false).then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'user-environment') {
        body.innerHTML = userEnvironmentHtml();
        bindUserEnvironmentPanel();
      }
    });
    // The Assets tab reads user_asset, which is a different endpoint.
    if ((state.userEnvironment?.tab || 'detect') === 'assets') {
      refreshUserAssets().then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'user-environment') {
          body.innerHTML = userEnvironmentHtml();
          bindUserEnvironmentPanel();
        }
      });
    }
    // The Presence tab reads computer_presence, which is a different endpoint.
    // The user: "trigger point by http://127.0.0.1:18765/llm-tasks/ open at
    // browser" / "so you can have status now!"
    if ((state.userEnvironment?.tab || 'detect') === 'presence') {
      refreshComputerPresence().then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'user-environment') {
          body.innerHTML = userEnvironmentHtml();
          bindUserEnvironmentPanel();
        }
      });
    }
    // The Sessions tab reads the PINNED SESSION AREA, which is a different
    // endpoint again. THE HUMAN (2026-09-27): "why i can't find at my
    // http://127.0.0.1:18765/llm-tasks/user_environment/ ????" — because this
    // page had no reader for it. This is the reader.
    if ((state.userEnvironment?.tab || 'detect') === 'sessions') {
      refreshPinnedSessions().then(() => {
        const body = $('#workspace-body');
        if (body && state.nav === 'user-environment') {
          body.innerHTML = userEnvironmentHtml();
          bindUserEnvironmentPanel();
        }
      });
    }
  }
  if (state.nav === 'chat-center') {
    const ccTab = state.chatCenter?.tab || 'flow';
    const root = $(ccTab === 'list'
      ? '#chat-center-list-root'
      : ccTab === 'workflow'
        ? '#chat-center-workflow-root'
        : ccTab === 'setting'
          ? '#chat-center-setting-root'
          : '#chat-center-root');
    if (root && !root.__vueMounted) {
      root.__vueMounted = true;
      try {
        if (ccTab === 'list') mountChatCenterList(root);
        else if (ccTab === 'workflow') mountChatCenterWorkflow(root);
        else if (ccTab === 'setting') mountChatCenterSetting(root);
        else mountChatCenter(root);
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Chat Center (Vue) failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'conversation-center') {
    // THE ONE PAGE for `chat_identity` + `chat_center`. Both old paths are
    // aliased here (LEGACY_NAV_SLUGS), so this block is what a bookmark to
    // either URL actually renders.
    const root = $('#conversation-center-root');
    if (root && !root.__vueMounted) {
      root.__vueMounted = true;
      try {
        mountConversationCenter(root);
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Conversation Center (Vue) failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'capability-center') {
    const root = $('#capability-center-root');
    if (root && !root.__vueMounted) {
      root.__vueMounted = true;
      try {
        mountCapabilityCenter(root);
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Capability Center (Vue) failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'ticket-center') {
    const root = $('#ticket-center-root');
    if (root && !root.__vueMounted) {
      root.__vueMounted = true;
      try {
        // The URL segment is the AUTHORITY for the tab. The component reads its
        // tab from localStorage, so without this a deep link to
        // /ticket_center/detail rendered the TICKETS page -- the URL said
        // `detail` and the page showed `tickets`. Passed explicitly rather than
        // read from the URL inside the component, so there is ONE parser
        // (`parseRoutePath`) and not a second one that can drift.
        window.__ticketCenterTab = state.ticketCenter?.tab || '';
        mountTicketCenter(root);
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Ticket Center (Vue) failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'screen-watch') {
    const root = $('#screen-watch-root');
    if (root && !root.__vueMounted) {
      root.__vueMounted = true;
      try {
        // Same rule as ticket-center: the URL segment is the AUTHORITY for the
        // tab, and the component reads localStorage. Passed explicitly so there
        // is ONE parser (`parseRoutePath`) and not a second one that can drift.
        window.__screenWatchTab = state.screenWatch?.tab || '';
        mountScreenWatch(root);
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Screen Watch (Vue) failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'worker') {
    const root = $('#worker-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountWorker(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Worker page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'playwright') {
    const root = $('#playwright-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountPlaywright(root, state, { toast, navPath });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Playwright page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'identity') {
    const root = $('#identity-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountIdentity(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Identity page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'registers') {
    const root = $('#registers-root');    if (root && !root.__vueMounted) {
      root.__vueMounted = true;
      try {
        mountRegisters(root);
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Registers (Vue) failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'mode-sessions') {
    const root = $('#mode-sessions-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountModeSessions(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Mode & Env page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'terminology') {
    const root = $('#terminology-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountTerminology(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Terminology page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'generator') {
    const root = $('#generator-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountGeneratorCenter(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Generator Center page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'question') {
    const root = $('#question-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountQuestionCenter(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Question Center page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  if (state.nav === 'consultant') {
    const root = $('#consultant-root');
    if (root && !root.__mounted) {
      root.__mounted = true;
      try {
        mountConsultantCenter(root, state, { toast });
      } catch (e) {
        root.innerHTML =
          '<div class="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">' +
          'Consultant Center page failed to mount: ' + esc(e.message || e) + '</div>';
      }
    }
  }
  // Auto-detect environment on enter (populates the top-right flag badge).
  ensureUserEnvironment();
  // Auto-detect OpenClaw on entering /llm-tasks/ so the status pill is accurate
  // immediately, independent of which page the user landed on. A dead MCP
  // server should be visible from EVERY page, not only its settings page.
  refreshOpenclaw().then(() => renderStatusPills());
  // Telemetry refreshes on its own cadence (below); skip it in the 5s loop so
  // the charts do not re-animate every few seconds.
  if (state.nav !== 'telemetry') refreshAll();
}

export function startApp() {
  // THE TIMEZONE TRIGGER POINT (2026-09-25). THE HUMAN:
  //     "timezone tranalate trigger point is windows.load....."
  //     "will have 2 timezone in my eye, it will make user confuse"
  //
  // MEASURED, and the human is right: `ensureUserEnvironment()` was called at
  // the END of `mount()`, i.e. AFTER the page had already rendered. So the FIRST
  // paint showed RAW UTC (the offset was still null -> `fmtLocal` returns
  // "<raw> UTC?"), and only the SECOND paint showed local time. Two clocks on
  // one screen, and the first one is wrong.
  //
  // The fix is ORDER, not a new formatter: fetch the offset BEFORE the first
  // render, so there is only ever ONE clock. `mount()` still calls
  // `ensureUserEnvironment()` (it is idempotent and cached), so a later
  // navigation cannot lose the offset.
  //
  // It is NOT awaited: a slow or dead /api/user_environment must not delay the
  // page. `fmtLocal` already handles an unknown offset by MARKING it, so the
  // worst case is the honest "UTC?" marker, never a silent wrong clock.
  ensureUserEnvironment();
  if (!listRecords().length) {
    upsertRecord(
      createBlankRecord({
        name: 'Sample record',
        task_id: '',
        writer: '',
        status: 'draft',
        prompt_content: '',
        result_content: '',
      })
    );
  }
  const first = listRecords()[0];
  state.draft = createBlankRecord(first);
  state.selectedId = first.id;
  const bootRoute = parseRoutePath();
  if (bootRoute) {
    state.nav = bootRoute.nav;
    if (bootRoute.tab) setTabId(state.nav, bootRoute.tab);
  } else {
    try {
      history.replaceState(
        { nav: state.nav, tab: currentTabId(state.nav) },
        '',
        navPath(state.nav, currentTabId(state.nav))
      );
    } catch (_) {}
  }
  window.addEventListener('popstate', () => {
    const route = parseRoutePath();
    if (route) {
      state.nav = route.nav;
      if (route.tab) setTabId(state.nav, route.tab);
      mount(false);
    }
  });
  mount(true);
  setInterval(() => {
    refreshAll();
  }, 5000);
  // Telemetry has its own slower cadence: aggregates over hours/days do not
  // change every 5s, and re-rendering re-animates the bars.
  setInterval(() => {
    if (state.nav !== 'telemetry') return;
    refreshTelemetry().then(() => {
      const body = $('#workspace-body');
      if (body && state.nav === 'telemetry') {
        body.innerHTML = workspaceHtml();
        bindTelemetryPanel();
      }
    });
  }, 60000);
  // OpenClaw probe is a live MCP call (can take seconds), so it runs on a slow
  // cadence and only refreshes the pill + the settings page body when visible.
  setInterval(() => {
    refreshOpenclaw().then(() => {
      renderStatusPills();
      if (state.nav !== 'openclaw') return;
      const body = $('#workspace-body');
      if (body && state.nav === 'openclaw') {
        body.innerHTML = workspaceHtml();
        bindOpenclawPanel();
      }
    });
  }, 60000);
}