# Plain vs Rich Transcript Reel Comparison

**Date:** 2026-07-30
**Status:** Approved design, pending implementation plan

## Goal

Measure whether rich transcripts (`[M:SS-M:SS]` sentence-level, real end times) produce
better sizzle reels than plain transcripts (`[M:SS]` turn-level) from the same source
interviews and the same prompts.

Ten reels: five topics (Cost, Convenience, Legitimacy, Lifespan, Objections) generated
twice, once from each transcript type. Every input except transcript syntax is held
constant.

## Why this is the right axis

The codebase already branches on transcript tier. `shared.transcript_tier` classifies a
file as rich only when *every* respondent line carries a valid end timestamp, and three
downstream behaviours change on that classification:

| | plain | rich |
|---|---|---|
| `read_transcript` routes to | `normalize_transcript` | `expand_anchors` |
| sentence timestamps come from | word-count interpolation, biased 1.0s early | the transcript's own anchors |
| `lines_in_range` selects by | line start within range ±0.5s | >50% of the line's speech overlapping the range |
| `group_lines_into_segments` ends a clip at | the next unselected line's start | the last selected line's real `end_seconds` |

So the comparison exercises an existing designed-for difference. No production code
changes.

## Scope

In scope: build two matched working folders, run analyze + generate ten times, score the
results on content quality.

Out of scope: changing any production code; cloud mode; re-transcribing the finished
reels (the user watches for playback judgement).

## 1. Working folders

`ForvenRich` contains transcripts only — no video. Its 11 `.rich.txt` stems match 11
sources in `FORVEN VIDEOS` exactly:

- 8 × `forven-interview-<uuid>.webm`
- `DogsAsFamily.mp4`, `Lifespan.mp4`, `Picky.mp4`

`FORVEN VIDEOS` additionally holds ~20 previously generated reels. Those are listed in
its `sizzle_generated_reels.txt` and would be filtered by `filter_generated_reels`, but
leaving them in makes the two sides asymmetric. Both runs therefore use clean folders:

```
Compare/plain/   11 hardlinked videos + <stem>.txt   (from FORVEN VIDEOS)
Compare/rich/    11 hardlinked videos + <stem>.txt   (from ForvenRich, .rich.txt renamed)
Compare/reels/   the 10 finished reels, gathered for viewing
```

Videos are hardlinked, not copied — same volume, so zero bytes and no transfer. Neither
source folder is modified; transcripts are read-only client data.

The rename `X.rich.txt` → `X.txt` is what makes tier detection fire: `read_transcript`
pairs a transcript with its video by stem, so the rich text must sit at the stem the
video uses.

### Verification

Before generating, assert for each folder that all 11 stems have both a video and a
transcript, and that `transcript_tier(parse_transcript_lines(read))` returns `plain` for
all 11 in `Compare/plain/` and `rich` for all 11 in `Compare/rich/`. A single
misclassified file silently invalidates that side of the comparison, so this is a hard
gate — abort rather than generate.

## 2. Driver

A throwaway script in the scratchpad importing `app._run_analyze` and
`generator_app._run_generation` in-process. No Flask servers, no browser.

The HTTP routes are thin wrappers over these two functions, so this is the identical
code path with fewer moving parts than standing up two servers or clicking the UI ten
times.

For each of the 5 topics × 2 folders:

1. `_run_analyze(folder, prompt)` → per-video scored candidate segments.
2. Selection rule: keep candidates scoring **≥7**, then take the **top 8 by score**
   across the whole folder. Ties broken by earlier `start_seconds`, so the rule is
   deterministic.
3. Union each kept candidate's `lines` into `selections[filename]`.
4. `_run_generation(job_id, folder, selections, prompt, output_filename)`.

The same prompt string is passed to both folders, byte-identical.

### Local-only guarantee

`.env` carries S3 credentials but no `APP_MODE`, and `storage.is_cloud()` is
`os.environ.get("APP_MODE", "local") == "cloud"`. The script must not set `APP_MODE`.
Consequences: no R2 reads or writes, no presigned URLs, no Render dispatch, no metered
bandwidth. The only metered cost is Anthropic API tokens for the 110 analyze calls.

## 3. Prompts

Identical text for both folders.

**Cost** — Moments about the COST or PRICE of dog food and how it affects what they buy:
what they pay, what feels expensive or good value, trading up or down, budget limits,
whether they'd pay more and why. Exclude non-food spend unless directly compared to food
spend.

**Convenience** — Moments about CONVENIENCE and how it affects what dog food they buy:
storing, preparing, serving, buying and reordering; busy days and time pressure;
subscriptions and delivery; mess and hassle; times they chose the easier option over the
better one.

**Legitimacy** — Moments about TRUST and LEGITIMACY of dog food and how it affects
purchase: how they judge whether a claim is real, what evidence convinces them (vet,
label, ingredients, reviews, recommendations), scepticism about marketing, brands they
trust or distrust and why.

**Lifespan** — Moments about the dog's HEALTH and LIFESPAN and how it affects food
purchase: wanting the dog to live longer, ageing, illness, vet-driven diet changes,
visible signs of health (coat, energy, weight, digestion), food chosen for long-term
wellbeing.

**Objections** — Moments where they raise an OBJECTION or barrier that stops or slows a
purchase: why they didn't switch, doubts, bad past experiences, the dog refusing food,
price/effort/trust stated as blockers, and what would change their mind.

## 4. Output

`<Topic>_plain.mp4` and `<Topic>_rich.mp4` for each of the five topics. The generator
writes to `Path(folder)/output_filename`, so reels land in their working folder and are
then copied into `Compare/reels/` alongside their `.vtt` sidecars for side-by-side
viewing.

Each run's analyze output, selection set, and resulting segment plan are saved as JSON so
the scoring is reproducible and auditable without re-running Claude.

## 5. Scoring

The user watches the reels for playback judgement. The scored analysis covers content
quality, derived from the selected transcript text and the cut plan:

- **Relevance** — does each clip address the topic or drift off it
- **Succinctness** — dead words per clip; the tight version of the point vs. padded with
  lead-in and trailing waffle
- **Coherence** — does the clip open and close on a complete thought; count
  `MAX_CLIP_SECONDS` truncations explicitly, as that is the one remaining path to a
  mid-sentence cut
- **Redundancy** — the same point from the same speaker more than once
- **Coverage** — how many of the 11 participants are represented
- **Shape** — clip count, per-clip duration spread, total runtime

Deliverable: a 1–10 score per reel with those components shown, a comparative verdict per
pair, and an overall call on which transcript type produces better reels — naming the
mechanism (`expand_anchors` vs `normalize_transcript`, and the differing clip-end rule)
that caused the observed difference.

## Failure modes

- **A rich file classifies as plain.** One respondent line missing a valid end collapses
  the whole file to plain-tier behaviour. Caught by the §1 verification gate.
- **A topic returns no candidates ≥7 on one side.** Record it as a finding rather than
  lowering the threshold for that topic — an asymmetric threshold would invalidate the
  pair. If a side yields zero segments, `_run_generation` errors with "No segments found
  in selections"; capture that as the pair's result.
- **Duplicate `raw` line collision** (documented in `normalize_transcript`) can duplicate
  a short clip on the plain side. Expected; count it under Redundancy rather than
  working around it.

## Cost and runtime

110 Claude Opus calls (11 transcripts × 5 topics × 2 folders), prompt-cached per
transcript. Then ~80 ffmpeg clip encodes — one `libx264`/`aac` pass per clip, which also
burns the identification overlay, the countdown timer, and the 0.4s fades. The final
stitch is `-c copy` and costs nothing. Expect 1–2 hours, dominated by ffmpeg.
