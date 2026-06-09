# Design: Crossfade Transitions, Library Sort, Prompt History

**Date:** 2026-06-09

---

## Feature 1 — Fade-through-black transitions

### Goal
Add a 2-second fade-to-black at the end of each content clip, and a 2-second fade-from-black at the start of each title card. This produces a clean "fade through black" transition between every clip and the title card that follows it.

### Approach
Chosen: **Fade-to-black / fade-from-black** (not xfade overlap dissolve). Rationale: the existing stitch step uses the concat demuxer with `-c copy`, which only works cleanly with independently-encoded, zero-timestamp clips. Overlapping crossfades would require a complex chained filtergraph and re-encoding the entire final output — high complexity, audio sync risk. Fades baked into individual clips require zero changes to `stitch_clips` and carry no sync risk.

### Changes

**`video_editor.py` — `extract_clip`**
- Add optional param `fade_out_secs: float = 0.0`
- When non-zero, append video filter `fade=t=out:st=<fade_start>:d=<fade_out_secs>` and audio filter `afade=t=out:st=<fade_start>:d=<fade_out_secs>` to the ffmpeg command
- `fade_start = max(0.0, (end_sec - start_sec) - fade_out_secs)` — clamped so short clips get a proportional fade
- Filters are combined with the existing `-vf` flag

**`generator_app.py` — `make_title_card`**
- Add optional param `fade_in_secs: float = 0.0`
- When non-zero, append `fade=t=in:st=0:d=<fade_in_secs>` (video) and `afade=t=in:st=0:d=<fade_in_secs>` (audio) to the drawtext filter chain

**`generator_app.py` — `_run_generation`**
- Pass `fade_out_secs=2.0` to every `extract_clip` call
- Pass `fade_in_secs=2.0` to every `make_title_card` call

### Non-changes
- `stitch_clips` — unchanged
- Audio sync — unchanged; fades are baked into each clip's own timeline before concat
- Title-card-to-clip boundary — hard cut (no fade-in on clips, no fade-out on title cards)

---

## Feature 2 — Library sort dropdown

### Goal
Let users re-order the library grid by: Newest (default), Oldest, Most clips, Fewest clips.

### Approach
Client-side sort only. The library is fetched once and held in `state.libraryEntries`. A sort dropdown re-renders the grid without re-fetching. No backend changes needed.

### Changes

**`templates/index.html`**
- Add `<select id="library-sort">` above the library grid with options:
  - `newest` — Date created: Newest (default)
  - `oldest` — Date created: Oldest
  - `most-clips` — Most clips
  - `fewest-clips` — Fewest clips

**`static/app.js`**
- Add `state.librarySort = 'newest'` and `state.libraryEntries = []`
- Split `loadLibrary()` into:
  - `fetchLibrary()` — fetches `/library`, stores in `state.libraryEntries`, calls `renderLibrary()`
  - `renderLibrary()` — sorts `state.libraryEntries` per `state.librarySort`, re-renders grid
- `library-sort` change event sets `state.librarySort` and calls `renderLibrary()`
- Sort logic:
  - `newest`/`oldest`: compare `created_at` ISO strings (lexicographic = chronological)
  - `most-clips`/`fewest-clips`: compare `clip_count` numbers

**`static/style.css`**
- Minor styles for the sort dropdown (alignment within library header row)

---

## Feature 3 — Prompt history + named templates

### Goal
A dropdown attached to the analyze prompt input shows the last 10 used prompts and any named templates. Users can select a past prompt to populate the input, save the current prompt as a named template, and delete templates.

### Data schema — `prompt_history.json`
```json
{
  "recent": ["prompt text", ...],
  "templates": [{"name": "Display Name", "text": "prompt text"}]
}
```
- `recent`: newest-first, max 10, deduped (re-using an existing prompt moves it to front)
- `templates`: ordered by insertion, deduped by name

### Backend — `app.py`

**`GET /prompt-history`**
Returns the JSON file, or `{"recent": [], "templates": []}` if missing/corrupt.

**`POST /prompt-history`**
Body: `{"action": "use" | "save_template" | "delete_template", "text": "...", "name": "..."}`
- `use`: prepend `text` to `recent`, dedup, cap at 10, save
- `save_template`: append `{"name": name, "text": text}` to `templates`, dedup by name (update text if name exists), save
- `delete_template`: remove template where `name` matches, save

Thread-safe via a `_prompt_history_lock`.

### Frontend — `static/app.js` + `templates/index.html`

**Layout:** The prompt `<input>` is inside a relative-positioned `.prompt-wrap` container. To its right: a `▾` toggle button that opens/closes the history panel. Also to its right (when input non-empty): a `★` "Save as template" button.

**History panel** (`.prompt-history-panel`, absolutely positioned below the input):
- Section "Recent" — last 10 prompts, click to populate input
- Section "Templates" — named templates, each with click-to-populate and `×` delete button
- Empty state: "No history yet" / "No templates saved"

**Interactions:**
- Panel opens on `▾` click; closes on outside click (document `click` listener, removed when panel is hidden)
- Selecting any item: sets input value, closes panel, does NOT auto-submit
- `★` button click: shows an inline `<input class="template-name-input">` that appears next to the button; pressing Enter or a "Save" button calls `save_template` and hides the name input
- On `POST /analyze`: automatically call `POST /prompt-history` with `action=use` and the current prompt text before firing the analyze request
- Panel is loaded from `GET /prompt-history` each time it opens (keeps it fresh)

**`static/style.css`**
- Styles for `.prompt-wrap`, `.prompt-history-panel`, `.prompt-history-item`, `.template-name-input`

---

## Files changed

| File | Change |
|---|---|
| `video_editor.py` | `extract_clip` gains optional `fade_out_secs` param |
| `generator_app.py` | `make_title_card` gains optional `fade_in_secs`; call sites pass `2.0` |
| `app.py` | `GET /prompt-history`, `POST /prompt-history`, `_prompt_history_lock` |
| `prompt_history.json` | New file, auto-created on first save (gitignored) |
| `templates/index.html` | Library sort `<select>`, prompt history panel markup |
| `static/app.js` | Library sort logic, prompt history dropdown logic |
| `static/style.css` | Sort dropdown and prompt history panel styles |

## Non-changes
- `stitch_clips` in `video_editor.py`
- `sizzle_library.json` schema
- `recent_folders.json`
- Any test files (no new testable backend logic that isn't trivially covered by existing patterns)
