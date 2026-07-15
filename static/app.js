const GENERATOR_URL = (window.__CONFIG__ || {}).generatorUrl || 'http://localhost:5001';
const APP_MODE      = (window.__CONFIG__ || {}).mode || 'local';

// ─── Auth (cloud mode only) ─────────────────────────────────────────────────
// Local mode is a trusted single-user desktop app — no token, no login screen.
let AUTH_TOKEN = (APP_MODE === 'cloud') ? sessionStorage.getItem('sizzle_token') : null;

function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  if (AUTH_TOKEN) h['Authorization'] = 'Bearer ' + AUTH_TOKEN;
  return h;
}

// Single choke point: every fetch in the app goes through here so the Bearer
// header is always attached and a 401 bounces the user back to login.
const _rawFetch = window.fetch.bind(window);
window.fetch = function (url, opts) {
  opts = opts || {};
  opts.headers = authHeaders(opts.headers);
  return _rawFetch(url, opts).then(r => {
    if (r.status === 401 && APP_MODE === 'cloud') {
      AUTH_TOKEN = null;
      sessionStorage.removeItem('sizzle_token');
      showLoginScreen();
    }
    return r;
  });
};

// Append the token to a generator WebSocket URL (WS can't send headers).
function withWsToken(base) {
  return AUTH_TOKEN
    ? base + (base.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(AUTH_TOKEN)
    : base;
}

async function doLogin(userId, password) {
  const r = await _rawFetch('/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, password }),
  });
  if (!r.ok) return false;
  AUTH_TOKEN = (await r.json()).token;
  sessionStorage.setItem('sizzle_token', AUTH_TOKEN);
  return true;
}

function showLoginScreen() {
  const el = document.getElementById('screen-login');
  if (el) el.classList.remove('hidden');
}
function hideLoginScreen() {
  const el = document.getElementById('screen-login');
  if (el) el.classList.add('hidden');
}

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
  pool: [],           // flat candidate array (buildCandidatePool output)
  poolOrdered: [],    // pool sorted into priority order
  sliderCustom: false,// true once the selection diverges from a priority prefix
  resultSegmentStarts: [],
  resultDownloadUrl: null,
  resultPath: null,
  librarySegmentStarts: [],
  librarySort: 'newest',
  libraryEntries: [],
};

// ─── Priority selection model ───────────────────────────────────────────────
// A "candidate" is one scored segment: {file, score, duration_seconds,
// start_seconds, lines:[raw...]}. All math below is pure (no DOM, no network).

const OPTIMAL_MIN_SCORE = 8;      // quality bar for the optimal cut
const OPTIMAL_SOFT_CAP_SECONDS = 180;  // ~3 min soft cap on the optimal cut

// Flatten the /analyze `segments` payload into one candidate array.
// fileOrder is the array of filenames in state.files order (for tie-breaking).
function buildCandidatePool(segmentsByFile, fileOrder) {
  const pool = [];
  fileOrder.forEach(file => {
    (segmentsByFile[file] || []).forEach(seg => {
      pool.push({
        file,
        score: seg.score,
        duration_seconds: seg.duration_seconds,
        start_seconds: seg.start_seconds,
        lines: seg.lines,
      });
    });
  });
  return pool;
}

// Deterministic priority order: score desc, duration asc, file order, start asc.
function sortByPriority(pool, fileOrder) {
  const fileIndex = f => {
    const i = fileOrder.indexOf(f);
    return i === -1 ? fileOrder.length : i;
  };
  return [...pool].sort((a, b) =>
    b.score - a.score ||
    a.duration_seconds - b.duration_seconds ||
    fileIndex(a.file) - fileIndex(b.file) ||
    a.start_seconds - b.start_seconds
  );
}

// Cumulative durations along the priority order — the slider's snap points.
// Returns [d1, d1+d2, ...] (length === ordered.length).
function cumulativeDurations(ordered) {
  const sums = [];
  let total = 0;
  ordered.forEach(c => { total += c.duration_seconds; sums.push(total); });
  return sums;
}

// The optimal cut: all candidates scoring >= OPTIMAL_MIN_SCORE, falling back to
// the highest score present; trimmed to the soft cap but never below 1 segment.
// Returns the optimal duration in seconds (a valid snap point).
function optimalDuration(ordered) {
  if (ordered.length === 0) return 0;
  let qualifying = ordered.filter(c => c.score >= OPTIMAL_MIN_SCORE);
  if (qualifying.length === 0) {
    const top = ordered[0].score;  // ordered is score-desc, so [0] is highest
    qualifying = ordered.filter(c => c.score === top);
  }
  // qualifying is a prefix of `ordered` by construction (highest-priority items).
  // Trim from the end until under the soft cap, keeping at least one.
  let dur = qualifying.reduce((s, c) => s + c.duration_seconds, 0);
  while (qualifying.length > 1 && dur > OPTIMAL_SOFT_CAP_SECONDS) {
    dur -= qualifying[qualifying.length - 1].duration_seconds;
    qualifying = qualifying.slice(0, -1);
  }
  return dur;
}

// Given a target duration, return the priority PREFIX whose cumulative duration
// is the largest snap point <= target, but always >= 1 segment.
function prefixForDuration(ordered, targetSeconds) {
  if (ordered.length === 0) return [];
  const sums = cumulativeDurations(ordered);
  let k = 1;  // always at least one segment
  for (let i = 0; i < sums.length; i++) {
    if (sums[i] <= targetSeconds + 1e-6) k = i + 1;
  }
  return ordered.slice(0, k);
}

// Merge new scored segments into the pool. Overlapping ranges in the same file
// dedupe keeping the higher score. Returns the merged flat pool. Candidates from
// buildCandidatePool lack end_seconds, so the overlap check reconstructs it from
// start_seconds + duration_seconds.
function mergeIntoPool(existingPool, segmentsByFile, fileOrder) {
  const merged = [...existingPool];
  const end = c => c.start_seconds + c.duration_seconds;
  const overlaps = (a, b) =>
    a.file === b.file && a.start_seconds < end(b) && b.start_seconds < end(a);
  fileOrder.forEach(file => {
    (segmentsByFile[file] || []).forEach(seg => {
      const cand = {
        file,
        score: seg.score,
        duration_seconds: seg.duration_seconds,
        start_seconds: seg.start_seconds,
        lines: seg.lines,
      };
      const hit = merged.find(m => overlaps(m, cand));
      if (hit) {
        if (cand.score > hit.score) Object.assign(hit, cand);
      } else {
        merged.push(cand);
      }
    });
  });
  return merged;
}

function _fmtSeconds(s) {
  const m = Math.floor(s / 60);
  const r = Math.round(s % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
}

// Replace the current selection with the given candidate list's lines,
// applied to BOTH checked and highlighted (mirrors runAnalyze).
function _applyCandidatesToSelection(candidates) {
  state.files.forEach(f => {
    state.checked[f.name] = new Set();
    state.highlighted[f.name] = new Set();
  });
  candidates.forEach(c => {
    c.lines.forEach(l => {
      state.checked[c.file].add(l);
      state.highlighted[c.file].add(l);
    });
  });
}

// Update slider min/max/marker/value WITHOUT changing the current selection.
function _refreshSliderChromeOnly(value) {
  const ordered = state.poolOrdered;
  if (ordered.length < 2) return;
  $('reel-length-row').classList.remove('hidden');
  const sums = cumulativeDurations(ordered);
  const slider = $('reel-slider');
  slider.min = sums[0];
  slider.max = sums[sums.length - 1];
  slider.value = value == null ? sums[0] : value;
  const optD = optimalDuration(ordered);
  const pct = sums[sums.length - 1] > sums[0]
    ? ((optD - sums[0]) / (sums[sums.length - 1] - sums[0])) * 100 : 0;
  $('reel-optimal-marker').style.left = `${pct}%`;
  $('reel-slider-min').textContent = _fmtSeconds(sums[0]);
  $('reel-slider-max').textContent = _fmtSeconds(sums[sums.length - 1]);
}

// Rebuild the slider UI from state.poolOrdered. selectDuration: the duration to
// select at (defaults to optimal). Mutates selection + DOM.
function _refreshSlider(selectDuration) {
  const ordered = state.poolOrdered;
  if (ordered.length < 2) { $('reel-length-row').classList.add('hidden'); return; }
  const optD = optimalDuration(ordered);
  const target = selectDuration == null ? optD : selectDuration;
  _refreshSliderChromeOnly(target);
  _applySliderSelection(target);
}

// Apply the priority prefix for `target` seconds and update label + counts.
function _applySliderSelection(target) {
  const ordered = state.poolOrdered;
  const prefix = prefixForDuration(ordered, target);
  _applyCandidatesToSelection(prefix);
  state.sliderCustom = false;

  const selDur = prefix.reduce((s, c) => s + c.duration_seconds, 0);
  $('reel-length-label').textContent =
    `Reel length · ${_fmtSeconds(selDur)} · ${prefix.length} of ${ordered.length} segments`;
  const status = document.querySelector('.reel-length-status');
  if (status) status.classList.remove('custom');

  if (state.activeFile) renderTranscript(state.activeFile);
  state.files.forEach(f => refreshBadge(f.name));
  updateGenerateBtn();
  _saveSelections();
  _savePool();
}

// Called when the user manually edits lines — mark the slider "custom".
function markSliderCustom() {
  if (state.poolOrdered.length < 2) return;
  state.sliderCustom = true;
  const status = document.querySelector('.reel-length-status');
  if (status) status.classList.add('custom');
  $('reel-length-label').textContent = 'Custom selection · drag slider to reset';
  _savePool();
}

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
  const label = name ? name : 'Set output folder';
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
  if (perm !== 'granted') {
    const result = await handle.requestPermission({ mode: 'readwrite' });
    if (result !== 'granted') return null;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 120_000);
  let blob;
  try {
    // Browser path has no server job — fetch from library-video via entry id.
    const videoSrc = jobId
      ? `${GENERATOR_URL}/video/${jobId}`
      : `${GENERATOR_URL}/library-video/${entryId}`;
    const resp = await fetch(videoSrc, { signal: controller.signal });
    if (!resp.ok) throw new Error(`Video fetch failed: ${resp.status}`);
    blob = await resp.blob();
  } finally {
    clearTimeout(timeoutId);
  }

  return _saveToOutputFolder(handle, filename, blob, entryId);
}

let _genWs = null;         // active generation WebSocket
let _genPollTimer = null;  // fallback HTTP polling timer for generation status
let _genTerminated = false; // guards against handling a job's end twice

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

function _savePool() {
  if (!state.folder) return;
  try {
    localStorage.setItem('sizzle_pool_' + state.folder, JSON.stringify({
      pool: state.pool,
      sliderValue: parseFloat($('reel-slider')?.value || '0'),
      custom: state.sliderCustom,
    }));
  } catch (_) {}
}

function _restorePool() {
  if (!state.folder) return;
  try {
    const raw = localStorage.getItem('sizzle_pool_' + state.folder);
    if (!raw) return;
    const saved = JSON.parse(raw);
    const fileNames = new Set(state.files.map(f => f.name));
    state.pool = (saved.pool || []).filter(c => fileNames.has(c.file));
    state.poolOrdered = sortByPriority(state.pool, state.files.map(f => f.name));
    if (state.poolOrdered.length >= 2) {
      if (saved.custom) {
        // rebuild slider chrome without overwriting the restored selection
        _refreshSliderChromeOnly(saved.sliderValue);
        markSliderCustom();
      } else {
        _refreshSlider(saved.sliderValue);
      }
    }
  } catch (_) {}
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
  if (state.folder) {
    try { localStorage.removeItem('sizzle_pool_' + state.folder); } catch (_) {}
  }
  state.pool = [];
  state.poolOrdered = [];
  state.sliderCustom = false;
  $('reel-length-row')?.classList.add('hidden');
  $('analyze-add-row')?.classList.add('hidden');
  const addInput = $('analyze-add-input');
  if (addInput) addInput.value = '';
}

// ─── DOM refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ─── Modal a11y: open/close with focus management, Escape, and Tab trap ──────
// Modals never stack, so a single return-focus slot + one trap handler suffice.
let _modalReturnFocus = null;
let _modalTrapHandler = null;

const _FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

function _openModal(overlayId, focusId) {
  _modalReturnFocus = document.activeElement;
  const overlay = $(overlayId);
  overlay.classList.remove('hidden');
  const target = $(focusId);
  if (target) target.focus();

  // Trap Tab within the dialog so keyboard focus can't wander to the still-present
  // background controls (aria-modal="true" promises this; the browser doesn't).
  _modalTrapHandler = (e) => {
    if (e.key !== 'Tab') return;
    const items = [...overlay.querySelectorAll(_FOCUSABLE)].filter(el => el.offsetParent !== null);
    if (items.length === 0) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  document.addEventListener('keydown', _modalTrapHandler);
}

function _closeModal(overlayId) {
  $(overlayId).classList.add('hidden');
  if (_modalTrapHandler) {
    document.removeEventListener('keydown', _modalTrapHandler);
    _modalTrapHandler = null;
  }
  if (_modalReturnFocus && document.contains(_modalReturnFocus)) _modalReturnFocus.focus();
  _modalReturnFocus = null;
}

// Escape closes whichever modal is open (routed through each modal's own
// close/cancel handler so cleanup logic stays in one place).
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (!$('library-player-overlay').classList.contains('hidden')) $('btn-close-player').click();
  else if (!$('not-downloaded-modal').classList.contains('hidden')) $('btn-not-downloaded-close').click();
  else if (!$('loading-folder-modal').classList.contains('hidden')) $('btn-loading-folder-cancel').click();
});

// ─── "Not downloaded" modal ───────────────────────────────────────────────────
let _modalEntry = null;

function _showNotDownloadedModal(entry) {
  _modalEntry = entry;
  const folderName = localStorage.getItem('sizzle_output_folder_name') || 'output folder';
  $('not-downloaded-body').textContent =
    `"${entry.filename}" has not been saved to your local machine.`;
  $('btn-not-downloaded-save').textContent = `Save to ${folderName}`;
  _openModal('not-downloaded-modal', 'btn-not-downloaded-close');
}

$('btn-not-downloaded-close').addEventListener('click', () => {
  _closeModal('not-downloaded-modal');
  _modalEntry = null;
});

$('btn-not-downloaded-view').addEventListener('click', () => {
  if (_modalEntry) {
    window.open(`${GENERATOR_URL}/library-video/${_modalEntry.id}`, '_blank');
  }
  _closeModal('not-downloaded-modal');
  _modalEntry = null;
});

$('btn-not-downloaded-save').addEventListener('click', async () => {
  if (!_modalEntry) return;
  const entry = _modalEntry;
  _closeModal('not-downloaded-modal');
  _modalEntry = null;

  let handle = await _idbLoad('sizzle_output_dir_handle').catch(() => null);
  if (!handle) {
    handle = await _pickOutputFolder();
    if (!handle) return;
  }

  const saveBtn = document.querySelector(`.reel-btn.show[data-id="${entry.id}"]`);
  const prevBtnText = saveBtn ? saveBtn.textContent : 'Show';
  if (saveBtn) { saveBtn.textContent = 'Saving…'; saveBtn.disabled = true; }

  try {
    const resp = await fetch(`${GENERATOR_URL}/library-video/${entry.id}`);
    if (!resp.ok) throw new Error(`Fetch failed: ${resp.status}`);
    const blob = await resp.blob();
    const saved = await _saveToOutputFolder(handle, entry.filename, blob, entry.id);

    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = saved.localFolderPath ? 'Show' : 'View';
    }
  } catch (err) {
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = prevBtnText; }
    alert(`Save failed: ${err.message}`);
  }
});

// ─── Navigation ───────────────────────────────────────────────────────────────
document.querySelectorAll('.nav-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-tab').forEach(b => {
      b.classList.remove('active');
      b.setAttribute('aria-selected', 'false');
    });
    btn.classList.add('active');
    btn.setAttribute('aria-selected', 'true');
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

// ─── Loading-folder modal ─────────────────────────────────────────────────────
// One load is in flight at a time. Each openFolder call captures its own ctx,
// so late responses from a cancelled load are ignored.
let _loadingCtx = null;

function _showLoadingModal(name) {
  _loadingCtx = { abort: null, jobId: null, pollTimer: null, cancelled: false };
  $('loading-folder-name').textContent = name;
  const bar = $('loading-folder-bar');
  bar.style.width = '';               // let the .indeterminate width apply
  bar.classList.add('indeterminate');
  $('loading-folder-status').textContent = 'Opening folder…';
  _openModal('loading-folder-modal', 'btn-loading-folder-cancel');
  return _loadingCtx;
}

function _closeLoadingModal() {
  _closeModal('loading-folder-modal');
  $('loading-folder-bar').classList.remove('indeterminate');
  _loadingCtx = null;
}

$('btn-loading-folder-cancel').addEventListener('click', () => {
  const ctx = _loadingCtx;
  if (!ctx) return;
  ctx.cancelled = true;
  if (ctx.abort) ctx.abort.abort();
  if (ctx.pollTimer) clearInterval(ctx.pollTimer);
  if (ctx.jobId) fetch(`/jobs/${ctx.jobId}`, { method: 'DELETE' }).catch(() => {});
  _closeLoadingModal();
  const btnLoad = $('btn-load-folder');
  if (btnLoad) btnLoad.disabled = false;
  // No-op when already on the picker; returns there from the upload flow.
  showScreen('screen-folder-picker');
});

async function openFolder(folder, displayName) {
  const folderErr = $('folder-error');
  const btnLoad   = $('btn-load-folder');
  const name = displayName || folder.split(/[\\/]/).pop() || folder;
  folderErr.classList.add('hidden');
  if (btnLoad) btnLoad.disabled = true;

  const ctx = _showLoadingModal(name + '/');
  ctx.abort = new AbortController();

  const fail = (msg) => {
    _closeLoadingModal();
    if (btnLoad) btnLoad.disabled = false;
    showScreen('screen-folder-picker');
    folderErr.textContent = msg;
    folderErr.classList.remove('hidden');
  };

  let resp, data;
  try {
    resp = await fetch('/load-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
      signal: ctx.abort.signal,
    });
    data = await resp.json();
  } catch (err) {
    if (ctx.cancelled) return;   // user hit ✕ — handler already cleaned up
    fail('Could not open folder — try uploading your files again.');
    return;
  }
  if (ctx.cancelled) return;

  if (!resp.ok) {
    fail(data.error || 'Failed to open folder');
    return;
  }

  if (data.job_type === 'session_download') {
    // Cloud: transcripts are downloading server-side — poll for progress.
    ctx.jobId = data.job_id;
    const bar = $('loading-folder-bar');
    bar.classList.remove('indeterminate');
    bar.style.width = '0%';
    _pollSessionDownload(ctx, folder, name, fail);
    return;
  }

  _closeLoadingModal();
  if (btnLoad) btnLoad.disabled = false;
  await _enterFolder(folder, name, data);
}

function _pollSessionDownload(ctx, folder, displayName, fail) {
  ctx.pollTimer = setInterval(async () => {
    let job;
    try {
      const resp = await fetch(`/status/${ctx.jobId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      job = await resp.json();
    } catch (err) {
      clearInterval(ctx.pollTimer);
      if (ctx.cancelled) return;
      fail('Lost contact with server while loading the folder.');
      return;
    }
    if (ctx.cancelled) return;

    if (job.total > 0) {
      const pct = Math.round((job.done / job.total) * 100);
      setBar('loading-folder-bar', pct);
      $('loading-folder-status').textContent =
        `Downloading transcripts… ${job.done} of ${job.total}`;
    }

    if (job.status === 'done') {
      clearInterval(ctx.pollTimer);
      _closeLoadingModal();
      const btnLoad = $('btn-load-folder');
      if (btnLoad) btnLoad.disabled = false;
      await _enterFolder(folder, displayName, job.result || {});
    } else if (job.status === 'error') {
      clearInterval(ctx.pollTimer);
      fail(job.error || 'Failed to load folder');
    } else if (job.status === 'cancelled') {
      // Cancelled from another tab/path — mirror the ✕ cleanup.
      clearInterval(ctx.pollTimer);
      _closeLoadingModal();
      const btnLoad = $('btn-load-folder');
      if (btnLoad) btnLoad.disabled = false;
    }
  }, 500);
}

async function _enterFolder(folder, displayName, data) {
  state.folder = folder;
  state.folderName = displayName || folder.split(/[\\/]/).pop();
  state.files = [];
  state.checked = {};
  state.highlighted = {};

  $('folder-badge').textContent = state.folderName + '/  ▾';

  if (data.job_id) {
    // Needs transcription (local mode)
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
    let job;
    try {
      const resp = await fetch(`/status/${jobId}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      job = await resp.json();
    } catch (err) {
      clearInterval(interval);
      appendLog('transcribe-log', `✗ Status check failed: ${err.message}`);
      showScreen('screen-folder-picker');
      $('folder-error').textContent = 'Lost contact with server during transcription.';
      $('folder-error').classList.remove('hidden');
      return;
    }

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
    // Timeout: a sleeping Render generator holds the connection open for its
    // whole cold start (30-60s+), which would silently block the workspace
    // from opening. Give up after 4s — same handled path as "unreachable".
    fetch(GENERATOR_URL + '/library', { signal: AbortSignal.timeout(4000) }).catch(() => null),
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

  _restorePool();
}

function showWorkspace() {
  const base = state.folderName || 'sizzle_reel';
  const takenStems = new Set([
    ...state.files.map(f => f.name.replace(/\.[^.]+$/, '').toLowerCase()),
    ...state.libraryEntries.map(e => (e.filename || '').replace(/\.[^.]+$/, '').toLowerCase()),
  ]);
  let suffix = '';
  let n = 1;
  while (takenStems.has((base + suffix).toLowerCase())) {
    suffix = String(n++);
  }
  $('output-filename').value = base + suffix;   // extension (.mp4) is a fixed suffix in the UI

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

// Set a determinate progress bar's width AND its aria-valuenow together, so
// screen readers announce real progress (WCAG 4.1.3). pct is 0–100.
function setBar(id, pct) {
  const bar = $(id);
  const clamped = Math.max(0, Math.min(100, pct));
  bar.style.width = clamped + '%';
  bar.setAttribute('aria-valuenow', Math.round(clamped));
}

// Announce a concise milestone to assistive tech via the polite live region.
// Clearing first guarantees a repeated message (e.g. two errors) is re-read.
function announce(msg) {
  const el = $('sr-status');
  if (!el) return;
  el.textContent = '';
  requestAnimationFrame(() => { el.textContent = msg; });
}

// Parse a fetch Response as JSON without ever throwing "unexpected end of JSON
// input" — a host/platform error page (413/502/504) or an empty body yields {}
// instead of a crash, so callers can fall back to the HTTP status.
async function _safeJson(resp) {
  try {
    return await resp.json();
  } catch {
    return {};
  }
}

// ─── Mode toggle ──────────────────────────────────────────────────────────────
document.querySelectorAll('.mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.mode-btn').forEach(b => {
      b.classList.remove('active');
      b.setAttribute('aria-pressed', 'false');
    });
    btn.classList.add('active');
    btn.setAttribute('aria-pressed', 'true');
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

$('reel-slider').addEventListener('input', e => {
  const sums = cumulativeDurations(state.poolOrdered);
  if (sums.length === 0) return;
  // snap raw value to the nearest cumulative snap point
  const raw = parseFloat(e.target.value);
  let snapped = sums[0];
  for (const s of sums) { if (Math.abs(s - raw) < Math.abs(snapped - raw)) snapped = s; }
  e.target.value = snapped;
  _applySliderSelection(snapped);
});

$('btn-analyze-add').addEventListener('click', runAddAnalyze);
$('analyze-add-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) runAddAnalyze();
});

// POST to /analyze and parse the JSON body. If the server responds with a
// non-JSON body — almost always an HTML error page from a proxy/gateway when a
// long analyze run times out — throw a legible error instead of letting
// resp.json() surface a cryptic "Unexpected token '<'".
async function _postAnalyze(prompt) {
  const resp = await fetch('/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ folder: state.folder, prompt }),
  });
  const text = await resp.text();
  try {
    return { resp, data: JSON.parse(text) };
  } catch {
    if ([502, 503, 504].includes(resp.status) || /<!DOCTYPE|<html/i.test(text)) {
      throw new Error('The server took too long and timed out. Try analyzing fewer or shorter videos at once.');
    }
    throw new Error(`The server returned an unexpected response (HTTP ${resp.status}). Please try again.`);
  }
}

// Shared message strip under the analyze bar. isNotice=true renders it as a
// neutral status (e.g. zero matches) instead of a red error.
function _showAnalyzeMsg(msg, isNotice = false) {
  const el = $('analyze-error');
  el.textContent = msg;
  el.classList.toggle('notice', isNotice);
  el.classList.remove('hidden');
}

// Turn an error from the analyze flow into a user-facing message.
function _analyzeErrorMessage(err) {
  if (err.message === 'Failed to fetch') {
    return 'Could not reach the server. Check your connection and try again.';
  }
  return err.message;
}

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
    const { resp, data } = await _postAnalyze(prompt);

    if (!resp.ok) {
      _showAnalyzeMsg(data.error || 'Analyze failed');
      return;
    }

    state.lastPrompt = prompt;

    // Build the candidate pool from scored segments and select the optimal cut.
    state.pool = buildCandidatePool(data.segments || {}, state.files.map(f => f.name));
    state.poolOrdered = sortByPriority(state.pool, state.files.map(f => f.name));

    if (state.poolOrdered.length >= 2) {
      _refreshSlider();  // selects optimal, renders, saves
    } else {
      // 0 or 1 candidate: no slider — fall back to selecting whatever we have.
      _applyCandidatesToSelection(state.poolOrdered);
      $('reel-length-row').classList.add('hidden');
      if (state.activeFile) renderTranscript(state.activeFile);
      state.files.forEach(f => refreshBadge(f.name));
      updateGenerateBtn();
      _saveSelections();
      _savePool();
    }
    if (state.poolOrdered.length === 0) {
      _showAnalyzeMsg('No matching moments found. Try rephrasing what you’re looking for.', true);
    }
    $('analyze-add-row').classList.remove('hidden');

  } catch (err) {
    _showAnalyzeMsg(_analyzeErrorMessage(err));
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
    const { resp, data } = await _postAnalyze(prompt);

    if (!resp.ok) {
      _showAnalyzeMsg(data.error || 'Analyze failed');
      return;
    }

    const newSegmentCount = Object.values(data.segments || {})
      .reduce((n, segs) => n + segs.length, 0);
    if (newSegmentCount === 0) {
      _showAnalyzeMsg('No new moments matched that prompt. Your current selection is unchanged.', true);
    }

    const fileOrder = state.files.map(f => f.name);
    // Merge new candidates into the pool; re-rank; widen the slider range.
    state.pool = mergeIntoPool(state.pool, data.segments || {}, fileOrder);
    state.poolOrdered = sortByPriority(state.pool, fileOrder);

    // Union the new qualifying (score >= OPTIMAL_MIN_SCORE) segments' lines into
    // the current selection, preserving the "add" intent.
    state.files.forEach(f => {
      if (!state.checked[f.name])     state.checked[f.name]     = new Set();
      if (!state.highlighted[f.name]) state.highlighted[f.name] = new Set();
    });
    Object.entries(data.segments || {}).forEach(([file, segs]) => {
      segs.filter(s => s.score >= OPTIMAL_MIN_SCORE).forEach(s => {
        s.lines.forEach(l => {
          state.checked[file].add(l);
          state.highlighted[file].add(l);
        });
      });
    });

    // Selection is generally no longer a clean prefix -> custom state.
    if (state.poolOrdered.length >= 2) {
      _refreshSliderChromeOnly($('reel-slider').value);
      markSliderCustom();
    }

    if (state.activeFile) renderTranscript(state.activeFile);
    state.files.forEach(f => refreshBadge(f.name));
    updateGenerateBtn();
    _saveSelections();
    _savePool();

  } catch (err) {
    _showAnalyzeMsg(_analyzeErrorMessage(err));
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
    scroll.textContent = 'No transcript for this video yet — re-open the folder to transcribe it, or pick another file from the sidebar.';
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
    labelEl.setAttribute('role', 'button');
    labelEl.setAttribute('tabindex', '0');
    labelEl.setAttribute('aria-label', `Select all lines in ${group.label}`);

    const headerCb = document.createElement('div');
    _updateHeaderCbState(headerCb, group.lines, s);

    const labelText = document.createElement('span');
    labelText.textContent = group.label;

    labelEl.appendChild(headerCb);
    labelEl.appendChild(labelText);

    const toggleGroup = () => {
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
          lineEl.setAttribute('aria-checked', checked ? 'true' : 'false');
        }
      });
      _updateHeaderCbState(headerCb, group.lines, s);
      refreshBadge(fileObj.name);
      updateGenerateBtn();
      markSliderCustom();
      _saveSelections();
    };
    labelEl.addEventListener('click', toggleGroup);
    labelEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleGroup(); }
    });

    groupEl.appendChild(labelEl);

    // ── Individual lines with per-line checkboxes ──────────────────────────
    group.lines.forEach(line => {
      const lineEl = document.createElement('div');
      lineEl.className = 'transcript-line-cb';
      lineEl.dataset.lineRaw = line.raw;
      lineEl.setAttribute('role', 'checkbox');
      lineEl.setAttribute('tabindex', '0');
      lineEl.setAttribute('aria-checked', s.has(line.raw) ? 'true' : 'false');
      lineEl.setAttribute('aria-label', `${line.timestamp} ${line.text}`);

      const lineCb = document.createElement('div');
      lineCb.className = 'cb-box cb-box-line' + (s.has(line.raw) ? ' checked' : '');
      lineCb.textContent = s.has(line.raw) ? '✓' : '';

      const ts = document.createElement('div');
      ts.className = 'ts-cb';
      ts.textContent = line.timestamp;

      const text = document.createElement('div');
      text.className = 'line-text-cb';
      text.textContent = line.text;
      if (line.is_interviewer) {
        lineEl.classList.add('interviewer');
        const tag = document.createElement('span');
        tag.className = 'speaker-tag';
        tag.textContent = 'Interviewer';
        text.insertBefore(tag, text.firstChild);
      }

      lineEl.appendChild(lineCb);
      lineEl.appendChild(ts);
      lineEl.appendChild(text);

      const toggleLine = () => {
        const checked = s.has(line.raw);
        if (checked) {
          s.delete(line.raw);
          lineCb.className = 'cb-box cb-box-line';
          lineCb.textContent = '';
          lineEl.setAttribute('aria-checked', 'false');
        } else {
          s.add(line.raw);
          lineCb.className = 'cb-box cb-box-line checked';
          lineCb.textContent = '✓';
          lineEl.setAttribute('aria-checked', 'true');
        }
        _updateHeaderCbState(headerCb, group.lines, s);
        refreshBadge(fileObj.name);
        updateGenerateBtn();
        markSliderCustom();
        _saveSelections();
      };
      lineEl.addEventListener('click', toggleLine);
      lineEl.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleLine(); }
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
  markSliderCustom();
  _saveSelections();
}

function uncheckAllInFile(filename) {
  if (!state.checked[filename]) return;
  state.checked[filename] = new Set();
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  markSliderCustom();
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
  if (_dragActive) { markSliderCustom(); _saveSelections(); }
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
    scroll.textContent = 'No transcript for this video yet — re-open the folder to transcribe it, or pick another file from the sidebar.';
    return;
  }

  fileObj.lines.forEach(line => {
    const lineEl = document.createElement('div');
    const isHl = state.highlighted[fileObj.name].has(line.raw);
    lineEl.className = 'transcript-line-hl' + (isHl ? ' highlighted' : '');
    lineEl.dataset.raw = line.raw;
    lineEl.setAttribute('role', 'checkbox');
    lineEl.setAttribute('tabindex', '0');
    lineEl.setAttribute('aria-checked', isHl ? 'true' : 'false');
    lineEl.setAttribute('aria-label', `${line.timestamp} ${line.text}`);

    const bar = document.createElement('div');
    bar.className = 'hl-bar';

    const ts = document.createElement('div');
    ts.className = 'ts-hl';
    ts.textContent = line.timestamp;

    const text = document.createElement('div');
    text.className = 'line-text-hl';
    text.textContent = line.text;
    if (line.is_interviewer) {
      lineEl.classList.add('interviewer');
      const tag = document.createElement('span');
      tag.className = 'speaker-tag';
      tag.textContent = 'Interviewer';
      text.insertBefore(tag, text.firstChild);
    }

    lineEl.appendChild(bar);
    lineEl.appendChild(ts);
    lineEl.appendChild(text);

    // Keyboard toggle (mouse uses drag-to-brush below)
    lineEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        const setTo = !state.highlighted[fileObj.name].has(line.raw);
        _applyHighlight(fileObj.name, lineEl, setTo);
        refreshBadge(fileObj.name);
        updateGenerateBtn();
        markSliderCustom();
        _saveSelections();
      }
    });

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
  lineEl.setAttribute('aria-checked', setTo ? 'true' : 'false');
}

function highlightAllInFile(filename) {
  const fileObj = state.files.find(f => f.name === filename);
  if (!fileObj) return;
  fileObj.lines.forEach(l => state.highlighted[filename].add(l.raw));
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  markSliderCustom();
  _saveSelections();
}

function clearHighlightsInFile(filename) {
  if (!state.highlighted[filename]) return;
  state.highlighted[filename] = new Set();
  renderTranscript(filename);
  refreshBadge(filename);
  updateGenerateBtn();
  markSliderCustom();
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
    const icon = document.createElement('span');
    icon.className = 'recent-folder-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2.2H19.5A1.5 1.5 0 0 1 21 8.7v9.3a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18V6.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    const label = document.createElement('span');
    label.className = 'recent-folder-label';
    label.textContent = `${name}/`;
    nameSpan.appendChild(icon);
    nameSpan.appendChild(label);
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
  // The .mp4 suffix is a fixed, non-editable adornment; strip any stray extension the
  // user pasted, then append the real output extension.
  const outputBase = $('output-filename').value.trim().replace(/\.mp4$/i, '') || 'sizzle_reel';
  const outputFilename = outputBase + '.mp4';

  showScreen('screen-generating');
  $('gen-log').innerHTML = '';
  setBar('gen-bar', 0);
  $('gen-error').classList.add('hidden');
  $('btn-cancel-gen').textContent = 'Cancel';
  announce('Generating your reel…');
  $('topbar-controls').classList.add('hidden');

  // In cloud mode, try the browser encode path if WebCodecs is available. This
  // moves the heavy H.264/AAC encode off the free-plan server into the user's
  // hardware encoder. Any failure falls through to the unchanged server pipeline.
  if (APP_MODE === 'cloud' && window.ReelEncoder?.isSupported()) {
    try {
      await _submitGenerateBrowser(mode, selections, prompt, outputFilename);
      return;
    } catch (err) {
      if (err.name === 'AbortError') {
        // User cancelled — don't fall through to the server.
        showScreen('screen-workspace');
        $('topbar-controls').classList.remove('hidden');
        return;
      }
      appendLog('gen-log', `⚠ Browser encode failed (${err.message}) — retrying on server…`);
      $('gen-log').innerHTML = '';
      setBar('gen-bar', 0);
    }
  }

  // Server path (local mode, unsupported browser, or browser fallback).
  await _submitGenerateServer(mode, selections, prompt, outputFilename);
}

async function _submitGenerateServer(mode, selections, prompt, outputFilename) {
  let resp, jobData;
  try {
    resp = await fetch(GENERATOR_URL + '/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        folder: state.folder,
        session_key: state.folder,
        mode,
        selections,
        prompt,
        output_filename: outputFilename,
      }),
    });
    jobData = await resp.json();
  } catch (err) {
    _showGenError(`Could not reach generator service: ${err.message}`);
    return;
  }

  const { job_id, error } = jobData;
  if (!resp.ok) {
    _showGenError(error || 'Failed to start generation');
    return;
  }

  state.currentJobId = job_id;
  watchGeneration(job_id);
}

async function _submitGenerateBrowser(mode, selections, prompt, outputFilename) {
  const controller = new AbortController();
  _genTerminated = false;

  // Cancel tears down the AbortController — there is no server job to DELETE.
  $('btn-cancel-gen').onclick = () => {
    _genTerminated = true;
    controller.abort();
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  };

  // POST /plan — get the ordered segment list and presigned URLs.
  appendLog('gen-log', '· Planning segments…');
  const planResp = await fetch(GENERATOR_URL + '/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_key: state.folder,
      mode,
      selections,
      prompt,
      output_filename: outputFilename,
    }),
    signal: controller.signal,
  });
  if (!planResp.ok) {
    const body = await planResp.json().catch(() => ({}));
    throw new Error(body.error || `Plan failed: ${planResp.status}`);
  }
  const plan = await planResp.json();
  plan.prompt = prompt;

  setBar('gen-bar', 5);

  // ReelEncoder.generate drives the encode, R2 upload, and library record.
  const result = await window.ReelEncoder.generate(plan, {
    onLog: (msg) => appendLog('gen-log', msg),
    onProgress: (done, tot) => {
      const pct = tot > 0 ? Math.round((done / tot) * 100) : 0;
      setBar('gen-bar', Math.max(pct, 5));
    },
    signal: controller.signal,
    generatorUrl: GENERATOR_URL,
  });

  if (_genTerminated) return; // cancelled mid-encode

  // result: { entry_id, filename, duration_seconds, clip_count, segment_starts }
  _genTerminated = true;
  setBar('gen-bar', 100);
  state.resultJobId = null; // no server job — playback goes via library-video
  _clearSelections();
  announce('Your reel is ready.');

  showResult({ ...result, download_url: null });

  // Auto-save (uses entry_id when jobId is null — see _autoSaveReelResult).
  if (result.entry_id) _runAutoSave(null, result.filename, result.entry_id);
}

function watchGeneration(jobId) {
  const wsUrl = withWsToken(GENERATOR_URL.replace(/^http/, 'ws') + `/ws/job/${jobId}`);
  _genTerminated = false;
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
      setBar('gen-bar', Math.max(pct, 5));
    } else if (msg.type === 'done') {
      _genWs = null;
      _handleGenerationTerminal(jobId, msg.status, msg.result, msg.error);
    }
  };

  // If the socket drops before the job reaches a terminal state — which is
  // exactly what happens when a long, silent stitch+upload phase lets the host
  // idle-close the connection — fall back to HTTP polling instead of giving up.
  // Without this the progress bar freezes at "finalizing" forever even though
  // the job may still finish (or already have finished) server-side.
  _genWs.onerror = () => {
    _genWs = null;
    if (!_genTerminated) _startGenerationPolling(jobId);
  };

  _genWs.onclose = () => {
    if (_genWs !== null) {
      _genWs = null;
      if (!_genTerminated) _startGenerationPolling(jobId);
    }
  };

  $('btn-cancel-gen').onclick = async () => {
    _genTerminated = true;
    _stopGenerationPolling();
    await fetch(`${GENERATOR_URL}/jobs/${jobId}`, { method: 'DELETE' });
    if (_genWs) {
      _genWs.close();
      _genWs = null;
    }
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  };
}

// Terminal handling for a generation job — shared by the WebSocket 'done' frame
// and the HTTP polling fallback so both paths reach an identical end state.
// Idempotent: only the first caller for a given job wins.
function _handleGenerationTerminal(jobId, status, result, error) {
  if (_genTerminated) return;
  _genTerminated = true;
  _stopGenerationPolling();

  if (status === 'done') {
    setBar('gen-bar', 100);
    state.resultJobId = jobId;
    _clearSelections();
    announce('Your reel is ready.');
    showResult(result);
    if (APP_MODE === 'cloud' && result && result.entry_id) {
      _runAutoSave(jobId, result.filename, result.entry_id);
    }
  } else if (status === 'error') {
    _showGenError(error);
  } else if (status === 'cancelled') {
    showScreen('screen-workspace');
    $('topbar-controls').classList.remove('hidden');
  }
}

function _stopGenerationPolling() {
  if (_genPollTimer !== null) {
    clearInterval(_genPollTimer);
    _genPollTimer = null;
  }
}

// Poll GET /status/<job_id> until the job reaches a terminal state. Used only
// as a fallback when the progress WebSocket drops mid-job.
function _startGenerationPolling(jobId) {
  _stopGenerationPolling();
  appendLog('gen-log', '⟳ Live connection dropped — checking progress…');
  let shownLogLen = null; // seeded on first poll so we don't re-print WS logs
  let missCount = 0;      // consecutive network/HTTP failures

  _genPollTimer = setInterval(async () => {
    if (_genTerminated) { _stopGenerationPolling(); return; }
    let resp;
    try {
      resp = await fetch(`${GENERATOR_URL}/status/${jobId}`);
    } catch {
      if (++missCount >= 5) {
        _showGenError('Lost contact with the generator service. It may still be finishing — check the Library shortly.');
      }
      return;
    }
    if (resp.status === 404) {
      _showGenError('Generation job is no longer available on the server.');
      return;
    }
    if (!resp.ok) { missCount++; return; }
    missCount = 0;

    const data = await _safeJson(resp);
    const log = data.log || [];
    if (shownLogLen === null) {
      shownLogLen = log.length; // assume the WS already printed these
    } else if (log.length > shownLogLen) {
      log.slice(shownLogLen).forEach(line => appendLog('gen-log', line));
      shownLogLen = log.length;
    }
    if (data.total > 0) {
      const pct = Math.round((data.done / data.total) * 100);
      setBar('gen-bar', Math.max(pct, 5));
    }

    if (data.status === 'done' || data.status === 'error' || data.status === 'cancelled') {
      _handleGenerationTerminal(jobId, data.status, data.result, data.error);
    }
  }, 2500);
}

// Result-screen save-status pill. Split from the Open Folder button so "it's
// saved" reads as status and "open it" reads as an action. `kind` drives the
// color class; the text always carries a word (never color alone — WCAG 1.4.1).
const _SAVE_ICONS = {
  saved:  '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  failed: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 8v5m0 3h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="12" r="8.25" stroke="currentColor" stroke-width="1.6"/></svg>',
};
function _setSaveStatus(kind, text) {
  const pill = $('result-save-pill');
  pill.classList.remove('saved', 'failed', 'hidden');
  if (kind === 'hidden') { pill.classList.add('hidden'); pill.replaceChildren(); return; }
  if (kind === 'saved' || kind === 'failed') pill.classList.add(kind);
  pill.innerHTML = _SAVE_ICONS[kind] || '';   // trusted constant markup only
  const span = document.createElement('span');
  span.textContent = text;                    // folder name via textContent, never innerHTML
  pill.appendChild(span);
}

// Shared auto-save flow for the result screen — drives the save pill and the
// Open Folder / Download button together. Used by both the browser-encode and
// server-generation success paths so their end state is identical.
function _runAutoSave(jobId, filename, entryId) {
  _setSaveStatus('saving', 'Saving…');
  const openBtn = $('btn-open-folder');
  openBtn.disabled = true;
  return _autoSaveReelResult(jobId, filename, entryId)
    .then(saved => {
      openBtn.disabled = false;
      if (saved) {
        _setSaveStatus('saved', `Saved to ${saved.folderName}`);
        openBtn.textContent = 'Open Folder';
        openBtn.style.display = '';
        openBtn.dataset.savedPath = saved.localFolderPath || '';
        openBtn.dataset.savedFilename = filename;
      } else {
        _setSaveStatus('failed', 'Not saved');
        openBtn.textContent = 'Download';
        openBtn.style.display = state.resultDownloadUrl ? '' : 'none';
      }
    })
    .catch(() => {
      openBtn.disabled = false;
      _setSaveStatus('failed', 'Not saved');
      openBtn.textContent = 'Download';
      openBtn.style.display = state.resultDownloadUrl ? '' : 'none';
    });
}

// Surface a generation failure on the generating screen instead of leaving the
// user staring at a frozen progress bar. The Cancel button becomes the way back.
function _showGenError(msg) {
  _genTerminated = true;
  _stopGenerationPolling();
  const box = $('gen-error');
  box.textContent = `Couldn't finish this reel: ${msg || 'unknown error'}`;
  box.classList.remove('hidden');
  appendLog('gen-log', `✗ ${msg || 'unknown error'}`);
  announce('Reel generation failed.');
  $('btn-cancel-gen').textContent = 'Back to editing';
  $('topbar-controls').classList.remove('hidden');
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
  // Browser path has no server job — serve from library-video directly.
  const src = state.resultJobId
    ? `${GENERATOR_URL}/video/${state.resultJobId}`
    : `${GENERATOR_URL}/library-video/${result.entry_id}`;
  $('result-source').src = src;
  $('result-video').load();

  // Captions: show the CC toggle only when this reel actually has a track.
  // /library-captions returns 200 (local sidecar or cloud key) or 404, so one
  // tiny GET decides it — simpler than threading a flag through both the cloud
  // (reel-encoder) and local (server job) result paths.
  const resTrack = $('result-track');
  const resCc = $('btn-result-cc');
  resTrack.removeAttribute('src');
  resCc.classList.add('hidden');
  if (result.entry_id) {
    const capUrl = `${GENERATOR_URL}/library-captions/${result.entry_id}`;
    fetch(capUrl).then(r => {
      if (!r.ok) return;
      resTrack.src = capUrl;
      resCc.classList.remove('hidden');
      _applyCcState('result-video', 'btn-result-cc', _captionsOn());
    }).catch(() => {});
  }

  $('result-filename').textContent = result.filename;
  const dur = Math.max(0, Math.round(result.duration_seconds || 0));
  const mins = Math.floor(dur / 60);
  const secs = dur % 60;
  $('result-duration').textContent = `${mins}:${String(secs).padStart(2,'0')}`;
  const n = result.clip_count || 0;
  $('result-clipcount').textContent = `${n} clip${n === 1 ? '' : 's'}`;

  // In cloud mode the "Open Folder" button becomes a download link (there is no
  // local folder to open).  If the R2 upload failed, hide the button entirely.
  const openBtn = $('btn-open-folder');
  if (APP_MODE === 'cloud') {
    // Cloud auto-save (when entry_id exists) drives the pill/button via
    // _runAutoSave; until then show the download fallback and no save claim.
    _setSaveStatus('hidden');
    if (state.resultDownloadUrl) {
      openBtn.textContent = 'Download';
      openBtn.style.display = '';
    } else {
      openBtn.style.display = 'none';
    }
  } else {
    // Local mode: the server wrote the reel to the folder during generation,
    // so it is already saved by the time we land here.
    _setSaveStatus('saved', 'Saved to folder');
    openBtn.textContent = 'Open Folder';
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
          const objUrl = URL.createObjectURL(await fh.getFile());
          window.open(objUrl, '_blank');
          setTimeout(() => URL.revokeObjectURL(objUrl), 100);
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
  try {
    const resp = await fetch(GENERATOR_URL + '/library');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    state.libraryEntries = await resp.json();
  } catch (err) {
    $('library-count').textContent = 'Generated Reels';
    const grid = $('library-grid');
    grid.innerHTML = '<div class="library-empty">Could not load library — is the generator service running?</div>';
    return;
  }
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
    empty.textContent = 'No reels yet. Open a folder in the Create tab, describe the moments you need, and generate your first reel — it will appear here.';
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
    thumb.innerHTML = `<div class="reel-play-icon"><svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7-11-7Z"/></svg></div><div class="reel-duration">${durStr}</div>`;
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

  const downloadBtn = document.createElement('button');
  downloadBtn.className = 'reel-btn-icon';
  downloadBtn.title = 'Download';
  downloadBtn.setAttribute('aria-label', 'Download');
  downloadBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M12 4v10m0 0l-4-4m4 4l4-4M5 19h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  const editBtn = document.createElement('button');
  editBtn.className = 'reel-btn-icon';
  editBtn.title = 'Edit';
  editBtn.setAttribute('aria-label', 'Edit');
  editBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M14.5 5.5l4 4M4.5 19.5l1-4L16 5a2 2 0 0 1 3 3L8.5 18.5l-4 1Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>';

  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'reel-btn-icon';
  deleteBtn.title = 'Delete';
  deleteBtn.setAttribute('aria-label', 'Delete');
  deleteBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M5 7h14M10 7V5h4v2M7.5 7l.8 12h7.4l.8-12" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  iconRow.appendChild(downloadBtn);
  if (entry.captions_key || entry.captions_filename) {
    const capBtn = document.createElement('button');
    capBtn.className = 'reel-btn-icon';
    capBtn.title = 'Download with captions';
    capBtn.setAttribute('aria-label', 'Download with captions');
    capBtn.textContent = 'CC↓';
    capBtn.style.cssText = 'font-size:11px;font-weight:700';
    capBtn.addEventListener('click', () => _downloadCaptioned(entry, capBtn));
    iconRow.appendChild(capBtn);
  }
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
  playBtn.textContent = 'Play';

  const showBtn = document.createElement('button');
  showBtn.className = 'reel-btn show';
  showBtn.dataset.id = entry.id;
  showBtn.dataset.path = entry.path || '';

  if (APP_MODE === 'cloud') {
    const dlInfo = _getDownload(entry.id);
    if (dlInfo && dlInfo.localFolderPath) {
      showBtn.textContent = 'Show';
    } else if (dlInfo) {
      showBtn.textContent = 'View';
    } else {
      showBtn.textContent = 'Show';
    }
  } else {
    showBtn.textContent = 'Show';
  }

  actions.appendChild(playBtn);
  actions.appendChild(showBtn);
  body.appendChild(actions);

  // Event listeners
  playBtn.addEventListener('click', () => openLibraryPlayer(entry));

  showBtn.addEventListener('click', async () => {
    if (APP_MODE === 'cloud') {
      const info = _getDownload(entry.id);
      if (info) {
        if (info.localFolderPath) {
          await fetch(GENERATOR_URL + '/open-folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              folder: info.localFolderPath,
              file_path: info.localFolderPath + '\\' + info.filename,
            }),
          });
        } else {
          const handle = await _idbLoad('sizzle_output_dir_handle').catch(() => null);
          if (handle) {
            try {
              const fh = await handle.getFileHandle(info.filename);
              const objUrl = URL.createObjectURL(await fh.getFile());
              window.open(objUrl, '_blank');
              setTimeout(() => URL.revokeObjectURL(objUrl), 100);
              return;
            } catch { /* fall through to proxy */ }
          }
          window.open(`${GENERATOR_URL}/library-video/${entry.id}`, '_blank');
        }
      } else {
        _showNotDownloadedModal(entry);
      }
      return;
    }
    const folder = (entry.path || '').replace(/[\\/][^\\/]+$/, '');
    await fetch(GENERATOR_URL + '/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, file_path: entry.path }),
    });
  });

  deleteBtn.addEventListener('click', () => _showDeleteConfirm(body, card, entry, dateStr, actions));

  editBtn.addEventListener('click', () => _showEditForm(body, card, entry, dateStr));

  downloadBtn.addEventListener('click', () => {
    const a = document.createElement('a');
    a.href = `${GENERATOR_URL}/library-video/${entry.id}?download=1`;
    a.download = entry.filename || 'reel.mp4';
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
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
    cancelBtn.disabled = true;
    const url = `${GENERATOR_URL}/library/${entry.id}` + (deleteFile ? '?delete_file=true' : '');
    try {
      const resp = await fetch(url, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      libOnly.disabled = false;
      withFile.disabled = false;
      cancelBtn.disabled = false;
      alert(`Delete failed: ${err.message}`);
      return;
    }
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

function _captionsOn() {
  return localStorage.getItem('sizzle_captions_on') === '1';
}

function _applyCcState(videoId, btnId, on) {
  const track = $(videoId).textTracks[0];
  if (track) track.mode = on ? 'showing' : 'hidden';
  const btn = $(btnId);
  btn.classList.toggle('active', on);
  btn.setAttribute('aria-pressed', on ? 'true' : 'false');
}

// Wire a CC button to the shared `sizzle_captions_on` preference so the choice
// is remembered and consistent across the result screen and the library player.
function _wireCcButton(btnId, videoId) {
  $(btnId).addEventListener('click', () => {
    const next = !_captionsOn();
    localStorage.setItem('sizzle_captions_on', next ? '1' : '0');
    _applyCcState(videoId, btnId, next);
  });
}
_wireCcButton('btn-result-cc', 'result-video');

async function _downloadCaptioned(entry, btn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    if (APP_MODE === 'cloud' && window.ReelEncoder?.isSupported()) {
      // Fetch the reel URL + VTT, burn in-browser, download the blob.
      const vtt = await fetch(`${GENERATOR_URL}/library-captions/${entry.id}`).then(r => r.text());
      const reelUrl = `${GENERATOR_URL}/library-video/${entry.id}`;
      const blob = await window.ReelEncoder.burnCaptions(reelUrl, vtt, {
        onLog: () => {},
        onProgress: (d, t) => { btn.textContent = `${Math.round((d / t) * 100)}%`; },
      });
      _downloadBlob(blob, `${(entry.title || entry.filename).replace(/\.mp4$/, '')}_captioned.mp4`);
    } else {
      // Local mode: server ffmpeg burn-in, streamed as an attachment.
      const resp = await fetch(`${GENERATOR_URL}/library/${entry.id}/download-captioned`, { method: 'POST' });
      if (!resp.ok) throw new Error(`Burn-in failed (${resp.status})`);
      const blob = await resp.blob();
      _downloadBlob(blob, `${(entry.title || entry.filename).replace(/\.mp4$/, '')}_captioned.mp4`);
    }
  } catch (err) {
    alert(`Could not create captioned download: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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

  // Captions: only wire the track + CC button when this reel has a caption file.
  const hasCaptions = !!(entry.captions_key || entry.captions_filename);
  const trackEl = $('library-track');
  const ccBtn = $('btn-lib-cc');
  if (hasCaptions) {
    trackEl.src = `${GENERATOR_URL}/library-captions/${entry.id}`;
    ccBtn.classList.remove('hidden');
  } else {
    trackEl.removeAttribute('src');
    ccBtn.classList.add('hidden');
  }

  // Show the overlay before calling load() — browsers defer loading media in
  // display:none elements, so the video must be visible before we trigger load.
  _openModal('library-player-overlay', 'btn-close-player');
  $('library-video').src = src;
  $('library-video').load();

  // The text track exists once the <track> is in the DOM; apply the remembered
  // on/off state so CC is consistent across reels.
  if (hasCaptions) _applyCcState('library-video', 'btn-lib-cc', _captionsOn());
}

_wireCcButton('btn-lib-cc', 'library-video');

$('btn-close-player').addEventListener('click', () => {
  $('library-video').pause();
  $('library-video').src = '';
  _closeModal('library-player-overlay');
});

// Startup: in cloud mode without a token, gate on the login screen and defer the
// bootstrap fetches (they hit protected endpoints). Local mode / authed → run now.
function startApp() {
  hideLoginScreen();
  // Load recent folders on startup
  loadRecentFolders();
  // Wake the generator service (Render free tier sleeps after ~15 min idle) so
  // it's usually up by the time the user opens a folder or generates a reel.
  fetch(GENERATOR_URL + '/library').catch(() => {});
}

if (APP_MODE === 'cloud' && !AUTH_TOKEN) {
  showLoginScreen();
} else {
  startApp();
}

document.getElementById('login-btn')?.addEventListener('click', async () => {
  const err = document.getElementById('login-error');
  const ok = await doLogin(
    document.getElementById('login-user').value.trim(),
    document.getElementById('login-pass').value,
  );
  if (ok) { err.classList.add('hidden'); startApp(); }
  else { err.classList.remove('hidden'); }
});

document.getElementById('login-pass')?.addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('login-btn').click();
});

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
      btn.textContent = s.name + '/';
      btn.title = s.name;
      btn.addEventListener('click', async () => {
        _closeFolderDropdown();
        await openFolder(s.folder, s.name);
      });
      dropdown.appendChild(btn);
    });

    const newBtn = document.createElement('button');
    newBtn.className = 'dropdown-new-folder';
    newBtn.textContent = 'Upload new files…';
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
      btn.textContent = entry.path.split(/[\\/]/).pop() + '/';
      btn.title = entry.path;
      btn.addEventListener('click', () => {
        _closeFolderDropdown();
        openFolder(entry.path);
      });
      dropdown.appendChild(btn);
    });

    const newBtn = document.createElement('button');
    newBtn.className = 'dropdown-new-folder';
    newBtn.textContent = 'Select new folder…';
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
      // Step 1: validate filenames and get one presigned PUT URL per file.
      btnLoad.textContent = 'Preparing upload…';
      const prepResp = await fetch('/upload/prepare', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ files: selectedFiles.map(f => f.name) }),
      });
      const prepData = await _safeJson(prepResp);
      if (!prepResp.ok) {
        folderErr.textContent = prepData.error || `Upload preparation failed (${prepResp.status})`;
        folderErr.classList.remove('hidden');
        return;
      }

      const session_key = prepData.session_key;
      const uploads = prepData.uploads || {};

      // Step 2: upload each file DIRECTLY to R2 via its presigned PUT URL.
      // Bytes go browser → R2 and never transit this host, so large videos
      // can't hit the host's request body-size limit or its metered bandwidth.
      $('transcribe-subtitle').textContent = `Uploading ${selectedFiles.length} files…`;
      setBar('transcribe-bar', 0);
      $('transcribe-log').textContent = '';
      showScreen('screen-transcribing');

      for (let i = 0; i < selectedFiles.length; i++) {
        const file = selectedFiles[i];
        $('transcribe-log').textContent = `⟳ ${file.name} (${i + 1} / ${selectedFiles.length})`;
        const putUrl = uploads[file.name];
        if (!putUrl) {
          throw new Error(`No upload URL was issued for ${file.name}`);
        }
        const resp = await fetch(putUrl, { method: 'PUT', body: file });
        if (!resp.ok) {
          throw new Error(`Failed to upload ${file.name}: ${resp.status}`);
        }
        const pct = Math.round(((i + 1) / selectedFiles.length) * 100);
        setBar('transcribe-bar', pct);
        $('transcribe-log').textContent = `✓ ${file.name} (${i + 1} / ${selectedFiles.length})`;
      }

      // Step 3: tell the server all uploads are done
      $('transcribe-subtitle').textContent = 'Finalising…';
      const commitResp = await fetch('/upload/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_key, files: selectedFiles.map(f => f.name) }),
      });
      const commitData = await _safeJson(commitResp);
      if (!commitResp.ok) {
        showScreen('screen-folder-picker');
        folderErr.textContent = commitData.error || `Upload commit failed (${commitResp.status})`;
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
    // Dedupe by folder NAME, not the session key. Every upload mints a fresh
    // session_key (sessions/<uuid>), so deduping by that would never match and
    // re-uploads of the same folder would stack up as duplicates pointing at
    // stale sessions. Keying on the name collapses re-uploads onto one entry
    // that always carries the newest session_key, video count, and timestamp.
    const sessions = JSON.parse(localStorage.getItem(key) || '[]')
      .filter(s => s.name !== name);
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
      nameSpan.textContent = `${s.name}/`;
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
