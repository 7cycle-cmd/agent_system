# -*- coding: utf-8 -*-
"""6-STEP target capture — the wizard page (self-contained HTML + JS).

WHY A STANDALONE PAGE AND NOT THE VITE SPA
------------------------------------------
The Vite SPA (`llm_task_monitor_ui`) needs `npm run build` before any change is
visible, so a page could be "written" and never actually render. This page is
served directly by the helper, so what is written is what is served. The legacy
`SETTINGS_HTML` in mouse_spot_helper.py is the same pattern.

WHAT THE UI MUST SHOW (user spec 2026-09-20: "ui new version have evidence for
each step")
--------------------------------------------------------------------------
A 6-row stepper. Each row shows its own ARTEFACT thumbnail and a green tick ONLY
when the server reports that step's evidence as PRESENT AND HASH-VERIFIED. The
tick is therefore read from `target_capture_evidence` via /verify — the UI never
decides for itself that a step is covered.

The page deliberately does NOT draw its own ticks on the client's own say-so: a
tick the browser awards itself proves nothing, which is the whole point of having
per-step evidence in the first place.
"""
from __future__ import annotations

from typing import Any

from flask import jsonify, render_template_string, request, send_file

TARGET_CAPTURE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Target Capture — 6 STEP</title>
<style>
  :root{--bg:#0f1115;--panel:#171a21;--line:#2a2f3a;--ink:#e6e9ef;--muted:#9aa3b2;
        --accent:#4da3ff;--ok:#22c55e;--bad:#f43f5e;--warn:#f59e0b;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
  header{padding:14px 18px;border-bottom:1px solid var(--line);background:var(--panel);
         display:flex;gap:14px;align-items:center;flex-wrap:wrap}
  h1{font-size:16px;margin:0}
  .mono{font-family:ui-monospace,Consolas,monospace}
  .muted{color:var(--muted)}
  .wrap{display:grid;grid-template-columns:minmax(320px,420px) 1fr;gap:16px;padding:16px}
  @media(max-width:960px){.wrap{grid-template-columns:1fr}}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px}
  label{display:block;font-size:12px;color:var(--muted);margin:8px 0 4px}
  input,select{width:100%;padding:7px 9px;border-radius:9px;border:1px solid var(--line);
               background:#0c0e13;color:var(--ink)}
  button{padding:7px 12px;border-radius:9px;border:1px solid var(--line);
         background:#20252f;color:var(--ink);cursor:pointer}
  button:hover{border-color:var(--accent)}
  button.primary{background:var(--accent);border-color:var(--accent);color:#04121f;
                 font-weight:600}
  button:disabled{opacity:.45;cursor:not-allowed}
  .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .step{border:1px solid var(--line);border-radius:12px;padding:10px;margin-bottom:10px;
        background:#13161c}
  .step.current{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
  .step.done{border-color:var(--ok)}
  .step-head{display:flex;gap:10px;align-items:center;justify-content:space-between}
  .tick{font-size:18px}
  .tick.ok{color:var(--ok)} .tick.no{color:var(--muted)} .tick.bad{color:var(--bad)}
  .thumb{margin-top:8px;border:1px solid var(--line);border-radius:8px;overflow:hidden;
         background:#000}
  .thumb img{display:block;width:100%;height:auto}
  .shot{width:100%;border:1px solid var(--line);border-radius:12px;background:#000}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;
         border:1px solid var(--line)}
  .badge.ok{color:var(--ok);border-color:var(--ok)}
  .badge.bad{color:var(--bad);border-color:var(--bad)}
  .badge.warn{color:var(--warn);border-color:var(--warn)}
  pre{white-space:pre-wrap;word-break:break-word;font-size:12px;margin:6px 0 0}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:12px}
  .kv div:nth-child(odd){color:var(--muted)}
  .big{font-size:22px;font-weight:700}
</style>
</head>
<body>
<header>
  <h1>Target Capture</h1>
  <span class="badge mono" id="sess">session: -</span>
  <span class="badge mono" id="stepbadge">STEP 1</span>
  <span id="verifybadge" class="badge warn">evidence 0/6</span>
  <span class="row" style="margin-left:auto">
    <button id="btn-refresh">Refresh</button>
    <button id="btn-fill">Fill missing evidence</button>
  </span>
</header>

<div class="wrap">
  <!-- ---------------- left: the flow ---------------- -->
  <div class="panel">
    <div class="muted" style="font-size:12px">source (typed ONCE, reused)</div>
    <label>Source</label>
    <select id="source"><option value="">-- load sources --</option></select>
    <label>Type</label>
    <select id="source_kind">
      <option value="APP">APP (exe path)</option>
      <option value="URL">URL</option>
    </select>
    <label>Path / URL</label>
    <input id="source_ref" placeholder="C:\\Users\\...\\Doubao.exe">
    <label>Target name</label>
    <input id="name" placeholder="豆包 send button">
    <div class="row" style="margin-top:10px">
      <button class="primary" id="btn-create">SUBMIT &amp; start capture</button>
    </div>

    <div id="envproof"></div>

    <hr style="border:0;border-top:1px solid var(--line);margin:14px 0">

    <div id="stepper"></div>

    <div id="gatebox"></div>
  </div>

  <!-- ---------------- right: screen + numbers ---------------- -->
  <div class="panel">
    <div class="row" style="justify-content:space-between">
      <b id="panel-title">Screen</b>
      <span class="muted mono" id="coords">-</span>
    </div>
    <div style="margin-top:10px">
      <img id="shot" class="shot" alt="latest step evidence">
    </div>
    <div id="metrics" style="margin-top:12px"></div>
    <pre id="log" class="muted"></pre>
  </div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let SESSION = new URLSearchParams(location.search).get('session') || '';
let state = {session: null, verify: null, evidence: []};

function logLine(s){
  const p = $('log');
  p.textContent = (new Date().toLocaleTimeString()) + '  ' + s + '\\n' + p.textContent;
}

async function api(path, body){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'},
                      body: JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch(e) { j = {raw:t}; }
  if(!r.ok) throw new Error((j && (j.error||j.detail)) || ('HTTP '+r.status));
  return j;
}

const STEPS = [
  [1,'STEP 1','X1 and Y1'],
  [2,'STEP 2','confirm X1, Y1'],
  [3,'STEP 3','X2 and Y2'],
  [4,'STEP 4','confirm rect'],
  [5,'LEVEL 1','ENVIRONMENT PROOF &middot; evidence/'],
  [6,'LEVEL 2','TARGET PROOF &middot; evidence_final/']
];

function renderStepper(){
  const ev = {};
  (state.evidence||[]).forEach(e => ev[e.step_no] = e);
  const probs = {};
  ((state.verify&&state.verify.problems)||[]).forEach(p=>{
    const m = p.match(/^step (\\d+)/); if(m) probs[+m[1]] = p;
  });
  const cur = state.session ? state.session.step_no : 0;
  $('stepper').innerHTML = STEPS.map(([n,label,what])=>{
    const e = ev[n];
    const bad = probs[n];
    const cls = bad ? 'bad' : (e ? 'ok' : 'no');
    const sym = bad ? '\\u2716' : (e ? '\\u2714' : '\\u25CB');
    return `<div class="step ${e&&!bad?'done':''} ${cur===n?'current':''}">
      <div class="step-head">
        <div><b>${label}</b> <span class="muted">${what}</span></div>
        <span class="tick ${cls}">${sym}</span>
      </div>
      ${e ? `<div class="kv" style="margin-top:6px">
        <div>artefact</div><div class="mono">${e.kind} &middot; ${(e.png_path||'').split(/[\\\\/]/).pop()}</div>
        <div>sha256</div><div class="mono">${(e.sha256||'').slice(0,16)}&hellip;</div>
        <div>size</div><div class="mono">${e.width||'?'} x ${e.height||'?'}</div>
        ${e.detail?`<div>detail</div><div class="mono">${escapeHtml(e.detail)}</div>`:''}
      </div>` : `<div class="muted" style="margin-top:6px;font-size:12px">
        ${bad ? escapeHtml(bad) : 'no evidence recorded yet'}</div>`}
      ${e && !bad ? `<div class="thumb"><img src="/target-capture/evidence/${SESSION}/step${n}.png?v=${(e.sha256||'').slice(0,8)}"></div>` : ''}
    </div>`;
  }).join('');
}

function escapeHtml(s){
  return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}

function renderGate(){
  const s = state.session;
  if(!s) { $('gatebox').innerHTML=''; return; }
  const steps = s.source_id ? '' : '<div class="badge warn">source not set — STEP 4 needs it</div>';
  $('gatebox').innerHTML = `
    <div class="row" style="margin-top:6px">
      <button id="btn-s1" ${s.step_no>1?'disabled':''}>STEP 1 (read cursor)</button>
      <button id="btn-s2" ${s.step_no!==2?'disabled':''}>STEP 2 confirm</button>
    </div>
    <div class="row" style="margin-top:8px">
      <button id="btn-s3" ${s.step_no!==3?'disabled':''}>STEP 3 (read cursor)</button>
      <button id="btn-s4" ${s.step_no!==4?'disabled':''}>STEP 4 confirm</button>
    </div>
    <div class="row" style="margin-top:8px">
      <input id="evid" placeholder="EVID-... (for STEP 5/6)" value="${escapeHtml(s.screenshot_id||'')}">
    </div>
    <div class="row" style="margin-top:8px">
      <button class="primary" id="btn-s5" ${s.step_no!==5?'disabled':''}>LEVEL 1 &mdash; ENVIRONMENT PROOF</button>
      <button class="primary" id="btn-s6" ${s.step_no!==6?'disabled':''}>LEVEL 2 &mdash; TARGET PROOF + register</button>
    </div>
    ${steps}`;
  const on = (id, fn) => { const b=$(id); if(b && !b.disabled) b.onclick = fn; };
  on('btn-s1', ()=>runStep('step1','read the cursor for X1,Y1'));
  on('btn-s2', ()=>runStep('step2','confirm X1,Y1'));
  on('btn-s3', ()=>runStep('step3','read the cursor for X2,Y2'));
  on('btn-s4', ()=>runStep('step4','confirm the rectangle'));
  on('btn-s5', ()=>runStep('step5','LEVEL 1 ENVIRONMENT PROOF (evidence/)', {evidence_id: $('evid').value.trim()}));
  on('btn-s6', ()=>runStep('step6','LEVEL 2 TARGET PROOF (evidence_final/) + register', {evidence_id: $('evid').value.trim()}));
}

async function runStep(ep, what, body){
  try{
    logLine(what + ' ...');
    const j = await api(`/api/target-capture/${SESSION}/${ep}`, body||{});
    logLine(what + ' -> ' + JSON.stringify(j).slice(0,180));
    await refresh();
  }catch(e){ logLine('FAILED ' + what + ': ' + e.message); }
}

function renderMetrics(){
  const s = state.session; if(!s){ $('metrics').innerHTML=''; return; }
  const w = (s.x2!=null && s.x1!=null) ? (s.x2 - s.x1) : null;
  const h = (s.y2!=null && s.y1!=null) ? (s.y2 - s.y1) : null;
  $('coords').textContent = `(${s.x1??'-'},${s.y1??'-'}) - (${s.x2??'-'},${s.y2??'-'})`;
  $('metrics').innerHTML = `<div class="row">
      <div><div class="muted" style="font-size:11px">WIDTH (X2-X1)</div>
           <div class="big">${w??'-'}</div></div>
      <div><div class="muted" style="font-size:11px">HEIGHT (Y2-Y1)</div>
           <div class="big">${h??'-'}</div></div>
      <div><div class="muted" style="font-size:11px">CENTRE</div>
           <div class="big">${w!=null?`${s.x1+Math.floor(w/2)},${s.y1+Math.floor(h/2)}`:'-'}</div></div>
    </div>`;
}

function latestShot(){
  const ev = (state.evidence||[]).slice().sort((a,b)=>b.step_no-a.step_no);
  if(!ev.length){ $('shot').removeAttribute('src'); $('panel-title').textContent='Screen'; return; }
  const e = ev[0];
  $('panel-title').textContent = 'STEP ' + e.step_no + ' evidence — ' + e.kind;
  $('shot').src = `/target-capture/evidence/${SESSION}/step${e.step_no}.png?v=${(e.sha256||'').slice(0,8)}`;
}

async function loadEnvProof(){
  const box = $('envproof');
  if(!box) return;
  if(!SESSION){ box.innerHTML = ''; return; }
  box.innerHTML = '<div class="muted" style="font-size:12px">environment proof ...</div>';
  try{
    const r = await fetch(`/api/target-capture/${SESSION}/env-proof`);
    const j = await r.json();
    state.envProof = j;
    const rows = (j.checks||[]).map(c => `<div>${c.ok?'\u2714':'\u2716'} <b>${escapeHtml(c.name)}</b>
      <span class="muted">${escapeHtml(c.detail||'')}</span></div>`).join('');
    box.innerHTML = `<div class="card" style="border:1px solid ${j.ok?'#1a7f37':'#b42318'};border-radius:10px;padding:10px;margin-bottom:10px">
      <div class="row" style="justify-content:space-between">
        <b>ENVIRONMENT PROOF</b>
        <span class="badge ${j.ok?'ok':'bad'}">${j.passed}/${j.total}</span>
      </div>
      <div style="font-size:12px;margin-top:6px">${rows || '(no checks)'}</div>
      ${j.ok?'':`<div class="badge bad" style="margin-top:8px">BLOCKED — 
        ${escapeHtml((j.blocking||[]).join(', '))} must pass before STEP 1</div>`}
    </div>`;
    applyEnvGate();
  }catch(e){
    box.innerHTML = `<div class="badge bad">environment proof unavailable: ${escapeHtml(e.message)}</div>`;
    state.envProof = {ok:false};
    applyEnvGate();
  }
}

// Disable every STEP button until the environment is PROVEN.
// A disabled button is a REFUSAL the operator can see; a warning line is not —
// measured 2026-09-21, a rule that is only written down does not run.
function applyEnvGate(){
  const ok = !!(state.envProof && state.envProof.ok);
  ['btn-s1','btn-s2','btn-s3','btn-s4','btn-s5','btn-s6'].forEach(id=>{
    const b = $(id);
    if(!b) return;
    if(!ok){ b.disabled = true; b.title = 'environment not proven'; }
  });
  const box = $('envproof');
  if(box && !ok) box.dataset.blocked = '1';
}

async function refresh(){
  if(!SESSION) return;
  // ENVIRONMENT PROOF runs BEFORE anything else on ENTER.
  // Every step writes evidence, so the environment must be PROVEN before STEP 1
  // rather than discovered after it. It is awaited first so the step buttons are
  // gated by the time the stepper draws — a gate that renders late is a gate the
  // operator clicks past.
  await loadEnvProof();
  try{
    const j = await api(`/api/target-capture/${SESSION}`);
    state.session = j.session;
    state.evidence = j.evidence || [];
    state.verify = j.verify || null;
    $('sess').textContent = 'session: ' + SESSION;
    $('stepbadge').textContent = 'STEP ' + (j.session?j.session.step_no:'?');
    const v = state.verify || {};
    $('verifybadge').textContent = `evidence ${v.count||0}/${v.total||6}`;
    $('verifybadge').className = 'badge ' + (v.ok ? 'ok' : (v.count ? 'warn' : 'bad'));
    if(j.session){
      if(!$('name').value) $('name').value = j.session.name || '';
      if(!$('source_ref').value) $('source_ref').value = j.session.source_ref || '';
      // #evid lives INSIDE renderGate()'s template, and renderGate() runs after
      // this block. Touching it here threw on null and aborted refresh() BEFORE
      // the stepper was drawn — the page then looked like it had no steps at all
      // while its badges, set earlier in the same function, were already correct.
      // Guard it, and let renderGate() carry the value once it exists.
      const evidEl = $('evid');
      if(evidEl && j.session.screenshot_id) evidEl.value = j.session.screenshot_id;
    }
    renderStepper(); renderGate(); renderMetrics(); latestShot();
    // renderGate() writes the step buttons, so the environment gate must be
    // re-applied AFTER it — otherwise freshly rendered buttons are enabled again
    // and the refusal silently disappears. Same trap as "markup injected after
    // bind() needs its own bind pass".
    applyEnvGate();
  }catch(e){ logLine('refresh failed: ' + e.message); }
}

async function loadSources(){
  try{
    const rows = await api('/api/sources');
    // The helper answers {"ok":true,"count":N,"rows":[...]}. Accepting only a
    // bare array left the dropdown EMPTY while everything else looked healthy —
    // a control that silently renders nothing is worse than one that errors.
    const list = Array.isArray(rows) ? rows : (rows.rows || rows.sources || []);
    $('source').innerHTML = ['<option value="">-- select --</option>'].concat(
      list.map(s=>`<option value="${s.id}" data-kind="${escapeHtml(s.source_kind||'')}"
        data-ref="${escapeHtml(s.url||'')}">${escapeHtml(s.name)} (${escapeHtml(s.kind)})</option>`)
    ).join('');
    $('source').onchange = () => {
      const o = $('source').selectedOptions[0]; if(!o) return;
      if(o.dataset.kind) $('source_kind').value = o.dataset.kind;
      if(o.dataset.ref) $('source_ref').value = o.dataset.ref;
    };
  }catch(e){ logLine('load sources failed: ' + e.message); }
}

$('btn-create').onclick = async () => {
  try{
    const j = await api('/api/target-capture/session', {
      name: $('name').value.trim(),
      source_id: parseInt($('source').value||'0',10),
      source_kind: $('source_kind').value,
      source_ref: $('source_ref').value.trim()
    });
    SESSION = j.session.session_key;
    history.replaceState(null,'','/target-capture?session='+encodeURIComponent(SESSION));
    logLine('session created: ' + SESSION);
    await refresh();
  }catch(e){ logLine('create failed: ' + e.message); }
};
$('btn-refresh').onclick = refresh;
$('btn-fill').onclick = async () => {
  try{
    const j = await api(`/api/target-capture/${SESSION}/fill`, {});
    logLine('fill -> ' + JSON.stringify(j.verify||{}).slice(0,180));
    await refresh();
  }catch(e){ logLine('fill failed: ' + e.message); }
};

loadSources();
if(SESSION) refresh(); else logLine('fill Name / Source / Path and press SUBMIT.');
</script>
</body>
</html>
"""


def register_target_capture_routes(app: Any, *, base_dir: Any, helper_log=None) -> None:
    """Attach the 6-STEP target capture page + API to an existing Flask app.

    WHY A REGISTRATION FUNCTION INSTEAD OF TOP-LEVEL ROUTES: mouse_spot_helper.py
    is ~8700 lines with no Blueprint, and the routes need `app` plus the module's
    own helpers. Taking them as arguments keeps this module importable (and thus
    testable) without importing the whole helper.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(base_dir))
    import coord_store as cs
    import target_capture as tc
    import target_step_evidence as tse

    def _log(msg: str) -> None:
        if helper_log:
            helper_log(msg)
        else:
            print(msg, flush=True)

    @app.route("/target-capture")
    def target_capture_page() -> Any:
        return render_template_string(TARGET_CAPTURE_HTML)

    def _slug(text: str) -> str:
        """A filesystem-safe slug, so an evidence id never carries a separator."""
        return "".join(c if c.isalnum() else "-" for c in str(text))[:48].strip("-")

    def _target_hwnd(session_key: str = "", session: dict | None = None) -> int:
        """The window to capture, derived from the SESSION'S SOURCE, or 0.

        WHY THE SOURCE AND NOT A HARD-CODED APP
        ---------------------------------------
        MEASURED BUG 2026-09-20: this called `pick_doubao_window()` unconditionally,
        ignoring the session's source. A session whose source was
        `URL https://gemini.google.com/app` therefore captured the DOUBAO window and
        reported ok — so the LEVEL 1 environment gate judged the WRONG PROGRAM. With
        豆包 closed it fell back to a full-screen capture that happened to include
        the right browser, so the same session captured the right app or the wrong
        app depending on whether an unrelated program was running.

        The source is entered ONCE at session creation and reused for every step, so
        the window is derived from it. A no-match returns **0** (a full-screen
        capture) and the reason is LOGGED, rather than silently substituting another
        app. Callers that need the strict behaviour can pass the report from
        `_resolve_source_window()` to STEP 5/6, which gate on it.
        """
        s = session
        if s is None and session_key:
            try:
                s = cs.get_capture_session(session_key)
            except Exception as e:
                _log("session read failed for %r: %s: %s"
                     % (session_key, type(e).__name__, e))
        kind = str((s or {}).get("source_kind") or "").strip().upper()
        ref = str((s or {}).get("source_ref") or "").strip()
        if not kind or not ref:
            _log("no source on the session -> full-screen capture")
            return 0
        try:
            sys.path.insert(0, str(base_dir))
            import source_window as sw

            rep = sw.resolve_window(kind, ref, allow_fallback=False)
            _log("capture window: %s -> %s" % (rep["rule"], rep["detail"]))
            return int(rep.get("hwnd") or 0)
        except Exception as e:
            _log("source window resolve failed: %s: %s" % (type(e).__name__, e))
            return 0

    def _resolve_source_window(session_key: str) -> dict:
        """The full resolution report (with env + candidates) for the UI to show."""
        s = cs.get_capture_session(session_key)
        if not s:
            return {"ok": False, "detail": "no such session"}
        kind = str(s.get("source_kind") or "").strip().upper()
        ref = str(s.get("source_ref") or "").strip()
        if not kind or not ref:
            return {"ok": False, "detail": "session has no source_kind/source_ref"}
        try:
            sys.path.insert(0, str(base_dir))
            import source_window as sw

            return sw.resolve_window(kind, ref, allow_fallback=False)
        except Exception as e:
            return {"ok": False,
                    "detail": "resolve failed: %s: %s" % (type(e).__name__, e)}

    @app.route("/api/target-capture/<session_key>/source-window")
    def api_tc_source_window(session_key: str) -> Any:
        """Show WHICH window the session's source resolves to, and the environment.

        A capture target nobody can inspect is how a session silently captures the
        wrong program, so the resolution (rule, hwnd, title, and the measured
        environment) is exposed here rather than only in a log line.
        """
        return jsonify(_resolve_source_window(session_key))

    @app.route("/api/target-capture/<session_key>/env-proof", methods=["GET", "POST"])
    def api_tc_env_proof(session_key: str) -> Any:
        """The ENVIRONMENT PROOF the page runs on ENTER.

        Every step of this wizard WRITES EVIDENCE, so the environment must be
        proven BEFORE step 1, not discovered afterwards. This aggregates the
        requirements that were previously written down but never enforced
        (`env_requirements.check_all`), and it is what the UI uses to disable the
        step buttons.

        Query/body `require_pins` (default 0): a taskbar pin is an OPERATOR
        PREFERENCE, not a machine property, so it is opt-in — and when opted in and
        missing, it FAILS the checklist rather than being quietly satisfied.
        """
        s = cs.get_capture_session(session_key)
        if not s:
            return jsonify({"ok": False, "error": "no such session"}), 404
        data = request.get_json(silent=True) or {}
        rp = data.get("require_pins")
        if rp is None:
            rp = request.args.get("require_pins")
        require_pins = str(rp or "0").strip().lower() in ("1", "true", "yes", "on")
        try:
            import env_requirements as _er

            out = _er.check_all(str(s.get("source_kind") or ""),
                                str(s.get("source_ref") or ""),
                                require_pins=require_pins)
            return jsonify(out)
        except Exception as e:
            return jsonify({"ok": False, "checks": [],
                            "blocking": ["env_proof"],
                            "error": "%s: %s" % (type(e).__name__, e)}), 500

    @app.route("/api/target-capture/session", methods=["POST"])
    def api_target_capture_session() -> Any:
        """Create or resume a capture session. Name + source are entered ONCE."""
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400
        try:
            sid = int(data.get("source_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        if sid <= 0:
            return jsonify({"ok": False,
                            "error": "source_id required — pick a source"}), 400
        kind = str(data.get("source_kind") or "").strip().upper()
        if kind not in ("APP", "URL"):
            return jsonify({"ok": False,
                            "error": "source_kind must be APP or URL"}), 400
        ref = str(data.get("source_ref") or "").strip()
        if not ref:
            return jsonify({"ok": False, "error":
                            "source_ref required (exe path or url)"}), 400
        # A session key that is stable for the same name+source, so re-submitting
        # RESUMES instead of silently starting a second, competing capture.
        sk = "TC-%s" % "".join(c if c.isalnum() else "-" for c in name)[:40]
        try:
            s = cs.upsert_capture_session(sk, name=name, source_id=sid,
                                          source_kind=kind, source_ref=ref)
        except Exception as e:
            return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
        return jsonify({"ok": True, "session": s})

    @app.route("/api/target-capture/<session_key>", methods=["GET"])
    def api_target_capture_get(session_key: str) -> Any:
        s = cs.get_capture_session(session_key)
        if not s:
            return jsonify({"ok": False, "error": "no such session"}), 404
        return jsonify({"ok": True, "session": s,
                        "evidence": cs.list_step_evidence(session_key),
                        "verify": cs.verify_step_evidence(session_key),
                        "missing": tse.missing_plan(session_key)})

    def _step_guard(session_key: str):
        s = cs.get_capture_session(session_key)
        if not s:
            return None, (jsonify({"ok": False, "error": "no such session"}), 404)
        return s, None

    # Which step endpoints WRITE evidence, and therefore must not run before the
    # environment is proven. Step 1 is included on purpose: it already writes its
    # own artefact, so "prove the environment first" cannot start at step 5.
    _ENV_GATED_STEPS = {"step1", "step2", "step3", "step4", "step5", "step6"}

    def _env_refusal(session_key: str, step: str):
        """Refuse a step SERVER-SIDE when the environment is not proven.

        WHY THE SERVER AND NOT ONLY THE BUTTON
        --------------------------------------
        Disabling a button in the page is a courtesy to the operator; it is not a
        gate. A curl, a retry, a stale page, or a future caller bypasses it
        silently. MEASURED 2026-09-21: the same shape (a rule written down but not
        enforced) produced four defects in one day. So the refusal lives here, and
        the page's disabled button is only the visible half of it.
        """
        if step not in _ENV_GATED_STEPS:
            return None
        s = cs.get_capture_session(session_key)
        if not s:
            return None
        try:
            import env_requirements as _er

            out = _er.check_all(str(s.get("source_kind") or ""),
                                str(s.get("source_ref") or ""),
                                require_pins=str(
                                    request.args.get("require_pins") or "0"
                                ).strip().lower() in ("1", "true", "yes", "on"))
        except Exception as e:
            return jsonify({"ok": False, "error":
                            "environment proof unavailable: %s: %s"
                            % (type(e).__name__, e)}), 500
        if out.get("ok"):
            return None
        return jsonify({
            "ok": False,
            "error": ("environment not proven — %s must pass before %s: %s"
                      % (", ".join(out.get("blocking") or []), step.upper(),
                         " | ".join(c["detail"] for c in out.get("checks") or []
                                    if not c["ok"]))),
            "env_proof": out,
        }), 409

    @app.route("/api/target-capture/<session_key>/step1", methods=["POST"])
    def api_tc_step1(session_key: str) -> Any:
        s, err = _step_guard(session_key)
        if err:
            return err
        refused = _env_refusal(session_key, "step1")
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        # No x1/y1 in the body -> READ THE CURSOR. That is the point of the step:
        # the operator aims the mouse and the value comes from the machine, not
        # from something typed in.
        if data.get("x1") is None or data.get("y1") is None:
            import pyautogui
            x1, y1 = pyautogui.position()
        else:
            x1, y1 = int(data["x1"]), int(data["y1"])
        try:
            return jsonify(tc.step1_capture_p1(session_key, x1, y1,
                                               capture_hwnd=_target_hwnd(session_key, s)))
        except tc.StepError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/target-capture/<session_key>/step2", methods=["POST"])
    def api_tc_step2(session_key: str) -> Any:
        s, err = _step_guard(session_key)
        if err:
            return err
        refused = _env_refusal(session_key, "step2")
        if refused:
            return refused
        try:
            return jsonify(tc.step2_confirm_p1(session_key,
                                               capture_hwnd=_target_hwnd(session_key, s)))
        except tc.StepError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/target-capture/<session_key>/step3", methods=["POST"])
    def api_tc_step3(session_key: str) -> Any:
        s, err = _step_guard(session_key)
        if err:
            return err
        refused = _env_refusal(session_key, "step3")
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        if data.get("x2") is None or data.get("y2") is None:
            import pyautogui
            x2, y2 = pyautogui.position()
        else:
            x2, y2 = int(data["x2"]), int(data["y2"])
        try:
            return jsonify(tc.step3_capture_p2(session_key, x2, y2,
                                               capture_hwnd=_target_hwnd(session_key, s)))
        except tc.StepError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/target-capture/<session_key>/step4", methods=["POST"])
    def api_tc_step4(session_key: str) -> Any:
        s, err = _step_guard(session_key)
        if err:
            return err
        refused = _env_refusal(session_key, "step4")
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(tc.step4_confirm_rect(
                session_key,
                name=data.get("name") or s.get("name"),
                source_id=data.get("source_id") or s.get("source_id"),
                source_kind=data.get("source_kind") or s.get("source_kind"),
                source_ref=data.get("source_ref") or s.get("source_ref"),
                capture_hwnd=_target_hwnd(session_key, s),
            ))
        except tc.StepError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/target-capture/<session_key>/step5", methods=["POST"])
    def api_tc_step5(session_key: str) -> Any:
        s, err = _step_guard(session_key)
        if err:
            return err
        refused = _env_refusal(session_key, "step5")
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        evid = str(data.get("evidence_id") or s.get("screenshot_id") or "").strip()
        if not evid:
            # The gate needs a REAL folder. Deriving a stable id from the session
            # means the operator never has to type an id the flow never created —
            # measured 2026-09-20, STEP 5 gated an absent folder for exactly that
            # reason. A typed id still wins when supplied.
            evid = "EVID-%s" % _slug(session_key)
        try:
            return jsonify(tc.step5_gate_evidence(
                session_key, evidence_id=evid, capture_hwnd=_target_hwnd(session_key, s)))
        except tc.StepError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/target-capture/<session_key>/step6", methods=["POST"])
    def api_tc_step6(session_key: str) -> Any:
        s, err = _step_guard(session_key)
        if err:
            return err
        refused = _env_refusal(session_key, "step6")
        if refused:
            return refused
        data = request.get_json(silent=True) or {}
        evid = str(data.get("evidence_id") or s.get("screenshot_id") or "").strip()
        if not evid:
            evid = "EVID-%s" % _slug(session_key)
        try:
            return jsonify(tc.step6_gate_and_registry(
                session_key, evidence_id=evid, capture_hwnd=_target_hwnd(session_key, s)))
        except tc.StepError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/target-capture/<session_key>/fill", methods=["POST"])
    def api_tc_fill(session_key: str) -> Any:
        """Render every step's artefact that the session already supports."""
        data = request.get_json(silent=True) or {}
        evid = str(data.get("evidence_id") or "").strip()
        if not evid:
            s = cs.get_capture_session(session_key) or {}
            evid = str(s.get("screenshot_id") or "").strip()
        return jsonify(tse.render_all(session_key, evidence_id=evid))

    @app.route("/api/target-capture/<session_key>/verify", methods=["GET"])
    def api_tc_verify(session_key: str) -> Any:
        """409 unless ALL SIX steps have a hash-verified artefact.

        Returns 409 rather than 200-with-ok:false so a caller that only checks the
        status code cannot mistake "5 of 6" for success.
        """
        v = cs.verify_step_evidence(session_key)
        return jsonify(v), (200 if v["ok"] else 409)

    @app.route("/target-capture/evidence/<session_key>/<path:fname>")
    def target_capture_evidence(session_key: str, fname: str) -> Any:
        # Only the recorded per-step PNGs are servable, and only by their
        # recorded name: serving an arbitrary path would turn the evidence route
        # into a file-read primitive.
        allowed = {"step%d.png" % n for n in cs.TARGET_STEP_NAMES}
        if fname not in allowed:
            return jsonify({"ok": False, "error": "not an evidence file"}), 404
        p = Path(tse.STEP_EVIDENCE_ROOT) / session_key / fname
        if not p.is_file():
            return jsonify({"ok": False, "error": "no such evidence"}), 404
        return send_file(str(p), mimetype="image/png")

    _log("target capture routes registered: /target-capture + 9 API endpoints")