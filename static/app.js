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
};

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
    if (tab === 'library') loadLibrary();
  });
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
  const resp = await fetch('/browse', { method: 'POST' });
  const { path } = await resp.json();
  if (path) $('folder-path-input').value = path;
});

$('btn-load-folder').addEventListener('click', () => {
  const folder = $('folder-path-input').value.trim();
  if (!folder) return;
  openFolder(folder);
});

$('folder-path-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const folder = e.target.value.trim();
    if (folder) openFolder(folder);
  }
});

async function openFolder(folder) {
  $('folder-error').classList.add('hidden');
  const resp = await fetch('/load-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    $('folder-error').textContent = data.error || 'Failed to open folder';
    $('folder-error').classList.remove('hidden');
    return;
  }
  state.folder = folder;
  state.files = [];
  state.checked = {};
  state.highlighted = {};

  $('folder-badge').textContent = '📁 ' + folder.split(/[\\/]/).pop() + '/';
  $('output-filename').value = folder.split(/[\\/]/).pop() + '_sizzle.mp4';

  if (data.job_id) {
    // Needs transcription
    showScreen('screen-transcribing');
    $('topbar-controls').classList.add('hidden');
    pollTranscription(data.job_id, data.files, folder);
  } else {
    await loadTranscripts(folder);
    showWorkspace();
  }
}

// ─── Transcription polling ────────────────────────────────────────────────────
function pollTranscription(jobId, files, folder) {
  const total = files.length;
  let lastLogLen = 0;

  const interval = setInterval(async () => {
    const resp = await fetch(`/status/${jobId}`);
    const job = await resp.json();

    const pct = total > 0 ? Math.round((job.done / total) * 100) : 0;
    $('transcribe-bar').style.width = pct + '%';
    $('transcribe-subtitle').textContent = `Transcribing ${job.done} / ${total} videos...`;

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
  const resp = await fetch(`/transcripts?folder=${encodeURIComponent(folder)}`);
  const data = await resp.json();
  state.files = data.files;
  state.files.forEach(f => {
    if (!state.checked[f.name]) state.checked[f.name] = new Set();
    if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
  });
}

function showWorkspace() {
  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
  renderSidebar();
  if (state.files.length > 0) selectFile(state.files[0].name);
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
  });
});

// ─── Analyze Everything ───────────────────────────────────────────────────────
$('btn-analyze-all').addEventListener('click', () => {
  submitGenerate('all', {});
});

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

// ─── Checkbox mode ────────────────────────────────────────────────────────────
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

  Object.values(groups).forEach(group => {
    const groupEl = document.createElement('div');
    groupEl.className = 'minute-group';

    const labelEl = document.createElement('div');
    labelEl.className = 'minute-label';
    labelEl.textContent = group.label;

    const checkAllBtn = document.createElement('button');
    checkAllBtn.className = 'check-all-group';
    checkAllBtn.textContent = 'check all';
    checkAllBtn.addEventListener('click', e => {
      e.stopPropagation();
      group.lines.forEach(l => state.checked[fileObj.name].add(l.raw));
      renderCheckboxMode(fileObj);
      refreshBadge(fileObj.name);
    });
    labelEl.appendChild(checkAllBtn);
    groupEl.appendChild(labelEl);

    group.lines.forEach(line => {
      const lineEl = document.createElement('div');
      lineEl.className = 'transcript-line-cb';

      const cbBox = document.createElement('div');
      cbBox.className = 'cb-box' + (state.checked[fileObj.name].has(line.raw) ? ' checked' : '');
      cbBox.textContent = state.checked[fileObj.name].has(line.raw) ? '✓' : '';

      const ts = document.createElement('div');
      ts.className = 'ts-cb';
      ts.textContent = line.timestamp;

      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;

      lineEl.appendChild(cbBox);
      lineEl.appendChild(ts);
      lineEl.appendChild(text);

      lineEl.addEventListener('click', () => {
        const s = state.checked[fileObj.name];
        if (s.has(line.raw)) { s.delete(line.raw); cbBox.classList.remove('checked'); cbBox.textContent = ''; }
        else { s.add(line.raw); cbBox.classList.add('checked'); cbBox.textContent = '✓'; }
        refreshBadge(fileObj.name);
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
}

function renderTranscript(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (state.mode === 'checkbox') renderCheckboxMode(fileObj);
  else renderHighlightMode(fileObj);
}
