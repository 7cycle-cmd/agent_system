import { API_BASE } from './api.js';

export function renderStep2(container) {
  const selected = JSON.parse(localStorage.getItem('msh_selected') || '[]');
  const targets = JSON.parse(localStorage.getItem('msh_targets') || '[]');
  const targetId = selected[0] || '';
  const target = targets.find(t => t.id === targetId) || { id: targetId, name: targetId || 'Target', image: '' };
  // Always try target logo file first (LOGO is the target).
  const imageUrl = `${API_BASE}/target-image/${target.id}`;

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
      <div class="status hint-capture" id="status">Press Ctrl+Shift+M to capture target</div>
      <img id="screenshot" class="screenshot hidden" alt="screenshot">
      <div class="nav">
        <button class="secondary" id="backBtn">← Back</button>
        <button class="secondary" id="analyzeBtn">Analyze with LLM</button>
      </div>
    </div>
  `;

  async function updateCoords() {
    try {
      const res = await fetch(`${API_BASE}/api/state`);
      const data = await res.json();
      document.getElementById('x').textContent = data.x;
      document.getElementById('y').textContent = data.y;
    } catch (e) {}
  }

  async function capture() {
    const statusEl = document.getElementById('status');
    statusEl.className = 'status';
    statusEl.textContent = 'Capturing...';
    try {
      const res = await fetch(`${API_BASE}/api/capture`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error('capture failed');
      document.getElementById('cx').textContent = data.x;
      document.getElementById('cy').textContent = data.y;
      localStorage.setItem('msh_last_capture', JSON.stringify(data));
      await takeScreenshot();
      statusEl.className = 'status success';
      statusEl.textContent = `STEP 3 — STATUS: SUCCESS | Captured X=${data.x}, Y=${data.y}`;
    } catch (e) {
      statusEl.className = 'status fail';
      statusEl.textContent = 'STEP 3 — STATUS: FAIL | Capture failed';
    }
  }

  async function takeScreenshot() {
    const res = await fetch(`${API_BASE}/api/screenshot`, { method: 'POST' });
    if (res.ok) {
      const img = document.getElementById('screenshot');
      img.src = `${API_BASE}/screenshot.png?t=${Date.now()}`;
      img.classList.remove('hidden');
    }
  }

  async function analyze() {
    const capture = JSON.parse(localStorage.getItem('msh_last_capture') || '{}');
    if (!capture.x) {
      alert('Capture a target first.');
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
  document.getElementById('backBtn').addEventListener('click', () => {
    localStorage.setItem('msh_step', '1');
    window.dispatchEvent(new StorageEvent('storage', { key: 'msh_step' }));
  });

  // global hotkey support inside browser
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'm') {
      e.preventDefault();
      capture();
    }
  });

  setInterval(updateCoords, 200);
  updateCoords();
}
