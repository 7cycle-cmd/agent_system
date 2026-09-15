const STORAGE_KEY = 'llm_task_monitor_records';

function uid() {
  return `r_${Math.random().toString(16).slice(2)}${Date.now().toString(16)}`;
}

/**
 * Local draft record.
 * session_id = IDE session id (VS Code / Cursor / Work Buddy / Codex).
 * New tasks leave session_id empty until an IDE works the task.
 */
export function createBlankRecord(partial = {}) {
  return {
    id: partial.id || uid(),
    task_id: partial.task_id || '',
    writer: partial.writer || '',
    // Never invent session_id on create
    session_id: partial.session_id || '',
    context: partial.context || '',
    prompt_content: partial.prompt_content || '',
    result_content: partial.result_content || '',
    status: partial.status || 'draft',
    name: partial.name || '',
    updated_at: partial.updated_at || new Date().toISOString(),
  };
}

/** Append identity trailer for Result display. */
export function formatIdentityTrailer({ session_id = '', task_id = '', writer = '' } = {}) {
  return (
    '---\n' +
    'session_id: ' +
    (session_id || '') +
    '\n' +
    'task_id: ' +
    (task_id || '') +
    '\n' +
    'writer: ' +
    (writer || '') +
    '\n'
  );
}

export function listRecords() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const data = raw ? JSON.parse(raw) : [];
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

function writeAll(records) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(records));
  return records;
}

export function getRecord(id) {
  return listRecords().find((r) => r.id === id) || null;
}

export function upsertRecord(record) {
  const next = createBlankRecord(record);
  next.updated_at = new Date().toISOString();
  if (!next.name) {
    next.name = next.task_id || next.writer || 'Untitled';
  }
  const all = listRecords();
  const idx = all.findIndex((r) => r.id === next.id);
  if (idx >= 0) all[idx] = next;
  else all.unshift(next);
  writeAll(all);
  return next;
}

export function deleteRecord(id) {
  writeAll(listRecords().filter((r) => r.id !== id));
}
