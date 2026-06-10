const GENERATOR_URL = (window.__CONFIG__ || {}).generatorUrl || 'http://localhost:5001';
const APP_MODE      = (window.__CONFIG__ || {}).mode || 'local';

// ─── State ────────────────────────────────────────────────────────────────────
const state = {
  folder: null,
  files: [],          // [{name, lines:[{raw, timestamp, text, seconds, minute_bucket}]}]
  activeFile: null,   // filename string
  mode: 'checkbox',   // 'checkbox' | 'highlight'
  checked: {},        // {filename: Set<raw_line_string>}
  highlighted: {},    // {filename: Set<raw_line_string>}
  currentJobId: null,
  resultJobId: null,
  lastPrompt: '',     // prompt used for the most recent Analyze call
  resultSegmentStarts: [],
  resultDownloadUrl: null,
  resultPath: null,
  librarySegmentStarts: [],
  librarySort: 'newest',
  libraryEntries: [],
};

// ─── IndexedDB helpers (persist FileSystemDirectoryHandle across reloads) ────
function _idbOpen() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('sizzle', 1);
    req.onupgradeneeded = () => req.result.createObjectStore('kv');
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function _idbSave(key, value) {
  const db = await _idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readwrite');
    tx.objectStore('kv').put(value, key);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
}
async function _idbLoad(key) {
  const db = await _idbOpen();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('kv', 'readonly');
    const req = tx.objectStore('kv').get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => reject(req.error);
  });
}

// ─── Downloads record in localStorage ────────────────────────────────────────
// sizzle_downloads: { [entryId]: { folderName, filename, localFolderPath|null } }
function _getDownloads() {
  try { return JSON.parse(localStorage.getItem('sizzle_downloads') || '{}'); }
  catch { return {}; }
}
function _getDownload(entryId) { return _getDownloads()[entryId] || null; }
function _setDownload(entryId, info) {
  const d = _getDownloads();
  d[entryId] = info;
  localStorage.setItem('sizzle_downloads', JSON.stringify(d));
}

// ─── Output folder name display ───────────────────────────────────────────────
function _updateOutputFolderUI() {
  const name = localStorage.getItem('sizzle_output_folder_name');
  const label = name ? `📁 ${name}` : '📁 Set output folder';
  const r = $('btn-set-output-folder');
  const l = $('btn-lib-set-output-folder');
  if (r) r.textContent = label;
  if (l) l.textContent = label;
}

// ─── Output folder picker ─────────────────────────────────────────────────────
async function _pickOutputFolder() {
  let handle;
  try {
    handle = await window.showDirectoryPicker({ mode: 'readwrite' });
  } catch (e) {
    if (e.name === 'AbortError') return null; // user cancelled
    throw e;
  }
  await _idbSave('sizzle_output_dir_handle', handle);
  localStorage.setItem('sizzle_output_folder_name', handle.name);
  _updateOutputFolderUI();
  return handle;
}

// ─── Core save: write blob to dir handle, probe for OS path ──────────────────
async function _saveToOutputFolder(handle, filename, blob, entryId) {
  // Write video file
  const fh = await handle.getFileHandle(filename, { create: true });
  const writable = await fh.createWritable();
  await writable.write(blob);
  await writable.close();

  // Write probe file, ask server to locate it on disk
  const probeUuid = crypto.randomUUID();
  const probeName = `sizzle_probe_${probeUuid}.tmp`;
  let localFolderPath = null;
  try {
    const probeFh = await handle.getFileHandle(probeName, { create: true });
    const probeW = await probeFh.createWritable();
    await probeW.write(probeUuid);
    await probeW.close();

    const scanResp = await fetch(`${GENERATOR_URL}/find-local-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ probe_name: probeName, probe_content: probeUuid }),
    });
    if (scanResp.ok) localFolderPath = (await scanResp.json()).path || null;
  } catch { /* probe scan is best-effort */ }

  try { await handle.removeEntry(probeName); } catch { /* best-effort */ }

  _setDownload(entryId, { folderName: handle.name, filename, localFolderPath });
  return { folderName: handle.name, localFolderPath };
}

// ─── Auto-save the just-generated reel to the output folder ──────────────────
async function _autoSaveReelResult(jobId, filename, entryId) {
  const handle = await _idbLoad('sizzle_output_dir_handle');
  if (!handle) return null;

  const perm = await handle.queryPermission({ mode: 'readwrite' });
  if (perm !== 'granted') await handle.requestPermission({ mode: 'readwrite' });

  const resp = await fetch(`${GENERATOR_URL}/video/${jobId}`);
  if (!resp.ok) throw new Error(`Video fetch failed: ${resp.status}`);
  const blob = await resp.blob();

  return _saveToOutputFolder(handle, filename, blob, entryId);
}

let _genWs = null;  // active generation WebSocket

function _saveSelections() {
  if (!state.folder) return;
  try {
    const key = 'sizzle_sel_' + state.folder;
    const payload = {
      checked: {},
      highlighted: {},
    };
    for (const [filename, set] of Object.entries(state.checked)) {
      payload.checked[filename] = [...set];
    }
    for (const [filename, set] of Object.entries(state.highlighted)) {
      payload.highlighted[filename] = [...set];
    }
    localStorage.setItem(key, JSON.stringify(payload));
  } catch (_) {
    // localStorage may be unavailable (private mode quota, etc.) — fail silently
  }
}

function _clearSelections() {
  // Remove the persisted payload so a page reload starts empty.
  if (state.folder) {
    try { localStorage.removeItem('sizzle_sel_' + state.folder); } catch (_) {}
  }
  // Reset in-memory Sets so the workspace renders with nothing selected
  // if the user navigates back without reloading.
  for (const filename of Object.keys(state.checked))     state.checked[filename]     = new Set();
  for (const filename of Object.keys(state.highlighted)) state.highlighted[filename] = new Set();
  $('analyze-add-row')?.classList.add('hidden');
  const addInput = $('analyze-add-input');
  if (addInput) addInput.value = '';
}

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ─── Navigation ───────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.dataset.tab;
    $('tab-create').classList.toggle('hidden', tab !== 'create');
    $('tab-library').classList.toggle('hidden', tab !== 'library');
    if (tab === 'library') fetchLibrary();
  });
});

$('library-sort').addEventListener('change', e => {
  state.librarySort = e.target.value;
  renderLibrary();
});

// ─── Screen helpers ───────────────────────────────────────────────────────────
function showScreen(id) {
  ['screen-folder-picker','screen-transcribing','screen-workspace',
   'screen-generating','screen-result'].forEach(s => {
    $(s).classList.toggle('hidden', s !== id);
  });
}

// ─── Folder picker ────────────────────────────────────────────────────────────
$('btn-browse').addEventListener('click', async () => {
  if (APP_MODE === 'cloud') return; // cloud mode wires its own handler in initCloudMode
  const resp = await fetch('/browse', { method: 'POST' });
  const { path } = await resp.json();
  if (path) $('folder-path-input').value = path;
});

$('btn-load-folder').addEventListener('click', () => {
  if (APP_MODE === 'cloud') return; // cloud mode wires its own handler in initCloudMode
  const folder = $('folder-path-input').value.trim();
  if (!folder) return;
  openFolder(folder);
});

$('folder-path-input').addEventListener('keydown', e => {
  if (APP_MODE === 'cloud') return; // cloud mode wires its own handler in initCloudMode
  if (e.key === 'Enter') {
    const folder = e.target.value.trim();
    if (folder) openFolder(folder);
  }
});

async function openFolder(folder, displayName) {
  // Show a neutral loading indicator while the folder loads.
  // In cloud mode /load-folder downloads files from remote storage which can
  // take ~10 seconds. Without feedback the user may click "Upload"
  // and see a spurious "Select a folder or files first." error.
  const folderErr = $('folder-error');
  const btnLoad   = $('btn-load-folder');
  folderErr.textContent = 'Loading folder…';
  folderErr.classList.remove('hidden');
  folderErr.classList.add('folder-loading');
  if (btnLoad) btnLoad.disabled = true;

  let resp, data;
  try {
    resp = await fetch('/load-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    });
    data = await resp.json();
  } catch (err) {
    folderErr.classList.remove('folder-loading');
    folderErr.textContent = 'Could not open folder — try uploading your files again.';
    // folderErr stays visible as an error
    if (btnLoad) btnLoad.disabled = false;
    return;
  }

  folderErr.classList.remove('folder-loading');
  if (btnLoad) btnLoad.disabled = false;

  if (!resp.ok) {
    folderErr.textContent = data.error || 'Failed to open folder';
    folderErr.classList.remove('hidden');
    return;
  }

  folderErr.classList.add('hidden');
  state.folder = folder;
  state.folderName = displayName || folder.split(/[\\/]/).pop();
  state.files = [];
  state.checked = {};
  state.highlighted = {};

  $('folder-badge').textContent = '📁 ' + state.folderName + '/ ▾';

  if (data.job_id) {
    // Needs transcription
    showScreen('screen-transcribing');
    $('topbar-controls').classList.add('hidden');
    pollTranscription(data.job_id, folder);
  } else {
    await loadTranscripts(folder);
    showWorkspace();
  }
}

// ─── Transcription polling ────────────────────────────────────────────────────
function pollTranscription(jobId, folder) {
  let lastLogLen = 0;

  const interval = setInterval(async () => {
    const resp = await fetch(`/status/${jobId}`);
    const job = await resp.json();

    const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
    $('transcribe-bar').style.width = pct + '%';
    $('transcribe-subtitle').textContent = `Transcribing ${job.done} / ${job.total} videos...`;

    const newLines = job.log.slice(lastLogLen);
    newLines.forEach(msg => appendLog('transcribe-log', msg));
    lastLogLen = job.log.length;

    if (job.status === 'done') {
      clearInterval(interval);
      await loadTranscripts(folder);
      showWorkspace();
    } else if (job.status === 'error' || job.status === 'cancelled') {
      clearInterval(interval);
      appendLog('transcribe-log', `✗ ${job.error || 'Cancelled'}`);
    }
  }, 2000);
}

async function loadTranscripts(folder) {
  const [transcResp, libResp] = await Promise.all([
    fetch(`/transcripts?folder=${encodeURIComponent(folder)}`),
    fetch(GENERATOR_URL + '/library').catch(() => null),
  ]);
  const data = await transcResp.json();
  state.files = data.files;
  if (libResp && libResp.ok) state.libraryEntries = await libResp.json();
  state.files.forEach(f => {
    if (!state.checked[f.name]) state.checked[f.name] = new Set();
    if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
  });

  // Restore persisted selections for this folder
  try {
    const key = 'sizzle_sel_' + state.folder;
    const raw = localStorage.getItem(key);
    if (raw) {
      const saved = JSON.parse(raw);
      const fileNames = new Set(state.files.map(f => f.name));
      for (const [filename, arr] of Object.entries(saved.checked || {})) {
        if (fileNames.has(filename)) state.checked[filename] = new Set(arr);
      }
      for (const [filename, arr] of Object.entries(saved.highlighted || {})) {
        if (fileNames.has(filename)) state.highlighted[filename] = new Set(arr);
      }
    }
  } catch (_) {
    // Malformed or unavailable localStorage — silently ignore
  }
}

function showWorkspace() {
  const base = state.folderName || 'sizzle_reel';
  const takenStems = new Set([
    ...state.files.map(f => f.name.replace(/\.[^.]+$/, '').toLowerCase()),
    ...state.libraryEntries.map(e => (e.filename || '').replace(/\.[^.]+$/, '').toLowerCase()),
  ]);
  $('output-filename').value = takenStems.has(base.toLowerCase()) ? base + '1.mp4' : base + '.mp4';

  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
  renderSidebar();
  if (state.files.length > 0) selectFile(state.files[0].name);
  updateGenerateBtn();
}

// ─── Log helper ───────────────────────────────────────────────────────────────
function appendLog(boxId, msg) {
  const box = $(boxId);
  const div = document.createElement('div');
  if (msg.startsWith('✓')) div.className = 'log-done';
  else if (msg.startsWith('⟳')) div.className = 'log-active';
  else if (msg.startsWith('✗')) div.className = 'log-error';
  else div.className = 'log-info';
  div.textContent = msg;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// ─── Mode toggle ──────────────────────────────────────────────────────────────
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.mode = btn.dataset.mode;
    if (state.activeFile) renderTranscript(state.activeFile);
    updateSelectAllBtn();
    updateClearAllBtn();
    updateGenerateBtn();
  });
});

// ─── Analyze bar ──────────────────────────────────────────────────────────────
$('btn-analyze').addEventListener('click', runAnalyze);
$('analyze-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') runAnalyze();
});

$('btn-analyze-add').addEventListener('click', runAddAnalyze);
$('analyze-add-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) runAddAnalyze();
});

async function runAnalyze() {
  const prompt = $('analyze-input').value.trim();
  if (!prompt) return;

  fetch('/prompt-history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'use', text: prompt }),
  });

  $('btn-analyze').textContent = 'Analyzing…';
  $('btn-analyze').disabled = true;
  $('analyze-input').disabled = true;
  $('analyze-error').classList.add('hidden');

  try {
    const resp = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: state.folder, prompt }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      $('analyze-error').textContent = data.error || 'Analyze failed';
      $('analyze-error').classList.remove('hidden');
      return;
    }

    state.lastPrompt = prompt;

    // Apply returned highlights to BOTH sets so mode-switching preserves analysis
    state.files.forEach(f => {
      const lines = data.highlights[f.name] || [];
      state.checked[f.name] = new Set(lines);
      state.highlighted[f.name] = new Set(lines);
    });

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();
    $('analyze-add-row').classList.remove('hidden');

  } catch (err) {
    $('analyze-error').textContent = 'Network error: ' + err.message;
    $('analyze-error').classList.remove('hidden');
  } finally {
    $('btn-analyze').textContent = 'Analyze';
    $('btn-analyze').disabled = false;
    $('analyze-input').disabled = false;
  }
}

async function runAddAnalyze() {
  const prompt = $('analyze-add-input').value.trim();
  if (!prompt) return;

  $('btn-analyze-add').textContent = 'Analyzing…';
  $('btn-analyze-add').disabled = true;
  $('analyze-add-input').disabled = true;
  $('analyze-error').classList.add('hidden');

  try {
    const resp = await fetch('/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: state.folder, prompt }),
    });
    const data = await resp.json();

    if (!resp.ok) {
      $('analyze-error').textContent = data.error || 'Analyze failed';
      $('analyze-error').classList.remove('hidden');
      return;
    }

    // Union — add new lines without removing existing selections
    state.files.forEach(f => {
      const lines = data.highlights[f.name] || [];
      if (!state.checked[f.name])     state.checked[f.name]     = new Set();
      if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
      lines.forEach(l => state.checked[f.name].add(l));
      lines.forEach(l => state.highlighted[f.name].add(l));
    });

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();

  } catch (err) {
    $('analyze-error').textContent = 'Network error: ' + err.message;
    $('analyze-error').classList.remove('hidden');
  } finally {
    $('btn-analyze-add').textContent = '+ Add to selection';
    $('btn-analyze-add').disabled = false;
    $('analyze-add-input').disabled = false;
  }
}

function updateGenerateBtn() {
  const hasAny = state.files.some(f => {
    const s = state.mode === 'checkbox'
      ? state.checked[f.name]
      : state.highlighted[f.name];
    return s && s.size > 0;
  });
  $('btn-generate').disabled = !hasAny;
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────
function renderSidebar() {
  const list = $('sidebar-list');
  list.innerHTML = '';
  state.files.forEach(f => {
    const li = document.createElement('li');
    li.className = 'sidebar-item' + (f.name === state.activeFile ? ' active' : '');
    li.dataset.name = f.name;

    const nameDiv = document.createElement('div');
    nameDiv.className = 'item-name';
    nameDiv.textContent = f.name;

    const badgeDiv = document.createElement('div');
    badgeDiv.className = 'item-badge';
    badgeDiv.id = `badge-${CSS.escape(f.name)}`;
    updateBadgeEl(badgeDiv, f.name);

    li.appendChild(nameDiv);
    li.appendChild(badgeDiv);
    li.addEventListener('click', () => selectFile(f.name));
    list.appendChild(li);
  });
}

function updateBadgeEl(el, filename) {
  const cb = state.checked[filename]?.size || 0;
  const hl = state.highlighted[filename]?.size || 0;
  if (state.mode === 'checkbox') {
    el.innerHTML = cb > 0 ? `<span class="badge-checked">${cb} checked</span>` : '0 checked';
  } else {
    el.innerHTML = hl > 0 ? `<span class="badge-highlighted">${hl} highlighted</span>` : 'none highlighted';
  }
}

function refreshBadge(filename) {
  const el = document.getElementById(`badge-${CSS.escape(filename)}`);
  if (el) updateBadgeEl(el, filename);
}

function selectFile(filename) {
  state.activeFile = filename;
  $('transcript-filename').textContent = filename.replace(/\.[^.]+$/, '.txt');
  document.querySelectorAll('.sidebar-item').forEach(li => {
    li.classList.toggle('active', li.dataset.name === filename);
  });
  renderTranscript(filename);
  updateSelectAllBtn();
  updateClearAllBtn();
}

function updateSelectAllBtn() {
  const btn = $('btn-select-all');
  if (state.mode === 'checkbox') {
    btn.textContent = 'check all';
    btn.className = 'select-all-btn checkbox-mode';
    btn.onclick = () => checkAllInFile(state.activeFile);
  } else {
    btn.textContent = 'highlight all';
    btn.className = 'select-all-btn highlight-mode';
    btn.onclick = () => highlightAllInFile(state.activeFile);
  }
}

function updateClearAllBtn() {
  const btn = $('btn-clear-all');
  if (!btn) return;  // guard: button may not exist in all layouts
  if (state.mode === 'checkbox') {
    btn.textContent = 'uncheck all';
    btn.onclick = () => uncheckAllInFile(state.activeFile);
  } else {
    btn.textContent = 'clear all';
    btn.onclick = () => clearHighlightsInFile(state.activeFile);
  }
}

// ─── Checkbox mode ────────────────────────────────────────────────────────────
function _updateHeaderCbState(cbEl, lines, s) {
  const count = lines.filter(l => s.has(l.raw)).length;
  const all = count === lines.length;
  const some = count > 0 && !all;
  cbEl.className = 'cb-box' + (all ? ' checked' : some ? ' indeterminate' : '');
  cbEl.textContent = all ? '✓' : some ? '–' : '';
}

function renderCheckboxMode(fileObj) {
  const scroll = $('transcript-scroll');
  scroll.innerHTML = '';
  if (!fileObj || fileObj.lines.length === 0) {
    scroll.textContent = 'No transcript available.';
    return;
  }

  // Group by minute
  const groups = {};
  fileObj.lines.forEach(line => {
    const b = line.minute_bucket;
    if (!groups[b]) groups[b] = { label: `${b}:00 – ${b + 1}:00`, lines: [] };
    groups[b].lines.push(line);
  });

  const s = state.checked[fileObj.name];

  Object.values(groups).forEach(group => {
    const groupEl = document.createElement('div');
    groupEl.className = 'minute-group';

    // ── Minute header with select-all checkbox ─────────────────────────────
    const labelEl = document.createElement('div');
    labelEl.className = 'minute-label-cb';

    const headerCb = document.createElement('div');
    _updateHeaderCbState(headerCb, group.lines, s);

    const labelText = document.createElement('span');
    labelText.textContent = group.label;

    labelEl.appendChild(headerCb);
    labelEl.appendChild(labelText);

    labelEl.addEventListener('click', () => {
      const allChecked = group.lines.every(l => s.has(l.raw));
      if (allChecked) {
        group.lines.forEach(l => s.delete(l.raw));
      } else {
        group.lines.forEach(l => s.add(l.raw));
      }
      // Mutate DOM in place — no re-render
      group.lines.forEach(l => {
        const lineEl = groupEl.querySelector(`[data-line-raw="${CSS.escape(l.raw)}"]`);
        if (lineEl) {
          const cb = lineEl.querySelector('.cb-box-line');
          const checked = s.has(l.raw);
          cb.className = 'cb-box cb-box-line' + (checked ? ' checked' : '');
          cb.textContent = checked ? '✓' : '';
        }
      });
      _updateHeaderCbState(headerCb, group.lines, s);
      refreshBadge(fileObj.name);
      updateGenerateBtn();
      _saveSelections();
    });

    groupEl.appendChild(labelEl);

    // ── Individual lines with per-line checkboxes ──────────────────────────
    group.lines.forEach(line => {
      const lineEl = document.createElement('div');
      lineEl.className = 'transcript-line-cb';
      lineEl.dataset.lineRaw = line.raw;

      const lineCb = document.createElement('div');
      lineCb.className = 'cb-box cb-box-line' + (s.has(line.raw) ? ' checked' : '');
      lineCb.textContent = s.has(line.raw) ? '✓' : '';

      const ts = document.createElement('div');
      ts.className = 'ts-cb';
      ts.textContent = line.timestamp;

      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;

      lineEl.appendChild(lineCb);
      lineEl.appendChild(ts);
      lineEl.appendChild(text);

      lineEl.addEventListener('click', () => {
        const checked = s.has(line.raw);
        if (checked) {
          s.delete(line.raw);
          lineCb.className = 'cb-box cb-box-line';
          lineCb.textContent = '';
        } else {
          s.add(line.raw);
          lineCb.className = 'cb-box cb-box-line checked';
          lineCb.textContent = '✓';
        }
        _updateHeaderCbState(headerCb, group.lines, s);
        refreshBadge(fileObj.name);
        updateGenerateBtn();
        _saveSelections();
      });

      groupEl.appendChild(lineEl);
    });

    scroll.appendChild(groupEl);
  });
}

function checkAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.checked[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();
}

function uncheckAllInFile(filename) {
  if (!state.checked[filename]) return;
  state.checked[filename] = new Set();
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();
}

function renderTranscript(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (state.mode === 'checkbox') renderCheckboxMode(fileObj);
  else renderHighlightMode(fileObj);
}

// ─── Highlight mode ───────────────────────────────────────────────────────────
let _dragActive = false;
let _dragSetTo = null;   // true = highlighting, false = un-highlighting
let _hlAbortController = null;  // cancels stale mousedown/mousemove listeners
document.addEventListener('mouseup', () => {
  if (_dragActive) _saveSelections();
  _dragActive = false;
});

function renderHighlightMode(fileObj) {
  const scroll = $('transcript-scroll');

  // Abort previous listeners before re-rendering
  if (_hlAbortController) _hlAbortController.abort();
  _hlAbortController = new AbortController();
  const { signal } = _hlAbortController;

  scroll.innerHTML = '';
  if (!fileObj || fileObj.lines.length === 0) {
    scroll.textContent = 'No transcript available.';
    return;
  }

  fileObj.lines.forEach(line => {
    const lineEl = document.createElement('div');
    lineEl.className = 'transcript-line-hl' +
      (state.highlighted[fileObj.name].has(line.raw) ? ' highlighted' : '');
    lineEl.dataset.raw = line.raw;

    const bar = document.createElement('div');
    bar.className = 'hl-bar';

    const ts = document.createElement('div');
    ts.className = 'ts-hl';
    ts.textContent = line.timestamp;

    const text = document.createElement('div');
    text.className = 'line-text-hl';
    text.textContent = line.text;

    lineEl.appendChild(bar);
    lineEl.appendChild(ts);
    lineEl.appendChild(text);
    scroll.appendChild(lineEl);
  });

  // ── Drag-to-brush ──────────────────────────────────────────────────────────
  scroll.addEventListener('mousedown', e => {
    const lineEl = e.target.closest('.transcript-line-hl');
    if (!lineEl) return;
    // No e.preventDefault() — user-select:none CSS handles text selection,
    // and preventDefault() on mousedown cancels pointer-based scroll tracking
    // on touchpad/touch devices.
    _dragActive = true;
    const raw = lineEl.dataset.raw;
    const hl = state.highlighted[fileObj.name];
    // Determine whether this drag is a highlight or un-highlight pass
    _dragSetTo = !hl.has(raw);
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
    updateGenerateBtn();
  }, { signal });

  // mousemove on document (not scroll) so auto-scroll fires even when the
  // mouse leaves the scroll container during a drag.
  document.addEventListener('mousemove', e => {
    if (!_dragActive) return;

    // Auto-scroll: check edge proximity FIRST, before checking what element
    // the mouse is over — during a drag the mouse is always over a line, so
    // checking lineEl first meant auto-scroll never fired.
    const rect = scroll.getBoundingClientRect();
    const threshold = 60;
    if (e.clientY < rect.top + threshold) {
      scroll.scrollTop -= 12;
    } else if (e.clientY > rect.bottom - threshold) {
      scroll.scrollTop += 12;
    }

    // Apply highlight to whichever line is under the cursor
    const lineEl = e.target.closest('.transcript-line-hl');
    if (!lineEl) return;
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
    updateGenerateBtn();
  }, { signal });

}

function _applyHighlight(filename, lineEl, setTo) {
  const raw = lineEl.dataset.raw;
  const hl = state.highlighted[filename];
  if (setTo) {
    hl.add(raw);
    lineEl.classList.add('highlighted');
  } else {
    hl.delete(raw);
    lineEl.classList.remove('highlighted');
  }
}

function highlightAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.highlighted[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();
}

function clearHighlightsInFile(filename) {
  if (!state.highlighted[filename]) return;
  state.highlighted[filename] = new Set();
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  _saveSelections();
}

// ─── Recent folders ───────────────────────────────────────────────────────────
function relativeTime(iso) {
  const diffDays = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (diffDays === 0) return 'today';
  if (diffDays === 1) return 'yesterday';
  if (diffDays < 7) return `${diffDays} days ago`;
  const weeks = Math.floor(diffDays / 7);
  return weeks === 1 ? '1 week ago' : `${weeks} weeks ago`;
}

function renderRecentFolders(entries) {
  const section = $('recent-folders-section');
  const list = $('recent-folders-list');
  if (!entries || entries.length === 0) {
    section.classList.add('hidden');
    return;
  }
  list.innerHTML = '';
  entries.forEach(entry => {
    const li = document.createElement('li');
    li.className = 'recent-folder-item';
    const name = entry.path.replace(/\\/g, '/').split('/').filter(Boolean).pop() || entry.path;
    const count = entry.video_count;
    const nameSpan = document.createElement('span');
    nameSpan.className = 'recent-folder-name';
    nameSpan.textContent = `📁 ${name}/`;
    const metaSpan = document.createElement('span');
    metaSpan.className = 'recent-folder-meta';
    metaSpan.textContent = `${count} video${count !== 1 ? 's' : ''} · ${relativeTime(entry.last_opened)}`;
    li.appendChild(nameSpan);
    li.appendChild(metaSpan);
    li.addEventListener('click', () => {
      $('folder-path-input').value = entry.path;
      openFolder(entry.path);
    });
    list.appendChild(li);
  });
  section.classList.remove('hidden');
}

async function loadRecentFolders() {
  if (APP_MODE === 'cloud') return; // cloud mode uses localStorage-based recent sessions
  try {
    const resp = await fetch('/recent-folders');
    if (resp.ok) renderRecentFolders(await resp.json());
  } catch (_) {
    // recent folders is a convenience feature — fail silently
  }
}

// ─── Generate ─────────────────────────────────────────────────────────────────
$('btn-generate').addEventListener('click', () => {
  const mode = state.mode;
  const selections = {};
  state.files.forEach(f => {
    const lines = mode === 'checkbox'
      ? [...(state.checked[f.name] || [])]
      : [...(state.highlighted[f.name] || [])];
    if (lines.length > 0) selections[f.name] = lines;
  });
  submitGenerate(mode, selections);
});

async function submitGenerate(mode, selections) {
  const prompt = state.lastPrompt || $('analyze-input').value.trim();
  const outputFilename = $('output-filename').value.trim() || 'sizzle_reel.mp4';

  showScreen('screen-generating');
  $('gen-log').innerHTML = '';
  $('gen-bar').style.width = '0%';
  $('topbar-controls').classList.add('hidden');

  const resp = await fetch(GENERATOR_URL + '/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      folder: state.folder,
      session_key: state.folder,   // cloud mode: folder === session key
      mode,
      selections,
      prompt,
      output_filename: outputFilename,
    }),
  });
  const { job_id, error } = await resp.json();
  if (!resp.ok) {
    appendLog('gen-log', `✗ ${error || 'Failed to start generation'}`);
    return;
  }

  state.currentJobId = job_id;
  watchGeneration(job_id);
}

function watchGeneration(jobId) {
  const wsUrl = GENERATOR_URL.replace(/^http/, 'ws') + `/ws/job/${jobId}`;
  _genWs = new WebSocket(wsUrl);

  _genWs.onmessage = (e) => {
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return; // ignore malformed frames
    }
    if (msg.type === 'log') {
      appendLog('gen-log', msg.message);
    } else if (msg.type === 'progress') {
      const pct = msg.total > 0 ? Math.round((msg.done / msg.total) * 100) : 0;
      $('gen-bar').style.width = Math.max(pct, 5) + '%';
    } else if (msg.type === 'done') {
      _genWs = null;
      if (msg.status === 'done') {
        $('gen-bar').style.width = '100%';
        state.resultJobId = jobId;
        _clearSelections();
        showResult(msg.result);
          if (APP_MODE === 'cloud' && msg.result.entry_id) {
            const openBtn = $('btn-open-folder');
            openBtn.textContent = '⬇ Saving…';
            openBtn.disabled = true;
            _autoSaveReelResult(jobId, msg.result.filename, msg.result.entry_id)
              .then(saved => {
                openBtn.disabled = false;
                if (saved) {
                  openBtn.textContent = `✓ Saved to ${saved.folderName}`;
                  openBtn.dataset.savedPath = saved.localFolderPath || '';
                  openBtn.dataset.savedFilename = msg.result.filename;
                } else {
                  openBtn.textContent = '⬇ Download';
                }
              })
              .catch(() => {
                openBtn.disabled = false;
                openBtn.textContent = '⬇ Download';
              });
          }
      } else if (msg.status === 'error') {
        appendLog('gen-log', `✗ Error: ${msg.error}`);
        $('topbar-controls').classList.remove('hidden');
      } else if (msg.status === 'cancelled') {
        showScreen('screen-workspace');
        $('topbar-controls').classList.remove('hidden');
      }
    }
  };

  _genWs.onerror = () => {
    _genWs = null;
    appendLog('gen-log', '✗ Connection error — generation may still be running');
    $('topbar-controls').classList.remove('hidden');
  };

  _genWs.onclose = () => {
    if (_genWs !== null) {
      _genWs = null;
      appendLog('gen-log', '✗ Connection closed unexpectedly — generation may still be running');
      $('topbar-controls').classList.remove('hidden');
    }
  };

  $('btn-cancel-gen').onclick = async () => {
    await fetch(`${GENERATOR_URL}/jobs/${jobId}`, { method: 'DELETE' });
    if (_genWs) {
      _genWs.close();
      _genWs = null;
    }
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  };
}

function showResult(result) {
  showScreen('screen-result');
  $('topbar-controls').classList.remove('hidden');

  state.resultSegmentStarts = result.segment_starts || [];
  state.resultDownloadUrl = result.download_url || null;
  state.resultPath = result.path || null;

  // Always serve through the generator endpoint — it serves directly from the
  // local temp file (kept alive until container restart) so playback works
  // even when the R2 upload failed or is slow.
  const src = `${GENERATOR_URL}/video/${state.resultJobId}`;
  $('result-source').src = src;
  $('result-video').load();

  $('result-filename').textContent = result.filename;
  const mins = Math.floor(result.duration_seconds / 60);
  const secs = result.duration_seconds % 60;
  const savedLabel = APP_MODE === 'cloud' ? 'uploaded to cloud' : 'saved to folder';
  $('result-info').textContent =
    `${mins}:${String(secs).padStart(2,'0')} · ${result.clip_count} clips · ${savedLabel}`;

  // In cloud mode the "Open Folder" button becomes a download link (there is no
  // local folder to open).  If the R2 upload failed, hide the button entirely.
  const openBtn = $('btn-open-folder');
  if (APP_MODE === 'cloud') {
    if (state.resultDownloadUrl) {
      openBtn.textContent = '⬇ Download';
      openBtn.style.display = '';
    } else {
      openBtn.style.display = 'none';
    }
  } else {
    openBtn.textContent = '📂 Open Folder';
    openBtn.style.display = '';
  }
}

// ─── Segment skip ─────────────────────────────────────────────────────────────
function skipToSegment(video, segmentStarts, direction) {
  const t = video.currentTime;
  if (direction === 'next') {
    const target = segmentStarts.find(s => s > t + 0.5);
    if (target !== undefined) video.currentTime = target;
  } else {
    // Find the segment currently playing (latest start we've passed by ≥ 0.5s)
    // then seek to the one before it, so Prev always navigates to the previous segment.
    const currentIdx = segmentStarts.reduce((acc, s, i) => s <= t - 0.5 ? i : acc, -1);
    if (currentIdx > 0) {
      video.currentTime = segmentStarts[currentIdx - 1];
    } else if (segmentStarts.length > 0) {
      video.currentTime = segmentStarts[0];
    }
  }
}

$('btn-prev-seg').addEventListener('click', () => {
  skipToSegment($('result-video'), state.resultSegmentStarts, 'prev');
});
$('btn-next-seg').addEventListener('click', () => {
  skipToSegment($('result-video'), state.resultSegmentStarts, 'next');
});

$('btn-lib-prev-seg').addEventListener('click', () => {
  skipToSegment($('library-video'), state.librarySegmentStarts, 'prev');
});
$('btn-lib-next-seg').addEventListener('click', () => {
  skipToSegment($('library-video'), state.librarySegmentStarts, 'next');
});

$('btn-new-reel').addEventListener('click', () => {
  $('result-video').pause();
  $('result-source').src = '';
  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
});

$('btn-open-folder').addEventListener('click', async () => {
  if (APP_MODE === 'cloud') {
    const localPath = $('btn-open-folder').dataset.savedPath;
    const filename = $('btn-open-folder').dataset.savedFilename;
    if (localPath && filename) {
      await fetch(GENERATOR_URL + '/open-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder: localPath, file_path: localPath + '\\' + filename }),
      });
      return;
    }
    // No OS path — try reading from dir handle as blob URL
    if (filename) {
      const handle = await _idbLoad('sizzle_output_dir_handle').catch(() => null);
      if (handle) {
        try {
          const fh = await handle.getFileHandle(filename);
          window.open(URL.createObjectURL(await fh.getFile()), '_blank');
          return;
        } catch { /* fall through */ }
      }
    }
    // Final fallback: presigned URL
    if (state.resultDownloadUrl) window.open(state.resultDownloadUrl, '_blank');
    return;
  }
  // Local mode (unchanged)
  await fetch(GENERATOR_URL + '/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder, file_path: state.resultPath }),
  });
});

// ─── Library ──────────────────────────────────────────────────────────────────
async function fetchLibrary() {
  const resp = await fetch(GENERATOR_URL + '/library');
  state.libraryEntries = await resp.json();
  state.librarySort = $('library-sort').value;
  renderLibrary();
}

function renderLibrary() {
  const entries = [...state.libraryEntries];
  const sort = state.librarySort;
  if (sort === 'newest') {
    entries.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  } else if (sort === 'oldest') {
    entries.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  } else if (sort === 'most-clips') {
    entries.sort((a, b) => (b.clip_count || 0) - (a.clip_count || 0));
  } else if (sort === 'fewest-clips') {
    entries.sort((a, b) => (a.clip_count || 0) - (b.clip_count || 0));
  }

  const grid = $('library-grid');
  grid.innerHTML = '';
  $('library-count').textContent = `Generated Reels (${entries.length})`;

  if (entries.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'library-empty';
    empty.textContent = 'No reels generated yet.';
    grid.appendChild(empty);
    return;
  }

  entries.forEach(entry => {
    const card = document.createElement('div');
    card.className = 'reel-card';
    card.dataset.id = entry.id;

    const mins = Math.floor((entry.duration_seconds || 0) / 60);
    const secs = (entry.duration_seconds || 0) % 60;
    const durStr = `${mins}:${String(secs).padStart(2, '0')}`;
    const dateStr = entry.created_at ? entry.created_at.split('T')[0] : '';

    const thumb = document.createElement('div');
    thumb.className = 'reel-thumb';
    thumb.dataset.id = entry.id;
    thumb.innerHTML = `<div class="reel-play-icon">▶</div><div class="reel-duration">${durStr}</div>`;
    thumb.addEventListener('click', () => openLibraryPlayer(entry));

    const body = document.createElement('div');
    body.className = 'reel-body';
    _renderCardBody(body, card, entry, dateStr);

    card.appendChild(thumb);
    card.appendChild(body);
    grid.appendChild(card);
  });
}

function escAttr(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _renderCardBody(body, card, entry, dateStr) {
  body.innerHTML = '';

  const displayName = entry.title || entry.filename;

  // Name row (name + edit + delete icons)
  const nameRow = document.createElement('div');
  nameRow.style.cssText = 'display:flex;align-items:flex-start;justify-content:space-between;gap:4px';

  const nameEl = document.createElement('div');
  nameEl.className = 'reel-name';
  nameEl.style.cssText = 'min-width:0;flex:1';
  nameEl.title = entry.filename;
  nameEl.textContent = displayName;

  const iconRow = document.createElement('div');
  iconRow.style.cssText = 'display:flex;gap:2px;flex-shrink:0';

  const editBtn = document.createElement('button');
  editBtn.className = 'reel-btn-icon';
  editBtn.title = 'Edit';
  editBtn.textContent = '✏️';

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'reel-btn-icon';
  deleteBtn.title = 'Delete';
  deleteBtn.textContent = '🗑';

  iconRow.appendChild(editBtn);
  iconRow.appendChild(deleteBtn);
  nameRow.appendChild(nameEl);
  nameRow.appendChild(iconRow);
  body.appendChild(nameRow);

  // Meta
  const meta = document.createElement('div');
  meta.className = 'reel-meta';
  meta.textContent = `${dateStr} · ${entry.clip_count || 0} clips · ${entry.source_folder || ''}`;
  body.appendChild(meta);

  // Prompt
  const prompt = document.createElement('div');
  prompt.className = 'reel-prompt';
  prompt.title = entry.prompt || '';
  prompt.textContent = `"${entry.prompt || ''}"`;
  body.appendChild(prompt);

  // Notes (shown only if present)
  if (entry.notes) {
    const notes = document.createElement('div');
    notes.className = 'reel-notes';
    notes.textContent = entry.notes;
    body.appendChild(notes);
  }

  // Action buttons
  const actions = document.createElement('div');
  actions.className = 'reel-actions';

  const playBtn = document.createElement('button');
  playBtn.className = 'reel-btn play';
  playBtn.dataset.id = entry.id;
  playBtn.textContent = '▶ Play';

  const showBtn = document.createElement('button');
  showBtn.className = 'reel-btn show';
  showBtn.dataset.id = entry.id;
  showBtn.dataset.path = entry.path || '';
  // In cloud mode there is no local folder to reveal; the button becomes a
  // download link that opens the reel via the generator proxy endpoint.
  showBtn.textContent = APP_MODE === 'cloud' ? '⬇ Download' : '📂 Show';

  actions.appendChild(playBtn);
  actions.appendChild(showBtn);
  body.appendChild(actions);

  // Event listeners
  playBtn.addEventListener('click', () => openLibraryPlayer(entry));

  showBtn.addEventListener('click', async () => {
    if (APP_MODE === 'cloud') {
      // Open the video through the generator proxy (handles CORS + S3 fallback).
      window.open(`${GENERATOR_URL}/library-video/${entry.id}`, '_blank');
      return;
    }
    // Local mode: open the containing folder with the file highlighted.
    const folder = (entry.path || '').replace(/[\\/][^\\/]+$/, '');
    await fetch(GENERATOR_URL + '/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, file_path: entry.path }),
    });
  });

  deleteBtn.addEventListener('click', () => _showDeleteConfirm(body, card, entry, dateStr, actions));

  editBtn.addEventListener('click', () => _showEditForm(body, card, entry, dateStr));
}

function _showDeleteConfirm(body, card, entry, dateStr, actions) {
  actions.innerHTML = '';

  const label = document.createElement('span');
  label.className = 'reel-delete-confirm-label';
  label.textContent = 'Remove?';

  const libOnly = document.createElement('button');
  libOnly.className = 'reel-btn confirm-lib';
  libOnly.textContent = 'Library only';

  const withFile = document.createElement('button');
  withFile.className = 'reel-btn confirm-file';
  withFile.textContent = 'Also delete file';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'reel-btn cancel-del';
  cancelBtn.textContent = 'Cancel';

  actions.appendChild(label);
  actions.appendChild(libOnly);
  actions.appendChild(withFile);
  actions.appendChild(cancelBtn);

  async function doDelete(deleteFile) {
    libOnly.disabled = true;
    withFile.disabled = true;
    const url = `${GENERATOR_URL}/library/${entry.id}` + (deleteFile ? '?delete_file=true' : '');
    await fetch(url, { method: 'DELETE' });
    card.classList.add('fading');
    setTimeout(() => {
      state.libraryEntries = state.libraryEntries.filter(e => e.id !== entry.id);
      renderLibrary();
    }, 300);
  }

  libOnly.addEventListener('click', () => doDelete(false));
  withFile.addEventListener('click', () => doDelete(true));
  cancelBtn.addEventListener('click', () => renderLibrary());
}

function _showEditForm(body, card, entry, dateStr) {
  body.innerHTML = '';

  const form = document.createElement('div');
  form.className = 'reel-edit-form';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'reel-edit-name';
  nameInput.value = entry.title || entry.filename;
  nameInput.placeholder = 'Display name';

  const notesInput = document.createElement('textarea');
  notesInput.className = 'reel-edit-notes';
  notesInput.value = entry.notes || '';
  notesInput.placeholder = 'Notes…';

  const btns = document.createElement('div');
  btns.className = 'reel-edit-btns';

  const saveBtn = document.createElement('button');
  saveBtn.className = 'reel-btn';
  saveBtn.textContent = 'Save';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'reel-btn';
  cancelBtn.textContent = 'Cancel';

  btns.appendChild(saveBtn);
  btns.appendChild(cancelBtn);
  form.appendChild(nameInput);
  form.appendChild(notesInput);
  form.appendChild(btns);
  body.appendChild(form);

  nameInput.focus();
  nameInput.select();

  saveBtn.addEventListener('click', async () => {
    const newTitle = nameInput.value.trim() || entry.filename;
    const newNotes = notesInput.value;
    const resp = await fetch(`${GENERATOR_URL}/library/${entry.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: newTitle, notes: newNotes }),
    });
    if (resp.ok) {
      fetchLibrary();
    }
  });

  cancelBtn.addEventListener('click', () => renderLibrary());

  nameInput.addEventListener('keydown', e => {
    if (e.key === 'Escape') renderLibrary();
    if (e.key === 'Enter') saveBtn.click();
  });

  notesInput.addEventListener('keydown', e => {
    if (e.key === 'Escape') renderLibrary();
  });
}

function openLibraryPlayer(entry) {
  state.librarySegmentStarts = entry.segment_starts || [];
  // Always route through the generator's /library-video endpoint so the
  // response passes through Flask (flask-cors adds CORS headers).  Direct
  // presigned R2 URLs are blocked by Chrome's ORB since they don't carry CORS
  // headers — routing through Flask sidesteps this entirely.
  const src = `${GENERATOR_URL}/library-video/${entry.id}`;
  const displayName = entry.title || entry.filename;
  $('library-player-meta').textContent =
    `${displayName} — "${entry.prompt}"`;
  // Show the overlay before calling load() — browsers defer loading media in
  // display:none elements, so the video must be visible before we trigger load.
  $('library-player-overlay').classList.remove('hidden');
  $('library-video').src = src;
  $('library-video').load();
}

$('btn-close-player').addEventListener('click', () => {
  $('library-video').pause();
  $('library-video').src = '';
  $('library-player-overlay').classList.add('hidden');
});

// Load recent folders on startup
loadRecentFolders();

// ─── Folder badge dropdown ────────────────────────────────────────────────────
let _folderDropdown = null;
let _folderDropdownOnOutside = null;
let _folderDropdownOnEscape = null;

function _closeFolderDropdown() {
  if (_folderDropdown) {
    _folderDropdown.remove();
    _folderDropdown = null;
  }
  if (_folderDropdownOnOutside) {
    document.removeEventListener('mousedown', _folderDropdownOnOutside);
    _folderDropdownOnOutside = null;
  }
  if (_folderDropdownOnEscape) {
    document.removeEventListener('keydown', _folderDropdownOnEscape);
    _folderDropdownOnEscape = null;
  }
}

$('folder-badge').addEventListener('click', async (e) => {
  e.stopPropagation();
  if (_folderDropdown) {
    _closeFolderDropdown();
    return;
  }

  const rect = $('folder-badge').getBoundingClientRect();
  const dropdown = document.createElement('div');
  dropdown.className = 'folder-dropdown';
  dropdown.style.top = (rect.bottom + 4) + 'px';
  dropdown.style.left = rect.left + 'px';
  _folderDropdown = dropdown;

  // Fetch recent folders (cloud: localStorage; local: server endpoint)
  if (APP_MODE === 'cloud') {
    const sessions = JSON.parse(localStorage.getItem('sizzleRecentSessions') || '[]');
    sessions.forEach(s => {
      const btn = document.createElement('button');
      btn.textContent = '📁 ' + s.name + '/';
      btn.title = s.name;
      btn.addEventListener('click', async () => {
        _closeFolderDropdown();
        await openFolder(s.folder, s.name);
      });
      dropdown.appendChild(btn);
    });

    const newBtn = document.createElement('button');
    newBtn.className = 'dropdown-new-folder';
    newBtn.textContent = '📂 Upload new files...';
    newBtn.addEventListener('click', () => {
      _closeFolderDropdown();
      showScreen('screen-folder-picker');
    });
    dropdown.appendChild(newBtn);
  } else {
    let recents = [];
    try {
      const resp = await fetch('/recent-folders');
      recents = await resp.json();
    } catch (_) {}

    recents.forEach(entry => {
      const btn = document.createElement('button');
      btn.textContent = '📁 ' + entry.path.split(/[\\/]/).pop() + '/';
      btn.title = entry.path;
      btn.addEventListener('click', () => {
        _closeFolderDropdown();
        openFolder(entry.path);
      });
      dropdown.appendChild(btn);
    });

    const newBtn = document.createElement('button');
    newBtn.className = 'dropdown-new-folder';
    newBtn.textContent = '📂 Select new folder...';
    newBtn.addEventListener('click', async () => {
      _closeFolderDropdown();
      const resp = await fetch('/browse', { method: 'POST' });
      const { path } = await resp.json();
      if (path) openFolder(path);
    });
    dropdown.appendChild(newBtn);
  }

  document.body.appendChild(dropdown);

  // Dismiss on outside click or Escape — stored on module vars so _closeFolderDropdown
  // can remove them even when closed via an internal button click.
  _folderDropdownOnOutside = (ev) => {
    if (!dropdown.contains(ev.target)) _closeFolderDropdown();
  };
  _folderDropdownOnEscape = (ev) => {
    if (ev.key === 'Escape') _closeFolderDropdown();
  };
  setTimeout(() => {
    document.addEventListener('mousedown', _folderDropdownOnOutside);
    document.addEventListener('keydown', _folderDropdownOnEscape);
  }, 0);
});

// ─── Cloud mode: repurpose local folder picker UI for upload ─────────────────
// In cloud mode the existing Browse/Open Folder/Recent Folders UI is reused:
//   • Browse…       → opens a webkitdirectory folder picker
//   • path input    → read-only, shows the selected folder name
//   • Open Folder   → uploads selected files then calls openFolder()
//   • Recent        → stored in localStorage (same look as local recent folders)
(function initCloudMode() {
  if (APP_MODE !== 'cloud') return;

  const pathInput  = $('folder-path-input');
  const btnBrowse  = $('btn-browse');
  const btnLoad    = $('btn-load-folder');
  const folderErr  = $('folder-error');
  const uploadErr  = $('upload-error');
  const folderPicker = $('cloud-folder-picker');
  const filePicker   = $('cloud-file-picker');

  const VALID_EXTS = new Set(['.mp4', '.mov', '.avi', '.mkv', '.webm', '.txt']);
  function ext(name) { return name.slice(name.lastIndexOf('.')).toLowerCase(); }

  let selectedFiles = [];
  let selectedFolderName = '';

  // Make path input read-only — it just displays the chosen folder name
  pathInput.readOnly = true;
  pathInput.placeholder = 'Select a folder to upload…';
  pathInput.style.cursor = 'default';

  // Browse button opens the folder picker
  btnBrowse.onclick = e => { e.preventDefault(); folderPicker.click(); };

  // Remove the existing keydown listener for local mode by replacing the element
  // (clone trick — strips all listeners set before this script ran)
  pathInput.addEventListener('keydown', e => e.preventDefault());

  // Folder selected via picker
  folderPicker.addEventListener('change', () => {
    const all = Array.from(folderPicker.files);
    selectedFiles = all.filter(f => VALID_EXTS.has(ext(f.name)));
    if (all.length > 0) {
      selectedFolderName = all[0].webkitRelativePath.split('/')[0] || 'folder';
      pathInput.value = selectedFolderName;
    }
    folderPicker.value = '';   // reset so same folder can be re-picked
  });

  // Individual files via hidden file picker (drag-and-drop also sets selectedFiles)
  filePicker.addEventListener('change', () => {
    selectedFiles = Array.from(filePicker.files).filter(f => VALID_EXTS.has(ext(f.name)));
    const names = selectedFiles.map(f => f.name);
    selectedFolderName = names.length === 1 ? names[0] : `${names.length} files`;
    pathInput.value = selectedFolderName;
    filePicker.value = '';
  });

  // Drag-and-drop onto the path input row
  const inputRow = document.querySelector('.folder-input-row');
  if (inputRow) {
    inputRow.addEventListener('dragover', e => { e.preventDefault(); inputRow.classList.add('drag-over'); });
    inputRow.addEventListener('dragleave', () => inputRow.classList.remove('drag-over'));
    inputRow.addEventListener('drop', e => {
      e.preventDefault();
      inputRow.classList.remove('drag-over');
      const items = Array.from(e.dataTransfer.items || []);
      const files = Array.from(e.dataTransfer.files || []);
      selectedFiles = files.filter(f => VALID_EXTS.has(ext(f.name)));
      if (selectedFiles.length) {
        selectedFolderName = selectedFiles.length === 1
          ? selectedFiles[0].name
          : `${selectedFiles.length} files`;
        pathInput.value = selectedFolderName;
      }
    });
  }

  // Open Folder → upload
  btnLoad.textContent = 'Upload';
  btnLoad.onclick = () => doUpload();

  async function doUpload() {
    if (!selectedFiles.length) {
      folderErr.textContent = 'Select a folder or files first.';
      folderErr.classList.remove('hidden');
      return;
    }
    folderErr.classList.add('hidden');
    uploadErr.classList.add('hidden');
    btnLoad.disabled = true;

    try {
      // Step 1: validate filenames and create a session key on the server
      btnLoad.textContent = 'Preparing upload…';
      const prepResp = await fetch('/upload/prepare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: selectedFiles.map(f => f.name) }),
      });
      const prepData = await prepResp.json();
      if (!prepResp.ok) {
        folderErr.textContent = prepData.error || 'Upload preparation failed';
        folderErr.classList.remove('hidden');
        return;
      }

      const session_key = prepData.session_key;

      // Step 2: upload each file to the server — server proxies bytes to R2.
      // No CORS needed: the browser posts to the same origin (this Flask server).
      $('transcribe-subtitle').textContent = `Uploading ${selectedFiles.length} files…`;
      $('transcribe-bar').style.width = '0%';
      $('transcribe-log').textContent = '';
      showScreen('screen-transcribing');

      for (let i = 0; i < selectedFiles.length; i++) {
        const file = selectedFiles[i];
        $('transcribe-log').textContent = `⟳ ${file.name} (${i + 1} / ${selectedFiles.length})`;
        const fd = new FormData();
        fd.append('file', file);
        fd.append('session_key', session_key);
        const resp = await fetch('/upload/file', { method: 'POST', body: fd });
        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          throw new Error(`Failed to upload ${file.name}: ${errData.error || resp.status}`);
        }
        const pct = Math.round(((i + 1) / selectedFiles.length) * 100);
        $('transcribe-bar').style.width = pct + '%';
        $('transcribe-log').textContent = `✓ ${file.name} (${i + 1} / ${selectedFiles.length})`;
      }

      // Step 3: tell the server all uploads are done
      $('transcribe-subtitle').textContent = 'Finalising…';
      const commitResp = await fetch('/upload/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_key, files: selectedFiles.map(f => f.name) }),
      });
      const commitData = await commitResp.json();
      if (!commitResp.ok) {
        showScreen('screen-folder-picker');
        folderErr.textContent = commitData.error || 'Upload commit failed';
        folderErr.classList.remove('hidden');
        return;
      }

      // Step 4: record in recent sessions and open the folder as before
      _saveRecentSession(
        selectedFolderName,
        selectedFiles.filter(f => ext(f.name) !== '.txt').length,
        commitData.folder
      );
      await openFolder(commitData.folder, selectedFolderName);

    } catch (err) {
      showScreen('screen-folder-picker');
      folderErr.textContent = 'Upload error: ' + err.message;
      folderErr.classList.remove('hidden');
    } finally {
      btnLoad.disabled = false;
      btnLoad.textContent = 'Upload';
    }
  }

  // ── Recent sessions (localStorage) ──────────────────────────────────────────

  function _saveRecentSession(name, videoCount, folder) {
    const key = 'sizzleRecentSessions';
    const sessions = JSON.parse(localStorage.getItem(key) || '[]')
      .filter(s => s.folder !== folder);
    sessions.unshift({ name, video_count: videoCount, folder, last_opened: new Date().toISOString() });
    localStorage.setItem(key, JSON.stringify(sessions.slice(0, 5)));
    _renderRecentSessions();
  }

  function _renderRecentSessions() {
    const key = 'sizzleRecentSessions';
    const sessions = JSON.parse(localStorage.getItem(key) || '[]');
    const section = $('recent-folders-section');
    const list    = $('recent-folders-list');
    if (!sessions.length) { section.classList.add('hidden'); return; }
    const label = section.querySelector('.recent-folders-label');
    if (label) label.textContent = 'Recent uploads';
    list.innerHTML = '';
    sessions.forEach(s => {
      const li = document.createElement('li');
      li.className = 'recent-folder-item';
      const nameSpan = document.createElement('span');
      nameSpan.className = 'recent-folder-name';
      nameSpan.textContent = `📁 ${s.name}/`;
      const metaSpan = document.createElement('span');
      metaSpan.className = 'recent-folder-meta';
      metaSpan.textContent = `${s.video_count} video${s.video_count !== 1 ? 's' : ''} · ${relativeTime(s.last_opened)}`;
      li.appendChild(nameSpan);
      li.appendChild(metaSpan);
      li.addEventListener('click', async () => {
        pathInput.value = s.name;
        await openFolder(s.folder, s.name);
      });
      list.appendChild(li);
    });
    section.classList.remove('hidden');
  }

  // Render on load and skip the server recent-folders fetch
  _renderRecentSessions();

  // Show output-folder buttons (cloud-only) and init their labels
  const setFolderBtns = [$('btn-set-output-folder'), $('btn-lib-set-output-folder')];
  setFolderBtns.forEach(btn => {
    if (!btn) return;
    btn.classList.remove('hidden');
    btn.addEventListener('click', _pickOutputFolder);
  });
  _updateOutputFolderUI();
})();

// ─── Prompt History ───────────────────────────────────────────────────────────

let _phOutsideClickHandler = null;

function _closePHPanel() {
  $('prompt-history-panel').classList.add('hidden');
  $('ph-template-save-area').classList.add('hidden');
  if (_phOutsideClickHandler) {
    document.removeEventListener('click', _phOutsideClickHandler);
    _phOutsideClickHandler = null;
  }
}

async function _openPHPanel() {
  const panel = $('prompt-history-panel');
  panel.classList.remove('hidden');

  const data = await fetch('/prompt-history').then(r => r.json()).catch(() => ({ recent: [], templates: [] }));
  _renderPHRecent(data.recent);
  _renderPHTemplates(data.templates);

  // Close on outside click
  setTimeout(() => {
    _phOutsideClickHandler = (e) => {
      if (!panel.contains(e.target) && e.target !== $('btn-history-toggle')) {
        _closePHPanel();
      }
    };
    document.addEventListener('click', _phOutsideClickHandler);
  }, 0);
}

function _renderPHRecent(items) {
  const list = $('ph-recent-list');
  list.innerHTML = '';
  if (!items.length) {
    list.innerHTML = '<div class="ph-empty">No history yet</div>';
    return;
  }
  items.forEach(text => {
    const item = document.createElement('div');
    item.className = 'ph-item';
    item.textContent = text;
    item.addEventListener('click', () => {
      $('analyze-input').value = text;
      _closePHPanel();
      _updateSaveTemplateBtn();
    });
    list.appendChild(item);
  });
}

function _renderPHTemplates(templates) {
  const list = $('ph-templates-list');
  list.innerHTML = '';
  if (!templates.length) {
    list.innerHTML = '<div class="ph-empty">No templates saved</div>';
    return;
  }
  templates.forEach(tpl => {
    const row = document.createElement('div');
    row.className = 'ph-item ph-template-row';

    const label = document.createElement('span');
    label.className = 'ph-template-label';
    label.textContent = tpl.name;
    label.addEventListener('click', () => {
      $('analyze-input').value = tpl.text;
      _closePHPanel();
      _updateSaveTemplateBtn();
    });

    const del = document.createElement('button');
    del.className = 'ph-delete-btn';
    del.type = 'button';
    del.textContent = '×';
    del.title = 'Delete template';
    del.addEventListener('click', async (e) => {
      e.stopPropagation();
      await fetch('/prompt-history', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete_template', name: tpl.name }),
      });
      row.remove();
      if (!$('ph-templates-list').children.length) {
        $('ph-templates-list').innerHTML = '<div class="ph-empty">No templates saved</div>';
      }
    });

    row.appendChild(label);
    row.appendChild(del);
    list.appendChild(row);
  });
}

function _updateSaveTemplateBtn() {
  const val = $('analyze-input').value.trim();
  $('btn-save-template').classList.toggle('hidden', !val);
}

$('btn-history-toggle').addEventListener('click', (e) => {
  e.stopPropagation();
  const panel = $('prompt-history-panel');
  if (panel.classList.contains('hidden')) {
    _openPHPanel();
  } else {
    _closePHPanel();
  }
});

$('analyze-input').addEventListener('input', _updateSaveTemplateBtn);

$('btn-save-template').addEventListener('click', (e) => {
  e.stopPropagation();
  const saveArea = $('ph-template-save-area');
  if (saveArea.classList.contains('hidden')) {
    // Make sure panel is open
    if ($('prompt-history-panel').classList.contains('hidden')) {
      _openPHPanel();
    }
    saveArea.classList.remove('hidden');
    $('ph-template-name').value = '';
    $('ph-template-name').focus();
  }
});

async function _doSaveTemplate() {
  const name = $('ph-template-name').value.trim();
  const text = $('analyze-input').value.trim();
  if (!name || !text) return;
  await fetch('/prompt-history', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'save_template', name, text }),
  });
  $('ph-template-save-area').classList.add('hidden');
  // Refresh templates list
  const data = await fetch('/prompt-history').then(r => r.json()).catch(() => ({ recent: [], templates: [] }));
  _renderPHTemplates(data.templates);
}

$('ph-template-save-btn').addEventListener('click', _doSaveTemplate);
$('ph-template-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') _doSaveTemplate();
  if (e.key === 'Escape') $('ph-template-save-area').classList.add('hidden');
});
