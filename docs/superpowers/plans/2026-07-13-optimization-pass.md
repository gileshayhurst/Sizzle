# Optimization Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut analyze API cost via prompt caching, fix the stale redesign note in CLAUDE.md, and produce a ranked design + code-health audit report (report only, no fixes).

**Architecture:** One doc edit (CLAUDE.md), one small code change (`claude_client.py` message restructured into content blocks with a `cache_control` breakpoint on the transcript block), and one report file produced by two audit passes (impeccable skill + ponytail-audit + manual WCAG/hierarchy checks). No changes to `app.py`, the frontend, or the generator service.

**Tech Stack:** Python/Flask, Anthropic SDK (`_client.messages.create`), pytest with `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-07-13-optimization-pass-design.md`

**Test command:** `.\venv\Scripts\python.exe -m pytest tests/ -v` (run from repo root, PowerShell)

---

### Task 1: Fix the stale redesign paragraph in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md:12`

`static/style.css` is fully on the Bright Studio light system (all tokens present, no dark-navy legacy styles), so the "active redesign" paragraph is out of date.

- [ ] **Step 1: Edit the paragraph**

Replace this exact line in `CLAUDE.md` (line 12):

```
An active redesign is converting the app from the outgoing dark navy theme to this light system, surface by surface. New/edited UI must follow DESIGN.md (not the legacy dark styles it may sit next to).
```

with:

```
The Bright Studio conversion is complete — the app is fully on this light system. New/edited UI must follow DESIGN.md.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md — Bright Studio redesign is complete, not in progress"
```

---

### Task 2: Prompt caching on the analyze transcript block

**Files:**
- Modify: `claude_client.py:28-40`
- Test: `tests/test_claude_client.py`

The user message currently sends one plain string: `f"Transcript:\n{transcript}\n\nPrompt: {prompt}"`. Restructure it into two content blocks — transcript first with a cache breakpoint, prompt last — so re-analyses of the same folder within the 5-minute cache TTL read the transcript from cache (~0.1× input price) instead of paying full price. The system prompt renders before messages, so the cached prefix is system + transcript.

Two existing tests (`test_sends_transcript_in_user_message`, `test_sends_prompt_in_user_message`) do `"..." in user_content` where `user_content` is a string today. After the change, `content` is a list of dicts, so those tests must read block text. Update them in the same step that adds the new cache test.

- [ ] **Step 1: Update the two content tests and add the cache-control test**

In `tests/test_claude_client.py`, add a module-level helper after `_make_mock_response` (line 11), replace the bodies of `test_sends_transcript_in_user_message` (lines 21-27) and `test_sends_prompt_in_user_message` (lines 30-36), and append the new test at the end of the file:

```python
def _user_blocks(mock_client) -> list:
    call_kwargs = mock_client.messages.create.call_args.kwargs
    return call_kwargs["messages"][0]["content"]


def test_sends_transcript_in_user_message():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("my transcript text", "my prompt")
        blocks = _user_blocks(mock_client)
    joined = "".join(b["text"] for b in blocks)
    assert "my transcript text" in joined


def test_sends_prompt_in_user_message():
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("some transcript", "hospitality of waiters")
        blocks = _user_blocks(mock_client)
    joined = "".join(b["text"] for b in blocks)
    assert "hospitality of waiters" in joined


def test_transcript_block_is_cached_and_prompt_block_is_not():
    """Transcript block carries the cache breakpoint; the prompt block must
    come after it (varying content after the breakpoint) and carry none."""
    with patch.object(claude_client, "_client") as mock_client:
        mock_client.messages.create.return_value = _make_mock_response("none")
        query_claude("long transcript", "topic prompt")
        blocks = _user_blocks(mock_client)
    assert len(blocks) == 2
    assert "long transcript" in blocks[0]["text"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "topic prompt" in blocks[1]["text"]
    assert "cache_control" not in blocks[1]
```

- [ ] **Step 2: Run the test file to verify the new test fails**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_claude_client.py -v`

Expected: `test_transcript_block_is_cached_and_prompt_block_is_not` FAILS (content is still a plain string, so indexing `blocks[0]["text"]` raises `TypeError` or the length assert fails). The two updated tests also fail the same way. The other four tests PASS.

- [ ] **Step 3: Restructure the message in `claude_client.py`**

Replace the `query_claude` function (lines 28-40) with:

```python
def query_claude(transcript: str, prompt: str) -> str:
    message = _client.messages.create(
        model="claude-opus-4-8",
        max_tokens=256,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        # Stable prefix: cached across repeated analyzes of the
                        # same folder (additive analyze re-sends this verbatim).
                        "type": "text",
                        "text": f"Transcript:\n{transcript}",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        # Varying suffix: must stay after the breakpoint.
                        "type": "text",
                        "text": f"\n\nPrompt: {prompt}",
                    },
                ],
            }
        ]
    )
    return message.content[0].text
```

Note the rendered text is byte-identical to the old single string (`Transcript:\n...` + `\n\nPrompt: ...`), so Claude sees the same prompt — zero quality impact.

- [ ] **Step 4: Run the test file to verify all tests pass**

Run: `.\venv\Scripts\python.exe -m pytest tests/test_claude_client.py -v`

Expected: all 7 tests PASS.

- [ ] **Step 5: Run the full suite to catch regressions**

Run: `.\venv\Scripts\python.exe -m pytest tests/ -v`

Expected: all tests PASS (`/analyze` tests in `test_app.py` mock `query_claude` itself or the client, so they are unaffected — if any fail, they are asserting on the old string-content shape and must be updated the same way as Step 1).

- [ ] **Step 6: Commit**

```bash
git add claude_client.py tests/test_claude_client.py
git commit -m "feat: prompt-cache the transcript block in analyze calls"
```

---

### Task 3: Design audit report (impeccable + manual pass)

**Files:**
- Create: `docs/audits/2026-07-13-optimization-audit.md`

Report only — apply no fixes. This task must run in the main session (it invokes the `impeccable` skill via the Skill tool; do not dispatch it to a subagent).

- [ ] **Step 1: Run the impeccable skill in audit mode**

Invoke the Skill tool: skill `impeccable`, args `audit the app UI against DESIGN.md and .impeccable/design.json — findings report only, do not change code`. Inputs it should cover: `templates/index.html`, `static/style.css`, `static/app.js`, `DESIGN.md`, `PRODUCT.md`. Collect its findings list.

- [ ] **Step 2: Independent manual pass**

Review the same files against this checklist, noting file:line for each finding:

- WCAG AA contrast: every text/background token pair used in `style.css` (spot-check `--muted` on `--canvas`, `--amber-ink` on `--amber-tint`, `--stage-ink` on `--stage`, semantic tints).
- Component states: hover/focus/disabled/active present for buttons, inputs, transcript lines, library cards; visible `:focus-visible` outlines.
- Empty states: folder picker with no recent folders, library with no reels, analyze with zero matches, empty transcript.
- Hierarchy & layout: analyze-zone vs transcript-zone weighting, workspace sidebar, modal scrims.
- Responsive: behavior at narrow widths (workspace layout is `flex-direction: row` — check wrap/overflow).
- UX copy: button labels, error messages, progress text against PRODUCT.md's register (`product`, paying market-research clients).

- [ ] **Step 3: Write the report skeleton and merge findings**

Create `docs/audits/2026-07-13-optimization-audit.md`:

```markdown
# Optimization Audit — 2026-07-13

Report only. No fixes applied. Ranked by impact; each finding has file:line.

## Part 1 — Design audit

### High
<!-- findings -->

### Medium
<!-- findings -->

### Low / polish
<!-- findings -->

## Part 2 — Code health

(filled by Task 4)

## Part 3 — Repo hygiene flags

(filled by Task 4)
```

Merge Step 1 + Step 2 findings into Part 1, deduplicated, ranked High/Medium/Low. Every finding: one line — location, what's wrong, which DESIGN.md/WCAG rule it violates.

- [ ] **Step 4: Commit**

```bash
git add docs/audits/2026-07-13-optimization-audit.md
git commit -m "docs: design audit findings (report only)"
```

---

### Task 4: Code-health audit (ponytail-audit + debris flags)

**Files:**
- Modify: `docs/audits/2026-07-13-optimization-audit.md` (Parts 2 and 3)

Also main-session (invokes the `ponytail:ponytail-audit` skill). Report only.

- [ ] **Step 1: Run ponytail-audit**

Invoke the Skill tool: skill `ponytail:ponytail-audit`. Scope: the Python modules and `static/app.js`. Collect the ranked delete/simplify/replace list.

- [ ] **Step 2: Fill Part 2 with the ponytail findings**

Paste the ranked list into Part 2 of the report, keeping the one-line-per-finding format (location — what to cut — what replaces it). Lead with the known headline: `static/app.js` (2,709 lines) — note which responsibilities could split out if/when it's next touched, per the audit's findings.

- [ ] **Step 3: Fill Part 3 with repo-hygiene flags (do NOT delete anything)**

List these untracked root-level items as candidates for the user to delete or gitignore, one line each with what they appear to be:

- `_fix_gen_escape.py` — one-off fix script
- `set` — stray file (likely a redirection accident)
- `transcribe_output.py` — scratch script
- `NOBU.txt`, `NOBU2.txt`, `NOBU3.txt`, `NOBU_short.txt`, `ChickenVideos.txt`, `WingReactions2.txt`, `riverside_grocery.txt` — transcript/scratch text files
- `NOBU/`, `FORVEN VIDEOS/`, `chicken_tutorials/`, `test_videos/` — local media folders (likely intentional; flag for .gitignore rather than deletion)
- `node_modules/`, `package.json`, `package-lock.json` — check whether anything references them; if not, flag as removable

Verify each item still exists (`git status --short`) before listing it; drop lines for anything already gone.

- [ ] **Step 4: Commit**

```bash
git add docs/audits/2026-07-13-optimization-audit.md
git commit -m "docs: code-health and repo-hygiene audit findings"
```
