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

// ─── Highlight mode ───────────────────────────────────────────────────────────
let _dragActive = false;
let _dragSetTo = null;   // true = highlighting, false = un-highlighting
document.addEventListener('mouseup', () => { _dragActive = false; });

function renderHighlightMode(fileObj) {
  const scroll = $('transcript-scroll');
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
    e.preventDefault();
    _dragActive = true;
    const raw = lineEl.dataset.raw;
    const hl = state.highlighted[fileObj.name];
    // Determine whether this drag is a highlight or un-highlight pass
    _dragSetTo = !hl.has(raw);
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
  });

  scroll.addEventListener('mousemove', e => {
    if (!_dragActive) return;
    const lineEl = e.target.closest('.transcript-line-hl');
    if (!lineEl) {
      // Auto-scroll when near edges
      const rect = scroll.getBoundingClientRect();
      const threshold = 40;
      if (e.clientY < rect.top + threshold) scroll.scrollTop -= 8;
      else if (e.clientY > rect.bottom - threshold) scroll.scrollTop += 8;
      return;
    }
    _applyHighlight(fileObj.name, lineEl, _dragSetTo);
    refreshBadge(fileObj.name);
  });

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
  const prompt = $('prompt-input').value.trim();
  if (!prompt) { alert('Please enter a prompt before generating.'); return; }

  const outputFilename = $('output-filename').value.trim() || 'sizzle_reel.mp4';

  showScreen('screen-generating');
  $('gen-log').innerHTML = '';
  $('gen-bar').style.width = '0%';
  $('topbar-controls').classList.add('hidden');

  const resp = await fetch('/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      folder: state.folder,
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
  pollGeneration(job_id);
}

function pollGeneration(jobId) {
  let lastLogLen = 0;

  const interval = setInterval(async () => {
    const resp = await fetch(`/status/${jobId}`);
    const job = await resp.json();

    const pct = job.total > 0 ? Math.round((job.done / job.total) * 100) : 0;
    $('gen-bar').style.width = Math.max(pct, 5) + '%';

    const newLines = job.log.slice(lastLogLen);
    newLines.forEach(msg => appendLog('gen-log', msg));
    lastLogLen = job.log.length;

    if (job.status === 'done') {
      clearInterval(interval);
      $('gen-bar').style.width = '100%';
      state.resultJobId = jobId;
      showResult(job.result);
    } else if (job.status === 'error') {
      clearInterval(interval);
      appendLog('gen-log', `✗ Error: ${job.error}`);
      $('topbar-controls').classList.remove('hidden');
    } else if (job.status === 'cancelled') {
      clearInterval(interval);
      showScreen('screen-workspace');
      $('topbar-controls').classList.remove('hidden');
    }
  }, 2000);

  $('btn-cancel-gen').onclick = async () => {
    await fetch(`/jobs/${jobId}`, { method: 'DELETE' });
    clearInterval(interval);
  };
}

function showResult(result) {
  showScreen('screen-result');
  $('topbar-controls').classList.remove('hidden');

  const src = `/video/${state.resultJobId}`;
  $('result-source').src = src;
  $('result-video').load();

  $('result-filename').textContent = result.filename;
  const mins = Math.floor(result.duration_seconds / 60);
  const secs = result.duration_seconds % 60;
  $('result-info').textContent =
    `${mins}:${String(secs).padStart(2,'0')} · ${result.clip_count} clips · saved to folder`;
}

$('btn-new-reel').addEventListener('click', () => {
  $('result-video').pause();
  $('result-source').src = '';
  showScreen('screen-workspace');
  $('topbar-controls').classList.remove('hidden');
});

$('btn-open-folder').addEventListener('click', async () => {
  await fetch('/open-folder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder }),
  });
});

// ─── Library ──────────────────────────────────────────────────────────────────
async function loadLibrary() {
  const resp = await fetch('/library');
  const entries = await resp.json();
  renderLibrary(entries);
}

function escAttr(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderLibrary(entries) {
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

    const mins = Math.floor((entry.duration_seconds || 0) / 60);
    const secs = (entry.duration_seconds || 0) % 60;
    const durStr = `${mins}:${String(secs).padStart(2,'0')}`;
    const dateStr = entry.created_at ? entry.created_at.split('T')[0] : '';

    card.innerHTML = `
      <div class="reel-thumb" data-id="${entry.id}">
        <div class="reel-play-icon">▶</div>
        <div class="reel-duration">${durStr}</div>
      </div>
      <div class="reel-body">
        <div class="reel-name" title="${escAttr(entry.filename)}">${escAttr(entry.filename)}</div>
        <div class="reel-meta">${escAttr(dateStr)} · ${entry.clip_count || 0} clips · ${escAttr(entry.source_folder || '')}</div>
        <div class="reel-prompt" title="${escAttr(entry.prompt)}">"${escAttr(entry.prompt)}"</div>
        <div class="reel-actions">
          <button class="reel-btn play" data-id="${entry.id}">▶ Play</button>
          <button class="reel-btn show" data-id="${entry.id}" data-path="${escAttr(entry.path)}">📂 Show</button>
          <button class="reel-btn delete" data-id="${entry.id}">🗑</button>
        </div>
      </div>`;

    // Thumb click = play
    card.querySelector('.reel-thumb').addEventListener('click', () => openLibraryPlayer(entry));
    card.querySelector('.reel-btn.play').addEventListener('click', () => openLibraryPlayer(entry));

    // Show in explorer
    card.querySelector('.reel-btn.show').addEventListener('click', async () => {
      const folder = entry.path.replace(/[\\/][^\\/]+$/, '');
      await fetch('/open-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder }),
      });
    });

    // Delete
    card.querySelector('.reel-btn.delete').addEventListener('click', async () => {
      await fetch(`/library/${entry.id}`, { method: 'DELETE' });
      loadLibrary();
    });

    grid.appendChild(card);
  });
}

function openLibraryPlayer(entry) {
  $('library-source').src = `/library-video/${entry.id}`;
  $('library-video').load();
  $('library-player-meta').textContent =
    `"${entry.prompt}" — ${entry.source_folder}`;
  $('library-player-overlay').classList.remove('hidden');
}

$('btn-close-player').addEventListener('click', () => {
  $('library-video').pause();
  $('library-source').src = '';
  $('library-player-overlay').classList.add('hidden');
});
