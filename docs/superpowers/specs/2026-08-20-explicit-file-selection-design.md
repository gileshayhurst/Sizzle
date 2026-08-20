# Explicit File Selection — Design

**Date:** 2026-08-20
**Status:** Approved for planning

## Problem

Which videos end up in a reel is decided implicitly, by which files happen to
sit in the opened folder. There is no way to say "not this one" short of
re-opening a different folder or manually unchecking every line in a transcript.

That implicitness costs three things:

1. **Money and time.** `_run_analyze` calls Claude on every transcript in the
   folder, concurrently. A folder of eleven interviews pays for eleven calls
   even when the reel is only ever going to draw on four.
2. **A wrong slider.** The reel-length slider ranks candidates across every
   file. Segments from a file the user has no intention of using still occupy
   the priority order, so the optimal cut and the "N of M segments" label are
   both computed against a set that does not match the user's intent.
3. **Tedium.** Excluding a file by hand means unchecking its lines, and any
   later Analyze run silently puts them back.

The fix is one explicit per-file switch that means the same thing everywhere.

## Non-Goals

- No change to `generator_app.py`, the generation pipeline, clip ordering,
  captions, or output format. The generator only ever sees a `selections` dict;
  excluding a file just removes a key from it.
- No change to the cloud upload path (`upload-filters.js`, the folder/file
  pickers, `/upload-session`). Exclusion happens after upload, client-side.
- No server-side storage of the exclusion set, and no auth work to support one.
- No reordering of files. Inclusion only; the reel still plays per-video in
  folder order.

## Key Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Scope of exclusion | Everything — Analyze skips the file, it leaves the priority order, and it is dropped at generation |
| Re-include after Analyze | Nudge to re-run Analyze; no automatic per-file Claude call |
| Excluded file's transcript | Still readable, dimmed, read-only; its existing selections are preserved |
| Where the filter lives | Client-side `state.included` Set, enforced at three boundaries |
| Pool vs. ordering | `state.pool` keeps every candidate ever scored; only `state.poolOrdered` filters |
| Persistence | `localStorage`, per folder, storing **exclusions** so new files default to on |

---

## Component 1 — State

Add to the `state` object in `static/app.js`:

```js
included: new Set(),   // filenames currently included; all files by default
```

Populated in the folder-load path that already restores selections and calls
`_restorePool()` (`static/app.js:864`–`889`), from the persisted exclusion list.

### Persistence

Key: `sizzle_excl_v1_<folder>`. Value: a JSON array of **excluded** filenames.

Storing exclusions rather than inclusions means a file added to the folder after
the list was written defaults to included, which is the safe direction — a new
file appearing silently switched off would be invisible.

Written by a `_saveIncluded()` helper alongside the existing `_saveSelections()`
/ `_savePool()` pair.

**Unlike selections and the pool, exclusions survive generation.**
`_clearSelections()` wipes `sizzle_sel_v3_` and `sizzle_pool_v3_` after a
successful reel so the next one starts empty; it must **not** touch
`sizzle_excl_v1_`. Which files a folder is about is a property of the folder,
not of one reel — resetting it would make the user re-exclude the same files
before every reel, which is the tedium this feature exists to remove. The only
things that change the set are the per-file checkbox and the all/none button.

### The pool split

`state.pool` continues to hold **every** candidate Claude has ever scored,
including candidates belonging to files that are currently excluded. Only the
derived ordering filters:

```js
const includedOrder = state.files.map(f => f.name).filter(n => state.included.has(n));
state.poolOrdered = sortByPriority(
  state.pool.filter(c => state.included.has(c.file)),
  includedOrder
);
```

This single expression is the whole cohesion mechanism. Because
`cumulativeDurations`, `optimalDuration`, `prefixForDuration` and the slider
label are all pure functions of `poolOrdered`, the slider's range, its optimal
marker, its selected prefix and its "N of M segments" text become correct for
the new file set with no further changes.

It also means re-including a file that *was* scored in an earlier Analyze run
restores its candidates instantly, at no cost. The re-analyze nudge then fires
only for files that genuinely have no candidates.

Extract this into one helper — `_reorderPool()` — called from `runAnalyze`,
`runAddAnalyze`, `_restorePool`, and the toggle handler, so there is exactly one
place the filter is applied.

`_restorePool` currently filters the restored pool down to known filenames
(`static/app.js:392`). It keeps doing that — dropping candidates for files no
longer in the folder — and then calls `_reorderPool()` instead of sorting
directly.

## Component 2 — Analyze

### `app.py`

`POST /analyze` accepts an optional `files` key:

```json
{"folder": "...", "prompt": "...", "files": ["a.mp4", "b.mp4"]}
```

`_run_analyze(folder, prompt, files=None)` filters `video_paths` by the
allow-list immediately after `_filter_generated_reels`, matching on
`Path.name`. When `files` is omitted or `None`, every video is analyzed —
so the existing behaviour, and every existing caller and test, is unchanged.

Filtering after `_filter_generated_reels` rather than before keeps the
generated-reel guard authoritative: a client that named a generated reel in its
allow-list still cannot get it analyzed.

The allow-list is a filter, never a source of paths. Names that do not match a
scanned video are simply ignored; nothing is opened by name.

### `static/app.js`

`_postAnalyze(prompt)` sends `files: [...state.included]`. Both `runAnalyze` and
`runAddAnalyze` inherit this, so an excluded file is never scored by either.

### The re-analyze nudge

After a file is toggled **on**, if a prior Analyze has run (`state.pool.length > 0`)
and the pool holds no candidate for that file, show the existing neutral strip:

```js
_showAnalyzeMsg('1 file has no results yet — re-run Analyze to include it.', true);
```

Pluralised by the count of such files. This reuses `#analyze-error` in its
`notice` styling, so there is no new UI. It is cleared by the next Analyze run
(both analyze paths already hide the strip on entry).

### Empty selection

If `state.included` is empty, both `#btn-analyze` and `#btn-generate` are
disabled. `updateGenerateBtn` gains the included-set filter; a small
`updateAnalyzeBtn` sibling handles the Analyze button, called from the same
places.

## Component 3 — Sidebar

### Markup

`renderSidebar` gains a checkbox as the first child of each `li`, before
`.item-name`:

```html
<li class="sidebar-item excluded">
  <input type="checkbox" class="item-include" checked>
  <div class="item-name">interview_04.mp4</div>
  <div class="item-badge">excluded</div>
</li>
```

The checkbox's `change` handler calls `stopPropagation()` so toggling a file
does not also switch the transcript pane to it. Row click continues to call
`selectFile`.

The existing `.sidebar-header` ("Video Files") gains a count and an all/none
control:

```
Video Files · 4 of 6      [All]
```

The button reads "All" when anything is excluded and "None" when everything is
included, and flips the whole set in one click.

### Toggle handler

One function, called by both the per-file checkbox and the all/none button:

1. Add or remove the name in `state.included`.
2. `_saveIncluded()`.
3. `_reorderPool()`, then `_refreshSlider(currentSliderValue)` if the slider is
   visible — or hide the slider row if fewer than two candidates remain.
4. Re-render the sidebar row's class and badge.
5. If the currently viewed file was the one toggled, re-render the transcript so
   its read-only state updates.
6. `updateGenerateBtn()`, `updateAnalyzeBtn()`.
7. Show the re-analyze nudge if warranted.

### Badge

`updateBadgeEl` returns `excluded` for a file not in `state.included`, ahead of
its existing checked/highlighted branches. The selection counts are not lost —
they are simply not shown while the file is off, and reappear unchanged when it
is switched back on.

### Styling

`.sidebar-item.excluded` dims to the muted token (name and badge both), per
DESIGN.md. The row stays fully clickable and keyboard-reachable — dimming
signals state, it does not remove the affordance.

Contrast: the dimmed text must still clear WCAG AA (4.5:1) against the sidebar
background. Use the existing `--muted` token rather than an opacity value, so
the ratio is the one DESIGN.md already validates.

## Component 4 — Read-only transcript

`renderTranscript(filename)` toggles a `readonly` class on `#transcript-scroll`
when the file is excluded. One CSS rule does the rest:

```css
.transcript-scroll.readonly .transcript-line,
.transcript-scroll.readonly .minute-label-cb { pointer-events: none; }
```

Targeting the children rather than the container disables both the checkbox
clicks and the highlight-mode drag-brush while leaving the container itself
scrollable, so the transcript is still readable. This is one rule instead of
guards in `toggleLine`, `toggleGroup`, the `mousedown` brush handler and the
minute-header handler.

`updateSelectAllBtn` and `updateClearAllBtn` set `disabled` on their buttons
when the active file is excluded.

Selections in `state.checked` / `state.highlighted` for that file are left
untouched throughout.

## Component 5 — Generate

The `#btn-generate` click handler (`static/app.js:1612`) skips files not in
`state.included` when collecting `selections`:

```js
state.files.forEach(f => {
  if (!state.included.has(f.name)) return;
  ...
});
```

`updateGenerateBtn` applies the same filter when deciding whether anything is
selected.

No server change. `generator_app._run_generation` receives a `selections` dict
that simply lacks the excluded keys, which is a shape it already handles — a
folder has always been allowed to contain videos with no selections.

## Error Handling

- **Persisted exclusions naming files no longer present** — ignored on load;
  `state.included` is built from `state.files`, with the persisted list acting
  only as a subtractive filter.
- **A persisted exclusion list covering every file in the folder** — honoured,
  and the disabled Analyze/Generate buttons plus the `0 of 6` header make the
  state legible. No silent auto-recovery, which would contradict a deliberate
  user action.
- **`localStorage` unavailable or full** — the existing `try/catch (_) {}`
  pattern used by `_saveSelections` and `_savePool` applies. Exclusion is a
  convenience; failing to persist it must not break the session.
- **`files` allow-list containing unknown names** — ignored, not an error. A
  stale client sending a name for a deleted video should not fail the whole
  analyze run.

## Testing

### `tests/test_app.py`

- `/analyze` with `files: ["a.mp4"]` in a two-video folder calls `query_claude`
  once and returns segments only for `a.mp4`.
- `/analyze` with no `files` key scores both videos — the regression guard for
  every existing caller.
- `/analyze` with `files` naming a generated reel does not analyze it, proving
  the allow-list cannot defeat `_filter_generated_reels`.

Existing tests mock `query_claude`; these follow the same pattern.

### What is not covered by an automated test

The repo has no JS test framework and this design does not add one, so the
client-side half — `_reorderPool`, the toggle handler, the read-only class, the
generate-payload filter — has no automated coverage. That is a real gap, stated
rather than papered over.

It is an acceptable one here because the risky logic was deliberately kept out
of JS: the slider maths (`sortByPriority`, `cumulativeDurations`,
`optimalDuration`, `prefixForDuration`) is untouched, and `_reorderPool` only
changes which array is handed to it. The failure mode of a bug in that one-line
filter is visible immediately in the sidebar and the slider label, not silent.

If this filter later grows conditions, that is the point to add a JS test
runner rather than keep extending an untested path.

### Manual verification

Local mode, a folder of three videos: exclude one, Analyze, confirm the log
shows two Claude calls; confirm the slider's max drops; re-include and confirm
the nudge appears; re-run Analyze and confirm the slider widens. Generate and
confirm the reel contains no clip from the excluded file.

## Documentation

`CLAUDE.md` gains a Key Behaviours entry: exclusion is enforced at three
boundaries (the `/analyze` allow-list, `poolOrdered`, the generate payload) and
any new code path that consumes `state.files` must filter by `state.included`
or state why it does not — mirroring the existing `_filter_generated_reels`
and `read_transcript` entries, which exist for the same class of mistake.
