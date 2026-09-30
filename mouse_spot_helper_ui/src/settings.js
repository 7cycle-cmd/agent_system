import { API_BASE } from './api.js';

async function loadTargets() {
  const res = await fetch(`${API_BASE}/api/targets`);
  return await res.json();
}

async function loadCoordMaps() {
  const byId = {};
  const byName = {};
  try {
    const res = await fetch(`${API_BASE}/api/coord-targets`);
    const data = await res.json();
    if (data && data.ok && Array.isArray(data.points)) {
      for (const p of data.points) {
        if (!p) continue;
        if (p.target_id) byId[p.target_id] = p;
        if (p.target_name && !byName[p.target_name]) byName[p.target_name] = p;
      }
    }
  } catch (e) {}
  return { byId, byName };
}

function pickPoint(t, maps) {
  if (!t) return null;
  if (t.id && maps.byId[t.id]) return maps.byId[t.id];
  const byName = maps.byName[t.name];
  if (byName) {
    const rid = byName.target_id || '';
    if (!rid || rid === t.id) return byName;
  }
  return null;
}

async function dualWriteCoords(name, action, x, y, imagePath, targetId) {
  const body = {
    target_id: targetId || '',
    id: targetId || '',
    target_name: name,
    name,
    action: action || 'click',
    x: x === '' || x == null ? 0 : Number(x),
    y: y === '' || y == null ? 0 : Number(y),
    target_logo: imagePath || '',
    isactive: 1,
    llm_score: 100,
    error: null,
  };
  const res = await fetch(`${API_BASE}/api/coord-targets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new Error(data.error || 'coords save failed');
  return data;
}

async function render() {
  const [targets, coordMaps, state] = await Promise.all([
    loadTargets(),
    loadCoordMaps(),
    fetch(`${API_BASE}/api/state`).then((r) => r.json()).catch(() => ({})),
  ]);
  const list = document.getElementById('targetList');
  list.innerHTML = '';
  const last = state && state.target ? state.target : null;

  targets.forEach((t) => {
    const pt = pickPoint(t, coordMaps) || {};
    const actionVal =
      pt.action != null && pt.action !== '' ? pt.action : t.action || 'click';
    const xVal = pt.x != null && pt.x !== '' ? pt.x : '';
    const yVal = pt.y != null && pt.y !== '' ? pt.y : '';
    const div = document.createElement('div');
    div.style.cssText =
      'background:#1e1e1e; border-radius:8px; padding:1em; margin-bottom:1em;';
    div.innerHTML = `
      <label style="display:block; margin:0.6em 0 0.2em; color:#aaa; font-size:0.9em;">Name *</label>
      <input type="text" id="name-${t.id}" value="${t.name || ''}" style="width:100%; padding:0.5em;">
      <label style="display:block; margin:0.6em 0 0.2em; color:#aaa; font-size:0.9em;">Action *</label>
      <input type="text" id="action-${t.id}" value="${actionVal}" style="width:100%; padding:0.5em;" placeholder="click">
      <div style="display:flex; gap:0.75em;">
        <div style="flex:1">
          <label style="display:block; margin:0.6em 0 0.2em; color:#aaa; font-size:0.9em;">X</label>
          <input type="number" id="x-${t.id}" value="${xVal}" style="width:100%; padding:0.5em;">
        </div>
        <div style="flex:1">
          <label style="display:block; margin:0.6em 0 0.2em; color:#aaa; font-size:0.9em;">Y</label>
          <input type="number" id="y-${t.id}" value="${yVal}" style="width:100%; padding:0.5em;">
        </div>
      </div>
      <label style="display:block; margin:0.6em 0 0.2em; color:#888; font-size:0.9em;">Image</label>
      <input type="file" id="file-${t.id}" accept="image/*,.webp,.png,.jpg,.jpeg,.gif,.bmp">
      ${t.image ? `<img src="${API_BASE}/target-image/${t.id}?t=${Date.now()}" style="max-width:80px; max-height:80px; margin-top:0.5em; border-radius:4px;">` : ''}
      ${pt.error ? `<div style="color:#f87171;font-size:0.85em;margin-top:0.4em;">error: ${pt.error}</div>` : ''}
      <div style="display:flex; gap:0.5em; margin-top:0.8em; flex-wrap:wrap;">
        <button onclick="saveTarget('${t.id}')">Save</button>
        <button class="secondary" onclick="fillFromCapture('${t.id}')">Fill X/Y from capture</button>
        <button class="danger" onclick="deleteTarget('${t.id}')">Delete</button>
      </div>
    `;
    list.appendChild(div);
  });

  if (last && last.x != null && last.y != null) {
    for (const t of targets) {
      const xi = document.getElementById('x-' + t.id);
      const yi = document.getElementById('y-' + t.id);
      if (xi && yi && xi.value === '' && yi.value === '') {
        xi.value = last.x;
        yi.value = last.y;
        break;
      }
    }
  }
}

window.fillFromCapture = async function (id) {
  try {
    const st = await (await fetch(`${API_BASE}/api/state`)).json();
    const t = st && st.target;
    if (!t || t.x == null || t.y == null) {
      alert('No capture yet. Capture a point on the main page first.');
      return;
    }
    document.getElementById('x-' + id).value = t.x;
    document.getElementById('y-' + id).value = t.y;
  } catch (e) {
    alert('Failed to read capture state');
  }
};

window.saveTarget = async function (id) {
  const name = document.getElementById(`name-${id}`).value.trim();
  const action =
    (document.getElementById(`action-${id}`).value || 'click').trim() || 'click';
  const x = document.getElementById(`x-${id}`).value;
  const y = document.getElementById(`y-${id}`).value;
  if (!name) {
    alert('Target name is required.');
    return;
  }
  const fileInput = document.getElementById(`file-${id}`);
  const body = { id, name, action };

  const finish = async (imageB64) => {
    if (imageB64) body.image = imageB64;
    await fetch(`${API_BASE}/api/targets`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await dualWriteCoords(name, action, x, y, `/target-image/${id}`, id);
    render();
  };

  if (fileInput.files && fileInput.files[0]) {
    const reader = new FileReader();
    reader.onload = () => finish(reader.result.split(',')[1]);
    reader.readAsDataURL(fileInput.files[0]);
  } else {
    finish(null);
  }
};

window.deleteTarget = async function (id) {
  if (!confirm('Delete this target?')) return;
  await fetch(`${API_BASE}/api/targets/${id}`, { method: 'DELETE' });
  render();
};

document.getElementById('addBtn').addEventListener('click', async () => {
  const nameEl = document.getElementById('newName');
  const actionEl = document.getElementById('newAction');
  const name = nameEl.value.trim();
  const action = ((actionEl && actionEl.value) || 'click').trim() || 'click';
  if (!name) return;
  const id = 't_' + Date.now();
  const xEl = document.getElementById('newX');
  const yEl = document.getElementById('newY');
  const x = xEl ? xEl.value : '';
  const y = yEl ? yEl.value : '';
  await fetch(`${API_BASE}/api/targets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, name, action, image: '' }),
  });
  await dualWriteCoords(name, action, x, y, `/target-image/${id}`, id);
  nameEl.value = '';
  if (actionEl) actionEl.value = 'click';
  if (xEl) xEl.value = '';
  if (yEl) yEl.value = '';
  render();
});

render();
