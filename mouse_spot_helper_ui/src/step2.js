import { API_BASE } from './api.js';

export function renderStep2(container) {
  const selected = JSON.parse(localStorage.getItem('msh_selected') || '[]');
  const targets = JSON.parse(localStorage.getItem('msh_targets') || '[]');
  const targetId = selected[0] || '';
  const target = targets.find(t => t.id === targetId) || { id: targetId, name: targetId || 'Target', image: '' };
  // Always try target logo file first (LOGO is the target).
  const imageUrl = `${API_BASE}/target-image/${target.id}`;

  let lastHandledSeq = 0;
  let lastShownError = '';

  container.innerHTML = `
    <div class="panel">
      <h1>Mouse Spot Helper</h1>
      <div class="hint">STEP 2: position your mouse and capture</div>
      <div class="target-header">
        <img class="target-logo" src="${imageUrl}" alt="${target.name}" onerror="this.style.display='none'">
        <span class="target-name">${target.name}</span>
      </div>
      <div class="coords">
        <div class="coord-box"><div class="label">X</div><div class="value" id="x">0</div></div>
        <div class="coord-box"><div class="label">Y</div><div class="value" id="y">0</div></div>
        <div class="coord-box"><div class="label">Captured X</div><div class="value" id="cx">-</div></div>
        <div class="coord-box"><div class="label">Captured Y</div><div class="value" id="cy">-</div></div>
      </div>
      <div class="status hint-capture" id="status">Global hotkey only (Ctrl+Shift+M) — works in any window including VS Code</div>
      <img id="screenshot" class="screenshot hidden" alt="screenshot">
      <div class="nav">
        <button class="secondary" id="backBtn">← Back</button>
        <button class="secondary" id="analyzeBtn">Analyze with LLM</button>
      </div>
    </div>
  `;

  async function pollState() {
    try {
      const res = await fetch(`${API_BASE}/api/state`);
      const data = await res.json();
      document.getElementById('x').textContent = data.x;
      document.getElementById('y').textContent = data.y;
      const statusEl = document.getElementById('status');
      const err = data.capture_error || data.error || '';
      if (err && err !== lastShownError) {
        lastShownError = err;
        statusEl.className = 'status fail';
        statusEl.textContent = 'Capture error: ' + err;
      }
      const seq = Number(data.capture_seq || 0);
      if (data.target && seq > 0 && seq !== lastHandledSeq) {
        lastHandledSeq = seq;
        lastShownError = '';
        document.getElementById('cx').textContent = data.target.x;
        document.getElementById('cy').textContent = data.target.y;
        localStorage.setItem('msh_last_capture', JSON.stringify({
          x: data.target.x,
          y: data.target.y,
          capture_seq: seq,
        }));
        const img = document.getElementById('screenshot');
        img.src = `${API_BASE}/screenshot.png?t=${Date.now()}`;
        img.classList.remove('hidden');
        statusEl.className = 'status success';
        statusEl.textContent = `Captured X=${data.target.x}, Y=${data.target.y} (global hotkey)`;
        // Prefer embedded step3 when served from helper origin
        if (API_BASE.includes('18765') || !API_BASE) {
          window.location.href = `${API_BASE}/step3?x=${data.target.x}&y=${data.target.y}`;
        }
      }
    } catch (e) {}
  }

  async function analyze() {
    const capture = JSON.parse(localStorage.getItem('msh_last_capture') || '{}');
    if (!capture.x) {
      alert('Capture a target first (global hotkey).');
      return;
    }
    const selected = JSON.parse(localStorage.getItem('msh_selected') || '[]');
    const targetId = selected[0] || '';
    const targets = JSON.parse(localStorage.getItem('msh_targets') || '[]');
    const target = targets.find(t => t.id === targetId) || { name: targetId };
    const statusEl = document.getElementById('status');
    statusEl.className = 'status';
    statusEl.textContent = 'Analyzing with LLM...';
    try {
      const res = await fetch(`${API_BASE}/api/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_id: targetId,
          target_name: target.name,
          x: capture.x,
          y: capture.y,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.reason || 'analyze failed');
      statusEl.className = data.result === 'SUCCESS' ? 'status success' : 'status fail';
      statusEl.textContent = `LLM: ${data.result} — ${data.reason} (confidence: ${(data.confidence * 100).toFixed(0)}%)`;
    } catch (e) {
      statusEl.className = 'status fail';
      statusEl.textContent = `LLM: FAIL — ${e.message}`;
    }
  }

  document.getElementById('analyzeBtn').addEventListener('click', analyze);
  document.getElementById('backBtn').addEventListener('click', async () => {
    try {
      await fetch(`${API_BASE}/api/reset-capture`, { method: 'POST' });
    } catch (e) {}
    localStorage.setItem('msh_step', '1');
    window.dispatchEvent(new StorageEvent('storage', { key: 'msh_step' }));
  });

  // No browser keydown capture — global pynput hotkey is the only trigger.
  setInterval(pollState, 200);
  pollState();
}
