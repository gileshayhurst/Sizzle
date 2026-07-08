---
name: Sizzle Reel
description: Light, warm-professional research tool that turns raw footage into presentation-ready highlight reels — a native feature of the Forven / HumanLens platform.
colors:
  studio-amber: "#E07B39"
  amber-hover: "#CC6B2C"
  amber-strong: "#BD5419"
  amber-strong-hover: "#A5470F"
  amber-ink: "#A84D17"
  amber-tint: "#FCE7D3"
  amber-tint-hover: "#F8DBC0"
  amber-border: "#F3D3B4"
  canvas: "#F1F5FB"
  canvas-top: "#FAFCFE"
  canvas-hover: "#E9EEF6"
  surface: "#FFFFFF"
  surface-warm: "#FDF4EC"
  stage: "#0B0F16"
  stage-ink: "#E6ECF3"
  scrim: "#14285080"
  ink: "#1B2A44"
  body: "#46586E"
  muted: "#5F7188"
  faint: "#9FADBE"
  link: "#2563EB"
  success: "#15803D"
  success-tint: "#DCFCE7"
  warning: "#B45309"
  warning-tint: "#FEF3C7"
  danger: "#DC3355"
  danger-tint: "#FDE7EC"
  danger-ink: "#B31E3E"
  danger-border: "#F3C2CD"
  danger-tint-hover: "#FBD5DE"
  border: "#E5EBF3"
  border-strong: "#D2DBE6"
typography:
  display:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "1.375rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.005em"
  title:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "normal"
  body:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "normal"
  label:
    fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.08em"
  mono:
    fontFamily: "'SF Mono', ui-monospace, 'Cascadia Code', monospace"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
rounded:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
  3xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.amber-strong}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.amber-strong-hover}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.body}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-danger:
    backgroundColor: "{colors.danger}"
    textColor: "{colors.surface}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: "8px 18px"
  chip:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.body}"
    typography: "{typography.label}"
    rounded: "{rounded.pill}"
    padding: "4px 12px"
  chip-active:
    backgroundColor: "{colors.amber-tint}"
    textColor: "{colors.amber-ink}"
    rounded: "{rounded.pill}"
    padding: "4px 12px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.body}"
    rounded: "{rounded.lg}"
    padding: "16px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "8px 12px"
  nav-item:
    backgroundColor: "transparent"
    textColor: "{colors.body}"
    typography: "{typography.body}"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
  nav-item-active:
    backgroundColor: "{colors.amber-tint}"
    textColor: "{colors.amber-ink}"
    rounded: "{rounded.pill}"
    padding: "6px 14px"
---

# Design System: Sizzle Reel

## 1. Overview

**Creative North Star: "The Bright Studio"**

Sizzle Reel is a clean, well-lit editing studio for research findings. The room is bright —
pale cool-blue canvas, white cards floating with soft shadows, generous air. One warm light
is on: Studio Amber, the terracotta accent, marks the single thing worth acting on at any
moment. And at the center of the room is the one dark surface that matters — the black video
stage where the work is watched. **Light chrome, dark media.** This is the exact posture of
the parent Forven / HumanLens platform; Sizzle Reel must read as a native feature of it, not
a bolt-on.

The personality is *modern, approachable, clear* (PRODUCT.md). Restraint is the strategy:
surfaces are quiet, hierarchy comes from weight and space rather than color, and the amber
accent earns its rarity by never being decoration. The tool is a client-facing deliverable
engine for a paid market-research service, so it must feel credible and effortless in the
same breath — serious enough to justify the invoice, simple enough for a client who has
never opened a video editor.

This system explicitly rejects three things. It is **not a consumer creator app** (no
TikTok/CapCut emoji chrome, no gamified flourishes — the current 🎬 ✦ 📼 nav is retired). It
is **not an intimidating pro-NLE** (no Premiere/Resolve wall of controls and timelines). And
it is **not a hacker-terminal dashboard** (no neon-on-black, no muted-gray-on-navy body text
that hides in the dark — the outgoing theme's `#3a5070`-on-`#080c14` is the exact failure we
are correcting).

**Key Characteristics:**
- Light, airy canvas; white cards; one dark video stage.
- Single warm accent (Studio Amber) reserved for primary action, active state, and identity.
- Deep navy-slate ink on white; every value clears WCAG AA.
- Generous rounding (16px cards, fully-rounded pills), soft 1px borders, whisper shadows.
- One humanist sans (Inter), fixed rem scale, hierarchy by weight.
- Calm, state-only motion; reduced-motion honored everywhere.

## 2. Colors

A pale cool-blue foundation carrying white task surfaces, with a single warm terracotta
identity accent and one deliberately dark media stage.

### Primary
- **Studio Amber** (`#E07B39` / `oklch(0.685 0.145 52)`): The one warm light in the room.
  Solid fill on primary buttons, active nav/filter pills (as its tint), progress bars, and
  brand moments. Its scarcity is the point — if amber is everywhere, nothing is lit.
  - **Amber Hover** (`#CC6B2C`): pressed/hover state of bright-amber accents.
  - **Amber Strong** (`#BD5419`, hover `#A5470F`): the deeper amber used for *solid CTA
    fills* (primary buttons). Bright Studio Amber fails white-text AA at 2.97:1; this deeper
    burnt orange carries white text at ≥4.5:1 while staying in the same hue family. Reserve
    bright `--amber` for markers, focus rings, progress, and the brand mark.
  - **Amber Ink** (`#A84D17` / `oklch(0.48 0.13 48)`): amber *text* on the amber tint —
    the only amber value legible enough to sit on a light peach pill at AA (≥4.5:1).
  - **Amber Tint** (`#FCE7D3`): active nav pill background, icon tiles, filter/count chips.
  - **Amber Border** (`#F3D3B4`): outline for amber pills and quiet accent edges.

### Neutral
- **Canvas** (`#F1F5FB` / `oklch(0.972 0.008 250)`): the page background, a pale cool blue.
  Pairs with **Canvas Top** (`#FAFCFE`) as a soft top-down page gradient.
- **Surface** (`#FFFFFF`): every card, panel, input, and toolbar. The working surfaces sit
  brighter than the canvas they float on.
- **Surface Warm** (`#FDF4EC`): a barely-there warm tint used only as a card-bottom gradient
  wash, echoing Forven's cards. Never a flat fill.
- **Stage** (`#0B0F16` / `oklch(0.16 0.008 260)`): the video player well and reel thumbnails.
  The single dark surface in the system, and the only place light text is correct.
- **Ink** (`#1B2A44`): headings and strong text — deep navy-slate.
- **Body** (`#46586E` / `oklch(0.45 0.030 255)`): body copy. Clears 4.5:1 on Surface/Canvas.
- **Muted** (`#5F7188`): meta and secondary text — AA on surface for small text, held to ≥12px.
- **Faint** (`#9FADBE`): disabled text, decorative dividers. Never body copy.
- **Border** (`#E5EBF3`) / **Border Strong** (`#D2DBE6`): card/input hairlines and stronger
  dividers.

### Semantic
- **Link** (`#2563EB`): inline links and cross-references (matches Forven's "Forven Base").
- **Success** (`#15803D` on `#DCFCE7`): completed/published states.
- **Warning** (`#B45309` on `#FEF3C7`): draft/in-progress states.
- **Danger** (`#DC3355` on `#FDE7EC`): destructive controls only (delete reel, clear). The
  Generate Reel action is **primary amber**, not danger — generating is a positive action.

### Named Rules
**The One Light Rule.** Studio Amber appears on ≤10% of any screen. It is reserved for the
single most important action or the current selection. It is never a background wash, never a
gradient, never decoration. Its rarity is what makes it read as "act here."

**The Light-Chrome-Dark-Media Rule.** Exactly one surface is dark: the video stage
(`#0B0F16`). Everything that frames, controls, or describes the video is light. Never invert
this to "go dark for a video app" — that is the outgoing theme, and it hid the text.

## 3. Typography

**Display Font:** Inter (with `system-ui, -apple-system, 'Segoe UI', sans-serif`)
**Body Font:** Inter (same family — one voice across the whole tool)
**Label/Mono Font:** SF Mono / `ui-monospace` — timestamps only

**Character:** One humanist sans does everything. Product UI does not need a display/body
pairing; contrast comes from weight (700 headings vs 400 body) and space, not from a second
typeface. Restrained, legible, invisible — the type gets out of the way of the task.

### Hierarchy
- **Display** (700, `1.75rem`, 1.2): page titles ("Projects", "Library"). Fixed rem, never
  fluid — a sidebar-shrinking clamp heading looks worse, not better.
- **Headline** (700, `1.375rem`, 1.25): screen/section headings, modal titles.
- **Title** (600, `1.0625rem`, 1.4): card names, file names, sub-headings.
- **Body** (400, `0.875rem`, 1.55): all body and control copy. Prose caps at ~70ch; dense UI
  and transcript lines may run tighter.
- **Label** (600, `0.6875rem`, +0.08em, UPPERCASE): section eyebrows and field labels.
- **Mono** (400, `0.8125rem`): transcript timestamps only.

### Named Rules
**The One Voice Rule.** Inter carries headings, buttons, labels, body, and data. No second
family, no display face. A different font in a UI label is a bug.

**The Rationed Eyebrow Rule.** Forven uses uppercase tracked labels ("PRIMARY NAVIGATION"),
so one eyebrow that genuinely titles a region is on-brand. But an eyebrow stamped over every
section is AI scaffolding — forbidden. If every section has a kicker, delete all but the ones
that title a real region.

## 4. Elevation

The system is **light and layered, not flat, and never heavy.** Depth is a whisper: white
surfaces lift off the pale canvas with 1px borders and low, cool, diffuse shadows — the
Forven card feel. Shadows are ambient (they suggest a surface resting just above the page),
not dramatic. Elevation increases only in response to state (hover) or stacking (popovers,
modals). A resting card is nearly flat; it earns its shadow by being interacted with.

### Shadow Vocabulary
- **Resting** (`box-shadow: 0 1px 2px rgba(20,40,80,.05)`): default card/panel lift.
- **Raised** (`box-shadow: 0 1px 2px rgba(20,40,80,.04), 0 6px 20px rgba(20,40,80,.06)`):
  card hover, active toolbars.
- **Popover** (`box-shadow: 0 8px 28px rgba(20,40,80,.14)`): dropdowns, prompt history,
  folder switcher, modals.

### Named Rules
**The Cool-Shadow Rule.** Shadows are cool and low-alpha (`rgba(20,40,80,…)`), never neutral
black. Black shadows on a blue-tinted canvas read as a 2014 app. If a shadow looks gray-black
or the blur is tight, it's wrong — widen the blur, drop the alpha, cool the hue.

## 5. Components

Buttons, cards, and inputs are **refined and restrained**: quiet at rest, soft-edged, with
state revealed on interaction rather than shouted by default.

### Buttons
- **Shape:** gently rounded (12px, `{rounded.md}`); a consistent shape across the whole app.
- **Primary:** solid **Amber Strong** (`#BD5419`) fill, white text — the deeper amber that
  passes white-text AA. Used for the one key action in a view — **Analyze, Generate Reel,
  Open Folder, + New Reel**. (Analyze and Generate live in different zones/phases, so one
  primary per zone still holds.) Bright Studio Amber is never a white-text fill.
- **Danger:** solid **Danger** (`#DC3355`) fill, white text — reserved for destructive
  actions only (delete reel, clear all). Never the Generate action.
- **Secondary:** white Surface, 1px **Border**, **Body** text — supporting actions.
- **Ghost / Icon:** transparent, hover fills **Amber Tint**. For toolbar and inline icons.
- **Hover / Focus:** hover shifts fill one step (amber→`amber-hover`) and lifts to *Raised*;
  `:focus-visible` shows a 3px Studio-Amber ring at ~25% alpha. Disabled drops to ~45%
  opacity, no shadow, `cursor: default`.

### Chips
- **Style:** fully rounded pills (`{rounded.pill}`), tint background + matching ink text,
  optional 1px tinted border — the Forven filter/count pattern.
- **State:** active/selected = **Amber Tint** bg + **Amber Ink** text; semantic states use
  their own tint+ink pair (success, warning). Always tint + text, **never color alone** —
  pair every semantic color with a label or icon.

### Cards / Containers
- **Corner Style:** 16px (`{rounded.lg}`).
- **Background:** white **Surface**, optional **Surface Warm** bottom-gradient wash.
- **Shadow Strategy:** *Resting* by default, *Raised* on hover with an **Amber Border** edge.
- **Border:** 1px **Border**.
- **Internal Padding:** `16px` (`{spacing.lg}`); roomier for feature cards.
- **No nesting.** One card level only — group internals with dividers and space, never a
  card-inside-a-card (a tell we do not import from the parent app).

### Inputs / Fields
- **Style:** white **Surface**, 1px **Border**, 8px radius (`{rounded.sm}`), **Ink** text.
- **Focus:** border shifts to **Studio Amber** + 3px amber ring (`0 0 0 3px` at ~25%).
- **Placeholder:** held to 4.5:1 — use **Muted**, never **Faint**.
- **Error / Disabled:** error border **Danger** with a text hint below (not color alone);
  disabled fills a faint neutral and drops text to **Faint**.

### Navigation
- **Style:** horizontal text links on white; no background bar. **Body** text default,
  **Ink** on hover. Active item is an **Amber Tint** pill with **Amber Ink** text (exactly
  Forven's active-nav treatment).
- **Mobile:** collapses to a menu; the active-pill treatment persists.

### Transcript Line (signature component)
The core surface — where clients review and trust the AI's picks, so selection must read
instantly and pass AA. A row shows a **Mono** timestamp + **Body** line text.
- **Checkbox mode:** a filled amber/success check + a faint tint on the row; selected text
  promotes to **Ink**, unselected stays **Body** (never the outgoing near-invisible
  `#3a5070`).
- **Highlight mode:** a Studio-Amber left marker (≤4px) + amber-tint row wash; selected text
  to **Ink**. The marker is a full affordance, not a decorative side-stripe on a card.
- Selection never relies on color alone — the check/marker shape carries it too.

## 6. Do's and Don'ts

### Do:
- **Do** keep the interface **light** — Canvas (`#F1F5FB`) page, white Surface cards — with
  the video stage (`#0B0F16`) as the only dark surface (The Light-Chrome-Dark-Media Rule).
- **Do** reserve **Studio Amber** for the single most important action or the current
  selection, on ≤10% of any screen (The One Light Rule).
- **Do** hold all body text to **WCAG AA** (≥4.5:1): **Body** `#46586E` for copy, **Muted**
  only for large/bold text, **Faint** for disabled/decoration only.
- **Do** carry hierarchy with **Inter weight and whitespace**, not with a second font or more
  color (The One Voice Rule).
- **Do** use fully-rounded tint+ink **pills** for filters, counts, and statuses, always
  pairing color with a label or icon.
- **Do** keep shadows **cool, low-alpha, and diffuse** (`rgba(20,40,80,…)`) (The Cool-Shadow
  Rule), and let elevation respond to state, not sit heavy at rest.
- **Do** honor `prefers-reduced-motion` on every transition, with a crossfade/instant
  fallback.

### Don't:
- **Don't** make it look like a **consumer creator app** — no TikTok/CapCut emoji chrome
  (🎬 ✦ 📼 ☑), no gamified flourishes. This is a B2B research deliverable.
- **Don't** rebuild the **intimidating pro-NLE** (Premiere/Resolve) wall of controls and
  timelines. Clients are not editors.
- **Don't** revert to a **hacker-terminal dark dashboard** — no neon-on-black, and never the
  outgoing muted-gray-on-navy body text (`#3a5070` on `#080c14`) that hides in the dark.
- **Don't** use `border-left`/`border-right` > 1px as a colored accent stripe on cards or
  rows. The transcript highlight marker is a selection affordance, not a decorative stripe.
- **Don't** nest cards inside cards, and don't stamp an uppercase eyebrow over every section
  (The Rationed Eyebrow Rule).
- **Don't** signal state by **color alone** — every success/warning/selected state gets a
  label, icon, or shape too.
- **Don't** let Studio Amber become a background wash, gradient, or gradient-text. One solid
  accent; emphasis otherwise comes from weight and size.
