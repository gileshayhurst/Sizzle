# Optimization Audit — 2026-07-13

Report only. No fixes applied. Ranked by impact; each finding has file:line.
Sources: impeccable `audit` pass (scored) + independent manual pass (contrast math
computed from tokens, ARIA/keyboard inspection, register check vs PRODUCT.md).

## Part 1 — Design audit

### Health score (impeccable audit dimensions, 0–4)

| # | Dimension | Score | Key finding |
|---|-----------|-------|-------------|
| 1 | Accessibility | 3 | Mode-toggle active pill fails AA (2.97:1); modals lack dialog semantics |
| 2 | Performance | 3 | Indeterminate progress bar animates `margin-left` |
| 3 | Responsive | 3 | Strong structural reflow + 44px coarse-pointer targets; no topbar rule for narrow phones |
| 4 | Theming | 3 | Full token system; repeated focus-ring literals and inline modal styles |
| 5 | Anti-patterns | 4 | No AI-slop tells; hl-bar is the sanctioned ≤4px selection marker; emoji chrome fully retired |
| **Total** | | **16/20** | **Good — address the High items, then polish** |

**Anti-patterns verdict: PASS.** No gradient text, no glassmorphism, no side-stripe
accents (the 3px highlight bar is DESIGN.md's sanctioned selection affordance), no
nested cards, no emoji nav (✓/✗/⚠ log glyphs are functional, paired with color +
shape). Uppercase micro-labels are field/region labels, within the Rationed Eyebrow
Rule. The Bright Studio system is applied consistently and reads native to Forven.

### High

1. **Mode toggle active state fails AA and violates DESIGN.md's own rule** —
   [style.css:188](../../static/style.css) `.mode-btn.active` sets white text on
   `--amber` (#E07B39) at 11px = **2.97:1** (needs 4.5:1). DESIGN.md: "Bright Studio
   Amber is never a white-text fill." Fix: amber-tint bg + amber-ink text (match
   `.nav-tab.active`), or `--amber-strong` bg (4.72:1).
2. **Analyze gives no feedback when zero moments match** — app.js `runAnalyze`/
   `runAddAnalyze` (~[app.js:879](../../static/app.js)–917): on a successful call
   that matches nothing, the button just reverts to "Analyze" and nothing visibly
   happens. Violates PRODUCT.md "Trust the output" / honest state feedback; a client
   under deadline reads it as "broken." Fix: a visible "No matching moments found —
   try rephrasing" state.

### Medium

3. **Muted text below DESIGN.md's ≥12px floor** — DESIGN.md holds `--muted` to
   ≥12px, but: timestamps `.ts-cb`/`.ts-hl` 11px ([style.css:445](../../static/style.css), :464),
   `.mode-btn` 11px (:184), `.reel-slider-ends` 10px (:1105), `.speaker-tag` 10px
   (:1010), `.reel-duration` 10px (:569), and `#analyze-error` inline 10px
   ([index.html:140](../../templates/index.html)). Contrast passes (4.57–5.0:1) but the size floor is
   systematically breached; the 10px error text is the worst offender.
4. **Modals lack dialog semantics** — `.overlay` divs ([index.html:246](../../templates/index.html), :262,
   :278) have no `role="dialog"`, `aria-modal`, focus trap, or Escape-to-close
   (Escape is wired only for the folder dropdown). Keyboard/SR users can tab behind
   the scrim.
5. **Prompt-history delete buttons fail non-text contrast** —
   [style.css:950](../../static/style.css) `.ph-delete-btn` colors an icon-only control `--faint`
   (#9FADBE, 2.28:1); WCAG 1.4.11 requires ≥3:1 for UI components. Use `--muted`
   at rest.
6. **Empty states don't teach** — "No reels generated yet." ([app.js:1909](../../static/app.js)) and
   "No transcript available." (:1082) are dead ends; the product register calls for
   empty states that point to the next action (e.g. "Generate your first reel from
   the Create tab").
7. **No captions on video playback** — [index.html:207](../../templates/index.html) and :249 `<video>`
   elements have no `<track>` despite the product owning a transcript for every
   clip (WCAG 1.2.2). A generated-WebVTT track is a natural future win.

### Low / polish

8. **Tabs ARIA incomplete** — [index.html:29](../../templates/index.html)–32: `role="tab"` without
   `aria-controls`, no `role="tabpanel"` on panels, no arrow-key navigation.
9. **History toggle lacks `aria-expanded`** — [index.html:119](../../templates/index.html); panel
   open/closed state is invisible to SRs.
10. **Indeterminate progress bar animates `margin-left`** —
    [style.css:310](../../static/style.css)–317; use `transform: translateX()` to stay off the
    layout path.
11. **Focus-ring literal repeated ~12×** — `rgba(224,123,57,.2…28)` appears
    throughout style.css; tokenize as `--focus-ring` so the alpha stops drifting
    (.20/.22/.25/.28 all exist today).
12. **Topbar has no narrow-viewport rule** — folder badge + mode toggle + tabs can
    crowd below ~480px ([style.css:126](../../static/style.css)); consider hiding the badge text or
    collapsing the toggle on phones.
13. **Inline styles in modal markup** — [index.html:263](../../templates/index.html)–288 hard-code
    sizes/spacing in `style=""` attributes (colors do use tokens); move to classes
    for consistency.

### Positive findings (keep doing these)

- Complete token system, and the core pairs genuinely clear AA (body 7.29:1,
  muted 5.0:1 on surface, amber-ink on tint 4.68:1, stage-ink 16.1:1).
- Transcript rows are real keyboard citizens: `role="checkbox"`, `aria-checked`,
  `tabindex`, Enter/Space handlers, visible inset focus rings.
- Global `prefers-reduced-motion` collapse; determinate progress animation is
  correctly width-based real progress.
- Coarse-pointer media query enforces 44px touch targets without bloating desktop.
- Cool low-alpha shadows, one type family, semantic z-index scale, self-hosted
  preloaded font — all per DESIGN.md.

### Recommended next commands (priority order)

1. `/impeccable polish` — items 1, 3, 5 (contrast + size-floor fixes are mechanical).
2. `/impeccable harden` — items 2, 4, 6 (zero-match state, dialog semantics, empty states).
3. `/impeccable adapt` — item 12 (narrow topbar).

## Part 2 — Code health

(filled by Task 4)

## Part 3 — Repo hygiene flags

(filled by Task 4)
