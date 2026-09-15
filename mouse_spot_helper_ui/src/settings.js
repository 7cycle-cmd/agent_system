import { API_BASE } from './api.js';

async function loadTargets() {
  const res = await fetch(`${API_BASE}/api/targets`);
  return await res.json();
}

async function render() {
  const targets = await loadTargets();
  const list = document.getElementById('targetList');
  list.innerHTML = '';

  targets.forEach(t => {
    const div = document.createElement('div');
    div.style.cssText = 'background:#1e1e1e; border-radius:8px; padding:1em; margin-bottom:1em;';
    div.innerHTML = `
      <label style="display:block; margin:0.6em 0 0.2em; color:#aaa; font-size:0.9em;">Name</label>
      <input type="text" id="name-${t.id}" value="${t.name}" style="width:100%; padding:0.5em;">
      <label style="display:block; margin:0.6em 0 0.2em; color:#888; font-size:0.9em;">Image</label>
      <input type="file" id="file-${t.id}" accept="image/*,.webp,.png,.jpg,.jpeg,.gif,.bmp">
      ${t.image ? `<img src="${API_BASE}/target-image/${t.id}?t=${Date.now()}" style="max-width:80px; max-height:80px; margin-top:0.5em; border-radius:4px;">` : ''}
      <div style="display:flex; gap:0.5em; margin-top:0.8em;">
        <button onclick="saveTarget('${t.id}')">Save</button>
        <svg width="0" height="0"></svg>
        <button class="danger" onclick="deleteTarget('${t.id}')">Delete</button>
      </div>
    `;
    list.appendChild(div);
  });
}

window.saveTarget = async function(id) {
  const name = document.getElementById(`name-${id}`).value;
  const fileInput = document.getElementById(`file-${id}`);
  const body = { id, name };

  if (fileInput.files && fileInput.files[0]) {
    const reader = new FileReader();
    reader.onload = async () => {
      body.image = reader.result.split(',')[1];
      await fetch(`${API_BASE}/api/targets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      render();
    };
    reader.readAsDataURL(fileInput.files[0]);
  } else {
    await fetch(`${API_BASE}/api/targets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    render();
  }
};

window.deleteTarget = async function(id) {
  if (!confirm('Delete this target?')) return;
  await fetch(`${API_BASE}/api/targets/${id}`, { method: 'DELETE' });
  render();
};

document.getElementById('addBtn').addEventListener('click', async () => {
  const name = document.getElementById('newName').value.trim();
  if (!name) return;
  const id = 't_' + Date.now();
  await fetch(`${API_BASE}/api/targets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, name, image: '' })
  });
  document.getElementById('newName').value = '';
  render();
});

render();
