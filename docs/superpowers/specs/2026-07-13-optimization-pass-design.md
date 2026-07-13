# Optimization Pass — Design

**Date:** 2026-07-13
**Status:** Approved

## Context

A general optimization review of the Sizzle Reel project. Speed was the user's
top concern, but investigation showed the previously slow path (cloud
generation) is already fixed by browser-side encoding (shipped 2026-07-09,
confirmed fast in a real cloud run). The remaining opportunities are cost,
documentation accuracy, and design/code-health visibility.

Cost analysis: R2 has free egress, Render is on the free plan, and reel
encoding runs in the client's browser — so the project's only meaningful bill
is Anthropic API input tokens. `POST /analyze` sends every transcript in the
folder to `claude-opus-4-8` ($5/M input), and the additive-analyze feature
re-sends the *same* transcripts with each new prompt. Output is capped at 256
tokens, so output cost is negligible.

## Scope

Four items, in execution order:

### 1. CLAUDE.md correction (doc-only)

`static/style.css` is fully converted to the Bright Studio light system —
all tokens present, zero legacy dark-navy styles remain. The CLAUDE.md
paragraph claiming "an active redesign is converting the app from the outgoing
dark navy theme … surface by surface" is stale. Rewrite it to state the
conversion is complete and that new/edited UI must follow DESIGN.md.
No code changes.

### 2. Prompt caching on analyze (code)

In `claude_client.py`, restructure the user message content into blocks and
add a cache breakpoint on the transcript block:

```python
messages=[{
    "role": "user",
    "content": [
        {"type": "text", "text": f"Transcript:\n{transcript}",
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"\nPrompt: {prompt}"},
    ],
}]
```

- The prefix (system prompt + transcript) is stable across analyzes of the
  same folder; only the trailing prompt block varies. This is the ideal
  cache shape and requires no changes to `app.py`.
- Default 5-minute ephemeral TTL. Additive analyzes happen in bursts, so the
  5-min window fits usage; the 1-hour TTL's 2× write premium is not worth it.
- First analyze pays ~1.25× on the transcript (cache write); re-analyzes
  within the TTL pay ~0.1× on it (cache read). Zero quality impact — same
  model, same prompt content.
- Known limitation: Opus 4.8's minimum cacheable prefix is 4,096 tokens.
  Short transcripts silently won't cache — acceptable, since they're also
  the cheap ones. No code needs to handle this; the API degrades gracefully.
- Rejected alternatives: Batch API (50% off but up to 1-hour latency —
  analyze is interactive), model downgrade to Sonnet 5 (quality trade the
  user ruled out; noted as an optional future A/B only).

**Testing:** update/extend the existing `query_claude` test (mocked
Anthropic client) to assert the message is block-structured with
`cache_control` on the transcript block. No live-API test.

### 3. Design audit (report only)

Two independent passes, merged into one ranked findings list:

- The **impeccable** skill's audit flow run against the app (templates/,
  static/style.css, static/app.js) and the design docs (DESIGN.md,
  PRODUCT.md, .impeccable/design.json).
- An independent manual pass: WCAG AA contrast, visual hierarchy, component
  states, empty states, responsive behavior, UX copy.

Deliverable is a ranked report; **no fixes are applied in this pass**. The
user picks items for a follow-up session.

### 4. Code-health audit (report only)

- Run **ponytail-audit** over the repo for over-engineering/bloat findings
  (app.js at 2,709 lines is the known headline).
- Flag — but do not delete — root-level debris: `_fix_gen_escape.py`, `set`,
  stray transcript `.txt` files, test scratch folders.

Deliverable folded into the same report as item 3.

## Error handling

Item 2 is the only runtime change. `cache_control` is additive: if caching
silently doesn't engage (short prefix), the request behaves exactly as today.
Existing exception handling in `_analyze_one` covers API errors unchanged.

## Out of scope

- Sonnet 5 A/B experiment (optional future work).
- GEN_WORKERS env var (deferred; browser encoding made it less relevant).
- Applying any audit findings — follow-up passes.
