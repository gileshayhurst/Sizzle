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
  librarySegmentStarts: [],
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
    updateGenerateBtn();
  });
});

// ─── Analyze bar ──────────────────────────────────────────────────────────────
$('btn-analyze').addEventListener('click', runAnalyze);
$('analyze-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') runAnalyze();
});

async function runAnalyze() {
  const prompt = $('analyze-input').value.trim();
  if (!prompt) return;

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

  } catch (err) {
    $('analyze-error').textContent = 'Network error: ' + err.message;
    $('analyze-error').classList.remove('hidden');
  } finally {
    $('btn-analyze').textContent = 'Analyze';
    $('btn-analyze').disabled = false;
    $('analyze-input').disabled = false;
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
document.addEventListener('mouseup', () => { _dragActive = false; });

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
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  };
}

function showResult(result) {
  showScreen('screen-result');
  $('topbar-controls').classList.remove('hidden');

  state.resultSegmentStarts = result.segment_starts || [];

  const src = `/video/${state.resultJobId}`;
  $('result-source').src = src;
  $('result-video').load();

  $('result-filename').textContent = result.filename;
  const mins = Math.floor(result.duration_seconds / 60);
  const secs = result.duration_seconds % 60;
  $('result-info').textContent =
    `${mins}:${String(secs).padStart(2,'0')} · ${result.clip_count} clips · saved to folder`;
}

// ─── Segment skip ─────────────────────────────────────────────────────────────
function skipToSegment(video, segmentStarts, direction) {
  const t = video.currentTime;
  if (direction === 'next') {
    const target = segmentStarts.find(s => s > t + 0.5);
    if (target !== undefined) video.currentTime = target;
  } else {
    const targets = segmentStarts.filter(s => s < t - 0.5);
    if (targets.length) video.currentTime = targets[targets.length - 1];
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
  state.librarySegmentStarts = entry.segment_starts || [];
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

// Load recent folders on startup
loadRecentFolders();
