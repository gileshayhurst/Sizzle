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
