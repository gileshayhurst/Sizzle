# Encoder validation clips

Cut straight from the timings in
`FORVEN VIDEOS/forven-interview-14076753-….rich.txt`, with **no fade** — a fade
would soften exactly the defect these clips exist to expose.

Source turn in Forven's original export was one line: `[02:01] Participant: We try
to get him groomed every couple months… But we t- try to keep him… We'll give him
pets. We have a toddler… he sleeps with us on our bed…`

That whole 19-second turn was the smallest clippable unit before this work.

| Clip | Range | What it proves | Failure looks like |
|---|---|---|---|
| `01_mid-turn-1sec` | 2:08–2:09 | Clipping the **3rd sentence of a 5-sentence turn** — the thing that was impossible before | Wrong sentence entirely, or a word fragment |
| `02_turn-first-sentence` | 1:59–2:05 | Start accuracy. Forven said the turn began at 2:01; we derived 1:59 | Opens mid-word, or with 2s of the previous speaker |
| `03_turn-last-sentence` | 2:34–2:40 | The ceil-end rule — the final word "happy" must survive whole | Cuts off on "happ—" |
| `04_speaker-boundary` | 2:32–2:34 | Participant line starting the instant the interviewer stops | Interviewer's voice audible at the head |
| `05_short-between-long` | 3:02–3:04 | A short sentence wedged between two long ones | Drifts into a neighbour |

**The one thing to watch for above all:** if *every* clip is late or early by
roughly the same amount, that is a systematic offset and it is a real bug. If
they're individually good, the encoder is sound.
