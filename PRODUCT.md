# Product

## Register

product

## Users

Clients of a market-research company — business users on the insights, marketing, and
strategy side, not video editors and not developers. They come to Sizzle Reel with a
folder of research footage (interviews, focus groups, product-reaction and usability
sessions) and a deadline to produce something a stakeholder will actually watch.

Their context: this is a **paid, client-facing feature** inside the company's AI
market-research platform. Users reach for it occasionally, per project, usually under
time pressure to turn hours of raw footage into a short, credible highlight reel they can
drop into a report or present. They are fluent in professional SaaS tools but have zero
tolerance for a video-editor learning curve.

## Product Purpose

Sizzle Reel turns folders of raw research video into presentation-ready highlight reels
without anyone opening a video editor. It transcribes each clip, lets the user describe in
plain language what they're looking for ("moments where people react to price",
"strongest positive quotes about the packaging"), uses AI to find the matching moments,
and stitches the selected clips — with title cards — into a single reel.

Success looks like: a non-technical client goes from a folder of footage to a credible,
shareable reel in minutes, and **trusts** that the AI surfaced the right moments because
they could see, review, and adjust every selection before generating. The tool is a
first-class deliverable engine for research findings, not a toy.

## Brand Personality

Modern, approachable, clear. The voice is confident and plain-spoken — no jargon, no
video-production vocabulary thrown at a non-editor. It should feel like a trustworthy
professional product in the Notion / Vercel / Linear family: polished enough to justify a
paid service, calm enough that a first-time client never feels lost.

Emotional goals: **confidence** in the AI output, **ease** through the pick → describe →
generate flow, and the quiet reassurance that this is a serious tool that respects the
user's time and their client-facing reputation.

## Anti-references

- **Consumer creator apps** (TikTok, CapCut, InShot): emoji-heavy chrome, gamified
  flourishes, playful "fun" energy. This is a B2B research deliverable, not a social edit.
  Note: the current UI's emoji-laden nav (🎬 ✦ 📼 ☑) leans this way and should go.
- **Intimidating pro-NLE** (Premiere, DaVinci Resolve): walls of controls, timelines,
  dense expert-only chrome. Clients are not editors.
- **Generic "hacker/terminal" dark dashboards**: neon-on-black, low-contrast muted text
  for "atmosphere." The current UI's muted-gray-on-navy body text fails this and fails
  legibility.

## Design Principles

- **Trust the output.** Every AI selection is visible, legible, and adjustable before the
  user commits. The user should always understand *what* was chosen and *why*, and be able
  to change it. Confidence is the product.
- **The tool disappears.** Pick folder → describe → generate, with clear state at every
  step and zero incidental friction. Familiarity beats novelty; standard affordances win.
- **Professional, not intimidating.** Serious enough to sell as a paid service, simple
  enough for a client who has never touched a video editor. Remove editor vocabulary and
  hand-holding in equal measure.
- **Legible under scrutiny.** This produces client-facing deliverables. Contrast, clear
  hierarchy, and honest state feedback (transcribing, analyzing, generating, error) are
  non-negotiable, not polish for later.
- **Calm, purposeful motion.** Motion conveys state and progress — nothing decorative.
  Transitions stay short (150–250ms) and out of the user's way.

## Accessibility & Inclusion

- **WCAG AA** as the enforced baseline: body text ≥4.5:1 contrast, large text ≥3:1,
  placeholders held to the same 4.5:1. The current design fails this widely (muted grays on
  near-black) and fixing it is in scope, not optional.
- **`prefers-reduced-motion`** honored on every animation, with a crossfade or instant
  fallback (progress bars, screen transitions, card reveals).
- Keyboard-navigable interactive controls with visible focus states.
- Selection and state must not rely on color alone (checkbox/highlight modes currently lean
  on green/orange — pair with shape, icon, or text).
