# Remove the `.mp4` badge from the output-filename field

**Date:** 2026-07-09
**Status:** Approved for planning

## Problem

The output-filename field in the workspace footer shows a locked `.mp4` badge
(a padlock glyph + `.mp4` text) attached to the right of the input. The base-name
input and automatic `.mp4` appending already work correctly — the badge is a
redundant visual adornment. The user wants it gone, along with every other `.mp4`
reference around this field.

## Goal

Remove all `.mp4` references from the output-filename UI. The field becomes a plain
text input showing only the base name. The reel is still saved as `<name>.mp4`;
that logic is untouched.

## Scope

Purely presentational. No change to generation, naming, numbering, library, or any
backend behavior.

### In scope

1. **`templates/index.html`**
   - Delete the `.filename-ext` badge `<span>` (padlock SVG + `.mp4` text).
   - Delete the `#filename-ext-hint` screen-reader-only `<span>` ("Your reel is
     always saved as an .mp4 video…").
   - Remove `aria-describedby="filename-ext-hint"` from the `#output-filename` input,
     since the element it points to is gone.

2. **`static/style.css`**
   - Remove the `.filename-field .filename-ext` and `.filename-field .filename-ext svg`
     rules.
   - Simplify `.filename-field`: it was a segmented container built to host the badge.
     Without the badge, keep it as a single-border input wrapper (the existing
     `:focus-within` amber ring and the `.filename-input` rule stay, so the input still
     looks and focuses correctly). Remove `overflow: hidden` only if it no longer serves
     a purpose after the badge is gone.

3. **`static/app.js`** — **no change.** `submitGenerate` already strips any stray
   `.mp4` and appends the real extension ([app.js:1184-1185](../../../static/app.js)).
   Default-name generation in `showWorkspace` is unaffected.

### Out of scope

- Any change to how the reel filename is computed or saved.
- The `.mp4` accept filter on the file picker and the video-extension sets in
  `generator_app.py` — those are unrelated to this field.

## Design context

Governed by DESIGN.md ("The Bright Studio"). The change removes a component, so the
main constraint is that the remaining input still honors the existing tokens
(`--surface`, `--border`, `--amber` focus ring, `--radius-sm`, mono font). No new
styles are introduced; we only delete the badge-specific rules and confirm the input
still renders cleanly as a standalone field.

## Verification

- Workspace footer shows the output-filename input with **no** `.mp4` badge and no
  padlock.
- Input still focuses with the amber ring.
- Generating a reel with name `foo` still produces `foo.mp4` (unchanged JS).
- No occurrence of `filename-ext` remains in `index.html` or `style.css`.
- Responsive rule at `style.css:1062` (`.filename-field { width: 100% }`) still applies
  and the field spans full width on narrow viewports.
