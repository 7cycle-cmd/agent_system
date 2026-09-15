import { API_BASE } from './api.js';

export async function renderStep1(container) {
  container.innerHTML = `
    <div class="panel">
      <h1>Mouse Spot Helper</h1>
      <div class="hint">STEP 1: choose target(s)</div>
      <div class="target-list" id="targetList"></div>
      <div class="nav">
        <button id="nextBtn">Next →</button>
        <button class="secondary" id="settingsBtn">⚙ Settings</button>
      </div>
    </div>
  `;

  const list = container.querySelector('#targetList');
  const targets = await loadTargets();
  localStorage.setItem('msh_targets', JSON.stringify(targets));
  const saved = JSON.parse(localStorage.getItem('msh_selected') || '[]');

  targets.forEach(t => {
    const row = document.createElement('label');
    row.className = 'target-row' + (saved.includes(t.id) ? ' selected' : '');
    const imgSrc = `${API_BASE}/target-image/${t.id}`;
    row.innerHTML = `
      <input type="checkbox" value="${t.id}" ${saved.includes(t.id) ? 'checked' : ''}>
      <img src="${imgSrc}" alt="${t.name}" onerror="this.src='${API_BASE}/static/target-placeholder.png'">
      <span class="name">${t.name}</span>
    `;
    const cb = row.querySelector('input');
    cb.addEventListener('change', () => {
      row.classList.toggle('selected', cb.checked);
      saveSelection();
    });
    list.appendChild(row);
  });

  function saveSelection() {
    const checked = Array.from(list.querySelectorAll('input:checked')).map(cb => cb.value);
    localStorage.setItem('msh_selected', JSON.stringify(checked));
  }

  container.querySelector('#nextBtn').addEventListener('click', () => {
    const selected = JSON.parse(localStorage.getItem('msh_selected') || '[]');
    if (selected.length === 0) {
      alert('Please select at least one target.');
      return;
    }
    localStorage.setItem('msh_step', '2');
    window.dispatchEvent(new StorageEvent('storage', { key: 'msh_step' }));
  });

  container.querySelector('#settingsBtn').addEventListener('click', () => {
    window.open('/settings.html', '_blank');
  });
}

async function loadTargets() {
  try {
    const res = await fetch(`${API_BASE}/api/targets`);
    return await res.json();
  } catch (e) {
    console.error('Failed to load targets', e);
    return [
      { id: 'vscode', name: 'VS Code', image: '' },
      { id: 'doubao', name: '豆包 AI', image: '' },
    ];
  }
}
